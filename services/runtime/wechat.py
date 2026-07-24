"""微信 ClawBot(iLink Bot)接入:扫码绑定 + 与总制片消息互通。

协议对齐 openclaw-weixin / cc-weixin weixin-bot-api.md(@tencent-weixin/openclaw-weixin@1.0.2):
- 绑定:get_bot_qrcode 取码 → 前端展示二维码并轮询 get_qrcode_status →
  confirmed 后把 bot_token/baseurl 持久化到 RUNTIME_DIR/wechat.json
- 收:长轮询 getupdates,微信侧文本作为用户消息转发给总制片(source="wechat")
- 发:订阅 HUB 聊天事件,总制片的回复与系统自动消息(设置变更/自动运行等)推回微信
仅文本互通;图片/语音等媒体消息暂不透传(语音有转写文本时按文本处理)。

注意:iLink 协议要求发消息必须携带来源消息的 context_token,
因此绑定后需微信端先发一条消息,系统才能反向推送(设置页有提示)。
"""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import core

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "1.0.2"
WECHAT_CONFIG_PATH = core.RUNTIME_DIR / "wechat.json"

MSG_TYPE_USER = 1          # message_type:1=用户消息 2=机器人消息
MSG_STATE_FINISH = 2
ITEM_TEXT, ITEM_VOICE = 1, 3
OUT_TEXT_LIMIT = 3000      # 微信单条文本上限裕量,超长截断并提示看控制台
LONG_POLL_TIMEOUT_S = 45
API_TIMEOUT_S = 15

# 收发循环运行状态(设置页展示;不含敏感信息)。
# err_in/err_out 分通道记录,成功后各自清零;瞬时网络抖动(长轮询 SSL 断连等)
# 会自动重试,连续 _ERR_IN_THRESHOLD 次失败才对用户展示,避免误解为不可用。
RELAY: dict = {"err_in": "", "err_out": "", "last_in": 0.0, "last_out": 0.0}
_ERR_IN_THRESHOLD = 3
_WAKE = asyncio.Event()    # 绑定确认后立刻唤醒收循环,不等下一轮轮询


# ---------------- 配置持久化 ----------------

def load_cfg() -> dict:
    try:
        return json.loads(WECHAT_CONFIG_PATH.read_text())
    except Exception:
        return {}


def save_cfg(cfg: dict):
    core.atomic_write_json(WECHAT_CONFIG_PATH, cfg)


# ---------------- iLink HTTP(阻塞式,线程里跑) ----------------

def _headers(token: str | None) -> dict:
    # X-WECHAT-UIN:随机 uint32 十进制字符串再 base64(对齐 openclaw-weixin api.ts)
    n = int.from_bytes(secrets.token_bytes(4), "big")
    h = {"Content-Type": "application/json",
         "AuthorizationType": "ilink_bot_token",
         "X-WECHAT-UIN": base64.b64encode(str(n).encode()).decode()}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get_json(path: str, params: dict, timeout: float) -> dict:
    url = f"{ILINK_BASE_URL}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _post_json(baseurl: str, path: str, body: dict, token: str, timeout: float) -> dict:
    raw = json.dumps(body, ensure_ascii=False).encode()
    url = (baseurl or ILINK_BASE_URL).rstrip("/") + "/" + path
    req = urllib.request.Request(url, data=raw, headers=_headers(token), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return json.loads(data.decode("utf-8", "replace")) if data else {}


# ---------------- 设置页 API ----------------

def _contact_label(cfg: dict) -> dict:
    c = cfg.get("contact") or {}
    return {"user_id": c.get("user_id") or "", "name": c.get("name") or ""}


async def api_wechat_status():
    cfg = load_cfg()
    return {
        "bound": bool(cfg.get("bot_token")),
        "bound_at": cfg.get("bound_at") or 0,
        "contact": _contact_label(cfg),
        # ready=已能反向推送(微信端发过消息,拿到了 context_token)
        "ready": bool(cfg.get("context_token") and _contact_label(cfg)["user_id"]),
        "relay": {"last_error": RELAY["err_in"] or RELAY["err_out"],
                  "last_in": RELAY["last_in"], "last_out": RELAY["last_out"]},
    }


def _qr_payload(qrcode_img_content: str, qrcode: str) -> dict:
    """把 get_bot_qrcode 的图片字段规整给前端:base64 图→dataURI;URL→本地生成 SVG。"""
    raw = (qrcode_img_content or "").strip()
    if raw.startswith("data:image"):
        return {"qr_img": raw}
    if raw.startswith(("http://", "https://")):
        return {"qr_svg": core._qr_svg(raw)}
    if raw:
        try:
            if len(base64.b64decode(raw, validate=True)) >= 32:
                return {"qr_img": "data:image/png;base64," + raw}
        except Exception:
            pass
    # 兜底:有些部署只回 qrcode 串(扫码目标),本地画码
    return {"qr_svg": core._qr_svg(qrcode)}


async def api_wechat_bind_start():
    """生成绑定二维码;前端展示后轮询 bind 状态。"""
    try:
        d = await asyncio.to_thread(
            _get_json, "ilink/bot/get_bot_qrcode", {"bot_type": 3}, API_TIMEOUT_S)
    except (OSError, urllib.error.URLError) as e:
        raise core.ServiceError(502, f"请求微信绑定二维码失败:{e}") from None
    qrcode = (d.get("qrcode") or "").strip()
    if not qrcode:
        raise core.ServiceError(502, f"get_bot_qrcode 未返回 qrcode:{d}")
    return {"qrcode": qrcode, **_qr_payload(d.get("qrcode_img_content") or "", qrcode)}


async def api_wechat_bind_poll(qrcode: str):
    """查询扫码状态;confirmed 时持久化 bot_token 并唤醒收发循环。"""
    if not (qrcode or "").strip():
        raise core.ServiceError(400, "qrcode is required")
    try:
        st = await asyncio.to_thread(
            _get_json, "ilink/bot/get_qrcode_status", {"qrcode": qrcode}, 30)
    except (OSError, urllib.error.URLError) as e:
        raise core.ServiceError(502, f"查询扫码状态失败:{e}") from None
    status = (st.get("status") or "waiting").strip()
    if status == "confirmed":
        tok = (st.get("bot_token") or "").strip()
        if not tok:
            raise core.ServiceError(502, "扫码已确认但未返回 bot_token,请重新绑定")
        save_cfg({"bot_token": tok, "baseurl": (st.get("baseurl") or "").strip(),
                  "bound_at": time.time(), "contact": {}, "context_token": "",
                  "get_updates_buf": ""})
        RELAY["err_in"] = RELAY["err_out"] = ""
        _WAKE.set()
    return {"status": status}


async def api_wechat_unbind():
    save_cfg({})
    RELAY["err_in"] = RELAY["err_out"] = ""
    return {"ok": True}


# ---------------- 收:微信 → 总制片 ----------------

def _msg_text(m: dict) -> str:
    for it in m.get("item_list") or []:
        t = it.get("type")
        if t == ITEM_TEXT and it.get("text_item"):
            return str(it["text_item"].get("text") or "")
        if t == ITEM_VOICE and (it.get("voice_item") or {}).get("text"):
            return str(it["voice_item"]["text"])
    return ""


def _contact_name(m: dict) -> str:
    for k in ("from_user_name", "from_nickname", "nickname"):
        if m.get(k):
            return str(m[k])
    return ""


async def _forward_inbound(m: dict):
    """微信文本 → 总制片对话(source=wechat,项目取顶栏当前项目)。"""
    text = _msg_text(m).strip()
    if not text:
        return
    orch = core.ORCHESTRATOR_AGENT
    proj = core.ui_prefs_pref()["project"]
    if not proj:
        projs = await core.api_projects()
        proj = projs[0] if projs else "demo"
    gm = core.agent_effective_model(orch)
    await core.api_chat({"agent": orch, "message": text, "project": proj,
                         "source": "wechat",
                         "engine": gm["engine"], "model": gm["model"]})
    RELAY["last_in"] = time.time()


async def _inbound_loop():
    fails = 0
    while True:
        cfg = load_cfg()
        tok = cfg.get("bot_token")
        if not tok:
            _WAKE.clear()
            try:
                await asyncio.wait_for(_WAKE.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass
            continue
        try:
            body = {"get_updates_buf": cfg.get("get_updates_buf") or "",
                    "base_info": {"channel_version": CHANNEL_VERSION}}
            resp = await asyncio.to_thread(
                _post_json, cfg.get("baseurl") or "", "ilink/bot/getupdates",
                body, tok, LONG_POLL_TIMEOUT_S)
        except TimeoutError:
            continue                      # 长轮询无消息超时,正常重试
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):      # 凭证问题,自动重试无望,立即提示
                RELAY["err_in"] = f"getupdates HTTP {e.code},绑定可能已失效,请尝试重新绑定"
                await asyncio.sleep(30)
            else:
                fails += 1
                if fails >= _ERR_IN_THRESHOLD:
                    RELAY["err_in"] = f"getupdates 持续失败(HTTP {e.code})"
                await asyncio.sleep(5)
            continue
        except Exception as e:  # noqa: BLE001
            # 长轮询偶发 SSL 断连/网络抖动属正常,自动重试;连续多次失败才提示
            fails += 1
            if fails >= _ERR_IN_THRESHOLD:
                RELAY["err_in"] = f"getupdates 持续失败:{e}"
            await asyncio.sleep(5)
            continue
        fails = 0
        RELAY["err_in"] = ""

        cfg = load_cfg()                  # 轮询期间可能已解绑
        if cfg.get("bot_token") != tok:
            continue
        changed = False
        nbuf = resp.get("get_updates_buf")
        if isinstance(nbuf, str) and nbuf != cfg.get("get_updates_buf"):
            cfg["get_updates_buf"] = nbuf
            changed = True
        for m in resp.get("msgs") or []:
            if m.get("message_type") != MSG_TYPE_USER:
                continue
            if m.get("context_token"):
                cfg["context_token"] = m["context_token"]
                changed = True
            if m.get("from_user_id"):
                c = cfg.setdefault("contact", {})
                c["user_id"] = m["from_user_id"]
                name = _contact_name(m)
                if name:
                    c["name"] = name
                changed = True
            try:
                await _forward_inbound(m)
            except Exception as e:  # noqa: BLE001
                RELAY["err_in"] = f"转发微信消息给总制片失败:{e}"
        if changed:
            save_cfg(cfg)


# ---------------- 发:总制片/系统 → 微信 ----------------

_SRC_PREFIX = {"settings": "⚙️ 设置变更", "watchdog": "🤖 自动运行"}


def _outbound_prefix(ev: dict) -> str | None:
    """哪些聊天事件推回微信:总制片的回复 + 系统自动发给总制片的消息。
    微信侧转发进来的(wechat)与网页端用户手输的(user)不回推,避免回声。"""
    role, src = ev.get("role"), ev.get("source") or ""
    if role == "assistant":
        return "🎬 总制片"
    if role == "user" and src not in ("user", "wechat"):
        return _SRC_PREFIX.get(src, "📣 系统")
    return None


async def _send_to_wechat(text: str):
    cfg = load_cfg()
    uid = (cfg.get("contact") or {}).get("user_id")
    if not (cfg.get("bot_token") and cfg.get("context_token") and uid):
        return                            # 未绑定,或微信端还没发过首条消息拿不到 context_token
    if len(text) > OUT_TEXT_LIMIT:
        text = text[:OUT_TEXT_LIMIT] + "\n……(超长截断,全文见控制台)"
    msg = {"from_user_id": "", "to_user_id": uid,
           "client_id": "weixin-" + uuid.uuid4().hex,
           "message_type": 2, "message_state": MSG_STATE_FINISH,
           "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
           "context_token": cfg["context_token"]}
    await asyncio.to_thread(
        _post_json, cfg.get("baseurl") or "", "ilink/bot/sendmessage",
        {"msg": msg, "base_info": {"channel_version": CHANNEL_VERSION}},
        cfg["bot_token"], API_TIMEOUT_S)
    RELAY["last_out"] = time.time()
    RELAY["err_out"] = ""


async def _outbound_loop():
    q = core.HUB.subscribe()
    try:
        while True:
            ev = await q.get()
            if ev.get("type") != "chat" or ev.get("agent") != core.ORCHESTRATOR_AGENT:
                continue
            prefix = _outbound_prefix(ev)
            text = (ev.get("text") or "").strip()
            if not prefix or not text:
                continue
            proj = ev.get("project") or ""
            head = f"{prefix} · {proj}" if proj else prefix
            try:
                await _send_to_wechat(f"{head}\n{text}")
            except Exception as e:  # noqa: BLE001
                # 发送失败意味着这条消息丢了,值得提示;下次发送成功自动清除
                RELAY["err_out"] = f"推送到微信失败:{e}"
    finally:
        core.HUB.unsubscribe(q)


async def relay_loop():
    """后台常驻:收发两条循环(services.api lifespan 启动)。"""
    await asyncio.gather(_inbound_loop(), _outbound_loop())

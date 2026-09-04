"""飞书机器人接入:应用凭证绑定 + 与总制片消息互通(手机消息通道之一)。

连接方式对齐 OpenClaw 的飞书通道:
- 绑定:填入飞书开放平台「企业自建应用」的 App ID / App Secret,
  经 tenant_access_token 校验通过后持久化到 RUNTIME_DIR/feishu.json
- 收:官方 SDK(lark-oapi)WebSocket 长连接订阅 im.message.receive_v1
  (无需公网回调地址),跑在独立子进程 feishu_bridge.py 里,退出自动重启;
  仅单聊文本,转发给总制片(source="feishu")
- 发:订阅 HUB 聊天事件,经 im/v1/messages(receive_id_type=open_id)推回
- 确认/签字卡片:订阅 HUB confirm 事件,把 dispatch.py --confirm 发起的
  确认/签字以互动卡片(msg_type=interactive)推到飞书,按钮点击经同一条
  长连接回传(card.action.trigger,桥进程透传),落到 api_confirm_answer,
  与网页弹窗同源同答案;答复/超时后 PATCH 卡片收尾,防止旧按钮残留
- 聊天命令(/clear、/auto on|off 等)不在本模块特殊处理:与普通文本一样
  转发给总制片对话,由 core.api_chat 统一识别并本地执行(不派发运行、
  不耗引擎配额),回复经 HUB 出站循环推回飞书
- 素材推送:media_push 监视循环发现新完成的人物/场景/道具主图或分镜组
  视频后调 push_image/push_video,经 im/v1/images / im/v1/files 上传,
  再以富文本图片(post)/可播放视频(media)消息推送;超过设置页「文件大小
  上限」(默认 5MB,存 feishu.json media_max_mb)或平台上限的文件不推送,
  只发一条文字提示

前置条件(设置页有说明):应用需开启机器人能力、以「长连接」方式订阅
「接收消息 im.message.receive_v1」事件与「回调订阅」(卡片按钮回传依赖
后者),并开通 im:message 收发权限(素材图片/视频推送还需 im:resource);
绑定后需在飞书里先给机器人发一条消息,系统才知道该推送给谁。
lark-oapi 未安装时绑定被拒并提示 pip install。
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import channels, core

FEISHU_CONFIG_PATH = core.RUNTIME_DIR / "feishu.json"
BRIDGE_PATH = core.ROOT / "services" / "runtime" / "feishu_bridge.py"
BRIDGE_LOG = core.RUNTIME_DIR / "feishu_bridge.log"
DEFAULT_DOMAIN = "https://open.feishu.cn"

OUT_TEXT_LIMIT = 3000                     # 与微信通道一致:手机端可读性截断
API_TIMEOUT_S = 15
MEDIA_MAX_MB_DEFAULT = 5                  # 素材推送单文件上限(MB),设置页可改
MEDIA_MAX_MB_CEIL = 30                    # 不超过飞书 im/v1/files 平台上限

RELAY: dict = {"err_in": "", "err_out": "", "last_in": 0.0, "last_out": 0.0}
_ERR_IN_THRESHOLD = 3
_WAKE = asyncio.Event()                   # 绑定/解绑后立刻唤醒收循环
_TOKEN = {"v": "", "exp": 0.0, "key": ""}  # tenant_access_token 缓存


@functools.lru_cache(maxsize=1)
def _sdk_status() -> tuple[bool, str]:
    """Import the SDK once; finding its package is not enough on Windows."""
    try:
        import lark_oapi  # noqa: F401, PLC0415
    except Exception as exc:  # noqa: BLE001
        return False, f"飞书 SDK 加载失败: {type(exc).__name__}: {exc}"
    return True, ""


def _sdk_ok() -> bool:
    return _sdk_status()[0]


def _sdk_error() -> str:
    return _sdk_status()[1]


def load_cfg() -> dict:
    try:
        return json.loads(FEISHU_CONFIG_PATH.read_text())
    except Exception:
        return {}


def save_cfg(cfg: dict):
    core.atomic_write_json(FEISHU_CONFIG_PATH, cfg)


def media_max_mb(cfg: dict | None = None) -> float:
    """素材推送单文件上限(MB):配置缺失/非法时取默认值。"""
    try:
        v = float((cfg if cfg is not None else load_cfg()).get("media_max_mb"))
    except (TypeError, ValueError):
        return float(MEDIA_MAX_MB_DEFAULT)
    return v if 0 < v <= MEDIA_MAX_MB_CEIL else float(MEDIA_MAX_MB_DEFAULT)


def _parse_media_max_mb(raw) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise core.ServiceError(400, "文件大小上限须为数字(MB)") from None
    if not (0 < v <= MEDIA_MAX_MB_CEIL):
        raise core.ServiceError(400, f"文件大小上限须在 0–{MEDIA_MAX_MB_CEIL}MB 之间")
    return round(v, 2)


# ---------------- 飞书 HTTP(阻塞式,线程里跑) ----------------

def _post_json(domain: str, path: str, body: dict, token: str | None,
               timeout: float, method: str = "POST") -> dict:
    url = (domain or DEFAULT_DOMAIN).rstrip("/") + path
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode(),
        headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _fetch_tenant_token(domain: str, app_id: str, app_secret: str) -> dict:
    return _post_json(domain, "/open-apis/auth/v3/tenant_access_token/internal",
                      {"app_id": app_id, "app_secret": app_secret}, None,
                      API_TIMEOUT_S)


async def _tenant_token(cfg: dict) -> str:
    key = cfg.get("app_id") or ""
    now = time.time()
    if _TOKEN["v"] and _TOKEN["key"] == key and _TOKEN["exp"] > now + 60:
        return _TOKEN["v"]
    d = await asyncio.to_thread(
        _fetch_tenant_token, cfg.get("domain") or DEFAULT_DOMAIN,
        key, cfg.get("app_secret") or "")
    if d.get("code") != 0 or not d.get("tenant_access_token"):
        raise RuntimeError(f"tenant_access_token 获取失败:{d.get('msg') or d}")
    _TOKEN.update(v=d["tenant_access_token"], key=key,
                  exp=now + float(d.get("expire") or 3600))
    return _TOKEN["v"]


# ---------------- 设置页 API ----------------

def _contact_label(cfg: dict) -> dict:
    c = cfg.get("contact") or {}
    return {"user_id": c.get("open_id") or "", "name": c.get("name") or ""}


async def api_feishu_status():
    cfg = load_cfg()
    return {
        "bound": bool(cfg.get("app_id")),
        "bound_at": cfg.get("bound_at") or 0,
        "app_id": cfg.get("app_id") or "",
        "contact": _contact_label(cfg),
        # ready=已能反向推送(飞书端发过消息,拿到了 open_id)
        "ready": bool(_contact_label(cfg)["user_id"]),
        "sdk": _sdk_ok(),
        "sdk_error": _sdk_error(),
        "media_max_mb": media_max_mb(cfg),
        "relay": {"last_error": _sdk_error() or RELAY["err_in"] or RELAY["err_out"],
                  "last_in": RELAY["last_in"], "last_out": RELAY["last_out"]},
    }


async def api_feishu_settings(body: dict):
    """推送设置:素材单文件上限(MB),超过的图片/视频不推送只发文字提示。"""
    cfg = load_cfg()
    if "media_max_mb" in body:
        cfg["media_max_mb"] = _parse_media_max_mb(body.get("media_max_mb"))
    save_cfg(cfg)
    return {"ok": True, "media_max_mb": media_max_mb(cfg)}


async def api_feishu_bind(body: dict):
    """校验 App ID / App Secret 后保存,并(重)启长连接。"""
    app_id = (body.get("app_id") or "").strip()
    app_secret = (body.get("app_secret") or "").strip()
    domain = (body.get("domain") or "").strip() or DEFAULT_DOMAIN
    if not app_id or not app_secret:
        raise core.ServiceError(400, "App ID 与 App Secret 均不能为空")
    if not _sdk_ok():
        raise core.ServiceError(400, _sdk_error())
    try:
        d = await asyncio.to_thread(_fetch_tenant_token, domain, app_id, app_secret)
    except (OSError, urllib.error.URLError) as e:
        raise core.ServiceError(502, f"连接飞书开放平台失败:{e}") from None
    if d.get("code") != 0:
        raise core.ServiceError(
            400, f"应用凭证校验失败(code={d.get('code')}):{d.get('msg') or d}")
    save_cfg({"app_id": app_id, "app_secret": app_secret, "domain": domain,
              "bound_at": time.time(), "contact": {},
              "media_max_mb": media_max_mb()})   # 重绑保留推送设置
    _TOKEN.update(v="", key="", exp=0.0)
    RELAY["err_in"] = RELAY["err_out"] = ""
    _WAKE.set()
    return {"ok": True}


async def api_feishu_unbind():
    save_cfg({"media_max_mb": media_max_mb()})   # 解绑只清凭证,推送设置保留
    RELAY["err_in"] = RELAY["err_out"] = ""
    _WAKE.set()
    return {"ok": True}


# ---------------- 收:飞书 → 总制片(桥子进程管理) ----------------

async def _handle_event(ev: dict):
    t = ev.get("type")
    if t == "msg":
        cfg = load_cfg()
        if not cfg.get("app_id"):
            return                        # 解绑竞态窗口内的残留事件
        c = cfg.setdefault("contact", {})
        if ev.get("open_id") and ev["open_id"] != c.get("open_id"):
            first_contact = not c.get("open_id")
            c["open_id"] = ev["open_id"]
            save_cfg(cfg)
            if first_contact:             # 刚拿到推送对象:把等着的确认/签字补推过去
                asyncio.create_task(_sync_pending_confirms())
        text = (ev.get("text") or "").strip()
        try:
            await channels.forward_inbound("feishu", text)
            RELAY["last_in"] = time.time()
            RELAY["err_in"] = ""
        except Exception as e:  # noqa: BLE001
            RELAY["err_in"] = f"转发飞书消息给总制片失败:{e}"
    elif t == "card":
        await _handle_card_click(ev)
    elif t == "fatal" and ev.get("error") == "sdk_missing":
        RELAY["err_in"] = "未安装飞书 SDK:请在服务端执行 pip install lark-oapi"


async def _kill(proc):
    if proc and proc.returncode is None:
        proc.kill()
        try:
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass


async def _inbound_loop():
    proc = None
    cur = None                            # 当前子进程使用的凭证
    fails = 0
    try:
        while True:
            cfg = load_cfg()
            creds = (cfg.get("app_id") or "", cfg.get("app_secret") or "",
                     cfg.get("domain") or DEFAULT_DOMAIN)
            if not creds[0]:
                await _kill(proc)
                proc, cur = None, None
                _WAKE.clear()
                try:
                    await asyncio.wait_for(_WAKE.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass
                continue
            if proc is None or proc.returncode is not None or creds != cur:
                await _kill(proc)
                if not _sdk_ok():
                    RELAY["err_in"] = _sdk_error()
                    proc, cur = None, None
                    await asyncio.sleep(30)
                    continue
                env = {**os.environ, "FEISHU_APP_ID": creds[0],
                       "FEISHU_APP_SECRET": creds[1], "FEISHU_DOMAIN": creds[2]}
                BRIDGE_LOG.parent.mkdir(parents=True, exist_ok=True)
                logf = open(BRIDGE_LOG, "ab")  # noqa: SIM115  子进程持有,进程退出即释放
                try:
                    proc = await asyncio.create_subprocess_exec(
                        sys.executable, str(BRIDGE_PATH),
                        env=env, stdout=asyncio.subprocess.PIPE, stderr=logf)
                finally:
                    logf.close()
                cur = creds
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
            except asyncio.TimeoutError:
                continue                  # 定期回头检查绑定状态/凭证变更
            if not line:                  # 子进程退出(连接失败/网络中断)
                rc = await proc.wait()
                proc = None
                fails += 1
                if fails >= _ERR_IN_THRESHOLD:
                    RELAY["err_in"] = (f"飞书长连接持续失败(rc={rc}),已自动重试;"
                                       f"请检查应用凭证/事件订阅/网络(日志:{BRIDGE_LOG.name})")
                await asyncio.sleep(min(30, 5 * fails))
                continue
            try:
                ev = json.loads(line.decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001
                continue
            if ev.get("type") == "started":
                fails = 0
                RELAY["err_in"] = ""
            await _handle_event(ev)
    finally:
        await _kill(proc)


# ---------------- 发:总制片/系统 → 飞书 ----------------

async def _send_to_feishu(text: str) -> bool:
    cfg = load_cfg()
    open_id = (cfg.get("contact") or {}).get("open_id")
    if not (cfg.get("app_id") and open_id):
        return False                      # 未绑定,或飞书端还没发过首条消息
    if len(text) > OUT_TEXT_LIMIT:
        text = text[:OUT_TEXT_LIMIT] + "\n……(超长截断,全文见控制台)"
    token = await _tenant_token(cfg)
    d = await asyncio.to_thread(
        _post_json, cfg.get("domain") or DEFAULT_DOMAIN,
        "/open-apis/im/v1/messages?receive_id_type=open_id",
        {"receive_id": open_id, "msg_type": "text",
         "content": json.dumps({"text": text}, ensure_ascii=False),
         "uuid": "va-" + uuid.uuid4().hex},
        token, API_TIMEOUT_S)
    if d.get("code") != 0:
        raise RuntimeError(f"im/v1/messages code={d.get('code')}:{d.get('msg') or d}")
    return True


# ---------------- 素材推送:图片/视频消息(media_push 监视循环调用) ----------------

IMG_MAX_BYTES = 10 * 1024 * 1024          # im/v1/images 单张上限
VID_MAX_BYTES = 30 * 1024 * 1024          # im/v1/files 单个上限
UPLOAD_TIMEOUT_S = 120


def _post_multipart(domain: str, path: str, token: str, fields: dict,
                    file_field: str, filename: str, data: bytes,
                    timeout: float) -> dict:
    url = (domain or DEFAULT_DOMAIN).rstrip("/") + path
    boundary = "----va" + uuid.uuid4().hex
    buf = bytearray()
    for k, v in fields.items():
        buf += (f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="{k}"\r\n\r\n{v}\r\n').encode()
    buf += (f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="{file_field}"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n").encode()
    buf += data
    buf += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        url, data=bytes(buf),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Authorization": f"Bearer {token}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


async def _upload_media(cfg: dict, kind: str, path: str) -> str:
    """上传图片/视频换取消息 key。kind: image|video。"""
    p = Path(path)
    token = await _tenant_token(cfg)
    if kind == "image":
        api, fields, field = "/open-apis/im/v1/images", {"image_type": "message"}, "image"
    else:
        api, fields, field = ("/open-apis/im/v1/files",
                              {"file_type": "mp4", "file_name": p.name}, "file")
    d = await asyncio.to_thread(
        _post_multipart, cfg.get("domain") or DEFAULT_DOMAIN, api, token,
        fields, field, p.name, p.read_bytes(), UPLOAD_TIMEOUT_S)
    key = (d.get("data") or {}).get("image_key" if kind == "image" else "file_key")
    if d.get("code") != 0 or not key:
        raise RuntimeError(f"{api} code={d.get('code')}:{d.get('msg') or d}"
                           "(若为权限错误,请在飞书开放平台为应用开通 im:resource)")
    return key


def _oversize_notice(cfg: dict, path: str, platform_max: int, what: str) -> str | None:
    """文件超过用户设置上限或平台上限 → 返回文字提示(不推送文件);否则 None。"""
    size = Path(path).stat().st_size
    user_mb = media_max_mb(cfg)
    limit = min(int(user_mb * 1024 * 1024), platform_max)
    if size <= limit:
        return None
    if size > platform_max:
        return f"({what}超过飞书平台上限 {platform_max // (1024 * 1024)}MB 无法直传,请在控制台查看)"
    return (f"({what} {size / (1024 * 1024):.1f}MB 超过推送上限 {user_mb:g}MB,未推送;"
            "可在设置 → 手机消息 → 飞书调整,或在控制台查看)")


async def push_image(path: str, caption: str) -> bool:
    """新完成概念图 → 富文本消息(标题带说明,正文图片)。未绑定返回 False。
    超过设置上限(默认 5MB)或平台上限的文件不推送,只发一条文字提示。"""
    cfg = load_cfg()
    open_id = (cfg.get("contact") or {}).get("open_id")
    if not (cfg.get("app_id") and open_id):
        return False
    notice = _oversize_notice(cfg, path, IMG_MAX_BYTES, "图片")
    if notice:
        return await _send_to_feishu(f"{caption}\n{notice}")
    key = await _upload_media(cfg, "image", path)
    await _card_request(
        "/open-apis/im/v1/messages?receive_id_type=open_id",
        {"receive_id": open_id, "msg_type": "post",
         "content": json.dumps(
             {"zh_cn": {"title": caption,
                        "content": [[{"tag": "img", "image_key": key}]]}},
             ensure_ascii=False),
         "uuid": "va-media-" + uuid.uuid4().hex})
    RELAY["last_out"] = time.time()
    RELAY["err_out"] = ""
    return True


async def push_video(path: str, caption: str) -> bool:
    """新完成分镜组视频 → 文本说明 + 可播放视频消息。未绑定返回 False。"""
    cfg = load_cfg()
    open_id = (cfg.get("contact") or {}).get("open_id")
    if not (cfg.get("app_id") and open_id):
        return False
    notice = _oversize_notice(cfg, path, VID_MAX_BYTES, "视频")
    if notice:
        return await _send_to_feishu(f"{caption}\n{notice}")
    key = await _upload_media(cfg, "video", path)
    await _send_to_feishu(caption)
    await _card_request(
        "/open-apis/im/v1/messages?receive_id_type=open_id",
        {"receive_id": open_id, "msg_type": "media",
         "content": json.dumps({"file_key": key}, ensure_ascii=False),
         "uuid": "va-media-" + uuid.uuid4().hex})
    RELAY["last_out"] = time.time()
    RELAY["err_out"] = ""
    return True


# ---------------- 确认/签字卡片:HUB confirm 事件 ↔ 飞书互动卡片 ----------------
# dispatch.py --confirm 发起的确认(重跑类)与签字(H 门)以互动卡片推到飞书,
# 按钮 value 带 confirm_id+option(+q),点击经长连接回传后落 api_confirm_answer,
# 与网页弹窗同一份答案。防重复点击分两层:桥进程在回调响应里原地把卡片换成
# 无按钮的"已提交"态(点击即变);父进程在答复/超时后再 PATCH 成终态。

_CARDS: dict[str, dict] = {}   # confirm_id -> {message_id, question, default}
_DONE_MIDS: dict[str, float] = {}   # 已写成终态(已处理/已超时)的 message_id,防被改写


def _mark_done(message_id: str):
    if not message_id:
        return
    _DONE_MIDS[message_id] = time.time()
    if len(_DONE_MIDS) > 500:              # 有界:只留最近的
        for k in sorted(_DONE_MIDS, key=_DONE_MIDS.get)[:100]:
            _DONE_MIDS.pop(k, None)


def _card(header: str, template: str, question: str,
          actions: list | None = None, note: str = "") -> dict:
    els: list = [{"tag": "div", "text": {"tag": "plain_text", "content": question}}]
    if actions:
        els.append({"tag": "action", "actions": actions})
    if note:
        els.append({"tag": "note",
                    "elements": [{"tag": "plain_text", "content": note}]})
    return {"config": {"wide_screen_mode": True},
            "header": {"template": template,
                       "title": {"tag": "plain_text", "content": header}},
            "elements": els}


def _confirm_card(ev: dict) -> dict:
    sign = ev.get("kind") == "sign"
    proj = (core.RUNS.get(ev.get("parent") or "") or {}).get("project") or ""
    head = ("✍️ 等你签字" if sign else "❓ 等你确认") + (f" · {proj}" if proj else "")
    note = ("签字类不超时;建议先在控制台核对相关产物再签。" if sign else
            f"{ev.get('remaining') or ev.get('timeout') or 60}s 内未选择将按默认"
            f"「{ev.get('default') or ''}」处理。")
    # value 多带一份 q(问题文本):桥进程在回调响应里原地把卡片换成"已提交"态
    # 时不依赖父进程就能拼出卡片(点击即变,不留可重复点击的窗口)。
    q = (ev.get("question") or "")[:300]
    actions = [{"tag": "button", "text": {"tag": "plain_text", "content": o},
                "type": "primary" if o == (ev.get("default") or "") else "default",
                "value": {"confirm_id": ev.get("id") or "", "option": o, "q": q}}
               for o in (ev.get("options") or [])]
    return _card(head, "orange" if sign else "blue",
                 ev.get("question") or "", actions, note)


async def _card_request(path: str, body: dict, method: str = "POST") -> dict:
    cfg = load_cfg()
    token = await _tenant_token(cfg)
    d = await asyncio.to_thread(
        _post_json, cfg.get("domain") or DEFAULT_DOMAIN, path, body,
        token, API_TIMEOUT_S, method)
    if d.get("code") != 0:
        raise RuntimeError(f"{path} code={d.get('code')}:{d.get('msg') or d}")
    return d


async def _push_confirm_card(ev: dict):
    """新确认项 → 发互动卡片。未绑定/联系人未就绪时静默跳过(网页弹窗仍在)。"""
    cid = ev.get("id") or ""
    cfg = load_cfg()
    open_id = (cfg.get("contact") or {}).get("open_id")
    if not (cid and cfg.get("app_id") and open_id) or cid in _CARDS:
        return
    _CARDS[cid] = {"message_id": "", "question": ev.get("question") or "",
                   "default": ev.get("default") or ""}   # 先占位防并发重复推
    try:
        d = await _card_request(
            "/open-apis/im/v1/messages?receive_id_type=open_id",
            {"receive_id": open_id, "msg_type": "interactive",
             "content": json.dumps(_confirm_card(ev), ensure_ascii=False),
             "uuid": "va-cfm-" + cid})
        _CARDS[cid]["message_id"] = (d.get("data") or {}).get("message_id") or ""
        RELAY["last_out"] = time.time()
        RELAY["err_out"] = ""
    except Exception as e:  # noqa: BLE001
        _CARDS.pop(cid, None)
        RELAY["err_out"] = f"推送确认卡片失败:{e}"
        return
    remaining = ev.get("remaining")
    if remaining is not None:             # 重跑类:到点未答就把卡片改为超时态
        asyncio.create_task(_expire_card_later(cid, int(remaining) + 2))


async def _patch_card(message_id: str, card: dict):
    await _card_request(f"/open-apis/im/v1/messages/{message_id}",
                        {"content": json.dumps(card, ensure_ascii=False)},
                        method="PATCH")


async def _finish_confirm_card(cid: str, answer: str):
    """已答复(飞书点按/网页弹窗任一入口)→ 卡片改为终态、移除按钮。"""
    c = _CARDS.pop(cid, None)
    if not (c and c["message_id"]):
        return
    _mark_done(c["message_id"])
    try:
        await _patch_card(c["message_id"],
                          _card("✅ 已处理", "green", c["question"],
                                note=f"已选「{answer}」。"))
    except Exception as e:  # noqa: BLE001
        RELAY["err_out"] = f"更新确认卡片失败:{e}"


async def _expire_card_later(cid: str, delay: int):
    await asyncio.sleep(max(1, delay))
    c = _CARDS.pop(cid, None)             # 已答复的先被 finish 弹掉,这里自然空
    if not (c and c["message_id"]):
        return
    _mark_done(c["message_id"])
    try:
        await _patch_card(c["message_id"],
                          _card("⏱ 已超时", "grey", c["question"],
                                note=f"超时未选择,已按默认「{c['default']}」处理。"))
    except Exception as e:  # noqa: BLE001
        RELAY["err_out"] = f"更新确认卡片失败:{e}"


async def _handle_card_click(ev: dict):
    """桥进程透传的确认/签字卡片按钮点击:落 api_confirm_answer(与网页弹窗同源)。"""
    cfg = load_cfg()
    if not cfg.get("app_id"):
        return
    bound = (cfg.get("contact") or {}).get("open_id")
    if bound and ev.get("open_id") and ev["open_id"] != bound:
        return                            # 单聊机器人本只此一人,防御性校验
    cid = ev.get("confirm_id") or ""
    if not cid:
        return
    try:
        await core.api_confirm_answer(cid, {"answer": ev.get("option") or ""})
        RELAY["last_in"] = time.time()
    except core.ServiceError:             # 确认项已被清理:把残留卡片改为失效态
        c = _CARDS.pop(cid, None)
        mid = (c or {}).get("message_id") or ev.get("message_id") or ""
        if mid and mid not in _DONE_MIDS:  # 已是终态的卡片(重复点击)不改写
            _mark_done(mid)
            try:
                await _patch_card(mid, _card(
                    "🚫 已失效", "grey", (c or {}).get("question") or "",
                    note="该确认项已失效(可能已在控制台处理或已超时)。"))
            except Exception as e:  # noqa: BLE001
                RELAY["err_out"] = f"更新确认卡片失败:{e}"


async def _sync_pending_confirms():
    """把仍在等待的确认项补推为卡片(绑定/首次建立联系人晚于确认项出现时)。"""
    try:
        for c in await core.api_confirms():
            await _push_confirm_card(c)
    except Exception as e:  # noqa: BLE001
        RELAY["err_out"] = f"补推确认卡片失败:{e}"


async def _confirm_loop():
    """订阅 HUB:新确认项发卡片,答复后收尾卡片。"""
    q = core.HUB.subscribe()
    await _sync_pending_confirms()
    try:
        while True:
            ev = await q.get()
            if ev.get("type") == "confirm":
                await _push_confirm_card(ev)
            elif ev.get("type") == "confirm_done":
                await _finish_confirm_card(ev.get("id") or "",
                                           ev.get("answer") or "")
    finally:
        core.HUB.unsubscribe(q)


async def relay_loop():
    """后台常驻:收、发、确认卡片三条循环(services.api lifespan 启动)。"""
    await asyncio.gather(
        _inbound_loop(),
        channels.outbound_loop("feishu", _send_to_feishu, RELAY),
        _confirm_loop())

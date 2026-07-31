"""飞书机器人接入:应用凭证绑定 + 与总制片消息互通(手机消息通道之一)。

连接方式对齐 OpenClaw 的飞书通道:
- 绑定:填入飞书开放平台「企业自建应用」的 App ID / App Secret,
  经 tenant_access_token 校验通过后持久化到 RUNTIME_DIR/feishu.json
- 收:官方 SDK(lark-oapi)WebSocket 长连接订阅 im.message.receive_v1
  (无需公网回调地址),跑在独立子进程 feishu_bridge.py 里,退出自动重启;
  仅单聊文本,转发给总制片(source="feishu")
- 发:订阅 HUB 聊天事件,经 im/v1/messages(receive_id_type=open_id)推回

前置条件(设置页有说明):应用需开启机器人能力、以「长连接」方式订阅
「接收消息 im.message.receive_v1」事件,并开通 im:message 收发权限;
绑定后需在飞书里先给机器人发一条消息,系统才知道该推送给谁。
lark-oapi 未安装时绑定被拒并提示 pip install lark-oapi。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

from . import channels, core

FEISHU_CONFIG_PATH = core.RUNTIME_DIR / "feishu.json"
BRIDGE_PATH = core.ROOT / "services" / "runtime" / "feishu_bridge.py"
BRIDGE_LOG = core.RUNTIME_DIR / "feishu_bridge.log"
DEFAULT_DOMAIN = "https://open.feishu.cn"

OUT_TEXT_LIMIT = 3000                     # 与微信通道一致:手机端可读性截断
API_TIMEOUT_S = 15

RELAY: dict = {"err_in": "", "err_out": "", "last_in": 0.0, "last_out": 0.0}
_ERR_IN_THRESHOLD = 3
_WAKE = asyncio.Event()                   # 绑定/解绑后立刻唤醒收循环
_TOKEN = {"v": "", "exp": 0.0, "key": ""}  # tenant_access_token 缓存


def _sdk_ok() -> bool:
    return importlib.util.find_spec("lark_oapi") is not None


def load_cfg() -> dict:
    try:
        return json.loads(FEISHU_CONFIG_PATH.read_text())
    except Exception:
        return {}


def save_cfg(cfg: dict):
    core.atomic_write_json(FEISHU_CONFIG_PATH, cfg)


# ---------------- 飞书 HTTP(阻塞式,线程里跑) ----------------

def _post_json(domain: str, path: str, body: dict, token: str | None,
               timeout: float) -> dict:
    url = (domain or DEFAULT_DOMAIN).rstrip("/") + path
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode(),
        headers=headers, method="POST")
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
        "relay": {"last_error": RELAY["err_in"] or RELAY["err_out"],
                  "last_in": RELAY["last_in"], "last_out": RELAY["last_out"]},
    }


async def api_feishu_bind(body: dict):
    """校验 App ID / App Secret 后保存,并(重)启长连接。"""
    app_id = (body.get("app_id") or "").strip()
    app_secret = (body.get("app_secret") or "").strip()
    domain = (body.get("domain") or "").strip() or DEFAULT_DOMAIN
    if not app_id or not app_secret:
        raise core.ServiceError(400, "App ID 与 App Secret 均不能为空")
    if not _sdk_ok():
        raise core.ServiceError(
            400, "未安装飞书 SDK:请先在服务端执行 pip install lark-oapi 再绑定")
    try:
        d = await asyncio.to_thread(_fetch_tenant_token, domain, app_id, app_secret)
    except (OSError, urllib.error.URLError) as e:
        raise core.ServiceError(502, f"连接飞书开放平台失败:{e}") from None
    if d.get("code") != 0:
        raise core.ServiceError(
            400, f"应用凭证校验失败(code={d.get('code')}):{d.get('msg') or d}")
    save_cfg({"app_id": app_id, "app_secret": app_secret, "domain": domain,
              "bound_at": time.time(), "contact": {}})
    _TOKEN.update(v="", key="", exp=0.0)
    RELAY["err_in"] = RELAY["err_out"] = ""
    _WAKE.set()
    return {"ok": True}


async def api_feishu_unbind():
    save_cfg({})
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
            c["open_id"] = ev["open_id"]
            save_cfg(cfg)
        try:
            await channels.forward_inbound("feishu", ev.get("text") or "")
            RELAY["last_in"] = time.time()
            RELAY["err_in"] = ""
        except Exception as e:  # noqa: BLE001
            RELAY["err_in"] = f"转发飞书消息给总制片失败:{e}"
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
                    RELAY["err_in"] = ("未安装飞书 SDK:请在服务端执行 "
                                       "pip install lark-oapi")
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


async def relay_loop():
    """后台常驻:收发两条循环(services.api lifespan 启动)。"""
    await asyncio.gather(
        _inbound_loop(),
        channels.outbound_loop("feishu", _send_to_feishu, RELAY))

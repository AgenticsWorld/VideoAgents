"""WhatsApp 接入:扫码配对 + 与总制片消息互通(手机消息通道之一)。

连接方式对齐 OpenClaw 的 WhatsApp 通道:
- 用 Baileys(WhatsApp Web 多设备协议)把本系统作为「已连接的设备」挂到
  用户自己的 WhatsApp 账号上——手机 WhatsApp「已连接的设备 → 连接设备」扫码,
  与 WhatsApp 网页版同一配对方式,无需官方 Business API
- 只把「给自己发消息」(Message Yourself 自聊)当作指令通道:自聊里发的文本
  转发给总制片(source="whatsapp"),回复与系统消息也推回自聊;
  他人来信一概不转发、不回复
- 收发跑在 Node 子进程 whatsapp_bridge.mjs(Baileys 为 Node 库),NDJSON 管道
  互通;会话凭证持久化在 RUNTIME_DIR/whatsapp/auth,重启免重新扫码

依赖:node(>=20)+ npm 包 baileys(仓库根目录 npm install 即装);
缺哪个,绑定时/状态里都会明确提示。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time

from . import channels, core

WA_DIR = core.RUNTIME_DIR / "whatsapp"
AUTH_DIR = WA_DIR / "auth"
WHATSAPP_CONFIG_PATH = core.RUNTIME_DIR / "whatsapp.json"
BRIDGE_PATH = core.ROOT / "services" / "runtime" / "whatsapp_bridge.mjs"
BRIDGE_LOG = core.RUNTIME_DIR / "whatsapp_bridge.log"

OUT_TEXT_LIMIT = 3000                     # 与微信通道一致:手机端可读性截断
PAIRING_WINDOW_S = 300                    # 点「生成二维码」后的配对窗口
SEND_ACK_TIMEOUT_S = 20

RELAY: dict = {"err_in": "", "err_out": "", "last_in": 0.0, "last_out": 0.0}
_ERR_IN_THRESHOLD = 3
_WAKE = asyncio.Event()                   # 绑定/解绑后立刻唤醒管理循环

# 桥子进程运行态(状态接口展示;由管理循环维护)
STATE: dict = {"proc": None, "connected": False, "qr": "", "qr_ts": 0.0,
               "pairing_until": 0.0}
# 发送命令的应答队列:send → 等 sent/send_err
_SEND_WAITERS: list[asyncio.Future] = []


def _node() -> str | None:
    return shutil.which("node")


def load_cfg() -> dict:
    try:
        return json.loads(WHATSAPP_CONFIG_PATH.read_text())
    except Exception:
        return {}


def save_cfg(cfg: dict):
    core.atomic_write_json(WHATSAPP_CONFIG_PATH, cfg)


def _bound() -> bool:
    return (AUTH_DIR / "creds.json").is_file()


# ---------------- 设置页 API ----------------

def _contact_label(cfg: dict) -> dict:
    c = cfg.get("contact") or {}
    return {"user_id": c.get("jid") or "", "name": c.get("name") or ""}


async def api_whatsapp_status():
    now = time.time()
    pairing = STATE["pairing_until"] > now and not _bound()
    qr = STATE["qr"] if pairing and now - STATE["qr_ts"] < 90 else ""
    return {
        "bound": _bound(),
        "bound_at": load_cfg().get("bound_at") or 0,
        "contact": _contact_label(load_cfg()),
        "connected": STATE["connected"],
        # ready=桥已连上,自聊双向可用(无需等首条消息,自聊对端就是本人)
        "ready": _bound() and STATE["connected"],
        "pairing": pairing,
        "qr_svg": core._qr_svg(qr) if qr else "",
        "node": bool(_node()),
        "relay": {"last_error": RELAY["err_in"] or RELAY["err_out"],
                  "last_in": RELAY["last_in"], "last_out": RELAY["last_out"]},
    }


async def api_whatsapp_bind():
    """开启一轮扫码配对窗口;前端轮询 status 取二维码与结果。"""
    if _bound():
        raise core.ServiceError(400, "已绑定 WhatsApp,如需换号请先解除绑定")
    if not _node():
        raise core.ServiceError(
            400, "未找到 node 可执行文件:WhatsApp 通道依赖 Node.js(>=20),请安装后重试")
    STATE["pairing_until"] = time.time() + PAIRING_WINDOW_S
    STATE["qr"], STATE["qr_ts"] = "", 0.0
    RELAY["err_in"] = RELAY["err_out"] = ""
    _WAKE.set()
    return {"ok": True, "window_s": PAIRING_WINDOW_S}


async def api_whatsapp_unbind():
    STATE["pairing_until"] = 0.0
    proc = STATE["proc"]
    if proc and proc.returncode is None:
        try:
            proc.stdin.write(json.dumps({"type": "logout"}).encode() + b"\n")
            await proc.stdin.drain()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()
    shutil.rmtree(AUTH_DIR, ignore_errors=True)
    save_cfg({})
    STATE.update(connected=False, qr="", qr_ts=0.0)
    RELAY["err_in"] = RELAY["err_out"] = ""
    _WAKE.set()
    return {"ok": True}


# ---------------- 桥子进程管理 + 收:WhatsApp → 总制片 ----------------

def _resolve_send_waiters(err: str | None):
    for fut in _SEND_WAITERS:
        if fut.done():
            continue
        if err:
            fut.set_exception(RuntimeError(err))
        else:
            fut.set_result(True)
    _SEND_WAITERS.clear()


async def _handle_event(ev: dict):
    t = ev.get("type")
    if t == "qr":
        STATE["qr"], STATE["qr_ts"] = ev.get("qr") or "", time.time()
    elif t == "open":
        STATE["connected"] = True
        STATE["qr"], STATE["pairing_until"] = "", 0.0
        cfg = load_cfg()
        if not cfg.get("bound_at"):
            cfg["bound_at"] = time.time()
        cfg["contact"] = {"jid": ev.get("jid") or "", "name": ev.get("name") or ""}
        save_cfg(cfg)
        RELAY["err_in"] = ""
    elif t == "close":
        STATE["connected"] = False
    elif t == "msg":
        try:
            await channels.forward_inbound("whatsapp", ev.get("text") or "")
            RELAY["last_in"] = time.time()
            RELAY["err_in"] = ""
        except Exception as e:  # noqa: BLE001
            RELAY["err_in"] = f"转发 WhatsApp 消息给总制片失败:{e}"
    elif t == "sent":
        _resolve_send_waiters(None)
    elif t == "send_err":
        _resolve_send_waiters(f"WhatsApp 发送失败:{ev.get('error')}")
    elif t == "logged_out":
        # 手机端解除了设备绑定:清会话,等用户重新扫码
        shutil.rmtree(AUTH_DIR, ignore_errors=True)
        save_cfg({})
        STATE["connected"] = False
        RELAY["err_in"] = "WhatsApp 已在手机端解除本设备的绑定,如需继续使用请重新扫码"
    elif t == "fatal" and ev.get("error") == "baileys_missing":
        RELAY["err_in"] = ("未安装 baileys 依赖:请在仓库根目录执行 npm install "
                           "后重启服务")


async def _kill(proc):
    if proc and proc.returncode is None:
        proc.kill()
        try:
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass


async def _spawn():
    node = _node()
    if not node:
        RELAY["err_in"] = "未找到 node 可执行文件:WhatsApp 通道依赖 Node.js(>=20)"
        return None
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    BRIDGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    logf = open(BRIDGE_LOG, "ab")  # noqa: SIM115  子进程持有,进程退出即释放
    try:
        return await asyncio.create_subprocess_exec(
            node, str(BRIDGE_PATH), cwd=str(core.ROOT),
            env={**os.environ, "WA_AUTH_DIR": str(AUTH_DIR)},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=logf)
    finally:
        logf.close()


async def _inbound_loop():
    fails = 0
    try:
        while True:
            want = _bound() or STATE["pairing_until"] > time.time()
            proc = STATE["proc"]
            alive = proc is not None and proc.returncode is None
            if not want:
                if alive:
                    await _kill(proc)
                STATE["proc"], STATE["connected"] = None, False
                _WAKE.clear()
                try:
                    await asyncio.wait_for(_WAKE.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass
                continue
            if not alive:
                STATE["proc"] = proc = await _spawn()
                STATE["connected"] = False
                if proc is None:
                    await asyncio.sleep(30)
                    continue
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
            except asyncio.TimeoutError:
                continue                  # 定期回头检查绑定/配对窗口状态
            if not line:                  # 子进程退出
                rc = await proc.wait()
                STATE["proc"], STATE["connected"] = None, False
                STATE["qr"] = ""
                _resolve_send_waiters("WhatsApp 桥进程已退出")
                if rc == 5 and not _bound():
                    # 一轮配对二维码超时;窗口还开着就再开一轮,否则静默收场
                    if STATE["pairing_until"] <= time.time():
                        continue
                elif rc in (3, 4):        # 缺依赖/已登出,事件里已给出提示
                    STATE["pairing_until"] = 0.0
                    await asyncio.sleep(5)
                    continue
                else:
                    fails += 1
                    if fails >= _ERR_IN_THRESHOLD:
                        RELAY["err_in"] = (f"WhatsApp 桥持续退出(rc={rc}),已自动重试;"
                                           f"详见日志 {BRIDGE_LOG.name}")
                    await asyncio.sleep(min(30, 5 * fails))
                continue
            try:
                ev = json.loads(line.decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001
                continue
            if ev.get("type") == "open":
                fails = 0
            await _handle_event(ev)
    finally:
        await _kill(STATE["proc"])


# ---------------- 发:总制片/系统 → WhatsApp(自聊) ----------------

async def _send_to_whatsapp(text: str) -> bool:
    proc = STATE["proc"]
    if not (_bound() and STATE["connected"] and proc and proc.returncode is None):
        return False                      # 未绑定或未连上,静默跳过
    if len(text) > OUT_TEXT_LIMIT:
        text = text[:OUT_TEXT_LIMIT] + "\n……(超长截断,全文见控制台)"
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    _SEND_WAITERS.append(fut)
    proc.stdin.write(json.dumps({"type": "send", "text": text},
                                ensure_ascii=False).encode() + b"\n")
    await proc.stdin.drain()
    await asyncio.wait_for(fut, timeout=SEND_ACK_TIMEOUT_S)
    return True


async def relay_loop():
    """后台常驻:桥管理/收循环 + 出站循环(services.api lifespan 启动)。"""
    await asyncio.gather(
        _inbound_loop(),
        channels.outbound_loop("whatsapp", _send_to_whatsapp, RELAY))

"""手机消息通道公共逻辑(微信 ClawBot / 飞书 / WhatsApp 共用)。

各通道模块(wechat / feishu / whatsapp)自持配置与收发实现,这里只沉淀三件事:
- forward_inbound:手机端文本 → 总制片对话(source=通道名,项目取顶栏当前项目)
- outbound_prefix:哪些聊天事件要推回手机端,以及消息头前缀
- outbound_loop:订阅 HUB 聊天事件的通用出站循环

出站规则:总制片的回复推给所有已绑定通道;系统自动消息(设置变更/自动运行等)
同样推送;网页端用户手输的(user)与本通道自己转发进来的不回推(避免回声),
其他通道转发进来的用户消息则带来源标注同步过来(多端看到同一份对话)。
"""

from __future__ import annotations

import time

from . import core

# 通道来源标识 → 出站消息头标注(推到其他通道时标明消息来自哪端)
CHANNEL_LABELS = {
    "wechat": "💬 微信",
    "feishu": "📱 飞书",
    "whatsapp": "📱 WhatsApp",
}

_SRC_PREFIX = {"settings": "⚙️ 设置变更", "watchdog": "🤖 自动运行", **CHANNEL_LABELS}


async def forward_inbound(source: str, text: str):
    """手机端文本 → 总制片对话(source=通道名,项目取顶栏当前项目)。"""
    text = (text or "").strip()
    if not text:
        return
    orch = core.ORCHESTRATOR_AGENT
    proj = core.ui_prefs_pref()["project"]
    if not proj:
        projs = await core.api_projects()
        proj = projs[0] if projs else "demo"
    gm = core.agent_effective_model(orch)
    await core.api_chat({"agent": orch, "message": text, "project": proj,
                         "source": source,
                         "engine": gm["engine"], "model": gm["model"]})


def outbound_prefix(ev: dict, own_source: str) -> str | None:
    """哪些聊天事件推回本通道:总制片的回复 + 系统/其他通道的用户消息。
    网页端用户手输的(user)与本通道自己转发进来的不回推,避免回声。"""
    role, src = ev.get("role"), ev.get("source") or ""
    if role == "assistant":
        return "🎬 总制片"
    if role == "user" and src not in ("user", own_source):
        return _SRC_PREFIX.get(src, "📣 系统")
    return None


async def outbound_loop(own_source: str, send_fn, relay: dict):
    """通用出站循环:订阅 HUB,把总制片回复/系统消息经 send_fn 推到手机端。

    send_fn: async (text) -> bool,实际发出返回真;未绑定时静默返回假;
    relay: 通道状态 dict,成功刷 last_out、清 err_out,失败记 err_out。
    """
    q = core.HUB.subscribe()
    try:
        while True:
            ev = await q.get()
            if ev.get("type") != "chat" or ev.get("agent") != core.ORCHESTRATOR_AGENT:
                continue
            prefix = outbound_prefix(ev, own_source)
            text = (ev.get("text") or "").strip()
            if not prefix or not text:
                continue
            proj = ev.get("project") or ""
            head = f"{prefix} · {proj}" if proj else prefix
            try:
                if await send_fn(f"{head}\n{text}"):
                    relay["last_out"] = time.time()
                    relay["err_out"] = ""
            except Exception as e:  # noqa: BLE001
                # 发送失败意味着这条消息丢了,值得提示;下次发送成功自动清除
                relay["err_out"] = f"推送失败:{e}"
    finally:
        core.HUB.unsubscribe(q)

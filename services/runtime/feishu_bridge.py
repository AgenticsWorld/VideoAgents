#!/usr/bin/env python3
"""飞书长连接收消息桥(独立子进程,由 services.runtime.feishu 拉起管理)。

用官方 SDK(lark-oapi)的 WebSocket 长连接模式订阅 im.message.receive_v1,
无需公网回调地址——与 OpenClaw 飞书通道同一接法。SDK 的 ws 客户端持有模块级
事件循环且无公开的停止接口,放独立进程里跑,父进程 kill 即停,崩溃互不影响。

凭证经环境变量传入(FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_DOMAIN);
事件按 NDJSON 写 stdout:
  {"type":"started"}                             SDK 就绪,开始连接
  {"type":"msg","open_id":...,"chat_id":...,"name":...,"text":...}   p2p 文本消息
  {"type":"card","confirm_id":...,"option":...,"open_id":...,"message_id":...}
                                                 确认/签字卡片按钮点击(card.action.trigger,
                                                 同一条长连接回传,需在开放平台把「回调订阅」
                                                 也设为长连接方式)
  {"type":"err","error":...}                     单条消息解析失败(不退出)
  {"type":"fatal","error":"sdk_missing"}         未安装 lark-oapi(rc=3)
仅转发单聊(p2p)文本;群聊/富文本/图片等不透传。
"""

from __future__ import annotations

import json
import os
import sys


def emit(obj: dict):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    app_id = os.environ.get("FEISHU_APP_ID") or ""
    app_secret = os.environ.get("FEISHU_APP_SECRET") or ""
    domain = os.environ.get("FEISHU_DOMAIN") or "https://open.feishu.cn"
    if not app_id or not app_secret:
        emit({"type": "fatal", "error": "missing_credentials"})
        return 2
    try:
        import lark_oapi as lark
    except ImportError:
        emit({"type": "fatal", "error": "sdk_missing"})
        return 3

    def on_message(data):
        try:
            msg = data.event.message
            if (msg.chat_type or "") != "p2p" or (msg.message_type or "") != "text":
                return                    # 仅单聊文本;群聊/媒体不透传
            text = (json.loads(msg.content or "{}").get("text") or "").strip()
            if not text:
                return
            sender = data.event.sender.sender_id
            emit({"type": "msg", "open_id": sender.open_id or "",
                  "chat_id": msg.chat_id or "", "text": text})
        except Exception as e:  # noqa: BLE001
            emit({"type": "err", "error": str(e)})

    from lark_oapi.event.callback.model.p2_card_action_trigger import (
        P2CardActionTriggerResponse)

    def ack_card(question: str, opt: str) -> dict:
        """点击后原地替换的"已提交"卡片:无按钮,防重复点击。布局与父进程
        feishu._card 保持一致(本进程刻意不 import 父模块,避免拉起整个 core)。"""
        # 旧卡片(改版前发出)的 value 没有 q,占位文本避免空 div 被飞书拒收
        els = [{"tag": "div", "text": {"tag": "plain_text",
                                       "content": question or "确认/签字项"}},
               {"tag": "note", "elements": [
                   {"tag": "plain_text",
                    "content": f"已提交「{opt}」,处理中…结果会更新到这张卡片。"}]}]
        return {"config": {"wide_screen_mode": True},
                "header": {"template": "turquoise",
                           "title": {"tag": "plain_text", "content": "⏳ 已提交"}},
                "elements": els}

    def on_card(data):
        """确认/签字卡片按钮点击透传给父进程(value 带 confirm_id/option/q,字段
        平铺)。响应里同时带 toast 和整张替换卡片:飞书收到即原地换成无按钮的
        "已提交"态,不留可重复点击的窗口;终态(已处理/超时/失效)由父进程 PATCH。"""
        try:
            ev = data.event
            v = (ev.action.value if ev and ev.action else None) or {}
            base = {"open_id": (ev.operator.open_id if ev.operator else "") or "",
                    "message_id": (ev.context.open_message_id
                                   if ev.context else "") or ""}
            cid = str(v.get("confirm_id") or "")
            opt = str(v.get("option") or "")
            if cid and opt:               # 确认/签字卡片(字段平铺)
                emit({"type": "card", "confirm_id": cid, "option": opt, **base})
                return P2CardActionTriggerResponse(
                    {"toast": {"type": "success", "content": f"已提交:{opt}"},
                     "card": {"type": "raw",
                              "data": ack_card(str(v.get("q") or ""), opt)}})
        except Exception as e:  # noqa: BLE001
            emit({"type": "err", "error": str(e)})
        return P2CardActionTriggerResponse({})

    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_im_message_receive_v1(on_message)
               .register_p2_card_action_trigger(on_card)
               .build())
    client = lark.ws.Client(app_id, app_secret, event_handler=handler,
                            domain=domain, log_level=lark.LogLevel.WARNING)
    emit({"type": "started"})
    client.start()                        # 阻塞;连接失败抛异常 → 进程退出,父进程重启
    return 0


if __name__ == "__main__":
    sys.exit(main())

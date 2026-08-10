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
  {"type":"card","value":{...},"open_id":...,"message_id":...}
                                                 泛化控制卡片按钮点击(value 无 confirm_id
                                                 时整体透传,如 /auto 自动运行开关卡片)
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

    def on_card(data):
        """卡片按钮点击透传给父进程:确认/签字卡片 value 带 confirm_id/option,
        平铺成老格式;其余(如 /auto 自动运行开关)把 value 整体透传,父进程按
        action 分发。返回 toast 让手机端立即看到已提交;卡片本体的收尾/刷新
        由父进程走 PATCH。"""
        try:
            ev = data.event
            v = (ev.action.value if ev and ev.action else None) or {}
            base = {"open_id": (ev.operator.open_id if ev.operator else "") or "",
                    "message_id": (ev.context.open_message_id
                                   if ev.context else "") or ""}
            cid = str(v.get("confirm_id") or "")
            opt = str(v.get("option") or "")
            if cid and opt:               # 确认/签字卡片(老格式,字段平铺)
                emit({"type": "card", "confirm_id": cid, "option": opt, **base})
                return P2CardActionTriggerResponse(
                    {"toast": {"type": "success", "content": f"已提交:{opt}"}})
            if v:                         # 泛化控制卡片:value 整体透传
                emit({"type": "card", "value": v, **base})
                return P2CardActionTriggerResponse(
                    {"toast": {"type": "success",
                               "content": str(v.get("toast") or "已收到")}})
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

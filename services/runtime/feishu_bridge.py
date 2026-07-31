#!/usr/bin/env python3
"""飞书长连接收消息桥(独立子进程,由 services.runtime.feishu 拉起管理)。

用官方 SDK(lark-oapi)的 WebSocket 长连接模式订阅 im.message.receive_v1,
无需公网回调地址——与 OpenClaw 飞书通道同一接法。SDK 的 ws 客户端持有模块级
事件循环且无公开的停止接口,放独立进程里跑,父进程 kill 即停,崩溃互不影响。

凭证经环境变量传入(FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_DOMAIN);
事件按 NDJSON 写 stdout:
  {"type":"started"}                             SDK 就绪,开始连接
  {"type":"msg","open_id":...,"chat_id":...,"name":...,"text":...}   p2p 文本消息
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

    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_im_message_receive_v1(on_message)
               .build())
    client = lark.ws.Client(app_id, app_secret, event_handler=handler,
                            domain=domain, log_level=lark.LogLevel.WARNING)
    emit({"type": "started"})
    client.start()                        # 阻塞;连接失败抛异常 → 进程退出,父进程重启
    return 0


if __name__ == "__main__":
    sys.exit(main())

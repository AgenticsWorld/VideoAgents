"""运行中插话(steer,2026-10-22):把用户追加的消息送进正在运行的 Agent 任务。

控制台 Ctrl+Enter 调 core.api_steer;每个运行中的引擎进程一条通道(core.RUN_STEER),
插话条目记在 run["steers"]({text, ts, delivered, via}),未送达的由宿主兜底(并进下一个进程的
提示词,或任务结束后补派一单)。各引擎口径(本机 claude 2.1.308 / pi 0.99.2 / opencode 2.1.2 实测):

- claude:`-p --input-format stream-json`,提示词作为 stdin 的一行 user 消息写入、stdin 保持打开;
  运行中再写一行 user 消息,CLI 在当前工具调用结束后插进本轮(实测只出一个 result)。
  `--replay-user-messages` 在每条 stdin 消息被取用时回放一条 isReplay 的 user 事件,据此确认送达。
  出 result 后进程不退、等下一条输入:没有未送达的插话就关 stdin 让它收尾;还有未送达的
  (插话撞上本轮最后一次模型输出,没有工具边界可插)CLI 会把它当下一轮接着跑,再出一个 result。
- pi / Agentics(内置 pi):`--mode rpc`,stdin 发 JSONL 命令。提示词走 prompt 命令;插话走
  prompt + streamingBehavior=steer——运行中排进 steer 队列(本轮工具执行完、下次调用模型前送达),
  已空闲则直接开一轮,兜住收尾竞态。RPC 模式不发 session 事件,会话 id 取 get_state 的回执。
  agent_settled = 不会再自动继续;此时插话命令都已回执才关 stdin(pi 读到 EOF 即收尾退出,
  运行中关 stdin 会把这一单直接掐断)。扩展的对话框请求(select / confirm / input / editor)
  一律回 cancelled,与 json 模式没有界面时一致。
- opencode 2.x:运行用 --standalone 私有服务器,不暴露地址;插话另起
  `opencode api --standalone POST /api/session/<id>/prompt`(delivery=steer、resume=false):
  只写进会话的持久化 inbox、不在这个短命进程里起执行,正在跑的进程在下一步边界从库里取走
  (实测送达)。收尾后仍留在 inbox 的(会话已空闲才写入)撤回,按未送达处理。1.x 无此接口。
- codex exec / kimi -p / grok -p / agy -p:进程起来后没有输入通道,不支持(排队兜底)。
"""
from __future__ import annotations

import asyncio
import json
import time

from services.runtime import opencode_cli

CLAUDE_CLOSE_GRACE_S = 20     # result 后仍有未送达插话:等 CLI 取用的宽限,过期即关 stdin
PI_PROMPT_ID = "va-prompt"
PI_STATE_ID = "va-state"


def steer_message(text: str) -> str:
    """运行中插话送给 Agent 的正文(对话记录里存用户原话)。"""
    return ("【用户插话】用户在你执行当前任务期间追加了以下指令。请结合当前进度一并处理,"
            "然后继续完成当前任务(这不是一张新工单):\n\n" + text)


def fold_steers(message: str, texts: list[str]) -> str:
    """排队中 / 进程未起 / 上个进程没送达的插话:并进本次提示词末尾。"""
    blocks = "\n\n".join(texts)
    return f"{message}\n\n---\n\n## 用户追加指令(在本工单开始执行前补充,与上文一并处理)\n\n{blocks}"


def requeue_message(texts: list[str]) -> str:
    """任务结束前没送达的插话:补派一单时的正文。"""
    return ("【用户插话·补发】以下指令是用户在上一单运行期间追加的,未能在其结束前送达。"
            "请结合上一单的结果处理:\n\n" + "\n\n".join(texts))


def _text_of(content) -> str:
    """user 消息正文:字符串或 [{type: text, text}] 内容块。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text") or "" for c in content
                       if isinstance(c, dict) and c.get("type") == "text")
    return ""


class SteerChannel:
    """一个引擎进程的插话通道。steer() 先同步把消息交给引擎(写 stdin / 排后台任务)再 await,
    调用方在 await 之前已把条目挂到 run["steers"],运行收尾不会漏看。"""
    engine = ""

    def __init__(self):
        self.open = True                    # 还接受插话
        self.items: list[dict] = []         # 本进程收下的插话条目(与 run["steers"] 同一批 dict)
        self.close_deadline: float | None = None

    def _match(self, text: str) -> dict | None:
        """引擎回放的 user 消息 → 对应的未送达条目(按正文匹配,匹配不上按先后取最早一条)。"""
        pend = [i for i in self.items if not i.get("delivered") and not i.get("failed")]
        for i in pend:
            if steer_message(i["text"]) == text or i["text"] in text:
                return i
        return None

    def _deliver(self, item: dict | None) -> list[dict]:
        if not item:
            return []
        item["delivered"] = True
        item["via"] = "run"
        item["delivered_ts"] = time.time()
        return [item]

    async def steer(self, item: dict) -> bool:
        raise NotImplementedError

    def on_event(self, obj: dict) -> list[dict]:
        """引擎事件 → 本条事件确认送达的条目。"""
        return []

    def on_deadline(self):
        """close_deadline 到期(core 读循环超时回调)。"""

    def shutdown(self):
        """进程已退出 / 将被杀:不再接受插话。"""
        self.open = False
        self.close_deadline = None

    async def finalize(self):
        """进程退出后核对送达情况(opencode 查 inbox)。"""


class _StdinChannel(SteerChannel):
    def __init__(self, proc):
        super().__init__()
        self.proc = proc

    def _write(self, obj: dict) -> bool:
        stdin = self.proc.stdin
        if stdin is None or stdin.is_closing():
            return False
        try:
            stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError, RuntimeError):
            return False
        return True

    async def _drain(self):
        stdin = self.proc.stdin
        if stdin is None or stdin.is_closing():
            return
        try:
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError):
            pass

    def close_stdin(self):
        self.shutdown()
        stdin = self.proc.stdin
        if stdin is not None and not stdin.is_closing():
            try:
                stdin.close()
            except Exception:
                pass

    async def _send(self, item: dict, obj: dict) -> bool:
        if not self.open or not self._write(obj):
            return False
        self.items.append(item)
        await self._drain()
        return True


class ClaudeSteer(_StdinChannel):
    engine = "claude"

    def __init__(self, proc):
        super().__init__(proc)
        self._replays = 0

    @staticmethod
    def _line(text: str) -> dict:
        return {"type": "user", "message": {"role": "user", "content": text}}

    async def start(self, message: str) -> bool:
        ok = self._write(self._line(message))
        await self._drain()
        return ok

    async def steer(self, item: dict) -> bool:
        ok = await self._send(item, self._line(steer_message(item["text"])))
        return ok

    def on_event(self, obj: dict) -> list[dict]:
        t = obj.get("type")
        if t == "user" and (obj.get("isReplay") or obj.get("is_replay")):
            self._replays += 1
            if self._replays == 1:          # 第一条回放是本单提示词
                return []
            self.close_deadline = None      # CLI 已在取用排队的插话(可能另起一轮),等它的 result
            return self._deliver(self._match(_text_of((obj.get("message") or {}).get("content"))))
        if t == "result":
            pend = [i for i in self.items if not i.get("delivered")]
            if obj.get("is_error") or not pend:
                self.close_stdin()
            else:
                self.close_deadline = time.time() + CLAUDE_CLOSE_GRACE_S
        return []

    def on_deadline(self):
        self.close_stdin()


class PiSteer(_StdinChannel):
    engine = "pi"

    def __init__(self, proc):
        super().__init__(proc)
        self._seq = 0
        self._awaiting: dict[str, dict] = {}   # 插话命令 id → 条目(等回执)
        self._settled = False
        self._users = 0

    async def start(self, message: str) -> bool:
        ok = (self._write({"type": "set_steering_mode", "mode": "all"})
              and self._write({"id": PI_STATE_ID, "type": "get_state"})
              and self._write({"id": PI_PROMPT_ID, "type": "prompt", "message": message}))
        await self._drain()
        return ok

    async def steer(self, item: dict) -> bool:
        if not self.open:
            return False
        self._seq += 1
        rid = f"va-steer-{self._seq}"
        ok = self._write({"id": rid, "type": "prompt", "message": steer_message(item["text"]),
                          "streamingBehavior": "steer"})
        if not ok:
            return False
        self._awaiting[rid] = item
        self.items.append(item)
        await self._drain()
        return True

    def on_event(self, obj: dict) -> list[dict]:
        t = obj.get("type")
        if t == "response":
            rid = obj.get("id")
            disp = (obj.get("data") or {}).get("disposition")
            if rid == PI_PROMPT_ID:
                if not obj.get("success") or disp == "handled":
                    self.close_stdin()      # 提示词被拒 / 被扩展命令消化:不会有运行,不等 agent_settled
                return []
            item = self._awaiting.pop(rid, None) if rid else None
            if item is None:
                return []
            out = []
            if not obj.get("success"):
                item["failed"] = True       # 被拒:按未送达,收尾补派
            elif disp == "handled":
                out = self._deliver(item)   # 被输入处理器消化,不会再出现 user 消息
            if self._settled and not self._awaiting and disp != "started":
                self.close_stdin()
            return out
        if t == "agent_start":
            self._settled = False
        elif t == "agent_settled":
            self._settled = True
            if not self._awaiting:
                self.close_stdin()
            # 还有插话命令没回执:pi 收到时已空闲则开新一轮(回执 started),跑完再来一次 agent_settled
        elif t == "message_end" and (obj.get("message") or {}).get("role") == "user":
            self._users += 1
            if self._users > 1:
                return self._deliver(self._match(_text_of((obj.get("message") or {}).get("content"))))
        elif (t == "extension_ui_request"
              and obj.get("method") in ("select", "confirm", "input", "editor")):
            self._write({"type": "extension_ui_response", "id": obj.get("id"), "cancelled": True})
        return []


class OpencodeSteer(SteerChannel):
    engine = "opencode"

    def __init__(self, exe: str, env: dict, cwd):
        super().__init__()
        self.exe, self.env, self.cwd = exe, env, cwd
        self.sid: str | None = None
        self._buffer: list[dict] = []
        self._tasks: list[asyncio.Task] = []

    def _post(self, item: dict):
        self._tasks.append(asyncio.get_running_loop().create_task(self._post_one(item)))

    async def _post_one(self, item: dict):
        iid = await asyncio.to_thread(opencode_cli.steer_prompt, self.exe, self.sid,
                                      steer_message(item["text"]), self.env, self.cwd)
        if iid:
            item["ref"] = iid
        else:
            item["failed"] = True

    async def steer(self, item: dict) -> bool:
        if not self.open:
            return False
        self.items.append(item)
        if self.sid:
            self._post(item)
        else:
            self._buffer.append(item)   # 会话 id 随首个事件到达,到了再发
        return True

    def on_event(self, obj: dict) -> list[dict]:
        sid = obj.get("sessionID")
        if sid and not self.sid:
            self.sid = sid
            for item in self._buffer:
                self._post(item)
            self._buffer.clear()
        return []

    async def finalize(self):
        self.shutdown()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        posted = [i for i in self.items if i.get("ref") and not i.get("delivered")]
        if not posted:
            return
        pending = await asyncio.to_thread(opencode_cli.inbox_ids, self.exe, self.sid,
                                          self.env, self.cwd)
        for item in posted:
            if pending is not None and item["ref"] in pending:
                # 会话空闲后才写进 inbox:撤回,收尾补派(不撤会混进该会话的下一张工单)
                await asyncio.to_thread(opencode_cli.inbox_cancel, self.exe, self.sid,
                                        item["ref"], self.env, self.cwd)
                item["failed"] = True
            else:
                self._deliver(item)

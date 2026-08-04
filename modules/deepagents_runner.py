#!/usr/bin/env python
"""deepagents 引擎 runner —— 用 OpenAI 兼容端点(如 LM Studio 本地模型)驱动 deepagents。

由 services/runtime/core.py 以子进程方式调用(解释器由 DEEPAGENTS_PY 指定):
  runner --model <id> --base-url <url> --api-key <key>
         [--checkpoint-db <sqlite>] [--thread-id <id>]
  系统提示词与工作指令经环境变量 DA_SYSTEM / DA_PROMPT 传入(避免超长 argv)。
  --checkpoint-db 给出时启用 LangGraph SqliteSaver 会话续接(Agent记忆):
  消息历史(含工具结果)按 thread_id 持久化;--thread-id 缺省则新开会话并生成 id。
  续接一个不存在的 thread_id 只得到空历史(全新会话),不报错。

stdout 输出 JSONL 事件(server.handle_deepagents_event 解析):
  {"type":"session","session_id":...}   本次会话 thread_id(仅启用检查点时)
  {"type":"text","text":...}        assistant 文本增量
  {"type":"tool","name":...,"input":{...}}   工具调用
  {"type":"usage","input_tokens":N,"output_tokens":N}
  {"type":"result","text":...}      最终回复
  {"type":"error","message":...}
"""
import argparse
import ipaddress
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def content_text(c) -> str:
    """AIMessage.content 可能是 str 或分块 list。"""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in c)
    return str(c or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--api-key", default="lm-studio")
    ap.add_argument("--recursion-limit", type=int, default=100)
    ap.add_argument("--checkpoint-db", default=None)
    ap.add_argument("--thread-id", default=None)
    args = ap.parse_args()

    system = os.environ.get("DA_SYSTEM") or ""
    prompt = os.environ.get("DA_PROMPT") or ""
    if not prompt:
        emit({"type": "error", "message": "DA_PROMPT 为空"})
        sys.exit(1)

    from langchain_openai import ChatOpenAI
    from langchain_core.messages import AIMessage
    from deepagents import create_deep_agent
    from deepagents.backends import LocalShellBackend

    # 本地/内网端点(LM Studio 等)强制直连:macOS 系统代理(如 Surge 6152)会劫持
    # localhost 请求并返回 HTML 错误页;远端渠道仍走环境代理设置
    client_kw = {}
    host = urlparse(args.base_url).hostname or ""
    try:
        local = ipaddress.ip_address(host).is_loopback or \
            ipaddress.ip_address(host).is_private
    except ValueError:
        local = host == "localhost"
    if local:
        import httpx
        client_kw = {"http_client": httpx.Client(trust_env=False),
                     "http_async_client": httpx.AsyncClient(trust_env=False)}

    model = ChatOpenAI(model=args.model, base_url=args.base_url,
                       api_key=args.api_key, timeout=600, max_retries=1,
                       **client_kw)
    # LocalShellBackend = 真实文件系统读写 + shell 执行,root 为工作区根目录
    backend = LocalShellBackend(root_dir=str(ROOT), virtual_mode=False,
                                inherit_env=True, timeout=600)
    # 会话续接(Agent记忆):SqliteSaver 按 thread_id 跨进程持久化消息历史。
    # 仅在启用时才传 checkpointer 参数,保证无记忆路径与旧版 deepagents 兼容
    config = {"recursion_limit": args.recursion_limit}
    agent_kw = {}
    if args.checkpoint_db:
        import sqlite3
        import uuid
        from langgraph.checkpoint.sqlite import SqliteSaver
        # SqliteSaver 内部自带锁串行化访问,跨线程复用连接须关 check_same_thread
        conn = sqlite3.connect(args.checkpoint_db, check_same_thread=False)
        agent_kw["checkpointer"] = SqliteSaver(conn)
        thread_id = args.thread_id or uuid.uuid4().hex
        config["configurable"] = {"thread_id": thread_id}
        emit({"type": "session", "session_id": thread_id})
    agent = create_deep_agent(model=model, system_prompt=system, backend=backend,
                              **agent_kw)

    last_text = ""
    in_tok = out_tok = 0
    try:
        for upd in agent.stream(
                {"messages": [{"role": "user", "content": prompt}]},
                stream_mode="updates",
                config=config):
            for _node, data in (upd or {}).items():
                for m in (data or {}).get("messages", []) or []:
                    if not isinstance(m, AIMessage):
                        continue
                    txt = content_text(m.content)
                    if txt:
                        last_text = txt
                        emit({"type": "text", "text": txt})
                    for tc in (m.tool_calls or []):
                        emit({"type": "tool", "name": tc.get("name", "?"),
                              "input": tc.get("args") or {}})
                    u = getattr(m, "usage_metadata", None) or {}
                    if u:
                        in_tok += u.get("input_tokens") or 0
                        out_tok += u.get("output_tokens") or 0
        emit({"type": "usage", "input_tokens": in_tok, "output_tokens": out_tok})
        emit({"type": "result", "text": last_text or "(无输出)"})
    except Exception as e:  # noqa: BLE001
        emit({"type": "error", "message": f"{type(e).__name__}: {e}"[:500]})
        sys.exit(1)


if __name__ == "__main__":
    main()

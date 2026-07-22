#!/usr/bin/env python
"""deepagents 引擎 runner —— 用 OpenAI 兼容端点(如 LM Studio 本地模型)驱动 deepagents。

由 services/runtime/core.py 以子进程方式调用(解释器由 DEEPAGENTS_PY 指定):
  runner --model <id> --base-url <url> --api-key <key>
  系统提示词与工作指令经环境变量 DA_SYSTEM / DA_PROMPT 传入(避免超长 argv)。

stdout 输出 JSONL 事件(server.handle_deepagents_event 解析):
  {"type":"text","text":...}        assistant 文本增量
  {"type":"tool","name":...,"input":{...}}   工具调用
  {"type":"usage","input_tokens":N,"output_tokens":N}
  {"type":"result","text":...}      最终回复
  {"type":"error","message":...}
"""
import argparse
import json
import os
import sys
from pathlib import Path

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

    model = ChatOpenAI(model=args.model, base_url=args.base_url,
                       api_key=args.api_key, timeout=600, max_retries=1)
    # LocalShellBackend = 真实文件系统读写 + shell 执行,root 为工作区根目录
    backend = LocalShellBackend(root_dir=str(ROOT), virtual_mode=False,
                                inherit_env=True, timeout=600)
    agent = create_deep_agent(model=model, system_prompt=system, backend=backend)

    last_text = ""
    in_tok = out_tok = 0
    try:
        for upd in agent.stream(
                {"messages": [{"role": "user", "content": prompt}]},
                stream_mode="updates",
                config={"recursion_limit": args.recursion_limit}):
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

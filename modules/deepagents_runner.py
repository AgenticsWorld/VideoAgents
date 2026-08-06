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

DEFAULT_CONTEXT_WINDOW = 128_000
DEFAULT_MAX_OUTPUT_TOKENS = 8_192
# LangGraph 默认 25 太低;50 在 orchestrator / 多工具 worker 上也会稳定撞上限
# (实测 SelfTest 调度与 p4-costume 等单次 50+ 工具调用后 GraphRecursionError)。
# 与 webui Claude MAX_TURNS=100 对齐:每轮约 2 个图步,取 250 作为工人默认。
DEFAULT_RECURSION_LIMIT = 250
DEFAULT_CONTEXT_HEADROOM = 16_384
DEFAULT_SUMMARY_TRIGGER_TOKENS = 48_000
DEFAULT_SUMMARY_KEEP_TOKENS = 8_000
DEFAULT_SUMMARY_INPUT_TOKENS = 24_000
DEFAULT_MAX_TOOL_OUTPUT_BYTES = 24_000
DEFAULT_MAX_TOOL_MESSAGE_CHARS = 24_000
DEFAULT_MAX_TOOL_ARGUMENT_CHARS = 8_000
INITIAL_INPUT_BUDGET_RATIO = 0.60


def runtime_paths() -> tuple[Path, Path | None]:
    """Resolve the one host-path view shared by file tools and shell commands."""
    workspace = Path(
        os.environ.get("VIDEOAGENTS_WORKSPACE_ROOT") or ROOT
    ).expanduser().resolve()
    project_value = (os.environ.get("VIDEOAGENTS_PROJECT_ROOT") or "").strip()
    if project_value:
        project_root = Path(project_value).expanduser().resolve()
    else:
        project = (os.environ.get("VIDEOAGENTS_PROJECT") or "").strip()
        data_root = Path(
            os.environ.get("VIDEOAGENTS_DATA_DIR") or workspace / "data"
        ).expanduser().resolve()
        project_root = (data_root / "projects" / project).resolve() if project else None
    return workspace, project_root


def path_contract(workspace: Path, project_root: Path | None) -> str:
    project_line = (str(project_root) if project_root else
                    "(未指定；只使用工单给出的真实绝对路径)")
    return f"""## DeepAgents 路径契约（强制）

- 仓库根目录（真实宿主机路径）：`{workspace}`
- 当前项目根目录（真实宿主机路径）：`{project_line}`
- 文件工具（ls/read_file/write_file/edit_file/grep）与 execute shell 共享同一宿主机文件系统；同一绝对路径必须指向同一文件。
- 文件工具始终使用上面给出的真实绝对路径。不要把仓库根虚拟成 `/`，不要自行改写为 `/data/projects/...`、`/workspace/...` 或重复拼接仓库根。
- execute 的初始工作目录是仓库根；项目产物的相对路径均以当前项目根为基准，仓库脚本/配置的相对路径均以仓库根为基准。存在歧义时使用上面的真实绝对路径。
- 工单中的 `story/...`、`bible/...`、`runs/...` 等均是项目内相对路径，必须拼到当前项目根；`agents/...`、`modules/...`、`services/...` 是仓库内相对路径，必须拼到仓库根。
"""


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


def env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ValueError(f"{name} 必须是整数,当前值:{raw!r}") from e


def estimate_tokens(text: str) -> int:
    """Conservative approximation for mixed Chinese and English text."""
    if not text:
        return 0
    return max((len(text) + 3) // 4, (len(text.encode("utf-8")) + 2) // 3)


def context_limits(context_window: int, max_output_tokens: int) -> tuple[int, int]:
    if context_window < 8_192:
        raise ValueError(f"context window 过小:{context_window},至少需要 8192")
    if max_output_tokens < 256:
        raise ValueError(f"max output tokens 过小:{max_output_tokens},至少需要 256")
    if max_output_tokens >= context_window // 2:
        raise ValueError(
            f"max output tokens({max_output_tokens}) 必须小于 context window 的一半"
        )
    max_input_tokens = context_window - max_output_tokens
    initial_input_budget = int(max_input_tokens * INITIAL_INPUT_BUDGET_RATIO)
    return max_input_tokens, initial_input_budget


def _json_text(value) -> str:
    if hasattr(value, "model_dump"):
        try:
            value = value.model_dump(mode="json")
        except Exception:
            pass
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def conservative_message_tokens(messages, tools=None) -> int:
    """Count messages and tool schemas conservatively for mixed Chinese text."""
    text = _json_text(messages)
    if tools:
        text += _json_text(tools)
    return estimate_tokens(text)


def _clip_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = max(20, int(limit * 0.70))
    tail = max(0, limit - head)
    return f"{text[:head]}\n...[内容已截断,请按原工具参数分段读取]...\n{text[-tail:]}"


def compact_messages(messages, max_chars: int = DEFAULT_MAX_TOOL_MESSAGE_CHARS):
    """Keep tool results bounded before a request reaches the model endpoint."""
    from langchain_core.messages import AIMessage, ToolMessage

    compacted = []
    for message in messages:
        if isinstance(message, ToolMessage) and isinstance(message.content, str):
            if len(message.content) > max_chars:
                message = message.model_copy(update={
                    "content": _clip_text(message.content, max_chars),
                })
        elif isinstance(message, AIMessage) and message.tool_calls:
            calls = []
            changed = False
            for call in message.tool_calls:
                args = dict(call.get("args") or {})
                clipped_args = {
                    key: _clip_text(value, DEFAULT_MAX_TOOL_ARGUMENT_CHARS)
                    if isinstance(value, str) else value
                    for key, value in args.items()
                }
                changed |= clipped_args != args
                calls.append({**call, "args": clipped_args})
            if changed:
                message = message.model_copy(update={"tool_calls": calls})
        compacted.append(message)
    return compacted


def is_context_overflow(error: Exception) -> bool:
    text = str(error).lower()
    return any(token in text for token in (
        "context size", "context length", "maximum context", "context window",
        "prompt is too long", "too many tokens", "max tokens",
    ))


def build_safe_summarizer(
    model, backend, context_window: int, max_output_tokens: int
):
    """Use bounded summarization instead of deepagents' unbounded factory default."""
    from deepagents.middleware.summarization import SummarizationMiddleware
    from langchain_core.exceptions import ContextOverflowError

    class SafeSummarizationMiddleware(SummarizationMiddleware):
        def wrap_model_call(self, request, handler):
            safe_request = request.override(
                messages=compact_messages(request.messages))

            def guarded_handler(inner_request):
                try:
                    return handler(inner_request)
                except Exception as error:  # noqa: BLE001
                    if is_context_overflow(error):
                        raise ContextOverflowError(str(error)) from error
                    raise

            return super().wrap_model_call(safe_request, guarded_handler)

    usable = max(
        8_192,
        context_window - max_output_tokens - DEFAULT_CONTEXT_HEADROOM,
    )
    trigger = min(DEFAULT_SUMMARY_TRIGGER_TOKENS, usable // 2)
    return SafeSummarizationMiddleware(
        model=model,
        backend=backend,
        trigger=("tokens", trigger),
        keep=("tokens", min(DEFAULT_SUMMARY_KEEP_TOKENS, trigger // 4)),
        token_counter=conservative_message_tokens,
        trim_tokens_to_summarize=DEFAULT_SUMMARY_INPUT_TOKENS,
        truncate_args_settings={
            "trigger": ("tokens", trigger // 2),
            "keep": ("tokens", min(DEFAULT_SUMMARY_KEEP_TOKENS, trigger // 4)),
            "max_length": DEFAULT_MAX_TOOL_ARGUMENT_CHARS,
        },
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--api-key", default="lm-studio")
    ap.add_argument(
        "--context-window", type=int,
        default=env_int("DEEPAGENTS_CONTEXT_WINDOW", DEFAULT_CONTEXT_WINDOW),
    )
    ap.add_argument(
        "--max-output-tokens", type=int,
        default=env_int("DEEPAGENTS_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS),
    )
    ap.add_argument(
        "--recursion-limit", type=int,
        default=env_int("DEEPAGENTS_RECURSION_LIMIT", DEFAULT_RECURSION_LIMIT),
    )
    ap.add_argument(
        "--max-tool-output-bytes", type=int,
        default=env_int("DEEPAGENTS_MAX_TOOL_OUTPUT_BYTES", DEFAULT_MAX_TOOL_OUTPUT_BYTES),
    )
    ap.add_argument("--checkpoint-db", default=None)
    ap.add_argument("--thread-id", default=None)
    args = ap.parse_args()

    workspace, project_root = runtime_paths()
    system = os.environ.get("DA_SYSTEM") or ""
    system = f"{system}\n\n{path_contract(workspace, project_root)}"
    prompt = os.environ.get("DA_PROMPT") or ""
    if not prompt:
        emit({"type": "error", "message": "DA_PROMPT 为空"})
        sys.exit(1)

    try:
        max_input_tokens, initial_input_budget = context_limits(
            args.context_window, args.max_output_tokens
        )
    except ValueError as e:
        emit({"type": "error", "message": str(e)})
        sys.exit(2)

    initial_tokens = estimate_tokens(system) + estimate_tokens(prompt)
    if initial_tokens > initial_input_budget:
        emit({
            "type": "error",
            "message": (
                f"DeepAgents 首轮输入约 {initial_tokens} tokens,超过硬限制 "
                f"{initial_input_budget}(context={args.context_window},"
                f"output_reserve={args.max_output_tokens})。请把长内容写入项目文件,"
                "任务中仅传文件路径和执行要求。"
            ),
        })
        sys.exit(2)

    from langchain_openai import ChatOpenAI
    from langchain_core.messages import AIMessage
    from deepagents import (
        GeneralPurposeSubagentProfile,
        HarnessProfile,
        create_deep_agent,
        register_harness_profile,
    )
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

    model = ChatOpenAI(
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        timeout=600,
        max_retries=1,
        max_completion_tokens=args.max_output_tokens,
        profile={
            "max_input_tokens": max_input_tokens,
            "max_output_tokens": args.max_output_tokens,
        },
        **client_kw,
    )
    # 文件工具与 shell 均使用宿主机真实路径。virtual_mode=True 会让文件工具把
    # /Users/... 或 /data/... 重解释为 workspace 下的虚拟路径，而 shell 不会，
    # 导致同一字符串指向两个位置，因此这里必须显式关闭虚拟路径语义。
    backend = LocalShellBackend(
        root_dir=str(workspace),
        virtual_mode=False,
        inherit_env=True,
        timeout=600,
        max_output_bytes=args.max_tool_output_bytes,
    )
    register_harness_profile(
        "openai",
        HarnessProfile(
            excluded_middleware=frozenset({"SummarizationMiddleware"}),
            excluded_tools=frozenset({"glob"}),
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
            extra_middleware=[build_safe_summarizer(
                model, backend, args.context_window, args.max_output_tokens
            )],
        ),
    )
    # 会话续接(Agent记忆):SqliteSaver 按 thread_id 跨进程持久化消息历史。
    config = {"recursion_limit": args.recursion_limit}
    agent_kw = {}
    if args.checkpoint_db:
        import sqlite3
        import uuid
        from langgraph.checkpoint.sqlite import SqliteSaver
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
        msg = f"{type(e).__name__}: {e}"
        if "GraphRecursionError" in type(e).__name__ or "recursion" in str(e).lower():
            msg = (
                f"{msg} | 当前 recursion_limit={args.recursion_limit}。"
                " 可用环境变量 DEEPAGENTS_RECURSION_LIMIT 或参数 --recursion-limit 提高;"
                " 调度类任务建议 >=500。"
            )
        emit({"type": "error", "message": msg[:700]})
        sys.exit(1)


if __name__ == "__main__":
    main()

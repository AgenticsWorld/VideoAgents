#!/usr/bin/env python3
"""PreToolUse hook(claude 引擎专用):调度层 Agent 越界生成硬拦截。

runtime 派单时把 VIDEOAGENTS_AGENT 注入运行环境;本 hook 在 00-orchestration
各 Agent(总制片等)的 Bash 调用命中生成脚本/生成 API 时直接 deny,并在
拒绝理由里给出正确的派单路径。命令文本绕过本拦截(如自写 driver 脚本)的,
由 modules/genmedia.py 内的 _forbid_dispatch_layer 运行时守卫兜底。

codex / deepagents 引擎不走 Claude hooks,只依赖 genmedia 内守卫。
交互式人工会话没有 VIDEOAGENTS_AGENT,不受影响。
"""
import json
import os
import re
import sys

FORBIDDEN = re.compile(
    r"genmedia|audiodsp|deepagents_runner|generate_(image|video|tts|music)",
    re.IGNORECASE)


def main() -> None:
    agent = os.environ.get("VIDEOAGENTS_AGENT", "")
    # 修改师(00-orchestration/reviser)归入调度层分组但亲手代行专业工位重出产物,放行
    if not agent.startswith("00-orchestration/") or agent == "00-orchestration/reviser":
        return
    try:
        data = json.load(sys.stdin)
    except Exception:
        return  # 输入异常时放行,不因 hook 自身故障卡死流水线
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command", "")
    if not FORBIDDEN.search(command):
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"{agent} 属调度层,只派单不生成,禁止直接运行生成脚本/生成 API。"
                "正确做法:按 WORKFLOW.md §6 生成工单,通过 services/runtime/dispatch.py "
                "派发给对应执行 Agent(图像=06-art、视频=08-video-gen、"
                "旁白/对白/BGM=09-audio),然后跟踪回执。"),
        }
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

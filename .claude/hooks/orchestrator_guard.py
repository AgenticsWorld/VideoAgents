#!/usr/bin/env python3
"""PreToolUse hook(claude 引擎专用):调度层 Agent 越界生成硬拦截 + 全员自发语音识别硬拦截。

runtime 派单时把 VIDEOAGENTS_AGENT 注入运行环境;本 hook 在 00-orchestration
各 Agent(总制片等)的 Bash 调用命中生成脚本/生成 API 时直接 deny,并在
拒绝理由里给出正确的派单路径。命令文本绕过本拦截(如自写 driver 脚本)的,
由 modules/genmedia.py 内的 _forbid_dispatch_layer 运行时守卫兜底。

codex / deepagents 引擎不走 Claude hooks,只依赖 genmedia 内守卫。
交互式人工会话没有 VIDEOAGENTS_AGENT,不受影响。

第二道拦截(2026-09-23,全员):非转写工位的 Agent 在 Bash 里调用 faster-whisper /
WhisperModel / modules/transcription.py / speechalign 等本地语音识别一律 deny——
前科 fengshen3 p7-video-ep06-grp020:视频生成成员出片后「核查对白音轨」,自发
WhisperModel('small')→('medium') 逐个下载模型转写,单次工单空耗数十分钟。语音转写
只允许 ASR_ALLOWED_AGENTS 里的固定工序工位执行;用户明确要求其它工位转写时,由宿主派单
带 VIDEOAGENTS_ALLOW_ASR=1 放行(或改派 09-audio/audio-transcription)。
"""
import json
import os
import re
import sys

FORBIDDEN = re.compile(
    r"genmedia|audiodsp|deepagents_runner|generate_(image|video|tts|music)",
    re.IGNORECASE)

# 本地语音识别(会触发模型权重下载/长时推理)的命令特征
ASR_FORBIDDEN = re.compile(
    r"faster_whisper|faster-whisper|WhisperModel|\bwhisper(\.cpp|x)?\b|"
    r"modules/transcription\.py|speechalign|speech-align|mashup_align|"
    r"\bvosk\b|funasr|paraformer|sensevoice|wav2vec",
    re.IGNORECASE)
# SOUL 把宿主 ASR 入口列为固定工序的工位(按 id 前缀匹配);插件工位按业务前缀放行
ASR_ALLOWED_AGENTS = (
    "09-audio/audio-transcription",   # 按需转写工位(WORKFLOW.md「按需音频转写」)
    "10-editing/caption",             # 花字逐字时间轴 render_captions.py speech-align
    "17-digital-human/",              # 数字人插件 dh0-transcribe(dialogue-ingest)
)
ASR_ALLOWED_KEYWORDS = ("mashup", "footage")   # 混剪/素材库插件工位(v4 声画对齐)


def _asr_guard(agent: str, command: str):
    """非转写工位自发跑语音识别 → deny。返回拒绝理由或 None。"""
    if os.environ.get("VIDEOAGENTS_ALLOW_ASR") == "1":
        return None
    if agent.startswith(ASR_ALLOWED_AGENTS) or any(k in agent for k in ASR_ALLOWED_KEYWORDS):
        return None
    if not ASR_FORBIDDEN.search(command):
        return None
    return (
        f"{agent} 不是转写工位,禁止自发调用本地语音识别(faster-whisper/WhisperModel/"
        "modules/transcription.py/speechalign 等)核查对白或音轨——这类调用会下载整套模型权重、"
        "空耗数十分钟(前科 fengshen3 p7-video-ep06-grp020)。产物验收只按 SOUL 与工单 acceptance "
        "列出的机检执行(ffprobe 音轨/时长、voice_f0_check 等);工单没写的转写核查一律不做。"
        "确需核对台词时把疑点写进回执交用户裁决;只有用户明确要求转写才可由调度层改派 "
        "09-audio/audio-transcription 或带 VIDEOAGENTS_ALLOW_ASR=1 派单。")


def _deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def main() -> None:
    agent = os.environ.get("VIDEOAGENTS_AGENT", "")
    if not agent:
        return  # 交互式人工会话不受影响
    try:
        data = json.load(sys.stdin)
    except Exception:
        return  # 输入异常时放行,不因 hook 自身故障卡死流水线
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command", "")

    # 第二道:全员自发语音识别硬拦截(转写工位放行)
    reason = _asr_guard(agent, command)
    if reason:
        _deny(reason)
        return

    # 第一道:调度层越界生成。修改师(00-orchestration/reviser)归入调度层分组但亲手代行专业工位重出产物,放行
    if not agent.startswith("00-orchestration/") or agent == "00-orchestration/reviser":
        return
    if not FORBIDDEN.search(command):
        return
    _deny(
        f"{agent} 属调度层,只派单不生成,禁止直接运行生成脚本/生成 API。"
        "正确做法:按 WORKFLOW.md §6 生成工单,通过 services/runtime/dispatch.py "
        "派发给对应执行 Agent(图像=06-art、视频=08-video-gen、"
        "旁白/对白/BGM=09-audio),然后跟踪回执。")


if __name__ == "__main__":
    main()

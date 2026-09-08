#!/usr/bin/env python3
"""小说→视频多 Agent 执行核心。

- 每个 Agent 一个对话入口:对话触发 `claude -p`(注入该 Agent 的 SOUL.md 作为身份),
  工作产物写入 data/projects/<project>/。
- Director(调度型 Agent)可通过 services/runtime/dispatch.py 把任务派给其他 Agent,形成父子运行链。
- 全部运行状态 / 工具活动由 API 服务对外发布。
"""
import asyncio
import gzip
import hashlib
import hmac
import importlib.util
import io
import json
import mimetypes
import os
import random
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections import deque
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path

import base64
import pygit2

from modules.output_format import OUTPUT_ASPECTS, resolve_output
from services.runtime import rhythm as narrative_rhythm

# ---------------- 配置 ----------------
ROOT = Path(__file__).resolve().parents[2]             # 工作区根目录
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", ROOT / "data")).expanduser().resolve()
RUNTIME_DIR = Path(os.environ.get(
    "VIDEOAGENTS_RUNTIME_DIR", DATA_DIR / ".videoagents"
)).expanduser().resolve()
AGENTS_DIR = ROOT / "agents"
PROJECTS_DIR = DATA_DIR / "projects"
CHATS_DIR = RUNTIME_DIR / "chats"
RUNS_DIR = RUNTIME_DIR / "runs"
STATE_PATH = RUNTIME_DIR / "state.json"


class ServiceError(Exception):
    """运行时业务错误，由 API、CLI 或 Worker 入口转换为各自响应。"""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail

# API port is propagated to dispatched Agent processes and sketch-session URLs.
PORT = int(os.environ.get("VIDEOAGENTS_PORT", "8630"))
PUBLIC_PORT = int(os.environ.get("VIDEOAGENTS_PUBLIC_PORT", str(PORT)))
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
CODEX_BIN = os.environ.get("CODEX_BIN", "codex")
PI_BIN = os.environ.get("PI_BIN", "pi")


def _default_kimi_bin() -> str:
    # kimi 官方安装脚本默认装到 ~/.kimi-code/bin,该目录通常由交互式 shell 的 rc 文件
    # 加进 PATH;从 IDE/launchd/bash 等环境启动本服务时 PATH 可能不含它,回退绝对路径
    found = shutil.which("kimi")
    if found:
        return found
    fallback = Path.home() / ".kimi-code" / "bin" / "kimi"
    return str(fallback) if fallback.is_file() else "kimi"


KIMI_BIN = os.environ.get("KIMI_BIN") or _default_kimi_bin()


def _default_opencode_bin() -> str:
    # opencode 官方安装脚本默认装到 ~/.opencode/bin,同 kimi:从 IDE/launchd 等
    # 非交互环境启动本服务时 PATH 可能不含该目录,回退绝对路径
    found = shutil.which("opencode")
    if found:
        return found
    fallback = Path.home() / ".opencode" / "bin" / "opencode"
    return str(fallback) if fallback.is_file() else "opencode"


OPENCODE_BIN = os.environ.get("OPENCODE_BIN") or _default_opencode_bin()


def _default_grok_bin() -> str:
    # Grok Build CLI(grok.com/build)官方安装脚本装到 ~/.grok/bin 并在 ~/.local/bin 建
    # 软链,同 kimi/opencode:非交互环境 PATH 可能不含这两个目录,回退绝对路径
    found = shutil.which("grok")
    if found:
        return found
    for fallback in (Path.home() / ".grok" / "bin" / "grok",
                     Path.home() / ".local" / "bin" / "grok"):
        if fallback.is_file():
            return str(fallback)
    return "grok"


GROK_BIN = os.environ.get("GROK_BIN") or _default_grok_bin()
CLI_BINS = {"claude": CLAUDE_BIN, "codex": CODEX_BIN, "kimi": KIMI_BIN,
            "pi": PI_BIN, "opencode": OPENCODE_BIN, "grok": GROK_BIN}
CLI_LABELS = {"claude": "Claude Code", "codex": "Codex CLI", "kimi": "Kimi Code",
              "pi": "Pi Coding Agent", "opencode": "OpenCode", "grok": "Grok Build"}
CLI_ENV_VARS = {"claude": "CLAUDE_BIN", "codex": "CODEX_BIN", "kimi": "KIMI_BIN",
                "pi": "PI_BIN", "opencode": "OPENCODE_BIN", "grok": "GROK_BIN"}


def resolve_cli_executable(engine: str) -> str | None:
    """Resolve the CLI to the exact path used to launch it.

    Windows CreateProcess does not expand a bare command such as ``claude``
    to the npm-generated ``claude.cmd`` shim, even when a shell can find it.
    """
    configured = CLI_BINS.get(engine)
    return shutil.which(configured) if configured else None


def cli_not_found_error(engine: str) -> str:
    configured = CLI_BINS.get(engine, engine)
    label = CLI_LABELS.get(engine, engine)
    env_var = CLI_ENV_VARS.get(engine, "对应的环境变量")
    return (f"无法启动 {label}：未找到命令“{configured}”。"
            f"请确认已安装 {label} 并将其加入 PATH，"
            f"或通过 {env_var} 配置可执行文件的完整路径。")

# deepagents 引擎:OpenAI 兼容端点(如 LM Studio 本地模型)。解释器按优先级解析:
# DEEPAGENTS_PY 环境变量 → 项目根 .venv-deepagents(deepagents 需 Python ≥3.11,
# 主环境为 3.10 时用 make install-deepagents 建)→ 当前解释器(已装 [deepagents] extra 时)
DEEPAGENTS_PY_DEFAULT = str(ROOT / ".venv-deepagents" / "bin" / "python")
# deepagents 会话续接(Agent记忆):LangGraph SqliteSaver 检查点库,thread_id 即
# 会话 id,与 claude --resume 同语义走 STATE["sessions"] 回存/恢复;续接一个已
# 不存在的 thread 只得到空历史(等效新会话)不报错,无需失效回退重试
DEEPAGENTS_SESSIONS_DB = RUNTIME_DIR / "deepagents_sessions.sqlite"


def deepagents_python() -> str:
    env = os.environ.get("DEEPAGENTS_PY")
    if env:
        return env
    for rel in ("bin/python", "Scripts/python.exe"):
        cand = ROOT / ".venv-deepagents" / rel
        if cand.exists():
            return str(cand)
    if importlib.util.find_spec("deepagents") is not None:
        return sys.executable
    return DEEPAGENTS_PY_DEFAULT
ENGINES = ("claude", "codex", "kimi", "pi", "opencode", "grok", "deepagents")   # 执行引擎:CLI 或 deepagents runner
PERMISSION_MODE = os.environ.get("VIDEOAGENTS_PERMISSION_MODE", "acceptEdits")
CLAUDE_USAGE_PROBE_ENABLED = os.environ.get(
    "VIDEOAGENTS_ENABLE_CLAUDE_USAGE_PROBE", ""
).lower() in {"1", "true", "yes"}
# 单次运行引擎轮次上限(设置菜单「高级→Agent 高级设置」可调,存 state.json):
# 仅 claude/grok 引擎有此参数(--max-turns);codex/pi/opencode 不传轮次上限,
# deepagents 走 LangGraph recursion_limit。撞上限时引擎直接退出且无续跑机制
# (回执只见退出码 1),大批量工位(curate/cut)实测 100 轮不够,缺省放宽到 250
MAX_TURNS_DEFAULT = 250
MAX_TURNS_MIN, MAX_TURNS_MAX = 10, 1000
MAX_CONCURRENT = 8                       # 同时运行的工人进程上限(调度器不占槽,见 execute_run)
# 单次运行超时缺省值(秒;设置菜单「高级→Agent 高级设置」可调,存 state.json):
# 墙钟硬限,兜底回收挂死的引擎进程(API 长连接不返回、代理 stall 等);调度型 Agent
# 要等整条流水线,取 4 倍。到点连派生子进程一起杀,run 记为 error
RUN_TIMEOUT_DEFAULT = 7200
RUN_TIMEOUT_MIN, RUN_TIMEOUT_MAX = 600, 24 * 3600
# 无输出超时缺省值(秒;同一弹窗可调,0=关闭):引擎事件流连续静默超过该时长即判死。
# 比墙钟更早识别挂死,又不误伤「跑得慢但在正常干活」的长批量任务;调度型 Agent 大部分
# 时间在 --wait-all 里静默等子任务,不受此项约束
IDLE_TIMEOUT_DEFAULT = 1800
IDLE_TIMEOUT_MAX = 6 * 3600
STREAM_LIMIT = 32 * 1024 * 1024          # 子进程 stdout 单行缓冲上限(stream-json 一行可能带整个文件内容)
# 拥有调度权的 Agent(系统提示词里会附加 dispatch.py 用法);仅总制片,导演不派单
DISPATCHERS = {"00-orchestration/workflow-orchestrator"}
# 无状态服务型/扇出型 Agent:每次派单自足(工单内联上下文 + SOUL 注入),不 resume 会话、
# 同 agent 允许并发(否则 8 个 eval/QA 会被 AGENT_SEMS 串成一列)。
# 08-video-gen 为组级扇出工位(prompt/imagegen/videogen…每组一单,2026-07-23 纳入):
# 组间依赖由 DAG depends_on 表达,不靠会话串行,同 agent 并发是 Phase 7 吞吐关键。
# 05-scenes 为场景级扇出工位(environment/architecture/lighting 每场景一单,2026-07-28 纳入):
# 大项目场景数可达 70+,同 agent 串行会拖垮 Phase 3。
# 03-characters(appearance/personality…每角色一单)、06-art(char-concept 每角色/
# env-concept 每场景一单)同为扇出工位,novel-parser 按章节分块工单,2026-07-28 一并纳入。
# 13-derivative-fiction/line-editor(插件 Agent,每章一单)章节级扇出,2026-07-29 纳入;
# prose-writer 不纳入:上一章正文是下一章输入,须线性串行执行(保持有状态)
STATELESS_AGENTS = {"00-orchestration/evaluation",
                    "01-story/novel-parser", "09-audio/audio-transcription"}
STATELESS_PREFIXES = ("11-qa/", "08-video-gen/", "05-scenes/",
                      "03-characters/", "06-art/",
                      "13-derivative-fiction/line-editor")
# 无状态 agent 的同 agent 并发额度缺省值(设置菜单「高级→Agent 高级设置」可调,存 state.json);
# 有状态 agent 恒为 1(串行保护会话),全局仍受 MAX_CONCURRENT 总闸
AGENT_CONCURRENCY_DEFAULT = 5
# Agent 对话记忆额度缺省值(KB;设置菜单「高级→Agent 高级设置」滑块 0..AGENT_MEMORY_KB_MAX
# 可调,存 state.json 的 agent_memory_kb,兼容旧布尔键 agent_memory):
# >0=有状态 agent 按 engine::agent::project 恢复上次会话,单会话历史(chats/<agent>.jsonl)
# 超过该额度自动新开会话防膨胀——resume 每轮重发全史,越大越慢越贵(实测 codex 单 run
# 累计 input 曾达 1400 万 token;2026-07-23 曾由 512KB 压至 128KB,2026-08-20 改滑块并降默认 16KB);
# 0=关闭:所有 agent 每次运行全新会话,跨工单记忆只靠落盘产物。
# 关闭/调小期间 session_id 照常回存,重新调大后从最近一次会话继续
AGENT_MEMORY_KB_DEFAULT = 16
AGENT_MEMORY_KB_MAX = 256
# Agent 自动重跑/重 roll 次数上限缺省值(设置菜单「高级→Agent 高级设置」可调,存 state.json):
# 验收/评分/QA 不过自动带意见退回重做、媒体生成机检不达标自动重 roll 的次数上限,
# 达到上限仍不过升级用户裁决;0=不自动重跑(首次不过即升级人工)。经 build_role_prompt
# 注入全员运行提示词,覆盖 SOUL/WORKFLOW 文档里写死的「最多 3 次」
MAX_RETRIES_DEFAULT = 1
MAX_RETRIES_MAX = 10
# 重跑等待确认时长缺省值(秒;设置菜单「高级→Agent 高级设置」可调,存 state.json):
# 重跑类确认弹窗(dispatch.py --confirm,非签字类)的倒计时,到点无人答复自动落默认答案;
# 同时是单条确认允许的最长等待(显式 --timeout 只能更短);签字类永不超时,不受此项影响
CONFIRM_TIMEOUT_DEFAULT = 60
CONFIRM_TIMEOUT_MIN, CONFIRM_TIMEOUT_MAX = 5, 3600
# 思考深度(Thinking Effort)统一设置(设置菜单「高级→Agent 高级设置」下拉,存 state.json 的
# thinking_effort):派单时按引擎翻译成各自的推理强度参数,全局对所有 Agent 生效——
#   claude   --effort <level>                    (low/medium/high/xhigh/max 原样)
#   codex    -c model_reasoning_effort=<level>   (无 max,max→xhigh)
#   pi       --thinking <level>                  (原样)
#   opencode --variant <level>                   (模型无该 variant 时由 opencode 忽略)
#   grok     --reasoning-effort <level>          (low/medium/high/xhigh,无 max,max→xhigh)
#   deepagents --reasoning-effort <level>        (仅云端/OpenRouter 渠道;本地端点不传)
#   kimi     CLI 无推理强度参数,不传(沿用其自身默认)
# 空串=引擎默认(不传任何参数,沿用各引擎 CLI 自身配置),亦为缺省值
THINKING_EFFORT_DEFAULT = ""
THINKING_EFFORT_LEVELS = ("", "low", "medium", "high", "xhigh", "max")
# deepagents 引擎单独封顶(实际生效额度取 min(记忆额度滑块值, 此上限)):
# 记忆额度量的是 chats/*.jsonl(只有用户/助手文本),而 deepagents 检查点历史含
# 全部工具消息(DAG 查询输出、read_file 全文),真实会话体量被严重低估;
# 且 OpenAI 兼容端点 resume=每轮重发全史,本地模型上下文窗口小、OpenRouter 渠道
# 无厂商 prompt cache 兜底,须更早换新会话
DEEPAGENTS_RESUME_LIMIT = 32 * 1024
# 运行面板手动停止的统一错误文案:core/dispatch.py/前端/agent 提示词都按这句字面识别
STOPPED_BY_USER_MSG = "已被用户手动停止"
# stopped 字段取值 → 收尾错误文案(user=运行面板 ⏹;shutdown=服务关闭/重启连带停止)
STOPPED_MSGS = {"user": STOPPED_BY_USER_MSG, "shutdown": "服务关闭,任务已停止"}
# 网络快速失败:claude CLI 遇 ECONNRESET/超时等连接层错误(api_retry 事件 error_status 为
# null,即没拿到任何 HTTP 响应)时默认自带指数退避重试 10 次,agent 会在「等待重试」里
# 干耗数分钟且多个并发 agent 一起卡死。宿主看到这类事件超过下面的容忍次数就立刻杀进程
# 报错(run.net_error=True),由总制片重派或人工修好代理/网络后再继续。HTTP 状态类错误
# (429/529/5xx)仍交给 CLI 自己重试。VIDEOAGENTS_NET_RETRY_LIMIT 可放宽(默认 0=首次即停)。
try:
    NET_RETRY_LIMIT = max(0, int(os.environ.get("VIDEOAGENTS_NET_RETRY_LIMIT", "0")))
except ValueError:
    NET_RETRY_LIMIT = 0
NET_ERROR_MSG = ("🔌 网络中断:API 连接被重置/超时(Connection dropped,未收到任何 HTTP 响应),"
                 "已按快速失败策略终止进程、不等待 CLI 自动重试;非程序错误、不计 attempt,"
                 "请检查代理/VPN 节点后由总制片重新派单或人工介入")
ORCHESTRATOR_AGENT = "00-orchestration/workflow-orchestrator"
IDLE_CHECK_INTERVAL = 300                # 空转看门狗巡检间隔缺省值(秒);
                                         # 实际间隔由 STATE.watchdog_idle_minutes 控制(设置弹窗可调)
# 生成类 Agent 所在类别(系统提示词里会附加 genmedia 模块用法)
MEDIA_CATEGORIES = {"06-art", "08-video-gen"}

CATEGORY_NAMES = {
    "00-orchestration": "调度层", "01-story": "剧情", "02-worldbuilding": "世界设定",
    "03-characters": "角色", "04-creatures": "生物资产", "05-scenes": "场景资产",
    "06-art": "美术资产", "07-directing": "导演", "08-video-gen": "视频生成",
    "09-audio": "音频", "10-editing": "剪辑", "11-qa": "审核", "12-publishing": "发布",
}

RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
CHATS_DIR.mkdir(exist_ok=True)
RUNS_DIR.mkdir(exist_ok=True)
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

REFS_README = """# refs/ — 用户参考目录

把「希望成片长成什么样」的参考图、希望使用的音频和文本资料放进来,相关 Agent 会优先参考/选用(约定见 agents/WORKFLOW.md §2)。
**推荐用 Web 客户端【项目预览 → 参考文件】页上传与管理**:按分类预览、上传文件,并给每个文件添加注释说明用途。

- `style/`      整体视觉风格:画风/渲染质感/色调/构图
- `characters/` 角色形象;按角色建子目录(如 characters/linzhao/)可定向到该角色
- `scenes/`     场景与世界观:建筑/地貌/氛围
- `props/`      道具/法宝;服装放 props/costumes/
- `music/`      希望使用的音频文件(背景音轨,BGM 候选,mp3/wav/flac 等);配乐 Agent 优先选用,并自动判断用在视频的合适位置
- `video/`      参考视频:动作/运镜/节奏/转场范例(mp4/mov/webm),视频生成 Agent 优先参考(支持时经 --ref-video 注入)
- `thumbnail/`  封面参考:他人爆款封面/构图/版式范例
- `text/`       文本资料:设定/文案等(txt/md 等),相关 Agent 参考使用
- `NOTES.md`    逐文件注释:哪个文件管什么、想用在哪(有则 Agent 必读)。
                注释在【参考文件】页逐文件填写,自动写入本文件的标记块(机器可读版在 annotations.json)

规则:用户参考素材 > Agent 自行发挥;与文字设定冲突时 Agent 会上报你裁决;目录为空不影响流程。
"""


def safe_slug(name, default: str = "demo") -> str:
    """项目名统一清洗:非 [字母数字_-] 一律替换为 '-'。"""
    return re.sub(r"[^\w\-]", "-", str(name or default))




def require_project_slug(name) -> str:
    """Accept a project name, never a relative or absolute filesystem path."""
    value = str(name or "")
    if not value or len(value) > 80 or not re.fullmatch(r"[\w-]+", value):
        raise ServiceError(400, "project must be a name, not a directory path")
    return value

def safe_agent(agent: str) -> str:
    """agent id 白名单校验(恒为「类别/名字」两段),防路径遍历。"""
    if not re.fullmatch(r"[\w\-]+/[\w\-]+", agent or ""):
        raise ServiceError(400, f"Invalid agent id: {agent}")
    return agent


def atomic_write_json(path: Path, obj):
    """临时文件 + os.replace,防写中崩溃损坏 JSON。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2))
    os.replace(tmp, path)


def ensure_project(project: str):
    """建项目目录 + 用户参考目录骨架。"""
    refs = PROJECTS_DIR / project / "refs"
    for sub in ("style", "thumbnail", "characters", "scenes", "props", "music", "video"):
        (refs / sub).mkdir(parents=True, exist_ok=True)
    readme = refs / "README.md"
    if not readme.exists():
        readme.write_text(REFS_README)

# ---------------- 状态 ----------------
RUNS: dict[str, dict] = {}                       # run_id -> run 记录
RUN_TASKS: dict[str, asyncio.Task] = {}          # run_id -> execute_run 任务(停止排队用)
RUN_PROCS: dict[str, asyncio.subprocess.Process] = {}  # run_id -> 子进程(停止运行用)
CONFIRMS: dict[str, dict] = {}                   # confirm_id -> 待用户确认项
SEM = asyncio.Semaphore(MAX_CONCURRENT)
# 每 agent 并发闸:agent_id -> (创建时的额度, 信号量)。额度被用户调整后按需重建;
# 旧信号量由仍持有它的 run 自然释放后回收,切换瞬间总并发可能短暂超出新额度(可接受)
AGENT_SEMS: dict[str, tuple[int, asyncio.Semaphore]] = {}


def agent_concurrency() -> int:
    """无状态 agent 的同 agent 并发额度(1..MAX_CONCURRENT,越界钳制)。"""
    try:
        n = int(STATE.get("agent_concurrency", AGENT_CONCURRENCY_DEFAULT))
    except (TypeError, ValueError):
        n = AGENT_CONCURRENCY_DEFAULT
    return max(1, min(n, MAX_CONCURRENT))


def max_turns_setting() -> int:
    """单次运行引擎轮次上限(MAX_TURNS_MIN..MAX_TURNS_MAX,越界钳制;claude/grok 有效)。"""
    try:
        n = int(STATE.get("max_turns", MAX_TURNS_DEFAULT))
    except (TypeError, ValueError):
        n = MAX_TURNS_DEFAULT
    return max(MAX_TURNS_MIN, min(n, MAX_TURNS_MAX))


def run_timeout_setting() -> int:
    """单次运行墙钟超时(秒,RUN_TIMEOUT_MIN..RUN_TIMEOUT_MAX,越界钳制)。"""
    try:
        n = int(STATE.get("run_timeout", RUN_TIMEOUT_DEFAULT))
    except (TypeError, ValueError):
        n = RUN_TIMEOUT_DEFAULT
    return max(RUN_TIMEOUT_MIN, min(n, RUN_TIMEOUT_MAX))


def idle_timeout_setting() -> int:
    """无输出超时(秒,0=关闭,上限 IDLE_TIMEOUT_MAX,越界钳制)。"""
    try:
        n = int(STATE.get("idle_timeout", IDLE_TIMEOUT_DEFAULT))
    except (TypeError, ValueError):
        n = IDLE_TIMEOUT_DEFAULT
    return max(0, min(n, IDLE_TIMEOUT_MAX))


def agent_sem(agent_id: str, limit: int) -> asyncio.Semaphore:
    cached = AGENT_SEMS.get(agent_id)
    if cached is None or cached[0] != limit:
        cached = (limit, asyncio.Semaphore(limit))
        AGENT_SEMS[agent_id] = cached
    return cached[1]


def agent_memory_kb() -> int:
    """Agent 对话记忆额度(KB,0=关闭,上限 AGENT_MEMORY_KB_MAX,越界钳制;
    设置菜单「高级→Agent 高级设置」滑块)。兼容旧布尔开关 agent_memory:
    未设过额度时 False→0,True/缺省→缺省额度(启动时 migrate_agent_memory_default
    会把旧版升级的存量安装统一重置为缺省额度,此分支仅在迁移前兜底)。"""
    v = STATE.get("agent_memory_kb")
    if v is None:
        return AGENT_MEMORY_KB_DEFAULT if STATE.get("agent_memory", True) else 0
    try:
        n = int(v)
    except (TypeError, ValueError):
        return AGENT_MEMORY_KB_DEFAULT
    return max(0, min(n, AGENT_MEMORY_KB_MAX))


def agent_memory_enabled() -> bool:
    """Agent 对话记忆总开关(额度 >0 即开启)。"""
    return agent_memory_kb() > 0


def max_retries_setting() -> int:
    """Agent 自动重跑/重 roll 次数上限(0..MAX_RETRIES_MAX,越界钳制;设置菜单「高级→Agent 高级设置」)。"""
    try:
        n = int(STATE.get("max_retries", MAX_RETRIES_DEFAULT))
    except (TypeError, ValueError):
        n = MAX_RETRIES_DEFAULT
    return max(0, min(n, MAX_RETRIES_MAX))


def confirm_timeout_setting() -> int:
    """重跑类确认弹窗倒计时时长(秒,CONFIRM_TIMEOUT_MIN..CONFIRM_TIMEOUT_MAX,越界钳制;
    设置菜单「高级→Agent 高级设置」)。签字类确认永不超时,不受此项影响。"""
    try:
        n = int(STATE.get("confirm_timeout", CONFIRM_TIMEOUT_DEFAULT))
    except (TypeError, ValueError):
        n = CONFIRM_TIMEOUT_DEFAULT
    return max(CONFIRM_TIMEOUT_MIN, min(n, CONFIRM_TIMEOUT_MAX))


def thinking_effort_setting() -> str:
    """思考深度统一设置(设置菜单「高级→Agent 高级设置」;取值见 THINKING_EFFORT_LEVELS,
    空串=引擎默认,亦为缺省;非法值回退缺省)。"""
    v = STATE.get("thinking_effort")
    if v is None:
        return THINKING_EFFORT_DEFAULT
    v = str(v).strip().lower()
    return v if v in THINKING_EFFORT_LEVELS else THINKING_EFFORT_DEFAULT


def engine_effort_args(engine: str, level: str, *, local_endpoint: bool = False) -> list[str]:
    """把统一思考深度翻译成引擎 CLI 参数(见 THINKING_EFFORT_DEFAULT 注释);空/不支持则 []。"""
    if not level:
        return []
    if engine == "claude":
        return ["--effort", level]
    if engine == "codex":
        return ["-c", f"model_reasoning_effort={'xhigh' if level == 'max' else level}"]
    if engine == "pi":
        return ["--thinking", level]
    if engine == "opencode":
        return ["--variant", level]
    if engine == "grok":
        return ["--reasoning-effort", "xhigh" if level == "max" else level]
    if engine == "deepagents":
        return [] if local_endpoint else ["--reasoning-effort", level]
    return []   # kimi 等无对应参数


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"sessions": {}}


def save_state(state: dict):
    atomic_write_json(STATE_PATH, state)


STATE = load_state()

# ---------------- Agent 插件机制(声明式,详见 WORKFLOW.md §10 与 plugins/README.md) ----------------
# 插件 = plugins/<name>/ 目录:plugin.json(manifest)+ agents/<类别>/<名字>/SOUL.md
# (+ 可选 workflows/*.yaml 独立流程 DAG)。纯声明式——插件不含可执行代码,SOUL.md 即身份,
# 派单/评审/闸门与内置 Agent 同等待遇;复制目录进 plugins/ 即安装(默认停用,须在
# 「插件」页手动启用),启用名单记 state.json 的 plugins_enabled 列表。manifest 用 JSON:服务端零 yaml 依赖。
BUNDLED_PLUGINS_DIR = ROOT / "plugins"
PLUGINS_DIR = Path(os.environ.get(
    "VIDEOAGENTS_PLUGINS_DIR", DATA_DIR / "plugins"
)).expanduser().resolve()
PLUGIN_MANIFEST = "plugin.json"
MAX_PLUGIN_UPLOAD = 50 * 1024 * 1024
MAX_PLUGIN_EXTRACTED = 200 * 1024 * 1024
MAX_PLUGIN_FILES = 5000
_PLUGIN_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{0,59}")
_AGENT_ID_RE = re.compile(r"[A-Za-z0-9_\-]+/[A-Za-z0-9_\-]+")
_PLUGINS_CACHE: tuple[float, list] = (0.0, [])
_PLUGINS_CACHE_TTL = 30.0


def _read_plugin_manifest(pdir: Path) -> dict:
    """读取并规范化单个插件的 manifest;所有问题写入 errors(有 errors 的插件不注册 Agent)。"""
    info = {"name": pdir.name, "version": "", "description": "", "path": str(pdir),
            "categories": {}, "agents": [], "workflows": [], "outputs_ns": "",
            "requires": {}, "errors": []}
    try:
        m = json.loads((pdir / PLUGIN_MANIFEST).read_text())
    except FileNotFoundError:
        info["errors"].append(f"缺少 {PLUGIN_MANIFEST}")
        return info
    except Exception as e:  # noqa: BLE001
        info["errors"].append(f"{PLUGIN_MANIFEST} 解析失败:{e}")
        return info
    if not isinstance(m, dict):
        info["errors"].append(f"{PLUGIN_MANIFEST} 顶层必须是 JSON 对象")
        return info
    if m.get("name") and m["name"] != pdir.name:
        info["errors"].append(f"manifest name({m['name']})与目录名({pdir.name})不一致")
    info["version"] = str(m.get("version") or "")
    info["description"] = str(m.get("description") or "")
    info["outputs_ns"] = str(m.get("outputs_ns") or "").strip().strip("/")
    info["requires"] = m.get("requires") if isinstance(m.get("requires"), dict) else {}
    cats = m.get("categories") or {}
    if not isinstance(cats, dict):
        info["errors"].append("categories 必须是 {类别目录名: 显示名} 对象")
    else:
        for k, v in cats.items():
            if not re.fullmatch(r"[A-Za-z0-9_\-]+", str(k)):
                info["errors"].append(f"非法类别名:{k}")
            else:
                info["categories"][str(k)] = str(v)
    for a in (m.get("agents") or []):
        a = {"id": a} if isinstance(a, str) else (a if isinstance(a, dict) else {})
        aid = str(a.get("id") or "")
        if not _AGENT_ID_RE.fullmatch(aid):
            info["errors"].append(f"非法 agent id(须为「类别/名字」两段):{aid or '(空)'}")
            continue
        if not (pdir / "agents" / aid / "SOUL.md").is_file():
            info["errors"].append(f"缺少 agents/{aid}/SOUL.md")
            continue
        info["agents"].append({"id": aid, "stateless": bool(a.get("stateless")),
                               "dispatcher": bool(a.get("dispatcher"))})
    if not info["agents"] and not info["errors"]:
        info["errors"].append("manifest 未声明任何 agents")
    for w in (m.get("workflows") or []):
        w = str(w).strip().strip("/")
        if ".." in w.split("/") or not w.endswith((".yaml", ".yml")):
            info["errors"].append(f"非法 workflow 路径:{w}")
        elif not (pdir / w).is_file():
            info["errors"].append(f"缺少 workflow 文件:{w}")
        else:
            info["workflows"].append(w)
    return info


def list_plugins(refresh: bool = False) -> list[dict]:
    """扫描 plugins/ 下全部插件(含 manifest 校验结果与启停状态);默认 30s 缓存。"""
    global _PLUGINS_CACHE
    if not refresh and time.time() < _PLUGINS_CACHE[0]:
        return _PLUGINS_CACHE[1]
    plugins, seen = [], {}                        # seen: agent id -> 插件名(跨插件撞名检测)
    roots = [(BUNDLED_PLUGINS_DIR, True), (PLUGINS_DIR, False)]
    plugin_names: set[str] = set()
    for plugin_root, bundled in roots:
        if not plugin_root.is_dir():
            continue
        for pdir in sorted(plugin_root.iterdir()):
            if not pdir.is_dir() or not _PLUGIN_NAME_RE.fullmatch(pdir.name):
                continue
            if pdir.name in plugin_names:
                continue  # 内置官方插件优先，用户目录不能用同名包覆盖它
            plugin_names.add(pdir.name)
            info = _read_plugin_manifest(pdir)
            info["builtin"] = bundled
            kept = []
            for a in info["agents"]:
                if (AGENTS_DIR / a["id"] / "SOUL.md").is_file():
                    info["errors"].append(f"agent id 与内置团队冲突:{a['id']}")
                elif a["id"] in seen:
                    info["errors"].append(f"agent id 与插件 {seen[a['id']]} 冲突:{a['id']}")
                else:
                    seen[a["id"]] = pdir.name
                    kept.append(a)
            info["agents"] = kept
            info["enabled"] = pdir.name in set(STATE.get("plugins_enabled") or [])
            info["active"] = info["enabled"] and not info["errors"]   # 有 errors 的插件不注册
            plugins.append(info)
    _PLUGINS_CACHE = (time.time() + _PLUGINS_CACHE_TTL, plugins)
    return plugins


def active_plugins() -> list[dict]:
    return [p for p in list_plugins() if p["active"]]


def plugin_prompt_path(plugin: dict) -> str:
    """Return a path that CLI agents can read from their backend working directory."""
    plugin_path = Path(plugin["path"]).resolve()
    try:
        return plugin_path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(plugin_path)


def _expire_agent_caches():
    global _PLUGINS_CACHE, _AGENTS_CACHE
    _PLUGINS_CACHE = (0.0, [])
    _AGENTS_CACHE = (0.0, [])


def plugin_of_agent(agent_id: str) -> dict | None:
    """agent id 归属的已启用插件(内置 Agent 返回 None)。"""
    for p in active_plugins():
        if any(a["id"] == agent_id for a in p["agents"]):
            return p
    return None


def agent_dir(agent_id: str) -> Path | None:
    """agent id → 目录解析:内置团队优先,其次已启用插件;非法/未知 id 返回 None(防路径遍历)。"""
    if not _AGENT_ID_RE.fullmatch(agent_id or ""):
        return None
    d = AGENTS_DIR / agent_id
    if (d / "SOUL.md").is_file():
        return d
    p = plugin_of_agent(agent_id)
    if p:
        d = Path(p["path"]) / "agents" / agent_id
        if (d / "SOUL.md").is_file():
            return d
    return None


def all_category_names() -> dict[str, str]:
    """内置类别 ∪ 已启用插件注册的类别(内置同名优先)。"""
    cats = dict(CATEGORY_NAMES)
    for p in active_plugins():
        for k, v in p["categories"].items():
            cats.setdefault(k, v)
    return cats


def is_dispatcher_agent(agent_id: str) -> bool:
    if agent_id in DISPATCHERS:
        return True
    p = plugin_of_agent(agent_id)
    return bool(p and any(a["id"] == agent_id and a["dispatcher"] for a in p["agents"]))

# ---------------- 生成模型配置(图像/视频) ----------------
GENCONFIG_PATH = RUNTIME_DIR / "genconfig.json"

DEFAULT_GENCONFIG = {
    "image": {
        "provider": "volcengine",   # agentics | openrouter | ideogram | volcengine | byteplus | minimax | comfyui
        "agentics": {"profile_code": ""},
        "openrouter": {"api_key": "", "model": "bytedance-seed/seedream-4.5",
                       "custom_model": ""},
        "ideogram": {"api_key": "", "model": "V_3", "custom_model": ""},
        # 火山方舟默认 5.0 Pro;BytePlus 仍默认 Lite(含人脸图片默认可过 Seedance 审核)
        "volcengine": {"api_key": "", "model": "doubao-seedream-5-0-pro-260628",
                       "custom_model": ""},
        "byteplus": {"api_key": "", "model": "seedream-5-0-lite-260128",
                     "custom_model": ""},
        # MiniMax:api_base 按「接口区域」二选一(海外 api.minimax.io/国内 api.minimaxi.com,
        # 两平台账号与 Key 不互通,api_key_io/api_key_cn 按区域分别保存,按 api_base 取用);
        # 图像/视频/音乐/TTS 四段各自独立保存
        "minimax": {"api_key_io": "", "api_key_cn": "",
                    "api_base": "https://api.minimax.io",
                    "model": "image-01", "custom_model": ""},
        # mode: local | cloud(Comfy Cloud)| rh_cn / rh_ai(RunningHub 国内/国际站,
        # 账号与 Key 不互通,rh_api_key_cn/rh_api_key_ai 按站点分别保存,按 mode 取用);
        # rh_workflows 为工作区工作流收藏 [{id, note, site}];
        # rh_instance_type 为 RunningHub 运行模式(机器规格)standard/plus/ultra,
        # standard 建任务不传 instanceType 沿用平台默认
        "comfyui": {"mode": "local", "url": "http://127.0.0.1:8188", "cloud_api_key": "",
                    "workflow": "",
                    "ref_workflow": "", "negative_mode": "conditioning", "checkpoint": "",
                    "rh_api_key_cn": "", "rh_api_key_ai": "",
                    "rh_workflow_id": "", "rh_ref_workflow_id": "",
                    "rh_workflows": [], "rh_instance_type": "standard"},
    },
    "video": {
        "provider": "volcengine",   # agentics | openrouter | volcengine | byteplus | fal | minimax | comfyui
        "agentics": {"profile_code": ""},
        "openrouter": {"api_key": "", "model": "bytedance/seedance-2.0",
                       "custom_model": ""},
        "volcengine": {"api_key": "", "model": "doubao-seedance-2-0-260128",
                       "custom_model": ""},
        "byteplus": {"api_key": "", "model": "dreamina-seedance-2-0-260128",
                     "custom_model": ""},
        # Fal(queue.fal.run 托管端点):model 存家族前缀(bytedance/seedance-2.0、minimax/h3、
        # fal-ai/kling-video/v3/pro),genmedia 按输入自动补 text-/image-/reference-to-video
        # 任务段;custom_model 可填完整端点 ID 原样调用;Key 在 fal.ai/dashboard/keys 创建
        "fal": {"api_key": "", "model": "minimax/h3-max", "custom_model": ""},
        # MiniMax-H3:分辨率仅 768P/2K,genmedia 把项目档位(360p..4k)自动就近映射
        "minimax": {"api_key_io": "", "api_key_cn": "",
                    "api_base": "https://api.minimax.io",
                    "model": "MiniMax-H3", "custom_model": ""},
        "comfyui": {"mode": "local", "url": "http://127.0.0.1:8188", "cloud_api_key": "",
                    "workflow": "", "checkpoint": "",
                    "rh_api_key_cn": "", "rh_api_key_ai": "",
                    "rh_workflow_id": "", "rh_workflows": [],
                    "rh_instance_type": "standard"},
    },
    "music": {
        "provider": "openrouter",   # openrouter(Lyria 3 系列)| elevenlabs(Eleven Music)| minimax
        "agentics": {"profile_code": ""},
        "openrouter": {"api_key": "", "model": "google/lyria-3-clip-preview",
                       "custom_model": ""},
        # Eleven Music:POST /v1/music;force_instrumental 默认 true(BGM 场景纯音乐)
        "elevenlabs": {"api_key": "", "model": "music_v1", "custom_model": "",
                       "force_instrumental": True},
        # MiniMax Music:force_instrumental 同上;关闭时按 prompt 自动写词演唱
        "minimax": {"api_key_io": "", "api_key_cn": "",
                    "api_base": "https://api.minimax.io",
                    "model": "music-3.0", "custom_model": "",
                    "force_instrumental": True},
        # ComfyUI 工作流按用户选择配置;模板说明见 comfy/music-ace-step-v1-api.md。
        "comfyui": {"mode": "local", "url": "http://127.0.0.1:8188", "cloud_api_key": "",
                    "workflow": "",
                    "checkpoint": "", "lyrics": "[Instrumental]",
                    "rh_api_key_cn": "", "rh_api_key_ai": "",
                    "rh_workflow_id": "", "rh_workflows": [],
                    "rh_instance_type": "standard"},
    },
    "tts": {
        "provider": "volcengine",   # openrouter | volcengine(豆包语音) | minimax | elevenlabs
        "agentics": {"profile_code": ""},
        "openrouter": {"api_key": "", "model": "x-ai/grok-voice-tts-1.0",
                       "custom_model": "", "voice": "eve"},
        # 豆包语音 openspeech v3(Doubao-Seed-TTS 2.0):凭证=新版语音技术控制台
        # 「API Key 管理」的 API Key(X-Api-Key 单头鉴权,非方舟 ARK Key;旧版
        # App ID + Access Token 已废弃);model 即 X-Api-Resource-Id 档位
        "volcengine": {"api_key": "", "model": "seed-tts-2.0",
                       "custom_model": "", "voice": ""},
        # MiniMax Speech:voice 存 voice_id(设置页可拉取音色库选择)
        "minimax": {"api_key_io": "", "api_key_cn": "",
                    "api_base": "https://api.minimax.io",
                    "model": "speech-2.8-hd", "custom_model": "", "voice": ""},
        # ElevenLabs:voice 存 voice_id;Voice Library 音色须先加入账号(设置页一键加入)
        "elevenlabs": {"api_key": "", "model": "eleven_multilingual_v2",
                       "custom_model": "", "voice": ""},
        # ComfyUI:本地 TTS,按角色内容自动选本地参考音频;工作流由用户选择。
        "comfyui": {"mode": "local", "url": "http://127.0.0.1:8188", "cloud_api_key": "",
                    "workflow": "",
                    "checkpoint": "", "timbre_dir": "data/TimbreModel",
                    "timbre_catalog": "data/TimbreModel/catalog.json",
                    "rh_api_key_cn": "", "rh_api_key_ai": "",
                    "rh_workflow_id": "", "rh_workflows": [],
                    "rh_instance_type": "standard"},
    },
    # 数字人:任意人物图片 + 对白音频生成单人说话片段。Kling 固定中国北京接口；
    # ComfyUI 渠道与图像/视频等段同口径:运行方式 mode=local(本地 InfiniteTalk)/
    # cloud(Comfy Cloud)/rh_cn/rh_ai(RunningHub 云端工作区工作流,rh_* 字段)
    "digital_human": {
        "provider": "heygen",  # heygen | klingai | comfyui
        "heygen": {"api_key": "", "resolution": "720p", "aspect_ratio": "16:9"},
        "klingai": {"api_key": "", "mode": "std"},
        "comfyui": {"mode": "local", "url": "http://127.0.0.1:8188", "cloud_api_key": "",
                    "workflow": "comfy/digitalhuman-infinitetalk-api.json",
                    "rh_api_key_cn": "", "rh_api_key_ai": "",
                    "rh_workflow_id": "", "rh_workflows": [],
                    "rh_instance_type": "standard"},
    },
    # deepagents 文字模型:local=OpenAI 兼容本地端点(LM Studio/Ollama/vLLM…);
    # cloud=OpenAI 兼容云端端点(默认 DeepSeek 官方 API,可换任意兼容服务商);
    # openrouter=OpenRouter 云端(base_url 固定 https://openrouter.ai/api/v1)
    "deepagents": {
        "provider": "local",   # agentics | local | cloud | openrouter
        "agentics": {"model": "anthropic/claude-sonnet-5", "custom_model": ""},
        "local": {"base_url": "http://127.0.0.1:1234/v1",
                  "api_key": "lm-studio", "model": ""},
        "cloud": {"base_url": "https://api.deepseek.com",
                  "api_key": "", "model": "deepseek-v4-flash",
                  "custom_model": ""},
        "openrouter": {"api_key": "", "model": "anthropic/claude-sonnet-5",
                       "custom_model": ""},
    },
    # 文件管理(设置页「文件管理」):对象存储托管,V2V 参考视频经预签名 URL 传给
    # 方舟(reference_video 硬性要求公网 URL,base64 内联被拒);生效渠道=选中标签页
    "storage": {
        "provider": "tos",   # tos | oss | cos | s3
        "tos": {"access_key": "", "secret_key": "",
                "endpoint": "tos-cn-beijing.volces.com", "region": "cn-beijing",
                "bucket": "", "prefix": "genmedia-refs/", "url_expires": 86400},
        "oss": {"access_key": "", "secret_key": "",
                "endpoint": "oss-cn-beijing.aliyuncs.com", "region": "",
                "bucket": "", "prefix": "genmedia-refs/", "url_expires": 86400},
        "cos": {"access_key": "", "secret_key": "",
                "endpoint": "", "region": "ap-beijing",
                "bucket": "", "prefix": "genmedia-refs/", "url_expires": 86400},
        "s3": {"access_key": "", "secret_key": "",
               "endpoint": "", "region": "us-east-1",
               "bucket": "", "prefix": "genmedia-refs/", "url_expires": 86400},
    },
    # 虚拟人像资产库(设置 → 高级):火山方舟私域虚拟人像素材库。启用后人物概念图可
    # 一键入库,生成视频时已入库(Active)的参考图自动改用 asset://<id> 提交,规避
    # Seedance 对含人脸参考图的审核拦截。AK/SK 为火山 IAM 密钥(留空回退文件托管
    # TOS 的 AK/SK 或环境变量 TOS_ACCESS_KEY/TOS_SECRET_KEY);project_name 须与
    # 视频生成所用方舟 ARK API Key 所属项目一致(默认 default);group_id 为首次
    # 上传时自动创建的素材组,记录后复用。auto_manage=全自动管理:开启且视频模型为
    # 火山引擎时,每集 video-generation 工单开跑前自动清空资产库(规避素材数量上限)
    # 并把该集组 prompt 引用的人物概念图入库、等审核 Active 后开跑(见 execute_run 钩子);
    # auto_manage_creatures=生物入库(默认关):开启时把组 prompt 引用的生物概念图
    # (assets/concepts/creatures/)与人物图一起入库(genmedia 按 sha256 台账匹配不分目录)
    "avatar_assets": {"enabled": False, "auto_manage": False,
                      "auto_manage_creatures": False,
                      "access_key": "", "secret_key": "",
                      "project_name": "default", "group_id": "",
                      "group_name": "VideoAgents"},
    # 时长设置:每集目标时长(分钟)与单个分镜时长范围(秒);
    # long_take=长镜头(默认关,2026-09-01):开=组间续接沿用尾帧锚流程(前组尾帧
    # 截图列入下组 refs+开场声明句);关=所有组交界 anchor: none、refs 不挂任何
    # *.last_frame.png,仅靠换构图文字承接开场句续接(低分辨率草稿档下低清尾帧作
    # 参考图会拖累续接组画质与人脸一致性,故默认关闭;续接链不存在,组可并行生成)
    "duration": {"episode_minutes": 10, "shot_min_s": 1, "shot_max_s": 10,
                 "long_take": False},
    # 视频模型设置:生成组总时长上限与每组参考素材数量上限——须与所选视频生成模型的
    # 能力匹配(Seedance 2.0 系列:≤15s/9图/3视频/3音频;Seedance 2.5:≤30s/30图/
    # 10视频/10音频;MiniMax H3:≤15s/9图/0视频/2音频),默认值按 2.0 口径(界面
    # 「默认值」按钮一键切换三档);注入 Agent 系统提示词约束分组与 prompt 组装,
    # 模型侧硬限另由 genmedia 按 model id 强制校验
    "shot_group": {"max_group_s": 15, "max_ref_images": 9,
                   "max_ref_videos": 3, "max_ref_audios": 3},
    # Agent 语言模型分配策略(顶栏「语言模型」下拉驱动:选「智能分配」→ smart_<引擎>,
    # 选具体模型→ global 跟随顶栏):初始化默认智能分配(顶栏默认引擎 claude)
    "agentmodel_mode": "smart_claude",
    # 输出设置(设置菜单「输出设置」):画幅预设 youtube=16:9(默认)/douyin=9:16/custom;
    # 语言约束剧本/台词/旁白/字幕/配音/发布物料;
    # 视频分辨率按用途分档:draft=草稿/迭代/待审版本,final=审核确认后的成片终稿;
    # platforms=发布平台(可多选,默认全选):只决定 Phase 11 发布目标与画幅矩阵/封面/字幕的平台清单,
    #   主生产画幅仍由 aspect_preset 单选决定;与主画幅不同画幅的平台由 platform-adapter 发布期裁/补适配;
    # subtitle_burn_in=内嵌字幕(默认关):开启后成片终稿自动把 subtitles.srt 烧录进画面;
    # caption_enabled=花字(默认关):开启后 caption Agent 在关键节点设计花字+配套音效,
    #   超分后的终版组 clip 上烧录副本,另封装花字版成片 final_caption.mp4
    #   (a:0=声轨+SFX 预混、a:1=声轨存档;干净版 final.mp4 照常产出,双版本并列,WORKFLOW.md §9A)
    # dialogue_voice=对白配音:native=视频原声(默认,对白语音由视频模型原生合成,不做任何对白 TTS)/
    #   dubbing=后期配音(组视频生成后按画面中人物开口的时间位置,结合角色 voice.json/casting.json
    #   用 TTS 逐句合成该角色对白并按开口时长贴合口型,替换组 clip 对白轨;workflow p7-dub)
    # narration_enabled=旁白(2026-09-01):此处默认 True 只作存量项目缺键回退(老项目旁白链路照常);
    #   **新建项目基线=False(旁白默认关闭)**——api_projects_create 置基线、向导默认不勾选(同 review 基线先例);
    #   关闭=用户约定全片没有任何旁白——
    #   p5-narration/p8-narrator 不派发,shot_list 不写 narration_anchors、audio_plan 禁 narration_over
    #   (无对白组一律 ambient_only,silent_rationale 照常核查但不再回派补写旁白),混音只有原生轨+BGM 两路,
    #   narration 系列机检跳过(报 skipped: narration off;WORKFLOW.md §7D/§8B)
    # spatial_blocking=人物精确空间位置(默认开)——2026-09-07 起含义=「用白模摄影机视角视频给视频生成定位人物」:
    #   开=场景布局包流程(每场景俯视空间布局图+9 宫格多角度图+layout.json,分镜组登记人物起点/动线/终点
    #   blocking_map)+ 白模链(p4-scene-model / p6-whitebox),导出的 camera.mp4/top.mp4 自动接成组视频生成的
    #   参考视频(video_refs + Whitebox reference/legend 固定段,code/sync_whitebox_refs.py),prompt 另挂干净俯视图+九格图
    #   并逐字注入 route_en(机检 scene_layout_pack_ok/blocking_map_present/layout_map_bound/whitebox_ref_bound);关=沿用单张场景概念图流程
    #   (environment-concept 只出主视角图+变体,不写 blocking_map,prompt 场景锚挂概念图,相关机检跳过)
    "output": {"aspect_preset": "youtube", "aspect_custom": "", "language": "English",
               "draft_resolution": "480p", "final_resolution": "480p",
               "subtitle_burn_in": False, "caption_enabled": False,
               "narration_enabled": True,
               "dialogue_voice": "native",
               "spatial_blocking": True,
               # whitebox_top_video(默认关,暂无 UI):白模参考视频默认只挂 camera.mp4;置 true 才在预算允许时追加 top.mp4(code/sync_whitebox_refs.py,2026-09-07)
               "whitebox_top_video": False,
               "platforms": ["youtube", "bilibili", "tiktok", "douyin", "xiaohongshu"]},
    # 审核设置(设置菜单「审核设置」):各维度审核力度 0-100(0=不审核 100=最严格),按项目独立;
    # 默认全 0=不审核(2026-07-23 由 60 改),用户在设置中调高才生效;
    # evaluation=质量评委(00-orchestration/evaluation)验收「必须照改」合格线,
    # 默认 60(较其 SOUL.md 固定 80 放宽),0=跳过质量评委(orchestrator 不派 evaluation 工单)
    "review": {"evaluation": 60, **{k: 0 for k in (
        "audio_quality", "character_consistency", "content_safety", "copyright",
        "logic", "timeline", "visual_quality", "worldview")}},
    # 片头片尾设置(设置菜单「片头片尾」):三段包装的开关 + 片头/片尾自由文本要求,按项目独立
    "packaging": {"intro_enabled": True, "intro_notes": "",
                  "outro_enabled": True, "outro_notes": "",
                  "teaser_enabled": True},
    # 版本管理开关(版本管理页,按项目独立):默认关——orchestrator 不派
    # 00-orchestration/version 工单(产物登记与闸门冻结跳过),开启后照常
    "versioning": {"enabled": False},
    # 视频提示词技能(视频模型设置弹窗 / H3A 签字弹窗 / 分镜预览页,按项目独立,2026-08-28):
    #   决定 prompt 工位(08-video-gen/prompt)写组级 video_prompt 时必须套用的官方提示词技能。
    #   mode=auto(默认):按真正跑视频生成的模型(video-generation 工位渠道覆盖优先)解析——
    #     Seedance 2.5→sd25-pe / Seedance 2.0 系列→sd20-prompt-writing / MiniMax H3(任意渠道)→
    #     h3-prompt-writing,解析不到(ComfyUI 工作流无模型 id、新模型无对应技能)= 无技能并提醒手选;
    #   mode=manual:skill_id 为用户从该工位已安装技能里指定的一项(与生效模型不匹配只告警不阻塞);
    #   mode=off:本项目不套用技能(回执 skill_applied.id=null, reason=user_skipped)。
    #   effective=运行时解析快照 {skill_id, mode, resolved_from, reason, decided_at}——派 prompt 工单、
    #   保存设置、H3A 签字时刷新;机检 code/prompt_skill_check.py(prompt_skill_applied)以此为准。
    "prompt_skill": {"mode": "auto", "skill_id": "", "effective": {}},
    # 仅记录用户覆盖;未设置时只有生效视频提示词技能默认开启。
    "project_skills": {"overrides": {}},
    # 界面语言(设置菜单「界面语言」,全局):影响界面文案与 agent 对话/汇报语言;
    # ""=未设置(首次打开浏览器自动判断后写入),成片内容语言仍由项目级 output.language 决定;
    # 持久化以 state.json 的 ui_lang 为准(写入时双写,此键保留兼容旧版回读)
    "ui_language": "",
}

# 界面语言:code -> 提示词中使用的语言名称(与 static/i18n/i18n.js 的 LANGS 一致)
UI_LANG_NAMES = {
    "en": "English", "zh": "中文", "ja": "日本語", "ko": "한국어",
    "vi": "Tiếng Việt", "es": "Español", "fr": "français", "de": "Deutsch",
    "id": "Bahasa Indonesia", "pt": "Português", "ru": "русский", "ar": "العربية",
}


def ui_lang_code(cfg: dict | None = None) -> str:
    """全局界面语言代码:以 state.json 的 ui_lang 为准(按全局保存,桌面端换随机
    端口后浏览器 localStorage 失效也能恢复);旧版只存 genconfig,读取回落无感迁移。"""
    return str(STATE.get("ui_lang") or (cfg or load_genconfig()).get("ui_language") or "")


def resolve_ui_language(cfg: dict | None = None) -> str:
    """界面语言名称(未设置回落中文,与历史行为一致)。"""
    return UI_LANG_NAMES.get(ui_lang_code(cfg) or "zh", "中文")

# 审核维度:key -> (名称, 负责的 QA Agent)(审核设置弹窗与提示词注入共用,顺序即展示顺序)
REVIEW_DIMENSIONS = {
    "audio_quality": ("音频质量审核", "11-qa/audio-qa"),
    "character_consistency": ("角色一致性审核", "11-qa/character-consistency-qa"),
    "content_safety": ("内容安全", "11-qa/content-safety"),
    "copyright": ("版权审核", "11-qa/copyright"),
    "logic": ("逻辑审核", "11-qa/logic-qa"),
    "timeline": ("时间线审核", "11-qa/timeline-qa"),
    "visual_quality": ("画面质量审核", "11-qa/visual-qa"),
    "worldview": ("世界观审核", "11-qa/world-consistency-qa"),
}

# 片头片尾设定的注入对象:包装制作(title)、占位(edit)、预告文案上游(hook)+ 调度(派单时写入工单)
PACKAGING_AGENTS = {"10-editing/title", "10-editing/edit", "01-story/hook"} | DISPATCHERS

# 花字设定的详细纪律注入对象:设计与烧录(caption)、花字版封装(edit)、发布物料(platform-adapter)
# + 调度(排产 condition 判定与工单撰写);其余 Agent 只收一行开关状态(WORKFLOW.md §9A)
CAPTION_AGENTS = {"10-editing/caption", "10-editing/edit",
                  "12-publishing/platform-adapter"} | DISPATCHERS

# 输出画幅预设:preset -> (比例, 名称);custom 走 aspect_custom(格式 宽:高)
# 发布平台:key -> (名称, 默认画幅);「输出设置」发布平台多选,只驱动 Phase 11 发布目标与
# aspect_ratio.json 平台矩阵/thumbnail 每平台封面/subtitle 每平台字幕的清单(展示顺序即此顺序)
OUTPUT_PLATFORMS = {
    "youtube": ("YouTube", "16:9"),
    "bilibili": ("Bilibili", "16:9"),
    "tiktok": ("TikTok", "9:16"),
    "douyin": ("抖音", "9:16"),
    "xiaohongshu": ("小红书", "9:16"),
}
OUTPUT_LANGS = ("English", "中文", "日本語", "한국어", "Tiếng Việt", "Español",
                "français", "Deutsch", "Indonesia", "Português", "русский", "عربي")
# 视频分辨率档位(4k 仅 Seedance 2.0 标准版支持;Seedance 2.5 仅 480p/720p,
# genmedia 越档自动压回;方舟 API 取小写)
VIDEO_RESOLUTIONS = ("360p", "480p", "720p", "1080p", "4k")
# 对白配音方式:native=视频原声(默认)/dubbing=后期配音(TTS 按画面开口时段贴合,workflow p7-dub)
DIALOGUE_VOICE_MODES = ("native", "dubbing")


def resolve_platforms(cfg: dict) -> list[tuple[str, str, str]]:
    """genconfig -> 已选发布平台 [(key, 名称, 默认画幅), ...],按注册表顺序;为空回落全选。"""
    out = cfg.get("output") or {}
    sel = out.get("platforms")
    if not isinstance(sel, list) or not sel:
        sel = list(OUTPUT_PLATFORMS)
    return [(k, OUTPUT_PLATFORMS[k][0], OUTPUT_PLATFORMS[k][1])
            for k in OUTPUT_PLATFORMS if k in sel]


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _split_legacy_key(cfg: dict, legacy_field: str, site_fields: tuple[str, str]) -> None:
    """旧版单一 API Key(两区域/站点共用)→ 按区域分别保存:旧行为即两侧同 Key,
    迁移把旧值填入两侧空位并移除旧字段(下次保存落盘即完成迁移)。"""
    legacy = str(cfg.pop(legacy_field, "") or "").strip()
    if legacy:
        for field in site_fields:
            if not str(cfg.get(field) or "").strip():
                cfg[field] = legacy


def _migrate_genconfig(config: dict) -> None:
    """Keep configurations saved by older versions usable(comfy 模板路径迁出 data/;
    MiniMax/RunningHub 单一 Key 拆分为按接口区域/站点分别保存)。"""
    for kind in ("image", "video", "music", "tts", "digital_human"):
        section = config.get(kind, {})
        # Older desktop defaults selected OpenRouter with an empty user key and
        # silently routed that tab through the Agentics account wrapper. Keep
        # those installs working while making the two providers explicit.
        if (kind != "digital_human" and os.environ.get("VIDEOAGENTS_USER_JWT")
                and section.get("provider") == "openrouter"
                and not str((section.get("openrouter") or {}).get("api_key")
                            or os.environ.get("OPENROUTER_API_KEY") or "").strip()):
            section["provider"] = "agentics"
        mm = section.get("minimax")
        if isinstance(mm, dict):
            _split_legacy_key(mm, "api_key", ("api_key_io", "api_key_cn"))
        comfy = section.get("comfyui")
        if not isinstance(comfy, dict):
            continue
        _split_legacy_key(comfy, "rh_api_key", ("rh_api_key_cn", "rh_api_key_ai"))
        for key in ("workflow", "ref_workflow"):
            value = comfy.get(key)
            if isinstance(value, str) and value.startswith("data/comfy/"):
                comfy[key] = "comfy/" + value[len("data/comfy/"):]
        # H3 uses the built-in compatible model/sampler settings. Older UI
        # versions persisted this advanced object; discard it on load.
        if kind == "video":
            comfy.pop("h3", None)
    # 数字人曾把 RunningHub 存成独立 provider/配置段(digital_human.runninghub),现并回
    # comfyui 段的运行方式:字段搬到 comfyui.rh_*(仅填空位),旧生效渠道 runninghub ⇒
    # comfyui + mode=站点;旧段移除,下次保存落盘即完成迁移
    dh = config.get("digital_human")
    if isinstance(dh, dict) and isinstance(dh.get("runninghub"), dict):
        rh = dh.pop("runninghub")
        comfy = dh.setdefault("comfyui", {})
        if not isinstance(comfy, dict):
            comfy = dh["comfyui"] = {}
        for src, dst in (("api_key_cn", "rh_api_key_cn"), ("api_key_ai", "rh_api_key_ai"),
                         ("workflow_id", "rh_workflow_id"), ("workflows", "rh_workflows"),
                         ("instance_type", "rh_instance_type")):
            if rh.get(src) and not comfy.get(dst):
                comfy[dst] = rh[src]
        if dh.get("provider") == "runninghub":
            dh["provider"] = "comfyui"
            comfy["mode"] = rh.get("site") if rh.get("site") in RH_BASES else "rh_cn"
    da = config.get("deepagents")
    if (isinstance(da, dict) and os.environ.get("VIDEOAGENTS_USER_JWT")
            and da.get("provider") == "openrouter"
            and not str((da.get("openrouter") or {}).get("api_key")
                        or os.environ.get("OPENROUTER_API_KEY") or "").strip()):
        da["provider"] = "agentics"


def load_genconfig() -> dict:
    try:
        saved = json.loads(GENCONFIG_PATH.read_text())
    except Exception:
        saved = {}
    da = saved.get("deepagents")
    if isinstance(da, dict) and "provider" not in da and "local" not in da:
        # 旧版扁平格式(base_url/api_key/model 直挂 deepagents)→ 迁移为 local 渠道
        saved["deepagents"] = {"provider": "local", "local": da}
    _migrate_genconfig(saved)
    return _merge(DEFAULT_GENCONFIG, saved)


def save_genconfig(cfg: dict):
    atomic_write_json(GENCONFIG_PATH, cfg)


def active_video_provider(cfg: dict | None = None, agent_id: str = "") -> str:
    """生效视频渠道:传 agent_id 时先看「每 Agent 模型配置」的视频渠道覆盖(与 genmedia
    _agent_provider_override 同口径),空则按全局「生成模型」页。"""
    v = (cfg or load_genconfig()).get("video") or {}
    ov = str(agent_model_config(agent_id).get("video_provider") or "") if agent_id else ""
    return ov or str(v.get("provider") or "volcengine")


def active_video_model(cfg: dict | None = None, agent_id: str = "") -> str:
    """「生成模型」页当前生效的视频模型 id(custom_model 优先;comfyui 等无模型渠道返 "")。
    传 agent_id 时按该 Agent 的视频渠道覆盖取对应渠道段的模型。"""
    cfg = cfg or load_genconfig()
    v = cfg.get("video") or {}
    pc = v.get(active_video_provider(cfg, agent_id)) or {}
    return str(pc.get("custom_model") or pc.get("model") or pc.get("profile_code") or "")


VIDEO_AGENT_ID = "08-video-gen/video-generation"     # 实际提交视频生成请求的工位
PROMPT_AGENT_ID = "08-video-gen/prompt"


def effective_video_model(cfg: dict | None = None) -> str:
    """真正跑视频生成的模型 id:按 video-generation 工位的渠道覆盖解析(2026-08-28 修:
    此前 prompt 技能注入只看全局渠道,该工位覆盖了渠道时判定失真)。"""
    return active_video_model(cfg, VIDEO_AGENT_ID)


def is_seedance25(model: str) -> bool:
    """Seedance 2.5 判定(与 genmedia._seedance_gen 同口径:大小写不敏感,兼容点号写法)。"""
    m = (model or "").lower()
    return "seedance-2-5" in m or "seedance-2.5" in m


# Seedance 2.5 官方提示词优化 skill(sd25-pe):仅当生效视频模型为 2.5 时注入
# 加载指令给 prompt agent;文件经官方 .well-known 索引下载并 sha256 校验后随仓库安装
SD25_PE_SKILL = "agents/08-video-gen/prompt/skills/sd25-pe/SKILL.md"


def is_seedance20(model: str) -> bool:
    """Seedance 2.0 系列判定(含 fast/mini 等衍生版;与 genmedia._seedance_gen 同口径:
    命中 seedance-2 且非 2.5 即按 2.0 口径,大小写不敏感)。"""
    return "seedance-2" in (model or "").lower() and not is_seedance25(model)


# Seedance 2.0 官方提示词写作 skill(sd20-prompt-writing):仅当生效视频模型为
# 2.0 系列(含 fast/mini)时注入加载指令给 prompt agent;随仓库分发
SD20_PE_SKILL = "agents/08-video-gen/prompt/skills/sd20-prompt-writing/SKILL.md"


def is_minimax_h3(model: str) -> bool:
    """MiniMax H3 判定:名字同时含 minimax 与 h3 即命中(大小写不敏感,不限写法/顺序/分隔符——
    H3 是开源模型,RunningHub/ComfyUI 等渠道的模型名、工作流名、节点名写法各异,如
    minimax-h3 / MiniMax: H3 / MiniMax-H3 / minimax/h3-preview / MiniMaxH3ReferenceToVideo)。"""
    m = (model or "").lower()
    return "minimax" in m and "h3" in m


# RunningHub(第三方云托管 ComfyUI):.cn/.ai 双站同构,账号与 Key 不互通;
# 工作流 JSON 经 getJsonApiFormat 拉取后缓存,genmedia 运行时同读该目录
RH_BASES = {"rh_cn": "https://www.runninghub.cn", "rh_ai": "https://www.runninghub.ai"}
RH_CACHE_DIR = RUNTIME_DIR / "rh_workflows"


def _rh_cached_workflow(comfy: dict, wf_id: str = "") -> str:
    """读 RunningHub 工作流本地缓存文本;缓存缺失返回空串(不发起网络请求)。"""
    wf_id = str(wf_id or comfy.get("rh_workflow_id") or "").strip()
    mode = comfy.get("mode") or ""
    if mode not in RH_BASES or not wf_id:
        return ""
    try:
        return (RH_CACHE_DIR / f"{mode}-{wf_id}.json").read_text(encoding="utf-8")
    except OSError:
        return ""


def is_minimax_h3_active(cfg: dict | None = None) -> bool:
    """生效视频渠道是否 MiniMax H3(引擎无关,统一按 is_minimax_h3「名字含 minimax 与 h3」判定):
    OpenRouter/MiniMax/RunningHub 直绑等按生效模型 id,ComfyUI 本地/Comfy Cloud 按所选工作流
    文件名(如 comfy/video-minimax-h3-ref2va-api.json),RunningHub 工作流方式按缓存的云端
    工作流 JSON 全文(节点类名 MiniMaxH3ReferenceToVideo 或任何含 minimax+h3 的节点/标题均命中)。"""
    cfg = cfg or load_genconfig()
    v = cfg.get("video") or {}
    if active_video_provider(cfg, VIDEO_AGENT_ID) == "comfyui":
        comfy = v.get("comfyui") or {}
        if (comfy.get("mode") or "local") in RH_BASES:
            return is_minimax_h3(_rh_cached_workflow(comfy))
        return is_minimax_h3(comfy.get("workflow") or "")
    return is_minimax_h3(effective_video_model(cfg))


# MiniMax H3 官方提示词写作 skill(h3-prompt-writing):仅当生效视频模型/工作流名含 minimax+h3
# 时注入加载指令给 prompt agent(H3 开源,任何渠道跑 H3 都触发);经 npx skills add
# MiniMax-AI/MiniMax-H3 安装后随仓库分发
H3_PE_SKILL = "agents/08-video-gen/prompt/skills/h3-prompt-writing/SKILL.md"


def minimax_region_key(mm: dict) -> str:
    """MiniMax 按「接口区域」(api_base)取对应区域的 Key:国内版 minimaxi.com →
    api_key_cn,否则海外版 → api_key_io;旧版单一 api_key 兜底(genmedia 同口径)。"""
    field = "api_key_cn" if "minimaxi.com" in str(mm.get("api_base") or "") else "api_key_io"
    return str(mm.get(field) or mm.get("api_key") or "").strip()


def is_minimax_upscale_available(cfg: dict | None = None) -> bool:
    """MiniMax Regenerate-2K 超分可用性:「生成模型」页视频 MiniMax 标签页已填当前
    接口区域的 API Key(或设环境变量 MINIMAX_API_KEY)即可,与生效视频渠道无关
    (genmedia 超分凭证同口径)。"""
    v = (cfg or load_genconfig()).get("video") or {}
    key = minimax_region_key(v.get("minimax") or {})
    return bool(key or os.environ.get("MINIMAX_API_KEY", "").strip())


# MiniMax Regenerate-2K 超分 skill:仅当 MiniMax Key 已配置时注入加载指令给 upscale agent
MINIMAX_UPSCALE_SKILL = "agents/08-video-gen/upscale/skills/minimax-regenerate-2k/SKILL.md"


# RunningHub 云端工作流参数化调用 skill:仅当视频渠道为 ComfyUI RunningHub 运行方式时
# 注入加载指令给 video-generation agent
RUNNINGHUB_VIDEO_SKILL = ("agents/08-video-gen/video-generation/skills/"
                          "runninghub-cloud-workflow/SKILL.md")
AGENTICS_VIDEO_SKILL = ("agents/08-video-gen/video-generation/skills/"
                        "agentics-media-generation/SKILL.md")

# 本地音频转写 skill:音频转文字 Agent 每单开工前必须读取；模型由宿主缓存到 data/models/
AUDIO_TRANSCRIPTION_SKILL = (
    "agents/09-audio/audio-transcription/skills/audio-transcription/SKILL.md")


# ---------------- Agent 技能(对话面板顶栏「技能」入口) ----------------
# 技能 = agents/<类别>/<agent>/skills/<技能目录>/SKILL.md(插件 Agent 同构)。
# 安装清单/全局禁用与项目选择分离:全局禁用保留为总闸;项目 overrides 控制自动激活。
# 默认仅模型/提示词设置选中的技能开启,其他技能(含 SOUL 引用)默认关闭。
# 注册表保留模型/渠道等适用条件;项目勾选不绕过这些条件。
SKILL_ACTIVATIONS: dict[str, dict] = {
    "08-video-gen/prompt/sd25-pe": {
        "kind": "conditional", "condition": "生效视频模型为 Seedance 2.5"},
    "08-video-gen/prompt/sd20-prompt-writing": {
        "kind": "conditional", "condition": "生效视频模型为 Seedance 2.0 系列"},
    "08-video-gen/prompt/h3-prompt-writing": {
        "kind": "conditional", "condition": "生效视频模型/工作流名含 minimax 与 h3(任意渠道)"},
    "08-video-gen/prompt/performance-direction": {
        "kind": "soul", "condition": "组 audio_plan 为 dialogue 或所属场次为情绪峰值场(SOUL.md 引用,引擎无关)"},
    "08-video-gen/upscale/minimax-regenerate-2k": {
        "kind": "conditional", "condition": "MiniMax API Key 已配置"},
    "08-video-gen/video-generation/runninghub-cloud-workflow": {
        "kind": "conditional", "condition": "视频渠道为 ComfyUI RunningHub 运行方式"},
    "08-video-gen/video-generation/agentics-media-generation": {
        "kind": "conditional", "condition": "视频渠道为 AgenticsLLM"},
    "09-audio/audio-transcription/audio-transcription": {"kind": "always"},
    "10-editing/caption/caption-styling": {"kind": "soul"},
    "12-publishing/publisher/skill-youtube-cdp-draft": {"kind": "soul", "condition": "发布到 YouTube"},
    "12-publishing/publisher/skill-douyin-cdp-draft": {"kind": "soul", "condition": "发布到抖音"},
    "12-publishing/publisher/skill-tiktok-cdp-draft": {"kind": "soul", "condition": "发布到 TikTok"},
    "12-publishing/publisher/skill-xhs-cdp-draft": {"kind": "soul", "condition": "发布到小红书"},
    "12-publishing/publisher/skill-bilibili-cdp-draft": {"kind": "soul", "condition": "发布到 B 站"},
    "12-publishing/publisher/XiaohongshuSkills": {
        "kind": "library", "condition": "skill-xhs-cdp-draft 的依赖库"},
}
_SKILL_DIR_RE = re.compile(r"[A-Za-z0-9_\-]+")


def _parse_skill_frontmatter(text: str) -> dict:
    """极简 YAML frontmatter:只取顶层 name/description(支持 | / > 块标量),零依赖。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict = {}
    key, buf = None, []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            if key and buf:
                out[key] = " ".join(x.strip() for x in buf if x.strip()).strip()
            key, val = m.group(1), m.group(2).strip()
            buf = []
            if val in ("|", ">", "|-", ">-"):
                continue
            out[key] = val.strip("'\"")
            key = None
        elif key is not None:
            buf.append(line)
    if key and buf:
        out[key] = " ".join(x.strip() for x in buf if x.strip()).strip()
    return {k: v for k, v in out.items() if k in ("name", "description")}


_SKILLS_CACHE: tuple[float, list] = (0.0, [])
_SKILLS_CACHE_TTL = 30.0


def scan_agent_skills(refresh: bool = False) -> list[dict]:
    """扫描全部 Agent(内置 + 已启用插件)的 skills/ 目录,返回不含开关状态的技能清单(带 TTL 缓存)。"""
    global _SKILLS_CACHE
    if not refresh and time.time() < _SKILLS_CACHE[0]:
        return _SKILLS_CACHE[1]
    skills: list[dict] = []
    for a in list_agents(refresh=refresh):
        d = agent_dir(a["id"])
        if not d or not (d / "skills").is_dir():
            continue
        for sd in sorted((d / "skills").iterdir()):
            f = sd / "SKILL.md"
            if not sd.is_dir() or not f.is_file() or not _SKILL_DIR_RE.fullmatch(sd.name):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                text = ""
            fm = _parse_skill_frontmatter(text)
            desc = fm.get("description", "")
            if not desc:                      # 无 frontmatter:优先首条引用行,其次首个标题/正文
                body = [x.strip() for x in text.splitlines() if x.strip() and not x.strip().startswith("---")]
                quote = next((x for x in body if x.startswith(">")), "")
                first = quote or (body[0] if body else "")
                desc = re.sub(r"^SKILL\.md\s*[—\-]+\s*", "", first.lstrip("#>").strip())
            sid = f"{a['id']}/{sd.name}"
            act = SKILL_ACTIVATIONS.get(sid, {"kind": "generic"})
            try:
                rel = str(f.relative_to(ROOT))
            except ValueError:
                rel = str(f)
            skills.append({
                "id": sid,
                "agent_id": a["id"],
                "agent_name": a["name"],
                "category_name": a["category_name"],
                "plugin": a.get("plugin"),
                "dir": sd.name,
                "name": fm.get("name") or sd.name,
                "description": desc[:400],
                "path": rel,
                "kind": act.get("kind", "generic"),
                "condition": act.get("condition", ""),
            })
    _SKILLS_CACHE = (time.time() + _SKILLS_CACHE_TTL, skills)
    return skills


def skills_disabled_setting() -> set[str]:
    v = STATE.get("skills_disabled")
    return {str(x) for x in v} if isinstance(v, list) else set()


def skill_enabled(skill_id: str) -> bool:
    """技能开关(默认启用;未在清单里的 id 同样按 STATE 判定,便于注入段常量直接引用)。"""
    return skill_id not in skills_disabled_setting()


def list_agent_skills(refresh: bool = False) -> list[dict]:
    off = skills_disabled_setting()
    return [dict(s, enabled=s["id"] not in off) for s in scan_agent_skills(refresh)]


PERFORMANCE_SKILL_ID = "08-video-gen/prompt/performance-direction"


def project_skill_enabled(skill_id: str, project: str, *, default: bool | None = None) -> bool:
    """项目自动激活总闸;全局禁用优先,其余技能默认关闭。

    视频提示词解析器传入 default=True:已由模型/用户选择该技能,无需再次解析。
    overrides 只保存用户明确改动,因此未手动覆盖的提示词技能继续随模型联动。
    """
    if not skill_enabled(skill_id):
        return False
    overrides = (load_project_settings(project).get("project_skills") or {}).get("overrides") or {}
    if skill_id in overrides:
        return overrides[skill_id] is True
    if default is not None:
        return default
    return skill_id == resolve_prompt_skill(project, apply_project=False)["skill_id"]


def list_project_skills(project: str, refresh: bool = False) -> list[dict]:
    skills = list_agent_skills(refresh)
    default_id = resolve_prompt_skill(project, apply_project=False)["skill_id"]
    overrides = (load_project_settings(project).get("project_skills") or {}).get("overrides") or {}
    return [dict(s, selected=s["enabled"] and overrides.get(s["id"], s["id"] == default_id),
                 default_selected=s["id"] == default_id) for s in skills]


def agent_skill_prompt(agent_id: str, project: str) -> str:
    """每轮注入当前项目的技能契约,包括 SOUL 中的旧自动触发规则覆盖。"""
    skills = list_project_skills(project)
    mine = [s for s in skills if s["agent_id"] == agent_id]
    p = "\n\n## 项目技能自动激活契约(本轮最新设置,优先于历史会话与 SOUL.md 的自动触发规则)\n"
    p += ("仅自动激活当前项目勾选的技能;安装技能不等于允许自动执行。"
          "先按技能介绍、任务输入与触发条件判断适用性。适用时必须在相关工作之前完整读取 SKILL.md,"
          "按其前置条件和步骤执行:准备输入后应用生产技能,交付前应用检查技能;"
          "不要等产物写完才补读。模型/渠道等条件仍须满足,项目勾选不绕过条件。"
          "缺少必要输入时报告缺项;不适用时跳过并说明原因。"
          "回执逐项列出技能 id、已完成/已跳过/失败、原因以及实际产物和检查结果;"
          "读过文件不等于完成,适用技能未完成不得把本单标为完成。"
          "技能不得改变冻结台词、项目结构和工位职责。依赖库仅作为所选技能需要的参考资源读取,不独立自动执行。")
    selected = [s for s in mine if s["selected"]]
    p += "\n本工位已勾选:\n" if selected else "\n本工位没有勾选自动激活技能。"
    for s in selected:
        p += f"\n- `{s['id']}`: `{s['path']}`\n  适用: {s['description']}"
        if s["condition"]:
            p += f"\n  触发条件: {s['condition']}"
    disabled = [s for s in mine if not s["selected"]]
    if disabled:
        p += "\n本工位未启用自动激活(覆盖 SOUL.md 中的默认/强制加载指引,本单不要自动读取或执行):"
        p += "".join(f"\n- `{s['id']}`" for s in disabled)
    if agent_id == PROMPT_AGENT_ID:
        active = resolve_prompt_skill(project)
        p += (f"\n本轮提示词主技能: {active['skill_id'] or '无'};文件: {active['path'] or '无'}。"
              f"主设定回执 skill_applied.id={json.dumps(active['skill_id'] or None)},"
              f"不套用原因={active['reason'] or '无'}。此处覆盖历史会话里的技能 id 与选择;"
              "逐组以最新 group_settings effective 快照为准,组级技能同样必须通过项目勾选。")
    performance = next((s["selected"] for s in skills if s["id"] == PERFORMANCE_SKILL_ID), False)
    p += ("\n表演控制:项目已勾选。对白/情绪峰值任务按表演控制技能执行;blocking 准备 performance 意图层,"
          "prompt 生成证据层,video-generation/QA 复核 performance_bound。" if performance else
          "\n表演控制:项目未勾选(默认关闭)。所有工位不自动触发表演控制:"
          "blocking 不要求补写 performance 意图层,prompt 不要求表演证据层/performance[] 回执,"
          "不因缺少这些字段返工;performance_present/performance_bound 检查跳过,保留普通动作与对白写法。")
    if agent_id in DISPATCHERS:
        p += "\n派单时将以下项目已勾选技能安排到所属工位的适用任务中(其他技能不得自动派活):"
        p += "".join(f"\n- {s['agent_name']} ({s['agent_id']}): {s['id']} — {s['description']}"
                     for s in skills if s["selected"])
    return p


def skill_resume_message(message: str, contract: str, resumed: bool) -> str:
    """只在不重传角色提示词的引擎续轮附加最新契约,防止会话沿用旧勾选。"""
    return f"{contract}\n\n## 当前工作指令\n{message}" if resumed else message


async def api_project_skills_get(project: str):
    project = safe_slug(project)
    return {"project": project, "skills": list_project_skills(project, refresh=True)}


async def api_project_skills_set(project: str, body: dict):
    project = safe_slug(project)
    # 使用项目配置的严格校验与持久化;只修改本次勾选变化,保留其他项目/技能设置。
    await api_projconfig_set({"project": project, "project_skills": body})
    HUB.publish({"type": "project_skills", "project": project})
    return await api_project_skills_get(project)


# ---------------- 视频提示词技能(项目级 prompt_skill,2026-08-28) ----------------
# 触发链:项目设置 prompt_skill(auto/manual/off)→ resolve_prompt_skill 解析出本项目应套用的
# 技能 → 派 prompt 工单时写入系统提示词「提示词技能契约」段 + run["skill"] 面板 chip →
# 运行结束核验活动记录里确实 Read 过该 SKILL.md(prompt_skill_read)→ 产物 grpNNN.json 须带
# skill_applied 回执 → 机检 code/prompt_skill_check.py(prompt_skill_applied)对照 effective 快照。
PROMPT_SKILL_CHECK = "code/prompt_skill_check.py"
PROMPT_SKILL_SD25 = f"{PROMPT_AGENT_ID}/sd25-pe"
PROMPT_SKILL_SD20 = f"{PROMPT_AGENT_ID}/sd20-prompt-writing"
PROMPT_SKILL_H3 = f"{PROMPT_AGENT_ID}/h3-prompt-writing"
PROMPT_SKILL_REASONS = ("", "user_skipped", "no_match", "disabled", "missing")


def prompt_skill_candidates() -> list[dict]:
    """prompt 工位可作「提示词技能」的技能清单:引擎相关的 conditional 技能 + 注册表未登记的
    generic 技能(用户自装)。SOUL 按组条件引用的引擎无关技能(performance-direction)不在此列,
    它们与提示词技能并用而非二选一。"""
    return [dict(s) for s in list_agent_skills()
            if s["agent_id"] == PROMPT_AGENT_ID and s["kind"] in ("conditional", "generic")]


def _video_model_label(cfg: dict) -> str:
    """生效视频模型的可读标识:模型 id;ComfyUI 类渠道无模型 id 时给工作流名/RunningHub 工作流 id。"""
    model = effective_video_model(cfg)
    if model:
        return model
    prov = active_video_provider(cfg, VIDEO_AGENT_ID)
    if prov == "comfyui":
        comfy = (cfg.get("video") or {}).get("comfyui") or {}
        mode = comfy.get("mode") or "local"
        if mode in RH_BASES:
            return f"comfyui/{mode}:{comfy.get('rh_workflow_id') or '?'}"
        return f"comfyui:{comfy.get('workflow') or '?'}"
    return prov


def auto_prompt_skill(cfg: dict | None = None) -> tuple[str, str]:
    """按真正跑视频生成的模型自动匹配技能 → (skill_id 或 "", 解析依据)。"""
    cfg = cfg or load_genconfig()
    label = _video_model_label(cfg)
    if is_minimax_h3_active(cfg):
        return PROMPT_SKILL_H3, label
    return auto_prompt_skill_for_model(effective_video_model(cfg)), label


def auto_prompt_skill_for_model(model: str) -> str:
    """按模型 id 匹配提示词技能(组级视频模型覆盖时用;ComfyUI 类无模型 id 的渠道不适用)。"""
    if is_minimax_h3(model):
        return PROMPT_SKILL_H3
    if is_seedance25(model):
        return PROMPT_SKILL_SD25
    if is_seedance20(model):
        return PROMPT_SKILL_SD20
    return ""


def resolve_prompt_skill(project: str, cfg: dict | None = None, *, apply_project: bool = True) -> dict:
    """解析本项目 prompt 工位应套用的提示词技能(不落盘)。返回:
    mode / skill_id(最终生效,"" = 不套用)/ dir / name / path(SKILL.md 仓库相对路径)/
    auto_id / resolved_from / reason(""|user_skipped|no_match|disabled|missing)/ warning。"""
    cfg = cfg or load_genconfig()
    ps = load_project_settings(project).get("prompt_skill") or {}
    mode = ps.get("mode") if ps.get("mode") in PROMPT_SKILL_MODES else "auto"
    manual_id = str(ps.get("skill_id") or "")
    auto_id, resolved_from = auto_prompt_skill(cfg)
    cands = {c["id"]: c for c in prompt_skill_candidates()}
    reason, warning = "", ""
    if mode == "off":
        sid = ""
        reason = "user_skipped"
    elif mode == "manual":
        sid = manual_id
        if sid not in cands:
            reason, warning, sid = "missing", f"指定的提示词技能 {sid or '(空)'} 未安装,本项目按无技能处理", ""
        elif auto_id and auto_id != sid:
            warning = (f"用户指定 {cands[sid]['dir']},与生效视频模型 {resolved_from} "
                       f"自动匹配的 {(cands.get(auto_id) or {}).get('dir', auto_id)} 不同(按用户指定执行)")
    else:
        sid = auto_id
        if not sid:
            reason = "no_match"
            warning = (f"生效视频模型 {resolved_from or '(未知)'} 没有对应的提示词技能:"
                       "可在「视频模型设置→提示词技能」手选一项或选择跳过")
    if sid and sid not in cands:
        warning, reason, sid = f"提示词技能 {sid} 未安装,本项目按无技能处理", "missing", ""
    if sid and (not cands[sid]["enabled"] or
                (apply_project and not project_skill_enabled(sid, project, default=True))):
        warning = f"提示词技能 {cands[sid]['dir']} 未在项目技能中启用或已被全局禁用,本项目按无技能处理"
        reason, sid = "disabled", ""
    c = cands.get(sid) or {}
    return {"mode": mode, "skill_id": sid, "dir": c.get("dir", ""), "name": c.get("name", ""),
            "path": c.get("path", ""), "auto_id": auto_id, "auto_dir": (cands.get(auto_id) or {}).get("dir", ""),
            "resolved_from": resolved_from, "reason": reason, "warning": warning}


def sync_prompt_skill_effective(project: str) -> dict:
    """把解析结果快照进项目 settings.json 的 prompt_skill.effective(仅变化时写盘),返回最新项目设置。
    快照是机检 prompt_skill_applied 的对照基准,派 prompt 工单/保存设置/H3A 签字时刷新。"""
    r = resolve_prompt_skill(project)
    eff = {"skill_id": r["skill_id"], "mode": r["mode"],
           "resolved_from": r["resolved_from"], "reason": r["reason"]}
    path = project_settings_path(project)
    try:
        saved = json.loads(path.read_text())
    except Exception:
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    block = saved.get("prompt_skill")
    if not isinstance(block, dict):
        block = {"mode": r["mode"], "skill_id": ""}
    cur = block.get("effective") if isinstance(block.get("effective"), dict) else {}
    if {k: cur.get(k) for k in eff} != eff:
        block["effective"] = eff | {"decided_at": datetime.now().isoformat(timespec="seconds")}
        saved["prompt_skill"] = block
        ensure_project(project)
        atomic_write_json(path, saved)
    # 组级覆盖(分镜预览「模型」按钮)的 effective 快照随项目级一起刷新:全局模型/技能变了,
    # 「跟随全局」的组解析结果也变
    try:
        sync_group_settings_effective(project)
    except Exception as e:  # noqa: BLE001
        print(f"[group_settings] 快照刷新失败 {project}:{e}", flush=True)
    return load_project_settings(project)


async def api_prompt_skill_get(project: str):
    project = safe_slug(project)
    cfg = sync_prompt_skill_effective(project)
    return {"project": project, "config": cfg.get("prompt_skill") or {},
            "resolved": resolve_prompt_skill(project),
            "candidates": prompt_skill_candidates()}


async def api_prompt_skill_set(body: dict):
    """视频模型设置弹窗 / H3A 签字弹窗 / 分镜预览页的「提示词技能」下拉:{project, mode, skill_id}。"""
    project = safe_slug((body or {}).get("project"))
    mode = str((body or {}).get("mode") or "auto")
    sid = str((body or {}).get("skill_id") or "")
    await api_projconfig_set({"project": project,
                              "prompt_skill": {"mode": mode, "skill_id": sid if mode == "manual" else ""}})
    out = await api_prompt_skill_get(project)
    HUB.publish({"type": "prompt_skill", "project": project, "resolved": out["resolved"],
                 "config": out["config"]})
    return out


def is_runninghub_video_active(cfg: dict | None = None, agent_id: str = "") -> bool:
    """生效视频渠道是否 ComfyUI 的 RunningHub 运行方式(rh_cn/rh_ai)。
    传 agent_id 时先看「每 Agent 模型配置」的视频渠道覆盖:覆盖为 comfyui ⇒ 按全局
    comfyui 段的运行方式判定;其他非空覆盖 ⇒ 否;空 ⇒ 按全局生效渠道判定。"""
    v = (cfg or load_genconfig()).get("video") or {}
    comfy = v.get("comfyui") or {}
    rh_mode = (comfy.get("mode") or "local") in RH_BASES
    if agent_id:
        ov = str(agent_model_config(agent_id).get("video_provider") or "")
        if ov:
            return ov == "comfyui" and rh_mode
    return (v.get("provider") or "volcengine") == "comfyui" and rh_mode


DEEPAGENTS_OPENROUTER_URL = "https://openrouter.ai/api/v1"
DEEPAGENTS_CLOUD_URL = "https://api.deepseek.com"
DESKTOP_OPENROUTER_WRAPPERS = {
    "https://api.agentics.world/wrapper/openrouter",
    "https://devapi.agentics.world/wrapper/openrouter",
    "https://wrapper.shumati.cn/wrapper/openrouter",
}
AGENTICS_SERVICE_ORIGINS = {
    "https://api.agentics.world",
    "https://devapi.agentics.world",
    "https://api.shumati.cn",
}
OPENROUTER_WRAPPER_API_SUFFIX = "/api/v1"


def resolve_openrouter_connection(api_key: str = "") -> dict:
    """Resolve the explicit user-owned OpenRouter connection."""
    configured = str(api_key or os.environ.get("OPENROUTER_API_KEY") or "").strip()
    return {"base_url": DEEPAGENTS_OPENROUTER_URL,
            "api_key": configured, "uses_wrapper": False}


def resolve_agentics_connection() -> dict:
    """Resolve the signed-in desktop account's Agentics service endpoints.

    These variables are injected only by the desktop shell. Browser deployments
    therefore cannot accidentally opt into account-billed Agentics requests.
    """
    jwt = str(os.environ.get("VIDEOAGENTS_USER_JWT") or "").strip()
    origin = str(os.environ.get("VIDEOAGENTS_SERVICE_API_ORIGIN") or "").strip().rstrip("/")
    if not origin:
        wrapper = str(os.environ.get("VIDEOAGENTS_OPENROUTER_WRAPPER_URL") or "").strip().rstrip("/")
        if wrapper.endswith("/wrapper/openrouter"):
            origin = wrapper[:-len("/wrapper/openrouter")]
    else:
        wrapper = str(os.environ.get("VIDEOAGENTS_OPENROUTER_WRAPPER_URL") or "").strip().rstrip("/")
    if not jwt:
        raise ServiceError(401, "AgenticsLLM requires a signed-in desktop account")
    if origin not in AGENTICS_SERVICE_ORIGINS:
        raise ServiceError(400, "Agentics service origin is unavailable")
    if wrapper not in DESKTOP_OPENROUTER_WRAPPERS:
        raise ServiceError(400, "AgenticsLLM wrapper endpoint is unavailable")
    return {
        "api_origin": origin,
        "base_url": wrapper + OPENROUTER_WRAPPER_API_SUFFIX,
        "api_key": jwt,
    }


def resolve_deepagents(cfg: dict | None = None) -> dict:
    """deepagents 配置 -> 生效渠道的 {provider, base_url, api_key, model}。"""
    da = (cfg or load_genconfig()).get("deepagents") or {}
    if (da.get("provider") or "local") == "agentics":
        a = da.get("agentics") or {}
        connection = resolve_agentics_connection()
        return {"provider": "agentics", **connection,
                "model": a.get("custom_model") or a.get("model") or ""}
    if (da.get("provider") or "local") == "openrouter":
        o = da.get("openrouter") or {}
        connection = resolve_openrouter_connection(o.get("api_key") or "")
        return {"provider": "openrouter", **connection,
                "model": o.get("custom_model") or o.get("model") or ""}
    if (da.get("provider") or "local") == "cloud":
        c = da.get("cloud") or {}
        return {"provider": "cloud",
                "base_url": c.get("base_url") or DEEPAGENTS_CLOUD_URL,
                "api_key": c.get("api_key") or "",
                "model": c.get("model") or c.get("custom_model") or ""}
    lo = da.get("local") or {}
    return {"provider": "local",
            "base_url": lo.get("base_url") or "http://127.0.0.1:1234/v1",
            "api_key": lo.get("api_key") or "lm-studio",
            "model": lo.get("model") or ""}


# ---------------- 项目级设置(输出设置/时长设置/审核设置:每个项目独立) ----------------
# 生成模型/模型策略 为全局配置(genconfig.json/agentmodels.json);
# output/duration/review 落盘 data/projects/<项目>/settings.json,随项目走。
PROJECT_SETTINGS_KEYS = ("output", "duration", "shot_group", "review",
                         "packaging", "versioning", "prompt_skill", "project_skills")


def project_settings_path(project: str) -> Path:
    return PROJECTS_DIR / safe_slug(project) / "settings.json"


def load_project_settings(project: str) -> dict:
    base = {k: DEFAULT_GENCONFIG[k] for k in PROJECT_SETTINGS_KEYS}
    try:
        saved = json.loads(project_settings_path(project).read_text())
    except Exception:
        saved = {}
    return _merge(base, {k: v for k, v in saved.items()
                         if k in PROJECT_SETTINGS_KEYS})


def _validate_duration(d: dict):
    try:
        ep = d.get("episode_minutes", 10)   # 数值 或 "auto"(每集时长由剧本结构自动决定)
        mn = float(d.get("shot_min_s", 1))
        mx = float(d.get("shot_max_s", 10))
        assert (ep == "auto" or float(ep) > 0) and 0 < mn <= mx
        assert isinstance(d.get("long_take", False), bool)
    except (TypeError, ValueError, AssertionError):
        raise ServiceError(400, "Invalid duration settings: episode duration must be > 0 or \"auto\"; shot duration must satisfy 0 < min <= max; long_take must be a boolean") from None


SHOT_GROUP_PRESETS = ("sd20", "sd25", "mmh3")   # 与 index.html SG_PRESETS 同步


def _validate_shot_group(g: dict):
    """视频模型设置:数值范围按当前支持的最强模型口径(Seedance 2.5)封顶。"""
    try:
        gs = float(g.get("max_group_s", 15))
        ni = int(g.get("max_ref_images", 9))
        nv = int(g.get("max_ref_videos", 3))
        na = int(g.get("max_ref_audios", 3))
        assert 4 <= gs <= 30 and 0 <= ni <= 30 and 0 <= nv <= 10 and 0 <= na <= 10
    except (TypeError, ValueError, AssertionError):
        raise ServiceError(400, "Invalid shot_group settings: max_group_s must be 4-30; "
                                "max_ref_images 0-30; max_ref_videos 0-10; max_ref_audios 0-10") from None
    # preset:界面点选的模型预设 key(sd20/sd25/mmh3),仅用于还原按钮高亮——数值相同的预设
    # (Seedance 2.0 与 MiniMax H3)靠它区分;空串/缺省=未点选
    if g.get("preset") not in (None, "", *SHOT_GROUP_PRESETS):
        raise ServiceError(400, f"shot_group.preset must be one of {SHOT_GROUP_PRESETS} or empty")


def _validate_output(o: dict):
    if o.get("aspect_preset") not in (*OUTPUT_ASPECTS, "custom"):
        raise ServiceError(400, f"output.aspect_preset must be one of {(*OUTPUT_ASPECTS, 'custom')}")
    if o.get("aspect_preset") == "custom" and \
            not re.match(r"^\d+\s*:\s*\d+$", o.get("aspect_custom") or ""):
        raise ServiceError(400, "Custom aspect ratio must be width:height, e.g. 21:9")
    if o.get("language") not in OUTPUT_LANGS:
        raise ServiceError(400, f"output.language must be one of {OUTPUT_LANGS}")
    for key in ("draft_resolution", "final_resolution"):
        if o.get(key) and o[key] not in VIDEO_RESOLUTIONS:
            raise ServiceError(400, f"output.{key} must be one of {VIDEO_RESOLUTIONS}")
    if "subtitle_burn_in" in o and not isinstance(o["subtitle_burn_in"], bool):
        raise ServiceError(400, "output.subtitle_burn_in must be a boolean")
    if "caption_enabled" in o and not isinstance(o["caption_enabled"], bool):
        raise ServiceError(400, "output.caption_enabled must be a boolean")
    if "narration_enabled" in o and not isinstance(o["narration_enabled"], bool):
        raise ServiceError(400, "output.narration_enabled must be a boolean")
    if o.get("dialogue_voice") and o["dialogue_voice"] not in DIALOGUE_VOICE_MODES:
        raise ServiceError(400, f"output.dialogue_voice must be one of {DIALOGUE_VOICE_MODES}")
    if "spatial_blocking" in o and not isinstance(o["spatial_blocking"], bool):
        raise ServiceError(400, "output.spatial_blocking must be a boolean")
    if "whitebox_top_video" in o and not isinstance(o["whitebox_top_video"], bool):
        raise ServiceError(400, "output.whitebox_top_video must be a boolean")
    if "platforms" in o:
        pf = o["platforms"]
        if not isinstance(pf, list) or not pf:
            raise ServiceError(400, "output.platforms must be a non-empty array (select at least one platform)")
        bad = [p for p in pf if p not in OUTPUT_PLATFORMS]
        if bad:
            raise ServiceError(400, f"output.platforms contains unknown platform {bad}; valid values: {tuple(OUTPUT_PLATFORMS)}")


def _validate_packaging(p: dict):
    for k in ("intro_enabled", "outro_enabled", "teaser_enabled"):
        if k in p and not isinstance(p[k], bool):
            raise ServiceError(400, f"packaging.{k} must be a boolean")
    for k in ("intro_notes", "outro_notes"):
        if k in p:
            if not isinstance(p[k], str):
                raise ServiceError(400, f"packaging.{k} must be a string")
            if len(p[k]) > 2000:
                raise ServiceError(400, "Intro/outro requirement text too long (max 2000 chars)")


def _validate_versioning(v: dict):
    if "enabled" in v and not isinstance(v["enabled"], bool):
        raise ServiceError(400, "versioning.enabled must be a boolean")


PROMPT_SKILL_MODES = ("auto", "manual", "off")


def _validate_project_skills(value):
    if not isinstance(value, dict) or set(value) - {"overrides"}:
        raise ServiceError(400, "project_skills must contain only overrides")
    overrides = value.get("overrides")
    if not isinstance(overrides, dict) or any(type(v) is not bool for v in overrides.values()):
        raise ServiceError(400, "project_skills.overrides must map skill ids to booleans")
    # 保留已卸载插件的旧配置,但 API 不允许新增未知 id(在提交处验证)。


def _validate_prompt_skill(ps: dict):
    if not isinstance(ps, dict):
        raise ServiceError(400, "prompt_skill must be an object")
    mode = ps.get("mode", "auto")
    if mode not in PROMPT_SKILL_MODES:
        raise ServiceError(400, f"prompt_skill.mode must be one of {PROMPT_SKILL_MODES}")
    sid = str(ps.get("skill_id") or "")
    if mode == "manual":
        if sid not in {c["id"] for c in prompt_skill_candidates()}:
            raise ServiceError(400, f"prompt_skill.skill_id 不是 {PROMPT_AGENT_ID} 已安装的提示词技能: {sid or '(空)'}")
    if "effective" in ps and not isinstance(ps["effective"], dict):
        raise ServiceError(400, "prompt_skill.effective must be an object")


def _validate_review(r: dict):
    for k, v in (r or {}).items():
        if k != "evaluation" and k not in REVIEW_DIMENSIONS:
            raise ServiceError(400, f"Unknown review dimension: {k} (valid: {sorted(REVIEW_DIMENSIONS)} + evaluation)")
        try:
            assert 0 <= int(v) <= 100
        except (TypeError, ValueError, AssertionError):
            label = "质量评委" if k == "evaluation" else REVIEW_DIMENSIONS[k][0]
            raise ServiceError(400, f"Invalid review strength: {label} must be an integer 0-100") from None


# ---------------- Agent 级模型配置(引擎/文字模型/图像/视频渠道) ----------------
# 每个 Agent 可单独指定,优先级:Agent 级配置 > 父运行引擎 > 顶栏全局。
# force/--engine 不得切换执行引擎(报错/返工禁换引擎);同引擎内 --model 仍可覆盖。
# 用户在 UI 保存的覆盖落盘 agentmodels.json;未覆盖时按下方分类默认。
AGENTMODELS_PATH = RUNTIME_DIR / "agentmodels.json"

# 「模型策略」(genconfig.agentmodel_mode,由顶栏「语言模型」下拉驱动:选「智能分配」
# → smart_<引擎>,选具体模型→ global;deepagents 无智能分配,始终 global):
# 每次切换引擎/语言模型都会同时清空 agentmodels.json 里全部 Agent 级单独配置,
# 避免「跟随全局」与「智能分配」/旧手动配置并存冲突。
#   global       全部 Agent 跟随顶栏全局设置
#   smart_claude 按任务复杂度自动选 claude 模型(high→opus 最新版 low→sonnet)
#   smart_codex  按任务复杂度自动选 codex 模型(high→gpt-6-astra low→gpt-5.6-terra)
#   smart_kimi   按任务复杂度自动选 kimi 模型(high→K3 low→K2.7 Coding)
#   smart_pi     按任务复杂度自动选 pi 模型(high→openai-codex/gpt-5.6-sol low→openai-codex/gpt-5.6-terra)
#   smart_deepseek 按任务复杂度自动选 DeepSeek 模型(opencode 引擎,high→V4 Pro low→V4 Flash)
#   smart_grok   按任务复杂度自动选 grok 模型(high→Grok 4.6 low→Grok 4.5)
AM_MODES = ("global", "smart_claude", "smart_codex", "smart_kimi", "smart_pi",
            "smart_deepseek", "smart_grok")

# 任务复杂度分两层:high=创作核心 low=分析/索引/评审/机械活
AM_CATEGORY_TIERS = {
    "00-orchestration": "high", "01-story": "high",
    "03-characters": "high", "05-scenes": "high",
    "06-art": "high", "07-directing": "high",
    "11-qa": "low",
    "02-worldbuilding": "low", "04-creatures": "low",
    "08-video-gen": "low", "09-audio": "low", "10-editing": "low",
    "12-publishing": "low",
}
AM_AGENT_TIERS = {                                      # 分类内的例外
    "00-orchestration/version": "low",                  # 版本快照 = 机械活
    "00-orchestration/evaluation": "low",               # 评分
    "01-story/event": "low",                            # 事件抽取索引
    "01-story/timeline-story": "low",                   # 时间线索引
    "02-worldbuilding/world": "high",                   # 世界观总纲 = 创作核心
    "03-characters/character-manager": "low",           # 角色索引管理
    "06-art/aspect-ratio": "low",                       # 画幅规范 = 机械活
    "08-video-gen/prompt": "high",                      # 生成 prompt 质量决定画面上限
    "08-video-gen/video-generation": "high",            # 视频生成主力
    # audio-to-video 插件:类别 15-audio-video 不在 AM_CATEGORY_TIERS,默认落 low;
    # 其中两个创作型工位需高档(其余三个是测量/算术活,low 即可)
    "15-audio-video/era-researcher": "high",            # 时代考据 = 创作+史料判断
    "15-audio-video/visual-scripter": "high",           # 逐段画面设计 = 创作核心
}
AM_MODE_MODELS = {
    # opus 不锁版本号:CLI 侧别名始终指向最新 opus
    "smart_claude": {"high": {"engine": "claude", "model": "opus"},
                     "low": {"engine": "claude", "model": "sonnet"}},
    "smart_codex": {"high": {"engine": "codex", "model": "gpt-6-astra"},
                    "low": {"engine": "codex", "model": "gpt-5.6-terra"}},
    "smart_kimi": {"high": {"engine": "kimi", "model": "kimi-code/k3"},
                   "low": {"engine": "kimi", "model": "kimi-code/kimi-for-coding"}},
    # pi 引擎经 openai-codex 渠道调用(模型 id 为 pi --list-models 的 provider/model)
    "smart_pi": {"high": {"engine": "pi", "model": "openai-codex/gpt-5.6-sol"},
                 "low": {"engine": "pi", "model": "openai-codex/gpt-5.6-terra"}},
    # DeepSeek 经 opencode 引擎调用,走 OpenCode Go 订阅渠道(opencode-go/ 前缀;
    # Zen 按量渠道为 opencode/ 前缀,动态模型列表 /engines/opencode/models 反映实际可用集)
    "smart_deepseek": {"high": {"engine": "opencode", "model": "opencode-go/deepseek-v4-pro"},
                       "low": {"engine": "opencode", "model": "opencode-go/deepseek-v4-flash"}},
    # grok 引擎(Grok Build CLI,grok.com 登录凭证;模型 id 同 `grok models` 列表)
    "smart_grok": {"high": {"engine": "grok", "model": "grok-4.6"},
                   "low": {"engine": "grok", "model": "grok-4.5"}},
}

AM_ENGINES = ("", "claude", "codex", "kimi", "pi", "opencode", "grok", "deepagents")      # "" = 跟随全局
# RunningHub 不是独立渠道:它是 comfyui 渠道的运行方式(mode=rh_cn/rh_ai,见「🎨 生成模型」页
# ComfyUI 标签页),按 Agent 覆盖只到渠道粒度,运行方式跟随全局 comfyui 段
AM_IMAGE_PROVIDERS = ("", "agentics", "openrouter", "ideogram", "volcengine", "byteplus", "minimax", "comfyui")
AM_VIDEO_PROVIDERS = ("", "agentics", "openrouter", "volcengine", "byteplus", "fal", "minimax", "comfyui")


def default_agent_model(agent_id: str, mode: str | None = None) -> dict:
    """按「模型策略」给出该 Agent 的默认配置;global 模式全部跟随顶栏全局。"""
    if mode is None:
        mode = load_genconfig().get("agentmodel_mode") or "global"
    tier = AM_AGENT_TIERS.get(agent_id) \
        or AM_CATEGORY_TIERS.get(agent_id.split("/")[0]) or "low"
    d = AM_MODE_MODELS.get(mode, {}).get(tier) or {}
    return {"engine": d.get("engine", ""), "model": d.get("model", ""),
            "image_provider": "", "video_provider": ""}


def load_agentmodels() -> dict:
    try:
        return json.loads(AGENTMODELS_PATH.read_text())
    except Exception:
        return {}


def agent_model_config(agent_id: str) -> dict:
    """该 Agent 的生效模型配置:UI 保存的覆盖(整体快照)优先,否则「模型策略」默认。"""
    ov = load_agentmodels().get(agent_id)
    if not isinstance(ov, dict):
        return default_agent_model(agent_id)
    ov = dict(ov)
    # 旧版曾把 RunningHub 列为独立渠道;现已并回 comfyui 渠道的运行方式,存量覆盖等价于 comfyui
    for field in ("image_provider", "video_provider"):
        if ov.get(field) == "runninghub":
            ov[field] = "comfyui"
    return ov


def global_model_pref() -> dict:
    """顶栏全局引擎/模型的服务端副本(经 /api/v1/config/global-model 同步)。
    顶栏选择本体存浏览器 localStorage,服务端自发对话(设置变更通知/看门狗唤醒/
    项目初始化)没有浏览器上下文,靠这份副本跟随顶栏全局。"""
    p = STATE.get("global_model") or {}
    eng = str(p.get("engine") or "").lower()
    model = str(p.get("model") or "").strip()
    # DeepAgents 顶栏的 model 选择器实际存的是渠道(agentics/local/cloud/openrouter)。旧 UI 会把
    # 渠道名写入 global_model，导致它覆盖 genconfig 中的真实模型 ID。
    if eng == "deepagents" and (not model or model in ("agentics", "local", "cloud", "openrouter")):
        try:
            model = resolve_deepagents()["model"]
        except ServiceError:
            model = ""
    return {"engine": eng if eng in ENGINES else "",
            "model": model}


def agent_effective_model(agent_id: str) -> dict:
    """服务端自发对话的生效引擎/模型:Agent 级配置(UI 覆盖或智能策略)优先;
    global 模式(engine 为空=跟随全局)回退顶栏全局的服务端副本;仍取不到才回退 claude。"""
    am = agent_model_config(agent_id)
    if am.get("engine"):
        return {"engine": am["engine"], "model": am.get("model") or ""}
    gp = global_model_pref()
    return {"engine": gp["engine"] or "claude", "model": gp["model"]}


# 各引擎对应的智能分配策略(deepagents 无智能分配,不在表内→global)
SMART_MODE_BY_ENGINE = {"claude": "smart_claude", "codex": "smart_codex",
                        "kimi": "smart_kimi", "pi": "smart_pi",
                        "opencode": "smart_deepseek", "grok": "smart_grok"}


def migrate_agent_memory_default():
    """对话记忆额度的一次性迁移(启动时调用):旧版(≤v1.0.20,记忆还是布尔开关,
    state.json 无 agent_memory_kb 键)升级上来的存量安装,不论旧开关开/关,
    统一重置为缺省额度 AGENT_MEMORY_KB_DEFAULT 并移除旧布尔键;
    滑块设过值(键已存在)的安装不受影响。"""
    if "agent_memory_kb" in STATE:
        return
    STATE["agent_memory_kb"] = AGENT_MEMORY_KB_DEFAULT
    STATE.pop("agent_memory", None)
    save_state(STATE)


def migrate_agentmodel_smart_default():
    """顶栏「语言模型」默认智能分配的一次性迁移(启动时调用):
    旧版(≤v1.0.21,「模型策略」还是设置菜单子菜单)升级上来的存量安装,
    按当前全局引擎自动切到对应智能分配(deepagents→跟随全局),并清空全部
    Agent 级单独配置,让各 Agent 立即按新策略生效;此后策略只随顶栏切换变化。"""
    if STATE.get("am_smart_migrated"):
        return
    eng = str((STATE.get("global_model") or {}).get("engine")
              or (STATE.get("ui_prefs") or {}).get("engine") or "claude").lower()
    mode = SMART_MODE_BY_ENGINE.get(eng, "global")
    cfg = load_genconfig()
    if cfg.get("agentmodel_mode") != mode:
        cfg["agentmodel_mode"] = mode
        save_genconfig(cfg)
    atomic_write_json(AGENTMODELS_PATH, {})     # 覆盖旧版遗留的 Agent 级手动配置
    STATE["am_smart_migrated"] = True
    save_state(STATE)


# macOS 系统代理(如 wsm)会连 127.0.0.1 一起劫持导致 503;
# 本机服务(ComfyUI/LM Studio)强制直连,外网 URL 维持默认代理行为。
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _http_get_json(url: str, headers: dict | None = None, timeout: int = 20):
    """阻塞式 HTTP GET(在线程里跑),返回解析后的 JSON。"""
    req = urllib.request.Request(url, headers=headers or {})
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    opener = _DIRECT_OPENER.open if host in _LOOPBACK_HOSTS else urllib.request.urlopen
    with opener(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return json.loads(body.decode("utf-8", "replace"))


def _http_post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 20):
    """阻塞式 HTTP POST JSON(在线程里跑),返回解析后的 JSON。"""
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    opener = _DIRECT_OPENER.open if host in _LOOPBACK_HOSTS else urllib.request.urlopen
    with opener(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


_OPENROUTER_CACHE: dict = {}          # modality -> (ts, models)
_OPENROUTER_TTL = 600


# ---------------- 引擎会话用量探测(watchdog 阈值门控 + 资源消耗面板,参考 CodexBar) ----------------
# 显示规则:设置里手动开启的引擎始终显示;未手动开启的随顶栏当前引擎自动显示
# (USAGE_AUTO_BY_ENGINE:pi 计入 codex,deepagents 无)。
# codex:本地 ~/.codex/sessions/**/*.jsonl 会记录 rate_limits(primary=5h 窗口,secondary=周窗口)。
# claude:本地无用量文件,走 OAuth 探针 GET /api/oauth/usage 取 five_hour/seven_day.utilization;
#   token 读取顺序 env CLAUDE_CODE_OAUTH_TOKEN → macOS Keychain → ~/.claude/.credentials.json,
#   探测失败/token 过期一律返回 None(未知即放行,不冻结流水线)。
# kimi:官方用量接口 GET api.kimi.com/coding/v1/usages(Key 在 ⚙️ 资源消耗 设置里配,
#   留空自动读本机 kimi CLI 登录凭证),usage=周配额,limits[](300min 窗口)=5h 会话配额。
# opencode:OpenCode Go 订阅用量 GET opencode.ai/zen/go/v1/usage(额度按美元计,
#   5 小时/周/月三窗口,面板取 5h→Session、周→Weekly);Key 在设置里配,留空自动读
#   本机 opencode 登录凭证 auth.json。
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KIMI_USAGE_URL = "https://api.kimi.com/coding/v1/usages"
OPENCODE_USAGE_URL = "https://opencode.ai/zen/go/v1/usage"
_USAGE_CACHE: dict = {}               # engine -> (ts, {"session":pct|None,"weekly":pct|None})
_USAGE_TTL = {"claude": 180, "codex": 180, "kimi": 180, "opencode": 180}   # claude 探针接口限流激进,≥180s 才安全;codex 探针要 rglob 扫 sessions 目录(秒级),TTL 太短资源面板每开必冷探
_CLAUDE_VERSION: str | None = None


def resource_cfg() -> dict:
    """设置 → 资源消耗的持久化配置(data/.videoagents/state.json)。"""
    return STATE.get("resources") or {}


def _parse_reset_ts(v):
    """重置时间统一为 epoch 秒:接受 ISO 字符串 / epoch 秒 / epoch 毫秒,解析不了返回 None。
    小数秒归一为 6 位:kimi 返回 9 位纳秒(如 …13.716839300Z),而 Python 3.10 的
    fromisoformat 只认 3/6 位,不归一会解析失败。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and v > 0:
        return float(v) / 1000 if v > 1e12 else float(v)
    if isinstance(v, str) and v.strip():
        s = v.strip().replace("Z", "+00:00")
        s = re.sub(r"\.(\d+)", lambda m: "." + m.group(1)[:6].ljust(6, "0"), s, count=1)
        try:
            return datetime.fromisoformat(s).timestamp()
        except ValueError:
            return None
    return None


def _find_rate_limits(d):
    """在 codex session 事件 JSON 里递归找非空 rate_limits 对象。"""
    if isinstance(d, dict):
        rl = d.get("rate_limits")
        if isinstance(rl, dict) and isinstance(rl.get("primary"), dict):
            return rl
        for v in d.values():
            r = _find_rate_limits(v)
            if r:
                return r
    elif isinstance(d, list):
        for v in d:
            r = _find_rate_limits(v)
            if r:
                return r
    return None


def _codex_usage_full() -> dict:
    """最近 codex session 里最后一次 rate_limits:primary=5h 会话,secondary=周窗口。

    读数是被动扒 session 文件的,codex 不跑就不会有新读数;若最新读数所在文件
    距今已超 5h(会话窗口必然已滚过),旧百分比只会误导,session 视为过期返回 None。
    重置时间:窗口对象带 resets_at 直接用;只有 resets_in_seconds 则以事件时间戳
    (缺失退回文件 mtime)为基准换算,已过期的重置时间不再返回。
    """
    empty = {"session": None, "weekly": None,
             "session_resets_at": None, "weekly_resets_at": None}
    try:
        files = sorted(CODEX_SESSIONS_DIR.rglob("*.jsonl"),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:8]
    except OSError:
        return empty
    for f in files:                          # 新→旧,取第一个有读数的文件里最后一条
        last, last_ts = None, 0.0
        try:
            mtime = f.stat().st_mtime
            with open(f, errors="replace") as fh:
                for line in fh:
                    if '"rate_limits"' not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    rl = _find_rate_limits(obj)
                    if rl and isinstance(rl["primary"].get("used_percent"), (int, float)):
                        last = rl
                        last_ts = _parse_reset_ts(obj.get("timestamp")) or mtime
        except OSError:
            continue
        if last is not None:
            sec = last.get("secondary") or {}
            weekly = sec.get("used_percent")
            stale = time.time() - mtime > 5 * 3600

            def reset_at(win):
                at = _parse_reset_ts(win.get("resets_at"))
                if at is None and isinstance(win.get("resets_in_seconds"), (int, float)):
                    at = last_ts + float(win["resets_in_seconds"])
                return at if at and at > time.time() else None
            return {"session": None if stale else float(last["primary"]["used_percent"]),
                    "weekly": float(weekly) if isinstance(weekly, (int, float)) else None,
                    "session_resets_at": None if stale else reset_at(last["primary"]),
                    "weekly_resets_at": reset_at(sec)}
    return empty


def _claude_oauth_token() -> str | None:
    """Claude Code 的 OAuth access token;过期或读不到返回 None(不做 refresh)。"""
    if not claude_probe_enabled():
        return None
    env = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if env:
        return env
    raw = None
    try:                                     # macOS Keychain(首次访问需在弹窗点「始终允许」)
        r = subprocess.run(["security", "find-generic-password",
                            "-s", "Claude Code-credentials", "-w"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            raw = r.stdout.strip()
    except Exception:
        pass
    if raw is None:
        try:                                 # Linux/Windows 落盘文件
            raw = (Path.home() / ".claude" / ".credentials.json").read_text()
        except OSError:
            return None
    try:
        oauth = json.loads(raw).get("claudeAiOauth") or {}
        exp = oauth.get("expiresAt")         # epoch 毫秒
        if exp and time.time() * 1000 >= float(exp):
            return None
        return oauth.get("accessToken") or None
    except Exception:
        return None


def _claude_version() -> str:
    """claude CLI 版本(User-Agent 用,错 UA 会撞激进限流桶);探测一次缓存。"""
    global _CLAUDE_VERSION
    if _CLAUDE_VERSION is None:
        _CLAUDE_VERSION = "2.1.0"            # 探测失败的回退值
        try:
            executable = resolve_cli_executable("claude")
            if not executable:
                return _CLAUDE_VERSION
            r = subprocess.run([executable, "--version"],
                               capture_output=True, text=True, timeout=10)
            m = re.search(r"(\d+\.\d+\.\d+)", r.stdout or "")
            if m:
                _CLAUDE_VERSION = m.group(1)
        except Exception:
            pass
    return _CLAUDE_VERSION


# 顶栏当前引擎自动开启的用量检查(手动开关之外的自动显示):pi→codex,因 pi 通常
# 配 ChatGPT(openai-codex)订阅渠道,消耗的正是 Codex 配额;deepagents 无用量口径。
USAGE_AUTO_BY_ENGINE = {"claude": "claude", "codex": "codex", "kimi": "kimi",
                        "opencode": "opencode", "pi": "codex"}


def usage_auto_engine() -> str | None:
    """随顶栏全局引擎自动显示用量的目标引擎。生效规则:设置里手动开启的始终显示;
    未手动开启的按当前引擎是否选中自动显示(*_probe_enabled = 手动 or 自动)。"""
    return USAGE_AUTO_BY_ENGINE.get(global_model_pref()["engine"])


def claude_probe_manual() -> bool:
    """Claude 用量探针手动开关:env VIDEOAGENTS_ENABLE_CLAUDE_USAGE_PROBE 或
    ⚙️ 资源消耗 设置(设置弹窗回显用,不含随引擎的自动开启)。"""
    return CLAUDE_USAGE_PROBE_ENABLED or bool(resource_cfg().get("claude_probe"))


def claude_probe_enabled() -> bool:
    return claude_probe_manual() or usage_auto_engine() == "claude"


def codex_probe_manual() -> bool:
    """Codex 用量检查手动开关(⚙️ 资源消耗 设置);默认关闭,不为未用 Codex 的
    用户白扫 sessions 目录——顶栏选中 codex/pi 时另行自动开启。"""
    return bool(resource_cfg().get("codex_probe"))


def codex_probe_enabled() -> bool:
    return codex_probe_manual() or usage_auto_engine() == "codex"


def kimi_probe_manual() -> bool:
    """KimiCode 用量检查手动开关(⚙️ 资源消耗 设置);历史无此键时按是否已配 Key
    判定(老配置只填了 Key 没有开关,升级后面板行为不变)。"""
    cfg = resource_cfg()
    v = cfg.get("kimi_probe")
    if v is None:
        return bool((cfg.get("kimi_api_key") or "").strip())
    return bool(v)


def kimi_probe_enabled() -> bool:
    return kimi_probe_manual() or usage_auto_engine() == "kimi"


def opencode_probe_manual() -> bool:
    """OpenCode Go 订阅用量检查手动开关(⚙️ 资源消耗 设置);默认关闭。"""
    return bool(resource_cfg().get("opencode_probe"))


def opencode_probe_enabled() -> bool:
    return opencode_probe_manual() or usage_auto_engine() == "opencode"


def _opencode_data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "opencode"


def _opencode_cache_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "opencode"


def _opencode_state_dir() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "opencode"


def _dir_writable(path: Path) -> bool:
    """path(或其最近的已存在祖先)对当前用户可写。已存在的目录还要求其内文件可写:
    root 建的目录常见「目录可进但文件不可覆盖」,而 opencode 刷新注册表是整文件重写。"""
    probe = path
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            return False
        probe = parent
    if not os.access(probe, os.W_OK):
        return False
    if probe == path and path.is_dir():
        return all(os.access(f, os.W_OK) for f in path.iterdir() if f.is_file())
    return True


_OPENCODE_CACHE_WARNED = False


def opencode_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """opencode 子进程环境:统一 NO_COLOR/禁自动更新;缓存目录(models.json 快照)或
    state 目录(刷新注册表前要在 <state>/opencode/locks 建锁)不可写时——典型:曾用 sudo
    跑过 opencode,这些目录归 root——把对应 XDG_*_HOME 指到运行时目录。否则 opencode
    「Failed to fetch models.dev: EACCES」后永远用旧快照,新上线模型在 `opencode models`
    不列、`run -m` 一律报语义无关的「UnknownError: Unexpected server error」。"""
    global _OPENCODE_CACHE_WARNED
    env = {**(os.environ if base is None else base),
           "NO_COLOR": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1"}
    broken = []
    for var, path, sub in (("XDG_CACHE_HOME", _opencode_cache_dir(), "opencode-cache"),
                           ("XDG_STATE_HOME", _opencode_state_dir() / "locks", "opencode-state")):
        if _dir_writable(path):
            continue
        fallback = RUNTIME_DIR / sub
        fallback.mkdir(parents=True, exist_ok=True)
        env[var] = str(fallback)
        broken.append(str(path))
    if broken and not _OPENCODE_CACHE_WARNED:
        _OPENCODE_CACHE_WARNED = True
        print(f"[opencode] {' 与 '.join(broken)} 不可写(属主多为 root),模型注册表无法刷新;"
              f"已改道到 {RUNTIME_DIR}。根治:sudo chown -R $USER ~/.cache ~/.local/state/opencode",
              flush=True)
    return env


_OPENCODE_SNAPSHOT_MAX_AGE = 24 * 3600


def opencode_models_stale(env: dict[str, str]) -> bool:
    """models.json 快照缺失或超过一天:`opencode models` 自身的后台刷新不可靠(实测锁目录
    可写后连跑数次仍沿用 11 天前的快照),须显式 --refresh 才能看到新上线模型。"""
    snapshot = Path(env.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "opencode" / "models.json"
    try:
        return time.time() - snapshot.stat().st_mtime > _OPENCODE_SNAPSHOT_MAX_AGE
    except OSError:
        return True


def _opencode_go_key() -> str | None:
    """OpenCode Go API Key:设置里配置的优先;留空时读本机 opencode 登录凭证
    (auth.json 里 provider 名含 opencode 的条目,常见字段名逐个尝试)。"""
    key = (resource_cfg().get("opencode_api_key") or "").strip()
    if key:
        return key
    try:
        auth = json.loads((_opencode_data_dir() / "auth.json").read_text())
    except Exception:
        return None
    if not isinstance(auth, dict):
        return None
    for name, ent in auth.items():
        if "opencode" in str(name).lower() and isinstance(ent, dict):
            for k in ("key", "apiKey", "api_key", "token", "access"):
                v = ent.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return None


def _opencode_usage_full() -> dict:
    """OpenCode Go 订阅用量:5h 窗口→session,周窗口→weekly(月窗口不上面板)。
    官方未公开该接口的响应字段(控制台同源接口,无 Key 时 401 AuthError),按常见
    命名宽松解析:utilization/percent 直读,used+limit(美元额度)换算;窗口名与
    重置时间字段逐个尝试,解析不到/未配 Key 一律返回 None(面板显示未知)。"""
    empty = {"session": None, "weekly": None,
             "session_resets_at": None, "weekly_resets_at": None}
    key = _opencode_go_key()
    if not key:
        return empty
    try:
        data = _http_get_json(OPENCODE_USAGE_URL,
                              headers={"Authorization": f"Bearer {key}"})
    except Exception:
        return empty

    def pct(d):
        if not isinstance(d, dict):
            return None
        for k in ("utilization", "used_percent", "percent", "percentage"):
            v = d.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return max(0.0, min(100.0, float(v)))
        used = next((float(d[k]) for k in ("used", "usage", "spent", "cost")
                     if isinstance(d.get(k), (int, float))), None)
        limit = next((float(d[k]) for k in ("limit", "quota", "total", "cap")
                      if isinstance(d.get(k), (int, float)) and float(d[k]) > 0),
                     None)
        if used is not None and limit:
            return max(0.0, min(100.0, used / limit * 100))
        return None

    def reset_ts(d):
        if not isinstance(d, dict):
            return None
        for k in ("resets_at", "resetAt", "reset_at", "resetTime", "reset_time",
                  "resets_in", "expires_at", "end_time", "endsAt"):
            ts = _parse_reset_ts(d.get(k))
            if ts and ts > time.time():
                return ts
        return None

    def window(*names):
        scopes = [data] + [data.get(k) for k in ("usage", "limits", "windows", "data")]
        for scope in scopes:
            if not isinstance(scope, dict):
                continue
            for n in names:
                d = scope.get(n)
                if isinstance(d, dict) and (pct(d) is not None or reset_ts(d)):
                    return d
        return None
    s = window("five_hour", "fiveHour", "5h", "session", "hour")
    w = window("seven_day", "sevenDay", "week", "weekly", "7d")
    return {"session": pct(s), "weekly": pct(w),
            "session_resets_at": reset_ts(s), "weekly_resets_at": reset_ts(w)}


def _claude_usage_full() -> dict:
    """OAuth 探针取 claude 用量:five_hour=会话,seven_day=周;窗口对象自带
    resets_at(ISO)即重置时间;任何异常返回 None。"""
    empty = {"session": None, "weekly": None,
             "session_resets_at": None, "weekly_resets_at": None}
    token = _claude_oauth_token()
    if not token:
        return empty
    try:
        data = _http_get_json(CLAUDE_USAGE_URL, headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": f"claude-code/{_claude_version()}",
            "Content-Type": "application/json",
        })

        def win(name):
            d = data.get(name) or {}
            v = d.get("utilization")
            return (float(v) if isinstance(v, (int, float)) else None,
                    _parse_reset_ts(d.get("resets_at")))
        s, sr = win("five_hour")
        w, wr = win("seven_day")
        return {"session": s, "weekly": w,
                "session_resets_at": sr, "weekly_resets_at": wr}
    except Exception:
        return empty


def _kimi_usage_key() -> str | None:
    """Kimi 用量接口凭证:设置里手填的 Key 优先;留空读本机 kimi CLI 登录凭证的
    access token(usages 接口同样接受,参考 CodexBar docs/kimi.md),再退环境变量。
    凭证字段名按常见命名宽松扫描(顶层与一层嵌套);带过期时间且已过期的跳过。
    不动 refresh token 也不回写凭证文件(避免与 CLI 抢刷新):token 过期后面板
    显示未知,重新 kimi login 即恢复——与 claude 探针「过期不刷新」策略一致。"""
    key = (resource_cfg().get("kimi_api_key") or "").strip()
    if key:
        return key
    for base in (Path.home() / ".kimi-code", Path.home() / ".kimi"):   # 新/旧版数据目录
        try:
            cred = json.loads((base / "credentials" / "kimi-code.json").read_text())
        except Exception:
            continue
        if not isinstance(cred, dict):
            continue
        for d in [cred] + [v for v in cred.values() if isinstance(v, dict)]:
            exp = next((_parse_reset_ts(d[k]) for k in
                        ("expires_at", "expiresAt", "expiry", "expire_at", "expireAt")
                        if d.get(k) is not None), None)
            if exp and exp <= time.time():
                continue
            for k in ("access_token", "accessToken", "api_key", "apiKey",
                      "token", "key"):
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    for env in ("KIMI_CODE_API_KEY", "KIMI_API_KEY", "MOONSHOT_API_KEY"):
        v = (os.environ.get(env) or "").strip()
        if v:
            return v
    return None


def _kimi_usage_full() -> dict:
    """Kimi Code 官方用量接口:usage=周配额,limits[](300min 窗口)=5h 会话配额。
    重置时间实测字段名为驼峰 resetTime(ISO 带 9 位小数秒,参考 CodexBar docs/kimi.md),
    另按常见命名兼容扫描;取不到为 None。
    无凭证/接口异常返回 None(资源消耗面板显示未知)。"""
    empty = {"session": None, "weekly": None,
             "session_resets_at": None, "weekly_resets_at": None}
    key = _kimi_usage_key()
    if not key:
        return empty
    try:
        data = _http_get_json(KIMI_USAGE_URL, headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        })

        def pct(d):
            try:
                limit, used = float(d.get("limit")), float(d.get("used"))
                return max(0.0, min(100.0, used / limit * 100)) if limit > 0 else None
            except (TypeError, ValueError):
                return None

        def reset_ts(*dicts):
            for d in dicts:
                for k in ("resetTime", "resetAt", "resets_at", "reset_at",
                          "reset_time", "resets_time", "end_time", "expires_at"):
                    ts = _parse_reset_ts(d.get(k))
                    if ts and ts > time.time():
                        return ts
            return None
        session, session_reset = None, None
        for it in data.get("limits") or []:
            detail = it.get("detail") or {}
            if pct(detail) is not None:
                session = pct(detail)
                session_reset = reset_ts(detail, it)
                break
        usage = data.get("usage") or {}
        return {"session": session, "weekly": pct(usage),
                "session_resets_at": session_reset,
                "weekly_resets_at": reset_ts(usage)}
    except Exception:
        return empty


def engine_usage_full(engine: str) -> dict:
    """该引擎 {session: 5h 窗口已用%, weekly: 周窗口已用%, *_resets_at: 窗口重置
    epoch 秒};取不到为 None。带 TTL 缓存。"""
    if engine not in _USAGE_TTL:
        return {"session": None, "weekly": None,
                "session_resets_at": None, "weekly_resets_at": None}
    ts, full = _USAGE_CACHE.get(engine, (0, None))
    if full is not None and time.time() - ts < _USAGE_TTL[engine]:
        return full
    full = {"claude": _claude_usage_full, "codex": _codex_usage_full,
            "kimi": _kimi_usage_full, "opencode": _opencode_usage_full}[engine]()
    _USAGE_CACHE[engine] = (time.time(), full)
    return full


def engine_usage_percent(engine: str) -> float | None:
    """该引擎当前会话(5h 窗口)已用百分比;不支持的引擎/取不到返回 None(watchdog 门控用)。"""
    return engine_usage_full(engine).get("session")


# ---------------- 账户余额探测(资源消耗面板) ----------------
_BALANCE_CACHE: dict = {}             # provider -> (ts, result|None)
_BALANCE_TTL = 600


def _openrouter_balance() -> dict | None:
    """OpenRouter 账户余额:GET /api/v1/credits(Management/Provisioning Key),
    余额 = total_credits - total_usage(USD)。未配 Key/异常返回 None。"""
    key = (resource_cfg().get("openrouter_key") or "").strip()
    if not key:
        return None
    try:
        d = (_http_get_json("https://openrouter.ai/api/v1/credits",
                            headers={"Authorization": f"Bearer {key}"})).get("data") or {}
        credits, usage = float(d.get("total_credits")), float(d.get("total_usage"))
        return {"balance": round(credits - usage, 4), "currency": "USD"}
    except Exception:
        return None


def _volc_signed_call(ak: str, sk: str, action: str, version: str,
                      body: dict | None = None,
                      service: str = "billing", region: str = "cn-north-1",
                      host: str = "open.volcengineapi.com") -> dict:
    """火山引擎 OpenAPI 调用(HMAC-SHA256 签名,同官方 SDK Signer);
    body 为 None 走 GET,否则 POST JSON。"""
    method = "GET" if body is None else "POST"
    payload = b"" if body is None else json.dumps(body).encode()
    query = urllib.parse.urlencode(sorted({"Action": action, "Version": version}.items()))
    xdate = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    payload_hash = hashlib.sha256(payload).hexdigest()
    headers = {"host": host, "x-date": xdate, "x-content-sha256": payload_hash}
    if body is not None:
        headers["content-type"] = "application/json; charset=utf-8"
    signed = ";".join(sorted(headers))
    canon = "\n".join([method, "/", query,
                       "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)),
                       signed, payload_hash])
    scope = f"{xdate[:8]}/{region}/{service}/request"
    sts = "\n".join(["HMAC-SHA256", xdate, scope,
                     hashlib.sha256(canon.encode()).hexdigest()])

    def h(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()
    k_sign = h(h(h(h(sk.encode(), xdate[:8]), region), service), "request")
    sig = hmac.new(k_sign, sts.encode(), hashlib.sha256).hexdigest()
    req_headers = {
        "Authorization": (f"HMAC-SHA256 Credential={ak}/{scope}, "
                          f"SignedHeaders={signed}, Signature={sig}"),
        "X-Date": xdate, "X-Content-Sha256": payload_hash,
    }
    if body is None:
        return _http_get_json(f"https://{host}/?{query}", headers=req_headers)
    req_headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(f"https://{host}/?{query}", data=payload,
                                 headers=req_headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _volc_balance() -> dict | None:
    """火山引擎账户可用余额(QueryBalanceAcct,CNY);未开启/未配 AK/SK/异常返回 None。"""
    cfg = resource_cfg()
    ak, sk = (cfg.get("volc_ak") or "").strip(), (cfg.get("volc_sk") or "").strip()
    if not (cfg.get("volc_enabled") and ak and sk):
        return None
    try:
        r = _volc_signed_call(ak, sk, "QueryBalanceAcct", "2022-01-01").get("Result") or {}
        return {"balance": float(r.get("AvailableBalance")), "currency": "CNY"}
    except Exception:
        return None


def _rh_balance() -> dict | None:
    """RunningHub 账户余额(accountStatus):RH 币 + 钱包余额;.ai/.cn 账号不互通,
    按站点分别查询,只查填了 Key 的站点。未开启/一个 Key 都没配返回 None;
    单站点查询失败该站点读数为 null(面板显示「未知」),不影响另一站点。"""
    cfg = resource_cfg()
    keys = {s: (cfg.get(f"rh_key_{s}") or "").strip() for s in ("ai", "cn")}
    if not (cfg.get("rh_enabled") and any(keys.values())):
        return None

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    out = {}
    for site, key in keys.items():
        if not key:
            continue
        try:
            resp = _http_post_json(
                RH_BASES[f"rh_{site}"] + "/uc/openapi/accountStatus",
                {"apikey": key, "apiKey": key},
                {"Authorization": f"Bearer {key}"}, 15)
            if resp.get("code") != 0:
                raise ValueError(str(resp.get("msg")))
            d = resp.get("data") or {}
            out[site] = {"coins": num(d.get("remainCoins")),
                         "balance": num(d.get("remainMoney")),
                         "currency": str(d.get("currency") or "")}
        except Exception:
            out[site] = {"coins": None, "balance": None, "currency": ""}
    return out


def provider_balance(provider: str) -> dict | None:
    """openrouter/volc/runninghub 账户余额,带 TTL 缓存;未配置/取不到返回 None。"""
    fn = {"openrouter": _openrouter_balance, "volc": _volc_balance,
          "runninghub": _rh_balance}.get(provider)
    if not fn:
        return None
    ts, res = _BALANCE_CACHE.get(provider, (0, None))
    if time.time() - ts < _BALANCE_TTL:
        return res
    res = fn()
    _BALANCE_CACHE[provider] = (time.time(), res)
    return res


class Hub:
    """SSE 广播中心。"""

    def __init__(self):
        self.clients: list[asyncio.Queue] = []

    def publish(self, ev: dict):
        for q in list(self.clients):
            try:
                q.put_nowait(ev)
            except Exception:
                pass

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue()
        self.clients.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self.clients:
            self.clients.remove(q)


HUB = Hub()

# ---------------- Agent 目录 ----------------


_AGENTS_CACHE: tuple[float, list] = (0.0, [])   # (过期时刻, 数据);目录基本静态,TTL 足矣
_AGENTS_CACHE_TTL = 30.0


def list_agents(refresh: bool = False) -> list[dict]:
    global _AGENTS_CACHE
    if not refresh and time.time() < _AGENTS_CACHE[0]:
        return _AGENTS_CACHE[1]
    if refresh:
        list_plugins(refresh=True)
    cats = all_category_names()
    agents = []

    def _add(soul: Path, aid: str, plugin: str | None):
        title, tagline = aid.split("/")[-1], ""
        try:
            for line in soul.read_text().splitlines()[:8]:
                m = re.match(r"^#\s*SOUL\.md\s*[—\-]+\s*(.+)$", line.strip())
                if m:
                    title = m.group(1).strip()
                elif line.strip().startswith(">") and not tagline:
                    tagline = line.strip().lstrip("> ").strip()
        except Exception:
            pass
        cat = aid.split("/")[0]
        agents.append({
            "id": aid,
            "name": title,
            "tagline": tagline,
            "category": cat,
            "category_name": cats.get(cat, cat),
            "dispatcher": is_dispatcher_agent(aid),
            "plugin": plugin,
        })

    for cat_dir in sorted(AGENTS_DIR.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name not in CATEGORY_NAMES:
            continue
        for a_dir in sorted(cat_dir.iterdir()):
            if (a_dir / "SOUL.md").is_file():
                _add(a_dir / "SOUL.md", f"{cat_dir.name}/{a_dir.name}", None)
    for p in active_plugins():
        for a in p["agents"]:
            _add(Path(p["path"]) / "agents" / a["id"] / "SOUL.md", a["id"], p["name"])
    agents.sort(key=lambda a: a["id"])           # 插件 Agent 按类别编号归入既有分组顺序
    _AGENTS_CACHE = (time.time() + _AGENTS_CACHE_TTL, agents)
    return agents


def chat_path(agent_id: str, project: str) -> Path:
    """对话记录按项目隔离:chats/<project>/<agent>.jsonl。"""
    d = CHATS_DIR / safe_slug(project)
    d.mkdir(parents=True, exist_ok=True)
    return d / (agent_id.replace("/", "__") + ".jsonl")


def _clear_agent_sessions(agent_id: str, project: str) -> list[str]:
    """清掉该 Agent 在项目下全部引擎的会话记录,返回清掉的引擎列表。
    引擎侧的历史会话文件留在原处,仅解除续接;下一条消息即开全新会话。
    (用途:/clear 命令、签字过门后的自动瘦身——防会话随项目推进无限膨胀。)"""
    cleared = [eng for eng in ENGINES
               if STATE["sessions"].pop(f"{eng}::{agent_id}::{project}", None)]
    if cleared:
        save_state(STATE)
    return cleared


def append_chat(agent_id: str, project: str, entry: dict):
    entry["ts"] = time.time()
    with chat_path(agent_id, project).open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    HUB.publish({"type": "chat", "agent": agent_id, "project": project, **entry})

# ---------------- 提示词 ----------------


def _fmt_num(x) -> str:
    """4.0 → 4;7.5 → 7.5"""
    f = float(x)
    return str(int(f)) if f == int(f) else str(f)


def project_prompt_path(project: str) -> str:
    """Return the persistent project path used in CLI-agent instructions."""
    return (PROJECTS_DIR / safe_slug(project)).resolve().as_posix()


def build_role_prompt(agent_id: str, project: str) -> str:
    soul = ((agent_dir(agent_id) or AGENTS_DIR / agent_id) / "SOUL.md").read_text(
        encoding="utf-8"
    )
    # Dispatched agents run with ROOT as their cwd so they can access bundled
    # code and workflow files. A relative data/projects path would therefore
    # write into the installation directory instead of VIDEOAGENTS_DATA_DIR.
    proj_rel = project_prompt_path(project)
    ps = load_project_settings(project)   # 输出/时长为项目级设置
    aspect, aspect_name, out_lang = resolve_output(ps)
    out = ps.get("output") or {}
    draft_res = out.get("draft_resolution") or "480p"
    final_res = out.get("final_resolution") or "480p"
    burn_in = (
        "**开启** —— 成片终稿必须内嵌字幕:subtitle 产出 subtitles.srt(正片 0 秒基准)后,由 edit 在封装终版时"
        "先用宿主 CLI `code/finalize_episode.py shift`(或 assemble 自动跑)按片头实测时长整体平移生成成片基准"
        " subtitles_final.srt,再把**平移后的字幕**烧录进画面(ffmpeg subtitles 滤镜等),烧录后 `finalize_episode.py check`"
        " 须全 PASS——final.mp4 含片头时严禁直接烧正片基准 SRT,否则字幕整体偏早;"
        "烧录样式严格按 subtitle SOUL.md 的烧录样式规范"
        "(小字号贴底、≤2 行、白字黑描边、禁大面积底板);orchestrator 排期须把该烧录步骤纳入本集必做项,"
        "platform-adapter 发布物料一律基于烧录版母版"
        if out.get("subtitle_burn_in") else
        "关闭(默认)—— 成片不烧录字幕,字幕仅以外挂形式交付:final.mp4 含片头时交付 edit 用 `code/finalize_episode.py shift`"
        " 平移后的成片基准 subtitles_final.srt(严禁把正片 0 秒基准的 subtitles.srt 直接配 final.mp4,"
        "`finalize_episode.py check` 全 PASS 才可交付),发布期按平台字幕清单处理")
    caption_line = (
        "**开启** —— caption Agent 在关键叙事节点设计花字+配套音效(WORKFLOW.md §9A),"
        "超分后的终版组 clip 上烧录副本(assets/clips_caption/,原 clip 不动),"
        "另封装花字版成片 edit/{ep}/final_caption.mp4(a:0=声轨+SFX 预混、a:1=声轨存档);"
        "干净版 final.mp4 照常产出,双版本并列;渲染/封装只准宿主 CLI code/render_captions.py,"
        "机检 code/check_captions.py 三阶段"
        if out.get("caption_enabled") else
        "关闭(默认)—— 不设计、不烧录花字,caption 相关节点(p9-caption*/av2-caption/av4-caption*)"
        "一律不派发、不建卡,闸门不因未派发而 HOLD;caption Agent 被派到也只说明开关已关闭并结单")
    spatial_on = out.get("spatial_blocking", True) is not False
    spatial_line = (
        "**开启(默认)—— 用白模摄影机视角视频给视频生成定位人物(2026-09-07 起该开关的含义),配套场景布局包 + 组级人物动线数据流程**:Phase 4 environment-concept 每场景出俯视空间布局图 "
        "`layout_top.png` + 9 宫格多角度图 `grid_9views.png` + `layout.json`(机检 scene_layout_pack_ok,§6A 按此判缺口);"
        "Phase 6 storyboard 每组写 `scene_refs`+`blocking_map`(逐角色起点/动线/终点引地标 + `route_en`)、每镜 `view_tile`,"
        "shot-planning 继承(每角色 `label` 收口为短规范名、全集同角色同词,机检 label_ok)并跑宿主 CLI `code/blocking_map_check.py` 机检"
        "(blocking_map_present;**2026-09-07 起不再渲染 `directing/epNN/blocking_maps/grpNNN.png` 动线标注图**——人物在场景中的空间位置与动线由 3D 白模参考视频承担,"
        "禁止自绘动线图或复制/改写宿主脚本),blocking 每镜站位落在组级动线上(blocking_on_map,站位片段=场景地标关系 + 屏侧方位 + 朝向);Phase 7 prompt refs 必挂该场景干净俯视图 `layout_top.png`(直接引用、不做人物标注)+ "
        "9 宫格图、写 Spatial layout 声明句 + Map usage 俯视图仅作空间位置参考句(不得直接用于画面,机检 map_reference_only)、逐字注入 route_en、主体定义句用 blocking_map `label`(机检 layout_map_bound,"
        "`code/layout_map_bound_check.py`);**白模链同开(workflow.yaml whitebox_requested = 本开关)**:Phase 4 每场景 scene-modeling 出 `bible/scenes/<sid>/whitebox.json`,Phase 6 whitebox-staging 写 `whitebox_plans/` 并用 `code/render_whitebox.py` 导出 `assets/whitebox/<ep>/<grp>/{camera.mp4,top.mp4}`,"
        "导出即自动接成该组视频生成的参考视频(`code/sync_whitebox_refs.py --write`:组 prompt `video_refs`=camera.mp4(默认只挂 camera;项目 output.whitebox_top_video=true 且预算允许时才追加 top.mp4)+ `Shot 1:` 前固定段 `Whitebox reference:`(两路视频作用)/`Whitebox legend:`(颜色↔人物、眼睛鼻尖=朝向、深色摄像机盒+射线=镜头方向)+ Global constraints 禁白模外观句;"
        "prompt 工位写完必跑 `--write`,机检 whitebox_ref_bound;video-generation 按 video_refs 顺序传 `--ref-video`,方舟/MiniMax 参考视频须公网 URL——「设置 → 文件托管」未配置即报错),video-generation 开跑前复核——以上 SOUL.md/WORKFLOW.md 标注 2026-08-19 / 2026-09-07 的条款全部生效"
        if spatial_on else
        "**关闭 —— 沿用单张场景概念图流程,不建白模、不接参考视频**(用户判断本片不需要精确人物位置;p4-scene-model / p6-whitebox 不派发,组 prompt 不写 video_refs / Whitebox reference 段,whitebox_ref_bound 报 skipped):Phase 4 environment-concept 只出主视角场景概念图 "
        "`main_*.png` + 昼夜变体(不出 layout_top/grid_9views/layout.json,§6A 场景所需视图=主视角概念图+变体);"
        "storyboard/shot-planning **不写** scene_refs/blocking_map/view_tile、不跑 blocking_map_check.py;blocking 不受 blocking_on_map 约束"
        "(space_fragment_en 地标词按场景空间描述自拟,2026-07-23 规则照旧);prompt 场景锚挂场景概念图(`[Image N]` 普通绑定),"
        "不写 Spatial layout 句、不跑 layout_map_bound_check.py;scene_layout_pack_ok/blocking_map_present/"
        "blocking_on_map/layout_map_bound 四项机检一律跳过(报 `skipped: spatial_blocking off`)——"
        "SOUL.md/WORKFLOW.md 标注 2026-08-19 的场景布局包/动线标注条款**不适用**")
    narration_on = out.get("narration_enabled", True) is not False
    narration_line = (
        "开启 —— 旁白链路照常:narration 出稿(narration.md)、shot-planning 定挂点(narration_anchors)与逐组"
        " audio_plan、narrator 在 p7-video 前合成实测、audio-mixing 三路混音,§7D/§8B 机检全数生效"
        if narration_on else
        "**关闭 —— 用户约定整个片子没有任何旁白**:p5-narration/p8-narrator 一律不派发、不建卡,闸门不因缺"
        " narration.md/旁白轨而 HOLD(两工位被派到也只说明开关已关闭并结单);剧本/hook/片头片尾/预告等一切内容"
        "不得以画外音旁白形式呈现叙事;shot-planning 不写 narration_anchors(留空或省略),audio_plan 禁用 narration_over"
        "——无对白组一律 ambient_only 且照常逐组核查 silent_rationale,纯画面讲不清叙事时**不回派补写旁白**,"
        "上报 orchestrator 走剧本变更加对白或交用户裁决;prompt 不写旁白声明句(narration_over 专用句作废,"
        "无对白约束句照写);audio-mixing 只混原生轨+BGM 两路;subtitle 只做对白字幕;narration 系列机检"
        "(narration_anchors_cover_all/narration_window_gte_est_x1.15/narration_fit/narration_anchor_sync)"
        "一律跳过(报 skipped: narration off)")
    dubbing = (out.get("dialogue_voice") or "native") == "dubbing"
    dialogue_voice = (
        "**后期配音(dubbing)** —— 用户明确选择用 TTS 后期配对白(接受口型只能尽量贴合、非模型原生的取舍):"
        "组视频仍按对白组常规生成(prompt 照写 `{}` 台词、挂 voiceprint 音色锚,人物开口表演由模型原生生成——"
        "画面开口时段就是配音的时间依据);**每个对白组(audio_plan=dialogue)在 p7-video 交付后必派 p7-dub**"
        "(负责:09-audio/voice-generation):从组 clip 原生音轨实测每句台词的开口起止(说话人按 shot_list "
        "dialogue_lines 顺序对位),按 casting.json 该角色的 tts_model/tts_voice、voice.json 声线用 TTS 逐句合成冻结版台词"
        "(`python3 code/dub_group.py --project <slug> --ep epNN --group grpNNN`,内部走 genmedia tts),"
        "以语速(--speed,±25% 内)贴合开口时长、起点对齐开口起点,替换该组 clip 的对白轨(画面流不变、时长不变;"
        "原生轨备份 `.native_audio.wav`),产物 `assets/audio/voice/epNN/dub/grpNNN/`;p7-lipsync 只在其后做不换语音的"
        "对齐兜底(av_offset_lt_80ms);upscale/edit/mix 一律取配音后的组 clip。**§8A「TTS 严禁进成片对白」红线在本模式下"
        "由用户设置显式解除**,但仍禁止用 TTS 干声重驱/重绘口型画面(仅换音轨、以时段贴合)"
        if dubbing else
        "视频原声(native,默认)—— 成片对白语音就是视频模型随组 clip 原生合成的语音,**全流程不做任何对白 TTS**"
        "(不派 p7-dub,严禁 TTS 音轨进成片对白——§8A 红线);voiceprint 样本照常出、只作生成期 reference_audio 音色锚")
    platforms = resolve_platforms(ps)
    plat_list = "、".join(f"{name}({asp})" for _, name, asp in platforms)
    cross = "、".join(f"{name}({asp})" for _, name, asp in platforms if asp != aspect)
    dur = ps.get("duration") or {}
    brief = ""
    try:
        bp = PROJECTS_DIR / project / "brief.md"
        if bp.is_file():
            brief = bp.read_text(encoding="utf-8").strip()
    except Exception:
        brief = ""
    rv = ps.get("review") or {}
    try:
        ev = max(0, min(100, int(rv.get("evaluation", 60))))
    except (TypeError, ValueError):
        ev = 60
    eval_line = (
        f"- 质量评委(负责:00-orchestration/evaluation):{ev} —— 该值是 evaluation 对工单验收评分的"
        f"「必须照着改」合格线,替代其 SOUL.md 固定的 80 分:评分低于 {ev} 分的工单,意见必须具体到能照着改并返工重验"
        if ev else
        "- 质量评委(负责:00-orchestration/evaluation):0 —— **跳过质量评委**:orchestrator 全流程不派"
        " 00-orchestration/evaluation 任何工单,任务关单免 evaluation 验收评分,闸门不因缺验收评分而 HOLD;"
        "evaluation 被派到也只说明开关已关闭并结单,不做评分")
    review_lines = "\n".join(
        f"- {label}(负责:{qa_agent}):{int(rv.get(key, 0))}"
        for key, (label, qa_agent) in REVIEW_DIMENSIONS.items())
    # 当前 Agent 本身是某维度的 QA 时,单独点名其力度,避免它按 SOUL.md 固定阈值照旧执行
    own_review = "\n".join(
        f"- **你就是「{label}」的负责 QA,本项目该维度力度:{int(rv.get(key, 0))},"
        f"按下方换算规则执行,而非 SOUL.md 固定阈值**"
        for key, (label, qa_agent) in REVIEW_DIMENSIONS.items() if qa_agent == agent_id)
    if agent_id == "00-orchestration/evaluation" and ev:
        own_review += (f"\n- **你就是质量评委,本项目「必须照着改」合格线:{ev} 分,"
                       f"以此替代 SOUL.md 固定的 80 分**")
    if own_review:
        own_review = "\n" + own_review
    ui_lang = resolve_ui_language()
    if dur.get("episode_minutes") == "auto":
        ep_line = ("根据剧本自动 —— 不设固定每集预算:剧本分集(episode_plan)由 episode-planner 按剧情结构"
                   "自行决定集数与每集时长,并在 episode_plan 中写明各集实际预算;节奏(pacing)、"
                   "剪辑(edit)以 episode_plan 的实际预算为基准")
    else:
        ep_minutes = _fmt_num(dur.get("episode_minutes") or 10)
        ep_seconds = _fmt_num(float(dur.get("episode_minutes") or 10) * 60)
        ep_line = (f"{ep_minutes} 分钟(= {ep_seconds} 秒)—— 剧本分集(episode_plan 每集预算)、"
                   f"节奏(pacing)、剪辑(edit)一律以此为基准")
    shot_min = _fmt_num(dur.get("shot_min_s") or 1)
    shot_max = _fmt_num(dur.get("shot_max_s") or 10)
    sg = ps.get("shot_group") or {}
    sg_max = _fmt_num(sg.get("max_group_s") or 15)
    sg_img = int(sg.get("max_ref_images", 9))
    sg_vid = int(sg.get("max_ref_videos", 3))
    sg_aud = int(sg.get("max_ref_audios", 3))
    long_take = dur.get("long_take") is True
    long_take_line = (
        "**开启——自动续接**:continuity 的 group_transitions 新增 boundary_type: continuous(同一动作/运镜跨组)"
        "或 cut(反打/换构图连戏,旧数据缺省);跨场景/转场仍 anchor:none。同场景 anchor:last_frame 表示续接意图,"
        "实际模式由宿主选择:continuous 优先前组最后 2–3 秒视频向后续写,cut/模型不支持/尾镜不足2秒/视频预算不足回退尾帧。"
        "prompt 产出后运行 python3 code/sync_continuity_refs.py --project <slug> --ep <epNN> --write;"
        "生成前必须等前组出片,单组运行同命令 <grpNNN> --prepare,再重新读取 JSON 原序提交 refs/video_refs。"
        "continuity_ref.mode=tail_video 使用明确向后延长语义,开场不得 cut/reverse angle,不重复挂尾帧图;"
        "角色/场景高清图保留。白模与尾段共用数量/时长预算,摄影机白模优先,可选俯视预算不足不挂。"
        "尾段只含画面不带原对白,声音按本组指令生成。续接链及重生成影响判据必须同时看 refs 尾帧和 video_refs 尾段,"
        "不得把无尾帧图的视频续接当硬断点。重生成前组后必须重新 prepare 后组并复查接缝。"
        "旧 SOUL/WORKFLOW 中仅按尾帧判依赖的条款由本段覆盖;低清视频同样可能累积漂移。"
        if long_take else
        "**关闭(默认)——组间只用文字承接**:group_transitions 一律 anchor:none,不挂前组尾帧或续接尾段。"
        "如已有 continuity_ref,运行 sync_continuity_refs.py --write 清理;仅由明确的连戏文字承接。"
        "--return-last-frame 照常保存供预览/转场,无续接依赖的组可并行。")

    p = f"""你是「小说→视频」多 Agent 制作团队的成员,编号:{agent_id}。
以下 SOUL.md 是你的职责与边界的权威定义,必须严格遵守:

{soul}

## 运行环境
- 当前目录即工作区根目录;团队流程权威文件:agents/WORKFLOW.md、agents/workflow.yaml(需要时自行阅读相关章节)
- 当前项目目录:{proj_rel}/ —— 你的一切工作产物必须写入该目录下的对应子目录(布局见 WORKFLOW.md §2);目录不存在就创建
- 只做你 SOUL.md 职责内的事;越界的需求要说明应由哪个 Agent 负责,不要代劳
- 任务回执/评分/日志一律写 {proj_rel}/runs/<task_id>/(项目目录内);**严禁写工作区根 runs/**(文档中省略前缀的 runs/ 均指项目目录内)
- 交付方式:JSON/MD/YAML 类设计产物**直接逐份写出最终文件**,严禁先写 Python 生成脚本(把数据写成 dict 再跑脚本落盘)、严禁按几份一批拆多轮;一单 N 份的批处理工单一次做完;同批产物的共用说明(输入清单/坐标系/画幅约定等)不逐份复制进每个文件,只写 SOUL 规定字段与本实例特有值。确需脚本(计算/媒体处理/机检/批量调用)才写,落 {proj_rel}/code/,不要放进 runs/<task_id>/(WORKFLOW.md §2)
- 发现设定冲突:记录到 {proj_rel}/qa/defects/,不要擅自改 bible/ 已确认内容
- 完成后:用{ui_lang}简要汇报做了什么、关键决策,并列出「创建/修改的文件路径」清单
- 一切面向用户的对话/汇报/进度说明一律使用 {ui_lang}(用户的界面语言设置);工作产物的内容语言不受此影响,仍按下方「输出语言」设定执行

## 用户时长设定(Web 客户端项目设置,当前项目实时生效,优先级高于文档中的示例值)
- 每集目标时长:{ep_line}
- 单个分镜时长范围:{shot_min}–{shot_max} 秒 —— storyboard 的每镜时长建议与 shot-planning 的每镜终稿时长必须落在该区间。**对白承载(§7D ①/①′,2026-08-30)**:每组 Σ台词估时 ≤ 组时长×0.7、每镜 Σ ≤ 镜长、单句 ≤ {shot_max}×0.7 秒(估时 = 有效字符 ÷ 角色 voice.json speed_cpm 中点 ÷60);storyboard 起草分组、shot-planning 定镜时长都要按此装得下台词,定稿 shot_list 后由固定节点 p6-dialogue-fit(dialogue-rewrite)跑宿主 CLI `python3 code/check_dialogue_fit.py --project <slug> --ep epNN` 校验,超限按报告 trim_targets 只动对白文本层精简并 `--write-est` 复检;该节点 PASS 前不派 blocking、不发起 H3A,严禁靠压语速放行
- 生成组(generation group)总时长上限:{sg_max} 秒(整数)—— storyboard 分组草案与 shot-planning 定稿的每组 Σ镜头时长必须 ≤{sg_max}s(项目「视频模型设置」,已由用户按所选视频模型的单次生成上限配置:Seedance 2.0 系列 15s、Seedance 2.5 30s;文档中出现的 15s 示例值一律以本设定为准,见 WORKFLOW.md §7A)
- 长镜头(时长设置「长镜头」开关,自动选择尾段视频或尾帧):{long_take_line}
- 每组参考素材数量上限(项目「视频模型设置」,优先级高于文档示例值):参考图 ≤{sg_img} 张、参考视频 ≤{sg_vid} 个、参考音频 ≤{sg_aud} 段 —— 这是**全局视频模型**的口径;模型侧硬限(Seedance 2.0:9图/3视频/3音频、参考音视频总时长各≤15s;Seedance 2.5:30图/10视频/10音频、总时长各≤30s)由 genmedia 提交前强制校验。**refs 按实际需要挂齐(2026-08-30 改):必挂项(每角色 sheet、每生物 sheet、场景干净俯视图+9 宫格、道具比例锚)与本组确需的按需项(额外脸部锚/道具细节图/手绘渲染图/前组尾帧)一律写入 refs,不得为凑上限省略必挂图、不得自行拆组;张数超过本组生效上限时照常落盘完整 refs 并标 `status: "blocked_refs_cap"` + `blocked_reason`(逐张路径与所属实体、上限值、超出张数),上报 orchestrator 转告用户——由用户在分镜预览决定:①「🎛 模型」给该组单独换参考图上限更高的视频模型(如 Seedance 2.5 ≤30 张,渠道不变),或 ②手动删减该组参考图;用户拍板后重派本组。视频生成工位对 `blocked_refs_cap` 或 refs 超本组生效上限的组禁开跑(refs_mandatory_le_cap);组级覆盖了模型的组,上限以该组模型硬限为准(见下方「组级覆盖」段,如有)**

## 用户输出设定(Web 客户端项目设置,当前项目实时生效,优先级高于文档示例与项目内旧规范)
- 输出画幅:{aspect}({aspect_name})—— 画幅规范(aspect_ratio.json)、分镜构图、关键帧、视频生成、剪辑成片一律按该画幅执行(生成时 genmedia 传 --aspect {aspect});发现项目内既有产物或规范与此冲突,新产出以本设定为准并在汇报中注明
- 输出语言:{out_lang} —— 剧本、台词、旁白、字幕、配音、成片文案、发布物料一律使用 {out_lang} 输出;提供给生成模型的 prompt 不受此限(视频/图像 prompt 语言随界面语言,见下两条;音乐 prompt 用英文)
- 视频生成 prompt 语言:提供给视频生成模型的 video_prompt **正文散文(镜头动作/画面/运镜描述等)用{ui_lang}书写,不必用英文**;**注入视频 prompt 的上游片段内容语言同样用{ui_lang}(2026-08-24)**——各生产方按{ui_lang}产出片段内容:art-director 的 style.json 注入用风格串 `style_fragment_ui`(英文版 style_fragment_en/negative_prompt_en 保留供负面词表与存量回退)、blocking 的 `space_fragment_en`、lighting 的 `prompt_fragment_en`、costume 的 `visual_en`、prop 的 `scale.prompt_token`、sound-effect/ambience 的 cue(字段名保留历史 `_en` 后缀,不改名);**空间布局链路字段同样用{ui_lang}(2026-08-24 二订)**——layout.json `name_en`/`desc_en`、storyboard `route_en`/`offset_en` 及站位/prompt 句内的地标词一并按{ui_lang}产出(这些词只进 prompt、不上图,无字体限制——2026-09-07 起俯视图直接引用、不再叠加人物动线标注;地标词仍逐字取 layout.json `name_en`,全链路统一写法);**逐字纪律优先于语言偏好**:下游对既有片段一律逐字拼入、严禁翻译或改写,存量片段语言与{ui_lang}不一致时以既有片段为准,要换语言须回派上游成套重出(同场景/同集一致),不得零散混语;但以下保持英文原样不翻译——结构锚点(`Overall visual style:`/`Shot N:`/`Global constraints:` 及 `[Image N]`/`[Audio N]`/`@Image N`/`@Audio N` 引用,机检与注释注入代码依赖这些英文锚点;素材指代只用这套英文锚点,禁写「图片N/音频N/视频N」等本地化变体)、固定英文约束句(Identity lock、非对白组静默句、Spatial layout 声明句、Global constraints 负面清单)、台词(按剧本冻结版)
- 图像生成 prompt 语言:提供给图像生成模型的 image prompt(概念图/锚点图/参考图,genmedia image)**正文同样用{ui_lang}书写(2026-08-24)**——风格段逐字取 style.json `style_fragment_ui`(存量项目缺该字段回退英文 `style_fragment_en`);**负面词表保持英文**(`--negative` 与 prompt 内负面清单取 `negative_prompt_en`,通用负面术语跨引擎稳定、机检按英文子串匹配);存量英文项目补图沿用英文,不得半中半英
- 发布平台:{plat_list} —— Phase 11 发布(platform-adapter/seo/metadata/publisher)**仅面向这些平台**;aspect_ratio.json 平台矩阵、thumbnail 每平台封面、subtitle 每平台字幕以此清单为准。主生产画幅仍是上面的 {aspect}(母版按此原生生成){"" if not cross else f";与母版画幅不同的平台【{cross}】由 platform-adapter 在发布期从母版裁/补适配,不重新生成视频(现架构单母版)"}
- 内嵌字幕:{burn_in}
- 花字:{caption_line}
- 旁白:{narration_line}
- 对白配音:{dialogue_voice}
- 人物精确空间位置:{spatial_line}
- 视频分辨率:一切视频生成(首次/重 roll/兜底重做)一律 `--resolution {draft_res}`(草稿档);成片分辨率({final_res})与草稿档不同时,终版**默认且仅由 upscale 超分**得到——不询问用户、严禁按成片档重新生成(重生成贵、慢且画面随机);成片档 `--resolution {final_res}` 重出仅限一种情形——QA 判定超分不达标的兜底重出(WORKFLOW.md §7B)—— 分辨率直接决定生成费用,严禁擅自调高(genmedia 有硬闸门,越档自动压回草稿档)

## 用户审核设定(Web 客户端项目设置,当前项目实时生效,优先级高于 SOUL.md 与 WORKFLOW.md 中的固定阈值/闸门线)
{eval_line}
各维度审核力度 0-100(默认 0=不审核,用户在设置中调高才生效),当前项目取值:
{review_lines}{own_review}
力度换算规则(QA Agent 审核打分、orchestrator 派单与判闸门一律遵守;质量评委按上方设定单独执行,不适用本换算):
- **0:跳过该维度** —— orchestrator 不派该维度 QA 单;QA 被派到也不检查、不开缺陷单,报告只写 `"skipped": true`;闸门按通过处理
- **1-39 宽松**:只拦 blocker;SOUL.md 中的分数合格线下调 10 分、比例上限翻倍;major/minor 记录在报告中但不拦、不强制返工
- **40-69 常规**:严格按 SOUL.md / WORKFLOW.md 既有阈值与闸门线执行
- **70-89 严格**:分数合格线上调 5 分、比例上限减半;blocker/major 均拦;minor 也要开缺陷单
- **90-100 最严格**:分数合格线上调 10 分(上限 100)、比例类指标按 0 容忍;任何级别缺陷均拦并开单,吹毛求疵
- **草稿迭代期一律按 0 执行(2026-07-23)**:上表力度只在 G0–G10 闸门判定前的闸门级审核生效——
  草稿/迭代/返工阶段的逐单环节,orchestrator 不派各维度 QA 单,QA 被派到也按 0 处理;
  判每个 G 闸门前,orchestrator 按上表力度对该闸门范围的产物统一补派 QA 审核(力度 0 的维度不补派),
  缺陷清零机检照常。本条只约束各维度 QA 审核;evaluation 对工单的 acceptance 验收评分按上方「质量评委」设定执行"""
    p += ("\n\n## 用户版本管理设定(Web 客户端「版本管理」页开关,当前项目实时生效,优先级高于 SOUL.md 与 WORKFLOW.md 的版本化要求)\n"
          + ("- 版本管理:**开启** —— 照常执行既有版本化纪律:任务关单时通知 00-orchestration/version 登记产物,闸门通过后下达冻结指令"
             if (ps.get("versioning") or {}).get("enabled") else
             "- 版本管理:**关闭(默认)** —— orchestrator 不派 00-orchestration/version 的任何工单,"
             "逐批次产物登记与闸门冻结全部跳过;on_task_complete 收尾钩子免查「version 已登记」,"
             "闸门判定不因未登记/未冻结而 HOLD;version Agent 被派到也只说明开关已关闭并结单,不做登记"))
    max_retries = max_retries_setting()
    p += ("\n\n## 用户重跑次数设定(Web 客户端「设置→高级→Agent 高级设置」全局设置,实时生效,优先级高于 SOUL.md 与 WORKFLOW.md 中写死的「最多 3 次」「≤3 次」「max_retries: 3」)\n"
          f"- 自动重跑/重 roll 次数上限:**{max_retries}** —— 验收/评分/QA 不过带意见退回重做、媒体生成机检不达标自动重 roll,"
          f"同一任务/同一产物累计最多 {max_retries} 次"
          + ("(即不自动重跑:首次不过就升级用户裁决,不得自行重做)" if max_retries == 0 else
             f",第 {max_retries} 次仍不过升级用户裁决(--confirm),不得超额自行重试")
          + ";文档中所有写死的重跑/重 roll 次数一律以本值为准(publisher 特例仍按其 SOUL 取 min(本值, 2))")
    plug = plugin_of_agent(agent_id)
    if plug:
        plug_path = plugin_prompt_path(plug)
        wf_list = "、".join(f"{plug_path}/{w}" for w in plug["workflows"]) \
            or "(无独立 workflow,并入主流程)"
        ns_line = (f"\n- 产物命名空间:{proj_rel}/{plug['outputs_ns']}/ —— 除非工单显式指定其他路径,"
                   f"你的落盘产物一律写入该命名空间(runs/、qa/defects/ 等通用运行记录不受此限)"
                   if plug["outputs_ns"] else "")
        p += f"""

## 你来自插件「{plug['name']}」{('—— ' + plug['description']) if plug['description'] else ''}
- 本插件目录:{plug_path}/;插件流程权威文件:{wf_list}(需要时自行阅读){ns_line}
- 团队通用纪律对插件成员同等生效:工单格式(WORKFLOW.md §6)、运行记录三件套(§6.1)、质量三道闸与缺陷单(§7)、文件名 ASCII 红线(§1 原则 9)、Bible 冲突只上报不擅改"""
    if agent_id in PACKAGING_AGENTS:
        pk = ps.get("packaging") or {}

        def _seg(label: str, enabled: bool, notes: str = "") -> str:
            if not enabled:
                return f"- {label}:**禁用** —— 不制作、不预留占位、不占包装时长额度,相关工单不含该段"
            line = f"- {label}:启用"
            notes = (notes or "").strip()
            if notes:
                line += ",用户要求如下(逐字落实,署名/网址/版权声明等文本内容原样呈现不得改写;未提及的沿用 SOUL.md 与 style.json 默认):\n" \
                    + "\n".join(f"  > {ln}" for ln in notes.splitlines() if ln.strip())
            else:
                line += ",无附加要求,按 SOUL.md 与 style.json 默认制作"
            return line
        p += f"""

## 用户片头片尾设定(Web 客户端项目设置,当前项目实时生效,优先级高于 SOUL.md 与 WORKFLOW.md 中的默认包装方案)
{_seg("片头(intro)", pk.get("intro_enabled", True), pk.get("intro_notes", ""))}
{_seg("片尾(outro)", pk.get("outro_enabled", True), pk.get("outro_notes", ""))}
{_seg("下集预告(teaser)", pk.get("teaser_enabled", True))}
执行规则:
- 用户要求中指向 refs/ 的素材路径(厂标/Logo/二维码等)必须实际读取该文件并使用;文件不存在时上报,不得凭空生成替代
- 用户要求与 style.json 风格冲突时上报 art-director 裁决,不擅自取舍;涉及剧名的以 story/episode_plan.json 为权威,显示用标题按本设定呈现
- 调度派单时须把本设定原文写入 title/edit 相关工单的 instruction
- **片头启用 ⇒ 时间轴平移是硬工序(WORKFLOW.md §9B)**:正片 0 秒基准的声轨 assets/audio/final/epNN.wav 与字幕 subtitles.srt 接入片头后都要整体后移一个片头实测时长。edit 总装只准走 `python3 code/finalize_episode.py assemble --project <slug> --ep epNN`(声轨随正片段拼接、偏移天然产生,自动产 subtitles_final.srt 并机检),禁止自写 concat 后再 -itsoffset/adelay 手算;成片由其它路径产出时至少 `shift` + `check`;机检 intro_offset_ok(`finalize_episode.py check`)FAIL 即不交付/不发布。调度开总装工单时 instruction 必须写明该 CLI 命令与 acceptance `intro_offset_ok`;片头禁用时同样跑 check(偏移 0,字幕原样拷贝)"""
    if agent_id == "09-audio/audio-transcription" and project_skill_enabled("09-audio/audio-transcription/audio-transcription", project):
        p += f"""

## 音频转文字 Skill（本工位强制）
执行任何转写工单前，**先完整阅读 Skill 文件并按其中的模型缓存、时间轴格式和人物归属纪律执行**：
- Skill 文件：{AUDIO_TRANSCRIPTION_SKILL}
- 统一入口：`python3 modules/transcription.py transcribe ...`；缺少模型时会自动下载到
  `data/models/faster-whisper/`，不得在项目目录或插件目录另存模型，不得在工单里临时 pip install
- 多人音频按 Skill 把自然语言转换成确定参数：显式时间边界优先；“第一/第二个出现”用
  `--speaker-order`；用户明确男/女声或低/高音映射时用 `--pitch-map`。不得逐行交替，不得从图片推断性别；
  `ready_for_digital_human=false` 时必须阻塞付费生成"""
    if agent_id in CAPTION_AGENTS and out.get("caption_enabled"):
        p += """

## 用户花字设定(Web 客户端「输出设置」花字开关,当前项目已开启;详细规范 WORKFLOW.md §9A)
- 设计(10-editing/caption):edit/epNN/captions.json 用 **schema v2**——全集 ≤4 个 style_presets(font_id 引用 data/fonts/manifest.json,优先 CJK 字体);每条必填 group_id + 组内 local_start/local_end,与集级 start/end 双写对账;音效只从 data/sfx/manifest.json 按 tags 选 sfx_id,**不生成新音效**。密度:headline 每集 2–5 处、keyword ≤1 条/分钟、同屏最多 1 条、不入底部字幕安全区。无 bible/dictionary.json 的项目,花字文案必须逐片段命中 av/beat_track.json 母带原文(禁造词)
- 烧录(caption-render 工单):**只准执行 `python3 code/render_captions.py render --project <slug> --ep epNN`,禁止自写花字 ffmpeg 滤镜/脚本**;产物是 assets/clips_caption/ 副本,原组 clip 永不改动;manifest 缺失先跑 fonts-scan / sfx-scan(幂等);单组返工 = 改该组条目后 `render --grp grpNNN`
- 花字版成片(caption-final 工单,归 10-editing/edit):干净版 final.mp4 照常产出后,用 clips_caption 副本替换对应组按同一 EDL 重拼,SFX 轨与封装走 `render_captions.py sfx-track` + `mux`——**a:0=声轨权威+SFX 预混(开箱即听),a:1=声轨权威流拷贝(存档轨)**;MP4 多音轨是互斥备选流,严禁指望播放器叠加混播;严禁 -shortest
- 机检:各阶段交付前 `python3 code/check_captions.py --project <slug> --ep epNN --require design|render|final` 全 PASS;干净版既有机检口径不变,零重编码承诺只对干净版 final.mp4 成立
- 发布(platform-adapter):发布物料默认基于**花字版** final_caption.mp4 转码(其 a:0 已含音效);用户显式要求无花字版本时才用干净版
- 调度(orchestrator):按 DAG condition 正常排产 caption 节点,把本设定要点写入相关工单 instruction"""
    # 视频提示词技能:按项目 prompt_skill 设定解析(auto=生效视频模型;manual=用户指定;off=跳过),
    # 而非只看全局渠道;解析结果同步快照进 settings.json(机检基准)
    psk = None
    if agent_id == PROMPT_AGENT_ID:
        sync_prompt_skill_effective(project)
        psk = resolve_prompt_skill(project)
    psk_id = (psk or {}).get("skill_id") or ""
    psk_how = ""
    if psk:
        psk_how = (f"用户在项目设置中指定(manual)" if psk["mode"] == "manual"
                   else f"按生效视频模型 {psk['resolved_from']} 自动解析(auto)")
    if psk_id == PROMPT_SKILL_SD25:
        p += f"""

## Seedance 2.5 提示词优化 Skill(项目「提示词技能」设定:{psk_how},当前已生效)
当前项目的视频生成模型是 Seedance 2.5。撰写或优化组级 video_prompt 前,**先阅读官方提示词优化技能并按其方法执行**:
- Skill 文件:{SD25_PE_SKILL}(官方 sd25-pe,已随仓库安装,直接 Read 全文)
- 应用其中的:任务模板(文生视频/参考生视频/首尾帧/视频编辑/延长)、素材职责逐份映射与【未采用素材】清单、主体基数匹配、事件状态与因果保持、情绪表演/运镜/声音表达技法
- **优先级边界(冲突时以本团队规范为准)**:结构锚点(`Overall visual style:`/`Shot N:`/`Global constraints:`/`[Image N]`/`[Audio N]` 引用)、SOUL.md 机检清单、上游逐字拼入片段(风格串/光照 prompt_fragment_en/站位 space_fragment_en/道具 prompt_token)与冻结版台词一律保持不动——skill 用于提升散文表达质量、素材职责说明与模板化组织,不得以 skill 模板为由拆掉团队锚点结构
- skill 的「参数分离」原则与本仓库一致:画幅/时长/分辨率由 genmedia 命令行参数传递,不写进 prompt 正文"""
    if psk_id == PROMPT_SKILL_SD20:
        p += f"""

## Seedance 2.0 提示词写作 Skill(项目「提示词技能」设定:{psk_how},当前已生效)
当前项目的视频生成模型是 Seedance 2.0 系列。撰写或优化组级 video_prompt 前,**先阅读官方提示词写作技能并按其方法执行**:
- Skill 文件:{SD20_PE_SKILL}(官方 sd20-prompt-writing,已随仓库安装,直接 Read 全文;需要情绪外化对照表/文字生成模板/常见问题排查时再读同目录 references/guide-zh.md)
- 应用其中的:任务类型基础公式(全模态参考/编辑视频/延长视频/组合任务,编辑与延长直接用 `<视频N>` 指代、不写「参考」)、主体先定义后逐次同标签指代、每镜「运镜+主体动作表情+位置空间+音频」四要素、动作量化与情绪外化技法、符号约定(`（）`音乐/`<>`音效/`{{}}`台词/`【】`字幕)与「保持无字幕」等约束词、ID 漂移/双胞胎/风格漂移排查
- **优先级边界(冲突时以本团队规范为准)**:结构锚点(`Overall visual style:`/`Shot N:`/`Global constraints:`/`[Image N]`/`[Audio N]` 引用)、SOUL.md 机检清单、上游逐字拼入片段(风格串/光照 prompt_fragment_en/站位 space_fragment_en/道具 prompt_token)与冻结版台词一律保持不动——skill 的 `<图片N>`/「镜头N」指代按团队 `[Image N]`/`Shot N:` 约定落地,不得以 skill 模板为由拆掉团队锚点结构
- skill 的「参数分离」原则与本仓库一致:画幅/时长/分辨率由 genmedia 命令行参数传递,不写进 prompt 正文;不写精确秒数时间段,用镜头顺序让模型自然分配节奏"""
    if psk_id == PROMPT_SKILL_H3:
        p += f"""

## MiniMax H3 提示词写作 Skill(项目「提示词技能」设定:{psk_how},当前已生效)
当前项目的视频生成走 MiniMax H3 模型(H3 为开源模型,不限渠道:MiniMax/OpenRouter API、RunningHub、ComfyUI H3 工作流等)。撰写或优化组级 video_prompt 前,**先阅读官方提示词写作技能并按其方法执行**:
- Skill 文件:{H3_PE_SKILL}(官方 h3-prompt-writing,已随仓库安装,直接 Read 全文,再按其指引读同目录 references/ 下对应模式的指南)
- 模式选择:带多参考图/参考音频的组级默认路径(--ref/--audio-ref)用 **Ref2VA 六段改写格式**(subject_definitions/summary/retention_analysis/detailed_description/overall_soundscape/non_diegetic_music,读 references/ref-en.txt);纯文本或首尾帧兜底路径用 **base 结构**(integrated_multimodal_description/overall_soundscape/non_diegetic_music,读 references/base-en.txt),按 T2VA/I2VA/FL2VA/L2VA 对号入座
- 参考标签纪律:skill 的 reference 标签体系与本团队 `[Image N]`/`[Audio N]` 序号约定(1-based,与 refs/audio_refs 数组顺序严格一致)必须同时满足——标签在各段间保持一致,严禁出现未定义/未解析的标签
- **优先级边界(冲突时以本团队规范为准)**:上游逐字拼入片段(风格串/光照 prompt_fragment_en/站位 space_fragment_en/道具 prompt_token)与冻结版台词一律原样保留;对白/歌词/画面内文字保持原语言,其余改写段用英文(与 skill 口径一致);SOUL.md 机检清单仍逐项过检
- skill 的「参数分离」原则与本仓库一致:画幅/时长/分辨率由 genmedia 命令行参数传递,不写进 prompt 正文;prompt 内时间标注须与工单组时长(Σ)吻合"""
    if psk_id and psk_id not in (PROMPT_SKILL_SD25, PROMPT_SKILL_SD20, PROMPT_SKILL_H3):
        p += f"""

## 提示词技能 `{psk['dir']}`(项目「提示词技能」设定:{psk_how},当前已生效)
撰写或优化组级 video_prompt 前,**先 Read 该技能文件全文并按其方法执行**:
- Skill 文件:{psk['path']}(按其指引再读同目录 references/ 下的资料)
- **优先级边界(冲突时以本团队规范为准)**:结构锚点(`Overall visual style:`/`Shot N:`/`Global constraints:`/`[Image N]`/`[Audio N]` 引用)、SOUL.md 机检清单、上游逐字拼入片段与冻结版台词一律保持不动——skill 只用于提升散文表达、模板化组织与素材职责说明"""
    if psk is not None:
        if psk_id:
            contract = f"""- 本项目生效技能:`{psk['dir']}`(id `{psk_id}`;{psk_how});Skill 文件 {psk['path']}
- **必须在动笔前 Read 该 SKILL.md 全文**(运行结束时宿主核验本次运行的工具活动记录:没有读取该文件的运行按机检 `prompt_skill_read` FAIL 退回重做,不看回执自述)
- 每个组级 `assets/prompts/epNN/grpNNN.json` 必带回执字段 `skill_applied`:`{{"id": "{psk_id}", "sha256": "<该 SKILL.md 文件的 sha256>", "checklist": [{{"item": "<技能要点,如 主体先定义后指代>", "pass": true}}, ...]}}`——checklist 逐条对应该技能的核心要点(≥3 条),`pass=false` 的条目须在同级 `notes` 说明原因
- 用户在设置里改动技能或视频模型后,快照 settings.json#prompt_skill.effective 随之变化,已写好的 grpNNN.json 会因 id/sha256 不符而机检退回——退回单按新技能重做,不得只改字段"""
        else:
            why = {"user_skipped": "用户在项目设置中选择跳过(off)",
                   "no_match": f"生效视频模型 {psk['resolved_from'] or '(未知)'} 没有对应技能(auto 未匹配)",
                   "disabled": "所匹配技能已被禁用",
                   "missing": "用户指定的技能未安装"}.get(psk["reason"], psk["reason"] or "未设定")
            contract = f"""- 本项目**不套用**提示词技能:{why}。按 SOUL.md 常规写法完成工单,不要自行读取未启用的引擎提示词技能(本单有组级覆盖且通过项目勾选时,该组按组级契约执行)
- 每个组级 `assets/prompts/epNN/grpNNN.json` 仍必带回执字段 `skill_applied`:`{{"id": null, "reason": "{psk['reason'] or 'no_match'}"}}`"""
        warn = f"\n- ⚠️ {psk['warning']}(已在回执 notes 里如实记录即可,不阻塞)" if psk.get("warning") else ""
        p += f"""

## 提示词技能契约(prompt_skill_applied,项目「视频模型设置→提示词技能」,当前项目实时生效)
{contract}{warn}
- 交付前必跑 `python3 {PROMPT_SKILL_CHECK} --project {project} --ep epNN`(机检 `prompt_skill_applied`:字段齐全、id 与项目快照一致、sha256 与当前 SKILL.md 一致、checklist 无 false;不过=不交付),结果写进回执"""
    if agent_id == "08-video-gen/upscale" and is_minimax_upscale_available() \
            and project_skill_enabled("08-video-gen/upscale/minimax-regenerate-2k", project):
        p += f"""

## MiniMax Regenerate-2K 超分 Skill(仅当 MiniMax API Key 已配置时注入,当前已生效)
MiniMax 云端超分模型 Regenerate-2K 可用。执行超分工单前,**先阅读技能文件并按其判定条件与流程执行**:
- Skill 文件:{MINIMAX_UPSCALE_SKILL}(直接 Read 全文)
- 适用条件(两条都满足才走本 skill,否则按 SOUL.md 常规超分手段执行并在回执说明):
  1. MiniMax 已配置(本段出现即满足);
  2. 源 clip 满足 API 输入规格——MiniMax-H3 768P 直出成片口径(24fps、含音轨、宽高均被 32 整除、面积 ≤768×1344、约 4-15s);`upscale` 命令提交前会用 ffprobe 自动预检,不合规会明确报错,可先 `--dry-run` 只跑预检
- 调用:`python3 modules/genmedia.py upscale --input <源clip.mp4> --output <路径.mp4> --prompt "<该组生成时的原始 video_prompt,取 prompts.json>"`(固定输出 2K;也可 `--source-task-id <任务id>` 用 7 天内 succeeded 的 MiniMax 生成任务直接重生成,免传源视频)
- 输出 2K 与「输出设置」成片档像素尺寸不一致时,按 skill 指引用 ffmpeg 缩放到 aspect_ratio.json 目标尺寸;fps/时长/画幅/音画同步严禁改变
- 冲突时以 SOUL.md 为准;不适用或失败时回退常规超分手段,回执如实记录所用模型与参数(按 output_seconds 计费,严禁对同一 clip 反复盲重试)"""
    if agent_id == "08-video-gen/video-generation" and is_runninghub_video_active(agent_id=agent_id) \
            and project_skill_enabled("08-video-gen/video-generation/runninghub-cloud-workflow", project):
        p += f"""

## RunningHub 云端工作流视频生成 Skill(仅当视频渠道为 ComfyUI RunningHub 运行方式时注入,当前已生效)
当前项目的视频生成走 RunningHub 云托管 ComfyUI 工作流。执行视频生成工单前,**先阅读技能文件,理解参数如何进入云端工作流再提交**:
- Skill 文件:{RUNNINGHUB_VIDEO_SKILL}(直接 Read 全文)
- 调用入口不变:统一 CLI `python3 modules/genmedia.py video ...`,先 `--dry-run` 核对生效 provider/mode/参数组合;严禁绕过 genmedia 手工拼 RunningHub API 请求,严禁自行切换渠道/工作流
- 成功输出的远端 taskId 必须记入产物 meta 与 result.json;--seed 与(无占位符模板下的)--resolution/--aspect 进不了云端模板,实际输出以 ffprobe 实测为准如实写回执,不得因与请求档位不符自行拒交或改档
- 失败按 skill 排错口径保留 promptTips/failedReason 原文上报;云端按任务计费,严禁同参盲重投"""
    if agent_id == VIDEO_AGENT_ID and active_video_provider(agent_id=agent_id) == "agentics" \
            and project_skill_enabled("08-video-gen/video-generation/agentics-media-generation", project):
        p += f"""

## AgenticsLLM 视频生成 Skill(当前渠道已生效)
当前项目通过登录桌面端账号调用 Agentics 媒体生成 profile。执行视频生成工单前,**先阅读技能文件并遵守其调用与错误处理边界**:
- Skill 文件:{AGENTICS_VIDEO_SKILL}(直接 Read 全文)
- 统一调用 `python3 modules/genmedia.py video ...`;profile 固定字段、取值与附件数量约束、上传、幂等重试、轮询和取消由模块处理
- 禁止手工拼 Agentics REST 请求、猜工作流 node/token/index、为了 `repeat_last` 补槽位而重复附件或在测试中修改核心模块
- Agentics 视频命令允许至少 2 小时运行并按心跳等待;TLS/网络/503 不得判定生成失败。若外层进程在 task ID 创建后中断,用 `python3 modules/genmedia.py reclaim --task-id <UUID> --output <原路径>` 恢复同一任务,严禁重新提交
- 422 只按结构化 field/reason 上报或修正明确的工单输入;401/402/403 停止;只有服务端明确 failed/cancelled 才按生成失败处理"""
    if agent_id == PROMPT_AGENT_ID:
        p += group_overrides_prompt(project)
    elif agent_id == VIDEO_AGENT_ID:
        p += group_overrides_prompt(project, for_video_agent=True)
    p += agent_skill_prompt(agent_id, project)
    if brief:
        p += f"""

## 用户设计构想(项目 {proj_rel}/brief.md,全片最高创作前提)
以下构想约束题材类型、叙事取舍等全部环节;其中「设计风格」一节(如有)是全片画面视觉风格的权威定义,
风格设定(style.json)、概念图、关键帧、视频生成等一切视觉产出及其 prompt 必须与之一致;
「叙事节奏」一节(如有)给出单集节奏与跨集节奏的节拍链,是剧本分集/分场、钩子与分镜节拍设计的权威依据,
剧本与分镜的节拍走向必须按该节拍链落地(单集节奏管一集内部,跨集节奏管集与集之间的衔接);
你的任何决策与构想冲突时,以构想为准或上报用户裁决:

{brief[:3000]}"""
    if agent_id.split("/")[0] in MEDIA_CATEGORIES:
        p += f"""

## 生成模型调用(环境已配置好)
图像/视频生成一律通过统一模块 modules/genmedia.py(渠道与模型已由用户在 Web 客户端配置,勿自行挑模型或直连各家 API):
- 查看当前渠道/模型:`python3 modules/genmedia.py info`(记入产物 meta,保证可复现)
- 生成图像:`python3 modules/genmedia.py image --prompt "<prompt,语言随界面语言(2026-08-24)>" --output <路径.png> [--negative "<英文负面词>"] [--aspect 16:9|--size 2560x1440] [--ref 参考图...] [--n 4] [--seed N]`
  (新生成供视频参考的图统一出图规格:16:9 用 2560x1440、9:16 用 1440x2560;无最小像素硬限,复用图/前组尾帧不设像素门槛)
- 生成视频(组级多镜头,默认路径):`python3 modules/genmedia.py video --prompt "<Shot 1:/Shot 2: 分镜结构>" --output <路径.mp4> --ref 锚点图... [--ref-video 组 json video_refs 的白模 camera.mp4/top.mp4,按序] [--audio-ref 音色样本...] [--generate-audio on] [--return-last-frame tail.png] --duration <组Σ,4–{sg_max}整数> [--aspect 16:9] --resolution <草稿{draft_res}|成片{final_res}>`
- 生成视频(单镜首尾帧,兜底路径):`python3 modules/genmedia.py video --prompt "..." --output <路径.mp4> [--first-frame a.png] [--last-frame b.png] [--duration 4] [--aspect 16:9] --resolution <草稿{draft_res}|成片{final_res}>`(--ref 与首尾帧互斥)
- 生成音乐(BGM,仅音乐类工位):`python3 modules/genmedia.py music --prompt "<英文音乐描述:风格/情绪/乐器/节奏>" --output <路径.mp3> [--duration <秒>]`(渠道/模型由「🎨 生成模型」页音乐生成配置;OpenRouter:Lyria 3 Pro 完整歌曲、Lyria 3 Clip 30s 片段/Loop;ElevenLabs Eleven Music:--duration 3–600s 按 cue 精确出段;ComfyUI:ACE-Step 本地工作流、--duration 1–240s;默认纯音乐)
- TTS 旁白/音色样本(narrator/voice 类工位):`python3 modules/genmedia.py tts --text "<文本>" --output <路径.mp3> [--character CHAR-0001] [--variant child] [--voice <音色;仅云渠道>] [--speed 1.0] [--instructions "<语气/情绪指令>"]`(渠道/模型/默认音色由「🎨 生成模型」页 TTS语音模型配置,渠道可选 OpenRouter/火山豆包语音/ElevenLabs/ComfyUI。**旁白一律不传 --voice**——项目有旁白声线卡 `assets/audio/voice/narrator.json`(由 voice-generation 设计冻结,旁白声线唯一事实源)时 genmedia 自动按卡固定声线:同渠道用卡冻结 tts_voice、seed-audio 用卡冻结描述+冻结样本参考锚、ComfyUI 直接用冻结样本作参考音频,**用户改 TTS 设置不影响旁白声线**(渠道与卡不一致时 genmedia 告警回退并提示重定卡,如实上报);无卡才回退生效渠道配置的「默认音色」。角色配音按 casting 传 --voice 覆盖,语义随渠道:OpenRouter=音色名、火山=speaker 名、ElevenLabs=voice_id。**火山模型为 seed-audio-1.0(Doubao-音频生成 1.0)= 描述定制嗓音**:免选音色——角色配音传 `--character`(+`--variant`),声线描述由声纹卡 voice.json 声学字段自动拼装;旁白声线=旁白声线卡冻结描述(无卡按 `--instructions` 描述,缺省内置旁白声线);均不传 --voice(speaker 名会被忽略);项目已有冻结 voiceprint 样本时自动作 @音频1 参考锚,逐句/逐段合成不漂音色。ComfyUI:根据项目 voice/personality/appearance 从内置音色目录(远端 ComfyUI-Index-TTS/TimbreModel 音频库,首次使用自动下载缓存到 `data/TimbreModel/`)自动选参考音频,角色传 `--character`,旁白留空,禁止手填 `--voice`。instructions:OpenRouter 仅 OpenAI 系模型生效,火山注入情绪指令,ElevenLabs 忽略,ComfyUI 参与音色自动匹配、不注入合成)
- 详细纪律见 agents/WORKFLOW.md §9;生成失败如实上报,严禁伪造或占位产物

## 用户参考素材(视觉/配乐工作前必查)
用户通过 Web 客户端「参考文件」页把风格/封面/角色/场景/道具参考图、希望使用的音频、参考视频与文本资料按分类上传到 {proj_rel}/refs/(style/ thumbnail/ characters/ scenes/ props/ music/ video/ text/),并逐文件填写注释:
- **注释必读**:{proj_rel}/refs/NOTES.md(自动汇总用户逐图/逐曲注释,机器可读版 refs/annotations.json)说明每个文件管什么、想用在哪——有则必读并按注释执行
- 优先级:用户参考素材 > 你的自行发挥;与文字设定冲突时上报用户裁决,不擅自取舍
- 命中的参考图经 genmedia --ref 注入生成,并把所用路径记入产物 meta/prompts.json 的 user_refs 字段
- 配乐(09-audio/music)须先盘点 refs/music/,自行判断每首曲子适合用在视频的哪些位置并优先选用,选用/弃用情况写入 cue sheet(规则见 WORKFLOW.md §2 第 6 条)
- 视频生成类工位须先盘点 refs/video/(动作/运镜/节奏/转场参考),按注释对位到相应镜头;所选视频模型支持参考视频时经 `genmedia.py video --ref-video` 注入,不支持时作为提示词描述依据,所用路径记入 user_refs(规则见 WORKFLOW.md §2 第 8 条)
- 目录为空则照常工作,不阻塞;详细约定见 agents/WORKFLOW.md §2"""
    if is_dispatcher_agent(agent_id):
        confirm_timeout = confirm_timeout_setting()
        p += f"""

## 你的调度权(团队中仅调度型 Agent 拥有)
媒体工单配置认知:
- ComfyUI/IndexTTS2 的参考音频由 `modules/genmedia.py tts` 按内置音色目录 `modules/timbre_catalog.json`(索引远端 ComfyUI-Index-TTS/TimbreModel 音频库,首次使用自动下载缓存到 `data/TimbreModel/`)自动选择并上传,项目目录内没有 WAV/MP3 **不是阻塞条件**,不得要求用户手填默认参考音频
- voice-generation 工单必须调用 `genmedia.py tts --character <CHAR-ID> [--variant ...]`,narrator 工单不传 `--character`;两者均禁止传 `--voice`(ComfyUI 纪律;火山渠道模型为 seed-audio-1.0 描述定制嗓音时同样禁止 --voice——描述由声纹卡自动拼装、旁白靠 --instructions;其余云渠道角色配音按 casting.json 传 --voice)
- TTS 从云渠道切到 ComfyUI 后,旧 casting 的 `eve`/`ara` 等云音色名不得传给 ComfyUI;派 voice-generation 自动重选并更新 casting。工单必须先用相同参数执行 `--dry-run` 记录自动选型,再正式合成；失败回执逐字保留 `node_type/exception_type/exception_message`,不得把 Python 依赖/模型/节点异常改写成缺参考音频。日志出现“自动参考音频已选择并上传”后严禁要求用户手填音色

你可以把任务派给团队里任何其他 Agent,他们会以各自 SOUL.md 的身份在独立进程里工作:
- 同步派单(阻塞至完成并返回结果摘要):`python3 services/runtime/dispatch.py "<agent_id>" "<工作指令>" --project {project} --wait`
- 异步派单(立即返回 run_id):同上去掉 `--wait`
- IMPORTANT: `--project` accepts only the project slug (`{project}`), never `data/projects/...` or an absolute directory path.
- 引擎/模型默认用该成员自己的模型配置(用户在控制台按 Agent 配置,未配置则继承你的引擎);
  默认不要传 `--engine`(报错/返工也禁止切换引擎);仅 `--model <id>` 可在**同一引擎内**覆盖模型
- 查看全部 agent_id:`python3 services/runtime/dispatch.py --list`
- 查看运行状态:`python3 services/runtime/dispatch.py --runs`;查看单个:`python3 services/runtime/dispatch.py --status <run_id>`
- `--wait-all` 的 run_id **只能**使用你刚才异步派单后 stdout 返回的子任务 run_id;
  严禁传当前总制片自身的 `WEBUI_RUN_ID`/当前 `--status` ID。没有已派出的子任务时不要调用 `--wait-all`

派单守则:
1. 指令必须具体可执行:输入在哪、产物写到哪个路径、质量标准是什么(对照 agents/WORKFLOW.md §4 各阶段表的「工作指令要点」与「校验」列)
2. 有依赖关系的任务用 --wait 串行;相互独立的任务异步并行派发,之后用
   `python3 services/runtime/dispatch.py --wait-all <run_id...> --timeout 7200` 一次性等待全部完成
   (它会自动把等待进度实时上报到控制台,并在结束后打印每个子任务的结果摘要)。
   严禁自己写 sleep/轮询循环等待——那会让你的运行在界面上长时间无响应。
   等待类命令记得给 Bash 工具设置足够大的 timeout(如 7200000 毫秒)
3. 收到产物后做验收:检查文件存在、抽查内容是否达标;不达标就带着具体意见重新派单(最多 {max_retries} 次,用户设置「Agent 高级设置→重跑次数」,见上方「用户重跑次数设定」)
4. 【重跑须先确认】每次准备让某个 Agent 重跑(返工/重新派单)之前,必须先征询用户:
   `python3 services/runtime/dispatch.py --confirm "任务<task_id>验收未过:<一句话原因>。是否重跑?"`
   该命令会阻塞直到用户在控制台点击「重跑」或「跳过」,{confirm_timeout} 秒无人答复则输出默认值「重跑」
   (等待时长为用户设置「Agent 高级设置→重跑等待确认」,不要自行传 --timeout 覆盖)。
   命令输出「重跑」→ 正常重新派单;输出「跳过」→ 不再重跑,把该问题记入
   data/projects/<project>/qa/defects/ 并在最终汇报中说明跳过原因。首次派单不需要确认,只有重跑需要
5. 【人工签字点必须用 --sign】H1-H5 与每集 H3A 等人工签字闸门,必须用签字类确认:
   `python3 services/runtime/dispatch.py --confirm "【H1 <闸门名>】<要点与放行影响>" --sign`
   弹窗按钮为「签字/暂缓」,不倒计时、永不自动确认,保留到用户操作;命令默认最多等 4 小时。
   输出「签字」→ 闸门通过,走冻结流程;「暂缓」或「未签字」(等待超时)→ 记为等待人工,
   继续推进无依赖任务后正常结束运行。严禁把超时当签字通过,严禁用普通确认({confirm_timeout}s 自动默认)代替签字
6. 你自己不做成员职责内的具体创作,你的产出是:任务拆解、派单、验收、向用户汇报进度与结果
   派 for_each 批处理单(一单交付 N 份 JSON/MD)时,指令末尾必写「直接逐份落 JSON,不要写生成脚本、不要分批;共用说明不逐份复制」
   (WORKFLOW.md §2「静态数据产物直接落盘」——否则执行方可能先写一堆 gen_*.py 分批跑,耗时/token 数倍于直写)
7. 【blocker 挂起 ≠ 停机】某任务升级人工或等待裁决时,必须继续派发 DAG 上与它无依赖关系的
   其他可跑任务,禁止整条流水线待机干等(例:词典返工只应阻塞 merge,不应阻塞剧情理解/QA 预审)
8. 【赛马仅限用户明确指令,禁换引擎】严禁自行发起并行赛马(改派多个 Agent 并行重做同一任务、
   择优交付)。同一任务返工达到重跑次数上限({max_retries} 次)仍未过,必须走 --confirm 升级用户裁决;确有必要时可在
   --confirm 征询或升级说明中向用户**建议**赛马,只有用户明确下达赛马指令后,才可改派职责相近的
   Agent 并行重做、先达标者交付(赛马也不换引擎)。**任何情况下禁止切换执行引擎**(不得传 --engine 覆盖,
   不得因 GraphRecursionError/超时/API 5xx 等报错改用 claude/codex/kimi/pi/opencode/grok/deepagents 中的另一个)。
   报错后重试一律沿用原引擎与 Agent/全局模型配置——不要在同一条路上串行耗死,也不要用换引擎当兜底
9. 【结束前 DAG 前沿巡检】每次准备结束当前运行前,必须先运行
   `python3 services/runtime/dagcheck.py --project {project} --strict` 并检查依赖已满足的节点:
   有非人工待办就继续派单;有已解锁 `human:true` 的 H 门就必须当场执行
   `python3 services/runtime/dispatch.py --confirm "【<checkpoint>】<审阅要点与放行影响>" --sign --project {project}`。
   只有签字单已经发起、确有 blocker/暂缓，或 DAG 全部完成时才可结束。严禁只回复“后续必须签字”后关单，
   严禁自行把人工节点写成 passed；服务端漏单守卫只负责补建同类永久签字单，不替代本项职责"""
        plugs = active_plugins()
        if plugs:
            lines = []
            for pl in plugs:
                plug_path = plugin_prompt_path(pl)
                wf = "、".join(f"{plug_path}/{w}" for w in pl["workflows"]) or "(无独立 workflow)"
                lines.append(
                    f"- **{pl['name']}**{('(' + pl['description'] + ')') if pl['description'] else ''}:"
                    f"成员 {'、'.join(a['id'] for a in pl['agents'])};流程 DAG:{wf}"
                    + (f";产物命名空间 {pl['outputs_ns']}/" if pl["outputs_ns"] else ""))
            p += "\n\n## 已启用插件(扩展工位,派单方式与内置成员完全相同)\n" + "\n".join(lines) + f"""
插件调度纪律:
- 用户要求做某插件覆盖的业务时,先读该插件的 workflows/*.yaml(与 agents/workflow.yaml 同等地位的机器可读 DAG),
  把其节点并入 {proj_rel}/runs/dag.json 统一跟踪(插件 DAG 自带 id 前缀,不与主流程冲突;改完照常跑 dagcheck --strict)
- orchestrator 只拆单、派单、等待和验收,**禁止直接执行插件成员的生产命令**,也禁止通过改写
  VIDEOAGENTS_AGENT 冒充成员绕过职责边界;耗时命令必须留在被派发的成员任务内前台完成
- 工具/命令仍为 in_progress 时对应节点只能保持 running;不得结束任务后声称“后台继续”。外部异步渠道
  必须按插件 DAG 的最小生成单元派单并保留可恢复回执,等待真实产物与 validation 通过后才能标 done
- 插件任务同样走工单格式 §6、三件套 §6.1、评分与闸门 §7;人工签字点用 --sign,与 H1–H5 同规格
- 插件 manifest 的 requires.artifacts 声明了前置产物(如需正史 bible/ 冻结);缺前置时先补主流程对应阶段,不要硬跑
- 插件流程 YAML 若声明顶层 main_dag_on_start.skip(与该插件业务无关的主流程节点清单),并入节点的同一次改动中
  按 WORKFLOW.md §10.3 第 6 条执行:清单节点全部未开工(pending/template/blocked)才整组置 skipped 并写 skip_reason,
  存在任一已开工节点则一个不跳;闸门判定时 skipped 依赖视为已满足;用户其后要求推进被跳分支时恢复 pending 重新排产"""
    return p

# ---------------- 运行 claude -p ----------------


def tool_summary(name: str, inp: dict) -> tuple[str, str | None]:
    """返回 (活动描述, 涉及的产物文件路径或 None)。"""
    fp = inp.get("file_path") or inp.get("path")
    if fp:
        fp = rel_path(fp)
    normalized = name.lower()
    if normalized in ("write", "edit", "multiedit", "notebookedit"):
        return f"✎ {name}: {fp}", fp
    if normalized == "read":
        return f"📖 Read: {fp}", None
    if normalized == "bash":
        cmd = (inp.get("command") or "")[:160]
        return f"⚙ Bash: {cmd}", None
    if normalized in ("glob", "grep"):
        return f"🔍 {name}: {inp.get('pattern', '')}", None
    if normalized in ("task", "agent"):        # claude 子代理工具叫 Task,kimi 叫 Agent
        return f"🤖 {name}: {inp.get('description', '')}", None
    return f"🔧 {name}", None


def run_public(run: dict) -> dict:
    """给前端的运行摘要(不带大文本)。"""
    return {k: run[k] for k in (
        "id", "agent", "agent_name", "source", "parent", "project", "status",
        "created", "started", "ended", "cost", "turns", "error", "stopped",
        "net_error", "engine", "model", "tokens", "progress", "skill", "skill_read",
        "skill_retry_of", "skill_retry_run") if k in run} | {
        "activity": run.get("activity", [])[-8:],
        "files": run.get("files", [])[-20:],
        "message": (run.get("message") or "")[:120],
    }


def publish_run(run: dict):
    HUB.publish({"type": "run", "run": run_public(run)})


async def read_jsonl_line(stream: asyncio.StreamReader) -> bytes:
    """readline,但单行超过缓冲上限时分段拼接返回完整行,而不是抛
    LimitOverrunError(chunk is longer than limit)导致整个运行报错。"""
    buf = bytearray()
    while True:
        try:
            buf += await stream.readuntil(b"\n")
            return bytes(buf)
        except asyncio.IncompleteReadError as e:      # EOF,返回残余
            buf += e.partial
            return bytes(buf)
        except asyncio.LimitOverrunError as e:        # 缓冲区满还没见到换行:先取走已缓冲部分
            buf += await stream.readexactly(e.consumed)


def is_stateless_agent(agent_id: str) -> bool:
    if agent_id in STATELESS_AGENTS or agent_id.startswith(STATELESS_PREFIXES):
        return True
    p = plugin_of_agent(agent_id)                # 插件 Agent 可在 manifest 声明 stateless
    return bool(p and any(a["id"] == agent_id and a["stateless"] for a in p["agents"]))


def agent_run_limit(agent_id: str, is_stateless: bool | None = None) -> int:
    """Return the local fan-out limit, honoring provider-specific hard capacity.

    RunningHub accounts may expose only one workflow slot.  Serialize digital-human
    avatar workers locally so queued utterances stay queued in VideoAgents instead of
    all reaching the remote create endpoint together.  The transport still handles
    documented backpressure because other clients may occupy that same account.
    """
    stateless = is_stateless_agent(agent_id) if is_stateless is None else is_stateless
    if not stateless:
        return 1
    if agent_id == "17-digital-human/avatar-generator":
        try:
            dh = load_genconfig().get("digital_human") or {}
            rh = (dh.get("provider") == "comfyui"
                  and ((dh.get("comfyui") or {}).get("mode") or "local") in RH_BASES)
        except Exception:
            rh = False
        if rh:
            return 1
    return agent_concurrency()


# 提示词技能读取核验(prompt_skill_read):活动记录里出现该技能目录(Read/cat/read_file 任一
# 形式)即算读过;codex 引擎不回传文件读取事件,无法核验(skill_read=None,不退回)。
_NO_READ_TRACKING_ENGINES = {"codex"}


def _prompt_skill_postcheck(run: dict):
    """prompt 工位运行结束:本项目要求套用技能而本次运行没读过 SKILL.md → 机检 FAIL 退回。
    派单(有父运行)的:状态改 error 回给总制片按返工流程重派;用户手动对话的:同样标 error,
    并自动在同一会话追加一次补读工单(仅一次,补读单再不读就只报错不再追加)。"""
    if run.get("agent") != PROMPT_AGENT_ID or not run.get("skill_path"):
        return
    if run.get("status") != "done" or run.get("stopped"):
        return
    if (run.get("engine") or "") in _NO_READ_TRACKING_ENGINES:
        run["skill_read"] = None
        return
    markers = [f"skills/{d}/" for d in [run.get("skill") or ""] + list(run.get("skill_alts") or []) if d]
    acts = run.get("activity") or []
    run["skill_read"] = any(m in a for a in acts for m in markers)
    if run["skill_read"]:
        return
    run["status"] = "error"
    run["error"] = (f"机检 prompt_skill_read FAIL:本项目要求套用提示词技能 {run['skill']},"
                    f"但本次运行的工具活动记录里没有读取 {run['skill_path']};"
                    "产出的 video_prompt 未经该技能优化,本单退回重做")
    if run.get("skill_retry_of"):
        run["error"] += "(这已是自动补读单,不再追加;请人工核查该工位是否按契约执行)"
    elif not run.get("parent"):
        run["_skill_followup"] = True
        run["error"] += "(已自动追加一次补读重做工单)"
    else:
        run["error"] += ("(派单方按返工流程重派:attempt 递增,指令首行写明「先 Read "
                         f"{run['skill_path']} 再按其 checklist 重写并补 skill_applied」)")


async def _prompt_skill_followup(run: dict):
    """用户手动对话触发的 prompt 运行漏读技能时,同会话自动追加一次补读工单。"""
    msg = (f"机检 prompt_skill_read FAIL:上一单没有读取 {run['skill_path']}。"
           f"现在先 Read 该文件全文,按其要点重新优化你刚写的每个组级 video_prompt"
           f"(团队锚点结构、逐字片段与冻结台词不动),逐组补 skill_applied 回执字段,"
           f"然后跑 `python3 {PROMPT_SKILL_CHECK} --project {run['project']} --ep <本单集号>` 到 PASS 再交付。")
    out = await api_chat({"agent": run["agent"], "message": msg, "project": run["project"],
                          "engine": run.get("engine") or "", "model": run.get("model") or None,
                          "source": "runtime", "parent": None})
    rid = out.get("run_id")
    if rid and rid in RUNS:
        RUNS[rid]["skill_retry_of"] = run["id"]
        run["skill_retry_run"] = rid
        publish_run(RUNS[rid])
        publish_run(run)


async def execute_run(run: dict, message: str, model: str | None):
    agent_id = run["agent"]
    is_dispatcher = is_dispatcher_agent(agent_id)
    # 调度型 Agent 要等整条流水线,墙钟超时放宽 4 倍;它大部分时间静默等子任务,不设无输出超时
    run_timeout = run_timeout_setting() * (4 if is_dispatcher else 1)
    idle_timeout = 0 if is_dispatcher else idle_timeout_setting()
    is_stateless = is_stateless_agent(agent_id)
    async with AsyncExitStack() as stack:
        # 调度器大部分时间在等子任务(--wait-all),不占工人槽;否则 8 个槽实际只剩 7 个干活
        if not is_dispatcher:
            await stack.enter_async_context(SEM)
        # 无状态服务型 agent 每次全新会话,同 agent 并发受「并发数量」额度约束;
        # 其余额度恒为 1(串行保护会话)。调度器不占槽但同样串行(同一总制片会话)
        limit = agent_run_limit(agent_id, is_stateless)
        await stack.enter_async_context(agent_sem(agent_id, limit))
        run["status"] = "running"
        run["started"] = time.time()
        publish_run(run)

        # 虚拟人像资产库「全自动管理」:视频生成工单开跑前自动清库+本集人物图入库
        # (仅火山引擎视频渠道;函数内部自带条件判定与兜底,失败不阻断)
        if agent_id == AVATAR_AUTO_AGENT:
            await avatar_auto_manage_for_run(run, message)

        engine = run.get("engine", "claude")
        cli_executable = None
        if engine in CLI_BINS:
            cli_executable = resolve_cli_executable(engine)
            if not cli_executable:
                run["status"] = "error"
                run["error"] = cli_not_found_error(engine)[:500]
                run["ended"] = time.time()
                append_chat(agent_id, run["project"],
                            {"role": "assistant", "text": run["error"],
                             "run_id": run["id"], "status": "error"})
                publish_run(run)
                return
        try:
            role = build_role_prompt(agent_id, run["project"])
            skill_contract = agent_skill_prompt(agent_id, run["project"])
            if agent_id == PROMPT_AGENT_ID:
                # 运行面板 chip + 结束时 prompt_skill_read 核验的依据(dir 空 = 本项目不套用技能)
                psk = resolve_prompt_skill(run["project"])
                run["skill"] = psk["dir"]
                run["skill_path"] = psk["path"]
                # 组级覆盖的技能(分镜预览「🎛 模型」):本单只写覆盖组时读的是覆盖技能,
                # prompt_skill_read 核验任一命中即可;逐组精确对照交给机检 prompt_skill_applied
                try:
                    alts = sorted({r["skill_dir"] for r in sync_group_settings_effective(run["project"])
                                   if r.get("skill_dir") and r["skill_dir"] != psk["dir"]})
                except Exception:
                    alts = []
                run["skill_alts"] = alts
                if not psk["dir"] and alts:
                    run["skill"], run["skill_path"] = alts[0], next(
                        (c["path"] for c in prompt_skill_candidates() if c["dir"] == alts[0]), "")
                publish_run(run)
        except Exception as error:  # noqa: BLE001
            # Prompt construction happens before the CLI process and its JSONL log
            # are created. Always finish the run here so an encoding/configuration
            # error cannot leave the UI stuck in the "running" state forever.
            run["status"] = "error"
            run["error"] = f"\u6784\u5efa Agent \u63d0\u793a\u8bcd\u5931\u8d25\uff1a{error}"[:500]
            run["ended"] = time.time()
            append_chat(agent_id, run["project"],
                        {"role": "assistant", "text": run["error"],
                         "run_id": run["id"], "status": "error"})
            publish_run(run)
            return
        thinking_effort = thinking_effort_setting()   # 统一思考深度,启动时取值(在跑的运行不变)
        session_key = f"{engine}::{agent_id}::{run['project']}"
        # 记忆开关关闭时所有 agent 全新会话(session_id 仍照常回存,重新开启即恢复)
        session_id = (None if is_stateless or not agent_memory_enabled()
                      else STATE["sessions"].get(session_key))
        # 会话膨胀保险丝:历史超过记忆额度时新开会话,避免 resume 每轮重发全史
        if session_id:
            resume_limit = agent_memory_kb() * 1024
            if engine == "deepagents":
                resume_limit = min(resume_limit, DEEPAGENTS_RESUME_LIMIT)
            try:
                if chat_path(agent_id, run["project"]).stat().st_size > resume_limit:
                    session_id = None
                    run["session_reset"] = True
            except OSError:
                pass

        if engine == "deepagents":
            try:
                da = resolve_deepagents()
                resolution_error = None
            except ServiceError as exc:
                da = {"provider": "agentics", "base_url": "", "api_key": "", "model": ""}
                resolution_error = exc.detail
            requested_model = str(model or "").strip()
            # 兼容修复前已落盘/传入的渠道占位值，绝不把 local/openrouter 发给模型端点。
            use_model = (da["model"] if not requested_model
                         or requested_model == da["provider"] else requested_model)
            err = resolution_error
            if not use_model:
                err = err or ("deepagents 引擎未配置模型:请在 🎨 生成模型 页"
                              "「语言模型/DeepAgents」为生效渠道"
                              "(AgenticsLLM/本地模型/云端模型/OpenRouter)配置模型")
            elif da["provider"] == "agentics" and not da["api_key"]:
                err = err or ("deepagents 引擎当前生效渠道为 AgenticsLLM，"
                              "请先登录桌面端账号")
            elif da["provider"] == "openrouter" and not da["api_key"]:
                err = ("deepagents 引擎当前生效渠道为 OpenRouter,但未配置 API Key:"
                       "请在 🎨 生成模型 页「语言模型/DeepAgents → OpenRouter」填写")
            elif da["provider"] == "cloud" and not da["api_key"]:
                err = ("deepagents 引擎当前生效渠道为云端模型,但未配置 API Key:"
                       "请在 🎨 生成模型 页「语言模型/DeepAgents → 云端模型」填写")
            if err:
                run["status"] = "error"
                run["error"] = err
                run["ended"] = time.time()
                append_chat(agent_id, run["project"],
                            {"role": "assistant", "text": run["error"],
                             "run_id": run["id"], "status": "error"})
                publish_run(run)
                return
            da_cmd = [deepagents_python(), str(ROOT / "modules" / "deepagents_runner.py"),
                      "--model", use_model,
                      "--base-url", da["base_url"],
                      "--api-key", da["api_key"]]
            da_cmd += engine_effort_args("deepagents", thinking_effort,
                                         local_endpoint=da["provider"] == "local")
            # 有状态 agent 启用检查点(会话续接);记忆开关关闭时 session_id 为
            # None,runner 仍新开会话并回报 id,照常回存——与 claude/codex 语义
            # 一致(关闭期间照存,重新开启后从最近一次会话继续)
            if not is_stateless:
                da_cmd += ["--checkpoint-db", str(DEEPAGENTS_SESSIONS_DB)]

        def codex_stdin(sid: str | None) -> bytes | None:
            if engine != "codex":
                return None
            prompt = skill_resume_message(message, skill_contract, bool(sid)) if sid else f"{role}\n\n---\n\n## 当前工作指令\n\n{message}"
            return prompt.encode("utf-8")

        def make_cmd(sid: str | None) -> list[str]:
            """按会话 id 生成引擎命令;会话失效回退时以 sid=None 重建全新会话命令。"""
            if engine == "deepagents":
                return da_cmd + (["--thread-id", sid] if sid else [])
            if engine == "codex":
                # codex 无 --append-system-prompt:首轮把角色说明拼进 prompt;续轮走 resume(会话已带上下文)
                base = [cli_executable, "exec", "--json", "--skip-git-repo-check",
                        "--dangerously-bypass-approvals-and-sandbox", "-C", str(ROOT)]
                if model:
                    base += ["-c", f"model={model}"]
                base += engine_effort_args("codex", thinking_effort)
                # Codex reads the prompt from stdin. This avoids Windows'
                # process command-line length limit for large Agent prompts.
                if sid:
                    return base + ["resume", sid, "-"]
                return base + ["-"]
            if engine == "kimi":
                # kimi 兼容 claude -p 用法,但无 --append-system-prompt:首轮把角色说明拼进
                # prompt;续轮走 -r resume(会话已带上下文)。-p 非交互模式固定 auto 权限,
                # 与 --yolo/--auto 互斥,无需也不能传权限参数
                base = [cli_executable, "--output-format", "stream-json"]
                if model:
                    base += ["-m", model]
                if sid:
                    return base + ["-r", sid, "-p", skill_resume_message(message, skill_contract, True)]
                return base + ["-p", f"{role}\n\n---\n\n## 当前工作指令\n\n{message}"]
            if engine == "opencode":
                # opencode run 原生 JSON 事件流与持久会话,但无 --append-system-prompt:
                # 首轮把角色说明拼进 prompt;续轮走 --session(会话已带上下文)。
                # --auto 放行未显式拒绝的工具权限(非交互运行必需)
                base = [cli_executable, "run", "--format", "json", "--auto"]
                if model:
                    base += ["-m", model]
                base += engine_effort_args("opencode", thinking_effort)
                if sid:
                    return base + ["--session", sid, skill_resume_message(message, skill_contract, True)]
                return base + [f"{role}\n\n---\n\n## 当前工作指令\n\n{message}"]
            if engine == "grok":
                # Grok Build CLI 的 headless 参数与 claude 同构:-p 单轮、--max-turns、
                # --resume <uuid> 续接;--output-format streaming-messages-json 输出 Anthropic
                # Messages 线格式(system/init 带 session_id → assistant 消息含 usage →
                # result 汇总,实测 1.0.5),事件经工具名归一后复用 claude 处理器;
                # --rules 追加系统提示词(无文件变体,走 argv);--always-approve 放行工具
                # 权限(非交互运行必需)。系统提示词每轮都传(同 claude --append-system-prompt)
                base = [cli_executable, "-p", message,
                        "--output-format", "streaming-messages-json",
                        "--always-approve", "--max-turns", str(max_turns_setting()),
                        "--rules", role]
                if model:
                    base += ["-m", model]
                base += engine_effort_args("grok", thinking_effort)
                if sid:
                    base += ["--resume", sid]
                return base
            if engine == "pi":
                # pi 原生 JSON 事件流与持久会话。系统提示通过文件传入，既避免
                # Windows 命令行长度限制，也让续接进程每轮恢复同一 Agent 身份。
                base = [cli_executable, "--mode", "json", "--approve",
                        "--append-system-prompt", str(system_prompt_file)]
                if model:
                    base += ["--model", model]
                base += engine_effort_args("pi", thinking_effort)
                if sid:
                    base += ["--session", sid]
                return base + [message]
            c = [cli_executable, "-p", message,
                 "--output-format", "stream-json", "--verbose"]
            if claude_prompt_file:
                c += ["--append-system-prompt-file", str(claude_prompt_file)]
            else:
                c += ["--append-system-prompt", role]
            c += ["--permission-mode", PERMISSION_MODE,
                  "--max-turns", str(max_turns_setting())]
            if model:
                c += ["--model", model]
            c += engine_effort_args("claude", thinking_effort)
            if sid:
                c += ["--resume", sid]
            return c

        env = {**os.environ,
               "VIDEOAGENTS_RUN_ID": run["id"], "VIDEOAGENTS_PORT": str(PORT),
               "VIDEOAGENTS_PROJECT": run["project"], "VIDEOAGENTS_ENGINE": engine,
               "VIDEOAGENTS_AGENT": agent_id,
               "VIDEOAGENTS_WORKSPACE_ROOT": str(ROOT),
               "VIDEOAGENTS_PROJECT_ROOT": project_prompt_path(run["project"]),
               # 兼容旧版媒体模块；值与 VIDEOAGENTS_PROJECT 始终一致，避免继承到旧项目。
               "WEBUI_PROJECT": run["project"]}   # genmedia 据此应用 Agent 级图像/视频渠道覆盖
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        if engine == "opencode":   # 缓存目录不可写时改道,避免模型注册表过期
            env = opencode_env(env)
        if engine == "deepagents":   # 长文本走环境变量,避免超长 argv
            env["DA_SYSTEM"] = role
            env["DA_PROMPT"] = message
            # LangGraph recursion_limit 默认过低时,多工具任务会稳定 GraphRecursionError。
            # 用户已设 DEEPAGENTS_RECURSION_LIMIT 时尊重;否则工人 250 / 调度器 500。
            # runner 撞上限后会从检查点自动续跑(DEEPAGENTS_MAX_CONTINUATIONS,默认 3 轮)。
            if "DEEPAGENTS_RECURSION_LIMIT" not in env:
                env["DEEPAGENTS_RECURSION_LIMIT"] = (
                    "500" if agent_id in DISPATCHERS else "250"
                )

        # Windows has a short process command-line limit. Agent role prompts can
        # exceed it, so pass Pi's prompt (and Claude's on Windows) through a UTF-8
        # file. Keep Claude's argv behavior elsewhere for older CLI compatibility.
        system_prompt_file = (RUNS_DIR / f"{run['id']}.system-prompt.txt"
                              if engine == "pi" or (os.name == "nt" and engine == "claude")
                              else None)
        claude_prompt_file = system_prompt_file if engine == "claude" else None
        log_f = (RUNS_DIR / f"{run['id']}.jsonl").open("w", encoding="utf-8")
        proc = None
        stderr_task = None
        try:
            if system_prompt_file:
                system_prompt_file.write_text(role, encoding="utf-8")
            deadline = time.time() + run_timeout
            session_retried = False
            while True:
                stdin_payload = codex_stdin(session_id)
                proc = await asyncio.create_subprocess_exec(
                    *make_cmd(session_id), cwd=ROOT, env=env, limit=STREAM_LIMIT,
                    start_new_session=True,   # 独立进程组:停止时可连同其派生子进程一起杀
                    stdin=(asyncio.subprocess.PIPE if stdin_payload is not None else None),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                RUN_PROCS[run["id"]] = proc
                if stdin_payload is not None and proc.stdin:
                    proc.stdin.write(stdin_payload)
                    try:
                        await proc.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    proc.stdin.close()
                # 并发排空 stderr:否则子进程 stderr 写满 OS 管道缓冲会卡死到超时
                stderr_task = asyncio.create_task(proc.stderr.read())
                last_output = time.time()
                while True:
                    now = time.time()
                    if now > deadline:
                        raise TimeoutError(f"运行超过 {run_timeout}s")
                    wait = deadline - now
                    if idle_timeout:
                        wait = min(wait, last_output + idle_timeout - now)
                    try:
                        raw = await asyncio.wait_for(read_jsonl_line(proc.stdout),
                                                     timeout=max(1, wait))
                    except asyncio.TimeoutError:
                        if idle_timeout and time.time() - last_output >= idle_timeout:
                            raise TimeoutError(
                                f"连续 {idle_timeout}s 无输出") from None
                        continue
                    if not raw:
                        break
                    last_output = time.time()
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    log_f.write(line + "\n")
                    log_f.flush()
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if engine == "codex":
                        handle_codex_event(run, obj)
                    elif engine == "kimi":
                        handle_kimi_event(run, obj)
                    elif engine == "pi":
                        handle_pi_event(run, obj)
                    elif engine == "opencode":
                        handle_opencode_event(run, obj)
                    elif engine == "grok":
                        handle_grok_event(run, obj)
                    elif engine == "deepagents":
                        handle_deepagents_event(run, obj)
                    else:
                        handle_claude_event(run, obj)
                    if run.get("net_error") and proc.returncode is None:
                        # 连接层错误快速失败:不等 CLI 的退避重试,直接杀进程组
                        _kill_proc_tree(proc)
                        break
                await proc.wait()
                stderr = (await stderr_task).decode("utf-8", "replace").strip()
                failed = ((proc.returncode != 0 and not run.get("result"))
                          or (engine == "pi" and bool(run.get("error"))))
                if run.get("stopped"):
                    # 用户在运行面板手动停止(或服务关闭):不走会话失效重试,不让引擎的
                    # aborted/退出码信息覆盖「手动停止」结论(下游总制片据此免于追查错误原因)
                    run["status"] = "error"
                    run["error"] = STOPPED_MSGS.get(run["stopped"], STOPPED_BY_USER_MSG)
                    break
                if failed:
                    # 会话失效回退:记录的会话已被引擎清理(换机/清缓存/引擎升级)时,
                    # resume 必然失败且下轮还会用同一失效 id;清掉记录换全新会话重试一次
                    if (session_id and not session_retried and re.search(
                            r"no conversation found|session[^\n]{0,80}not found"
                            r"|no session found|thread[^\n]{0,40}not found",
                            f"{run.get('error') or ''} {stderr}", re.I)):
                        session_retried = True
                        session_id = None
                        run.pop("error", None)
                        run["session_reset"] = True
                        STATE["sessions"].pop(session_key, None)
                        save_state(STATE)
                        continue
                    run["status"] = "error"
                    # 引擎事件流里报过错(如 deepagents 的 error 事件)则保留原始信息
                    run["error"] = (run.get("error") or stderr
                                    or f"{engine} 退出码 {proc.returncode}")[:500]
                else:
                    run["status"] = "done"
                break
        except (TimeoutError, asyncio.TimeoutError) as error:
            run["status"] = "error"
            run["error"] = f"超时({str(error) or f'运行超过 {run_timeout}s'}),进程已终止"
            if proc and proc.returncode is None:
                _kill_proc_tree(proc)     # 连派生的 yt-dlp/ffmpeg/dispatch 子进程一起杀
        except asyncio.CancelledError:
            run["status"] = "error"
            run["error"] = run.get("error") or "服务关闭，任务已停止"
            if proc and proc.returncode is None:
                _kill_proc_tree(proc)
            raise
        except FileNotFoundError as error:
            run["status"] = "error"
            if engine in CLI_BINS:
                run["error"] = cli_not_found_error(engine)[:500]
            else:
                missing = error.filename or deepagents_python()
                run["error"] = (f"无法启动 DeepAgents Python 运行环境："
                                f"未找到“{missing}”。请在项目根目录运行 "
                                f"make install-deepagents 创建专用环境；或在当前环境执行 "
                                f"pip install -e \".[deepagents]\"（需 Python ≥3.11）；"
                                f"或设置 DEEPAGENTS_PY 指向已安装 deepagents 的解释器。")[:500]
            if proc:
                proc.kill()
        except OSError as error:
            run["status"] = "error"
            if getattr(error, "winerror", None) == 206:
                run["error"] = (f"无法启动 {CLI_LABELS.get(engine, engine)}："
                                "传给进程的命令行过长。请更新 VideoAgents 后重试。")
            else:
                run["error"] = f"启动 {CLI_LABELS.get(engine, engine)} 失败：{error}"[:500]
            if proc:
                proc.kill()
        except Exception as e:  # noqa: BLE001
            run["status"] = "error"
            run["error"] = str(e)[:500]
            if proc:
                proc.kill()
        finally:
            RUN_PROCS.pop(run["id"], None)
            RUN_TASKS.pop(run["id"], None)
            if stderr_task and not stderr_task.done():
                stderr_task.cancel()
            log_f.close()
            if system_prompt_file:
                system_prompt_file.unlink(missing_ok=True)
            run["ended"] = time.time()
            run.pop("progress", None)
            _prompt_skill_postcheck(run)
            # 会话续用:记录本次会话 id(无状态服务型 agent 不留会话)。
            # 无任何 assistant 产出的运行不记:部分引擎(如 pi)惰性落盘会话文件,
            # 刚启动就被停止/报错的运行留下的是从未写盘的幽灵会话 id,续用必报
            # "No session found";这种会话也没有值得续接的上下文
            if run.pop("clear_sessions_on_end", None):
                # 本运行期间用户签字过门(见 _continue_signed_gate):阶段收官,
                # 不回存本次会话并清掉全部记录,下一条消息即开全新会话
                _clear_agent_sessions(run["agent"], run["project"])
                append_chat(run["agent"], run["project"], {
                    "role": "assistant", "status": "done",
                    "text": "🧹 签字通过,本轮结束后已自动清理会话记录,"
                            "下一条消息将开启全新会话。"})
            elif (run.get("session_id") and not is_stateless
                    and (run.get("result") or run.get("text"))):
                STATE["sessions"][session_key] = run["session_id"]
                save_state(STATE)
            reply = run.get("result") or run.get("text") or run.get("error") or "(无输出)"
            chat_entry = {"role": "assistant", "text": reply,
                          "run_id": run["id"], "status": run["status"]}
            if run.get("stopped"):
                # 对话记录里明确标注「手动停止」,而不是只留下被截断的半截输出+error 状态,
                # 否则后续 agent(尤其总制片)读到会误判为程序错误去追查原因
                partial = run.get("result") or run.get("text") or ""
                dur = int(run["ended"] - run["started"]) if run.get("started") else 0
                how = ("被用户在运行面板手动停止" if run["stopped"] == "user"
                       else "因服务关闭/重启被中断")
                reply = (f"⏹ {run['error']}(运行 {dur}s 后{how},"
                         "非程序错误,无需追查失败原因;是否重派由用户决定)"
                         + (f"\n\n--- 停止前的部分输出 ---\n{partial}" if partial else ""))
                chat_entry.update(text=reply, stopped=run["stopped"])
            elif run.get("net_error"):
                partial = run.get("result") or run.get("text") or ""
                dur = int(run["ended"] - run["started"]) if run.get("started") else 0
                reply = (f"{run['error']}(运行 {dur}s 后网络中断快速失败)"
                         + (f"\n\n--- 中断前的部分输出 ---\n{partial}" if partial else ""))
                chat_entry.update(text=reply, net_error=True)
            elif run["status"] == "error" and run.get("error") and reply != run["error"]:
                # 有半截输出的失败运行:把错误原因一并落进对话,避免只见输出不见错误
                reply = f"{reply}\n\n--- 运行以 error 结束 ---\n{run['error']}"
                chat_entry["text"] = reply
            append_chat(agent_id, run["project"], chat_entry)
            publish_run(run)
            try:
                # 诊断事件旁路(设置「高级→诊断数据」,modules/diagnostics.py):
                # 白名单字段本地落盘,错误消息模板化、项目名只存哈希;绝不出网
                from modules.diagnostics import record_run_event
                record_run_event(run)
            except Exception:
                pass
            # Agent 结束前即使漏掉 dispatch.py --confirm --sign，也不能让已解锁的
            # 人工闸门静默留在 DAG 中。这里只补建签字单，绝不自动改 gate state。
            try:
                await ensure_human_gate_approvals(run["project"], parent=run["id"])
            except Exception as e:  # noqa: BLE001
                print(f"[approval] 运行结束闸门核对失败(不影响运行回执):{e}", flush=True)
            if run.pop("_skill_followup", None):
                try:
                    await _prompt_skill_followup(run)
                except Exception as e:  # noqa: BLE001
                    print(f"[prompt_skill] 自动补读派发失败:{e}", flush=True)


def rel_path(p: str) -> str:
    try:
        return os.path.relpath(p, ROOT) if os.path.isabs(p) else p
    except Exception:
        return p


def handle_codex_event(run: dict, obj: dict):
    """解析 codex exec --json 的 JSONL 事件。"""
    t = obj.get("type")
    if t == "thread.started":
        run["session_id"] = obj.get("thread_id")
    elif t in ("item.started", "item.completed"):
        item = obj.get("item") or {}
        it = item.get("type")
        if it == "agent_message" and t == "item.completed":
            txt = item.get("text") or ""
            if txt:
                run["text"] = run.get("text", "") + txt
                run["result"] = txt          # codex 无独立 result 事件,取最后一条 agent_message
                HUB.publish({"type": "text", "run_id": run["id"],
                             "agent": run["agent"], "text": txt})
        elif it == "command_execution" and t == "item.started":
            desc = "⚙ $ " + (item.get("command") or "")[:160]
            run.setdefault("activity", []).append(desc)
            HUB.publish({"type": "tool", "run_id": run["id"],
                         "agent": run["agent"], "desc": desc})
            publish_run(run)
        elif it == "file_change" and t == "item.completed":
            for ch in item.get("changes", []):
                fp = rel_path(ch.get("path") or "")
                if not fp:
                    continue
                run.setdefault("files", []).append(fp)
                run.setdefault("activity", []).append(f"✎ {ch.get('kind', 'edit')}: {fp}")
                HUB.publish({"type": "file", "run_id": run["id"],
                             "agent": run["agent"], "path": fp})
            publish_run(run)
        elif it in ("web_search", "mcp_tool_call") and t == "item.started":
            desc = f"🔍 {it}: {item.get('query') or item.get('tool') or ''}"[:160]
            run.setdefault("activity", []).append(desc)
            HUB.publish({"type": "tool", "run_id": run["id"],
                         "agent": run["agent"], "desc": desc})
            publish_run(run)
    elif t == "turn.completed":
        u = obj.get("usage") or {}
        run["tokens"] = (run.get("tokens") or 0) + \
            (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
    elif t in ("turn.failed", "error"):
        run["error"] = str(obj.get("error") or obj.get("message") or obj)[:500]


def handle_kimi_event(run: dict, obj: dict):
    """解析 kimi -p --output-format stream-json 的 JSONL 事件。

    事件形态(实测 0.26):assistant 文本 {"role":"assistant","content":...};
    工具调用 {"role":"assistant","tool_calls":[{"function":{"name",...,"arguments":<JSON字符串>}}]};
    末尾 meta {"role":"meta","type":"session.resume_hint","session_id":...}。"""
    role_ = obj.get("role")
    if role_ == "assistant":
        # content 实测为字符串;若未来版本改成 claude 式 blocks 列表,跳过而非让整个 run 报错
        txt = obj.get("content")
        if isinstance(txt, str) and txt:
            run["text"] = run.get("text", "") + txt
            run["result"] = txt          # kimi 无独立 result 事件,取最后一条 assistant 文本
            HUB.publish({"type": "text", "run_id": run["id"],
                         "agent": run["agent"], "text": txt})
        for tc in obj.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                inp = json.loads(fn.get("arguments") or "{}")
            except Exception:
                inp = {}
            desc, fp = tool_summary(fn.get("name", "?"), inp)
            run.setdefault("activity", []).append(desc)
            if fp:
                run.setdefault("files", []).append(fp)
                HUB.publish({"type": "file", "run_id": run["id"],
                             "agent": run["agent"], "path": fp})
            HUB.publish({"type": "tool", "run_id": run["id"],
                         "agent": run["agent"], "desc": desc})
            publish_run(run)
    elif role_ == "meta" and obj.get("type") == "session.resume_hint":
        run["session_id"] = obj.get("session_id")


def _pi_message_text(message: dict) -> str:
    """Extract visible assistant text from a pi message."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(str(block.get("text") or "") for block in content
                   if isinstance(block, dict) and block.get("type") == "text")


def handle_pi_event(run: dict, obj: dict):
    """解析 pi --mode json 的 JsonAgentSessionEvent JSONL 事件。"""
    event = obj.get("type")
    if event == "session":
        run["session_id"] = obj.get("id")
        return
    if event == "message_start" and (obj.get("message") or {}).get("role") == "assistant":
        run["_pi_message_text"] = ""
        return
    if event == "message_update":
        update = obj.get("assistantMessageEvent") or {}
        if update.get("type") == "text_delta":
            delta = update.get("delta") or ""
            if delta:
                run["_pi_message_text"] = run.get("_pi_message_text", "") + delta
                run["text"] = run.get("text", "") + delta
                HUB.publish({"type": "text", "run_id": run["id"],
                             "agent": run["agent"], "text": delta})
        return
    if event == "message_end":
        message = obj.get("message") or {}
        if message.get("role") != "assistant":
            return
        text = _pi_message_text(message)
        streamed = run.get("_pi_message_text", "")
        if text:
            # JSON mode normally emits deltas; publish only a missing suffix (or the
            # full message for implementations that emit message_end only).
            missing = text[len(streamed):] if text.startswith(streamed) else ("" if streamed else text)
            if missing:
                run["text"] = run.get("text", "") + missing
                HUB.publish({"type": "text", "run_id": run["id"],
                             "agent": run["agent"], "text": missing})
            run["result"] = text
        usage = message.get("usage") or {}
        total = usage.get("totalTokens")
        if not isinstance(total, (int, float)):
            total = sum(usage.get(k) or 0 for k in ("input", "output", "cacheRead", "cacheWrite"))
        run["tokens"] = (run.get("tokens") or 0) + int(total or 0)
        if message.get("stopReason") in ("error", "aborted"):
            reason = message.get("stopReason")
            run["error"] = str(message.get("errorMessage") or f"pi request {reason}")[:500]
        return
    if event == "tool_execution_start":
        desc, fp = tool_summary(str(obj.get("toolName") or "?"), obj.get("args") or {})
        run.setdefault("activity", []).append(desc)
        if fp:
            run.setdefault("files", []).append(fp)
            HUB.publish({"type": "file", "run_id": run["id"],
                         "agent": run["agent"], "path": fp})
        HUB.publish({"type": "tool", "run_id": run["id"],
                     "agent": run["agent"], "desc": desc})
        publish_run(run)
        return
    if event == "error":
        run["error"] = str(obj.get("error") or obj.get("message") or obj)[:500]


def handle_opencode_event(run: dict, obj: dict):
    """解析 opencode run --format json 的 JSONL 事件(实测 1.18)。

    每条事件顶层带 sessionID;text 事件的 part.text 是完成的文本块(非增量);
    工具调用 tool_use 事件 part.tool=工具名、part.state.input=参数(文件参数是
    驼峰 filePath);step_finish 每次模型调用一条,part.tokens 计 token
    (total 已含 cache read/reasoning;cost 免费模型恒为 0,不采)。"""
    sid = obj.get("sessionID")
    if sid:
        run["session_id"] = sid
    t = obj.get("type")
    part = obj.get("part") or {}
    if t == "text":
        txt = part.get("text") or ""
        if txt:
            run["text"] = run.get("text", "") + txt
            run["result"] = txt          # opencode 无独立 result 事件,取最后一个文本块
            HUB.publish({"type": "text", "run_id": run["id"],
                         "agent": run["agent"], "text": txt})
    elif t == "tool_use":
        inp = (part.get("state") or {}).get("input") or {}
        if isinstance(inp.get("filePath"), str) and "file_path" not in inp:
            inp = {**inp, "file_path": inp["filePath"]}
        desc, fp = tool_summary(str(part.get("tool") or "?"), inp)
        run.setdefault("activity", []).append(desc)
        if fp:
            run.setdefault("files", []).append(fp)
            HUB.publish({"type": "file", "run_id": run["id"],
                         "agent": run["agent"], "path": fp})
        HUB.publish({"type": "tool", "run_id": run["id"],
                     "agent": run["agent"], "desc": desc})
        publish_run(run)
    elif t == "step_finish":
        tok = part.get("tokens") or {}
        total = tok.get("total")
        if not isinstance(total, (int, float)):
            cache = tok.get("cache") or {}
            total = sum(int(v or 0) for v in (
                tok.get("input"), tok.get("output"), tok.get("reasoning"),
                cache.get("read"), cache.get("write")))
        run["tokens"] = (run.get("tokens") or 0) + int(total or 0)
    elif t == "error":
        run["error"] = str(obj.get("error") or part.get("error") or obj)[:500]


# Grok Build 内置工具名 → claude 同义工具名(tool_summary 按 claude 命名归类活动/产物文件)
GROK_TOOL_ALIASES = {"write": "Write", "search_replace": "Edit", "read_file": "Read",
                     "run_terminal_command": "Bash", "grep": "Grep",
                     "spawn_subagent": "Task"}


def handle_grok_event(run: dict, obj: dict):
    """解析 grok --output-format streaming-messages-json 的 JSONL 事件(实测 1.0.5)。

    与 claude stream-json 同构:system/init.session_id、assistant.message 的
    content(text/tool_use 块)与 usage(input/output/cache_read/cache_creation)、
    result 的 result/usage/num_turns/total_cost_usd/session_id;只有内置工具命名
    不同(write/search_replace/read_file/run_terminal_command,路径参数
    read_file.target_file、list_dir.target_directory),先归一成 claude 口径再交给
    claude 处理器,用量统计(_parse_run_usage)也按 claude 分支走。"""
    if obj.get("type") == "assistant":
        message = obj.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            blocks = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    inp = dict(c.get("input") or {})
                    for key in ("target_file", "target_directory"):
                        if isinstance(inp.get(key), str) and "file_path" not in inp:
                            inp["file_path"] = inp[key]
                    name = str(c.get("name") or "?")
                    c = {**c, "name": GROK_TOOL_ALIASES.get(name, name), "input": inp}
                blocks.append(c)
            obj = {**obj, "message": {**message, "content": blocks}}
    handle_claude_event(run, obj)


def handle_deepagents_event(run: dict, obj: dict):
    """解析 deepagents_runner 的 JSONL 事件。"""
    t = obj.get("type")
    if t == "session":
        run["session_id"] = obj.get("session_id")
    elif t == "text":
        txt = obj.get("text") or ""
        if txt:
            run["text"] = run.get("text", "") + txt
            HUB.publish({"type": "text", "run_id": run["id"],
                         "agent": run["agent"], "text": txt})
    elif t == "notice":   # runner 自身提示(如撞 recursion_limit 自动续跑),不计入产出文本
        txt = obj.get("text") or ""
        if txt:
            run.setdefault("activity", []).append(txt)
            HUB.publish({"type": "tool", "run_id": run["id"],
                         "agent": run["agent"], "desc": txt})
            publish_run(run)
    elif t == "tool":
        name = obj.get("name", "?")
        inp = obj.get("input") or {}
        fp = inp.get("file_path") or inp.get("path")
        if fp:
            fp = rel_path(fp)
        if name in ("write_file", "edit_file"):
            desc = f"✎ {name}: {fp}"
        elif name == "execute":
            desc = "⚙ $ " + str(inp.get("command") or "")[:160]
            fp = None
        elif name in ("read_file", "ls", "glob", "grep"):
            desc = f"📖 {name}: {fp or inp.get('pattern', '')}"
            fp = None
        else:
            desc = f"🔧 {name}"
            fp = None
        run.setdefault("activity", []).append(desc)
        if fp:
            run.setdefault("files", []).append(fp)
            HUB.publish({"type": "file", "run_id": run["id"],
                         "agent": run["agent"], "path": fp})
        HUB.publish({"type": "tool", "run_id": run["id"],
                     "agent": run["agent"], "desc": desc})
        publish_run(run)
    elif t == "usage":
        run["tokens"] = (run.get("tokens") or 0) + \
            (obj.get("input_tokens") or 0) + (obj.get("output_tokens") or 0)
    elif t == "result":
        run["result"] = obj.get("text") or ""
    elif t == "error":
        run["error"] = str(obj.get("message") or "")[:500]


def handle_claude_event(run: dict, obj: dict):
    t = obj.get("type")
    if t == "system" and obj.get("subtype") == "init":
        run["session_id"] = obj.get("session_id")
    elif t == "system" and obj.get("subtype") == "api_retry":
        # CLI 将要退避重试一次 API 调用。error_status 为 null = 连接层错误(ECONNRESET/
        # 超时,没拿到 HTTP 响应):超过容忍次数即标记 net_error,由运行循环杀进程快速失败。
        # 有 HTTP 状态的(429/529/5xx)是服务端瞬时故障,仍交给 CLI 自己重试。
        if obj.get("error_status") is None:
            n = run["net_retries"] = run.get("net_retries", 0) + 1
            if n > NET_RETRY_LIMIT and not run.get("net_error"):
                run["net_error"] = True
                run["error"] = (f"{NET_ERROR_MSG}(CLI 报 api_retry 第 {n} 次,"
                                f"错误 {obj.get('error') or 'unknown'})")[:500]
                HUB.publish({"type": "text", "run_id": run["id"],
                             "agent": run["agent"], "text": "\n" + run["error"]})
    elif t == "assistant":
        for c in (obj.get("message") or {}).get("content", []):
            if c.get("type") == "text" and c.get("text"):
                run["text"] = run.get("text", "") + c["text"]
                HUB.publish({"type": "text", "run_id": run["id"],
                             "agent": run["agent"], "text": c["text"]})
            elif c.get("type") == "tool_use":
                desc, fp = tool_summary(c.get("name", "?"), c.get("input") or {})
                run.setdefault("activity", []).append(desc)
                if fp:
                    run.setdefault("files", []).append(fp)
                    HUB.publish({"type": "file", "run_id": run["id"],
                                 "agent": run["agent"], "path": fp})
                HUB.publish({"type": "tool", "run_id": run["id"],
                             "agent": run["agent"], "desc": desc})
                publish_run(run)
    elif t == "result":
        if obj.get("is_error"):
            # CLI 以 API 错误收尾(重试耗尽/未授权等)时 result 正文是错误文案
            # ("API Error: Connection dropped (ECONNRESET)"),不能当产出:留空 result
            # 让退出码判定为失败,错误文案进 error(网络快速失败已写的 error 优先)
            run["error"] = run.get("error") or str(obj.get("result") or "API error")[:500]
        else:
            run["result"] = obj.get("result") or ""
        run["session_id"] = obj.get("session_id") or run.get("session_id")
        run["cost"] = round(obj.get("total_cost_usd") or 0, 4)
        run["turns"] = obj.get("num_turns")

# ---------------- 预览数据(人物/场景/分镜预览) ----------------
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff")
VIDEO_EXTS = (".mp4", ".webm", ".mov")
AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".flac", ".ogg")


# ---------------- 手绘分镜(手机扫码为生成组绘制空间线稿) ----------------
# 流程:桌面在分镜预览页对某组发起会话 → 手机扫码打开 /draw/<token> 全屏画布 →
# 提交线稿+文字说明 → 落盘 assets/sketches/epNN/grpNNN/(仅存档,不入 refs 不注入
# prompt)→ 后台以线稿为构图底、既有组参考图为形象锚、按项目风格(bible/style.json)
# 生成一张成图 → 成图落 assets/uploads/ 并加入组 refs,SSE sketchgen 事件通知桌面。
DRAW_SESSIONS: dict[str, dict] = {}      # token -> {project, ep, grp, expires}
DRAW_TTL_S = 1800


def _lan_ip() -> str:
    """取本机真实局域网 IP(手机扫码用)。开代理(Surge/Clash 增强模式 TUN)时默认路由
    走虚拟网卡,朝公网探测会拿到 Fake-IP 段地址(如 198.18.0.1)——先朝私网段探测
    (TUN 通常对局域网段直连不接管),并过滤虚拟网卡常用段;VIDEOAGENTS_LAN_IP 可强制指定。"""
    import ipaddress
    import socket
    override = os.environ.get("VIDEOAGENTS_LAN_IP", "").strip()
    if override:
        return override

    def real_lan(ip: str) -> bool:
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if not a.is_private or a.is_loopback or a.is_link_local:
            return False
        # 代理/VPN 虚拟网卡常用段:Fake-IP 198.18.0.0/15、CGNAT(Tailscale 等)100.64.0.0/10
        return not (a in ipaddress.ip_network("198.18.0.0/15")
                    or a in ipaddress.ip_network("100.64.0.0/10"))

    cands = []
    for probe in ("192.168.255.255", "10.255.255.255", "172.31.255.255", "8.8.8.8"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((probe, 80))          # UDP connect 不发包,仅查路由表选源地址
            cands.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass
    try:                                    # 主机名解析兜底(macOS 通常解析到局域网地址)
        cands += [i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except Exception:
        pass
    return (next((ip for ip in cands if real_lan(ip)), None)
            or next((ip for ip in cands if not ip.startswith("127.")), "127.0.0.1"))


def _qr_svg(data: str) -> str:
    import io

    import qrcode
    import qrcode.image.svg
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=12)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def _draw_session(token: str) -> dict:
    s = DRAW_SESSIONS.get(token)
    if not s or s["expires"] < time.time():
        DRAW_SESSIONS.pop(token, None)
        raise ServiceError(404, "Sketch session not found or expired; start a new one from the storyboard preview page")
    return s


def _grp_prompt_path(project: str, ep: str, grp: str) -> Path:
    return PROJECTS_DIR / project / "assets" / "prompts" / ep / f"{grp}.json"


def _sketch_dir(project: str, ep: str, grp: str) -> Path:
    return PROJECTS_DIR / project / "assets" / "sketches" / ep / grp


def _sketch_list(project: str, ep: str, grp: str) -> list[dict]:
    d = _sketch_dir(project, ep, grp)
    out = []
    for p in sorted(d.glob("sketch_*.png")):
        meta = _read_json_safe(p.with_suffix(".json")) or {}
        out.append({"name": p.name, "text": meta.get("text", ""),
                    "image_n": meta.get("image_n"),
                    "url": f"/projects/{project}/assets/sketches/{ep}/{grp}/{p.name}"
                           f"?v={int(p.stat().st_mtime)}"})
    return out


async def api_draw_session(body: dict):
    """桌面发起手绘会话,返回手机页 URL 与二维码 SVG。"""
    project = safe_slug(body.get("project") or "")
    ep = re.sub(r"[^\w\-]", "", body.get("ep") or "")
    grp = re.sub(r"[^\w\-]", "", body.get("grp") or "")
    if not _grp_prompt_path(project, ep, grp).is_file():
        raise ServiceError(404, f"Group prompt not found: {project}/{ep}/{grp}")
    token = uuid.uuid4().hex
    DRAW_SESSIONS[token] = {"project": project, "ep": ep, "grp": grp,
                            "expires": time.time() + DRAW_TTL_S}
    url = f"http://{_lan_ip()}:{PUBLIC_PORT}/draw/{token}"
    return {"token": token, "url": url, "qr_svg": _qr_svg(url), "ttl_s": DRAW_TTL_S}


async def api_draw_info(token: str):
    s = _draw_session(token)
    ps = load_project_settings(s["project"])
    aspect, _, _ = resolve_output(ps)
    return {"project": s["project"], "ep": s["ep"], "grp": s["grp"], "aspect": aspect}


MAX_SKETCH_REFS = 9   # 方舟多参考图上限(Seedance 2.0 口径;项目可经「视频模型设置」调整)


def max_group_ref_images(project: str) -> int:
    """每组参考图数量上限:项目「视频模型设置」max_ref_images(Seedance 2.5 最高 30),
    读不到回落 MAX_SKETCH_REFS=9(Seedance 2.0 口径)。"""
    try:
        sg = load_project_settings(project).get("shot_group") or {}
        return max(0, min(30, int(sg.get("max_ref_images", MAX_SKETCH_REFS))))
    except Exception:
        return MAX_SKETCH_REFS



# ---------------- 组级视频模型/提示词技能覆盖(分镜预览「🎛 模型」按钮,2026-08-30) ----------------
# 动机:参考图上限随模型走(Seedance 2.0 ≤9、2.5 ≤30),某组必挂参考图超过全局模型上限时,
# 用户可只给这一组换更高上限的模型(渠道不可换:仍是「生成模型」页/视频工位覆盖所定的渠道,
# 只在该渠道的模型目录里选),并为该组单独指定提示词技能(默认跟随全局;auto=按本组模型解析)。
# 存储:<project>/assets/group_settings/<ep>/<grp>.json(用户所有,不随 prompt 重出丢失):
#   {"video_model": "<id>"|"", "provider": "<存 override 时的渠道>",
#    "prompt_skill": {"mode": "global"|"auto"|"manual"|"off", "skill_id": ""},
#    "effective": {...运行时解析快照(机检 prompt_skill_applied 与 genmedia 对照)...}}
# 消费:genmedia video/info 按 --group 或 --output 路径(clips/epNN/grpNNN.mp4)自动找到该文件
# 并改用组模型;prompt 工位系统提示词列出全部覆盖组;预览页组卡按组生效上限判 refs 超限。
GROUP_SKILL_MODES = ("global", "auto", "manual", "off")
REF_CAP_HARD_MAX = 30   # 任何视频模型的参考图上限极值(Seedance 2.5);手动加图/手绘生成以此兜底
# 各渠道可选视频模型目录(与 apps/web/static/models.html 的 VOLC_MODELS/BP_VIDEO_MODELS/
# FAL_VIDEO_MODELS/MINIMAX_VIDEO_MODELS/OR_RECOMMENDED.video 同步维护;comfyui 无模型 id,组级不可覆盖)
VIDEO_MODEL_CATALOG: dict[str, list[tuple[str, str]]] = {
    "volcengine": [
        ("doubao-seedance-2-5-260628", "Seedance 2.5(单段 30s,参考 30 图/10 视频/10 音频,480p/720p)"),
        ("doubao-seedance-2-0-260128", "Seedance 2.0(音画同生,最高 4K,参考 9 图/3 视频/3 音频)"),
        ("doubao-seedance-2-0-fast-260128", "Seedance 2.0 Fast(快速版,480p/720p)"),
        ("doubao-seedance-2-0-mini-260615", "Seedance 2.0 Mini(轻量版,480p/720p)"),
        ("doubao-seedance-1-5-pro-251215", "Seedance 1.5 Pro(即将下线)"),
        ("doubao-seedance-1-0-pro-250528", "Seedance 1.0 Pro(文/图生视频)"),
        ("doubao-seedance-1-0-pro-fast-251015", "Seedance 1.0 Pro Fast(文/图生视频)"),
    ],
    "byteplus": [
        ("dreamina-seedance-2-5-260628", "Seedance 2.5(单段 30s,参考 30 图/10 视频/10 音频,480p/720p)"),
        ("dreamina-seedance-2-0-260128", "Seedance 2.0(音画同生,最高 4K,参考 9 图/3 视频/3 音频)"),
        ("dreamina-seedance-2-0-fast-260128", "Seedance 2.0 Fast(快速版,480p/720p)"),
        ("dreamina-seedance-2-0-mini-260615", "Seedance 2.0 Mini(轻量版,480p/720p)"),
        ("seedance-1-5-pro-251215", "Seedance 1.5 Pro"),
        ("seedance-1-0-pro-250528", "Seedance 1.0 Pro(文/图生视频)"),
        ("seedance-1-0-pro-fast-251015", "Seedance 1.0 Pro Fast(文/图生视频)"),
    ],
    "fal": [
        ("minimax/h3-max", "MiniMax H3 Max(Fal 托管;H3 后训练版,提示遵循更强)"),
        ("minimax/h3", "MiniMax H3(Fal 托管;首尾帧/多模态参考,480P/768P/2K/4K,参考合计 ≤12 件)"),
        ("minimax/h3-max-turbo", "MiniMax H3 Max Turbo(Fal 托管;速度优先版,仅文生/首尾帧,480P/768P,不支持参考素材)"),
        ("bytedance/seedance-2.5", "Seedance 2.5(Fal 托管;单段 4-30 秒,参考 30 图/10 视频/10 音频,480p/720p/1080p)"),
        ("bytedance/seedance-2.0", "Seedance 2.0(Fal 托管;音画同生,4-15 秒,参考 9 图/3 视频/3 音频,最高 4K)"),
        ("fal-ai/kling-video/v3/pro", "Kling 3.0 Pro(Fal 托管;首尾帧,3-15 秒,原生音频,不支持参考素材)"),
        ("fal-ai/kling-video/v3/standard", "Kling 3.0 Standard(Fal 托管;首尾帧,3-15 秒,原生音频,不支持参考素材)"),
    ],
    "minimax": [
        ("MiniMax-H3", "MiniMax H3(多模态生视频,768P/2K,4-15 秒)"),
    ],
    "openrouter": [
        ("bytedance/seedance-2.0", "Seedance 2.0(字节)"),
        ("bytedance/seedance-2.0-fast", "Seedance 2.0 Fast(字节)"),
        ("kwaivgi/kling-v3.0-pro", "Kling 3.0 Pro"),
        ("kwaivgi/kling-v3.0-std", "Kling 3.0 Standard"),
        ("openai/sora-2-pro", "Sora 2 Pro"),
        ("minimax/hailuo-2.3", "Hailuo 2.3(MiniMax)"),
        ("alibaba/wan-2.7", "Wan 2.7(阿里)"),
        ("google/veo-3.1", "Veo 3.1(Google)"),
        ("google/veo-3.1-fast", "Veo 3.1 Fast(Google)"),
    ],
}


def video_model_label(model: str, provider: str = "") -> str:
    """模型的短标签:目录里有则取括号前的名字(如 Seedance 2.5),否则原 id。"""
    for prov, rows in VIDEO_MODEL_CATALOG.items():
        if provider and prov != provider:
            continue
        for mid, label in rows:
            if mid == model:
                return label.split("(")[0]
    return model


def video_model_caps(model: str) -> dict | None:
    """模型侧硬限(与 genmedia 同口径):参考图/视频/音频数量与单段时长上限;未知模型返回 None
    (按项目「视频模型设置」执行)。"""
    # max_ref_video_s:参考视频总时长硬限(秒;genmedia 提交前按 2.0≤15.2/2.5≤30.2 预检)——
    # Seedance 官方口径;MiniMax H3 官方未见明示,按组时长上限 15s 同口径(2026-09-07 假定,待实测)
    if is_seedance25(model):
        return {"max_ref_images": 30, "max_ref_videos": 10, "max_ref_audios": 10, "max_group_s": 30,
                "max_ref_video_s": 30}
    if is_seedance20(model):
        return {"max_ref_images": 9, "max_ref_videos": 3, "max_ref_audios": 3, "max_group_s": 15,
                "max_ref_video_s": 15}
    if is_minimax_h3(model):
        return {"max_ref_images": 9, "max_ref_videos": 3, "max_ref_audios": 3, "max_group_s": 15,   # 2026-09-07:参考视频/音频各 3(白模参考视频可挂)
                "max_ref_video_s": 15}
    return None


_MEDIA_DUR_CACHE: dict[tuple[str, int, int], float | None] = {}


def media_duration_s(path: Path) -> float | None:
    """ffprobe 实测音视频时长(秒);按 (路径, mtime, size) 缓存,ffprobe 不可用/失败返回 None。
    预览页每次加载都要汇总各组参考视频总时长,靠缓存避免反复起 ffprobe。"""
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key in _MEDIA_DUR_CACHE:
        return _MEDIA_DUR_CACHE[key]
    dur = None
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, timeout=30)
        dur = float(out.stdout.strip().splitlines()[0])
    except Exception:
        dur = None
    if len(_MEDIA_DUR_CACHE) > 2000:
        _MEDIA_DUR_CACHE.clear()
    _MEDIA_DUR_CACHE[key] = dur
    return dur


def _grpsettings_path(project: str, ep: str, grp: str) -> Path:
    return PROJECTS_DIR / project / "assets" / "group_settings" / ep / f"{grp}.json"


def _grpsettings_get(project: str, ep: str, grp: str) -> dict:
    d = _read_json_safe(_grpsettings_path(project, ep, grp))
    return d if isinstance(d, dict) else {}


def _grpsettings_all(project: str) -> list[tuple[str, str, dict]]:
    """项目内全部组级设定文件 → [(ep, grp, dict)],按 ep/grp 排序。"""
    root = PROJECTS_DIR / safe_slug(project) / "assets" / "group_settings"
    out = []
    if root.is_dir():
        for f in sorted(root.glob("ep*/grp*.json")):
            d = _read_json_safe(f)
            if isinstance(d, dict):
                out.append((f.parent.name, f.stem, d))
    return out


def group_video_candidates(project: str, cfg: dict | None = None) -> dict:
    """本项目组级可选的视频模型:渠道固定为视频工位生效渠道,候选=该渠道目录 + 全局当前模型
    (自定义 id 不在目录时也列出)。comfyui 类无模型 id → overridable=False。"""
    cfg = cfg or load_genconfig()
    provider = active_video_provider(cfg, VIDEO_AGENT_ID)
    gmodel = effective_video_model(cfg)
    rows = [{"id": m, "label": lbl} for m, lbl in VIDEO_MODEL_CATALOG.get(provider, [])]
    if gmodel and gmodel not in {r["id"] for r in rows}:
        rows.insert(0, {"id": gmodel, "label": gmodel + "(当前全局,自定义)"})
    return {"provider": provider, "overridable": provider != "comfyui" and bool(gmodel),
            "global_model": gmodel,
            "global_label": video_model_label(gmodel, provider) if gmodel else _video_model_label(cfg),
            "candidates": rows}


def resolve_group_settings(project: str, ep: str, grp: str, cfg: dict | None = None,
                           gs: dict | None = None, proj_skill: dict | None = None) -> dict:
    """解析某组生效的视频模型与提示词技能(不落盘)。返回:
    provider / video_model / model_source(global|group)/ model_label / ref_cap(本组生效参考图上限)/
    caps / skill_id / skill_dir / skill_path / skill_mode(global|auto|manual|off)/ skill_source /
    reason / warning / overridden(存在任一组级覆盖)。"""
    cfg = cfg or load_genconfig()
    gs = gs if gs is not None else _grpsettings_get(project, ep, grp)
    cand = group_video_candidates(project, cfg)
    provider, gmodel = cand["provider"], cand["global_model"]
    warning = ""
    model, source = gmodel, "global"
    ov = str(gs.get("video_model") or "")
    if ov:
        if not cand["overridable"]:
            warning = f"组级视频模型 {ov} 未生效:当前渠道 {provider} 无模型 id(按工作流运行),按全局执行"
        elif gs.get("provider") and gs.get("provider") != provider:
            warning = (f"组级视频模型 {ov} 属渠道 {gs.get('provider')},当前视频渠道已改为 {provider},"
                       "该覆盖未生效(按全局执行);请重新为本组选模型或清除覆盖")
        else:
            model, source = ov, "group"
    caps = video_model_caps(model) if source == "group" else None
    sg = load_project_settings(project).get("shot_group") or {}
    ref_cap = (caps or {}).get("max_ref_images") if caps else None
    if ref_cap is None:
        ref_cap = max(0, min(REF_CAP_HARD_MAX, int(sg.get("max_ref_images", MAX_SKETCH_REFS))))
    # 参考视频上限(2026-09-07):个数同参考图口径(组级覆盖→模型硬限,否则项目「视频模型设置」);
    # 总时长项目设置里没有,按本组生效模型(含跟随全局)的硬限,未知模型/comfyui 类渠道 = None(不判)
    vref_cap = (caps or {}).get("max_ref_videos") if caps else None
    if vref_cap is None:
        vref_cap = max(0, int(sg.get("max_ref_videos", 3) or 0))
    mcaps = video_model_caps(model) if model else None
    vref_cap_s = (mcaps or {}).get("max_ref_video_s")
    if provider in ("comfyui", "runninghub"):
        vref_cap, vref_cap_s = 0, None   # 工作流方式渠道不支持 --ref-video(与 modules/whitebox_refs.video_budget 同口径)
    # 提示词技能
    ps = gs.get("prompt_skill") if isinstance(gs.get("prompt_skill"), dict) else {}
    smode = ps.get("mode") if ps.get("mode") in GROUP_SKILL_MODES else "global"
    if smode == "global" and source == "group":
        # 组换了模型却没指定技能:按本组模型自动解析,而不是套全局模型的技能(否则 2.5 组套 2.0 技能)
        smode_eff = "auto"
    else:
        smode_eff = smode
    proj_skill = proj_skill or resolve_prompt_skill(project, cfg)
    cands = {c["id"]: c for c in prompt_skill_candidates()}
    reason = ""
    if smode_eff == "global":
        sid, reason = proj_skill["skill_id"], proj_skill["reason"]
        if proj_skill.get("warning"):
            warning = (warning + " · " if warning else "") + proj_skill["warning"]
    elif smode_eff == "off":
        sid, reason = "", "user_skipped"
    elif smode_eff == "manual":
        sid = str(ps.get("skill_id") or "")
        if sid not in cands:
            warning = (warning + " · " if warning else "") + f"组指定的提示词技能 {sid or '(空)'} 未安装,本组按无技能处理"
            sid, reason = "", "missing"
    else:   # auto:按本组生效模型
        if provider == "comfyui":
            sid = PROMPT_SKILL_H3 if is_minimax_h3_active(cfg) else ""
        else:
            sid = auto_prompt_skill_for_model(model)
        if not sid:
            reason = "no_match"
            warning = (warning + " · " if warning else "") + f"本组视频模型 {model or '(未知)'} 没有对应的提示词技能"
    if sid and sid not in cands:
        sid, reason = "", "missing"
    if sid and not project_skill_enabled(sid, project):
        warning = (warning + " · " if warning else "") + f"提示词技能 {cands[sid]['dir']} 未在项目技能中启用或已被全局禁用,本组按无技能处理"
        sid, reason = "", "disabled"
    c = cands.get(sid) or {}
    return {"provider": provider, "video_model": model, "model_source": source,
            "model_label": video_model_label(model, provider) if model else cand["global_label"],
            "global_model": gmodel, "ref_cap": ref_cap, "caps": caps,
            "vref_cap": vref_cap, "vref_cap_s": vref_cap_s,
            "skill_id": sid, "skill_dir": c.get("dir", ""), "skill_path": c.get("path", ""),
            "skill_mode": smode, "skill_source": "global" if smode_eff == "global" else "group",
            "reason": reason, "warning": warning,
            "overridden": bool(ov) or smode != "global"}


def group_ref_cap(project: str, ep: str, grp: str) -> int:
    """本组生效的参考图上限:组级覆盖了模型 → 该模型硬限;否则项目「视频模型设置」。"""
    try:
        return int(resolve_group_settings(project, ep, grp)["ref_cap"])
    except Exception:
        return max_group_ref_images(project)


def sync_group_settings_effective(project: str) -> list[dict]:
    """把各组解析结果快照进 group_settings/<ep>/<grp>.json 的 effective(仅变化时写盘)。
    快照供 code/prompt_skill_check.py 按组对照、genmedia 按组取模型(它只读 video_model/provider,
    快照仅作对照记录)。返回 [{ep, grp, ...resolved}]。"""
    project = safe_slug(project)
    cfg = load_genconfig()
    proj_skill = resolve_prompt_skill(project, cfg)
    out = []
    for ep, grp, gs in _grpsettings_all(project):
        r = resolve_group_settings(project, ep, grp, cfg, gs, proj_skill)
        eff = {"provider": r["provider"], "video_model": r["video_model"],
               "model_source": r["model_source"], "ref_cap": r["ref_cap"],
               "skill_id": r["skill_id"], "skill_mode": r["skill_mode"],
               "skill_source": r["skill_source"], "reason": r["reason"]}
        cur = gs.get("effective") if isinstance(gs.get("effective"), dict) else {}
        if {k: cur.get(k) for k in eff} != eff:
            gs["effective"] = eff | {"decided_at": datetime.now().isoformat(timespec="seconds")}
            atomic_write_json(_grpsettings_path(project, ep, grp), gs)
        out.append({"ep": ep, "grp": grp, **r})
    return out


def group_overrides_prompt(project: str, for_video_agent: bool = False) -> str:
    """系统提示词段:列出本项目存在组级覆盖的组(视频模型 / 提示词技能),prompt 与视频生成工位各一版。"""
    try:
        rows = [r for r in sync_group_settings_effective(project) if r["overridden"]]
    except Exception:
        rows = []
    if not rows:
        return ""
    lines = []
    for r in rows:
        caps = r.get("caps") or {}
        cap_txt = (f"参考图 ≤{caps['max_ref_images']} 张、参考视频 ≤{caps['max_ref_videos']} 个、"
                   f"参考音频 ≤{caps['max_ref_audios']} 段、组时长 ≤{caps['max_group_s']}s"
                   if caps else f"参考图 ≤{r['ref_cap']} 张(其余上限沿用项目设定)")
        model_txt = (f"视频模型 **{r['video_model']}**(组级覆盖,{cap_txt})" if r["model_source"] == "group"
                     else f"视频模型跟随全局 {r['video_model'] or r['model_label']}")
        skill_txt = (f"提示词技能 **{r['skill_dir']}**(id `{r['skill_id']}`,Skill 文件 {r['skill_path']})"
                     if r["skill_id"] else f"不套用提示词技能(reason={r['reason'] or 'no_match'})")
        warn = f";⚠️ {r['warning']}" if r.get("warning") else ""
        lines.append(f"- `{r['ep']}/{r['grp']}`:{model_txt};{skill_txt}{warn}")
    body = "\n".join(lines)
    if for_video_agent:
        return f"""

## 组级视频模型覆盖(用户在分镜预览「🎛 模型」按钮为个别组单独指定,当前已生效)
以下组不按全局模型生成——genmedia 会按 `--output assets/clips/epNN/grpNNN.mp4` 路径(或显式 `--group epNN/grpNNN`)自动读取组级设定并改用该模型,渠道不变;`python3 modules/genmedia.py info --group epNN/grpNNN` 可核对本组生效模型。参考素材数量与时长上限按该组模型的硬限执行(不再以项目「视频模型设置」为准),回执 meta 记录实际所用模型:
{body}"""
    return f"""

## 组级视频模型/提示词技能覆盖(用户在分镜预览「🎛 模型」按钮为个别组单独指定,当前已生效)
以下组不按项目级设定——写这些组的 video_prompt 时按本组的视频模型能力与技能执行:参考素材上限按本组模型硬限(而非项目「视频模型设置」),提示词技能按本组所列(须先 Read 该 SKILL.md,`skill_applied.id` 填本组技能 id;不套用时填 `{{"id": null, "reason": ...}}`),机检 `prompt_skill_applied` 会按组分别对照 `assets/group_settings/epNN/grpNNN.json` 的 effective 快照:
{body}"""


async def api_grpsettings_get(project: str, ep: str, grp: str):
    project = safe_slug(project)
    ep = re.sub(r"[^\w\-]", "", ep or "")
    grp = re.sub(r"[^\w\-]", "", grp or "")
    _proj_base(project)
    cfg = load_genconfig()
    gs = _grpsettings_get(project, ep, grp)
    proj_skill = resolve_prompt_skill(project, cfg)
    return {"project": project, "ep": ep, "grp": grp,
            "config": {"video_model": str(gs.get("video_model") or ""),
                       "prompt_skill": gs.get("prompt_skill") if isinstance(gs.get("prompt_skill"), dict)
                       else {"mode": "global", "skill_id": ""}},
            "resolved": resolve_group_settings(project, ep, grp, cfg, gs, proj_skill),
            "models": group_video_candidates(project, cfg),
            "skills": prompt_skill_candidates(),
            "project_skill": proj_skill,
            "project_ref_cap": max_group_ref_images(project)}


async def api_grpsettings_set(body: dict):
    """分镜预览「🎛 模型」弹窗:{project, ep, grp, video_model, prompt_skill:{mode, skill_id}}。
    video_model 空 + mode=global ⇒ 清除覆盖(删文件)。渠道不可选:模型只能取当前视频渠道目录。"""
    project = safe_slug((body or {}).get("project") or "")
    ep = re.sub(r"[^\w\-]", "", (body or {}).get("ep") or "")
    grp = re.sub(r"[^\w\-]", "", (body or {}).get("grp") or "")
    if not ep or not grp:
        raise ServiceError(400, "ep and grp are required")
    _proj_base(project)
    cfg = load_genconfig()
    cand = group_video_candidates(project, cfg)
    model = str((body or {}).get("video_model") or "")
    if model:
        if not cand["overridable"]:
            raise ServiceError(400, f"当前视频渠道 {cand['provider']} 按工作流运行、无模型 id,不支持组级切换模型")
        if model not in {c["id"] for c in cand["candidates"]}:
            raise ServiceError(400, f"模型 {model} 不在当前视频渠道 {cand['provider']} 的可选目录内(渠道不可切换)")
        if model == cand["global_model"]:
            model = ""   # 选了全局同款 = 跟随全局
    ps = (body or {}).get("prompt_skill") or {}
    if not isinstance(ps, dict):
        raise ServiceError(400, "prompt_skill must be an object")
    mode = str(ps.get("mode") or "global")
    if mode not in GROUP_SKILL_MODES:
        raise ServiceError(400, f"prompt_skill.mode must be one of {GROUP_SKILL_MODES}")
    sid = str(ps.get("skill_id") or "") if mode == "manual" else ""
    if mode == "manual" and sid not in {c["id"] for c in prompt_skill_candidates()}:
        raise ServiceError(400, f"prompt_skill.skill_id 不是 {PROMPT_AGENT_ID} 已安装的提示词技能: {sid or '(空)'}")
    path = _grpsettings_path(project, ep, grp)
    old = _grpsettings_get(project, ep, grp)
    if not model and mode == "global":
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, {"video_model": model, "provider": cand["provider"] if model else "",
                                 "prompt_skill": {"mode": mode, "skill_id": sid},
                                 "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    sync_group_settings_effective(project)
    out = await api_grpsettings_get(project, ep, grp)
    # 组 prompt 文件记一笔(存在时),方便回溯;组模型变了参考图上限也变,预览页据此重判超限
    pf = _grp_prompt_path(project, ep, grp)
    if pf.is_file():
        try:
            d = json.loads(pf.read_text())
            r = out["resolved"]
            d.setdefault("notes", []).append(
                f"用户在分镜预览设置组级覆盖:视频模型 {r['video_model'] or '(跟随全局)'}({r['model_source']}),"
                f"提示词技能 {r['skill_dir'] or '(不套用)'}({r['skill_mode']});本组参考图上限 {r['ref_cap']}。"
                "重出本组 prompt/视频时生效。由 storyboard service 自动补丁。")
            atomic_write_json(pf, d)
        except Exception:
            pass
    HUB.publish({"type": "group_settings", "project": project, "ep": ep, "grp": grp,
                 "resolved": out["resolved"], "changed": old != _grpsettings_get(project, ep, grp)})
    return out


SKETCHGEN_JOBS: dict[str, dict] = {}   # "project/ep/grp" -> 手绘生成任务状态(单机内存态)
SKETCHGEN_PROMPT_TMPL = (
    "Image 1 is a rough black-and-white hand-drawn layout sketch by the director. "
    "Generate one polished final frame that follows the sketch's spatial composition "
    "(subject placement, relative positions, framing) exactly; use the sketch ONLY for "
    "layout, never for art style or rendering. The remaining reference images are the "
    "project's official character/scene/prop designs — keep their identity, appearance "
    "and outfits strictly consistent.")


def _sketchgen_size(aspect: str) -> str:
    """按项目画幅算成图尺寸:成图会随重出作为视频参考图,按平台统一出图规格
    2560x1440 当量像素出图(非接口硬限);边长向上取 8 的倍数。"""
    try:
        rw, rh = (int(x) for x in aspect.split(":"))
    except Exception:
        rw, rh = 16, 9
    w = -(-int((3_686_400 * rw / rh) ** 0.5) // 8) * 8
    h = -(-(w * rh) // (rw * 8)) * 8
    return f"{w}x{h}"


def _sketchgen_worker(project: str, ep: str, grp: str, png: Path, text: str):
    """后台线程:genmedia 子进程生图(线稿+组 refs 作参考,项目风格串入 prompt),
    成图加入组 refs;进度经 SKETCHGEN_JOBS + SSE sketchgen 事件对外。"""
    job = SKETCHGEN_JOBS[f"{project}/{ep}/{grp}"]
    base = _proj_base(project)
    try:
        pf = _grp_prompt_path(project, ep, grp)
        d = json.loads(pf.read_text())
        refs = [str(png)] + [str(base / r) for r in (d.get("refs") or [])
                             if (base / r).is_file()][:MAX_SKETCH_REFS - 1]
        st = _read_json_safe(base / "bible" / "style.json") or {}
        prompt = SKETCHGEN_PROMPT_TMPL
        if text.strip():
            prompt += f" Director's note: {text.strip()}"
        style = str(st.get("style_anchor_string_en") or "").strip()
        if style:
            prompt += f" Overall visual style: {style}"
        aspect, _, _ = resolve_output(load_project_settings(project))
        out_dir = base / "assets" / "uploads" / ep / grp
        out_dir.mkdir(parents=True, exist_ok=True)
        out, i = out_dir / f"{png.stem}_final.png", 1
        while out.exists():
            out, i = out_dir / f"{png.stem}_final_{i}.png", i + 1
        cmd = [sys.executable, str(ROOT / "modules" / "genmedia.py"), "image",
               "--prompt", prompt, "--output", str(out),
               "--size", _sketchgen_size(aspect), "--ref", *refs]
        neg = str(st.get("negative_prompt_string_en") or "").strip()
        if neg:
            cmd += ["--negative", neg]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=str(ROOT))
        if r.returncode != 0 or not out.is_file():
            raise RuntimeError((r.stderr or r.stdout or "").strip()[-500:]
                               or f"genmedia exit {r.returncode}")
        ref = out.relative_to(base).as_posix()
        n = _grpref_append(pf, ref, "从手绘生成")
        job.update(status="done", ref=ref, refs=n,   # 直出 artifacts 路由(此链路不经 _artifact_urls 改写)
                   url=f"/api/v1/projects/{base.name}/artifacts/{ref}?v={int(out.stat().st_mtime)}")
    except Exception as e:
        job.update(status="failed", error=str(e)[:500])
    HUB.publish({"type": "sketchgen", "project": project, "ep": ep, "grp": grp,
                 "status": job["status"], "src": job.get("src", ""),
                 "error": job.get("error", "")})


async def api_sketchgen_status(project: str, ep: str, grp: str):
    """桌面轮询手绘生成任务状态(SSE 之外的兜底)。"""
    ep = re.sub(r"[^\w\-]", "", ep)
    grp = re.sub(r"[^\w\-]", "", grp)
    return SKETCHGEN_JOBS.get(f"{safe_slug(project)}/{ep}/{grp}") or {"status": "idle"}


async def api_draw_submit(token: str, body: dict):
    """手机提交线稿:PNG dataURL + 文字说明。线稿仅存档(不入 refs 不注入 prompt),
    后台以线稿为构图底、既有组参考图为形象锚、按项目风格生成一张成图并加入组 refs;
    进度经 SSE sketchgen 事件 + /storyboard/sketchgen 轮询对外。"""
    s = _draw_session(token)
    text = (body.get("text") or "").strip()
    img = body.get("image") or ""
    if not text:
        raise ServiceError(400, "A text note is required (describe the spatial relationship the sketch expresses)")
    if img.startswith("data:image/png;base64,"):
        img = img.split(",", 1)[1]
    try:
        raw = base64.b64decode(img)
        assert raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) < 8 * 1024 * 1024
    except Exception:
        raise ServiceError(400, "Image must be PNG (base64) and smaller than 8MB") from None
    project, ep, grp = s["project"], s["ep"], s["grp"]
    key = f"{project}/{ep}/{grp}"
    if (SKETCHGEN_JOBS.get(key) or {}).get("status") == "running":
        raise ServiceError(409, "A sketch-based generation is already running for this group; wait for it to finish")
    pd = json.loads(_grp_prompt_path(project, ep, grp).read_text())
    # 2026-08-30:不再按项目/组上限硬拦——refs 按实际需要加,超组生效上限由预览页黄条提示,
    # 用户决定删图或给本组换更高上限的模型(🎛 模型);只以模型极值 30 兜底
    if len(pd.get("refs") or []) >= REF_CAP_HARD_MAX:
        raise ServiceError(400, f"This group already has {REF_CAP_HARD_MAX} refs (the hard maximum of any video model); cannot add more")
    d = _sketch_dir(project, ep, grp)
    d.mkdir(parents=True, exist_ok=True)
    n = 1
    while (d / f"gen_src_{n:02d}.png").exists():
        n += 1
    png = d / f"gen_src_{n:02d}.png"
    png.write_bytes(raw)
    atomic_write_json(png.with_suffix(".json"), {
        "text": text, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "user_hand_drawn(api /draw-sessions)",
        "purpose": "sketchgen 构图底稿(不入 refs;成图另存 assets/uploads/ 并加入组 refs)"})
    SKETCHGEN_JOBS[key] = {"status": "running", "src": png.name,
                           "started_at": time.time(), "text": text[:120]}
    threading.Thread(target=_sketchgen_worker, args=(project, ep, grp, png, text),
                     daemon=True).start()
    HUB.publish({"type": "sketchgen", "project": project, "ep": ep, "grp": grp,
                 "status": "running", "src": png.name})
    return {"saved": png.name, "generating": True}


# ---------------- 组注释(用户对生成组的导演意图注释,注入组 prompt) ----------------
GRPNOTE_TMPL = " Director's note (user instruction, must follow): {text}"


def _grpnote_path(project: str, ep: str, grp: str) -> Path:
    return PROJECTS_DIR / project / "assets" / "notes" / ep / f"{grp}.json"


def _grpnote_get(project: str, ep: str, grp: str) -> dict:
    return _read_json_safe(_grpnote_path(project, ep, grp)) or {}


async def api_grpnote_set(body: dict):
    """保存/编辑/清空组注释:替换式注入组 prompt(旧句精确移除再注入新句);空文本=清除。"""
    project = safe_slug(body.get("project") or "")
    ep = re.sub(r"[^\w\-]", "", body.get("ep") or "")
    grp = re.sub(r"[^\w\-]", "", body.get("grp") or "")
    text = (body.get("text") or "").strip()
    pf = _grp_prompt_path(project, ep, grp)
    if not pf.is_file():
        raise ServiceError(404, f"Group prompt not found: {project}/{ep}/{grp}")
    d = json.loads(pf.read_text())
    vp = d["video_prompt"]
    old = _grpnote_get(project, ep, grp)
    if old.get("prompt_sentence") and old["prompt_sentence"] in vp:
        vp = vp.replace(old["prompt_sentence"], "", 1)
    np = _grpnote_path(project, ep, grp)
    if text:
        sent = GRPNOTE_TMPL.format(text=text) + ("" if text.endswith(("。", ".", "!", "！")) else ".")
        k = vp.rfind(" Global constraints:")
        vp = (vp[:k] + sent + vp[k:]) if k != -1 else vp + sent
        np.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(np, {"text": text, "prompt_sentence": sent,
                               "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        d.setdefault("notes", []).append(f"组注释更新(API storyboard notes,重出生效):{text[:80]}")
    else:
        np.unlink(missing_ok=True)
        d.setdefault("notes", []).append("组注释已清除并从 video_prompt 回滚(API storyboard notes)")
    d["video_prompt"] = vp
    d["video_prompt_word_count"] = len(vp.split())
    atomic_write_json(pf, d)
    return {"text": text}


async def api_grpprompt_set(body: dict):
    """保存用户手动编辑的组 video_prompt(分镜预览页 Prompt 弹窗「编辑→保存」,整段替换)。"""
    project = safe_slug(body.get("project") or "")
    ep = re.sub(r"[^\w\-]", "", body.get("ep") or "")
    grp = re.sub(r"[^\w\-]", "", body.get("grp") or "")
    text = (body.get("text") or "").strip()
    if not text:
        raise ServiceError(400, "video_prompt must not be empty")
    pf = _grp_prompt_path(project, ep, grp)
    if not pf.is_file():
        raise ServiceError(404, f"Group prompt not found: {project}/{ep}/{grp}")
    d = json.loads(pf.read_text())
    if text == (d.get("video_prompt") or ""):
        return {"changed": False}
    d["video_prompt"] = text
    d["video_prompt_word_count"] = len(text.split())
    d.setdefault("notes", []).append(
        f"用户在分镜预览页手动编辑 video_prompt(API storyboard prompt,"
        f"{time.strftime('%Y-%m-%d %H:%M:%S')});重出本组时以设计文件为准重写")
    atomic_write_json(pf, d)
    return {"changed": True}


async def api_sketches(project: str, ep: str, grp: str):
    """桌面查询某组已有线稿(仅本机,受 _lan_guard 保护)。"""
    return _sketch_list(safe_slug(project), re.sub(r"[^\w\-]", "", ep),
                        re.sub(r"[^\w\-]", "", grp))


async def api_sketch_delete(project: str, ep: str, grp: str, name: str):
    """删除线稿并回滚组 prompt 补丁(仅允许删 refs 末位的线稿,避免 [Image N] 错位)。"""
    project = safe_slug(project)
    ep = re.sub(r"[^\w\-]", "", ep)
    grp = re.sub(r"[^\w\-]", "", grp)
    name = re.sub(r"[^\w.\-]", "", name)
    png = _sketch_dir(project, ep, grp) / name
    meta = _read_json_safe(png.with_suffix(".json")) or {}
    pf = _grp_prompt_path(project, ep, grp)
    d = json.loads(pf.read_text())
    ref_rel, sent = meta.get("ref_path"), meta.get("prompt_sentence")
    if ref_rel and d.get("refs"):
        if d["refs"][-1] != ref_rel:
            raise ServiceError(400, "This sketch is not the last ref; delete later-injected sketches first (to keep [Image N] numbering aligned)")
        d["refs"].pop()
        if sent and sent in d["video_prompt"]:
            d["video_prompt"] = d["video_prompt"].replace(sent, "", 1)
            d["video_prompt_word_count"] = len(d["video_prompt"].split())
        d.setdefault("notes", []).append(f"手绘分镜删除并回滚:{ref_rel}")
        atomic_write_json(pf, d)
    png.unlink(missing_ok=True)
    png.with_suffix(".json").unlink(missing_ok=True)
    return {"deleted": name}


# ---------------- 组参考图(分镜预览页从资产库选图,追加进组 prompt 的 refs) ----------------
ASSET_REF_PREFIXES = ("assets/concepts/characters/",
                      "assets/concepts/scenes/",      # 含干净俯视图 layout_top.png / 九格图(2026-09-07 起俯视图直接作 ref,动线标注图退役)
                      "assets/concepts/props/",
                      "assets/concepts/creatures/")   # 生物/坐骑 sheet(2026-08-26)


def _grpref_append(pf: Path, ref: str, src: str) -> int:
    """向组 prompt 的 refs 追加一张参考图;返回追加后的 refs 数量。
    2026-08-30:不再按项目「视频模型设置」上限硬拦——按实际需要加,超过本组生效上限时预览页
    黄条提示,由用户决定删图或给本组换更高上限的视频模型(🎛 模型);只以模型极值 30 兜底。"""
    d = json.loads(pf.read_text())
    refs = d.setdefault("refs", [])
    if ref in refs:
        raise ServiceError(400, "This image is already in the group's refs")
    if len(refs) >= REF_CAP_HARD_MAX:
        raise ServiceError(400, f"This group already has {REF_CAP_HARD_MAX} refs (the hard maximum of any video model); cannot add more")
    refs.append(ref)
    d.setdefault("notes", []).append(
        f"用户{src}加入组参考图:{ref}(refs 第 {len(refs)} 张);重出本组时生效。由 storyboard service 自动补丁。")
    atomic_write_json(pf, d)
    return len(refs)


def _grpref_ctx(body_or_kw: dict) -> tuple[str, str, str, Path, Path]:
    """清洗 project/ep/grp 并定位组 prompt 文件(不存在=404)。"""
    project = safe_slug(body_or_kw.get("project") or "")
    ep = re.sub(r"[^\w\-]", "", body_or_kw.get("ep") or "")
    grp = re.sub(r"[^\w\-]", "", body_or_kw.get("grp") or "")
    base = _proj_base(project)
    pf = _grp_prompt_path(project, ep, grp)
    if not pf.is_file():
        raise ServiceError(404, f"Group prompt not found: {project}/{ep}/{grp}")
    return project, ep, grp, base, pf


async def api_grpref_add(body: dict):
    """把人物/场景/道具概念图加入组 prompt 的 refs(随重出作为参考图传给视频模型)。"""
    project, ep, grp, base, pf = _grpref_ctx(body)
    ref = (body.get("ref") or "").strip().lstrip("/")
    if ".." in ref.split("/") or not ref.startswith(ASSET_REF_PREFIXES):
        raise ServiceError(400, "ref must be an image under assets/concepts/(characters|scenes|props|creatures)/")
    target = (base / ref).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        raise ServiceError(400, "invalid ref path") from None
    if not target.is_file() or target.suffix.lower() not in IMG_EXTS:
        raise ServiceError(404, f"Ref image not found: {ref}")
    return {"ref": ref, "refs": _grpref_append(pf, ref, "从资产库")}


MAX_GRPREF_UPLOAD = 30 * 1024 * 1024
# 常见图片格式魔数(与 IMG_EXTS 对应;webp 的 RIFF 头另验 WEBP 标记)
_IMG_MAGIC = {b"\x89PNG\r\n\x1a\n": ".png", b"\xff\xd8\xff": ".jpg",
              b"RIFF": ".webp", b"GIF8": ".gif", b"BM": ".bmp",
              b"II*\x00": ".tif", b"MM\x00*": ".tif"}


async def api_grpref_upload(data: bytes, project: str, ep: str, grp: str, filename: str):
    """本地上传一张图片并直接加入组 refs:落盘 assets/uploads/<ep>/<grp>/,
    请求体即文件原始字节(与参考文件页上传同法,避免 multipart 依赖)。"""
    project, ep, grp, base, pf = _grpref_ctx(
        {"project": project, "ep": ep, "grp": grp})
    if not data:
        raise ServiceError(400, "empty upload body")
    if len(data) > MAX_GRPREF_UPLOAD:
        raise ServiceError(400, "file too large (>30MB)")
    ext = next((v for k, v in _IMG_MAGIC.items() if data.startswith(k)), None)
    if ext == ".webp" and data[8:12] != b"WEBP":
        ext = None
    if not ext:
        raise ServiceError(400, "file must be a png/jpg/webp/gif/bmp/tiff image")
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.splitext(filename)[0]).strip("._-")
    stem = re.sub(r"_{2,}", "_", stem)[:80] or "upload"
    d = base / "assets" / "uploads" / ep / grp
    d.mkdir(parents=True, exist_ok=True)
    p, i = d / f"{stem}{ext}", 1
    while p.exists():
        p, i = d / f"{stem}_{i}{ext}", i + 1
    p.write_bytes(data)
    ref = p.relative_to(base).as_posix()
    try:
        n = _grpref_append(pf, ref, "从本地上传")
    except ServiceError:
        p.unlink(missing_ok=True)
        raise
    return {"ref": ref, "refs": n,
            "url": f"/projects/{base.name}/{ref}?v={int(p.stat().st_mtime)}"}


def _grpref_user_added(d: dict, ref: str) -> bool:
    """该 ref 是否由用户经「添加参考图」加入(资产选图/本地上传)——
    以 _grpref_append 落的 note 锚「加入组参考图:<ref>(」判定;
    流水线锚点与手绘线稿注入不落此 note,判 False。"""
    return any(f"加入组参考图:{ref}(" in str(n) for n in d.get("notes") or [])


_IMG_REF_RE = re.compile(r" ?(?:\[Image (\d+)\]|@Image (\d+))")


def _grpref_renumber_prompt(vp: str, removed: int) -> tuple[str, int, int]:
    """删除 refs 第 removed 张(1-based)后重排正文里的 [Image N] / @Image N 引用:
    序号大于 removed 的减一;指向被删图的引用 token 整体删除(连同前导空格)。
    返回 (新正文, 重排数, 删除数)。一次 sub 完成,避免链式替换把 3→2 再当 2→1 处理。"""
    shifted = dropped = 0

    def _sub(m: re.Match) -> str:
        nonlocal shifted, dropped
        n = int(m.group(1) or m.group(2))
        if n == removed:
            dropped += 1
            return ""
        if n < removed:
            return m.group(0)
        shifted += 1
        tok = m.group(0)
        return tok.replace(str(n), str(n - 1), 1)

    return _IMG_REF_RE.sub(_sub, vp), shifted, dropped


async def api_grpref_delete(body: dict):
    """从组 refs 移除一张参考图(重出本组生效)。
    2026-09-07:不再限于用户手动加入的 ref——流水线锚点/概念图/线稿注入的也可删,但须带
    force=true(预览页弹窗对系统添加的图弹强确认后才带);删中间位置时正文 [Image N]/@Image N
    引用自动重排(指向被删图的引用整体移除),线稿另回滚注入句并删线稿文件;
    本地上传的图一并删除落盘文件。"""
    project, ep, grp, base, pf = _grpref_ctx(body)
    ref = (body.get("ref") or "").strip().lstrip("/")
    d = json.loads(pf.read_text())
    refs = d.get("refs") or []
    if ref not in refs:
        raise ServiceError(404, f"Ref not in this group's refs: {ref}")
    user_added = _grpref_user_added(d, ref)
    if not user_added and not body.get("force"):
        raise ServiceError(400, "This ref was injected by the pipeline/a sketch, not added by hand; "
                                "pass force=true to remove it (the preview dialog asks for confirmation)")
    i = refs.index(ref)
    refs.pop(i)
    vp = d.get("video_prompt") or ""
    extra = []
    # 线稿:按 meta.ref_path 反查,回滚其注入句并删线稿 png/json(与 api_sketch_delete 同法,
    # 但不受「须为末位」限制——序号错位由下方重排兜住)
    sdir = _sketch_dir(project, ep, grp)
    if sdir.is_dir():
        for mj in sorted(sdir.glob("sketch_*.json")):
            meta = _read_json_safe(mj) or {}
            if meta.get("ref_path") != ref:
                continue
            sent = meta.get("prompt_sentence") or ""
            # 早先删过前面的 ref 时,正文里这句的 [Image N] 已被重排,与 meta 存的原句对不上:
            # 再按线稿当前位置(第 i+1 张)改写序号试一次
            for cand in (sent, re.sub(r"\[Image \d+\]", f"[Image {i + 1}]", sent)):
                if cand and cand in vp:
                    vp = vp.replace(cand, "", 1)
                    break
            mj.with_suffix(".png").unlink(missing_ok=True)
            mj.unlink(missing_ok=True)
            extra.append(f"线稿 {mj.stem} 注入句已回滚、文件已删")
    shifted = dropped = 0
    if vp:
        vp, shifted, dropped = _grpref_renumber_prompt(vp, i + 1)
        if shifted or dropped:
            extra.append(f"正文引用重排 {shifted} 处、移除指向该图的引用 {dropped} 处")
        d["video_prompt"] = vp
        d["video_prompt_word_count"] = len(vp.split())
    d.setdefault("notes", []).append(
        f"用户移除组参考图:{ref}(原 refs 第 {i + 1} 张"
        f"{'' if user_added else ',流水线/线稿注入'}"
        f"{';' + ';'.join(extra) if extra else ''});重出本组时生效。由 storyboard service 自动补丁。")
    atomic_write_json(pf, d)
    if ref.startswith(f"assets/uploads/{ep}/{grp}/"):
        p = (base / ref).resolve()
        try:
            p.relative_to(base.resolve())
        except ValueError:
            p = None
        if p:
            p.unlink(missing_ok=True)
    return {"deleted": ref, "refs": len(refs), "user_added": user_added,
            "prompt_refs_shifted": shifted, "prompt_refs_dropped": dropped}


# ---------------- 组参考视频(分镜预览「🎬 视频」弹窗,2026-09-07) ----------------
# 组 prompt json 的 video_refs(与 refs/audio_refs 同级;video-generation 按序传 --ref-video,正文以 [Video N] 引用):
# 来源三类——白模 camera.mp4/top.mp4(code/sync_whitebox_refs.py 注入)、前组尾段 .continuation.mp4
# (modules/continuity_refs 注入)、用户本地上传(assets/uploads/<ep>/<grp>/,note 锚「加入组参考视频:<ref>(」)。
# 总时长按本组生效模型硬限判超(resolve_group_settings.vref_cap_s):超限只提示,由用户决定删视频或「🎛 模型」换模型;
# genmedia 提交前另有同口径硬校验(参考视频总时长 2.0≤15.2s / 2.5≤30.2s)兜底。
MAX_GRPVREF_UPLOAD = 45 * 1024 * 1024   # 与 genmedia.MAX_VIDEOIN_BYTES 同口径(方舟参考视频单文件上限)
_VID_REF_RE = re.compile(r" ?(?:\[Video (\d+)\]|@Video (\d+))(?!\d)")
_CONT_CLAUSE_RE = re.compile(r"\s*Continuation reference:.*?End continuation reference\.\s*", re.S)


def _grpvref_user_added(d: dict, ref: str) -> bool:
    return any(f"加入组参考视频:{ref}(" in str(n) for n in d.get("notes") or [])


def _grpvref_kind(ref: str) -> str:
    if "/whitebox/" in ref:
        return "whitebox"
    if ref.endswith(".continuation.mp4"):
        return "continuation"
    if "/uploads/" in ref:
        return "upload"
    return "other"


def _group_video_refs(base: Path, ep: str, gid: str, pd: dict, gres: dict | None) -> dict:
    """本组参考视频清单 + 总时长 + 上限判定(预览组卡与「🎬 视频」弹窗共用)。"""
    rows, total, unknown = [], 0.0, 0
    for i, r in enumerate(pd.get("video_refs") or [], 1):
        if not isinstance(r, str):
            continue
        f = base / r
        row = {"ref": r, "idx": i, "name": "/".join(r.split("/")[-2:]), "kind": _grpvref_kind(r),
               "user_added": _grpvref_user_added(pd, r), "missing": not f.is_file(),
               "url": None, "duration_s": None, "size": None}
        if f.is_file():
            st = f.stat()
            row["url"] = f"/projects/{base.name}/{r}?v={int(st.st_mtime)}"
            row["size"] = st.st_size
            d = media_duration_s(f)
            row["duration_s"] = round(d, 2) if d is not None else None
            if d is None:
                unknown += 1
            else:
                total += d
        rows.append(row)
    cap_n = gres.get("vref_cap") if gres else None
    cap_s = gres.get("vref_cap_s") if gres else None
    model_label = (gres or {}).get("model_label") or ""
    unsupported = bool(rows) and cap_n == 0
    over_n = cap_n is not None and not unsupported and len(rows) > cap_n
    over_s = cap_s is not None and total > cap_s + 0.05
    reasons = []   # 中文原因串给 agent/日志;前端按下方三个布尔位自行拼多语言文案
    if unsupported:
        reasons.append("本组生效渠道/模型不支持参考视频")
    if over_n:
        reasons.append(f"参考视频 {len(rows)} 个超过本组生效上限 {cap_n} 个")
    if over_s:
        reasons.append(f"参考视频总时长 {total:.1f}s 超过本组生效模型硬限 {cap_s:g}s")
    return {"video_refs": rows, "vrefs_total_s": round(total, 2), "vrefs_unknown": unknown,
            "vrefs_cap": cap_n, "vrefs_cap_s": cap_s, "vrefs_model_label": model_label,
            "vrefs_over_n": over_n, "vrefs_over_s": over_s, "vrefs_unsupported": unsupported,
            "vrefs_blocked": bool(reasons),
            "vrefs_blocked_reason": ("；".join(reasons) + (f"({model_label})" if model_label else "")) if reasons else ""}


def _grpvref_summary(project: str, ep: str, grp: str, base: Path, pf: Path) -> dict:
    pd = _read_json_safe(pf) or {}
    try:
        gres = resolve_group_settings(project, ep, grp)
    except Exception:
        gres = None
    return {"project": project, "ep": ep, "grp": grp,
            "model_source": (gres or {}).get("model_source"),
            **_group_video_refs(base, ep, grp, pd, gres)}


async def api_grpvref_list(project: str, ep: str, grp: str):
    project, ep, grp, base, pf = _grpref_ctx({"project": project, "ep": ep, "grp": grp})
    return _grpvref_summary(project, ep, grp, base, pf)


def _video_ext_by_magic(data: bytes) -> str | None:
    """mp4/mov(ISO BMFF:offset 4 为 ftyp)、webm/mkv(EBML 头)——与 VIDEO_EXTS 对应,mkv 归 webm 外拒收。"""
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        return ".mov" if brand in (b"qt  ",) else ".mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return ".webm" if b"webm" in data[:64] else None
    return None


async def api_grpvref_upload(data: bytes, project: str, ep: str, grp: str, filename: str):
    """本地上传一段参考视频并直接加入组 video_refs:落盘 assets/uploads/<ep>/<grp>/(与参考图上传同目录),
    请求体即文件原始字节。不按上限硬拦(与参考图同策略):超限由弹窗/组卡黄条提示,用户决定删或换模型。"""
    project, ep, grp, base, pf = _grpref_ctx({"project": project, "ep": ep, "grp": grp})
    if not data:
        raise ServiceError(400, "empty upload body")
    if len(data) > MAX_GRPVREF_UPLOAD:
        raise ServiceError(400, "file too large (>45MB, the Ark reference_video per-file limit); compress it first")
    ext = _video_ext_by_magic(data)
    if not ext:
        raise ServiceError(400, "file must be an mp4/mov/webm video")
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.splitext(filename)[0]).strip("._-")
    stem = re.sub(r"_{2,}", "_", stem)[:80] or "upload"
    d = base / "assets" / "uploads" / ep / grp
    d.mkdir(parents=True, exist_ok=True)
    p, i = d / f"{stem}{ext}", 1
    while p.exists():
        p, i = d / f"{stem}_{i}{ext}", i + 1
    p.write_bytes(data)
    ref = p.relative_to(base).as_posix()
    dur = media_duration_s(p)
    if dur is not None and dur < 0.5:
        p.unlink(missing_ok=True)
        raise ServiceError(400, "video too short or unreadable (ffprobe duration < 0.5s)")
    pj = json.loads(pf.read_text())
    vrefs = pj.setdefault("video_refs", [])
    if ref in vrefs:
        p.unlink(missing_ok=True)
        raise ServiceError(400, "This video is already in the group's video_refs")
    vrefs.append(ref)
    pj.setdefault("notes", []).append(
        f"用户从本地上传加入组参考视频:{ref}(video_refs 第 {len(vrefs)} 个,[Video {len(vrefs)}]"
        f"{f',{dur:.1f}s' if dur is not None else ''});正文尚未引用——重出本组 prompt 时按用途写 [Video {len(vrefs)}] 说明句;"
        "重出本组时生效。由 storyboard service 自动补丁。")
    atomic_write_json(pf, pj)
    return {"ref": ref, "idx": len(vrefs), "duration_s": round(dur, 2) if dur is not None else None,
            **_grpvref_summary(project, ep, grp, base, pf)}


def _grpvref_renumber_prompt(vp: str, removed: int) -> tuple[str, int, int]:
    """删除 video_refs 第 removed 个(1-based)后重排正文 [Video N]/@Video N:大于的减一,指向被删的整体删除。"""
    shifted = dropped = 0

    def _sub(m: re.Match) -> str:
        nonlocal shifted, dropped
        n = int(m.group(1) or m.group(2))
        if n == removed:
            dropped += 1
            return ""
        if n < removed:
            return m.group(0)
        shifted += 1
        return m.group(0).replace(str(n), str(n - 1), 1)

    return _VID_REF_RE.sub(_sub, vp), shifted, dropped


async def api_grpvref_delete(body: dict):
    """从组 video_refs 移除一个参考视频(重出本组生效)。用户上传的普通确认即删(并删落盘文件);
    白模/前组尾段等流水线注入的须 force=true(弹窗强确认):同时回滚其固定说明段
    (白模:Whitebox reference/legend 段——本组不再挂任何白模视频时整段删;尾段:Continuation reference 块),
    再重排正文 [Video N] 引用。注意流水线重跑 sync_whitebox_refs --write / continuity 同步会按预算重新挂回,
    要彻底不用需在「🎛 模型」换模型或关闭对应项目开关。"""
    project, ep, grp, base, pf = _grpref_ctx(body)
    ref = (body.get("ref") or "").strip().lstrip("/")
    d = json.loads(pf.read_text())
    vrefs = d.get("video_refs") or []
    if ref not in vrefs:
        raise ServiceError(404, f"Ref not in this group's video_refs: {ref}")
    user_added = _grpvref_user_added(d, ref)
    if not user_added and not body.get("force"):
        raise ServiceError(400, "This reference video was injected by the pipeline (whitebox/continuation), not added by hand; "
                                "pass force=true to remove it (the preview dialog asks for confirmation)")
    i = vrefs.index(ref)
    kind = _grpvref_kind(ref)
    vp = d.get("video_prompt") or ""
    extra = []
    cm = _CONT_CLAUSE_RE.search(vp) if kind == "continuation" else None
    if cm and f"[Video {i + 1}]" in cm.group(0):
        vp = _CONT_CLAUSE_RE.sub("\n", vp, count=1).strip()
        d.pop("continuity_ref", None)
        extra.append("已移除 Continuation reference 续写块")
    vrefs.pop(i)
    if kind == "whitebox" and not any("/whitebox/" in v for v in vrefs):
        try:
            from modules.whitebox_refs import _BLOCK_RE as _WB_BLOCK_RE, GC_SENTENCE as _WB_GC
            vp2 = _WB_BLOCK_RE.sub("", vp)
            vp2 = re.sub(r"\s*" + re.escape(_WB_GC), "", vp2)   # Global constraints 里的禁白模外观句一并摘掉
            if vp2 != vp:
                vp = vp2
                extra.append("已移除 Whitebox reference/legend 固定段")
        except Exception:
            pass
        d.pop("whitebox_refs", None)
    shifted = dropped = 0
    if vp:
        vp, shifted, dropped = _grpvref_renumber_prompt(vp, i + 1)
        if shifted or dropped:
            extra.append(f"正文引用重排 {shifted} 处、移除指向该视频的引用 {dropped} 处")
        d["video_prompt"] = vp
        d["video_prompt_word_count"] = len(vp.split())
    if vrefs:
        d["video_refs"] = vrefs
    else:
        d.pop("video_refs", None)
    d.setdefault("notes", []).append(
        f"用户移除组参考视频:{ref}(原 video_refs 第 {i + 1} 个"
        f"{'' if user_added else ',流水线注入(' + kind + ')'}"
        f"{';' + ';'.join(extra) if extra else ''});重出本组时生效。由 storyboard service 自动补丁。")
    atomic_write_json(pf, d)
    if ref.startswith(f"assets/uploads/{ep}/{grp}/"):
        p = (base / ref).resolve()
        try:
            p.relative_to(base.resolve())
            p.unlink(missing_ok=True)
        except ValueError:
            pass
    return {"deleted": ref, "user_added": user_added, "kind": kind,
            "prompt_refs_shifted": shifted, "prompt_refs_dropped": dropped, "extra": extra,
            **_grpvref_summary(project, ep, grp, base, pf)}


# 对白编号 → 台词索引(2026-08-27):story/episodes/<ep>/dialogue.md 是对白层权威定稿,
# shot_list 的镜条目只带编号(dialogue_refs ["S04-D05"] / dialogue_ref "LN-ep01-01"),
# 分镜预览要显示台词正文必须回这里解析。各项目 dialogue.md 由 agent 自由排版,兼容三种形态:
#   ① 表格行 `| S04-D05 | 镜位 | CHAR-0005 | 通道 | 台词 | … |`(按表头「台词/对白/说话人」定列)
#   ② 紧凑行 `[LN-ep01-01] 章墨(CHAR-0001)〔OV〕:台词 {emotion: …}`
#   ③ 标题块 `### [LN-ep01-01] 章墨(CHAR-0001)` + `- **定稿**:**台词**`
_DLG_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+$")
_DLG_TEXT_COL = ("台词", "对白", "定稿", "line", "text")
_DLG_SPK_COL = ("说话人", "角色", "speaker", "char")


def _dialogue_index(text: str) -> dict[str, dict]:
    """解析 dialogue.md,返回 {对白编号: {"speaker": CHAR-id 或名字, "text": 台词}}。"""
    idx: dict[str, dict] = {}

    def _put(did: str, speaker: str | None, line: str | None):
        line = (line or "").strip().strip("*").strip()
        if not did or not line or did in idx:
            return
        spk = (speaker or "").strip()
        m = re.search(r"(CHAR-\d+)", spk)
        idx[did] = {"speaker": m.group(1) if m else re.sub(r"[〔【(\[].*$", "", spk).strip() or None,
                    "text": line}

    text_col = spk_col = None
    cur_id = cur_spk = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", c or "-") for c in cells):
                continue
            if not cells or not _DLG_ID_RE.match(cells[0]):
                # 表头:定位台词列 / 说话人列(排除「原句」「字数」等近似列)
                low = [c.lower() for c in cells]
                text_col = next((i for i, c in enumerate(low)
                                 if any(k in c for k in _DLG_TEXT_COL)
                                 and not any(k in c for k in ("原句", "字数", "编号", "id"))), None)
                spk_col = next((i for i, c in enumerate(low) if any(k in c for k in _DLG_SPK_COL)), None)
                continue
            if text_col is not None and text_col < len(cells):
                _put(cells[0], cells[spk_col] if spk_col is not None and spk_col < len(cells) else None,
                     cells[text_col])
            continue
        m = re.match(r"^(?:#{1,6}\s*)?\[([A-Za-z][A-Za-z0-9-]+)\]\s*(.*)$", line)
        if m and _DLG_ID_RE.match(m.group(1)):
            rest = m.group(2)
            # ② 同行带台词:说话人段与台词以全角/半角冒号分隔,尾随 {…} 元数据剔除
            m2 = re.match(r"^(.*?)[:：]\s*(.+?)(?:\s*\{[^{}]*\})?\s*$", rest)
            if m2 and m2.group(2) and not line.startswith("#"):
                _put(m.group(1), m2.group(1), m2.group(2))
                cur_id = None
            else:
                cur_id, cur_spk = m.group(1), rest
            continue
        if cur_id:
            # ③ 标题块内的「定稿」条目
            m3 = re.match(r"^-\s*\*\*定稿\*\*\s*[:：]\s*(.+)$", line)
            if m3:
                _put(cur_id, cur_spk, m3.group(1))
                cur_id = None
            elif line.startswith("#"):
                cur_id = None
    return idx


def _shot_has_own_dialogue(s: dict) -> bool:
    """镜条目自身是否带对白信息(内嵌 dialogue / dialogue_lines 或编号 dialogue_refs / dialogue_ref)。
    有则以镜为准,不再回落 storyboard 草稿——拆镜(storyboard_ref 带 /split:x)的草稿是整镜
    未拆前的全部台词,回落会把兄弟镜的台词也挂上来(2026-08-30 dzg5 ep01)。"""
    if isinstance(s.get("dialogue_lines"), list):
        return True  # 规范字段显式给了列表(含空列表 = 本镜无台词),即为权威
    return any(_nonempty(s.get(k)) for k in ("dialogue", "dialogue_refs", "dialogue_ref"))


def _nonempty(v) -> bool:
    if isinstance(v, str):
        return bool(v.strip())
    return bool(v)


def _shot_dialogue_lines(s: dict, draft: dict, idx: dict[str, dict]) -> list[dict]:
    """镜条目的对白列表 [{ref, speaker, text}]:优先 shot_list 内嵌 dialogue{text} /
    规范字段 dialogue_lines[{speaker, text}](shot-planning 直出台词正文,2026-08-30),
    其次按 dialogue_refs / dialogue_ref 编号查 dialogue.md;编号解析不到的 text=None,
    非编号的整段字符串(老项目把台词原文写进 dialogue_ref)原样当台词。
    storyboard 草稿的 dialogue_refs / dialogue_ref 只在镜条目自身无任何对白信息时才回落。"""
    lines: list[dict] = []
    seen: set[str] = set()

    def _add(ref, speaker, text):
        key = ref or text
        if not key or key in seen:
            return
        seen.add(key)
        lines.append({"ref": ref, "speaker": speaker, "text": text})

    for emb in (s.get("dialogue"), s.get("dialogue_lines")):
        for d in (emb if isinstance(emb, list) else [emb]):
            # 规约键 text;有的出稿把正文写成 line(liaozhai2 ep08,2026-09-04),与 check_dialogue_fit 同样兼容
            txt = d.get("text") or d.get("line") if isinstance(d, dict) else None
            if txt:
                _add(d.get("ref") or d.get("line_id") or d.get("id"), d.get("speaker"), str(txt).strip())
    refs = []
    srcs = [s.get("dialogue_refs"), s.get("dialogue_ref")]
    if not _shot_has_own_dialogue(s):
        srcs += [draft.get("dialogue_refs"), draft.get("dialogue_ref")]
    for v in srcs:
        if isinstance(v, list):
            refs.extend(x for x in v if isinstance(x, str))
        elif isinstance(v, str) and v.strip():
            refs.append(v.strip())
    for r in refs:
        toks = [t.strip() for t in re.split(r"[\s,;、/]+", r) if t.strip()]
        if toks and all(_DLG_ID_RE.match(t) for t in toks):
            for t in toks:
                hit = idx.get(t) or {}
                _add(t, hit.get("speaker"), hit.get("text"))
        else:
            _add(None, None, r)
    return lines


def _read_json_safe(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _proj_base(project: str) -> Path:
    base = PROJECTS_DIR / safe_slug(project)
    if not base.is_dir():
        raise ServiceError(404, f"Project not found: {project}")
    return base


_NUM_ID_RE = re.compile(r"^([A-Za-z]+)0*(\d+)")


def _num_id_key(token: str) -> tuple[str, int] | None:
    """grp001/grp01/grp1 → ("grp", 1)。规范是三位零填充(WORKFLOW.md §7A),
    但生成 agent 写盘时位数偶发漂移(grp01.mp4 对 group_id=grp001),
    预览对位按 前缀词+编号数值 容错,不要求逐字符相同。"""
    m = _NUM_ID_RE.match(token or "")
    return (m.group(1).lower(), int(m.group(2))) if m else None


def _id_name_match(cid: str, name: str, any_segment: bool = False) -> bool:
    """资产相对路径 name 是否属于镜/组编号 cid。保留原逐字符前缀规则,
    另按 _num_id_key 数值容错;any_segment 时子目录段也参与(镜级 clips
    历史上允许 archive/sh001_x.mp4 这类归档路径命中)。"""
    key = _num_id_key(cid)
    segs = name.split("/") if any_segment else name.split("/")[:1]
    return any(seg.startswith(cid) or (key is not None and _num_id_key(seg) == key)
               for seg in segs)


def _id_dir(root: Path, cid: str) -> Path:
    """root/cid 目录;不存在时按编号数值找同义目录(keyframes/grp01 ≙ grp001)。"""
    d = root / cid
    key = _num_id_key(cid)
    if not d.is_dir() and key is not None and root.is_dir():
        for sub in sorted(root.iterdir()):
            if sub.is_dir() and _num_id_key(sub.name) == key:
                return sub
    return d


def _id_file(adir: Path, cid: str, suffix: str) -> Path:
    """adir/cid+suffix 文件;不存在时按编号数值找同义文件(grp01.meta.json ≙ grp001)。"""
    f = adir / f"{cid}{suffix}"
    key = _num_id_key(cid)
    if not f.is_file() and key is not None and adir.is_dir():
        for p in sorted(adir.glob(f"*{suffix}")):
            if _num_id_key(p.name) == key:
                return p
    return f


def _asset_urls(base: Path, adir: Path, exts: tuple) -> list[dict]:
    """目录下(含子目录)的媒体文件 → [{name, url}],按文件名排序。"""
    if not adir.is_dir():
        return []
    files = sorted(f for f in adir.rglob("*")
                   if f.is_file() and f.suffix.lower() in exts)
    # URL 带 mtime 版本参数,文件被覆写后浏览器不会命中旧缓存
    return [{"name": f.relative_to(adir).as_posix(),
             "url": (f"/projects/{base.name}/{f.relative_to(base).as_posix()}"
                     f"?v={int(f.stat().st_mtime)}")} for f in files]


def _audio_url(base: Path, f: Path) -> str | None:
    """项目内单个音频文件 → 带 mtime 版本参数的 /projects URL;不存在返回 None。"""
    try:
        if not f.is_file() or f.suffix.lower() not in AUDIO_EXTS:
            return None
        return (f"/projects/{base.name}/{f.relative_to(base).as_posix()}"
                f"?v={int(f.stat().st_mtime)}")
    except (OSError, ValueError):
        return None


def _character_voices(base: Path) -> dict[str, list[dict]]:
    """音色试听聚合:assets/audio/voice/casting.json(选角事实源)按 char_id 归组,
    refs/ 下未登记进 casting 的 <CHAR-ID>[_<variant>]_voiceprint 音频兜底补入。"""
    vdir = base / "assets" / "audio" / "voice"
    casting = _read_json_safe(vdir / "casting.json") or {}
    voices: dict[str, list[dict]] = {}
    for c in casting.get("castings", []):
        if not (isinstance(c, dict) and c.get("char_id")):
            continue
        vp = c.get("voiceprint")
        voices.setdefault(c["char_id"], []).append({
            "variant": c.get("variant") or "",
            "tts_model": c.get("tts_model") or "",
            "tts_voice": c.get("tts_voice") or "",
            "status": c.get("status") or "",
            "url": _audio_url(base, base / vp) if vp else None})
    rdir = vdir / "refs"
    if rdir.is_dir():
        seen = {v["url"].split("?")[0]
                for vs in voices.values() for v in vs if v["url"]}
        for f in sorted(rdir.iterdir()):
            m = re.match(r"([^_]+)(?:_(.+))?_voiceprint$", f.stem)
            url = _audio_url(base, f)
            if m and url and url.split("?")[0] not in seen:
                voices.setdefault(m.group(1), []).append({
                    "variant": m.group(2) or "", "tts_model": "",
                    "tts_voice": "", "status": "", "url": url})
    return voices


# ---------------- 服装 sheet(06-art/costume-concept,2026-08-26) ----------------
def _costume_entries(doc: dict) -> list[dict]:
    """bible/costumes.json 两种既有 schema 统一成扁平服装条目列表:
    ① 平台约定 characters[].outfits[](默认装由条目 default 或角色级
       default_outfit_id / default_outfit / default_costume 指定);
    ② 扁平 costumes[] / entries[](以 character_ref / character_id 归属)。
    每条:id / character_ref / label / occasion / period / default / visual_en /
    scenes / chapters / episodes / variants / condition。"""
    out: list[dict] = []

    def _norm(e: dict, cid: str, default_id: str | None) -> dict | None:
        oid = e.get("id") or e.get("outfit_id") or e.get("costume_id")
        if not oid:
            return None
        variants = e.get("variants") or []
        return {
            "id": oid, "character_ref": cid,
            "label": e.get("label") or e.get("name") or e.get("name_cn") or oid,
            "occasion": e.get("occasion") or "", "period": e.get("period") or e.get("period_anchor") or "",
            "default": bool(e.get("default")) or (default_id is not None and oid == default_id),
            "visual_en": e.get("visual_en") or e.get("visual") or "",
            "condition": e.get("condition_and_wear") or e.get("condition") or "",
            "scenes": e.get("scenes") or [], "chapters": e.get("chapters") or [],
            "episodes": e.get("episodes") or [],
            "variants": [v.get("name") or v.get("label") or v.get("id") or str(v)
                         if isinstance(v, dict) else str(v) for v in variants],
        }

    if not isinstance(doc, dict):
        return out
    for e in (doc.get("costumes") or doc.get("entries") or []):
        if not isinstance(e, dict):
            continue
        cid = e.get("character_ref") or e.get("character_id") or ""
        n = _norm(e, cid, None)
        if n:
            out.append(n)
    for c in (doc.get("characters") or []):
        if not isinstance(c, dict) or not isinstance(c.get("outfits"), list):
            continue
        cid = c.get("character_id") or c.get("id") or ""
        d = c.get("default_outfit_id") or c.get("default_outfit") or c.get("default_costume")
        for e in c["outfits"]:
            if not isinstance(e, dict):
                continue
            n = _norm(e, e.get("character_ref") or cid, d if isinstance(d, str) else None)
            if n:
                out.append(n)
    return out


def _character_costume_sheets(base: Path, cid: str, entries: list[dict] | None = None) -> list[dict]:
    """某角色的服装 sheet 清单(人物预览页「👕 服装」区 / 分镜组卡服装图 / 资产选图器共用):
    以 assets/concepts/characters/<id>/costume_sheets.json 台账为准,台账缺条的套装
    回落检查主目录 sheet_<COS-id>.png 是否存在(默认装 = sheet.png)。每条带 url(缺图 None)。"""
    if entries is None:
        entries = _costume_entries(_read_json_safe(base / "bible" / "costumes.json") or {})
    adir = base / "assets" / "concepts" / "characters" / cid
    ledger = _read_json_safe(adir / "costume_sheets.json") or {}
    lrows = {r.get("costume_ref"): r for r in (ledger.get("sheets") or [])
             if isinstance(r, dict) and r.get("costume_ref")}

    def _url(rel_file: str | None):
        if not rel_file:
            return None, None
        f = (adir / rel_file).resolve()
        try:
            f.relative_to(base.resolve())
        except ValueError:
            return None, None
        if not f.is_file():
            return None, None
        rel = f.relative_to(base).as_posix()
        return rel, f"/projects/{base.name}/{rel}?v={int(f.stat().st_mtime)}"

    rows = []
    for e in entries:
        if e["character_ref"] != cid:
            continue
        r = lrows.get(e["id"]) or {}
        file = r.get("reuse_of") or r.get("file")
        if not r:
            file = "sheet.png" if e["default"] else f"sheet_{e['id']}.png"
        ref, url = _url(file)
        rows.append({**e, "file": ref, "url": url, "in_ledger": bool(r),
                     "skip_reason": r.get("skip_reason") or "",
                     "blocked_on": r.get("blocked_on") or ""})
    return rows

def _preview_characters(project: str):
    """人物设定聚合:bible/characters/* 文字 + assets/concepts/characters/* 概念图。"""
    base = _proj_base(project)
    idx = _read_json_safe(base / "bible" / "characters" / "index.json") or {}
    info = {c.get("id"): c for c in idx.get("characters", [])
            if isinstance(c, dict) and c.get("id")}
    bdir = base / "bible" / "characters"
    adir = base / "assets" / "concepts" / "characters"
    ids = set(info)
    for d in (bdir, adir):
        if d.is_dir():
            ids |= {x.name for x in d.iterdir()
                    if x.is_dir() and not x.name.startswith(".")}
    voices = _character_voices(base)
    costume_entries = _costume_entries(_read_json_safe(base / "bible" / "costumes.json") or {})
    chars = []
    # 旁白条目(2026-09-01):项目「📤 输出设置」旁白开关开启时,列表首位固定一个「旁白」
    # 伪角色——右侧展示旁白声线卡 assets/audio/voice/narrator.json(声线描述/选型/冻结样本,
    # 由 09-audio/voice-generation 设计并冻结,此后不随 TTS 设置变)与冻结样本试听
    if (load_project_settings(project).get("output") or {}) \
            .get("narration_enabled", True) is not False:
        card = _read_json_safe(base / "assets" / "audio" / "voice" / "narrator.json") or {}
        vp = base / "assets" / "audio" / "voice" / "refs" / "NARRATOR_voiceprint.mp3"
        if card.get("voiceprint"):
            vp = base / card["voiceprint"]
        chars.append({
            "id": "NARRATOR", "name": "旁白", "narrator": True,
            "meta": {"kind": "narrator"}, "docs": {},
            "voice_card": card,
            "voices": [{"variant": "",
                        "tts_model": card.get("tts_model") or "",
                        "tts_voice": card.get("tts_voice") or "",
                        "status": card.get("status") or "",
                        "url": _audio_url(base, vp) if vp.is_file() else None}],
            "images": [], "costumes": []})
    for cid in sorted(ids):
        docs = {}
        if (bdir / cid).is_dir():
            for f in sorted((bdir / cid).glob("*.json")):
                docs[f.stem] = _read_json_safe(f)
        meta = info.get(cid) or {}
        chars.append({"id": cid, "name": meta.get("canonical_name") or cid,
                      "meta": meta, "docs": docs,
                      "voices": voices.get(cid, []),
                      "images": _asset_urls(base, adir / cid, IMG_EXTS),
                      # 服装 sheet(costume-concept 台账 + costumes.json 套装),2026-08-26
                      "costumes": _character_costume_sheets(base, cid, costume_entries)})
    return {"project": base.name, "characters": chars}


async def api_preview_characters(project: str = "demo"):
    return await asyncio.to_thread(_preview_characters, project)


def _preview_props(project: str):
    """道具设定聚合:bible/props.json 设定卡 + assets/concepts/props/* 参考图。"""
    base = _proj_base(project)
    doc = _read_json_safe(base / "bible" / "props.json") or {}
    # 兼容两种数组键:平台约定 props;部分项目自建 schema 写成 entries
    cards = {p.get("id"): p for p in (doc.get("props") or doc.get("entries") or [])
             if isinstance(p, dict) and p.get("id")}
    adir = base / "assets" / "concepts" / "props"
    ids = set(cards)
    if adir.is_dir():
        ids |= {x.name for x in adir.iterdir()
                if x.is_dir() and not x.name.startswith(".")}
    props = []
    for pid in sorted(ids):
        card = cards.get(pid) or {}
        props.append({"id": pid, "name": card.get("name") or pid,
                      "card": card,
                      "images": _asset_urls(base, adir / pid, IMG_EXTS)})
    return {"project": base.name, "props": props}


async def api_preview_props(project: str = "demo"):
    return await asyncio.to_thread(_preview_props, project)


def _preview_creatures(project: str):
    """生物/坐骑设定聚合:bible/creatures/index.json 登记表(CRE-* 权威)+ 详情卡
    (index 条目 detail_file 指向 creature.json#creatures[] 或 mount.json#mounts[],
    后者以 creature_ref 反查)+ assets/concepts/creatures/* 概念图。目录与 ID 一律
    以 CRE-* 为键;MNT-* 是 mount.json 单文件内部编号,不作聚合键。"""
    base = _proj_base(project)
    cdir = base / "bible" / "creatures"
    idx = _read_json_safe(cdir / "index.json") or {}
    info = {c.get("id"): c for c in idx.get("creatures", [])
            if isinstance(c, dict) and c.get("id")}
    # 详情卡:两份文件各自的数组键不同(creatures / mounts),统一按 id 或 creature_ref 建索引
    cards: dict[str, tuple[dict, str]] = {}
    for fname, key in (("creature.json", "creatures"), ("mount.json", "mounts")):
        doc = _read_json_safe(cdir / fname) or {}
        for e in doc.get(key) or doc.get("entries") or []:
            if not isinstance(e, dict):
                continue
            cid = e.get("creature_ref") or e.get("id")
            if cid and cid not in cards:
                cards[cid] = (e, f"bible/creatures/{fname}")
    adir = base / "assets" / "concepts" / "creatures"
    ids = set(info) | set(cards)
    if adir.is_dir():
        ids |= {x.name for x in adir.iterdir()
                if x.is_dir() and not x.name.startswith(".")}
    creatures = []
    for cid in sorted(ids):
        meta = info.get(cid) or {}
        card, src = cards.get(cid) or ({}, "")
        creatures.append({"id": cid, "name": meta.get("name") or card.get("name") or cid,
                          "type": meta.get("type") or "",
                          "meta": meta, "card": card, "card_source": src,
                          "images": _asset_urls(base, adir / cid, IMG_EXTS)})
    return {"project": base.name, "creatures": creatures}


async def api_preview_creatures(project: str = "demo"):
    return await asyncio.to_thread(_preview_creatures, project)


def _preview_scenes(project: str):
    """场景设定聚合:bible/scenes/* 文字 + assets/concepts/scenes/* 概念图。"""
    base = _proj_base(project)
    idx = _read_json_safe(base / "bible" / "scenes" / "index.json") or {}
    info = {s.get("id"): s for s in idx.get("scenes", [])
            if isinstance(s, dict) and s.get("id")}
    bdir = base / "bible" / "scenes"
    adir = base / "assets" / "concepts" / "scenes"
    ids = set(info)
    for d in (bdir, adir):
        if d.is_dir():
            ids |= {x.name for x in d.iterdir()
                    if x.is_dir() and not x.name.startswith(".")}
    scenes = []
    for sid in sorted(ids):
        docs = {}
        if (bdir / sid).is_dir():
            for f in sorted((bdir / sid).glob("*.json")):
                docs[f.stem] = _read_json_safe(f)
        meta = info.get(sid) or {}
        scenes.append({"id": sid, "name": meta.get("name") or sid,
                       "meta": meta, "docs": docs,
                       "images": _asset_urls(base, adir / sid, IMG_EXTS)})
    return {"project": base.name, "scenes": scenes}


async def api_preview_scenes(project: str = "demo"):
    return await asyncio.to_thread(_preview_scenes, project)


def _preview_worldview(project: str):
    """世界观设定聚合:bible/ 根下的 JSON(+README.md)。characters/creatures/scenes
    是子目录,天然不在扫描范围(各有独立预览页);新增的根级 JSON 自动出现。"""
    base = _proj_base(project)
    bdir = base / "bible"
    sections = []
    if bdir.is_dir():
        for p in sorted(bdir.iterdir()):
            if not p.is_file() or p.name.startswith("."):
                continue
            sec = {"id": p.stem, "file": p.name,
                   "size": p.stat().st_size, "mtime": p.stat().st_mtime}
            if p.suffix.lower() == ".json":
                sec["data"] = _read_json_safe(p)
            elif p.suffix.lower() == ".md":
                try:
                    sec["text"] = p.read_text(encoding="utf-8")
                except Exception:
                    continue
            else:
                continue
            sections.append(sec)
    return {"project": base.name, "sections": sections}


async def api_preview_worldview(project: str = "demo"):
    return await asyncio.to_thread(_preview_worldview, project)


def _shot_camera_move(base: Path, ep: str, sid: str) -> dict | None:
    """镜级运镜(camera-movement agent 产出 directing/<ep>/shots/<sid>/camera.json,
    2026-08-27 起进分镜预览):movement 是英文枚举(static/push_in/…),中文标签各项目
    字段名不一(movement_label / movement_zh / movement_cn / movement_en),取首个非空;
    没有标签时前端按枚举查词典。文件缺失(如混剪项目无运镜设计)返回 None,前端不渲染。"""
    if not sid:
        return None
    c = _read_json_safe(_id_dir(base / "directing" / ep / "shots", sid) / "camera.json") or {}
    if not c:
        return None
    _s = lambda k: c.get(k) if isinstance(c.get(k), str) and c.get(k).strip() else None
    # offer 式项目把 movement 直接写成中文("固定机位")、movement_en 才是英文:
    # 非 ASCII 的 movement 已可读,优先于 movement_en
    mv = _s("movement")
    label = next((v for v in (_s("movement_label"), _s("movement_zh"), _s("movement_cn"),
                              mv if mv and not mv.isascii() else None,
                              _s("movement_en")) if v), None)
    out = {"movement": _s("movement"), "label": label, "rig": _s("rig"),
           "speed_curve": _s("speed_curve"), "start_frame": _s("start_frame"),
           "end_frame": _s("end_frame")}
    return out if any(out.values()) else None


def _pick_str(d: dict, *keys: str) -> str | None:
    """取首个字符串字段;值为 dict 时退到其 level/summary/value 子字段(各项目 lighting.json
    结构自由度大,如 polan2 的 contrast_ratio={level,note})。"""
    for k in keys:
        v = d.get(k)
        if isinstance(v, dict):
            v = next((v[x] for x in ("level", "summary", "value", "label")
                      if isinstance(v.get(x), str)), None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _group_lighting(base: Path, g: dict, sb_scene_tod: dict, cache: dict) -> dict | None:
    """组光照(2026-08-27 起进分镜预览组卡):组 time_of_day + lighting_scheme_id →
    bible/scenes/<scene_id>/lighting.json 的 schemes[].scheme_id(WORKFLOW time_anchor_ok
    口径)。老项目组未钉 scheme_id 时按时段唯一匹配 condition.time_of_day 兜底
    (时段来源依次:组 time_of_day → offer 式 lighting_condition.time_of_day →
    storyboard 场块 time_of_day);全无时段字段的老项目取场景唯一方案;仍无命中只回
    时段并标 resolved=False,前端灰显提示;完全无据返回 None,前端不渲染。"""
    scene_id = g.get("scene_id") or ""
    scheme_id = g.get("lighting_scheme_id") if isinstance(g.get("lighting_scheme_id"), str) else None
    lc = g.get("lighting_condition") if isinstance(g.get("lighting_condition"), dict) else {}
    tod = next((v for v in (g.get("time_of_day"), lc.get("time_of_day"),
                            sb_scene_tod.get(scene_id)) if isinstance(v, str) and v.strip()), None)
    if scene_id not in cache:
        cache[scene_id] = (_read_json_safe(_id_dir(base / "bible" / "scenes", scene_id) / "lighting.json")
                           if scene_id else {}) or {}
    schemes = [s for s in (cache[scene_id].get("schemes") or []) if isinstance(s, dict)]
    hit, source = None, None
    if scheme_id:
        hit = next((s for s in schemes if (s.get("scheme_id") or s.get("id")) == scheme_id), None)
        source = "scheme_id"
    if hit is None and tod:
        cands = [s for s in schemes
                 if isinstance((s.get("condition") or {}).get("time_of_day"), str)
                 and (tod in s["condition"]["time_of_day"] or s["condition"]["time_of_day"] in tod)]
        if len(cands) == 1:
            hit, source = cands[0], "time_of_day"
    if hit is None and not tod and not scheme_id:
        # 2026-07-20 前的老项目(如 thedoor)组/场块都无时段字段:场景只有一套方案时唯一可选
        if len(schemes) == 1:
            hit, source = schemes[0], "single_scheme"
            cond_tod = (hit.get("condition") or {}).get("time_of_day")
            tod = cond_tod if isinstance(cond_tod, str) else None
        else:
            return None
    hit = hit or {}
    return {"time_of_day": tod, "scheme_id": scheme_id or _pick_str(hit, "scheme_id", "id"),
            "label": _pick_str(hit, "label", "name"),
            "key_source": _pick_str(hit, "key_source", "key_light"),
            "direction": _pick_str(hit, "direction"),
            "color_temp": _pick_str(hit, "color_temp", "color_temperature", "color_temperature_range"),
            "contrast": _pick_str(hit, "contrast", "contrast_ratio"),
            "prompt_fragment": _pick_str(hit, "prompt_fragment_en", "prompt_fragment"),
            "source": source, "resolved": bool(hit)}


def _preview_storyboard(project: str, ep: str):
    """分镜设定聚合:分集列表 + 指定集的剧本/分镜表/每镜关键帧与成片视频。"""
    base = _proj_base(project)
    plan = _read_json_safe(base / "story" / "episode_plan.json") or {}
    plan_eps = {e.get("ep"): e for e in plan.get("episodes", [])
                if isinstance(e, dict) and e.get("ep")}
    eps = set(plan_eps)
    for sub in ("story/episodes", "directing"):
        d = base / sub
        if d.is_dir():
            eps |= {x.name for x in d.iterdir()
                    if x.is_dir() and not x.name.startswith(".")}
    episodes = [{"ep": e, "title": (plan_eps.get(e) or {}).get("title", ""),
                 "summary": (plan_eps.get(e) or {}).get("mainline_summary", "")}
                for e in sorted(eps)]
    ep = ep or (episodes[0]["ep"] if episodes else "")
    data = {"project": base.name, "episodes": episodes, "ep": ep}
    if not ep:
        return data
    ep = re.sub(r"[^\w\-]", "", ep)

    def _read_text(rel: str) -> str:
        p = base / rel
        try:
            return p.read_text() if p.is_file() else ""
        except Exception:
            return ""

    data["screenplay"] = _read_text(f"story/episodes/{ep}/screenplay.md")
    data["narration"] = _read_text(f"story/episodes/{ep}/narration.md")
    data["directing_plan"] = _read_text(f"directing/{ep}/directing_plan.md")
    # 结构化旁白条目:[N-xx | anchor: 场景锚 | est_duration_s: 秒 | source: 章#段]\n正文
    data["narration_items"] = [
        {"id": m.group(1), "anchor": m.group(2).strip(),
         "est_s": float(m.group(3)), "text": m.group(4).strip()}
        for m in re.finditer(
            r"^\[(N-\d+)\s*\|\s*anchor:\s*([^|\]]+)\|\s*est_duration_s:\s*([\d.]+)"
            r"(?:\s*\|.*)?\]\s*\n(.+)$",
            data["narration"], re.M)]
    # 旁白音频对位:narration/<ep>/manifest.json(早期集)或 narration_track.json
    # 的 segments[].num(N-xx)→ file;manifest 缺失时按 <ep>_nar_<xx>.mp3 命名兜底
    ndir = base / "assets" / "audio" / "narration" / ep
    nman = (_read_json_safe(ndir / "manifest.json")
            or _read_json_safe(ndir / "narration_track.json") or {})
    nfile = {s["num"]: s.get("file") for s in nman.get("segments", [])
             if isinstance(s, dict) and s.get("num")}
    for it in data["narration_items"]:
        rel = nfile.get(it["id"]) or f"{ep}_nar_{it['id'].split('-')[-1]}.mp3"
        it["audio"] = _audio_url(base, ndir / rel)
    sb = _read_json_safe(base / "directing" / ep / "storyboard.json") or {}
    data["title"] = sb.get("title") or (plan_eps.get(ep) or {}).get("title", "")
    data["board_scenes"] = [
        {k: s.get(k) for k in ("scene_no", "scene_code", "int_ext", "alloc_s",
                               "emotion", "director_beat_note")}
        for s in sb.get("scenes", []) if isinstance(s, dict)]
    sl = _read_json_safe(base / "directing" / ep / "shot_list.json") or {}
    from modules.scene_cast import scene_cast_groups
    scene_cast_contexts = scene_cast_groups(sl)
    # 旁白挂点定稿(shot-planning 产出,§7D ①):预览页最优先按它对位,
    # 缺失时前端回退 narration.md 锚的 grpNNN/beat 前缀匹配并标注"挂点未定稿"
    data["narration_anchors"] = [
        {k: a.get(k) for k in ("narration_id", "anchor_shots", "anchor_group",
                               "window_s", "est_duration_s")}
        for a in (sl.get("narration_anchors") or []) if isinstance(a, dict)]
    # 分镜脚本文案索引:storyboard.json scenes[].shots_draft[](shot_list 的镜条目
    # 不带文案,经 storyboard_ref "SCN-0001/order:1" 指回这里)
    drafts = {}
    for sc in sb.get("scenes", []):
        if not isinstance(sc, dict):
            continue
        for dr in (sc.get("shots_draft") or []):
            if isinstance(dr, dict) and dr.get("order") is not None:
                drafts[(sc.get("scene_no"), dr["order"])] = dr

    # 场块时段索引(组未钉 lighting_scheme_id 的老项目按时段兜底匹配光照方案)
    sb_scene_tod = {sc.get("scene_id"): sc.get("time_of_day")
                    for sc in sb.get("scenes", [])
                    if isinstance(sc, dict) and sc.get("scene_id")}

    def _shot_draft(s: dict) -> dict:
        # storyboard_ref 规范形 "S03/order:1";shot-planning 按空间/台词把一条草稿拆成多镜时
        # 写成 "S02/order:3/split:a" / ".../split:b"(2026-08-30 dzg5 ep01 六镜),后缀不参与索引;
        # 兼容 "S01/shots_draft/order:1"(2026-09-02 liaozhai2 ep01/ep02 多插了一段路径),中段不参与索引
        m = re.match(r"^(.+?)(?:/shots_draft)?/order:(\d+)(?:/split:[^/]+)?$", s.get("storyboard_ref") or "")
        return (drafts.get((m.group(1), int(m.group(2)))) if m else None) or {}

    def _shot_content(s: dict) -> str:
        # 镜条目自身 content / 规范字段 content_brief(shot-planning 直出,拆镜时是本镜独有内容)
        # 优先于 storyboard 草稿(拆镜的草稿是整镜未拆前的全文)
        for k in ("content", "content_brief"):
            if isinstance(s.get(k), str) and s[k].strip():
                return s[k]
        dr = _shot_draft(s)
        return dr.get("content") or dr.get("subject_action") or ""

    kroot = base / "assets" / "keyframes" / ep
    croot = base / "assets" / "clips" / ep
    clips = _asset_urls(base, croot, VIDEO_EXTS)
    # 组服装(2026-08-26):shot_list 组 costumes_by_char(权威)→ 缺则由镜 costumes 并集
    # → 再缺回落 continuity 的 costume_states(存量项目);经 costume_sheets.json 台账落到服装 sheet
    cidx = _read_json_safe(base / "bible" / "characters" / "index.json") or {}
    cname = {c.get("id"): c.get("canonical_name") or c.get("name") or c.get("id")
             for c in cidx.get("characters", []) if isinstance(c, dict) and c.get("id")}
    costume_entries = _costume_entries(_read_json_safe(base / "bible" / "costumes.json") or {})
    sheet_cache: dict[str, dict[str, dict]] = {}
    shot_costumes = {s.get("shot_id"): s.get("costumes") for s in (sl.get("shots") or [])
                     if isinstance(s, dict) and isinstance(s.get("costumes"), dict)}
    cont = (_read_json_safe(base / "directing" / ep / "continuity.json")
            or _read_json_safe(base / "directing" / ep / "continuity_plan.json") or {})
    # 组间尾帧锚(continuity-planning SOUL 职责 1):group_transitions[].anchor ∈ {last_frame, none};
    # shot_list 的 continuity_from 只是组序(前一组 id),跨场景硬切也有值,不等于「续接尾帧」
    cont_trans = {t.get("to_group"): t for t in (cont.get("group_transitions") or [])
                  if isinstance(t, dict) and t.get("to_group")}
    cont_states = {st.get("shot_id"): st for st in (cont.get("costume_states") or [])
                   if isinstance(st, dict) and st.get("shot_id")}

    def _group_costumes(g: dict) -> list[dict]:
        by = g.get("costumes_by_char")
        source = "shot_list"
        if not isinstance(by, dict) or not by:
            by = {}
            for sid in (g.get("shots") or []):
                for ch, cos in (shot_costumes.get(sid) or {}).items():
                    by.setdefault(ch, cos)
            if not by:
                source = "continuity"
                # costume_states 两种在产结构都认(2026-08-31):SOUL 示例是扁平
                # {shot_id, CHAR-xxxx: {outfit}},实际 continuity-planning 产物(dzg5/dzg6)
                # 是嵌套 {shot_id, characters: {CHAR-xxxx: {outfit}}}
                for sid in (g.get("shots") or []):
                    st = cont_states.get(sid) or {}
                    chmap = st.get("characters") if isinstance(st.get("characters"), dict) else st
                    for ch, v in chmap.items():
                        if ch != "shot_id" and isinstance(v, dict) and v.get("outfit"):
                            by.setdefault(ch, v["outfit"])
            if not by:
                return []
        rows = []
        for ch, cos in by.items():
            if ch not in sheet_cache:
                sheet_cache[ch] = {r["id"]: r for r in _character_costume_sheets(base, ch, costume_entries)}
            r = sheet_cache[ch].get(cos) or {}
            rows.append({"char": ch, "char_name": cname.get(ch) or ch, "costume": cos,
                         "label": r.get("label") or cos, "default": bool(r.get("default")),
                         "known": bool(r), "url": r.get("url"), "file": r.get("file"),
                         "skip_reason": r.get("skip_reason") or "", "blocked_on": r.get("blocked_on") or "",
                         "source": source})
        return rows
    # 组出场生物(2026-08-27):creatures_union → bible/creatures/index.json 名字 + 概念库 sheet 有无,
    # 预览页组卡只发 🐎 文字 chip(缺 sheet 黄标)、不贴 sheet 缩略——参考图由 prompt 工位按章节选阶段变体
    # sheet_<stage>.png 写进组 refs,prompt 前贴图只会与 pipeline_refs 同图双显
    cre_index = _read_json_safe(base / "bible" / "creatures" / "index.json") or {}
    cre_name = {c.get("id"): (c.get("name") or c.get("id"))
                for c in cre_index.get("creatures", []) if isinstance(c, dict) and c.get("id")}

    def _creature_rows(ids) -> list[dict]:
        rows = []
        for cid in (ids or []):
            if not isinstance(cid, str):
                continue
            cdir = base / "assets" / "concepts" / "creatures" / cid
            sheet = cdir / "sheet.png"
            rel = f"assets/concepts/creatures/{cid}/sheet.png"
            rows.append({"id": cid, "name": cre_name.get(cid) or cid, "known": cid in cre_name,
                         "url": f"/projects/{base.name}/{rel}?v={int(sheet.stat().st_mtime)}" if sheet.is_file() else None,
                         "file": rel if sheet.is_file() else None,
                         "variants": sorted(f.name for f in cdir.glob("sheet_*.png")) if cdir.is_dir() else []})
        return rows
    # 花字烧录副本(clips_caption,WORKFLOW.md §9A):有则随组下发,预览页并列展示
    cap_clips = _asset_urls(base, base / "assets" / "clips_caption" / ep, VIDEO_EXTS)
    # 对白正文(2026-08-27):镜条目的编号回 dialogue.md 解析成台词,预览页显示台词而非编号
    dlg_idx = _dialogue_index(_read_text(f"story/episodes/{ep}/dialogue.md") or "")
    shots = []
    for s in (sl.get("shots") or []):
        if not isinstance(s, dict):
            continue
        sid = s.get("shot_id") or ""
        dlines = _shot_dialogue_lines(s, _shot_draft(s), dlg_idx)
        # 镜自身带对白信息时不回落草稿的 dialogue_ref(拆镜草稿含兄弟镜台词,见 _shot_has_own_dialogue)
        raw_ref = s.get("dialogue_ref") or (
            None if _shot_has_own_dialogue(s) else _shot_draft(s).get("dialogue_ref"))
        for ln in dlines:
            ln["speaker_name"] = cname.get(ln.get("speaker")) or ln.get("speaker")
        shots.append({k: s.get(k) for k in (
            "shot_id", "scene_no", "scene_id", "duration_s", "size",
            "camera_position", "characters", "creatures", "costumes", "is_dialogue", "dialogue_ref",
            "beat")} | {
            "scene_no": s.get("scene_no") or s.get("scene_id"),
            # 机位:规约字段 camera_position;有的项目(offer)agent 自创写成 camera,
            # 镜条目与 storyboard 草稿同名兼容(2026-08-27)
            "camera_position": next((v for v in (s.get("camera_position"), s.get("camera"),
                                                 _shot_draft(s).get("camera_position"),
                                                 _shot_draft(s).get("camera"))
                                     if isinstance(v, str) and v.strip()), None),
            # 编号归一成字符串(storyboard 草稿里有的项目存数组,前端 esc() 会拼成 "S04-D05,S04-D06")
            "dialogue_ref": (" / ".join(str(x) for x in raw_ref) if isinstance(raw_ref, list) else raw_ref)
                            or " / ".join(ln["ref"] or ln["text"] for ln in dlines) or None,
            "dialogue_lines": dlines,
            "content": _shot_content(s),
            # 运镜(2026-08-27):镜级 camera.json,预览页 📷 机位下方 🎥 行
            "camera_move": _shot_camera_move(base, ep, sid),
            "keyframes": _asset_urls(base, _id_dir(kroot, sid), IMG_EXTS),
            "clips": [c for c in clips
                      if sid and _id_name_match(sid, c["name"], any_segment=True)],
        })
    data["shots"] = shots
    # 生成组(WORKFLOW.md §7A):组锚点包 keyframes/<grp>/、组视频 clips/<grp>.mp4、
    # 切变边界与尾帧来自 clips/<grp>.meta.json
    groups = []
    lighting_cache: dict[str, dict] = {}
    # 组级视频模型/技能覆盖(2026-08-30):refs 上限按组生效模型判;顶部展示全局模型与技能
    gcfg = load_genconfig()
    proj_skill = resolve_prompt_skill(base.name, gcfg)
    gcand = group_video_candidates(base.name, gcfg)
    data["video_model"] = {"provider": gcand["provider"], "model": gcand["global_model"],
                           "label": gcand["global_label"], "overridable": gcand["overridable"]}
    data["prompt_skill"] = {"mode": proj_skill["mode"], "skill_id": proj_skill["skill_id"],
                            "dir": proj_skill["dir"], "warning": proj_skill["warning"],
                            "reason": proj_skill["reason"]}
    for g in (sl.get("generation_groups") or []):
        if not isinstance(g, dict):
            continue
        gid = g.get("group_id") or ""
        gres = resolve_group_settings(base.name, ep, gid, gcfg, None, proj_skill) if gid else None
        ref_cap = int(gres["ref_cap"]) if gres else max_group_ref_images(base.name)
        meta = _read_json_safe(_id_file(croot, gid, ".meta.json")) or {}
        # 组参考图(组 prompt json 的 refs)分两列:用户经「添加参考图」手动加入的
        # (notes 锚判定,可删)入 user_refs;流水线/agent 直连写入的(如概念图路径)
        # 入 pipeline_refs——指向本组锚点包目录的 ref 已由 anchors 扫描覆盖,跳过防重
        pd = _read_json_safe(_grp_prompt_path(base.name, ep, gid)) or {}
        user_refs, pipeline_refs = [], []
        kf_prefix = f"assets/keyframes/{ep}/{gid}/"
        # 有的流水线把 refs 逐字节复制进锚点包(anchor_*.png),按文件尺寸比对
        # 跳过这类副本,防止组卡片同图双显;尺寸不同的(概念图→生成锚点图)照常展示
        kdir = _id_dir(kroot, gid)
        anchor_sizes = ({p.stat().st_size for p in kdir.iterdir()
                         if p.is_file() and p.suffix.lower() in IMG_EXTS}
                        if kdir.is_dir() else set())
        for i, r in enumerate(pd.get("refs") or [], 1):
            f = base / r
            if not f.is_file():
                # 缺文件的 ref(典型:前组尾帧 grpNNN.last_frame.png——prompt 先于视频产出,
                # 要等前组出片后由 --return-last-frame 落盘):下发占位条目保住 [Image N] 序号,
                # 预览页渲染 ⏳ 占位格,免得组卡张数与 Prompt 面板 refs 数对不上被误判丢图
                pipeline_refs.append({"ref": r, "idx": i, "missing": True, "url": None,
                                      "name": "/".join(r.split("/")[-2:])})
                continue
            url = f"/projects/{base.name}/{r}?v={int(f.stat().st_mtime)}"
            if _grpref_user_added(pd, r):
                user_refs.append({"ref": r, "idx": i, "name": f.name, "url": url})
            elif (not r.startswith(kf_prefix)
                  and f.stat().st_size not in anchor_sizes):
                # 概念图文件名易撞名(如多个 three-quarter.png),取末两段路径作显示名
                pipeline_refs.append({"ref": r, "idx": i, "url": url,
                                      "name": "/".join(r.split("/")[-2:])})
        # 2026-09-07:组人物动线俯视图 directing/{ep}/blocking_maps/{gid}.png 退役,不再补插进组卡;
        # H3A 审看站位/动线改看组卡「🧊白模」参考视频(whitebox-ui.js),存量项目残留的旧图不再展示
        tr = cont_trans.get(gid) or {}
        groups.append({k: g.get(k) for k in (
            "group_id", "scene_id", "shots", "total_duration_s",
            "characters_union", "creatures_union", "has_dialogue", "continuity_from")} | {
            "scene_no": scene_cast_contexts.get(gid, {}).get("scene_no"),
            "scene_cast": scene_cast_contexts.get(gid, {}).get("actor_ids", []),
            "scene_cast_refs": g.get("scene_cast_refs", []),
            # 组链 chip 三态:anchor last_frame=续接尾帧 / none=硬切不传尾帧 / 无 continuity 计划=只知前组;
            # tail_ref 是 prompt refs 里实际挂的前组尾帧路径(None=未挂),与 anchor 不一致时前端打 ⚠
            "continuity_anchor": tr.get("anchor") or None,
            "continuity_ref": pd.get("continuity_ref") or {},
            "continuity_video_ref": next((r for r in (pd.get("video_refs") or [])
                                          if isinstance(r, str) and r.endswith(".continuation.mp4")), None),
            "continuity_anchor_reason": str(tr.get("anchor_reason") or tr.get("notes") or ""),
            # 组入口转场 / 叙事块(shot_list transition_in / narrative_block,WORKFLOW §9C,2026-08-28):
            # 非硬切才在组卡显示 chip;实施在 Phase 9 code/render_transitions.py,这里只展示设计
            "transition_in": g.get("transition_in") if isinstance(g.get("transition_in"), dict) else None,
            "narrative_block": g.get("narrative_block") if isinstance(g.get("narrative_block"), dict) else None,
            "has_prompt": bool(pd),
            "tail_ref": next((r for r in (pd.get("refs") or [])
                              if isinstance(r, str) and r.endswith(".last_frame.png")), None),
            "blocking_map": g.get("blocking_map"),
            "creatures": _creature_rows(g.get("creatures_union")),
            "anchors": _asset_urls(base, _id_dir(kroot, gid), IMG_EXTS),
            "user_refs": user_refs,
            "pipeline_refs": pipeline_refs,
            # refs 超项目上限(refs_mandatory_le_cap,2026-08-27):prompt 工位判 FAIL 时
            # 落盘 status=blocked_refs_cap;预览页组卡黄条提示用户手动删减或换更高上限模型
            "refs_total": len(pd.get("refs") or []),
            "refs_cap": ref_cap,
            "group_settings": ({k: gres[k] for k in ("video_model", "model_source", "model_label",
                                                     "skill_dir", "skill_mode", "skill_source",
                                                     "warning", "overridden")} if gres else None),
            "refs_blocked": (pd.get("status") == "blocked_refs_cap"
                             or len(pd.get("refs") or []) > ref_cap),
            "refs_blocked_reason": str(pd.get("blocked_reason") or ""),
            # 参考视频(2026-09-07「🎬 视频」):清单+总时长+按本组生效模型硬限判超(超限黄条,用户决定删或换模型)
            **_group_video_refs(base, ep, gid, pd, gres),
            "clips": [c for c in clips if gid and _id_name_match(gid, c["name"])],
            "caption_clips": [c for c in cap_clips
                              if gid and _id_name_match(gid, c["name"])],
            "boundaries_s": meta.get("boundaries_s") or [],
            "sketches": _sketch_list(base.name, ep, gid),
            "user_note": _grpnote_get(base.name, ep, gid).get("text", ""),
            "costumes": _group_costumes(g),
            # 组光照(2026-08-27):time_of_day + lighting_scheme_id → 场景 lighting.json 方案
            "lighting": _group_lighting(base, g, sb_scene_tod, lighting_cache),
        })
    data["generation_groups"] = groups
    # 配乐 cue:bgm/<ep>/cue_sheet.json → 预览页按 covers_groups/beat_ref/scene 对位试听;
    # 兼容 music_cues.json 文件名与 file 写成项目根相对路径(2026-09-02 liaozhai2 三集)
    bdir = base / "assets" / "audio" / "bgm" / ep
    cs = (_read_json_safe(bdir / "cue_sheet.json")
          or _read_json_safe(bdir / "music_cues.json") or {})

    def _cue_audio(f) -> str | None:
        if not isinstance(f, str) or not f.strip():
            return None
        return _audio_url(base, bdir / f) or _audio_url(base, base / f)

    data["bgm_cues"] = [
        {k: c.get(k) for k in ("cue_id", "in_s", "out_s", "scene",
                               "beat_ref", "covers_groups", "mood", "loop_fill")} | {
            "audio": _cue_audio(c.get("file"))}
        for c in (cs.get("cues") or []) if isinstance(c, dict)]
    return data


async def api_preview_storyboard(project: str = "demo", ep: str = ""):
    return await asyncio.to_thread(_preview_storyboard, project, ep)


def _project_media_tokens(base: Path):
    """项目级生成侧 token 累计消耗,返回 (video_tokens, image_tokens)。权威来源是
    项目内所有 usage_ledger.jsonl(genmedia 每次成功生成追加一行,含重roll/探针/
    候选,覆盖/删档不丢);台账未覆盖的成片再补 assets/clips/*/ meta.json 里的
    usage.completion_tokens(按同目录文件名去重)。候选晋级后同一次生成可能同时
    出现在 runs/ 台账与 clips meta,按 task_id 全局去重防止重复计数。
    图片/视频按台账 kind 字段或文件后缀分桶;无任何记录的桶返回 None(前端显示 —)。"""
    vid = img = 0
    vfound = ifound = False
    seen_tasks: set[str] = set()
    ledger_files: dict[Path, set[str]] = {}
    for ledger in base.rglob("usage_ledger.jsonl"):
        try:
            lines = ledger.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        names = ledger_files.setdefault(ledger.parent, set())
        for ln in lines:
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            names.add(str(rec.get("file") or ""))
            tid = str(rec.get("task_id") or "")
            if tid:
                if tid in seen_tasks:
                    continue
                seen_tasks.add(tid)
            t = rec.get("completion_tokens") or rec.get("total_tokens")
            if not isinstance(t, (int, float)):
                continue
            ext = Path(str(rec.get("file") or "")).suffix.lower()
            if rec.get("kind") == "image" or ext in IMG_EXTS:
                img += int(t)
                ifound = True
            else:
                vid += int(t)
                vfound = True
    clips = base / "assets" / "clips"
    if clips.is_dir():
        for cdir in clips.iterdir():
            if not cdir.is_dir():
                continue
            names = ledger_files.get(cdir, set())
            for mf in cdir.glob("*.meta.json"):
                if mf.name[:-len(".meta.json")] + ".mp4" in names:
                    continue
                u = (_read_json_safe(mf) or {}).get("usage") or {}
                tid = str(u.get("task_id") or "")
                if tid and tid in seen_tasks:
                    continue
                t = u.get("completion_tokens")
                if isinstance(t, (int, float)):
                    if tid:
                        seen_tasks.add(tid)
                    vid += int(t)
                    vfound = True
    return (vid if vfound else None), (img if ifound else None)


_LLM_INDEX_LOCK = threading.Lock()


def _parse_run_usage(path: Path):
    """解析单个 runs/<run_id>.jsonl 的 LLM token 消耗,返回 {"in","out"} 或 None。
    claude stream-json 末尾 result.usage(整个 run 的累计,grok 同格式);codex exec 末尾
    turn.completed.usage;pi 的每个 assistant message_end 各带一次调用用量;
    opencode 的每个 step_finish 各带一次调用用量(part.tokens);
    无终态事件(被停止/超时/仍在跑)则全文逐条累加。"""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 262144))
            tail = fh.read().decode("utf-8", "replace").splitlines()
        if size > 262144:
            tail = tail[1:]          # 掐掉可能被截断的首行
    except OSError:
        return None
    for ln in reversed(tail):
        try:
            d = json.loads(ln)
        except Exception:
            continue
        u = d.get("usage")
        if not isinstance(u, dict):
            continue
        if d.get("type") == "result":        # claude:input 三段是互斥口径,求和
            return {"in": int(u.get("input_tokens") or 0)
                          + int(u.get("cache_creation_input_tokens") or 0)
                          + int(u.get("cache_read_input_tokens") or 0),
                    "out": int(u.get("output_tokens") or 0)}
        if d.get("type") == "turn.completed":   # codex:input_tokens 已含 cached
            return {"in": int(u.get("input_tokens") or 0),
                    "out": int(u.get("output_tokens") or 0)}
    # claude 每次 API 调用发多条 assistant 事件(每内容块一条)携带同一 usage,
    # 必须按 message.id 去重取末次快照,直接累加会把输入算 ~2.4 倍(已实测:
    # 去重后 input 与 result.usage 完全一致;output 在流事件中只有极小快照值,
    # 会低估 ~1%,可接受——中断 run 的消耗以输入为绝对主体)。
    by_id, tin, tout, found = {}, 0, 0, False
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, ln in enumerate(fh):
                try:
                    d = json.loads(ln)
                except Exception:
                    continue
                t = d.get("type")
                if t == "assistant":
                    m = d.get("message") or {}
                    u = m.get("usage")
                    if isinstance(u, dict):
                        by_id[m.get("id") or i] = u
                        found = True
                elif t == "turn.completed":
                    u = d.get("usage")
                    if isinstance(u, dict):
                        tin += int(u.get("input_tokens") or 0)
                        tout += int(u.get("output_tokens") or 0)
                        found = True
                elif t == "message_end":       # pi:每轮模型调用(含工具往返)各一条
                    m = d.get("message") or {}
                    u = m.get("usage")
                    if m.get("role") == "assistant" and isinstance(u, dict):
                        tin += (int(u.get("input") or 0)
                                + int(u.get("cacheRead") or 0)
                                + int(u.get("cacheWrite") or 0))
                        tout += int(u.get("output") or 0)
                        found = True
                elif t == "step_finish":       # opencode:每次模型调用各一条
                    tok = (d.get("part") or {}).get("tokens")
                    if isinstance(tok, dict):
                        cache = tok.get("cache") or {}
                        tin += (int(tok.get("input") or 0)
                                + int(cache.get("read") or 0)
                                + int(cache.get("write") or 0))
                        tout += (int(tok.get("output") or 0)
                                 + int(tok.get("reasoning") or 0))
                        found = True
    except OSError:
        return None
    for u in by_id.values():
        tin += (int(u.get("input_tokens") or 0)
                + int(u.get("cache_creation_input_tokens") or 0)
                + int(u.get("cache_read_input_tokens") or 0))
        tout += int(u.get("output_tokens") or 0)
    return {"in": tin, "out": tout} if found else None


def _llm_usage_index() -> dict:
    """runs/*.jsonl → {run_id: {in, out, size}} 增量索引,缓存 runs/llm_usage_index.json。
    只重解析新增或大小变化的文件(运行中的 run 随日志增长自动刷新),日常请求开销≈一次
    目录 stat + 读缓存;首次全量构建约扫 1GB 日志,只发生一次。"""
    cache_p = RUNS_DIR / "llm_usage_index.json"
    with _LLM_INDEX_LOCK:
        try:
            cache = json.loads(cache_p.read_text(encoding="utf-8"))
            if not isinstance(cache, dict):
                cache = {}
        except Exception:
            cache = {}
        dirty, seen = False, set()
        for f in RUNS_DIR.glob("*.jsonl"):
            rid = f.stem
            seen.add(rid)
            try:
                size = f.stat().st_size
            except OSError:
                continue
            ent = cache.get(rid)
            if isinstance(ent, dict) and ent.get("size") == size:
                continue
            u = _parse_run_usage(f) or {"in": 0, "out": 0}
            cache[rid] = {"in": u["in"], "out": u["out"], "size": size}
            dirty = True
        for rid in [k for k in cache if k not in seen]:
            cache.pop(rid)
            dirty = True
        if dirty:
            tmp = cache_p.with_suffix(".tmp")
            tmp.write_text(json.dumps(cache), encoding="utf-8")
            tmp.replace(cache_p)
        return cache


def _project_llm_tokens(project: str):
    """项目级语言模型 token 消耗:runs/<run_id>.jsonl 的 usage 索引 × chats/<project>/
    派单记录的 run_id 归集(含未提集数的全局任务,run_id 去重)。
    口径 = 输入(含缓存写/读)+ 输出 的总和,含全部引擎(claude/codex/kimi/pi/opencode/grok/deepagents)。
    无任何记录返回 None(会话日志按 TTL 清理后查不到属正常,前端显示 —)。"""
    cdir = CHATS_DIR / safe_slug(project)
    if not cdir.is_dir():
        return None
    idx = _llm_usage_index()
    run_ids = set()
    for cf in cdir.glob("*.jsonl"):
        try:
            lines = cf.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("role") == "user" and r.get("run_id"):
                run_ids.add(str(r["run_id"]))
    total, found = 0, False
    for rid in run_ids:
        u = idx.get(rid)
        if u:
            total += u.get("in", 0) + u.get("out", 0)
            found = True
    return total if found else None


def _ep_publish_info(base: Path, ep: str):
    """publish/<ep>/ 发布物料聚合:metadata.json 关键字段、seo.json 标题备选/标签/简介、
    各平台子目录的 spec_report.json 核对结论与 package/ 发布包文件清单。目录不存在返回 None。"""
    pdir = base / "publish" / ep
    if not pdir.is_dir():
        return None
    info = {"metadata": None, "seo": None, "platforms": []}
    meta = _read_json_safe(pdir / "metadata.json")
    if isinstance(meta, dict):
        v = meta.get("video") or {}
        nxt = meta.get("next_episode") or {}
        info["metadata"] = {
            "date": meta.get("date"),
            "series": meta.get("series"),
            "episode_no": meta.get("episode_no"),
            "episode_total": meta.get("episode_total"),
            "episode_title": meta.get("episode_title"),
            "producer": meta.get("producer"),
            "production_credit": meta.get("production_credit"),
            "rating": meta.get("rating"),
            "made_for_kids": meta.get("rating_made_for_kids"),
            "age_restricted": meta.get("rating_age_restricted"),
            "source_file": v.get("source_file"),
            "duration_s": v.get("duration_s_measured") or v.get("duration_s_nominal"),
            "resolution": v.get("resolution"),
            "fps": v.get("fps"),
            "gate_verdict": v.get("h5_gate_verdict"),
            "next_episode": " ".join(str(nxt.get(k) or "") for k in ("ep", "title")).strip(),
        }
    seo = _read_json_safe(pdir / "seo.json")
    if isinstance(seo, dict):
        items = seo.get("items") or []
        it = items[0] if items and isinstance(items[0], dict) else {}
        info["seo"] = {
            "platform": (seo.get("meta") or {}).get("platform"),
            "titles": [t for t in (it.get("titles") or []) if isinstance(t, dict)],
            "picked": it.get("picked"),
            "tags": [str(t) for t in (it.get("tags") or [])],
            "description": str(it.get("description") or ""),
        }
    for d in sorted(x for x in pdir.iterdir()
                    if x.is_dir() and not x.name.startswith(".")):
        rep = _read_json_safe(d / "spec_report.json") or {}
        lint = rep.get("lint_summary") or {}
        files = []
        pkg = d / "package"
        if pkg.is_dir():
            for f in sorted(pkg.rglob("*")):
                if not f.is_file() or f.name.startswith("."):
                    continue
                st = f.stat()
                ext = f.suffix.lower()
                files.append({"name": f.relative_to(pkg).as_posix(),
                              "size_mb": round(st.st_size / 1048576, 1),
                              "kind": ("video" if ext in VIDEO_EXTS
                                       else "image" if ext in IMG_EXTS else "file"),
                              "url": (f"/projects/{base.name}/{f.relative_to(base).as_posix()}"
                                      f"?v={int(st.st_mtime)}")})
        info["platforms"].append({
            "platform": d.name,
            "verdict": rep.get("overall_verdict"),
            "uploaded": rep.get("uploaded"),
            "note": rep.get("note"),
            "lint_total": len(lint),
            "lint_flagged": {k: str(v) for k, v in lint.items()
                             if isinstance(v, str)
                             and v.lower() != "pass" and not v.startswith("pass（")},
            "files": files})
    return info


def _preview_videos(project: str, ep: str):
    """视频预览聚合:分集列表 + 指定集的成片(final)视频、封面 thumbnail、发布物料、审核缺陷工单。"""
    base = _proj_base(project)
    plan = _read_json_safe(base / "story" / "episode_plan.json") or {}
    plan_eps = {e.get("ep"): e for e in plan.get("episodes", [])
                if isinstance(e, dict) and e.get("ep")}
    eps = set(plan_eps)
    for sub in ("story/episodes", "directing", "edit"):
        d = base / sub
        if d.is_dir():
            eps |= {x.name for x in d.iterdir()
                    if x.is_dir() and not x.name.startswith(".")}
    episodes = [{"ep": e, "title": (plan_eps.get(e) or {}).get("title", "")}
                for e in sorted(eps)]
    ep = ep or (episodes[0]["ep"] if episodes else "")
    data = {"project": base.name, "episodes": episodes, "ep": ep}
    if not ep:
        return data
    ep = re.sub(r"[^\w\-]", "", ep)
    edir = base / "edit" / ep

    def _files(match: tuple, exts: tuple) -> list[dict]:
        if not edir.is_dir():
            return []
        out = []
        seen_inodes = set()
        for f in sorted(edir.rglob("*")):
            if not (f.is_file() and f.suffix.lower() in exts
                    and any(m in f.name.lower() for m in match)):
                continue
            st = f.stat()
            if st.st_ino in seen_inodes:   # final.mp4 可能是 master.mp4 的硬链接,去重
                continue
            seen_inodes.add(st.st_ino)
            out.append({"name": f.relative_to(edir).as_posix(),
                        "size_mb": round(st.st_size / 1048576, 1),
                        "url": (f"/projects/{base.name}/{f.relative_to(base).as_posix()}"
                                f"?v={int(st.st_mtime)}")})
        return out

    # 规范名是 final.mp4(WORKFLOW §2);兼容个别工单跑偏产出的 master.mp4 总装母版
    data["finals"] = _files(("final", "master"), VIDEO_EXTS)
    data["thumbnails"] = _files(("thumb",), IMG_EXTS)
    data["publish"] = _ep_publish_info(base, ep)

    # 审核缺陷工单 qa/defects/:JSON 结构化工单;MD/TXT 文字工单取首行做摘要
    defects = []
    ddir = base / "qa" / "defects"
    if ddir.is_dir():
        for f in sorted(ddir.iterdir()):
            if f.name.startswith("."):
                continue
            row = None
            if f.suffix.lower() == ".json":
                j = _read_json_safe(f)
                if isinstance(j, dict):
                    row = {k: j.get(k) for k in (
                        "defect_id", "severity", "blocking", "type", "status",
                        "artifact", "found_by", "task_id", "date", "violated",
                        "evidence", "impact", "recommended_fix", "assigned_to",
                        "resolution", "waiver")}
                    row["defect_id"] = row["defect_id"] or f.stem
            elif f.suffix.lower() in (".md", ".txt"):
                try:
                    txt = f.read_text()
                except Exception:
                    txt = ""
                first = next((ln.strip().lstrip("# ").strip()
                              for ln in txt.splitlines() if ln.strip()), "")
                row = {"defect_id": f.stem, "type": "文字工单",
                       "violated": first, "detail_text": txt[:6000]}
            if row is None:
                continue
            blob = " ".join(str(row.get(k) or "") for k in
                            ("defect_id", "task_id", "artifact", "violated"))
            row["eps"] = sorted(set(re.findall(r"ep\d+", blob.lower())))
            defects.append(row)
    data["defects"] = defects
    return data


async def api_preview_videos(project: str = "demo", ep: str = ""):
    return await asyncio.to_thread(_preview_videos, project, ep)


# ---------------- 工作流预览(DAG 可视化) ----------------
# 页面 preview_workflow.html:把 runs/dag.json 展开成阶段泳道图,标注状态/依赖/
# 签字节点,并按历史运行(runs/<task>/meta.json 的起止时刻,兜底
# data/.videoagents/runs/<run_id>.jsonl 的 duration_ms)给未完成任务估时。
# 金额不再统计:成本数据只有 claude 引擎会话上报(codex 等无美元口径),
# 且会话日志按 TTL 清理,数字必然不完整,展示反而误导。

_WF_TERMINAL_SKIP = {"skipped", "cancelled", "waived"}   # 不再执行,也不进剩余预估


def _wf_phase_of(n: dict) -> str:
    """节点所属阶段:优先落盘 phase 字段;否则按 id 前缀 pN-/gN 推断;加派的
    自由任务(如 ep06-costume-blue-arbitration)归入 extra。"""
    if n.get("phase"):
        return str(n["phase"])
    m = re.match(r"^[pg](\d+)\b", n.get("id") or "")
    return f"p{m.group(1)}" if m else "extra"


def _wf_family_of(n: dict, by_id: dict) -> str:
    """扇出实例归并键:沿 expanded_from 链回溯到根;无该字段(tothemoon 旧格式)
    则按 for_each_instance 从 id 里剥掉实例段(p6-plan-ep01 → p6-plan)。"""
    nid = n.get("id") or ""
    seen = {nid}
    cur = n
    while cur.get("expanded_from") and cur["expanded_from"] not in seen:
        nid = cur["expanded_from"]
        seen.add(nid)
        cur = by_id.get(nid) or {"id": nid}
    inst = cur.get("for_each_instance") if cur is n else None
    if isinstance(inst, str) and inst:
        # 实例段可能是 ep01 / ep05/grp015 之类,取各段依次从尾部剥离
        for part in reversed(re.split(r"[/,]", inst)):
            part = part.strip()
            if part and nid.endswith("-" + part):
                nid = nid[: -len(part) - 1]
    return nid


def _wf_run_stats(run_id: str | None) -> float | None:
    """从 data/.videoagents/runs/<run_id>.jsonl 末尾的 result 事件取 duration_s
    (claude/kimi 引擎的 stream-json 才有该事件)。会话日志会按 TTL 清理,查不到属正常。"""
    if not run_id or not re.fullmatch(r"[\w.\-]+", run_id):
        return None
    p = RUNS_DIR / f"{run_id}.jsonl"
    if not p.is_file():
        return None
    try:
        with open(p, "rb") as f:
            f.seek(max(0, p.stat().st_size - 16384))
            tail = f.read().decode("utf-8", "replace")
    except Exception:
        return None
    for line in reversed(tail.splitlines()):
        if '"type":"result"' not in line and '"type": "result"' not in line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        dur = obj.get("duration_ms")
        return (dur / 1000) if isinstance(dur, (int, float)) else None
    return None


def _wf_meta_duration(base: Path, task_id: str) -> float | None:
    """runs/<task_id>/meta.json 的 started_at → ended_at/finished_at → 秒。
    结束字段两种写法并存(WORKFLOW.md §6.1 规约 finished_at,存量多为 ended_at)。"""
    meta = _read_json_safe(base / "runs" / task_id / "meta.json")
    if not isinstance(meta, dict):
        return None
    ended = meta.get("ended_at") or meta.get("finished_at")
    try:
        t0 = datetime.fromisoformat(str(meta["started_at"]).replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(str(ended).replace("Z", "+00:00"))
        s = (t1 - t0).total_seconds()
        return s if 0 < s < 86400 * 7 else None
    except Exception:
        return None


def _preview_workflow(project: str):
    base = _proj_base(project)
    dag_path = base / "runs" / "dag.json"
    if not dag_path.is_file():
        return {"project": base.name, "nodes": [], "summary": None}
    raw = _dag_load_nodes(dag_path)
    by_id = {n["id"]: n for n in raw if n.get("id")}

    nodes = []
    for n in raw:
        if not n.get("id"):
            continue
        state = n.get("state") or "pending"
        nid = n["id"]
        dur = _wf_meta_duration(base, nid)
        if dur is None:
            dur = _wf_run_stats(n.get("run_id"))
        note = next((str(n[k]) for k in ("note", "orch_note", "skip_reason",
                                         "fail_reason", "pilot_note") if n.get(k)), "")
        nodes.append({
            "id": nid, "phase": _wf_phase_of(n), "state": state,
            "family": _wf_family_of(n, by_id),
            "depends_on": [d for d in (n.get("depends_on") or [])
                           if isinstance(d, str)],
            "agent": n.get("agent"), "gate": bool(n.get("gate")),
            "human": bool(n.get("human")), "checkpoint": n.get("checkpoint"),
            "for_each": n.get("for_each"),
            "for_each_instance": n.get("for_each_instance"),
            "outputs": n.get("outputs") or [], "attempt": n.get("attempt"),
            "run_id": n.get("run_id"), "note": note[:400],
            "duration_s": round(dur) if dur is not None else None,
        })

    # 估时:同族均值 → 同 agent 均值 → 同阶段均值 → 全局均值
    def _avg(rows):
        vals = [r["duration_s"] for r in rows if r["duration_s"] is not None]
        return (sum(vals) / len(vals)) if vals else None

    done_rows = [r for r in nodes if r["state"] in _DONE_STATES]
    fam_rows: dict[str, list] = {}
    ag_rows: dict[str, list] = {}
    ph_rows: dict[str, list] = {}
    for r in done_rows:
        fam_rows.setdefault(r["family"], []).append(r)
        if r["agent"]:
            ag_rows.setdefault(r["agent"], []).append(r)
        ph_rows.setdefault(r["phase"], []).append(r)

    for r in nodes:
        if r["state"] in _DONE_STATES or r["state"] in _WF_TERMINAL_SKIP \
                or r["state"] in ("expanded", "template"):
            continue
        for basis, rows in (("family", fam_rows.get(r["family"])),
                            ("agent", ag_rows.get(r["agent"])),
                            ("phase", ph_rows.get(r["phase"])),
                            ("overall", done_rows)):
            v = _avg(rows or [])
            if v is not None:
                r["est_duration_s"] = round(v)
                r["est_duration_basis"] = basis
                break

    # 汇总:已花费(实际)/ 剩余(估)/ 关键路径(剩余任务沿依赖的最长估时链)
    remaining = [r for r in nodes
                 if r["state"] not in _DONE_STATES
                 and r["state"] not in _WF_TERMINAL_SKIP
                 and r["state"] not in ("expanded", "template")]
    node_map = {r["id"]: r for r in nodes}
    memo: dict[str, float] = {}

    def _cp(nid: str, stack: frozenset) -> float:
        if nid in memo:
            return memo[nid]
        r = node_map.get(nid)
        if r is None or nid in stack:
            return 0.0
        own = 0.0
        if r["state"] not in _DONE_STATES and r["state"] not in _WF_TERMINAL_SKIP:
            own = float(r.get("est_duration_s") or 0)
        best = 0.0
        for d in r["depends_on"]:
            best = max(best, _cp(d, stack | {nid}))
        memo[nid] = own + best
        return memo[nid]

    critical_s = max((_cp(r["id"], frozenset()) for r in remaining), default=0.0)
    video_tok, image_tok = _project_media_tokens(base)
    summary = {
        "total": len(nodes),
        "spent_duration_s": round(sum(r["duration_s"] or 0 for r in nodes)),
        "video_tokens": video_tok,
        "image_tokens": image_tok,
        "llm_tokens": _project_llm_tokens(project),
        "remaining_count": len(remaining),
        "remaining_est_duration_s": round(sum(r.get("est_duration_s") or 0
                                              for r in remaining)),
        "critical_path_s": round(critical_s),
    }
    return {"project": base.name, "nodes": nodes, "summary": summary}


async def api_preview_workflow(project: str = "demo"):
    return await asyncio.to_thread(_preview_workflow, project)


async def api_genconfig_get():
    cfg = load_genconfig()
    cfg["ui_language"] = ui_lang_code(cfg)   # 语言以 state.json 为准(旧版存 genconfig)
    return cfg


def _flat_diff(old, new, prefix="") -> list[str]:
    """递归比较两份配置,返回「路径: 旧 → 新」清单;密钥类字段不回显明文。"""
    out = []
    for k in sorted(set(old or {}) | set(new or {})):
        ov, nv = (old or {}).get(k), (new or {}).get(k)
        if ov == nv:
            continue
        path = f"{prefix}{k}"
        if isinstance(ov, dict) or isinstance(nv, dict):
            out += _flat_diff(ov if isinstance(ov, dict) else {},
                              nv if isinstance(nv, dict) else {}, path + ".")
        elif any(t in k.lower() for t in ("key", "token", "secret")):
            out.append(f"{path}: (已更新,不回显)")
        else:
            out.append(f"{path}: {json.dumps(ov, ensure_ascii=False)}"
                       f" → {json.dumps(nv, ensure_ascii=False)}")
    return out


async def _notify_settings_change(project: str, label: str, changes: list[str]):
    """设置保存后自动知会总制片,由其通知依赖该配置的 agent,避免继续按旧配置执行。"""
    if not changes:
        return
    orch = next(iter(DISPATCHERS))
    lines = "\n".join(f"- {c}" for c in changes[:40])
    msg = (f"[设置变更] 用户刚在控制台更新了「{label}」,差异:\n{lines}\n"
           "请评估影响范围并通知相关 agent 更新配置认知:在跑/待派任务中依赖旧配置的,"
           "在派单工单里注明以最新配置为准;已按旧配置产出且已过审的产物不重做,"
           "除非与新配置冲突。若无受影响任务,简要确认记录后结束,不要额外派活。")
    try:
        gm = agent_effective_model(orch)
        await api_chat({"agent": orch, "message": msg, "project": project,
                        "source": "settings",
                        "engine": gm["engine"], "model": gm["model"]})
    except Exception as e:  # noqa: BLE001
        print(f"[settings-notify] 通知总制片失败(忽略):{e}", flush=True)


async def _refresh_rh_wf_caches(cfg: dict) -> list[dict]:
    """把各类别当前选中的 RunningHub 工作流本地缓存同步为云端最新版(保存设置时调用)。

    genmedia 提交走本地缓存的工作流 JSON 整包且缓存不过期:用户在 RunningHub 网页端
    改过工作流(如换扩散模型)后,不重拉缓存改动就不会生效。刷新失败不阻断保存
    (提交沿用旧缓存),逐条结果返回给设置页回显。"""
    jobs = {}
    for kind in ("image", "video", "music", "tts"):
        comfy = (cfg.get(kind) or {}).get("comfyui") or {}
        mode = str(comfy.get("mode") or "")
        if mode not in RH_BASES:
            continue
        key = (str(comfy.get(f"rh_api_key_{mode[3:]}") or "").strip()
               or str(comfy.get("rh_api_key") or "").strip())
        if not key:
            continue
        for field in ("rh_workflow_id", "rh_ref_workflow_id"):
            wf_id = str(comfy.get(field) or "").strip()
            if wf_id:
                jobs.setdefault((mode, wf_id), key)
    # 数字人的 ComfyUI 渠道同口径(mode=rh_* + rh_* 字段)
    dh = cfg.get("digital_human") or {}
    if dh.get("provider") == "comfyui":
        comfy = dh.get("comfyui") or {}
        mode = str(comfy.get("mode") or "local")
        if mode in RH_BASES:
            key = str(comfy.get(f"rh_api_key_{mode[3:]}") or "").strip()
            wf_id = str(comfy.get("rh_workflow_id") or "").strip()
            if key and wf_id:
                jobs.setdefault((mode, wf_id), key)
    if not jobs:
        return []

    async def sync(mode: str, wf_id: str, key: str) -> dict:
        item = {"id": wf_id, "mode": mode}
        try:
            resp = await asyncio.to_thread(
                _http_post_json, RH_BASES[mode] + "/api/openapi/getJsonApiFormat",
                {"apiKey": key, "workflowId": wf_id},
                {"Authorization": f"Bearer {key}"}, 30)
            text = (resp.get("data") or {}).get("prompt") if resp.get("code") == 0 else None
            if not text:
                raise RuntimeError(f"code={resp.get('code')}: {str(resp.get('msg'))[:120]}")
            new_wf = json.loads(text)  # 接口偶发回异常内容,坏 JSON 不落缓存
            cache = RH_CACHE_DIR / f"{mode}-{wf_id}.json"
            old = cache.read_text(encoding="utf-8") if cache.is_file() else None
            try:
                old_wf = json.loads(old) if old is not None else None
            except json.JSONDecodeError:
                old_wf = None
            if old_wf == new_wf and old_wf is not None:
                # 语义比较:接口偶发序列化抖动(键序/空白),字节不同不代表工作流变了
                item["status"] = "unchanged"
            else:
                RH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache.write_text(text, encoding="utf-8")
                # created=首次缓存(genmedia 本就会现拉,不算行为变化);updated=覆盖旧版
                item["status"] = "updated" if old_wf is not None else "created"
        except Exception as e:  # noqa: BLE001
            item["status"] = "error"
            item["detail"] = str(e)[:200]
        return item

    return list(await asyncio.gather(*(sync(m, w, k) for (m, w), k in jobs.items())))


async def api_genconfig_set(body: dict):
    body = dict(body or {})
    # project 仅用于「设置变更」通知的会话归属(genconfig 本身是全局配置),不落盘
    project = safe_slug(body.pop("project", None))
    old = load_genconfig()
    cfg = _merge(load_genconfig(), body)
    for kind in ("image", "video", "music", "tts", "digital_human", "deepagents"):
        allowed = set(DEFAULT_GENCONFIG[kind]) - {"provider"}
        if cfg.get(kind, {}).get("provider") not in allowed:
            raise ServiceError(400, f"{kind}.provider must be one of {sorted(allowed)}")
    if any((cfg.get(kind) or {}).get("provider") == "agentics"
           for kind in ("image", "video", "music", "tts", "deepagents")):
        resolve_agentics_connection()
    dh_comfy = (cfg.get("digital_human") or {}).get("comfyui") or {}
    if dh_comfy.get("mode") not in ("local", "cloud", *RH_BASES):
        raise ServiceError(400, "digital_human.comfyui.mode must be local, cloud, "
                                f"or one of {sorted(RH_BASES)}")
    if dh_comfy.get("rh_instance_type") not in {"standard", "plus", "ultra"}:
        raise ServiceError(400, "digital_human.comfyui.rh_instance_type must be standard, plus or ultra")
    if cfg.get("agentmodel_mode") not in AM_MODES:
        raise ServiceError(400, f"agentmodel_mode must be one of {AM_MODES}")
    if cfg.get("ui_language") not in ("", *UI_LANG_NAMES):
        raise ServiceError(400, f"ui_language must be one of {sorted(UI_LANG_NAMES)} or empty")
    storage = cfg.get("storage") or {}
    allowed_st = set(DEFAULT_GENCONFIG["storage"]) - {"provider"}
    if storage.get("provider") not in allowed_st:
        raise ServiceError(400, f"storage.provider must be one of {sorted(allowed_st)}")
    for name, pc in storage.items():
        if not isinstance(pc, dict):
            continue
        try:
            pc["url_expires"] = int(pc.get("url_expires") or 86400)
        except (TypeError, ValueError):
            raise ServiceError(400, f"storage.{name}.url_expires must be seconds (integer)")
    save_genconfig(cfg)
    if "agentmodel_mode" in body:
        # 切换「模型策略」= 全部 Agent 按新策略走:同时清空 Agent 级单独配置
        atomic_write_json(AGENTMODELS_PATH, {})
    if "ui_language" in body:
        # 界面语言按全局持久化到 state.json(genconfig 键双写,兼容旧版回读)
        STATE["ui_lang"] = cfg.get("ui_language") or ""
        save_state(STATE)
    lang_only = set(body) <= {"ui_language"}
    if lang_only and not old.get("ui_language"):
        # 首次打开浏览器自动判定语言的静默初始化:不知会总制片
        return {"ok": True, "config": cfg}
    # 顶栏引擎/语言模型切换只带 agentmodel_mode:与生成模型渠道无关,跳过 RH 缓存同步
    mode_only = set(body) <= {"agentmodel_mode"}
    # RunningHub 工作流缓存随保存同步云端最新版:genmedia 提交走本地缓存整包,
    # 用户在 RH 网页端改过的工作流不重拉不生效;失败沿用旧缓存,不阻断保存
    rh_refresh = [] if (lang_only or mode_only) else await _refresh_rh_wf_caches(cfg)
    changes = _flat_diff(old, cfg)
    # 云端工作流内容变了但配置本身无 diff 时,也要让总制片知会相关 agent
    changes += [f"RunningHub 工作流缓存已同步云端最新版: {it['mode']}-{it['id']}"
                for it in rh_refresh if it["status"] == "updated"]
    await _notify_settings_change(
        project, "界面语言" if lang_only
        else "语言模型分配策略" if mode_only else "生成模型", changes)
    return {"ok": True, "config": cfg, "rh_cache_refresh": rh_refresh}


BRIEF_HEADER = "# 主创构想"
BRIEF_SECTION = "## 主要构想"
STYLE_SECTION = "## 设计风格"
RHYTHM_SECTION = "## 叙事节奏"   # 2026-09-07:单集/跨集叙事节奏,正文=节拍链文本(目录见 services/runtime/rhythm.py)


def parse_brief(text: str) -> tuple:
    """brief.md 正文 → (主要构想, 设计风格, 叙事节奏结构)。兼容旧格式(无小节标题=全文即主要构想)。

    叙事节奏结构 = rhythm.normalize 形状 {episode, episode_custom, season, season_custom}。
    小节顺序不限:按各小节标题出现位置切分。"""
    text = (text or "").strip()
    if text.startswith(BRIEF_HEADER):
        text = text[len(BRIEF_HEADER):].strip()
    marks = sorted((i, sec) for sec in (STYLE_SECTION, RHYTHM_SECTION)
                   if (i := text.find(sec)) >= 0)
    parts = {}
    end = len(text)
    for i, sec in reversed(marks):
        parts[sec] = text[i + len(sec):end].strip()
        end = i
    brief = text[:end].strip()
    if brief.startswith(BRIEF_SECTION):
        brief = brief[len(BRIEF_SECTION):].strip()
    return brief, parts.get(STYLE_SECTION, ""), narrative_rhythm.parse_section(parts.get(RHYTHM_SECTION, ""))


def format_brief(brief: str, style: str, rhythm: dict | None = None) -> str:
    """(主要构想, 设计风格, 叙事节奏) → brief.md 全文;三者皆空返回 ''(表示应删除文件)。"""
    parts = [BRIEF_HEADER]
    if brief:
        parts.append(f"{BRIEF_SECTION}\n\n{brief}")
    if style:
        parts.append(f"{STYLE_SECTION}\n\n{style}")
    rhythm_text = narrative_rhythm.format_section(rhythm)
    if rhythm_text:
        parts.append(f"{RHYTHM_SECTION}\n\n{rhythm_text}")
    return "\n\n".join(parts) + "\n" if len(parts) > 1 else ""


async def api_rhythms_get():
    """叙事节奏目录(单集/跨集两层;新建项目向导与设计构想弹窗共用)。"""
    return narrative_rhythm.catalog()


async def api_brief_get(project: str = "demo"):
    """当前项目的设计构想:brief.md 的主要构想 + 设计风格 + 叙事节奏三个字段。"""
    p = PROJECTS_DIR / safe_slug(project) / "brief.md"
    brief, style, rhythm = parse_brief(p.read_text() if p.is_file() else "")
    return {"project": project, "brief": brief, "style": style, "rhythm": rhythm}


async def api_brief_set(body: dict):
    """保存设计构想(主要构想+设计风格+叙事节奏)到 data/projects/<项目>/brief.md;全部清空即移除该设定。"""
    project = safe_slug(body.get("project"))
    if not (PROJECTS_DIR / project).is_dir():
        raise ServiceError(404, f"Project not found: {project}")
    brief = str(body.get("brief") or "").strip()
    style = str(body.get("style") or "").strip()
    rhythm = narrative_rhythm.normalize(body.get("rhythm") if isinstance(body.get("rhythm"), dict) else None)
    p = PROJECTS_DIR / project / "brief.md"
    old = p.read_text().strip() if p.is_file() else ""
    text = format_brief(brief, style, rhythm)
    if text:
        p.write_text(text)
    elif p.is_file():
        p.unlink()
    new = p.read_text().strip() if p.is_file() else ""
    if new != old:
        await _notify_settings_change(project, "设计构想", [
            f"brief.md 已更新,最新全文:\n{new[:1500]}" if new
            else "brief.md 已清空(移除主创构想、设计风格与叙事节奏设定)"])
    return {"ok": True, "project": project, "brief": brief, "style": style, "rhythm": rhythm}


# ---------------- 参考文件页(refs/ 分类预览、上传、逐文件注释) ----------------
REF_CATEGORIES = ("style", "thumbnail", "characters", "scenes", "props", "music", "video", "text")
REF_SKIP_FILES = {"README.md", "NOTES.md", "annotations.json"}
AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg")
REF_VIDEO_EXTS = (".mp4", ".mov", ".webm", ".m4v", ".mkv")
REF_NOTES_BEGIN = "<!-- BEGIN videoagents-refs-notes 本块由客户端「参考文件」页自动生成,勿手改;手写内容请放本块之外 -->"
REF_NOTES_END = "<!-- END videoagents-refs-notes -->"
MAX_REF_UPLOAD = 100 * 1024 * 1024


def _load_ref_notes(refs: Path) -> dict:
    data = _read_json_safe(refs / "annotations.json")
    return data if isinstance(data, dict) else {}


def _write_ref_notes_md(refs: Path, notes: dict):
    """annotations.json → NOTES.md 自动块(仅重写标记内内容,标记外的手写内容保留)。"""
    lines = []
    for rel in sorted(notes):
        v = notes[rel]
        note = str(v.get("note") if isinstance(v, dict) else v or "").strip()
        if note:
            lines.append(f"- `{rel}`:" + note.replace("\n", "\n  "))
    block = REF_NOTES_BEGIN + "\n" + ("\n".join(lines) or "(暂无注释)") + "\n" + REF_NOTES_END
    p = refs / "NOTES.md"
    if p.is_file():
        txt = p.read_text()
        if REF_NOTES_BEGIN in txt and REF_NOTES_END in txt:
            head, rest = txt.split(REF_NOTES_BEGIN, 1)
            _, tail = rest.split(REF_NOTES_END, 1)
            txt = head + block + tail
        else:
            txt = txt.rstrip() + "\n\n" + block + "\n"
    else:
        txt = "# refs/ 逐图注释(哪张图管什么、哪首曲子想用在哪)\n\n" + block + "\n"
    p.write_text(txt)


async def api_refs_list(project: str = "demo"):
    """参考文件页数据:refs/ 按分类列出文件(含子目录)、预览 URL 与注释。"""
    base = _proj_base(project)
    refs = base / "refs"
    notes = _load_ref_notes(refs)

    def entry(f: Path) -> dict:
        rel = f.relative_to(refs).as_posix()
        ext = f.suffix.lower()
        st = f.stat()
        v = notes.get(rel)
        return {"path": rel, "name": f.name,
                "url": f"/projects/{base.name}/refs/{rel}?v={int(st.st_mtime)}",
                "is_image": ext in IMG_EXTS, "is_audio": ext in AUDIO_EXTS,
                "is_video": ext in REF_VIDEO_EXTS,
                "size": st.st_size,
                "note": str(v.get("note") if isinstance(v, dict) else v or "").strip()}

    def listing(d: Path, recursive: bool) -> list:
        if not d.is_dir():
            return []
        files = (f for f in (d.rglob("*") if recursive else d.glob("*"))
                 if f.is_file() and not f.name.startswith(".")
                 and f.name not in REF_SKIP_FILES)
        return [entry(f) for f in sorted(files, key=lambda f: str(f).lower())]

    cats = [{"key": c, "files": listing(refs / c, True)} for c in REF_CATEGORIES]
    root = listing(refs, False)          # 散放在 refs/ 根的文件 = 整体风格参考
    if root:
        cats.append({"key": "", "files": root})
    return {"project": base.name, "categories": cats}


async def api_refs_upload(data: bytes, project: str = "demo",
                          category: str = "style", subdir: str = "",
                          filename: str = "ref"):
    """参考文件页上传:请求体即文件原始字节(避免 multipart 依赖)。
    文件名按工作流纪律清洗为 ASCII;subdir 用于 characters/<角色>、props/costumes 等定向子目录。"""
    base = _proj_base(project)
    if category not in REF_CATEGORIES:
        raise ServiceError(400, f"category must be one of {REF_CATEGORIES}")
    if not data:
        raise ServiceError(400, "empty upload body")
    if len(data) > MAX_REF_UPLOAD:
        raise ServiceError(400, "file too large (>100MB)")
    stem, ext = os.path.splitext(filename)
    ext = re.sub(r"[^a-z0-9.]", "", ext.lower())[:10]
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem).strip("._-")
    stem = re.sub(r"_{2,}", "_", stem)[:80] or "ref"
    sub = re.sub(r"[^A-Za-z0-9._-]", "_", subdir).strip("._-")[:60]
    d = (base / "refs" / category / sub) if sub else (base / "refs" / category)
    d.mkdir(parents=True, exist_ok=True)
    p, i = d / f"{stem}{ext}", 1
    while p.exists():
        p, i = d / f"{stem}_{i}{ext}", i + 1
    p.write_bytes(data)
    rel = p.relative_to(base / "refs").as_posix()
    return {"ok": True, "project": base.name, "path": rel,
            "url": f"/projects/{base.name}/refs/{rel}?v={int(p.stat().st_mtime)}"}


async def api_refs_note(body: dict):
    """保存/编辑参考文件注释:写 refs/annotations.json 并同步 NOTES.md 自动块
    (NOTES.md 是 workflow 既有约定的「有则必读」文件,视觉/配乐 Agent 由此读到逐图注释)。"""
    base = _proj_base(body.get("project"))
    refs = base / "refs"
    rel = str(body.get("path") or "").strip().strip("/")
    if not rel or any(part in ("", ".", "..") for part in rel.split("/")):
        raise ServiceError(400, "invalid path")
    target = refs / rel
    if not target.is_file():
        raise ServiceError(404, f"No such ref file: {rel}")
    note = str(body.get("note") or "").strip()
    notes = _load_ref_notes(refs)
    if note:
        notes[rel] = {"note": note,
                      "updated": datetime.now().isoformat(timespec="seconds")}
    else:
        notes.pop(rel, None)
    atomic_write_json(refs / "annotations.json", notes)
    _write_ref_notes_md(refs, notes)
    return {"ok": True, "project": base.name, "path": rel, "note": note}


async def api_refs_delete(body: dict):
    """删除参考文件:移除文件与其注释(annotations.json + NOTES.md 同步),空子目录顺带清掉。"""
    base = _proj_base(body.get("project"))
    refs = base / "refs"
    rel = str(body.get("path") or "").strip().strip("/")
    if not rel or any(part in ("", ".", "..") for part in rel.split("/")):
        raise ServiceError(400, "invalid path")
    target = refs / rel
    if not target.is_file():
        raise ServiceError(404, f"No such ref file: {rel}")
    target.unlink()
    # 清理删空的子目录(保留分类目录与 refs/ 本身)
    d = target.parent
    while d != refs and d.parent != refs and not any(d.iterdir()):
        d.rmdir()
        d = d.parent
    notes = _load_ref_notes(refs)
    if notes.pop(rel, None) is not None:
        atomic_write_json(refs / "annotations.json", notes)
        _write_ref_notes_md(refs, notes)
    return {"ok": True, "project": base.name, "path": rel}


async def api_projconfig_get(project: str = "demo"):
    """项目级设置(输出设置/时长设置/审核设置),每个项目独立。"""
    return load_project_settings(project)


PROJ_SETTING_LABELS = {"output": "输出设置", "duration": "时长设置",
                       "shot_group": "视频模型设置",
                       "review": "审核设置", "packaging": "片头片尾",
                       "versioning": "版本管理", "prompt_skill": "提示词技能",
                       "project_skills": "项目技能"}


async def api_projconfig_set(body: dict):
    project = safe_slug(body.get("project"))
    old = load_project_settings(project)
    if "project_skills" in body:
        _validate_project_skills(body["project_skills"])
        known = {s["id"] for s in scan_agent_skills(refresh=True)}
        unknown = set(body["project_skills"]["overrides"]) - known
        if unknown:
            raise ServiceError(400, "unknown skill id: " + ", ".join(sorted(unknown)))
    cfg = _merge(load_project_settings(project),
                 {k: v for k, v in (body or {}).items()
                  if k in PROJECT_SETTINGS_KEYS})
    _validate_duration(cfg.get("duration") or {})
    _validate_shot_group(cfg.get("shot_group") or {})
    _validate_output(cfg.get("output") or {})
    _validate_review(cfg.get("review") or {})
    _validate_packaging(cfg.get("packaging") or {})
    _validate_versioning(cfg.get("versioning") or {})
    _validate_prompt_skill(cfg.get("prompt_skill") or {})
    _validate_project_skills(cfg.get("project_skills"))
    ensure_project(project)
    project_settings_path(project).write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2))
    # 提示词技能快照随设置一起刷新(模式/指定技能/视频模型任一变化都会体现在 effective 里);
    # 快照本身的变化不计入「设置变更」通知(那是派生值,不是用户改的)
    cfg = sync_prompt_skill_effective(project)
    if "project_skills" in body:
        sync_group_settings_effective(project)
    old.setdefault("prompt_skill", {})["effective"] = (cfg.get("prompt_skill") or {}).get("effective")
    changes = _flat_diff(old, cfg)
    secs = {c.split(":")[0].split(".")[0] for c in changes}
    label = "、".join(v for k, v in PROJ_SETTING_LABELS.items() if k in secs)
    await _notify_settings_change(project, label or "项目设置", changes)
    return {"ok": True, "project": project, "config": cfg}


OPENROUTER_TTS_MODELS = [
    ("x-ai/grok-voice-tts-1.0", "Grok Voice TTS 1.0(xAI)· 20+语言/5音色(eve/ara/rex/sal/leo)"),
    ("microsoft/mai-voice-2", "MAI-Voice-2(微软)· 英/西/法/德(en-US-Harper:MAI-Voice-2 等)"),
    ("mistralai/voxtral-mini-tts-2603", "Voxtral Mini TTS(Mistral)· 英/法,音色带情绪(en_paul_neutral 等)"),
    ("hexgrad/kokoro-82m", "Kokoro 82M · 唯一含中文音色(zf_xiaoxiao/zm_yunxi 等)/多语言/快"),
    ("zyphra/zonos-v0.1-transformer", "Zonos v0.1 Transformer(Zyphra)· 英文 american/british 音色"),
    ("zyphra/zonos-v0.1-hybrid", "Zonos v0.1 Hybrid(Zyphra)· 英文 american/british 音色"),
    ("sesame/csm-1b", "CSM 1B(Sesame)· 英文对话/朗读音色(conversational/read_speech)"),
    ("canopylabs/orpheus-3b-0.1-ft", "Orpheus 3B(Canopy)· 英文 7 音色(tara/leah/leo 等)"),
]

AGENTICS_MEDIA_TYPES = {"video": 1, "image": 2, "music": 3, "tts": 4}


def _agentics_response_data(payload: dict) -> dict:
    """Unwrap the Agentics API's ``{code,msg,data}`` response envelope."""
    if not isinstance(payload, dict):
        raise ServiceError(502, "Agentics service returned an invalid response")
    if "code" not in payload:
        return payload
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        raise ServiceError(502, str(payload.get("msg") or "Agentics service request failed")[:300])
    return payload["data"]


async def api_agentics_models(modality: str = "image", refresh: bool = False):
    """List signed-in Agentics media profiles or AgenticsLLM text models."""
    del refresh  # Service-side ordering and freshness are authoritative.
    if modality not in (*AGENTICS_MEDIA_TYPES, "media", "text"):
        raise ServiceError(400, "modality must be one of image / video / music / tts / media / text")
    connection = resolve_agentics_connection()
    headers = {"Authorization": f"Bearer {connection['api_key']}"}
    try:
        if modality == "text":
            payload = await asyncio.to_thread(
                _http_get_json, connection["api_origin"] + "/v1/agent_models", headers, 15)
            values = _agentics_response_data(payload).get("models") or []
            models = [{"id": m, "name": m} for m in values if isinstance(m, str) and m]
            return {"models": models, "profiles": [], "cached": False}
        query = ("" if modality == "media" else
                 "?" + urllib.parse.urlencode({"media_type": AGENTICS_MEDIA_TYPES[modality]}))
        payload = await asyncio.to_thread(
            _http_get_json,
            connection["api_origin"] + "/v1/media_generation/profiles" + query,
            headers, 15)
        profiles = _agentics_response_data(payload).get("profiles") or []
        profiles = [p for p in profiles if isinstance(p, dict) and p.get("profile_code")]
        if modality == "media":
            type_to_modality = {value: key for key, value in AGENTICS_MEDIA_TYPES.items()}
            media = {kind: {"models": [], "profiles": []} for kind in AGENTICS_MEDIA_TYPES}
            for profile in profiles:
                kind = type_to_modality.get(profile.get("media_type"))
                if not kind:
                    continue
                media[kind]["profiles"].append(profile)
                media[kind]["models"].append({
                    "id": profile["profile_code"],
                    "name": profile.get("name") or profile["profile_code"],
                })
            return {"media": media, "cached": False}
        return {
            "models": [{"id": p["profile_code"], "name": p.get("name") or p["profile_code"]}
                       for p in profiles],
            "profiles": profiles,
            "cached": False,
        }
    except ServiceError:
        raise
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise ServiceError(e.code if e.code in (401, 403) else 502,
                           f"Agentics service HTTP {e.code}: {detail}") from e
    except Exception as e:  # noqa: BLE001
        raise ServiceError(502, f"Failed to fetch Agentics model list: {str(e)[:300]}") from e


async def api_openrouter_models(modality: str = "image", refresh: bool = False):
    """列出 OpenRouter 目录里的模型(image/video/music/tts/text,带 10 分钟缓存)。"""
    if modality not in ("image", "video", "music", "tts", "text"):
        raise ServiceError(400, "modality must be one of image / video / music / tts / text")
    if modality == "tts":
        # TTS 模型无公开目录端点(/audio/speech 专用),返回内置清单;自定义 ID 走前端「自定义…」
        return {"models": [{"id": i, "name": n} for i, n in OPENROUTER_TTS_MODELS], "cached": True}
    cached = _OPENROUTER_CACHE.get(modality)
    if cached and not refresh and time.time() - cached[0] < _OPENROUTER_TTL:
        return {"models": cached[1], "cached": True}
    # image/video 有专门的媒体模型目录;music/text 无专目录,走总目录(music 按输出模态 audio 过滤)
    url = ("https://openrouter.ai/api/v1/models" if modality in ("music", "text")
           else f"https://openrouter.ai/api/v1/{modality}s/models")
    try:
        data = await asyncio.to_thread(_http_get_json, url)
    except Exception as e:  # noqa: BLE001
        raise ServiceError(502, f"Failed to fetch OpenRouter model list: {e}") from e
    models = []
    for m in data.get("data", []):
        if m.get("id") == "openrouter/auto":
            continue
        out_mods = (m.get("architecture") or {}).get("output_modalities") or []
        if modality == "music" and "audio" not in out_mods:
            continue
        if modality == "text" and "text" not in out_mods:
            continue
        models.append({"id": m.get("id"), "name": m.get("name") or m.get("id")})
    models.sort(key=lambda x: x["id"] or "")
    _OPENROUTER_CACHE[modality] = (time.time(), models)
    return {"models": models, "cached": False}


# ---------------- ElevenLabs 音色(设置页 TTS → ElevenLabs 用) ----------------

def _elevenlabs_json(url: str, api_key: str, payload: dict | None = None) -> dict:
    """带 xi-api-key 的 ElevenLabs API 调用(payload 非空则 POST)。"""
    headers = {"xi-api-key": api_key}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise ServiceError(502, f"ElevenLabs HTTP {e.code}: {body}") from e
    except Exception as e:  # noqa: BLE001
        raise ServiceError(502, f"ElevenLabs request failed: {e}") from e


def _elevenlabs_norm(v: dict) -> dict:
    """账号音色(labels 子对象)与 Voice Library 音色(平铺字段)归一成同一形状。"""
    labels = v.get("labels") or {}
    return {"voice_id": v.get("voice_id") or "",
            "name": v.get("name") or "",
            "gender": v.get("gender") or labels.get("gender") or "",
            "age": v.get("age") or labels.get("age") or "",
            "language": v.get("language") or labels.get("language") or "",
            "descriptive": v.get("descriptive") or labels.get("descriptive")
                or v.get("description") or "",
            "category": v.get("category") or "",
            "preview_url": v.get("preview_url") or "",
            "public_owner_id": v.get("public_owner_id") or ""}


async def api_elevenlabs_voices(body: dict):
    """列音色:scope=mine 账号内音色(可直接用于合成);scope=library 搜 Voice
    Library 公共音色(需先「加入我的音色」才能合成)。Key 用请求体的(未保存也可试)。"""
    key = str((body or {}).get("api_key") or "").strip()
    if not key:
        raise ServiceError(400, "api_key required")
    search = str((body or {}).get("search") or "").strip()
    if (body or {}).get("scope") == "library":
        q = {"page_size": "50"}
        if search:
            q["search"] = search
        url = "https://api.elevenlabs.io/v1/shared-voices?" + urllib.parse.urlencode(q)
        d = await asyncio.to_thread(_elevenlabs_json, url, key)
        return {"voices": [_elevenlabs_norm(v) for v in d.get("voices") or []]}
    d = await asyncio.to_thread(_elevenlabs_json,
                                "https://api.elevenlabs.io/v1/voices", key)
    voices = [_elevenlabs_norm(v) for v in d.get("voices") or []]
    if search:
        s = search.lower()
        voices = [v for v in voices
                  if s in (v["name"] + v["descriptive"] + v["language"]).lower()]
    return {"voices": voices}


async def api_elevenlabs_voice_add(body: dict):
    """把 Voice Library 公共音色加入账号(加入后其 voice_id 才能用于 TTS 合成)。"""
    key = str((body or {}).get("api_key") or "").strip()
    owner = str((body or {}).get("public_owner_id") or "").strip()
    vid = str((body or {}).get("voice_id") or "").strip()
    if not (key and owner and vid):
        raise ServiceError(400, "api_key / public_owner_id / voice_id required")
    name = str((body or {}).get("name") or "").strip() or vid
    d = await asyncio.to_thread(
        _elevenlabs_json,
        f"https://api.elevenlabs.io/v1/voices/add/{urllib.parse.quote(owner)}"
        f"/{urllib.parse.quote(vid)}", key, {"new_name": name})
    return {"ok": True, "voice_id": d.get("voice_id") or vid, "name": name}


_VOLC_SPEAKERS_CACHE: dict = {}       # resource_id -> (ts, speakers)
_VOLC_SPEAKERS_TTL = 600


def _volc_list_speakers(ak: str, sk: str, resource_id: str) -> list[dict]:
    """分页拉全新版豆包语音控制台「音色库」音色(ListSpeakers,Service=speech_saas_prod)。"""
    out, page = [], 1
    while True:
        r = _volc_signed_call(ak, sk, "ListSpeakers", "2025-05-20",
                              {"ResourceIDs": [resource_id], "Page": page, "Limit": 100},
                              service="speech_saas_prod", region="cn-beijing")
        err = (r.get("ResponseMetadata") or {}).get("Error") or {}
        if err:
            raise RuntimeError(f"{err.get('Code')}: {err.get('Message')}")
        res = r.get("Result") or {}
        batch = res.get("Speakers") or []
        for v in batch:
            out.append({
                "voice_type": v.get("VoiceType") or "",
                "name": v.get("Name") or "",
                "gender": v.get("Gender") or "",
                "age": v.get("Age") or "",
                "labels": (v.get("NormalLabels") or []) + (v.get("SpecialLabels") or []),
                "languages": [x.get("Language") for x in (v.get("Languages") or [])
                              if x.get("Language")],
                "emotions": [e.get("Value") for e in (v.get("Emotions") or [])
                             if e.get("Value")],
                "description": v.get("Description") or "",
                "trial_url": v.get("TrialURL") or v.get("ShortTrialURL") or "",
                "resource_id": v.get("ResourceID") or resource_id})
        total = int(res.get("Total") or 0)
        if not batch or len(out) >= total or page >= 20:
            return out
        page += 1


async def api_volc_speakers(body: dict):
    """新版豆包语音「音色库」列表(ListSpeakers,10 分钟缓存)。走火山 OpenAPI
    AK/SK 签名(凭证绑定 ⚙️ 设置 → 文件托管 → 存储渠道「火山引擎 TOS」的
    AccessKey/SecretKey,空时回退环境变量 TOS_ACCESS_KEY/TOS_SECRET_KEY),
    非语音 API Key。"""
    resource_id = str((body or {}).get("resource_id") or "seed-tts-2.0").strip()
    if resource_id not in ("seed-tts-2.0", "seed-tts-1.0"):
        resource_id = "seed-tts-2.0"   # 克隆/自定义档没有公共音色库,回落 2.0
    tos = (load_genconfig().get("storage") or {}).get("tos") or {}
    ak = (tos.get("access_key") or os.environ.get("TOS_ACCESS_KEY", "")).strip()
    sk = (tos.get("secret_key") or os.environ.get("TOS_SECRET_KEY", "")).strip()
    if not (ak and sk):
        raise ServiceError(400, "需先在 ⚙️ 设置 → 文件托管 → 存储渠道「火山引擎 TOS」"
                                "配置 AccessKey/SecretKey")
    ts, cached = _VOLC_SPEAKERS_CACHE.get(resource_id, (0, None))
    if cached is not None and time.time() - ts < _VOLC_SPEAKERS_TTL:
        return {"speakers": cached}
    try:
        speakers = await asyncio.to_thread(_volc_list_speakers, ak, sk, resource_id)
    except urllib.error.HTTPError as e:
        raise ServiceError(502, f"ListSpeakers HTTP {e.code}:"
                                 f"{e.read().decode('utf-8', 'replace')[:300]}")
    except Exception as e:  # noqa: BLE001
        raise ServiceError(502, f"ListSpeakers 调用失败:{e}")
    _VOLC_SPEAKERS_CACHE[resource_id] = (time.time(), speakers)
    return {"speakers": speakers}


# ---------------- 火山方舟 私域虚拟人像素材库(设置 → 高级 → 虚拟人像资产库) ----------------
# 台账 avatar_assets.json:{"assets": {<文件sha256>: {asset_id,status,group_id,...}},
# "files": {"<绝对路径>|<mtime_ns>|<size>": <sha256>}}(files 为免重复哈希的缓存)。
# genmedia 生成视频时同读该台账:已入库(Active)的参考图改用 asset://<id> 提交,
# 规避 Seedance 对含人脸参考图的审核拦截。
AVATAR_LEDGER_PATH = RUNTIME_DIR / "avatar_assets.json"
_AVATAR_ARK_HOST = "ark.cn-beijing.volcengineapi.com"
_AVATAR_API_VERSION = "2024-01-01"


def _avatar_cfg() -> dict:
    return load_genconfig().get("avatar_assets") or {}


def _avatar_keys(cfg: dict) -> tuple[str, str]:
    """资产库 AK/SK:本页配置优先,留空回退文件托管 TOS 的 AK/SK 或环境变量。"""
    ak = str(cfg.get("access_key") or "").strip()
    sk = str(cfg.get("secret_key") or "").strip()
    if not (ak and sk):
        tos = (load_genconfig().get("storage") or {}).get("tos") or {}
        ak = ak or (tos.get("access_key") or os.environ.get("TOS_ACCESS_KEY", "")).strip()
        sk = sk or (tos.get("secret_key") or os.environ.get("TOS_SECRET_KEY", "")).strip()
    if not (ak and sk):
        raise ServiceError(400, "需先配置火山引擎 Access Key/Secret Key"
                                "(⚙️ 设置 → 高级 → 虚拟人像资产库)")
    return ak, sk


_AVATAR_THROTTLE_RE = re.compile(
    r"Quota\w*Exceeded|QPM|QPS|Throttl|RateLimit|TooManyRequests|HTTP 429", re.I)
_AVATAR_CALL_RETRIES = 6            # 限流退避:2s→4s→…≤30s(+抖动),共重试 6 次


def _avatar_call(action: str, body: dict) -> dict:
    """方舟素材资产(Assets)OpenAPI 调用(AK/SK V4 签名,Service=ark);
    返回 Result,业务/HTTP 错误统一抛 ServiceError。方舟写接口有 QPM 限额
    (实测 CreateAsset 连发触发 QuotaWriteQPMExceeded),命中限流类错误按指数
    退避重试;其他错误直接抛。"""
    for attempt in range(_AVATAR_CALL_RETRIES + 1):
        try:
            return _avatar_call_once(action, body)
        except ServiceError as e:
            if attempt >= _AVATAR_CALL_RETRIES or not _AVATAR_THROTTLE_RE.search(e.detail):
                raise
            time.sleep(min(30.0, 2.0 ** (attempt + 1)) + random.random())
    raise AssertionError("unreachable")


def _avatar_call_once(action: str, body: dict) -> dict:
    ak, sk = _avatar_keys(_avatar_cfg())
    try:
        r = _volc_signed_call(ak, sk, action, _AVATAR_API_VERSION, body,
                              service="ark", region="cn-beijing",
                              host=_AVATAR_ARK_HOST)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        try:
            err = (json.loads(detail).get("ResponseMetadata") or {}).get("Error") or {}
            if err:
                detail = f"{err.get('Code')}: {err.get('Message')}"
        except Exception:
            pass
        raise ServiceError(502, f"{action} HTTP {e.code}:{detail}") from None
    except ServiceError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ServiceError(502, f"{action} 调用失败:{e}") from None
    err = (r.get("ResponseMetadata") or {}).get("Error") or {}
    if err:
        raise ServiceError(502, f"{action} 失败:{err.get('Code')}: {err.get('Message')}")
    return r.get("Result") or {}


def _avatar_ledger() -> dict:
    try:
        d = json.loads(AVATAR_LEDGER_PATH.read_text())
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    d.setdefault("assets", {})
    d.setdefault("files", {})
    return d


def _avatar_ledger_save(d: dict):
    files = d.get("files") or {}
    if len(files) > 5000:   # 哈希缓存只增不减,超限丢最旧的一半
        d["files"] = dict(list(files.items())[-2500:])
    atomic_write_json(AVATAR_LEDGER_PATH, d)


def _avatar_digest(p: Path, led: dict) -> str:
    """文件内容 sha256(带 mtime/size 缓存,避免预览页反复全量读图)。"""
    st = p.stat()
    fkey = f"{p}|{st.st_mtime_ns}|{st.st_size}"
    dig = led["files"].get(fkey)
    if not dig:
        dig = hashlib.sha256(p.read_bytes()).hexdigest()
        led["files"][fkey] = dig
    return dig


def _avatar_ref_path(project: str, ref: str) -> Path:
    """清洗并定位项目内图片(仅允许项目目录内的图片文件)。"""
    base = _proj_base(safe_slug(project))
    ref = (ref or "").strip().lstrip("/")
    if not ref or ".." in ref.split("/"):
        raise ServiceError(400, "invalid ref path")
    p = (base / ref).resolve()
    try:
        p.relative_to(base.resolve())
    except ValueError:
        raise ServiceError(400, "invalid ref path") from None
    if not p.is_file() or p.suffix.lower() not in IMG_EXTS:
        raise ServiceError(404, f"Image not found: {ref}")
    return p


async def _avatar_group_id(cfg: dict) -> str:
    """素材组 Id:已记录的直接用,否则 CreateAssetGroup 自动创建并写回 genconfig。"""
    gid = str(cfg.get("group_id") or "").strip()
    if gid:
        return gid
    name = str(cfg.get("group_name") or "").strip() or "VideoAgents"
    res = await asyncio.to_thread(
        _avatar_call, "CreateAssetGroup",
        {"Name": name, "Description": "VideoAgents character images (auto-created)",
         "ProjectName": cfg.get("project_name") or "default"})
    gid = str(res.get("Id") or "").strip()
    if not gid:
        raise ServiceError(502, f"CreateAssetGroup 未返回素材组 Id:{res}")
    full = load_genconfig()
    full.setdefault("avatar_assets", {})["group_id"] = gid
    save_genconfig(full)
    return gid


# 「文件托管」各渠道对应的 SDK 模块与 AK/SK 环境变量(与 genmedia.STORAGE_ENV 同口径)
_STORAGE_SDK = {"tos": ("tos", "tos"), "oss": ("oss2", "oss2"),
                "cos": ("qcloud_cos", "cos-python-sdk-v5"), "s3": ("boto3", "boto3")}
_STORAGE_ENV = {"tos": ("TOS_ACCESS_KEY", "TOS_SECRET_KEY"),
                "oss": ("OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET"),
                "cos": ("COS_SECRET_ID", "COS_SECRET_KEY"),
                "s3": ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")}


def _avatar_storage_check() -> dict:
    """人像入库前置自检:方舟 CreateAsset 只收公网 URL(data: base64 实测 400
    「URL must be a valid HTTP or HTTPS URL」),本地图必须先经「文件托管」对象存储换
    预签名 URL——检查生效渠道的 bucket/AK/SK 是否配齐、当前解释器能否导入该渠道 SDK
    (桌面捆绑运行时曾缺 tos,上传子进程静默失败被误报成 URL 无效)。
    返回 {ok, provider, bucket, configured, sdk_ok, sdk, detail}。"""
    st = load_genconfig().get("storage") or {}
    provider = str(st.get("provider") or "tos")
    c = st.get(provider) or {}
    mod, pkg = _STORAGE_SDK.get(provider, (provider, provider))
    ak_env, sk_env = _STORAGE_ENV.get(provider, ("", ""))
    ak = str(c.get("access_key") or os.environ.get(ak_env, "")).strip()
    sk = str(c.get("secret_key") or os.environ.get(sk_env, "")).strip()
    bucket = str(c.get("bucket") or "").strip()
    configured = bool(ak and sk and bucket)
    sdk_ok = importlib.util.find_spec(mod) is not None
    out = {"ok": configured and sdk_ok, "provider": provider, "bucket": bucket,
           "configured": configured, "sdk_ok": sdk_ok, "sdk": pkg, "detail": ""}
    if not configured:
        out["detail"] = (f"文件托管「{provider}」未配齐 bucket/Access Key/Secret Key"
                         "(⚙️ 设置 → 文件托管);方舟入库只收公网 URL,须先配置对象存储")
    elif not sdk_ok:
        out["detail"] = (f"当前 Python 运行时缺少 {provider} 存储 SDK(模块 {mod}):"
                         f"请执行 `{sys.executable} -m pip install {pkg}` 后重试")
    else:
        out["detail"] = f"{provider} 桶 {bucket},SDK {pkg} 已就绪"
    return out


async def api_avatar_ready() -> dict:
    """/avatars 页「使用前准备」就绪自检(文件托管配置 + SDK 可导入)。"""
    return await asyncio.to_thread(_avatar_storage_check)


def _avatar_source_url(p: Path) -> str:
    """本地图片 → CreateAsset 可访问的公网 URL:经「文件托管」对象存储出预签名 URL
    (genmedia upload 子进程,与参考视频同链路)。不再回退 data: base64——方舟实测拒收,
    回退只会把真实原因(SDK 缺失/密钥错误)换成误导性的 URL 无效报错;失败直接带
    子进程 stderr 原文抛出。"""
    chk = _avatar_storage_check()
    if not chk["ok"]:
        raise ServiceError(400, chk["detail"])
    r = subprocess.run([sys.executable, str(ROOT / "modules" / "genmedia.py"),
                        "upload", "--input", str(p)],
                       capture_output=True, text=True, timeout=600, cwd=str(ROOT))
    out = (r.stdout or "").strip()
    url = out.splitlines()[-1].strip() if out else ""
    if r.returncode == 0 and url.startswith("http"):
        return url
    err = (r.stderr or "").strip().splitlines()
    raise ServiceError(502, f"参考图上传到对象存储({chk['provider']} {chk['bucket']})失败:"
                            f"{(err[-1] if err else f'退出码 {r.returncode}')[:300]}")


async def api_avatar_upload(body: dict):
    """把项目内一张人物图片上传入虚拟人像库(CreateAsset,异步审核:Processing →
    Active/Failed);按文件内容 sha256 幂等,已入库(Active/Processing)直接返回现状。"""
    cfg = _avatar_cfg()
    if not cfg.get("enabled"):
        raise ServiceError(400, "虚拟人像资产库未启用(⚙️ 设置 → 高级 → 虚拟人像资产库)")
    project = safe_slug(body.get("project") or "")
    p = _avatar_ref_path(project, body.get("ref") or "")
    if p.stat().st_size > 30 * 1024 * 1024:
        raise ServiceError(400, "图片超过 30MB(方舟单张图片素材上限)")
    led = _avatar_ledger()
    dig = _avatar_digest(p, led)
    ent = led["assets"].get(dig) or {}
    if ent.get("asset_id") and ent.get("status") in ("Active", "Processing"):
        _avatar_ledger_save(led)
        return {"asset_id": ent["asset_id"], "status": ent["status"], "existing": True}
    gid = await _avatar_group_id(cfg)
    url = await asyncio.to_thread(_avatar_source_url, p)
    name = f"{project}-{p.stem}"[:80]
    res = await asyncio.to_thread(
        _avatar_call, "CreateAsset",
        {"GroupId": gid, "URL": url, "AssetType": "Image", "Name": name,
         "ProjectName": cfg.get("project_name") or "default"})
    aid = str(res.get("Id") or "").strip()
    if not aid:
        raise ServiceError(502, f"CreateAsset 未返回素材 Id:{res}")
    led["assets"][dig] = {"asset_id": aid, "status": "Processing", "group_id": gid,
                          "name": name, "source": str(p),
                          "uploaded_at": int(time.time())}
    _avatar_ledger_save(led)
    return {"asset_id": aid, "status": "Processing"}


async def api_avatar_status(body: dict):
    """查询项目内一批图片的入库状态:{ref: {asset_id, status}}。Processing 的条目
    顺带经 GetAsset 刷新(单条目 5s 节流);未启用时只返回 enabled 标记。"""
    cfg = _avatar_cfg()
    out = {"enabled": bool(cfg.get("enabled")), "status": {}}
    if not out["enabled"]:
        return out
    project = safe_slug(body.get("project") or "")
    refs = [str(r) for r in (body.get("refs") or []) if r][:500]
    led = _avatar_ledger()
    before = json.dumps(led, sort_keys=True)
    now = time.time()
    for ref in refs:
        try:
            p = _avatar_ref_path(project, ref)
        except ServiceError:
            continue
        ent = led["assets"].get(_avatar_digest(p, led))
        if not ent or not ent.get("asset_id"):
            continue
        if ent.get("status") == "Processing" and now - (ent.get("checked_at") or 0) > 5:
            ent["checked_at"] = int(now)
            try:
                res = await asyncio.to_thread(
                    _avatar_call, "GetAsset",
                    {"Id": ent["asset_id"],
                     "ProjectName": cfg.get("project_name") or "default"})
                ent["status"] = res.get("Status") or ent["status"]
            except ServiceError:
                pass
        out["status"][ref] = {"asset_id": ent["asset_id"], "status": ent.get("status")}
    if json.dumps(led, sort_keys=True) != before:
        _avatar_ledger_save(led)
    return out


async def api_avatar_list(body: dict):
    """虚拟人像库资产列表(ListAssets):分页 + 名称模糊搜索,返回账号内全部图片
    素材与总数;顺带把结果里的状态同步回本地台账(预览页「已入库」标记保鲜)。"""
    body = body or {}
    try:
        page = max(1, int(body.get("page") or 1))
        size = min(100, max(1, int(body.get("page_size") or 50)))
    except (TypeError, ValueError):
        raise ServiceError(400, "page/page_size must be integers") from None
    filt: dict = {"GroupType": "AIGC"}
    q = str(body.get("q") or "").strip()
    if q:
        filt["Name"] = q
    res = await asyncio.to_thread(
        _avatar_call, "ListAssets",
        {"Filter": filt, "PageNumber": page, "PageSize": size})
    items = [{"id": it.get("Id") or "", "name": it.get("Name") or "",
              "asset_type": it.get("AssetType") or "",
              "status": it.get("Status") or "", "url": it.get("URL") or "",
              "group_id": it.get("GroupId") or "",
              "project_name": it.get("ProjectName") or "",
              "create_time": it.get("CreateTime") or "",
              "last_inference_time": it.get("LastInferenceTime") or ""}
             for it in (res.get("Items") or [])]
    led = _avatar_ledger()
    by_id = {it["id"]: it["status"] for it in items if it["id"]}
    changed = False
    for ent in led["assets"].values():
        st = by_id.get(ent.get("asset_id"))
        if st and st != ent.get("status"):
            ent["status"] = st
            changed = True
    if changed:
        _avatar_ledger_save(led)
    return {"items": items, "total": int(res.get("TotalCount") or 0),
            "page": page, "page_size": size}


async def api_avatar_delete(body: dict):
    """删除虚拟人像库中的一个素材(DeleteAsset,不可恢复);同步清掉本地台账里
    指向该素材的条目(人物预览的「已入库」标记随之消失)。"""
    aid = str((body or {}).get("id") or "").strip()
    if not aid:
        raise ServiceError(400, "id is required")
    cfg = _avatar_cfg()
    await asyncio.to_thread(
        _avatar_call, "DeleteAsset",
        {"Id": aid, "ProjectName": cfg.get("project_name") or "default"})
    led = _avatar_ledger()
    stale = [k for k, v in led["assets"].items() if v.get("asset_id") == aid]
    for k in stale:
        led["assets"].pop(k, None)
    if stale:
        _avatar_ledger_save(led)
    return {"deleted": aid}


async def api_avatar_clear(body: dict):
    """清空虚拟人像库全部素材(资产库页「全部删除」按钮;复用全自动管理的清库逻辑,
    逐页列出逐个删除,不可恢复),返回删除数。"""
    deleted = await _avatar_clear_all()
    return {"deleted": deleted}


# ---- 全自动管理(avatar_assets.auto_manage):video-generation 工单开跑前自动整备 ----
# 触发条件:资产库已启用 + 全自动管理开启 + 该 agent 生效视频渠道为火山引擎。
# 动作:同一 (项目, 集) 首次开跑先清空资产库(方舟素材数量有限额),再把该集组级
# prompt refs 引用的人物概念图(assets/concepts/characters/;「生物入库」开启时连同
# assets/concepts/creatures/ 生物概念图)逐张入库,等待审核
# Active(genmedia 提交时按台账 sha256 自动改 asset://<id>)。整备顺利完成后在台账
# auto_manage 记 {key: "<项目>|<集>", done: true}:后续同集重跑先查该标识,命中且台账
# 里本集人物图全部 Active 就整体跳过(不再逐张试入库/轮询);换集或换项目则重新整备。
AVATAR_AUTO_AGENT = "08-video-gen/video-generation"
_AVATAR_AUTO_LOCK = asyncio.Lock()          # 并发 video-generation 工单串行整备
_AVATAR_AUTO_WAIT_S = 600                   # 等待审核 Active 的上限(超时告警放行)
_AVATAR_AUTO_PACE_S = 0.6                   # 逐条删除/上传间隔,先不撞方舟写 QPM 限额


def _avatar_auto_enabled(agent_id: str) -> bool:
    """全自动管理是否对该 agent 生效:开关都开 + 生效视频渠道为火山引擎
    (「每 Agent 模型配置」的视频渠道覆盖优先,空则按全局,与 genmedia 同口径)。"""
    cfg = load_genconfig()
    av = cfg.get("avatar_assets") or {}
    if not (av.get("enabled") and av.get("auto_manage")):
        return False
    prov = (str(agent_model_config(agent_id).get("video_provider") or "")
            or str((cfg.get("video") or {}).get("provider") or "volcengine"))
    return prov == "volcengine"


def _avatar_episode_char_refs(project: str, eps: list[str],
                              creatures: bool = False) -> list[str]:
    """该集(们)组级 prompt refs 中引用的人物概念图(项目内相对路径,去重,仅存在的
    文件)。人物参考图口径 = assets/concepts/characters/ 下的图(引用化后组 refs 一律
    写实体图原路径;存量副本式锚点包的包内副本与原图逐字节一致,sha256 台账同样命中);
    creatures=True(设置页「生物入库」勾选)时 assets/concepts/creatures/ 生物概念图
    同样纳入。"""
    base = _proj_base(project)
    prefixes = ("assets/concepts/characters/",) + (
        ("assets/concepts/creatures/",) if creatures else ())
    out: list[str] = []
    seen: set[str] = set()
    for ep in eps:
        for f in sorted((base / "assets" / "prompts" / ep).glob("grp*.json")):
            try:
                refs = json.loads(f.read_text()).get("refs") or []
            except Exception:
                continue
            for r in refs:
                r = str(r).strip().lstrip("/")
                pfx = f"data/projects/{base.name}/"
                if r.startswith(pfx):
                    r = r[len(pfx):]
                if not r.startswith(prefixes) or r in seen:
                    continue
                seen.add(r)
                if (base / r).is_file():
                    out.append(r)
    return out


def _avatar_refs_all_active(project: str, refs: list[str], led: dict) -> bool:
    """本集人物图是否已全部入库 Active(按台账 sha256 查,digest 带 mtime 缓存不重读图)。
    任一张缺台账/非 Active(如人物图重生成过、库被手动清空)即返回 False。"""
    for ref in refs:
        try:
            p = _avatar_ref_path(project, ref)
        except ServiceError:
            return False
        ent = led["assets"].get(_avatar_digest(p, led)) or {}
        if not ent.get("asset_id") or ent.get("status") != "Active":
            return False
    return True


async def _avatar_clear_all() -> int:
    """清空虚拟人像库全部素材(逐页 ListAssets + 逐个 DeleteAsset),返回删除数;
    单条删除失败跳过,整页无一删成即停(防不可删素材导致死循环)。台账 assets 同步清空。"""
    cfg = _avatar_cfg()
    deleted = 0
    for _ in range(50):
        res = await asyncio.to_thread(
            _avatar_call, "ListAssets",
            {"Filter": {"GroupType": "AIGC"}, "PageNumber": 1, "PageSize": 100})
        items = [it for it in (res.get("Items") or []) if it.get("Id")]
        if not items:
            break
        ok = 0
        for it in items:
            try:
                await asyncio.to_thread(
                    _avatar_call, "DeleteAsset",
                    {"Id": it["Id"],
                     "ProjectName": cfg.get("project_name") or "default"})
                ok += 1
            except ServiceError:
                pass
            await asyncio.sleep(_AVATAR_AUTO_PACE_S)
        deleted += ok
        if not ok:
            break
    led = _avatar_ledger()
    if led["assets"]:
        led["assets"] = {}
        _avatar_ledger_save(led)
    return deleted


async def avatar_auto_manage_for_run(run: dict, message: str):
    """「虚拟人像资产库 → 全自动管理」钩子(execute_run 在 video-generation 工单
    开跑前调用):清空资产库 → 本集人物概念图(「生物入库」开启时含生物概念图)入库
    → 等审核 Active。任何失败只在运行进度条告警,不阻断工单(genmedia 对未入库图
    照旧走 URL/base64 提交)。"""
    if run["agent"] != AVATAR_AUTO_AGENT or not _avatar_auto_enabled(run["agent"]):
        return
    with_creatures = bool((load_genconfig().get("avatar_assets") or {})
                          .get("auto_manage_creatures"))
    kind = "人物/生物图" if with_creatures else "人物图"

    def note(txt: str):
        run["progress"] = txt[:300]
        publish_run(run)

    project = run["project"]
    eps, seen = [], set()
    for n in re.findall(r"\bep(\d{1,3})\b", message or "", re.I):
        ep = f"ep{int(n):02d}"
        if ep not in seen:
            seen.add(ep)
            eps.append(ep)
    try:
        async with _AVATAR_AUTO_LOCK:
            if not eps:
                note("⚠️ 虚拟人像库全自动管理:工单未标明集号(epNN),跳过整备")
                return
            refs = await asyncio.to_thread(_avatar_episode_char_refs, project, eps,
                                           with_creatures)
            if not refs:
                note(f"虚拟人像库全自动管理:{'/'.join(eps)} 组 prompt 未引用{kind[:-1]}概念图,跳过整备")
                return
            epkey = f"{project}|{','.join(eps)}"
            led = _avatar_ledger()
            am = led.get("auto_manage") or {}
            # 入库完成标识命中(同项目同集已整备完成)且台账里本集人物图全部 Active:
            # 整体跳过,不再逐张试入库、不轮询审核(进度行留一条提示,运行结束自动清)
            if (am.get("key") == epkey and am.get("done")
                    and _avatar_refs_all_active(project, refs, led)):
                note(f"✅ 虚拟人像库全自动管理:{'/'.join(eps)} {kind} {len(refs)} 张"
                     f"已入库(标识 {epkey}),跳过整备")
                return
            chk = await asyncio.to_thread(_avatar_storage_check)
            if not chk["ok"]:      # 托管/SDK 不就绪:整备必然全败,不清库、不上传,只告警
                note(f"⚠️ 虚拟人像库全自动管理跳过:{chk['detail']}")
                return
            if am.get("key") != epkey:
                note(f"🧹 虚拟人像库全自动管理:清空资产库(为 {'/'.join(eps)} 腾额度)…")
                n = await _avatar_clear_all()
                led = _avatar_ledger()
                led["auto_manage"] = {"key": epkey, "cleared": n,
                                      "at": int(time.time())}
                _avatar_ledger_save(led)
            failed: list[str] = []
            for i, ref in enumerate(refs):
                note(f"⬆ 虚拟人像库全自动管理:{kind}入库 {i + 1}/{len(refs)} {Path(ref).name}")
                try:
                    await api_avatar_upload({"project": project, "ref": ref})
                except ServiceError as e:
                    failed.append(f"{Path(ref).name}: {e.detail}")
                await asyncio.sleep(_AVATAR_AUTO_PACE_S)
            deadline = time.time() + _AVATAR_AUTO_WAIT_S
            pending = list(refs)
            while pending and time.time() < deadline:
                st = (await api_avatar_status({"project": project,
                                               "refs": refs})).get("status") or {}
                pending = [r for r in refs
                           if (st.get(r) or {}).get("status") == "Processing"]
                if not pending:
                    break
                note(f"⏳ 虚拟人像库全自动管理:等待审核 "
                     f"{len(refs) - len(pending)}/{len(refs)}(剩 {int(deadline - time.time())}s)…")
                await asyncio.sleep(5)
            # 记入库完成标识:无失败且全部审核完毕才算 done(下次同集直接跳过);
            # 有失败/超时则不记,下次重跑继续补(已入库的按台账幂等跳过)
            led = _avatar_ledger()
            am = led.get("auto_manage") or {}
            if am.get("key") == epkey:
                am["done"] = not failed and not pending
                am["refs"] = len(refs)
                am["done_at"] = int(time.time())
                led["auto_manage"] = am
                _avatar_ledger_save(led)
            tail = ""
            if failed:
                tail += f";{len(failed)} 张入库失败({failed[0]})"
            if pending:
                tail += f";{len(pending)} 张审核超时仍 Processing,将按原图提交"
            if tail:            # 有告警才留在进度行(挂整个运行期间提醒用户)
                note(f"⚠️ 虚拟人像库整备完成:{'/'.join(eps)} {kind} {len(refs)} 张{tail}")
            else:               # 顺利完成:清进度行,不在后续视频生成全程挂陈旧提示
                run.pop("progress", None)
                publish_run(run)
    except Exception as e:  # noqa: BLE001  失败不阻断视频生成
        note(f"⚠️ 虚拟人像库全自动管理失败(不阻断工单):{e}")


# ---------------- MiniMax 音色(设置页 TTS → MiniMax 用) ----------------

_MINIMAX_BASES = ("https://api.minimax.io", "https://api.minimaxi.com")


async def api_minimax_voices(body: dict):
    """MiniMax get_voice 音色列表(系统音色 + 账号内克隆/生成音色)。Key 用请求体的
    (未保存也可试);api_base 须与 Key 来源平台一致(海外/国内两平台不互通)。"""
    key = str((body or {}).get("api_key") or "").strip()
    if not key:
        raise ServiceError(400, "api_key required")
    base = str((body or {}).get("api_base") or _MINIMAX_BASES[0]).strip().rstrip("/")
    if base not in _MINIMAX_BASES:
        raise ServiceError(400, f"api_base must be one of {list(_MINIMAX_BASES)}")

    def call():
        req = urllib.request.Request(
            base + "/v1/get_voice",
            data=json.dumps({"voice_type": "all"}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")

    try:
        d = await asyncio.to_thread(call)
    except urllib.error.HTTPError as e:
        raise ServiceError(502, f"MiniMax get_voice HTTP {e.code}:"
                                f"{e.read().decode('utf-8', 'replace')[:300]}")
    except Exception as e:  # noqa: BLE001
        raise ServiceError(502, f"MiniMax get_voice 调用失败:{e}")
    err = d.get("base_resp") or {}
    if err.get("status_code"):
        raise ServiceError(502, f"MiniMax get_voice 失败(code={err['status_code']}):"
                                f"{err.get('status_msg') or ''}")
    voices = []
    for group, tag in (("system_voice", "系统"), ("voice_cloning", "克隆"),
                       ("voice_generation", "生成")):
        for v in d.get(group) or []:
            desc = v.get("description")
            if isinstance(desc, list):
                desc = " / ".join(str(x) for x in desc if x)
            voices.append({"voice_id": v.get("voice_id") or "",
                           "name": v.get("voice_name") or v.get("voice_id") or "",
                           "category": tag, "description": str(desc or "")})
    return {"voices": voices}


async def api_test_openrouter(body: dict):
    """验证 OpenRouter API Key(GET /api/v1/key)。"""
    connection = resolve_openrouter_connection(body.get("api_key") or "")
    key = connection["api_key"]
    if not key:
        raise ServiceError(400, "api_key must not be empty")
    try:
        data = await asyncio.to_thread(
            _http_get_json, connection["base_url"] + "/key",
            {"Authorization": f"Bearer {key}"})
        d = data.get("data") or {}
        return {"ok": True, "label": d.get("label"),
                "usage": d.get("usage"), "limit": d.get("limit")}
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"ok": False, "error": "Invalid key (401)"}
        return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


async def api_deepagents_models():
    """列出 deepagents 生效渠道端点上的可用模型(本地端点如 LM Studio,或 OpenRouter)。"""
    da = resolve_deepagents()
    base = (da.get("base_url") or "").rstrip("/")
    if not base:
        raise ServiceError(400, "deepagents base_url is not configured")
    headers = {}
    if da.get("api_key"):
        headers["Authorization"] = f"Bearer {da['api_key']}"
    try:
        data = await asyncio.to_thread(_http_get_json, base + "/models", headers, 8)
    except Exception as e:  # noqa: BLE001
        raise ServiceError(502, f"Failed to connect to {base}: {str(e)[:200]}") from e
    models = sorted(m.get("id") for m in data.get("data", []) if m.get("id"))
    return {"models": models, "base_url": base}


async def api_test_deepagents(body: dict):
    """测试 OpenAI 兼容端点连通性,返回模型列表。"""
    base = (body.get("base_url") or "").strip().rstrip("/")
    if not re.match(r"^https?://", base):
        raise ServiceError(400, "base_url must start with http(s)://")
    headers = {}
    if body.get("api_key"):
        headers["Authorization"] = f"Bearer {body['api_key']}"
    try:
        data = await asyncio.to_thread(_http_get_json, base + "/models", headers, 8)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True,
            "models": sorted(m.get("id") for m in data.get("data", []) if m.get("id"))}


COMFY_CLOUD_API = "https://cloud.comfy.org/api"


async def api_test_digitalhuman(body: dict):
    """测试数字人渠道凭证;Kling 固定北京;ComfyUI 按运行方式测本地/Comfy Cloud/RunningHub。"""
    provider = (body.get("provider") or "").strip()
    key = (body.get("api_key") or "").strip()
    if provider == "heygen":
        if not key:
            return {"ok": False, "error": "HeyGen API Key 未填写"}
        try:
            data = await asyncio.to_thread(
                _http_get_json, "https://api.heygen.com/v2/user/remaining_quota",
                {"x-api-key": key}, 12)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:240]}
        return {"ok": True, "provider": provider,
                "remaining_quota": (data.get("data") or {}).get("remaining_quota")}
    if provider == "klingai":
        if not key:
            return {"ok": False, "error": "Kling AI API Key 未填写"}
        url = ("https://api-beijing.klingai.com/v1/videos/avatar/image2video"
               "?pageNum=1&pageSize=1")
        try:
            data = await asyncio.to_thread(
                _http_get_json, url, {"Authorization": f"Bearer {key}"}, 12)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:240]}
        if data.get("code") not in (None, 0):
            return {"ok": False, "error": str(data.get("message") or data)[:240]}
        return {"ok": True, "provider": provider, "region": "中国北京"}
    if provider == "comfyui":
        mode = str(body.get("mode") or "local").strip()
        if mode in RH_BASES:
            # RunningHub 运行方式:只验 Key/余额,素材上传与工作流绑定在生成时校验
            result = await api_test_comfyui({"mode": mode, "api_key": key})
            return {**result, "provider": provider, "mode": mode}
        # 本地/Comfy Cloud:连通后检查 InfiniteTalk(MultiTalk)自定义节点是否可见
        required = ("MultiTalkModelLoader", "MultiTalkWav2VecEmbeds",
                    "WanVideoImageToVideoMultiTalk", "WanVideoSampler")
        result = await api_test_comfyui({"mode": mode, "url": body.get("url"), "api_key": key},
                                        extra_nodes=required)
        if not result.get("ok"):
            return result
        nodes = {n: bool((result.get("custom_nodes") or {}).get(n)) for n in required}
        workflow = Path(str(body.get("workflow") or "comfy/digitalhuman-infinitetalk-api.json"))
        if not workflow.is_absolute():
            workflow = ROOT / workflow
        return {**result, "provider": provider, "mode": mode, "infinitetalk_nodes": nodes,
                "infinitetalk_ready": all(nodes.values()),
                "workflow_exists": workflow.is_file()}
    raise ServiceError(400, "provider must be heygen, klingai or comfyui")


async def api_test_comfyui(body: dict, extra_nodes: tuple[str, ...] = ()):
    """测试 ComfyUI 连接(本地/Comfy Cloud/RunningHub),并检查关键自定义节点是否可见
    (extra_nodes 追加调用方关心的节点类名,结果一并放进 custom_nodes)。

    RunningHub 运行方式改测 accountStatus:验证 API Key 并回显余额与并发任务数
    (无原生 /system_stats、/object_info 可探)。"""
    mode = (body.get("mode") or "local").strip()
    if mode in RH_BASES:
        key = (body.get("api_key") or "").strip()
        if not key:
            return {"ok": False, "error": "RunningHub API Key 未填写"}
        base = RH_BASES[mode]
        try:
            resp = await asyncio.to_thread(
                _http_post_json, base + "/uc/openapi/accountStatus",
                {"apikey": key, "apiKey": key},
                {"Authorization": f"Bearer {key}"}, 15)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Connection failed: {str(e)[:200]}"}
        if resp.get("code") != 0:
            return {"ok": False,
                    "error": f"code={resp.get('code')}: {str(resp.get('msg'))[:200]}"}
        data = resp.get("data") or {}
        return {"ok": True, "runninghub": True,
                "remain_coins": data.get("remainCoins"),
                "current_tasks": data.get("currentTaskCounts")}
    cloud = mode == "cloud"
    if cloud:
        key = (body.get("api_key") or "").strip()
        if not key:
            return {"ok": False, "error": "Comfy Cloud API Key 未填写"}
        url = COMFY_CLOUD_API
        headers = {"X-API-Key": key}
    else:
        url = (body.get("url") or "").strip().rstrip("/")
        if not re.match(r"^https?://", url):
            raise ServiceError(400, "url must start with http(s)://")
        headers = None
    try:
        stats = await asyncio.to_thread(_http_get_json, url + "/system_stats", headers, 6)
    except Exception as e:  # noqa: BLE001
        if not cloud:
            return {"ok": False, "error": f"Connection failed: {str(e)[:200]}"}
        # Comfy Cloud 未必提供 /system_stats,退回 /queue 验证连通与鉴权
        try:
            await asyncio.to_thread(_http_get_json, url + "/queue", headers, 8)
            stats = {}
        except Exception as e2:  # noqa: BLE001
            return {"ok": False, "error": f"Connection failed: {str(e2)[:200]}"}
    all_info = None
    if cloud:
        # Comfy Cloud 无单节点 /object_info/<节点> 端点(404: "Use /api/object_info
        # instead"),只能全量拉取;本地仍走单节点端点省流量。全量约 9MB,明文长流
        # 易被代理掐断(IncompleteRead),请求 gzip 压到约 0.7MB
        try:
            all_info = await asyncio.to_thread(
                _http_get_json, url + "/object_info",
                {"Accept-Encoding": "gzip", **headers}, 30)
        except Exception:  # noqa: BLE001
            all_info = {}
    checkpoints = []
    try:
        info = all_info if cloud else await asyncio.to_thread(
            _http_get_json, url + "/object_info/CheckpointLoaderSimple", headers, 6)
        req = info.get("CheckpointLoaderSimple", {}).get("input", {}).get("required", {})
        ckpt = req.get("ckpt_name") or [[]]
        if isinstance(ckpt[0], list):
            checkpoints = ckpt[0]
    except Exception:  # noqa: BLE001
        pass
    custom_nodes = {}
    for node_type in ("ACEModelLoader", "ACEStepGen", "MiniMaxH3ReferenceToVideo", *extra_nodes):
        try:
            info = all_info if cloud else await asyncio.to_thread(
                _http_get_json, url + "/object_info/" + node_type, headers, 6)
            custom_nodes[node_type] = bool(info.get(node_type))
        except Exception:  # noqa: BLE001
            custom_nodes[node_type] = False
    sysinfo = (stats.get("system") or {})
    return {"ok": True,
            "version": sysinfo.get("comfyui_version") or sysinfo.get("os")
                       or ("Comfy Cloud" if cloud else "unknown"),
            "devices": [d.get("name") for d in stats.get("devices") or []],
            "checkpoints": checkpoints, "custom_nodes": custom_nodes,
            "ace_step_ready": all(custom_nodes.get(n) for n in
                                    ("ACEModelLoader", "ACEStepGen")),
            "minimax_h3_ready": custom_nodes.get("MiniMaxH3ReferenceToVideo", False)}


COMFY_WORKFLOW_KINDS = ("image", "video", "music", "tts", "digitalhuman")


async def api_comfy_workflows():
    """列出 comfy/ 目录中按文件名前缀分类的 API 工作流 JSON，
    并标注是否有同名 .md 说明文档。"""
    out = {kind: [] for kind in COMFY_WORKFLOW_KINDS}
    comfy_dir = ROOT / "comfy"
    if comfy_dir.is_dir():
        for path in sorted(comfy_dir.glob("*.json")):
            kind = path.name.split("-", 1)[0]
            if kind in out:
                out[kind].append({"path": f"comfy/{path.name}",
                                  "doc": path.with_suffix(".md").is_file()})
    return {"workflows": out}


async def api_comfy_workflow_doc(name: str):
    """返回 comfy 工作流同名 .md 说明文档的内容。name 只取文件名,不允许目录穿越。"""
    base = Path(str(name or "")).name
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.(json|md)", base):
        raise ServiceError(400, f"invalid workflow name: {name!r}")
    doc = (ROOT / "comfy" / base).with_suffix(".md")
    if not doc.is_file():
        raise ServiceError(404, f"no doc for workflow: {base}")
    return {"name": doc.name, "markdown": doc.read_text(encoding="utf-8")}


# 网页端工作流详情链接里的数字 ID(如 https://www.runninghub.cn/workflow/19041520…)
_RH_WF_ID_RE = re.compile(r"(\d{6,})")


def _rh_workflow_active_load_ids(workflow: dict, class_type: str) -> list[str]:
    """RunningHub API JSON 中真正被下游消费的加载节点 id；忽略未连接的演示孤岛。"""
    referenced = set()
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        for value in (node.get("inputs") or {}).values():
            if isinstance(value, list) and len(value) == 2 \
                    and isinstance(value[0], (str, int)) and isinstance(value[1], int):
                referenced.add(str(value[0]))
    return [str(nid) for nid, node in workflow.items() if isinstance(node, dict)
            and node.get("class_type") == class_type and str(nid) in referenced]


async def api_rh_workflow_verify(body: dict):
    """验证 RunningHub 工作流:按 ID(或工作区页面链接)经 getJsonApiFormat 拉取
    JSON,写入本地缓存(genmedia 运行时同读),并回报检测到的 {{TOKEN}} 占位符,
    供设置页「验证并添加」收藏。官方 OpenAPI 无工作区列表接口,收藏列表由此累积。"""
    mode = (body.get("mode") or "").strip()
    if mode not in RH_BASES:
        raise ServiceError(400, f"mode must be one of {sorted(RH_BASES)}")
    key = (body.get("api_key") or "").strip()
    if not key:
        return {"ok": False, "error": "RunningHub API Key 未填写"}
    raw = str(body.get("workflow") or "").strip()
    m = _RH_WF_ID_RE.search(raw)
    if not m:
        return {"ok": False, "error": "未能识别工作流 ID(纯数字,或粘贴工作流页面链接)"}
    wf_id = m.group(1)
    try:
        resp = await asyncio.to_thread(
            _http_post_json, RH_BASES[mode] + "/api/openapi/getJsonApiFormat",
            {"apiKey": key, "workflowId": wf_id},
            {"Authorization": f"Bearer {key}"}, 30)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Connection failed: {str(e)[:200]}"}
    text = (resp.get("data") or {}).get("prompt") if resp.get("code") == 0 else None
    if not text:
        return {"ok": False,
                "error": f"code={resp.get('code')}: {str(resp.get('msg'))[:200]}"}
    try:
        workflow = json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "error": "工作流 JSON 解析失败(接口返回异常内容)"}
    RH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (RH_CACHE_DIR / f"{mode}-{wf_id}.json").write_text(text, encoding="utf-8")
    tokens = sorted(set(re.findall(r"\{\{([A-Z][A-Z0-9_]*)\}\}", text)))
    h3 = any(isinstance(n, dict) and n.get("class_type") == "MiniMaxH3ReferenceToVideo"
             for n in workflow.values()) if isinstance(workflow, dict) else False
    load_images = _rh_workflow_active_load_ids(workflow, "LoadImage")
    load_audios = _rh_workflow_active_load_ids(workflow, "LoadAudio")
    return {"ok": True, "id": wf_id, "tokens": tokens,
            "node_count": len(workflow) if isinstance(workflow, dict) else 0,
            "minimax_h3": h3,
            "active_load_images": load_images, "active_load_audios": load_audios}


async def api_agents(refresh: bool = False):
    return list_agents(refresh=refresh)


# ---------------- Agent 插件管理 ----------------


async def api_plugins():
    return list_plugins(refresh=True)


async def api_plugins_toggle(body: dict):
    name = str(body.get("name") or "")
    if name not in {p["name"] for p in list_plugins(refresh=True)}:
        raise ServiceError(404, f"no such plugin: {name}")
    enabled = set(STATE.get("plugins_enabled") or [])
    if body.get("enabled"):
        enabled.add(name)
    else:
        enabled.discard(name)
    STATE["plugins_enabled"] = sorted(enabled)
    save_state(STATE)
    _expire_agent_caches()
    return {"ok": True, "plugins": list_plugins(refresh=True)}


async def api_plugins_upload(data: bytes):
    """安装插件包:请求体即 zip 原始字节(与 refs 上传同口径,免 multipart 依赖)。
    zip 根可以直接是插件内容(含 plugin.json),也可以套一层同名目录;
    解压前做路径穿越拦截,同名插件已存在则拒绝(先删除再装,避免新旧文件混杂)。"""
    if not data:
        raise ServiceError(400, "empty upload body")
    if len(data) > MAX_PLUGIN_UPLOAD:
        raise ServiceError(400, "plugin package too large (>50MB)")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        entries = [entry for entry in zf.infolist() if not entry.is_dir()]
    except Exception as e:  # noqa: BLE001
        raise ServiceError(400, f"invalid zip: {e}")
    if len(entries) > MAX_PLUGIN_FILES:
        raise ServiceError(400, f"plugin package contains too many files (>{MAX_PLUGIN_FILES})")
    if sum(entry.file_size for entry in entries) > MAX_PLUGIN_EXTRACTED:
        raise ServiceError(400, "plugin package is too large after extraction (>200MB)")
    names = [entry.filename for entry in entries]
    # 定位 plugin.json:取层级最浅的一个,其所在目录即插件根
    manifests = sorted((n for n in names if Path(n).name == PLUGIN_MANIFEST),
                       key=lambda n: n.count("/"))
    if not manifests:
        raise ServiceError(400, f"zip 内找不到 {PLUGIN_MANIFEST}")
    prefix = manifests[0][: -len(PLUGIN_MANIFEST)]          # ""(根)或 "xxx/"
    try:
        m = json.loads(zf.read(manifests[0]))
        name = str(m.get("name") or "").strip() or Path(prefix.rstrip("/")).name
    except Exception as e:  # noqa: BLE001
        raise ServiceError(400, f"{PLUGIN_MANIFEST} 解析失败:{e}")
    if not _PLUGIN_NAME_RE.fullmatch(name or ""):
        raise ServiceError(400, f"非法插件名:{name!r}")
    target = PLUGINS_DIR / name
    if name in {p["name"] for p in list_plugins(refresh=True)}:
        raise ServiceError(409, f"插件 {name} 已存在;请先删除旧版再安装")
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    staging = PLUGINS_DIR / f".{name}.{uuid.uuid4().hex}.tmp"
    staging.mkdir()
    extracted = 0
    try:
        for n in names:
            if not n.startswith(prefix):
                continue
            rel = n[len(prefix):]
            if not rel:
                continue
            dest = (staging / rel).resolve()
            try:
                dest.relative_to(staging.resolve())
            except ValueError as exc:
                raise ServiceError(400, f"zip 含路径穿越条目:{n}") from exc
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(n))
            extracted += 1
        if not extracted:
            raise ServiceError(400, "zip 内没有可解压的插件文件")
        staging.replace(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _expire_agent_caches()
    info = next((p for p in list_plugins(refresh=True) if p["name"] == name), None)
    return {"ok": True, "name": name, "files": extracted, "plugin": info}


async def api_plugins_delete(body: dict):
    name = str(body.get("name") or "")
    if not _PLUGIN_NAME_RE.fullmatch(name):
        raise ServiceError(400, f"非法插件名:{name!r}")
    info = next((p for p in list_plugins(refresh=True) if p["name"] == name), None)
    if not info:
        raise ServiceError(404, f"no such plugin: {name}")
    if info.get("builtin"):
        raise ServiceError(403, f"内置插件 {name} 不能删除，只能停用")
    target = Path(info["path"])
    try:
        target.resolve().relative_to(PLUGINS_DIR)
    except ValueError as exc:
        raise ServiceError(400, "plugin path is outside the user plugin directory") from exc
    shutil.rmtree(target)
    enabled = set(STATE.get("plugins_enabled") or [])
    enabled.discard(name)
    STATE["plugins_enabled"] = sorted(enabled)
    save_state(STATE)
    _expire_agent_caches()
    return {"ok": True, "plugins": list_plugins(refresh=True)}


async def api_agentmodels():
    """全部 Agent 的模型配置:mode=「模型策略」;defaults=策略默认;overrides=用户在 UI 保存的覆盖。"""
    mode = load_genconfig().get("agentmodel_mode") or "global"
    # 覆盖经 agent_model_config 归一(旧版独立 runninghub 渠道 ⇒ comfyui),与运行时同口径
    return {"mode": mode,
            "defaults": {a["id"]: default_agent_model(a["id"], mode)
                         for a in list_agents()},
            "overrides": {aid: agent_model_config(aid)
                          for aid, ov in load_agentmodels().items() if isinstance(ov, dict)}}


async def api_globalmodel_get():
    return {"global_model": global_model_pref()}


async def api_globalmodel_set(body: dict):
    """顶栏全局引擎/模型的服务端副本:前端每次切换顶栏选择时同步写入 STATE。
    localStorage 只有该浏览器可见,没有这份副本时服务端自发对话只能硬编码回退
    claude(global 模式下不跟随顶栏)。多浏览器场景后写者生效。"""
    eng = str(body.get("engine") or "").lower()
    if eng and eng not in ENGINES:
        raise ServiceError(400, f"engine must be one of {ENGINES}")
    model = str(body.get("model") or "").strip()
    if eng == "deepagents" and (not model or model in ("agentics", "local", "cloud", "openrouter")):
        try:
            model = resolve_deepagents()["model"]
        except ServiceError:
            model = ""
    STATE["global_model"] = {"engine": eng, "model": model}
    save_state(STATE)
    return {"ok": True, "global_model": global_model_pref()}


def ui_prefs_pref() -> dict:
    prefs = STATE.get("ui_prefs") or {}
    gm = global_model_pref()
    return {
        "engine": str(prefs.get("engine") or gm["engine"] or ""),
        "model": str(prefs.get("model") or gm["model"] or ""),
        "model_custom": str(prefs.get("model_custom") or ""),
        "project": str(prefs.get("project") or ""),
        "lang": ui_lang_code(),   # 界面语言:i18n.js 首访/换 origin 时凭此恢复
    }


async def api_uiprefs_get():
    return {"prefs": ui_prefs_pref()}


async def api_uiprefs_set(body: dict):
    """浏览器顶栏偏好。桌面端使用随机端口时 localStorage 会换 origin,
    因此项目/引擎/模型需要另存一份到用户数据目录的 STATE。"""
    eng = str(body.get("engine") or "").lower()
    if eng and eng not in ENGINES:
        raise ServiceError(400, f"engine must be one of {ENGINES}")
    project = safe_slug(body.get("project", ""))
    prefs = {
        "engine": eng,
        "model": str(body.get("model") or "").strip(),
        "model_custom": str(body.get("model_custom") or "").strip(),
        "project": project,
    }
    STATE["ui_prefs"] = prefs
    effective_model = str(body.get("effective_model") or "").strip()
    if eng == "deepagents" and (not effective_model
                                 or effective_model in ("agentics", "local", "cloud", "openrouter")):
        try:
            effective_model = resolve_deepagents()["model"]
        except ServiceError:
            effective_model = ""
    elif not effective_model:
        effective_model = str(body.get("model") or "").strip()
    STATE["global_model"] = {"engine": eng, "model": effective_model}
    save_state(STATE)
    return {"ok": True, "prefs": ui_prefs_pref(), "global_model": global_model_pref()}


async def api_agentmodels_set(body: dict):
    agent = body.get("agent") or ""
    if not agent_dir(agent):
        raise ServiceError(404, f"Unknown agent: {agent}")
    overrides = load_agentmodels()
    if body.get("reset"):                       # 删除覆盖,回到「模型策略」默认
        overrides.pop(agent, None)
    else:
        cfg = body.get("config") or {}
        c = {"engine": str(cfg.get("engine") or "").lower(),
             "model": str(cfg.get("model") or "").strip(),
             "image_provider": str(cfg.get("image_provider") or ""),
             "video_provider": str(cfg.get("video_provider") or "")}
        if c["engine"] not in AM_ENGINES:
            raise ServiceError(400, f"engine must be one of {AM_ENGINES} (empty = follow global)")
        if c["image_provider"] not in AM_IMAGE_PROVIDERS:
            raise ServiceError(400, f"image_provider must be one of {AM_IMAGE_PROVIDERS}")
        if c["video_provider"] not in AM_VIDEO_PROVIDERS:
            raise ServiceError(400, f"video_provider must be one of {AM_VIDEO_PROVIDERS}")
        if not c["engine"]:
            c["model"] = ""                     # 引擎跟随全局时模型无意义
        overrides[agent] = c
    atomic_write_json(AGENTMODELS_PATH, overrides)
    return {"ok": True, "effective": agent_model_config(agent)}


_PI_MODELS_CACHE: tuple[float, list[dict]] = (0, [])
_PI_MODELS_TTL = 30
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _parse_pi_models_table(output: str) -> list[dict]:
    """Parse the stable, whitespace-separated table emitted by pi --list-models."""
    models: list[dict] = []
    header_seen = False
    for raw in output.splitlines():
        line = _ANSI_ESCAPE_RE.sub("", raw).strip()
        if not line:
            continue
        fields = re.split(r"\s{2,}", line)
        if len(fields) >= 2 and fields[0] == "provider" and fields[1] == "model":
            header_seen = True
            continue
        if not header_seen or len(fields) < 2:
            continue
        provider, model = fields[0].strip(), fields[1].strip()
        if not provider or not model:
            continue
        full_id = f"{provider}/{model}"
        models.append({"id": full_id, "name": model, "provider": provider,
                       "model": model,
                       "context": fields[2] if len(fields) > 2 else "",
                       "max_output": fields[3] if len(fields) > 3 else "",
                       "thinking": fields[4] == "yes" if len(fields) > 4 else None,
                       "images": fields[5] == "yes" if len(fields) > 5 else None})
    return models


async def api_pi_models(refresh: bool = False):
    """列出当前 pi 登录凭证实际可用的模型，供全部语言模型选择器复用。"""
    global _PI_MODELS_CACHE
    ts, cached = _PI_MODELS_CACHE
    if ts and not refresh and time.time() - ts < _PI_MODELS_TTL:
        return {"models": cached, "cached": True}
    executable = await asyncio.to_thread(resolve_cli_executable, "pi")
    if not executable:
        raise ServiceError(503, cli_not_found_error("pi"))
    env = {**os.environ, "NO_COLOR": "1", "PI_SKIP_VERSION_CHECK": "1"}
    proc = await asyncio.create_subprocess_exec(
        executable, "--approve", "--list-models", cwd=ROOT, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ServiceError(504, "读取 pi 模型列表超时") from exc
    output = stdout.decode("utf-8", "replace")
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", "replace").strip() or output.strip()
        raise ServiceError(502, f"读取 pi 模型列表失败:{detail[:300]}")
    models = _parse_pi_models_table(output)
    plain_output = _ANSI_ESCAPE_RE.sub("", output)
    no_models = re.search(r"no models|/login|api key", plain_output, re.I)
    if not models and "provider" not in plain_output and not no_models:
        detail = stderr.decode("utf-8", "replace").strip() or output.strip()
        raise ServiceError(502, f"pi 未返回可用模型:{detail[:300]}")
    _PI_MODELS_CACHE = (time.time(), models)
    return {"models": models, "cached": False}


_OPENCODE_MODELS_CACHE: tuple[float, list[dict]] = (0, [])
_OPENCODE_MODELS_TTL = 30


async def api_opencode_models(refresh: bool = False):
    """列出当前 opencode 登录凭证实际可用的模型(`opencode models` 一行一个
    provider/model),供全部语言模型选择器复用。未登录时只有免费模型属正常。"""
    global _OPENCODE_MODELS_CACHE
    ts, cached = _OPENCODE_MODELS_CACHE
    if ts and not refresh and time.time() - ts < _OPENCODE_MODELS_TTL:
        return {"models": cached, "cached": True}
    executable = await asyncio.to_thread(resolve_cli_executable, "opencode")
    if not executable:
        raise ServiceError(503, cli_not_found_error("opencode"))
    env = opencode_env()
    argv = [executable, "models"]
    if refresh or opencode_models_stale(env):   # 显式刷新 models.dev 快照(约 10s)
        argv.append("--refresh")
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=ROOT, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=60 if "--refresh" in argv else 20)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ServiceError(504, "读取 opencode 模型列表超时") from exc
    output = stdout.decode("utf-8", "replace")
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", "replace").strip() or output.strip()
        raise ServiceError(502, f"读取 opencode 模型列表失败:{detail[:300]}")
    models = []
    for raw in output.splitlines():
        line = _ANSI_ESCAPE_RE.sub("", raw).strip()
        m = re.fullmatch(r"([\w.-]+)/(\S+)", line)
        if m:
            models.append({"id": line, "name": m.group(2),
                           "provider": m.group(1), "model": m.group(2)})
    _OPENCODE_MODELS_CACHE = (time.time(), models)
    return {"models": models, "cached": False}


_GROK_MODELS_CACHE: tuple[float, list[dict]] = (0, [])
_GROK_MODELS_TTL = 30
_GROK_MODEL_LINE_RE = re.compile(r"^\s*[*-]\s+(\S+)(\s+\(default\))?\s*$")


def _parse_grok_models(output: str) -> list[dict]:
    """`grok models` 输出(实测 1.0.5):登录信息与 Default model 行之后,
    `  * grok-4.6 (default)` / `  - grok-4.5` 一行一个模型。"""
    models = []
    for raw in output.splitlines():
        m = _GROK_MODEL_LINE_RE.match(_ANSI_ESCAPE_RE.sub("", raw))
        if m:
            models.append({"id": m.group(1), "name": m.group(1), "provider": "xai",
                           "model": m.group(1), "default": bool(m.group(2))})
    return models


async def api_grok_models(refresh: bool = False):
    """列出当前 grok 登录凭证实际可用的模型,供全部语言模型选择器复用。"""
    global _GROK_MODELS_CACHE
    ts, cached = _GROK_MODELS_CACHE
    if ts and not refresh and time.time() - ts < _GROK_MODELS_TTL:
        return {"models": cached, "cached": True}
    executable = await asyncio.to_thread(resolve_cli_executable, "grok")
    if not executable:
        raise ServiceError(503, cli_not_found_error("grok"))
    env = {**os.environ, "NO_COLOR": "1"}
    proc = await asyncio.create_subprocess_exec(
        executable, "models", cwd=ROOT, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ServiceError(504, "读取 grok 模型列表超时") from exc
    output = stdout.decode("utf-8", "replace")
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", "replace").strip() or output.strip()
        raise ServiceError(502, f"读取 grok 模型列表失败:{detail[:300]}")
    models = _parse_grok_models(output)
    if not models:
        detail = stderr.decode("utf-8", "replace").strip() or output.strip()
        raise ServiceError(502, f"grok 未返回可用模型(未登录请先执行 grok login):{detail[:300]}")
    _GROK_MODELS_CACHE = (time.time(), models)
    return {"models": models, "cached": False}


async def api_soul(agent: str):
    d = agent_dir(safe_agent(agent))
    if not d:
        raise ServiceError(404, "no such agent")
    return {"agent": agent, "soul": (d / "SOUL.md").read_text()}


async def api_enginecheck(engine: str):
    """检测执行引擎 CLI 是否已安装(顶栏切换 claude/codex/kimi/pi/opencode/grok 时前端调用)。
    deepagents 为进程内 runner,无 CLI 依赖,视为始终可用。"""
    if engine not in CLI_BINS:
        return {"engine": engine, "available": True, "bin": ""}
    path = await asyncio.to_thread(resolve_cli_executable, engine)
    return {"engine": engine, "available": bool(path),
            "bin": CLI_BINS[engine], "path": path or ""}


async def api_projects():
    return sorted([d.name for d in PROJECTS_DIR.iterdir()
                   if d.is_dir() and not d.name.startswith(".")])


async def api_projects_create(body: dict):
    """新建项目(顶栏「＋新建项目」弹窗):建目录、存小说原文与主创构想,
    并自动派总制片完成初始化(整理文本结构,完成后提醒用户放参考图)。"""
    name = (body.get("name") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9]+", name):
        raise ServiceError(400, "Project name may only contain ASCII letters and digits")
    if (PROJECTS_DIR / name).exists():
        raise ServiceError(400, f"Project already exists: {name}")
    # 向导初始设置(输出/时长/审核/片头片尾):先校验后建目录,校验失败不留下半成品项目
    # 新建项目审核默认全 0:质量评委 0(跳过不派单)+各维度力度 0(不审核);2026-09-07 用户拍板,向导不再传 review,只能在项目设置「审核设置」改;调用方显式给值仍可覆盖
    settings = body.get("settings") or {}
    base = {k: DEFAULT_GENCONFIG[k] for k in PROJECT_SETTINGS_KEYS}
    base["review"] = {"evaluation": 0, **{k: 0 for k in REVIEW_DIMENSIONS}}
    # 新建项目旁白默认关闭(2026-09-01);DEFAULT_GENCONFIG 保持 True 仅作存量项目缺键回退
    base["output"] = {**base["output"], "narration_enabled": False}
    cfg = _merge(base, {k: v for k, v in settings.items()
                        if k in PROJECT_SETTINGS_KEYS})
    _validate_duration(cfg["duration"])
    _validate_shot_group(cfg["shot_group"])
    _validate_output(cfg["output"])
    _validate_review(cfg["review"])
    _validate_packaging(cfg["packaging"])
    ensure_project(name)
    project_settings_path(name).write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2))
    novel = (body.get("novel") or "").strip()
    brief = (body.get("brief") or "").strip()
    style = (body.get("style") or "").strip()
    rhythm = narrative_rhythm.normalize(body.get("rhythm") if isinstance(body.get("rhythm"), dict) else None)
    if novel:
        nd = PROJECTS_DIR / name / "novel"
        nd.mkdir(parents=True, exist_ok=True)
        (nd / "original.txt").write_text(novel)
    brief_text = format_brief(brief, style, rhythm)
    if brief_text:
        (PROJECTS_DIR / name / "brief.md").write_text(brief_text)

    # 交给总制片完成初始化;完成后提醒用户放参考图,不自行启动后续流水线
    orch = next(iter(DISPATCHERS))
    msg = [f"【项目初始化】新项目 {name} 刚创建,请完成初始化:",
           f"1) 检查并补全项目目录结构(data/projects/{name}/,布局见 WORKFLOW.md §2)"]
    if novel:
        msg.append(
            f"2) 小说原文已导入 novel/original.txt(约 {len(novel)} 字):请优化文本结构与格式"
            "——清洗乱码与冗余空白、规范分章分段,按章节拆分为 novel/ 下的规范文件;"
            "工作量大时派单给 01-story/novel-parser 执行")
    else:
        msg.append("2) 用户暂未导入小说文本:在汇报中提醒用户把小说原文放入 novel/ 目录")
    if brief_text:
        msg.append(
            "3) 用户设计构想(主要构想 + 设计风格 + 叙事节奏)已写入 brief.md(系统会把它自动注入团队每个成员的系统提示词,"
            "是后续全部工作的最高创作前提,其中设计风格约束全部画面视觉产出、叙事节奏约束剧本分集/分场与分镜节拍):"
            "请通读,并在汇报中简要复述你的理解以便用户纠偏")
    if cfg is not None:
        aspect, aspect_name, lang = resolve_output(cfg)
        dur = cfg["duration"]
        ep_desc = ("每集时长根据剧本自动决定" if dur.get("episode_minutes") == "auto"
                   else f"每集约 {dur['episode_minutes']} 分钟")
        narr_off = ("" if cfg["output"].get("narration_enabled", True) is not False
                    else "、**旁白已关闭(用户约定全片没有任何旁白,p5-narration/p8-narrator 不派发)**")
        msg.append(
            f"另:用户已在新建向导完成项目初始设置并写入 settings.json——输出画幅 {aspect}({aspect_name})、"
            f"输出语言 {lang}、{ep_desc}{narr_off}、各维度审核力度与片头片尾开关等,"
            "后续派单自动生效,无需再向用户逐项确认。")
    msg.append(
        "完成以上工作后只做汇报,并【提醒用户】:可从控制台顶栏「预览设定产物」菜单进入【参考文件】页,"
        "按分类(视觉风格/角色/场景/道具/音频/封面/文本)上传参考图、希望使用的音频与文本资料,并可为每个文件添加注释说明用途"
        "(注释会写入 refs/NOTES.md,视觉与配乐 Agent 必读);"
        "等用户确认参考图就绪或明确表示跳过后,再启动后续流水线——现在不要派发剧情/设定类任务。")
    gm = agent_effective_model(orch)
    await api_chat({"agent": orch, "message": "\n".join(msg),
                    "project": name, "source": "user",
                    "engine": gm["engine"], "model": gm["model"]})
    return {"ok": True, "name": name}


# ---------------- 版本克隆(设置菜单 → 版本克隆页) ----------------
# 从项目 .version/repo.git 的任一历史提交(全量快照)克隆出全新项目:
# pygit2 导出该 commit 整树 → vc.py install 建全新版本库 → 全部文件登记 v1 基线。
# 对源项目纯只读(固定 commit),与在跑的流水线无写盘冲突。
CLONE_JOBS: dict = {}                    # 源项目名 → 最近一次克隆任务状态
CLONE_LOCK = threading.Lock()


def _version_dir(base: Path) -> Path:
    vdir = base / ".version"
    if not (vdir / "changelog.jsonl").is_file():
        raise ServiceError(404, f"Project has no version repository: {base.name}")
    return vdir


async def api_versions_log(project: str = "demo"):
    """changelog.jsonl → 按 task_id 连续分组的提交批次,倒序。
    仅取含 commit 字段的 register 记录(freeze_gate_record/backfill 等异构行跳过)。"""
    base = _proj_base(project)
    vdir = _version_dir(base)

    def _load():
        batches, cur = [], None
        with open(vdir / "changelog.jsonl", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if not (rec.get("commit") and rec.get("artifact")):
                    continue                      # 非 register 记录
                tid = rec.get("task_id") or "?"
                if cur is None or cur["task_id"] != tid:
                    cur = {"task_id": tid, "reason": rec.get("reason") or "",
                           "timestamp": rec.get("timestamp") or "",
                           "files": [], "frozen_tags": [], "snapshot_commit": ""}
                    batches.append(cur)
                cur["files"].append({"artifact": rec["artifact"],
                                     "version": rec.get("version") or ""})
                cur["timestamp"] = rec.get("timestamp") or cur["timestamp"]
                cur["snapshot_commit"] = rec["commit"]   # 批内最后一条 = 快照点
                tag = rec.get("tag")
                if tag and tag not in cur["frozen_tags"]:
                    cur["frozen_tags"].append(tag)
        return batches

    batches = await asyncio.to_thread(_load)
    batches.reverse()
    return {"project": base.name, "batches": batches}


def _clone_worker(project: str, gitdir: str, commit: str, task_id: str,
                  commit_time: str, name: str):
    job = CLONE_JOBS[project]
    tmp = PROJECTS_DIR / f".clone-tmp-{name}"    # . 开头:构建期间不被 /api/projects 列出
    try:
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)

        job["step"] = "导出快照"
        repo = pygit2.Repository(gitdir)
        snapshot = repo[pygit2.Oid(hex=commit)]
        archive_path = tmp.parent / f".{name}.tar"
        try:
            with tarfile.open(archive_path, "w") as archive:
                repo.write_archive(snapshot, archive)
            with tarfile.open(archive_path, "r") as archive:
                root = tmp.resolve()
                for member in archive.getmembers():
                    target = (tmp / member.name).resolve()
                    if root != target and root not in target.parents:
                        raise RuntimeError("版本快照包含非法路径")
                archive.extractall(tmp)
        finally:
            archive_path.unlink(missing_ok=True)

        job["step"] = "初始化版本库"
        r = subprocess.run([sys.executable, str(ROOT / "modules" / "vc.py"),
                            "install", str(tmp)],
                           capture_output=True, text=True, cwd=str(ROOT))
        if r.returncode:
            raise RuntimeError(f"vc.py install 失败: {(r.stderr or r.stdout)[-500:]}")

        files = sorted(f.relative_to(tmp).as_posix() for f in tmp.rglob("*")
                       if f.is_file()
                       and not f.relative_to(tmp).as_posix().startswith(".version/"))
        total = len(files)
        job["step"] = "登记 v1 基线"
        job["progress"] = [0, total]
        reason = (f"克隆自 {project}@{commit[:12]}(任务 {task_id or '?'}, "
                  f"{commit_time}),全部产物重置为 v1 基线")
        for i in range(0, total, 25):
            chunk = files[i:i + 25]
            r = subprocess.run([sys.executable, str(tmp / ".version" / "vc.py"),
                                "register", *chunk,
                                "--task-id", "p0-clone-baseline",
                                "--attempt", "1", "--reason", reason],
                               capture_output=True, text=True, cwd=str(tmp))
            if r.returncode:
                raise RuntimeError(f"基线登记失败: {(r.stderr or r.stdout)[-500:]}")
            job["progress"] = [min(i + 25, total), total]

        # manifest project 字段用最终名(install 时目录还叫 .clone-tmp-*)
        mpath = tmp / ".version" / "manifest.json"
        m = json.loads(mpath.read_text())
        m["project"] = name
        mpath.write_text(json.dumps(m, ensure_ascii=False, indent=2))

        os.rename(tmp, PROJECTS_DIR / name)      # 原子上线
        job["step"] = "完成"
        job["state"] = "done"
    except Exception as e:
        job["state"] = "error"
        job["error"] = str(e)
        shutil.rmtree(tmp, ignore_errors=True)


async def api_versions_clone(body: dict):
    project = safe_slug(body.get("project"))
    base = _proj_base(project)
    vdir = _version_dir(base)
    commit = (body.get("snapshot_commit") or "").strip().lower()
    task_id = (body.get("task_id") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ServiceError(400, "snapshot_commit must be a 40-char hex git commit")
    gitdir = str(vdir / "repo.git")
    try:
        repo = pygit2.Repository(gitdir)
        commit_obj = repo[pygit2.Oid(hex=commit)]
        if not isinstance(commit_obj, pygit2.Commit):
            raise KeyError(commit)
    except (KeyError, ValueError, pygit2.GitError):
        raise ServiceError(400, f"Commit not found in the repository of {project}: {commit[:12]}")

    with CLONE_LOCK:
        job = CLONE_JOBS.get(project)
        if job and job.get("state") == "running":
            raise ServiceError(409, f"A clone job is already in progress: {job.get('new_name')}")
        # 新名 = 源名-提交时间(如 sample-20260712-215704);重名追加 -2/-3
        ct = datetime.fromtimestamp(commit_obj.commit_time).astimezone().isoformat(timespec="seconds")
        stamp = ct[:19].replace("-", "").replace(":", "").replace("T", "-")
        name = f"{project}-{stamp}"
        n = 2
        while (PROJECTS_DIR / name).exists():
            name = f"{project}-{stamp}-{n}"
            n += 1
        CLONE_JOBS[project] = {"state": "running", "step": "准备",
                               "progress": [0, 0], "new_name": name,
                               "source": project, "commit": commit,
                               "task_id": task_id, "error": None}
        threading.Thread(target=_clone_worker,
                         args=(project, gitdir, commit, task_id, ct, name),
                         daemon=True).start()
    return {"ok": True, "name": name}


async def api_versions_clone_status(project: str = "demo"):
    return CLONE_JOBS.get(safe_slug(project)) or {"state": "idle"}


# ---------------- 完整复制项目(版本管理页顶部「复制项目」板块) ----------------
# 与「版本克隆」不同:不走版本库快照,而是把项目目录原样整份复制
# (含 runs/、.version/ 嵌入式版本库、未登记文件),版本历史随目录一并带走。
COPY_JOBS: dict = {}                     # 源项目名 → 最近一次复制任务状态
COPY_LOCK = threading.Lock()


def _copy_worker(project: str, src: Path, name: str):
    job = COPY_JOBS[project]
    tmp = PROJECTS_DIR / f".copy-tmp-{name}"     # . 开头:复制期间不被 /api/projects 列出
    try:
        if tmp.exists():
            shutil.rmtree(tmp)
        job["step"] = "统计文件"
        total = sum(1 for f in src.rglob("*") if f.is_file())
        job["step"] = "复制文件"
        job["progress"] = [0, total]
        done = 0

        def _cp(s, d, *, follow_symlinks=True):
            nonlocal done
            shutil.copy2(s, d, follow_symlinks=follow_symlinks)
            done += 1
            job["progress"] = [min(done, total), total]

        shutil.copytree(src, tmp, symlinks=True, copy_function=_cp)
        # 嵌入式版本库随目录复制,manifest project 字段改为新名
        mpath = tmp / ".version" / "manifest.json"
        if mpath.is_file():
            try:
                m = json.loads(mpath.read_text())
                m["project"] = name
                mpath.write_text(json.dumps(m, ensure_ascii=False, indent=2))
            except Exception:
                pass                             # manifest 损坏不阻断复制
        os.rename(tmp, PROJECTS_DIR / name)      # 原子上线
        job["step"] = "完成"
        job["state"] = "done"
    except Exception as e:
        job["state"] = "error"
        job["error"] = str(e)
        shutil.rmtree(tmp, ignore_errors=True)


async def api_projects_copy(body: dict):
    project = safe_slug(body.get("project"))
    base = _proj_base(project)
    name = (body.get("name") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name) or len(name) > 80:
        raise ServiceError(
            400, "New project name may only contain ASCII letters, digits, '-' and '_', "
                 "start with a letter or digit, and be at most 80 chars")
    if (PROJECTS_DIR / name).exists():
        raise ServiceError(400, f"Project already exists: {name}")
    with COPY_LOCK:
        job = COPY_JOBS.get(project)
        if job and job.get("state") == "running":
            raise ServiceError(409, f"A copy job is already in progress: {job.get('new_name')}")
        COPY_JOBS[project] = {"state": "running", "step": "准备",
                              "progress": [0, 0], "new_name": name,
                              "source": project, "error": None}
        threading.Thread(target=_copy_worker, args=(project, base, name),
                         daemon=True).start()
    return {"ok": True, "name": name}


async def api_projects_copy_status(project: str = "demo"):
    return COPY_JOBS.get(safe_slug(project)) or {"state": "idle"}


async def api_projects_delete(body: dict):
    """删除整个项目目录(版本管理页「危险操作」):前端已两重确认,
    后端再校验一次 confirm 必须与项目名完全一致,防误调。"""
    project = safe_slug(body.get("project"))
    base = _proj_base(project)
    confirm = (body.get("confirm") or "").strip()
    if confirm != base.name:
        raise ServiceError(400, "Entered project name does not match the project to delete")
    job = CLONE_JOBS.get(project)
    if job and job.get("state") == "running":
        raise ServiceError(409, "A clone job is in progress for this project; delete it after the job finishes")
    job = COPY_JOBS.get(project)
    if job and job.get("state") == "running":
        raise ServiceError(409, "A copy job is in progress for this project; delete it after the job finishes")
    live = [r for r in RUNS.values()
            if r.get("project") == project
            and r.get("status") in ("queued", "running")]
    if live:
        raise ServiceError(409, f"Project has {len(live)} tasks queued or running; stop them before deleting")
    await asyncio.to_thread(shutil.rmtree, base)
    remain = sorted([d.name for d in PROJECTS_DIR.iterdir()
                     if d.is_dir() and not d.name.startswith(".")])
    return {"ok": True, "deleted": project, "next": remain[0] if remain else ""}


async def api_history(agent: str, project: str = "demo", limit: int = 200):
    p = chat_path(agent, project)
    if not p.is_file():
        return []

    def _tail():
        with p.open() as f:
            return list(deque(f, maxlen=limit))
    lines = await asyncio.to_thread(_tail)
    return [json.loads(x) for x in lines if x.strip()]


async def api_runs():
    return [run_public(r) for r in list(RUNS.values())[-100:]]


async def api_run(run_id: str):
    r = RUNS.get(run_id)
    if not r:
        raise ServiceError(404, "no such run")
    return run_public(r) | {"result": r.get("result"), "text": r.get("text")}


async def api_run_progress(run_id: str, body: dict):
    """父运行(如总制片)等待子任务期间上报进度,UI 实时显示。"""
    r = RUNS.get(run_id)
    if not r:
        raise ServiceError(404, "no such run")
    r["progress"] = str(body.get("note") or "")[:300]
    publish_run(r)
    return {"ok": True}


# ---------------- 用户确认(重跑/跳过等) ----------------


def _notify_user_macos(text: str):
    """macOS 本机通知(失败静默):人工确认/签字等待是全天最大空转来源,主动喊人。"""
    try:
        subprocess.Popen(
            ["osascript", "-e",
             f'display notification {json.dumps(text[:120], ensure_ascii=False)} '
             f'with title "VideoAgents" sound name "Glass"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def notify_user(text: str):
    """Send a native macOS/Windows notification; fail silently."""
    message = text[:120]
    try:
        if os.name == "nt":
            payload = base64.b64encode(message.encode("utf-8")).decode("ascii")
            script = f"""
$message = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}'))
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$null = [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime]
$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template)
$nodes = $xml.GetElementsByTagName('text')
$null = $nodes.Item(0).AppendChild($xml.CreateTextNode('VideoAgents'))
$null = $nodes.Item(1).AppendChild($xml.CreateTextNode($message))
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('VideoAgents').Show($toast)
""".strip()
            encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                 "-EncodedCommand", encoded],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        elif sys.platform == "darwin":
            _notify_user_macos(message)
    except Exception:
        pass


def confirm_public(c: dict) -> dict:
    remaining = (None if c["timeout"] is None else
                 max(0, int(c["created"] + c["timeout"] - time.time())))
    public = {k: c[k] for k in ("id", "question", "options", "default",
                                "timeout", "parent", "answer", "kind")}
    for key in ("project", "gate_id", "checkpoint"):
        if c.get(key):
            public[key] = c[key]
    return public | {"remaining": remaining}


async def api_confirm_create(body: dict):
    """运行中的 Agent(经 dispatch.py --confirm)向用户发起确认。
    kind=confirm(默认,重跑类):至多「重跑等待确认」设定时长(缺省 60s)后自动落默认答案;
    kind=sign(签字类,H 门人工签字点):永不超时、永不自动确认,弹窗保留到用户操作。
    签字类同题去重:等待方(dispatch.py)超时退出后重发同一签字时,复用原 confirm_id
    接回原弹窗,避免重复弹窗、且用户点旧弹窗即刻生效;若同题刚被答复(竞态窗口内
    用户已点过),直接返回原 id 让新等待方首轮轮询即取到答案,免用户白点。"""
    q = (body.get("question") or "").strip()
    if not q:
        raise ServiceError(400, "question must not be empty")
    kind = "sign" if body.get("kind") == "sign" else "confirm"
    parent = body.get("parent")
    project = body.get("project") or (RUNS.get(parent or "") or {}).get("project")
    if project:
        project = require_project_slug(project)
    gate_id = checkpoint = None
    if kind == "sign" and project:
        binding = _sign_gate_binding(project, q, body.get("gate_id"))
        if binding:
            gate_id, checkpoint = binding
    if kind == "sign":
        dups = [o for o in CONFIRMS.values()
                if o["kind"] == "sign" and o["question"] == q[:500]]
        pending = next((o for o in dups if o["answer"] is None), None)
        if pending:
            return {"confirm_id": pending["id"]}
        fresh = max((o for o in dups if o.get("answered")),
                    key=lambda o: o["answered"], default=None)
        if fresh and time.time() - fresh["answered"] < 600:
            return {"confirm_id": fresh["id"]}
    fallback = ["签字", "暂缓"] if kind == "sign" else ["重跑", "跳过"]
    options = [str(o)[:40] for o in (body.get("options") or fallback)][:4]
    c = {"id": uuid.uuid4().hex[:8], "question": q[:500], "options": options,
         "default": str(body.get("default") or options[0])[:40],
         "timeout": None if kind == "sign" else
         min(confirm_timeout_setting(),
             max(CONFIRM_TIMEOUT_MIN, int(body.get("timeout")
                                          or confirm_timeout_setting()))),
         "kind": kind, "parent": parent,
         "created": time.time(), "answer": None}
    if project:
        c["project"] = project
    if gate_id:
        c.update({"gate_id": gate_id, "checkpoint": checkpoint,
                  "origin": body.get("origin") or "dispatch"})
    CONFIRMS[c["id"]] = c
    HUB.publish({"type": "confirm", **confirm_public(c)})
    notify_user(("需要你签字:" if kind == "sign" else "需要你确认:") + q)
    # timeout 返回服务端实际生效值(经用户设置钳制),等待方(dispatch.py)据此对齐本地截止时间
    return {"confirm_id": c["id"], "timeout": c["timeout"]}


async def api_confirms():
    """未答复且未超时的确认项(前端刷新页面后恢复弹窗用);签字类不超时。"""
    now = time.time()
    return [confirm_public(c) for c in CONFIRMS.values()
            if c["answer"] is None and
            (c["timeout"] is None or now - c["created"] < c["timeout"])]


async def api_confirm_get(cid: str):
    c = CONFIRMS.get(cid)
    if not c:
        raise ServiceError(404, "no such confirm")
    return confirm_public(c)


async def api_confirm_answer(cid: str, body: dict):
    c = CONFIRMS.get(cid)
    if not c:
        raise ServiceError(404, "no such confirm")
    if c["answer"] is None:
        c["answer"] = str(body.get("answer") or "")[:40] or c["default"]
        c["answered"] = time.time()
        if (c.get("kind") == "sign" and c.get("gate_id")
                and c["answer"] == "签字"):
            if str(c.get("checkpoint") or "").upper().startswith("H3A"):
                # 分镜签字即冻结本集的提示词技能快照(用户在弹窗里可能刚改过)
                try:
                    proj = _approval_project(c)
                    if proj:
                        sync_prompt_skill_effective(proj)
                except Exception as e:  # noqa: BLE001
                    print(f"[prompt_skill] H3A 签字快照失败:{e}", flush=True)
            c["continuation_attempted"] = time.time()
            try:
                continuation = await _continue_signed_gate(c)
                if continuation:
                    c["continuation_run_id"] = continuation
            except Exception as e:  # noqa: BLE001
                c["continuation_error"] = str(e)[:300]
                print(f"[approval] 签字后唤醒总制片失败(审批已保留):{e}", flush=True)
        HUB.publish({"type": "confirm_done", "id": cid, "answer": c["answer"]})
    return {"ok": True, "answer": c["answer"]}


_AUTO_USAGE = ("用法:/auto — 查看当前项目自动运行状态;"
               "/auto on|off — 开/关当前项目自动运行;/auto off all — 关闭全部项目")


async def _auto_command(message: str, project: str) -> str:
    """/auto 聊天命令(api_chat 已确保首词为 /auto):返回要回给用户的文本。"""
    parts = message.split()
    sub = parts[1].lower() if len(parts) > 1 else ""
    target = parts[2].lower() if len(parts) > 2 else ""
    if not sub:
        on = (await api_watchdog_get(project))["enabled"]
        th = await api_watchdog_threshold_get()
        return (f"🤖 项目 {project} 自动运行:{'🟢 开启' if on else '⚪ 关闭'}"
                f"(闲置 {th['idle_minutes']} 分钟 · 用量阈值 {th['threshold']}%)。\n{_AUTO_USAGE}")
    if sub not in ("on", "off") or len(parts) > 3 or (target and not (sub == "off" and target == "all")):
        return f"无法识别的命令「{message}」。{_AUTO_USAGE}"
    if target == "all":
        projects = await api_projects()
        for p in projects:
            await api_watchdog_set({"project": p, "enabled": False})
        return f"🤖 已关闭全部 {len(projects)} 个项目的自动运行。"
    await api_watchdog_set({"project": project, "enabled": sub == "on"})
    return f"🤖 已{'开启' if sub == 'on' else '关闭'}项目 {project} 的自动运行。"


async def api_chat(body: dict):
    agent = safe_agent(body.get("agent", ""))
    message = (body.get("message") or "").strip()
    project = require_project_slug(body.get("project"))
    model = body.get("model") or None
    engine = (body.get("engine") or "").lower()
    if not engine:      # 调用方未指定引擎:回退顶栏全局的服务端副本,而非硬编码 claude
        gp = global_model_pref()
        engine = gp["engine"] or "claude"
        model = model or gp["model"] or None
    source = body.get("source", "user")
    parent = body.get("parent") or None
    if not message:
        raise ServiceError(400, "message must not be empty")
    if not agent_dir(agent):
        raise ServiceError(404, f"Unknown agent: {agent}")
    if message == "/clear":
        # 会话清理命令:不派发运行,清掉该 Agent 在本项目下全部引擎的会话记录,
        # 下一条消息即开全新会话。引擎侧的历史会话文件留在原处,仅解除续接
        # (用途:会话膨胀后续接请求过大被网络掐断、或想甩掉陈旧上下文时手动重置)。
        cleared = _clear_agent_sessions(agent, project)
        if cleared:
            reply = (f"🧹 已清理会话记录({'、'.join(cleared)}),"
                     "下一条消息将开启全新会话。")
        else:
            reply = "当前没有会话记录可清理,下一条消息本就会开启全新会话。"
        if any(r.get("agent") == agent and r.get("project") == project
               and r.get("status") in ("queued", "running") for r in RUNS.values()):
            reply += ("\n⚠️ 该 Agent 尚有在跑/排队的任务,其结束时会重新记下所用会话;"
                      "如需彻底清理,请等任务结束后再发一次 /clear。")
        append_chat(agent, project, {"role": "user", "text": message, "source": source})
        append_chat(agent, project, {"role": "assistant", "text": reply, "status": "done"})
        return {"ok": True, "cleared": cleared}
    if message.split()[0].lower() == "/auto":
        # 自动运行(空转看门狗)开关命令:与 /clear 同一机制——不派发运行、零引擎配额,
        # 任何通道(网页/飞书/微信/WhatsApp)发来都在此本地处理,回复经 append_chat
        # 走 HUB 回到各端;开关落 api_watchdog_set(与运行面板 🤖 按钮同一入口),
        # 其 HUB watchdog 事件同步刷新网页按钮状态。项目=本次对话所在项目。
        reply = await _auto_command(message, project)
        append_chat(agent, project, {"role": "user", "text": message, "source": source})
        append_chat(agent, project, {"role": "assistant", "text": reply, "status": "done"})
        return {"ok": True, "watchdog": (await api_watchdog_get(project))["enabled"]}
    # 引擎解析优先级:Agent 级配置 > 父运行引擎 > 请求/全局。
    # 报错后禁止切换引擎:force/--engine 不得把成员改到另一执行引擎;仅允许同引擎内 --model。
    am = agent_model_config(agent)
    parent_engine = ""
    if parent and parent in RUNS:
        parent_engine = str(RUNS[parent].get("engine") or "").lower()
    natural_engine = (am.get("engine")
                      or parent_engine
                      or global_model_pref()["engine"]
                      or "claude")
    if natural_engine not in ENGINES:
        natural_engine = "claude"
    if body.get("force"):
        if engine != natural_engine:
            engine = natural_engine
    else:
        if am.get("engine"):
            engine = am["engine"]
            model = am.get("model") or None     # 引擎被覆盖时,模型也取该 Agent 的配置
        elif parent_engine in ENGINES:
            engine = parent_engine
        if not model:
            # dispatch 继承派单方引擎时 engine 非空,走不到上方空引擎回退;
            # 无 Agent 级覆盖且未显式指定模型的,在此跟随顶栏全局的 model
            # (仅引擎一致时借用,模型 ID 不跨引擎通用)
            gp = global_model_pref()
            if gp["model"] and engine == gp["engine"]:
                model = gp["model"]
    if engine not in ENGINES:
        raise ServiceError(400, f"engine must be one of {ENGINES}")
    ensure_project(project)

    agents = {a["id"]: a for a in list_agents()}
    # 缓存 TTL 内新建的 agent 可能不在 agents 里(存在性已由上方 is_file 校验)
    agent_name = (agents.get(agent) or {}).get("name") or agent.split("/")[-1]
    run = {
        "id": uuid.uuid4().hex[:12], "agent": agent,
        "agent_name": agent_name, "source": source, "parent": parent,
        "project": project, "status": "queued", "created": time.time(),
        "message": message, "engine": engine, "model": model or "",
    }
    RUNS[run["id"]] = run
    append_chat(agent, project, {"role": "user", "text": message,
                                 "run_id": run["id"], "source": source})
    publish_run(run)
    RUN_TASKS[run["id"]] = asyncio.create_task(
        execute_run(run, message, model))
    return {"run_id": run["id"]}


def _kill_proc_tree(proc):
    """杀整个进程组(claude/codex 及其派生的 bash/dispatch 子进程)。"""
    import signal
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill.exe", "/pid", str(proc.pid), "/t", "/f"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode == 0:
                return
        except OSError:
            pass
        try:
            proc.kill()
        except Exception:
            pass
        return

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _stop_run(run, by: str = "user") -> bool:
    """停止单个 run:running 杀进程组,queued 取消排队;返回是否执行了停止。
    by: "user"(运行面板 ⏹)或 "shutdown"(服务关闭连带停止)。"""
    msg = STOPPED_MSGS.get(by, STOPPED_BY_USER_MSG)
    if run.get("status") == "running":
        # stopped 标记随 run_public/对话记录/dispatch.py 输出一路透传:
        # 下游 agent 看到的是「用户手动停止」而不是一条来历不明的 error
        run["stopped"] = by
        run["error"] = msg
        proc = RUN_PROCS.get(run["id"])
        if proc and proc.returncode is None:
            _kill_proc_tree(proc)      # execute_run 读到 EOF 后按 error 收尾
        return True
    if run.get("status") == "queued":
        t = RUN_TASKS.pop(run["id"], None)
        if t:
            t.cancel()
        run["status"] = "error"
        run["stopped"] = by
        run["error"] = f"{msg}(排队中取消)"
        run["ended"] = time.time()
        append_chat(run["agent"], run["project"],
                    {"role": "assistant",
                     "text": f"⏹ {run['error']},非程序错误,无需追查失败原因;是否重派由用户决定",
                     "run_id": run["id"], "status": "error", "stopped": by})
        publish_run(run)
        return True
    return False


async def api_stop_all(by: str = "user"):
    """停止全部排队/运行中的任务(运行面板「⏹ 停止」按钮;服务关闭时 by="shutdown")。"""
    stopped = [run["id"] for run in list(RUNS.values()) if _stop_run(run, by)]
    return {"stopped": stopped}


async def shutdown_runtime(timeout: float = 4) -> None:
    """Boundedly stop Agent tasks/process groups and release OS resources."""
    tasks = list(RUN_TASKS.values())
    await api_stop_all(by="shutdown")

    active = [task for task in tasks if not task.done()]
    if active:
        _, pending = await asyncio.wait(active, timeout=timeout)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # Defensive fallback for a task that failed before removing its process.
    procs = list(RUN_PROCS.values())
    for proc in procs:
        if proc.returncode is None:
            _kill_proc_tree(proc)
    if procs:
        await asyncio.gather(*(proc.wait() for proc in procs), return_exceptions=True)
    RUN_PROCS.clear()
    RUN_TASKS.clear()
    _release_awake()


async def api_stop_run(run_id: str):
    """停止单个排队/运行中的任务(运行条目行内「⏹」按钮)。"""
    run = RUNS.get(run_id)
    if not run:
        raise ServiceError(404, f"run not found: {run_id}")
    return {"stopped": [run_id] if _stop_run(run) else []}


# ---------------- 空转看门狗 ----------------

_HUMAN_GATE_NOTIFIED: dict[str, float] = {}   # project -> 上次提醒时刻(防刷屏)
_DAG_RECONCILE_NOTIFIED: dict[str, float] = {}   # project -> 上次唤醒补展开 DAG 的时刻
_DAG_INVALID_NOTIFIED: dict[str, float] = {}   # project -> 上次唤醒修复 DAG 的时刻

_DONE_STATES = {"done", "passed", "passed_human_override"}


def _dag_load_nodes(dag_path) -> list[dict]:
    """读 dag.json 并归一化为 [{id, ...}] 列表。兼容三种落盘格式:
    nodes 列表 [{id,...}](demo)、nodes 字典 {task_id: {...}}(sample)、
    tasks 列表 [{task_id,...}](orchestrator 实际产出,见 tothemoon)。"""
    try:
        doc = json.loads(dag_path.read_text())
        nodes = doc.get("nodes") or doc.get("tasks") or []
    except Exception:
        return []
    if isinstance(nodes, dict):
        # 以字典键为准(depends_on 引用的锚),防内层残留 id 字段覆盖
        return [{**v, "id": k} for k, v in nodes.items()]
    return [{**n, "id": n.get("id") or n.get("task_id", "")} for n in nodes]


def _dag_runnable(proj: str) -> tuple[list[str], list[str]]:
    """读 runs/dag.json,返回 (依赖已满足的非人工待办节点, 依赖已满足的人工签字节点)。"""
    dag_path = PROJECTS_DIR / proj / "runs" / "dag.json"
    if not dag_path.is_file():
        return [], []
    nodes = _dag_load_nodes(dag_path)
    done = {n["id"] for n in nodes if n.get("state") in _DONE_STATES}
    runnable, human_waiting = [], []
    for n in nodes:
        if n.get("state") in _DONE_STATES:
            continue
        if not all(d in done for d in (n.get("depends_on") or [])):
            continue
        if n.get("human"):
            # expanded/template 只是待实例化骨架，blocked 是用户明确暂缓；只有
            # pending 人工节点能够发起新的签字。
            if n.get("state") == "pending":
                human_waiting.append(n["id"])
        else:
            runnable.append(n["id"])
    return runnable, human_waiting


def _gate_checkpoint(node: dict) -> str:
    gate = node.get("gate")
    if isinstance(gate, dict) and gate.get("checkpoint"):
        return str(gate["checkpoint"])
    return str(node.get("checkpoint") or node.get("id") or "人工闸门")


def _ready_human_gates(proj: str) -> list[dict]:
    """返回依赖均完成、状态仍为 pending 的人工闸门节点。"""
    dag_path = PROJECTS_DIR / proj / "runs" / "dag.json"
    if not dag_path.is_file():
        return []
    nodes = _dag_load_nodes(dag_path)
    done = {n["id"] for n in nodes if n.get("state") in _DONE_STATES}
    return [n for n in nodes
            if n.get("id") and n.get("human") and n.get("state") == "pending"
            and all(dep in done for dep in (n.get("depends_on") or []))]


def _sign_gate_binding(proj: str, question: str,
                       requested_gate_id: str | None = None) -> tuple[str, str] | None:
    """把 dispatch.py 的签字问题绑定到当前已解锁闸门，供持久化去重。"""
    ready = _ready_human_gates(proj)
    if requested_gate_id:
        for node in ready:
            if node["id"] == requested_gate_id:
                return node["id"], _gate_checkpoint(node)
        return None
    normalized = re.sub(r"[\s_｜|]+", "", question).lower()
    # 优先匹配完整 checkpoint/id，避免 H3 误命中 H3A/H3B。
    matches = [node for node in ready
               if re.sub(r"[\s_｜|]+", "", _gate_checkpoint(node)).lower()
               in normalized]
    if len(matches) == 1:
        node = matches[0]
        return node["id"], _gate_checkpoint(node)
    matches = []
    for node in ready:
        checkpoint = _gate_checkpoint(node)
        tokens = (node["id"], checkpoint.split("-", 1)[0])
        if any(re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])",
                         question, re.I) for token in tokens if token):
            matches.append(node)
    if len(matches) == 1:
        node = matches[0]
        return node["id"], _gate_checkpoint(node)
    if len(ready) == 1:
        node = ready[0]
        return node["id"], _gate_checkpoint(node)
    return None


def _approval_project(c: dict) -> str | None:
    project = c.get("project")
    if project and re.fullmatch(r"[\w-]{1,80}", str(project)):
        return str(project)
    return (RUNS.get(c.get("parent") or "") or {}).get("project")


async def ensure_human_gate_approvals(proj: str, parent: str | None = None) -> list[str]:
    """为已解锁人工闸门补建持久签字单；只建单，不放行 DAG。"""
    created = []
    for node in _ready_human_gates(proj):
        gate_id = node["id"]
        existing = [c for c in CONFIRMS.values()
                    if c.get("kind") == "sign" and _approval_project(c) == proj
                    and c.get("gate_id") == gate_id]
        if existing:
            # 服务若恰在签字落盘后、续跑派单前退出，重启后由核对钩子补唤醒。
            signed = next((c for c in reversed(existing)
                           if c.get("answer") == "签字"), None)
            if signed and time.time() - signed.get("continuation_attempted", 0) > 3600:
                continuation = RUNS.get(signed.get("continuation_run_id") or "")
                if not continuation or continuation.get("status") == "error":
                    signed["continuation_attempted"] = time.time()
                    try:
                        run_id = await _continue_signed_gate(signed)
                        if run_id:
                            signed["continuation_run_id"] = run_id
                        HUB.publish({"type": "confirm_done", "id": signed["id"],
                                     "answer": signed["answer"]})
                    except Exception as e:  # noqa: BLE001
                        signed["continuation_error"] = str(e)[:300]
            continue
        checkpoint = _gate_checkpoint(node)
        result = await api_confirm_create({
            "question": (f"【{checkpoint}｜项目 {proj}】前置任务已完成。"
                         "请审阅对应产物后签字；签字后由总制片复核闸门并冻结版本，"
                         "暂缓则保持阻塞。"),
            "kind": "sign", "options": ["签字", "暂缓"], "default": "签字",
            "project": proj, "gate_id": gate_id, "parent": parent,
            "origin": "runtime_gate_guard",
        })
        created.append(result["confirm_id"])
        print(f"[approval] 已为 {proj}:{gate_id} 创建永久签字单 {result['confirm_id']}",
              flush=True)
    return created


async def _continue_signed_gate(c: dict) -> str | None:
    """签字后恢复总制片；仍在等待 dispatch.py 的父运行会自行继续。"""
    parent = RUNS.get(c.get("parent") or "")
    if parent and parent.get("status") in ("queued", "running"):
        # 总制片仍在运行中等签字,会话正在使用不能立刻清;打标记让该运行结束时
        # 不回存会话并清掉记录(见 execute_run finally),下一轮即全新会话。
        parent["clear_sessions_on_end"] = True
        return None
    proj = str(c["project"])
    checkpoint = c.get("checkpoint") or c["gate_id"]
    # 签字过门 = 一个阶段收官:先做 /clear 同款清理再唤醒,复核以全新会话开始,
    # 防止总制片会话随项目推进无限膨胀(项目状态一律以文件为准,不依赖对话记忆)。
    cleared = _clear_agent_sessions(ORCHESTRATOR_AGENT, proj)
    if cleared:
        append_chat(ORCHESTRATOR_AGENT, proj, {
            "role": "assistant", "status": "done",
            "text": (f"🧹 签字通过,已自动清理会话记录({'、'.join(cleared)}),"
                     "闸门复核将以全新会话开始。")})
    message = (
        f"[人工签字回执] 用户已在永久签字单 {c['id']} 对项目 {proj} 的 "
        f"{checkpoint}(DAG 节点 {c['gate_id']})明确选择「签字」。"
        "这是人工签字证据，不是自动放行。请立即复核该闸门的缺陷清零、到期缺陷、"
        "QA hold 与人工检查项；满足后写入完整 gate JSON、更新 DAG 并派 version 冻结。"
        "若机检不满足则保持 HOLD 并向用户说明，禁止重复发起同一签字。"
        "注意:你的会话刚被重置,没有此前对话记忆;闸门/工单/缺陷状态一律读项目文件"
        "(runs/dag.json、gate 快照、缺陷单)重建,不要臆测。"
    )
    result = await api_chat({"agent": ORCHESTRATOR_AGENT, "message": message,
                             "project": proj, "source": "approval",
                             "parent": c.get("parent")})
    return result["run_id"]


def _linked_gate_still_open(c: dict) -> bool:
    """已答复的绑定签字单在 gate 真正关单前保留，供重启恢复和去重。"""
    proj, gate_id = _approval_project(c), c.get("gate_id")
    if not (proj and gate_id):
        return False
    dag_path = PROJECTS_DIR / proj / "runs" / "dag.json"
    for node in _dag_load_nodes(dag_path) if dag_path.is_file() else []:
        if node.get("id") == gate_id:
            return node.get("state") not in _DONE_STATES
    return False


def _dag_missing_episodes(proj: str) -> list[str]:
    """episode_plan.json 已规划、但 dag.json 没有任何对应 epNN 节点的分集。
    模板节点被首集耗尽后,后续集的工单会游离于 DAG 之外(前科:sample ep02),
    看门狗据此把「DAG 与现实脱节」本身当作唤醒事件,让总制片先补展开再派单。"""
    plan_path = PROJECTS_DIR / proj / "story" / "episode_plan.json"
    dag_path = PROJECTS_DIR / proj / "runs" / "dag.json"
    if not (plan_path.is_file() and dag_path.is_file()):
        return []
    try:
        planned = {e["ep"] for e in json.loads(plan_path.read_text())["episodes"]}
    except Exception:
        return []
    ids = [n["id"] for n in _dag_load_nodes(dag_path)]
    covered: set[str] = set()
    for nid in ids:
        covered.update(re.findall(r"ep\d+", nid))
    return sorted(planned - covered)


PRUNE_RUN_TTL = 86400      # 终态 run 保留 24h(dispatch --wait-all 上限 4h 的 6 倍裕量,
                           # 淘汰后 GET /api/runs/{id} 404 会被 dispatch 当 pending 干等)
PRUNE_RUN_MAX = 1000       # 终态 run 数量上限,超出按 ended 从旧到新删


def prune_runs_confirms():
    """内存中 RUNS/CONFIRMS 的定期淘汰;只删终态,绝不动 queued/running。"""
    now = time.time()
    dead = [rid for rid, r in RUNS.items()
            if r.get("status") in ("done", "error")
            and now - (r.get("ended") or now) > PRUNE_RUN_TTL]
    finals = sorted((rid for rid, r in RUNS.items()
                     if r.get("status") in ("done", "error")),
                    key=lambda rid: RUNS[rid].get("ended") or 0)
    over = len(finals) - PRUNE_RUN_MAX
    if over > 0:
        dead.extend(finals[:over])
    for rid in set(dead):
        RUNS.pop(rid, None)
    # 已答复的不能立删:dispatch 每 2s 轮询 /api/confirm/{cid},404 会落到默认答案;
    # 重跑类按年龄淘汰(发起方自身 deadline = created + timeout);
    # 签字类(timeout=None)未答复永不淘汰,答复后保留 10 分钟供发起方轮询读取
    gone = [cid for cid, c in CONFIRMS.items()
            if (c.get("timeout") is not None
                and now - c.get("created", now) > (c.get("timeout") or 0) + 600)
            or (c.get("timeout") is None and c.get("answered")
                and not _linked_gate_still_open(c)
                and now - c["answered"] > 600)]
    for cid in gone:
        CONFIRMS.pop(cid, None)
    if dead or gone:
        print(f"[prune] 淘汰 run {len(set(dead))} 条 / confirm {len(gone)} 条", flush=True)


# ---------------- 防休眠(自动运行开启期间保持系统唤醒;macOS/Windows) ----------------
# macOS:caffeinate -i 子进程(-w 随本进程退出自动释放,强杀也不残留);
# Windows:SetThreadExecutionState 线程级声明,进程退出由系统自动清除。
# 均只防"闲置自动睡眠":屏幕照常熄灭,不拦合盖睡眠与手动关机/重启。
_KEEPAWAKE_PROC: subprocess.Popen | None = None
_KEEPAWAKE_WIN_ON = False
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def keepawake_supported() -> bool:
    return sys.platform == "darwin" or os.name == "nt"


def keepawake_active() -> bool:
    if sys.platform == "darwin":
        return _KEEPAWAKE_PROC is not None and _KEEPAWAKE_PROC.poll() is None
    return _KEEPAWAKE_WIN_ON


def _ensure_awake():
    global _KEEPAWAKE_PROC, _KEEPAWAKE_WIN_ON
    if sys.platform == "darwin":
        if _KEEPAWAKE_PROC is None or _KEEPAWAKE_PROC.poll() is not None:
            _KEEPAWAKE_PROC = subprocess.Popen(
                ["caffeinate", "-i", "-w", str(os.getpid())],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("[keepawake] 防休眠已开启(caffeinate)", flush=True)
    elif os.name == "nt":
        if not _KEEPAWAKE_WIN_ON:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(
                _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
            _KEEPAWAKE_WIN_ON = True
            print("[keepawake] 防休眠已开启(SetThreadExecutionState)", flush=True)


def _release_awake():
    global _KEEPAWAKE_PROC, _KEEPAWAKE_WIN_ON
    if _KEEPAWAKE_PROC is not None:
        if _KEEPAWAKE_PROC.poll() is None:
            _KEEPAWAKE_PROC.terminate()
        _KEEPAWAKE_PROC = None
        print("[keepawake] 防休眠已释放", flush=True)
    if _KEEPAWAKE_WIN_ON:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        _KEEPAWAKE_WIN_ON = False
        print("[keepawake] 防休眠已释放", flush=True)


def sync_keepawake():
    """按当前状态声明/释放防休眠:任一项目开启自动运行且设置未关 → 保持唤醒。
    幂等,caffeinate 意外退出会被拉起;watchdog 每轮与相关设置保存时调用。
    Windows 的声明按线程记账,恒在事件循环线程里调用(勿丢线程池)。"""
    if not keepawake_supported():
        return
    try:
        wd = STATE.get("watchdog", {})
        on = any(wd.get(d.name) for d in PROJECTS_DIR.iterdir() if d.is_dir())
        if on and bool(STATE.get("watchdog_keepawake", True)):
            _ensure_awake()
        else:
            _release_awake()
    except Exception as e:  # noqa: BLE001
        print(f"[keepawake] 同步失败(忽略):{e}", flush=True)


async def idle_watchdog():
    """空转看门狗:实测单日曾有 ~3.5h 完全无活动(调度器停摆/等人工无人接续)。
    流水线仍有可跑任务、却无任务在跑且无待确认项时,自动唤醒总制片续派;
    只剩人工签字节点时,转为本机通知提醒用户。"""
    orch = next(iter(DISPATCHERS))
    while True:
        try:   # 闲置时间可在设置弹窗调整,每轮实时读取,改动无需重启
            idle_s = int(STATE.get("watchdog_idle_minutes", 5)) * 60
        except (TypeError, ValueError):
            idle_s = IDLE_CHECK_INTERVAL
        await asyncio.sleep(max(60, idle_s))
        try:
            prune_runs_confirms()   # 同一事件循环内同步执行,与 api_runs/遍历天然互斥
            sync_keepawake()        # 每轮校准防休眠状态(兼作 caffeinate 自愈)
            now = time.time()
            wd = STATE.get("watchdog", {})   # {project: bool};缺省关闭
            if not any(wd.get(d.name) for d in PROJECTS_DIR.iterdir() if d.is_dir()):
                continue                     # 没有项目开启看门狗,免探用量
            # 用量阈值门控:总制片生效引擎的会话用量超阈值则本轮全部不唤醒(配额是账号级)
            gm = agent_effective_model(orch)
            eng, mdl = gm["engine"], gm["model"]
            used = await asyncio.to_thread(engine_usage_percent, eng)
            th = int(STATE.get("watchdog_threshold", 80))
            if used is not None and used >= th:
                print(f"[watchdog] {eng} 会话用量 {used:.0f}% ≥ 阈值 {th}%,本轮跳过",
                      flush=True)
                continue
            # 有未答复的确认项时先不打扰。能通过父运行定位项目的只闸对应项目
            # (签字类永不超时,若当全局闸门,一个没人签的弹窗会冻结全部项目的
            # 自动运行);定位不到项目的按全局闸门处理。签字类每小时本机提醒一次
            pending = [c for c in CONFIRMS.values()
                       if c["answer"] is None and
                       (c["timeout"] is None or now - c["created"] < c["timeout"])]
            confirm_global = any(_approval_project(c) is None for c in pending)
            confirm_projs = {_approval_project(c) for c in pending} - {None}
            for c in pending:
                if c.get("kind") == "sign" \
                        and now - c.get("notified", c["created"]) > 3600:
                    c["notified"] = now
                    notify_user(f"签字待处理:{c['question'][:80]}")
            for proj_dir in sorted(PROJECTS_DIR.iterdir()):
                if not proj_dir.is_dir() or proj_dir.name.startswith("."):
                    continue
                proj = proj_dir.name
                if not wd.get(proj, False):   # 该项目看门狗默认关闭,逐项目独立开关
                    continue
                # 逐项目独立判断:该项目仍有任务在跑就跳过(不受其它项目影响)
                if any(r.get("status") in ("queued", "running")
                       and r.get("project") == proj for r in RUNS.values()):
                    continue
                if confirm_global or proj in confirm_projs:
                    continue
                # 常任指令:用户为该项目设定的长期约束(设置菜单「自动运行」编辑),
                # 随每条唤醒消息附带——总制片引擎会话被 reset 后口头指令会全部丢失,
                # 这里保证约束在每次自动唤醒时都重新进入上下文
                orders = str(STATE.get("watchdog_orders", {}).get(proj) or "").strip()
                orders_note = ("\n[常任指令]" + orders +
                               "\n(用户设定的长期约束,始终有效;与 DAG 待办冲突时以"
                               "常任指令为准,受限节点暂不派发,只推进不受限部分。)"
                               ) if orders else ""
                # DAG 缺失/解析不出任何节点时不再静默失明:唤醒总制片核对
                # (至多 1 次/小时)。格式规范与写入时自检见 WORKFLOW.md §3.2。
                dag_path = proj_dir / "runs" / "dag.json"
                if not dag_path.is_file() or not _dag_load_nodes(dag_path):
                    if now - _DAG_INVALID_NOTIFIED.get(proj, 0) > 3600:
                        _DAG_INVALID_NOTIFIED[proj] = now
                        state = "不存在" if not dag_path.is_file() else "无法解析或没有任何节点"
                        msg = (f"[自动运行·状态检查] 项目 {proj} 的 runs/dag.json {state},"
                               "看门狗无法判断待办前沿。请按 WORKFLOW.md §3.2 规范格式补齐/修复"
                               f"(自检:python3 services/runtime/dagcheck.py --project {proj} --strict),"
                               "然后继续按 DAG 推进。" + orders_note)
                        await api_chat({"agent": orch, "message": msg,
                                        "project": proj, "source": "watchdog",
                                        "engine": eng, "model": mdl})
                        print(f"[watchdog] 唤醒 {orch}:{proj} DAG {state}", flush=True)
                    continue
                runnable, human_waiting = _dag_runnable(proj)
                if runnable:
                    msg = (f"[自动运行·状态检查] 项目 {proj} 当前没有任何任务在运行,"
                           f"但 runs/dag.json 仍有依赖已满足的待办节点(如:{', '.join(runnable[:6])}"
                           f"{' 等' if len(runnable) > 6 else ''})。"
                           "请按 DAG 与派单守则继续推进;若确在等待人工或有原因暂停,简要说明后结束。"
                           + orders_note)
                    await api_chat({"agent": orch, "message": msg,
                                    "project": proj, "source": "watchdog",
                                    "engine": eng, "model": mdl})
                    print(f"[watchdog] 唤醒 {orch}:{proj} 待办 {len(runnable)} 项", flush=True)
                    continue   # 各项目独立唤醒,不再一轮只唤醒一个
                # DAG 覆盖率兜底:无可跑节点 ≠ 只等人工——若 episode_plan 里
                # 有分集在 DAG 中完全没有节点,说明 DAG 未按集展开/未同步,
                # 唤醒总制片先修 DAG(每项目至多 1 次/小时,防止修复失败刷屏)
                missing = _dag_missing_episodes(proj)
                if missing and now - _DAG_RECONCILE_NOTIFIED.get(proj, 0) > 3600:
                    _DAG_RECONCILE_NOTIFIED[proj] = now
                    msg = (f"[自动运行·状态检查] 项目 {proj} 的 runs/dag.json 已无可跑节点,"
                           f"但 story/episode_plan.json 规划的分集 {', '.join(missing[:6])}"
                           f"{' 等' if len(missing) > 6 else ''} 在 DAG 中没有任何节点,"
                           "疑似模板节点被首集耗尽、后续集未按集展开。"
                           "请按 WORKFLOW.md §3.1「DAG 按集动态展开」把缺失分集展开为 "
                           "pX-*-epNN 节点(含每集闸门),并依据 runs/ 既往工单回填 state/run_id,"
                           "完成后继续按 DAG 与派单守则推进;若该分集确已完结或另有安排,"
                           "在 DAG 中补记节点状态后简要说明。" + orders_note)
                    await api_chat({"agent": orch, "message": msg,
                                    "project": proj, "source": "watchdog",
                                    "engine": eng, "model": mdl})
                    print(f"[watchdog] 唤醒 {orch}:{proj} DAG 缺集 "
                          f"{', '.join(missing)}", flush=True)
                    continue
                if human_waiting:
                    created = await ensure_human_gate_approvals(proj)
                    answered = any(c.get("kind") == "sign"
                                   and _approval_project(c) == proj
                                   and c.get("gate_id") in human_waiting
                                   and c.get("answer") is not None
                                   for c in CONFIRMS.values())
                    if (not created and not answered
                            and now - _HUMAN_GATE_NOTIFIED.get(proj, 0) > 3600):
                        _HUMAN_GATE_NOTIFIED[proj] = now
                        notify_user(f"项目 {proj} 流水线停在人工签字点:"
                                    f"{', '.join(human_waiting[:3])},等你确认")
        except Exception as e:  # noqa: BLE001
            print(f"[watchdog] 异常(忽略):{e}\n{traceback.format_exc()}", flush=True)


async def api_watchdog_threshold_get():
    return {"threshold": int(STATE.get("watchdog_threshold", 80)),
            "idle_minutes": int(STATE.get("watchdog_idle_minutes", 5)),
            "keep_awake": bool(STATE.get("watchdog_keepawake", True)),
            "keep_awake_supported": keepawake_supported(),
            "keep_awake_active": keepawake_active()}


async def api_watchdog_threshold_set(body: dict):
    """自动运行设置(设置菜单「自动运行」):threshold=用量阈值(总制片生效
    引擎的会话用量达到该百分比时,看门狗本轮不自动唤醒);idle_minutes=闲置
    时间(巡检间隔,分钟)。两项均可选,只更新给出的项;持久化,重启后保持。"""
    if body.get("threshold") is not None:
        try:
            th = int(body.get("threshold"))
        except (TypeError, ValueError):
            raise ServiceError(400, "threshold must be an integer") from None
        if not 1 <= th <= 100:
            raise ServiceError(400, "threshold must be between 1 and 100")
        STATE["watchdog_threshold"] = th
    if body.get("idle_minutes") is not None:
        try:
            im = int(body.get("idle_minutes"))
        except (TypeError, ValueError):
            raise ServiceError(400, "idle_minutes must be an integer") from None
        if not 1 <= im <= 720:
            raise ServiceError(400, "idle_minutes must be between 1 and 720")
        STATE["watchdog_idle_minutes"] = im
    if body.get("keep_awake") is not None:
        STATE["watchdog_keepawake"] = bool(body.get("keep_awake"))
    save_state(STATE)
    sync_keepawake()    # 立即生效,不等下一轮巡检
    return await api_watchdog_threshold_get()


async def api_agent_advanced_get():
    """Agent 高级设置(设置菜单「高级→Agent 高级设置」)汇总读:并发/超时 + 对话记忆 + 重跑次数。"""
    d = await api_agent_concurrency_get()
    d.update(await api_agent_memory_get())
    d.update({"max_retries": max_retries_setting(),
              "max_retries_default": MAX_RETRIES_DEFAULT,
              "max_retries_max": MAX_RETRIES_MAX,
              "confirm_timeout": confirm_timeout_setting(),
              "confirm_timeout_default": CONFIRM_TIMEOUT_DEFAULT,
              "confirm_timeout_min": CONFIRM_TIMEOUT_MIN,
              "confirm_timeout_max": CONFIRM_TIMEOUT_MAX,
              "thinking_effort": thinking_effort_setting(),
              "thinking_effort_default": THINKING_EFFORT_DEFAULT,
              "thinking_effort_levels": list(THINKING_EFFORT_LEVELS),
              "max_turns": max_turns_setting(),
              "max_turns_default": MAX_TURNS_DEFAULT,
              "max_turns_min": MAX_TURNS_MIN,
              "max_turns_max": MAX_TURNS_MAX})
    return d


async def api_agent_advanced_set(body: dict):
    """Agent 高级设置合并提交,各字段均可选、可单独提交:
    - agent_concurrency / run_timeout / idle_timeout:同 api_agent_concurrency_set
    - agent_memory_kb:同 api_agent_memory_set(0=关闭;旧布尔字段 agent_memory 仍兼容)
    - max_retries:Agent 自动重跑/重 roll 次数上限(0..MAX_RETRIES_MAX,0=不自动重跑),
      经运行提示词注入全员,覆盖文档写死的 3 次;持久化,对后续启动的运行生效
    - confirm_timeout:重跑类确认弹窗倒计时时长(秒,CONFIRM_TIMEOUT_MIN..CONFIRM_TIMEOUT_MAX),
      到点无人答复自动落默认答案;签字类不受影响;持久化,对后续发起的确认生效
    - thinking_effort:思考深度统一设置(THINKING_EFFORT_LEVELS 之一,空串=引擎默认),
      派单时按引擎翻译成推理强度参数;持久化,对后续启动的运行生效
    - max_turns:单次运行引擎轮次上限(MAX_TURNS_MIN..MAX_TURNS_MAX;仅 claude/grok
      引擎有 --max-turns 参数,撞上限即被切断且无续跑,大批量工位需放宽);持久化,
      对后续启动的运行生效"""
    updates: dict = {}
    if body.get("max_turns") is not None:
        try:
            n = int(body.get("max_turns"))
        except (TypeError, ValueError):
            raise ServiceError(400, "max_turns must be an integer") from None
        if not MAX_TURNS_MIN <= n <= MAX_TURNS_MAX:
            raise ServiceError(400, f"max_turns must be between {MAX_TURNS_MIN} and {MAX_TURNS_MAX}")
        updates["max_turns"] = n
    if body.get("thinking_effort") is not None:
        lv = str(body.get("thinking_effort")).strip().lower()
        if lv not in THINKING_EFFORT_LEVELS:
            raise ServiceError(400, "thinking_effort must be one of: "
                               + ", ".join(x or "(default)" for x in THINKING_EFFORT_LEVELS))
        updates["thinking_effort"] = lv
    if body.get("max_retries") is not None:
        try:
            n = int(body.get("max_retries"))
        except (TypeError, ValueError):
            raise ServiceError(400, "max_retries must be an integer") from None
        if not 0 <= n <= MAX_RETRIES_MAX:
            raise ServiceError(400, f"max_retries must be between 0 and {MAX_RETRIES_MAX}")
        updates["max_retries"] = n
    if body.get("confirm_timeout") is not None:
        try:
            n = int(body.get("confirm_timeout"))
        except (TypeError, ValueError):
            raise ServiceError(400, "confirm_timeout must be an integer (seconds)") from None
        if not CONFIRM_TIMEOUT_MIN <= n <= CONFIRM_TIMEOUT_MAX:
            raise ServiceError(400, f"confirm_timeout must be between {CONFIRM_TIMEOUT_MIN} and {CONFIRM_TIMEOUT_MAX} seconds")
        updates["confirm_timeout"] = n
    if body.get("agent_memory_kb") is not None:
        try:
            mk = int(body.get("agent_memory_kb"))
        except (TypeError, ValueError):
            raise ServiceError(400, "agent_memory_kb must be an integer") from None
        if not 0 <= mk <= AGENT_MEMORY_KB_MAX:
            raise ServiceError(400, f"agent_memory_kb must be between 0 and {AGENT_MEMORY_KB_MAX}")
        updates["agent_memory_kb"] = mk
    elif body.get("agent_memory") is not None:   # 旧布尔开关兼容
        updates["agent_memory_kb"] = AGENT_MEMORY_KB_DEFAULT if body["agent_memory"] else 0
    conc = {k: body.get(k) for k in ("agent_concurrency", "run_timeout", "idle_timeout")
            if body.get(k) is not None}
    if not updates and not conc:
        raise ServiceError(400, "nothing to update: pass agent_concurrency / run_timeout / idle_timeout / agent_memory_kb / max_retries / confirm_timeout / thinking_effort / max_turns")
    if conc:
        await api_agent_concurrency_set(conc)   # 自带校验;校验失败则整单不落盘
    if updates:
        STATE.update(updates)
        save_state(STATE)
    return await api_agent_advanced_get()


async def api_skills_get(refresh: bool = False):
    """Agent 技能清单(对话面板「技能」弹窗):自动扫描各 Agent skills/ 目录,返回清单 + 开关状态。"""
    return {"skills": list_agent_skills(refresh=refresh),
            "skills_disabled": sorted(skills_disabled_setting())}


async def api_skills_text(skill_id: str):
    """技能弹窗「查看文本」:按技能 id 返回该 SKILL.md 全文(只读,路径以扫描清单为准不收任意路径)。"""
    s = next((x for x in scan_agent_skills() if x["id"] == skill_id), None)
    if not s:
        raise ServiceError(404, f"unknown skill id: {skill_id}")
    d = agent_dir(s["agent_id"])
    f = (d / "skills" / s["dir"] / "SKILL.md") if d else None
    if not f or not f.is_file():
        raise ServiceError(404, f"SKILL.md not found: {skill_id}")
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise ServiceError(500, f"read failed: {e}") from e
    return {"id": skill_id, "path": s["path"], "text": text}


async def api_skills_set(body: dict):
    """技能开关提交(设置页入口已下线,保留 API 供脚本/存量调用),两种形态任选:
    - skills_disabled: [id, ...] 整体覆盖;未知 id 拒绝
    - skill_id + enabled: 单项切换
    语义「启用=允许」:开关只是总闸,条件注入型技能仍须满足各自运行时条件才注入;
    持久化到 state.json,对后续启动的运行生效。"""
    known = {s["id"] for s in scan_agent_skills(refresh=True)}
    if body.get("skills_disabled") is not None:
        ids = body.get("skills_disabled")
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
            raise ServiceError(400, "skills_disabled must be a list of skill ids")
        bad = sorted(set(ids) - known)
        if bad:
            raise ServiceError(400, "unknown skill id: " + ", ".join(bad))
        STATE["skills_disabled"] = sorted(set(ids))
    elif body.get("skill_id"):
        sid = str(body["skill_id"])
        if sid not in known:
            raise ServiceError(400, f"unknown skill id: {sid}")
        off = skills_disabled_setting()
        (off.discard if body.get("enabled", True) else off.add)(sid)
        STATE["skills_disabled"] = sorted(off)
    else:
        raise ServiceError(400, "nothing to update: pass skills_disabled or skill_id + enabled")
    save_state(STATE)
    return await api_skills_get()


async def api_skills_upload(agent_id: str, data: bytes, filename: str = ""):
    """对话面板「技能」弹窗「加载技能」:上传技能 zip 包,解压安装到该 Agent 的 skills/ 目录。
    请求体即 zip 原始字节(与插件安装同口径,免 multipart 依赖)。zip 根可以直接是技能内容
    (SKILL.md 在根),也可以套一层技能目录;技能目录名取内层目录名,根级则取 zip 文件名;
    解压前做路径穿越拦截,同名技能已存在则拒绝(先删除再装,避免新旧文件混杂)。"""
    d = agent_dir(str(agent_id or ""))
    if not d:
        raise ServiceError(404, f"no such agent: {agent_id}")
    if not data:
        raise ServiceError(400, "empty upload body")
    if len(data) > MAX_PLUGIN_UPLOAD:
        raise ServiceError(400, "skill package too large (>50MB)")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        entries = [entry for entry in zf.infolist() if not entry.is_dir()]
    except Exception as e:  # noqa: BLE001
        raise ServiceError(400, f"invalid zip: {e}")
    if len(entries) > MAX_PLUGIN_FILES:
        raise ServiceError(400, f"skill package contains too many files (>{MAX_PLUGIN_FILES})")
    if sum(entry.file_size for entry in entries) > MAX_PLUGIN_EXTRACTED:
        raise ServiceError(400, "skill package is too large after extraction (>200MB)")
    names = [entry.filename for entry in entries]
    # 定位 SKILL.md:取层级最浅的一个,其所在目录即技能根(macOS 压缩的 __MACOSX 噪声排除)
    manifests = sorted((n for n in names
                        if Path(n).name == "SKILL.md" and not n.startswith("__MACOSX/")),
                       key=lambda n: n.count("/"))
    if not manifests:
        raise ServiceError(400, "zip 内找不到 SKILL.md")
    prefix = manifests[0][: -len("SKILL.md")]               # ""(根)或 "xxx/"
    if prefix:
        name = Path(prefix.rstrip("/")).name
    else:                                                    # 根级内容:目录名取 zip 文件名
        stem = Path(filename or "").stem
        name = re.sub(r"[^A-Za-z0-9_\-]+", "-", stem).strip("-")
    if not name or not _SKILL_DIR_RE.fullmatch(name):
        raise ServiceError(400, f"非法技能目录名:{name!r}(仅限字母/数字/_-,根级 zip 请用规范文件名)")
    (d / "skills").mkdir(exist_ok=True)
    target = d / "skills" / name
    if target.exists():
        raise ServiceError(409, f"技能 {name} 已存在;请先删除 {target} 再安装")
    staging = d / "skills" / f".{name}.{uuid.uuid4().hex}.tmp"
    staging.mkdir()
    extracted = 0
    try:
        for n in names:
            if not n.startswith(prefix) or n.startswith("__MACOSX/"):
                continue
            rel = n[len(prefix):]
            if not rel or Path(rel).name.startswith(".DS_Store"):
                continue
            dest = (staging / rel).resolve()
            try:
                dest.relative_to(staging.resolve())
            except ValueError as exc:
                raise ServiceError(400, f"zip 含路径穿越条目:{n}") from exc
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(n))
            extracted += 1
        if not extracted:
            raise ServiceError(400, "zip 内没有可解压的技能文件")
        staging.replace(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    scan_agent_skills(refresh=True)
    return {"ok": True, "name": name, "agent_id": agent_id, "files": extracted,
            **await api_skills_get()}


async def api_agent_concurrency_get():
    return {"agent_concurrency": agent_concurrency(),
            "default": AGENT_CONCURRENCY_DEFAULT,
            "max": MAX_CONCURRENT,
            "run_timeout": run_timeout_setting(),
            "run_timeout_default": RUN_TIMEOUT_DEFAULT,
            "run_timeout_min": RUN_TIMEOUT_MIN,
            "run_timeout_max": RUN_TIMEOUT_MAX,
            "idle_timeout": idle_timeout_setting(),
            "idle_timeout_default": IDLE_TIMEOUT_DEFAULT,
            "idle_timeout_max": IDLE_TIMEOUT_MAX}


async def api_agent_concurrency_set(body: dict):
    """并发和超时设置(设置菜单「高级→Agent 高级设置」;前端经 api_agent_advanced_set 合并提交,本接口保留兼容),三项均可选、可单独提交:
    - agent_concurrency:无状态扇出型 Agent(05-scenes/08-video-gen/11-qa/eval 等)的
      同 agent 并发额度;有状态 Agent 恒为 1,全局仍受 MAX_CONCURRENT 总闸
    - run_timeout:单次运行墙钟超时(秒,调度型 Agent 自动 4 倍)
    - idle_timeout:引擎事件流无输出超时(秒,0=关闭;调度型 Agent 不受约束)
    持久化,立即对后续启动的运行生效(在跑的运行沿用启动时的取值)。"""
    updates: dict[str, int] = {}
    if body.get("agent_concurrency") is not None:
        try:
            n = int(body.get("agent_concurrency"))
        except (TypeError, ValueError):
            raise ServiceError(400, "agent_concurrency must be an integer") from None
        if not 1 <= n <= MAX_CONCURRENT:
            raise ServiceError(400, f"agent_concurrency must be between 1 and {MAX_CONCURRENT}")
        updates["agent_concurrency"] = n
    if body.get("run_timeout") is not None:
        try:
            n = int(body.get("run_timeout"))
        except (TypeError, ValueError):
            raise ServiceError(400, "run_timeout must be an integer (seconds)") from None
        if not RUN_TIMEOUT_MIN <= n <= RUN_TIMEOUT_MAX:
            raise ServiceError(400, f"run_timeout must be between {RUN_TIMEOUT_MIN} and {RUN_TIMEOUT_MAX} seconds")
        updates["run_timeout"] = n
    if body.get("idle_timeout") is not None:
        try:
            n = int(body.get("idle_timeout"))
        except (TypeError, ValueError):
            raise ServiceError(400, "idle_timeout must be an integer (seconds, 0 disables)") from None
        if not 0 <= n <= IDLE_TIMEOUT_MAX:
            raise ServiceError(400, f"idle_timeout must be between 0 and {IDLE_TIMEOUT_MAX} seconds")
        updates["idle_timeout"] = n
    if not updates:
        raise ServiceError(400, "nothing to update: pass agent_concurrency / run_timeout / idle_timeout")
    STATE.update(updates)
    save_state(STATE)
    return await api_agent_concurrency_get()


async def api_agent_memory_get():
    return {"agent_memory": agent_memory_enabled(),
            "agent_memory_kb": agent_memory_kb(),
            "agent_memory_kb_default": AGENT_MEMORY_KB_DEFAULT,
            "agent_memory_kb_max": AGENT_MEMORY_KB_MAX}


async def api_agent_memory_set(body: dict):
    """Agent记忆设置(设置菜单「高级→Agent 高级设置」;前端经 api_agent_advanced_set 合并提交,本接口保留兼容):
    agent_memory_kb=对话记忆额度(KB,0..AGENT_MEMORY_KB_MAX):>0=有状态 Agent 恢复
    上次会话,单会话历史超过该额度自动新开;0=所有 Agent 每次运行全新会话。持久化,
    立即对后续运行生效;关闭/调小期间会话号照常回存,重新调大后从最近一次会话继续。
    旧布尔字段 agent_memory 仍兼容(True=缺省额度,False=0;两者同给时以额度为准)。"""
    if body.get("agent_memory_kb") is not None:
        try:
            n = int(body.get("agent_memory_kb"))
        except (TypeError, ValueError):
            raise ServiceError(400, "agent_memory_kb must be an integer") from None
        if not 0 <= n <= AGENT_MEMORY_KB_MAX:
            raise ServiceError(400, f"agent_memory_kb must be between 0 and {AGENT_MEMORY_KB_MAX}")
    elif body.get("agent_memory") is not None:   # 旧布尔开关兼容
        n = AGENT_MEMORY_KB_DEFAULT if body["agent_memory"] else 0
    else:
        raise ServiceError(400, "agent_memory_kb must be an integer (KB, 0=off)")
    STATE["agent_memory_kb"] = n
    save_state(STATE)
    return await api_agent_memory_get()


# ---------------- 诊断数据(设置菜单「高级→诊断数据」) ----------------
# 本地结构化事件(run 收敛处与 genmedia CLI 出口按「字段白名单」落盘
# telemetry/outbox,错误消息模板化聚签名)与经验卡(runs/<task_id>/lesson.md,
# WORKFLOW.md §6.1)的汇总、预览与手动导出。只落盘、只导出,永不自动上传——
# 导出 zip 由用户自行提交(如 GitHub issue 附件)。实现在 modules/diagnostics.py
# (genmedia 子进程与本服务共用);懒加载 + 采集端全静默,旁路故障不影响主链路。


def _diagnostics():
    from modules import diagnostics
    return diagnostics


def diagnostics_enabled() -> bool:
    """诊断采集开关(默认开;仅本地落盘,无任何上传)。"""
    return bool(STATE.get("diagnostics_enabled", True))


async def api_diagnostics_get():
    d = await asyncio.to_thread(lambda: _diagnostics().summary())
    d["enabled"] = diagnostics_enabled()   # STATE 为准,避开文件读取的 TTL 缓存
    return d


async def api_diagnostics_set(body: dict):
    if body.get("diagnostics_enabled") is None:
        raise ServiceError(400, "diagnostics_enabled must be a boolean")
    STATE["diagnostics_enabled"] = bool(body["diagnostics_enabled"])
    save_state(STATE)
    return {"enabled": diagnostics_enabled()}


async def api_diagnostics_lessons():
    return {"lessons": await asyncio.to_thread(lambda: _diagnostics().scan_lessons())}


async def api_diagnostics_clear():
    return {"ok": True,
            "removed": await asyncio.to_thread(lambda: _diagnostics().clear_outbox())}


async def api_diagnostics_export(body: dict):
    """构建诊断导出包:事件(可选)+ 聚合摘要 + 用户勾选的经验卡。
    经验卡路径在 diagnostics 侧对照 scan_lessons() 白名单,防任意文件打包。"""
    lessons = body.get("lessons") or []
    if not isinstance(lessons, list) or not all(isinstance(x, str) for x in lessons):
        raise ServiceError(400, "lessons must be a list of paths")
    include_events = bool(body.get("include_events", True))
    path = Path(await asyncio.to_thread(
        lambda: _diagnostics().build_export(lessons, include_events)))
    return {"ok": True, "name": path.name, "bytes": path.stat().st_size}


def diagnostics_export_path(name: str) -> Path:
    """导出包下载路径校验:仅放行 telemetry/export 下本服务生成的文件名格式。"""
    if not re.fullmatch(r"diagnostics-\d{8}-\d{6}\.zip", name or ""):
        raise ServiceError(400, "invalid export name")
    p = _diagnostics().EXPORT_DIR / name
    if not p.is_file():
        raise ServiceError(404, "export not found")
    return p


async def api_usage():
    """claude/codex 当前会话(5h 窗口)已用百分比;取不到为 null(设置弹窗显示用)。"""
    codex, claude = await asyncio.gather(
        asyncio.to_thread(engine_usage_percent, "codex"),
        asyncio.to_thread(engine_usage_percent, "claude"))
    orch = next(iter(DISPATCHERS))
    return {"codex": {"used": codex}, "claude": {"used": claude},
            "gate_engine": agent_effective_model(orch)["engine"]}


async def api_resources_config_get():
    """资源消耗设置；密钥保存在运行状态目录，不向客户端回传明文。"""
    cfg = resource_cfg()
    # 开关一律回传手动配置值(不含随顶栏引擎的自动开启,否则保存会把自动固化成手动)
    return {"claude_probe": bool(cfg.get("claude_probe")),
            "claude_probe_env": CLAUDE_USAGE_PROBE_ENABLED,
            "codex_probe": codex_probe_manual(),
            "kimi_probe": kimi_probe_manual(),
            "kimi_api_key": cfg.get("kimi_api_key") or "",
            "opencode_probe": opencode_probe_manual(),
            "opencode_api_key": cfg.get("opencode_api_key") or "",
            "openrouter_key": cfg.get("openrouter_key") or "",
            "volc_enabled": bool(cfg.get("volc_enabled")),
            "volc_ak": cfg.get("volc_ak") or "",
            "volc_sk": cfg.get("volc_sk") or "",
            "rh_enabled": bool(cfg.get("rh_enabled")),
            "rh_key_ai": cfg.get("rh_key_ai") or "",
            "rh_key_cn": cfg.get("rh_key_cn") or ""}


async def api_resources_config_set(body: dict):
    """保存资源消耗设置:只更新给出的字段;保存后清用量/余额缓存立即生效。"""
    cfg = STATE.setdefault("resources", {})
    for k in ("claude_probe", "codex_probe", "kimi_probe", "opencode_probe",
              "volc_enabled", "rh_enabled"):
        if body.get(k) is not None:
            cfg[k] = bool(body[k])
    for k in ("kimi_api_key", "opencode_api_key", "openrouter_key", "volc_ak",
              "volc_sk", "rh_key_ai", "rh_key_cn"):
        if body.get(k) is not None:
            if not isinstance(body[k], str) or len(body[k]) > 500:
                raise ServiceError(400, f"{k} must be a string (≤500 chars)")
            cfg[k] = body[k].strip()
    save_state(STATE)
    _USAGE_CACHE.clear()
    _BALANCE_CACHE.clear()
    return await api_resources_config_get()


async def api_resources(fresh: bool = False):
    """资源消耗面板聚合数据:四引擎 session/weekly 用量与重置时间 + 已配置渠道的账户余额。
    只探测已开启用量检查的引擎,全部探测并发跑(各自带 TTL 缓存,fresh=1 清缓存
    强制重测);取不到的读数为 null。"""
    if fresh:
        _USAGE_CACHE.clear()
        _BALANCE_CACHE.clear()
    empty = {"session": None, "weekly": None,
             "session_resets_at": None, "weekly_resets_at": None}

    async def usage(engine, on):
        return await asyncio.to_thread(engine_usage_full, engine) if on else dict(empty)
    claude_on, codex_on, kimi_on, opencode_on = (
        claude_probe_enabled(), codex_probe_enabled(), kimi_probe_enabled(),
        opencode_probe_enabled())
    cu, co, ki, oc, orb, vb, rb = await asyncio.gather(
        usage("claude", claude_on),
        usage("codex", codex_on),
        usage("kimi", kimi_on),
        usage("opencode", opencode_on),
        asyncio.to_thread(provider_balance, "openrouter"),
        asyncio.to_thread(provider_balance, "volc"),
        asyncio.to_thread(provider_balance, "runninghub"))
    cfg = resource_cfg()
    rh_keys = any((cfg.get(k) or "").strip() for k in ("rh_key_ai", "rh_key_cn"))
    return {"claude": {**cu, "enabled": claude_on},
            "codex": {**co, "enabled": codex_on},
            "kimi": {**ki, "enabled": kimi_on},
            "opencode": {**oc, "enabled": opencode_on},
            "openrouter": {"configured": bool((cfg.get("openrouter_key") or "").strip()),
                           **(orb or {})},
            "volc": {"configured": bool(cfg.get("volc_enabled")), **(vb or {})},
            "runninghub": {"configured": bool(cfg.get("rh_enabled") and rh_keys),
                           "sites": rb or {}}}


async def api_watchdog_get(project: str = ""):
    """按项目查看空转看门狗开关(缺省关闭)与常任指令。不带 project 时返回全部映射。"""
    wd = STATE.get("watchdog", {})
    if project:
        proj = safe_slug(project)
        return {"project": proj, "enabled": bool(wd.get(proj, False)),
                "orders": str(STATE.get("watchdog_orders", {}).get(proj) or "")}
    return {"watchdog": wd}


async def api_watchdog_set(body: dict):
    """按项目设置空转看门狗:enabled=开关(运行面板 🤖 按钮/聊天 /auto 命令),
    orders=常任指令(设置菜单「自动运行」,随每条唤醒消息附带,空串清除)。
    两项均可选,只更新给出的项;逐项目独立,状态持久化,重启后保持。
    开关变化广播 HUB watchdog 事件,网页 🤖 按钮据此同步(命令/其他页签改的也能跟上)。"""
    proj = safe_slug(body.get("project"))
    if body.get("enabled") is not None:
        enabled = bool(body.get("enabled"))
        wd = STATE.setdefault("watchdog", {})
        changed = bool(wd.get(proj, False)) != enabled
        wd[proj] = enabled
        if changed:
            HUB.publish({"type": "watchdog", "project": proj, "enabled": enabled})
    if body.get("orders") is not None:
        orders = str(body.get("orders")).strip()
        if len(orders) > 2000:
            raise ServiceError(400, "orders must be at most 2000 characters")
        od = STATE.setdefault("watchdog_orders", {})
        if orders:
            od[proj] = orders
        else:
            od.pop(proj, None)
    save_state(STATE)
    sync_keepawake()    # 开/关项目自动运行随手校准防休眠
    return await api_watchdog_get(proj)


# ---------------- 素材库(设置菜单「高级→素材库」:data/footage/<name>/) ----------------
# 业务实现在 modules/footage_library.py(零 core 依赖,可独立 CLI 排障);这里只做薄封装:
# ServiceError 映射、默认 CLI 引擎规格(全局模型偏好 → 可执行文件)与界面语言的注入。

def _footage_lib():
    mods = str(ROOT / "modules")
    if mods not in sys.path:
        sys.path.insert(0, mods)
    import footage_library  # noqa: WPS433
    return footage_library


def _footage_call(fn, *args, **kw):
    lib = _footage_lib()
    try:
        return fn(lib, *args, **kw)
    except lib.FootageLibError as exc:
        raise ServiceError(exc.status, exc.detail) from exc


def footage_engine_spec() -> dict:
    """AI 画面分析用的引擎规格:当前全局默认引擎/模型(未设置回落 claude)。"""
    pref = global_model_pref()
    engine = pref["engine"] or "claude"
    model = pref["model"]
    if model.startswith("__"):
        # 顶栏「智能分配」策略(__smart)不是具体模型:画面分析是单轮看图小活,取该引擎策略的 low 档
        mode = SMART_MODE_BY_ENGINE.get(engine, "")
        model = ((AM_MODE_MODELS.get(mode) or {}).get("low") or {}).get("model", "")
    spec = {"engine": engine, "model": model, "permission_mode": PERMISSION_MODE,
            "executable": resolve_cli_executable(engine) if engine != "deepagents" else None}
    if engine == "deepagents":
        da = resolve_deepagents()
        spec["deepagents"] = {"python": deepagents_python(),
                              "runner": str(ROOT / "modules" / "deepagents_runner.py"),
                              "base_url": da["base_url"], "api_key": da["api_key"],
                              "model": da["model"]}
    return spec


async def api_footage_list():
    spec = footage_engine_spec()
    return {"projects": _footage_call(lambda lib: lib.list_projects()),
            "dir": str(_footage_lib().FOOTAGE_DIR), "engine": spec["engine"], "model": spec["model"]}


async def api_footage_create(body: dict):
    return _footage_call(lambda lib: lib.create_project((body or {}).get("name") or None))


async def api_footage_get(name: str):
    spec = footage_engine_spec()
    return {**_footage_call(lambda lib: lib.get_project(name)),
            "engine": spec["engine"], "model": spec["model"]}


async def api_footage_settings_set(name: str, body: dict):
    _footage_call(lambda lib: lib.update_settings(name, body or {}))
    return await api_footage_get(name)


async def api_footage_delete(name: str):
    return await asyncio.to_thread(_footage_call, lambda lib: lib.delete_project(name))


async def api_footage_upload(name: str, data: bytes, upload_id: str, index: int, total: int,
                             filename: str):
    return await asyncio.to_thread(
        _footage_call, lambda lib: lib.receive_upload_chunk(name, data, upload_id, index, total, filename))


async def api_footage_download(name: str, body: dict):
    return _footage_call(lambda lib: lib.start_download(name, (body or {}).get("url", "")))


async def api_footage_reprocess(name: str):
    return _footage_call(lambda lib: lib.reprocess(name))


async def api_footage_retranscribe(name: str):
    return _footage_call(lambda lib: lib.retranscribe(name))


async def api_footage_cancel(name: str):
    return _footage_call(lambda lib: lib.cancel(name))


async def api_footage_clip_update(name: str, clip_id: str, body: dict):
    return _footage_call(lambda lib: lib.update_clip(name, clip_id, body or {}))


async def api_footage_clip_delete(name: str, clip_id: str):
    return _footage_call(lambda lib: lib.delete_clip(name, clip_id))


async def api_footage_clip_analyze(name: str, clip_id: str):
    spec, lang = footage_engine_spec(), ui_lang_code() or "zh"
    return await asyncio.to_thread(
        _footage_call, lambda lib: lib.analyze_clip(name, clip_id, spec, lang))


async def api_footage_analyze_all(name: str, body: dict):
    spec, lang = footage_engine_spec(), ui_lang_code() or "zh"
    return _footage_call(lambda lib: lib.analyze_all(name, spec, lang, bool((body or {}).get("force"))))


def footage_file_path(name: str, rel: str) -> Path:
    return _footage_call(lambda lib: lib.resolve_file(name, rel))


# ---------------- 直播(设置菜单「高级→直播」:data/live/) ----------------
# 业务实现在 modules/live_stream.py(零 core 依赖,可独立 CLI 排障);这里只做薄封装:
# LiveError → ServiceError 映射,并注入「🎨 生成模型」页的 Fal 模型目录(与 Key 共用同一配置)。

def _live_lib():
    mods = str(ROOT / "modules")
    if mods not in sys.path:
        sys.path.insert(0, mods)
    import live_stream  # noqa: WPS433
    return live_stream


def _live_call(fn, *args, **kw):
    lib = _live_lib()
    try:
        return fn(lib, *args, **kw)
    except lib.LiveError as exc:
        raise ServiceError(exc.status, exc.detail) from exc


def _live_decorate(res: dict) -> dict:
    """状态附带 Fal 模型目录(与生成模型页同一份 VIDEO_MODEL_CATALOG)与生效渠道的模型。"""
    fal = (load_genconfig().get("video") or {}).get("fal") or {}
    return {**res, "models": [list(m) for m in VIDEO_MODEL_CATALOG.get("fal", [])],
            "providers": [["fal", "Fal"]],
            "genconfig_model": str(fal.get("custom_model") or fal.get("model") or "")}


async def api_live_status(touch: bool = True, played: int | None = None):
    return _live_decorate(await asyncio.to_thread(
        _live_call, lambda lib: lib.status(touch=touch, played=played)))


async def api_live_ref_add(data: bytes, filename: str):
    return await asyncio.to_thread(_live_call, lambda lib: lib.add_ref(data, filename))


async def api_live_ref_delete(ref_id: str):
    return _live_call(lambda lib: lib.delete_ref(ref_id))


async def api_live_start(body: dict):
    return _live_decorate(await asyncio.to_thread(_live_call, lambda lib: lib.start(body or {})))


async def api_live_stop():
    return _live_decorate(await asyncio.to_thread(_live_call, lambda lib: lib.stop()))


async def api_live_prompt(body: dict):
    return _live_decorate(_live_call(lambda lib: lib.update_prompt(str((body or {}).get("prompt") or ""))))


async def api_live_settings(body: dict):
    return {"settings": _live_call(lambda lib: lib.save_settings(body or {}))}


async def api_live_clear():
    return _live_decorate(await asyncio.to_thread(_live_call, lambda lib: lib.clear_history()))


async def api_live_sessions():
    return {"sessions": await asyncio.to_thread(_live_call, lambda lib: lib.list_sessions())}


async def api_live_session_new():
    return _live_decorate(await asyncio.to_thread(_live_call, lambda lib: lib.new_session()))


async def api_live_session_load(sid: str, body: dict):
    b = body or {}
    return _live_decorate(await asyncio.to_thread(
        _live_call, lambda lib: lib.select_session(sid, apply_settings=bool(b.get("apply_settings", True)),
                                                   restore_refs=bool(b.get("restore_refs", True)))))


async def api_live_session_delete(sid: str):
    return _live_decorate(await asyncio.to_thread(_live_call, lambda lib: lib.delete_session(sid)))


async def api_live_director_bridge(path: str, body: dict):
    return await asyncio.to_thread(_live_call, lambda lib: lib.director_bridge(path, body or {}))


async def api_live_director_config():
    return _live_call(lambda lib: lib.director_config())


async def api_live_director_event(body: dict):
    return _live_decorate(await asyncio.to_thread(_live_call, lambda lib: lib.director_event(body or {})))


async def api_live_director_record(seq: int, data: bytes, ext: str):
    return await asyncio.to_thread(_live_call, lambda lib: lib.director_record(int(seq), data, ext))


def live_file_path(rel: str) -> Path:
    return _live_call(lambda lib: lib.resolve_file(rel))

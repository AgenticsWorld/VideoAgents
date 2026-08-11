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
import os
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
CLI_BINS = {"claude": CLAUDE_BIN, "codex": CODEX_BIN, "kimi": KIMI_BIN,
            "pi": PI_BIN}
CLI_LABELS = {"claude": "Claude Code", "codex": "Codex CLI", "kimi": "Kimi Code",
              "pi": "Pi Coding Agent"}
CLI_ENV_VARS = {"claude": "CLAUDE_BIN", "codex": "CODEX_BIN", "kimi": "KIMI_BIN",
                "pi": "PI_BIN"}


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
ENGINES = ("claude", "codex", "kimi", "pi", "deepagents")   # 执行引擎:CLI 或 deepagents runner
PERMISSION_MODE = os.environ.get("VIDEOAGENTS_PERMISSION_MODE", "acceptEdits")
CLAUDE_USAGE_PROBE_ENABLED = os.environ.get(
    "VIDEOAGENTS_ENABLE_CLAUDE_USAGE_PROBE", ""
).lower() in {"1", "true", "yes"}
MAX_TURNS = "100"
MAX_CONCURRENT = 8                       # 同时运行的工人进程上限(调度器不占槽,见 execute_run)
RUN_TIMEOUT = 3600                       # 单次运行超时(秒)
STREAM_LIMIT = 32 * 1024 * 1024          # 子进程 stdout 单行缓冲上限(stream-json 一行可能带整个文件内容)
# 拥有调度权的 Agent(系统提示词里会附加 dispatch.py 用法);仅总制片,导演不派单
DISPATCHERS = {"00-orchestration/workflow-orchestrator"}
# 无状态服务型/扇出型 Agent:每次派单自足(context.md + SOUL 注入),不 resume 会话、
# 同 agent 允许并发(否则 8 个 eval/QA 会被 AGENT_SEMS 串成一列)。
# 08-video-gen 为组级扇出工位(prompt/imagegen/videogen…每组一单,2026-07-23 纳入):
# 组间依赖由 DAG depends_on 表达,不靠会话串行,同 agent 并发是 Phase 7 吞吐关键。
# 05-scenes 为场景级扇出工位(environment/architecture/lighting 每场景一单,2026-07-28 纳入):
# 大项目场景数可达 70+,同 agent 串行会拖垮 Phase 3。
# 03-characters(appearance/personality…每角色一单)、06-art(char-concept 每角色/
# env-concept 每场景一单)同为扇出工位,novel-parser 按章节分块工单,2026-07-28 一并纳入。
# 13-derivative-fiction/line-editor(插件 Agent,每章一单)章节级扇出,2026-07-29 纳入;
# prose-writer 不纳入:上一章正文是下一章输入,须线性串行执行(保持有状态)
STATELESS_AGENTS = {"00-orchestration/context", "00-orchestration/evaluation",
                    "01-story/novel-parser"}
STATELESS_PREFIXES = ("11-qa/", "08-video-gen/", "05-scenes/",
                      "03-characters/", "06-art/",
                      "13-derivative-fiction/line-editor")
# 无状态 agent 的同 agent 并发额度缺省值(设置菜单「高级→并发数量」可调,存 state.json);
# 有状态 agent 恒为 1(串行保护会话),全局仍受 MAX_CONCURRENT 总闸
AGENT_CONCURRENCY_DEFAULT = 5
# Agent 对话记忆缺省值(设置菜单「高级→Agent记忆」可关,存 state.json):
# 开启=有状态 agent 按 engine::agent::project 恢复上次会话(下方 CHAT_RESUME_LIMIT
# 128KB 保险丝仍生效);关闭=所有 agent 每次运行全新会话,跨工单记忆只靠落盘产物。
# 关闭期间 session_id 照常回存,重新开启后从最近一次会话继续
AGENT_MEMORY_DEFAULT = True
# 会话膨胀保险丝:chats/<agent>.jsonl 超过此大小则不再 --resume(新开会话),
# 防止 resume 每轮重发全史(实测 codex 单 run 累计 input 曾达 1400 万 token);
# 2026-07-23 由 512KB 压至 128KB:长会话后段每轮重发全史又慢又贵
CHAT_RESUME_LIMIT = 128 * 1024
# deepagents 引擎单独调低:该保险丝量的是 chats/*.jsonl(只有用户/助手文本),
# 而 deepagents 检查点历史含全部工具消息(DAG 查询输出、read_file 全文),
# 真实会话体量被严重低估;且 OpenAI 兼容端点 resume=每轮重发全史,本地模型
# 上下文窗口小、OpenRouter 渠道无厂商 prompt cache 兜底,须更早换新会话
DEEPAGENTS_RESUME_LIMIT = 32 * 1024
# 总制片(仅 workflow-orchestrator 一个,不随 DISPATCHERS 扩员生效)单独调低:
# 长会话里系统提示约束力被历史稀释,一旦出现过一次"自己动手跑生成"的先例还会被
# 模型自我模仿;调度状态权威在 runs/dag.json 上,新开会话零成本,且 codex 引擎
# 只在新会话首轮注入 SOUL,更需要尽早重开
ORCHESTRATOR_AGENT = "00-orchestration/workflow-orchestrator"
ORCHESTRATOR_RESUME_LIMIT = 128 * 1024
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
    for sub in ("style", "characters", "scenes", "props", "music", "thumbnail"):
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


def agent_sem(agent_id: str, limit: int) -> asyncio.Semaphore:
    cached = AGENT_SEMS.get(agent_id)
    if cached is None or cached[0] != limit:
        cached = (limit, asyncio.Semaphore(limit))
        AGENT_SEMS[agent_id] = cached
    return cached[1]


def agent_memory_enabled() -> bool:
    """Agent 对话记忆总开关(设置菜单「高级→Agent记忆」)。"""
    return bool(STATE.get("agent_memory", AGENT_MEMORY_DEFAULT))


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
        "provider": "volcengine",   # openrouter | ideogram | volcengine | byteplus | minimax | comfyui
        "openrouter": {"api_key": "", "model": "bytedance-seed/seedream-4.5",
                       "custom_model": ""},
        "ideogram": {"api_key": "", "model": "V_3", "custom_model": ""},
        "volcengine": {"api_key": "", "model": "doubao-seedream-5-0-260128",
                       "custom_model": ""},
        "byteplus": {"api_key": "", "model": "seedream-5-0-260128",
                     "custom_model": ""},
        # MiniMax:api_base 按「接口区域」二选一(海外 api.minimax.io/国内 api.minimaxi.com,
        # 两平台账号与 Key 不互通,api_key_io/api_key_cn 按区域分别保存,按 api_base 取用);
        # 图像/视频/音乐/TTS 四段各自独立保存
        "minimax": {"api_key_io": "", "api_key_cn": "",
                    "api_base": "https://api.minimax.io",
                    "model": "image-01", "custom_model": ""},
        # mode: local | cloud(Comfy Cloud)| rh_cn / rh_ai(RunningHub 国内/国际站,
        # 账号与 Key 不互通,rh_api_key_cn/rh_api_key_ai 按站点分别保存,按 mode 取用);
        # rh_workflows 为工作区工作流收藏 [{id, note, site}]
        "comfyui": {"mode": "local", "url": "http://127.0.0.1:8188", "cloud_api_key": "",
                    "workflow": "",
                    "ref_workflow": "", "negative_mode": "conditioning", "checkpoint": "",
                    "rh_api_key_cn": "", "rh_api_key_ai": "",
                    "rh_workflow_id": "", "rh_ref_workflow_id": "",
                    "rh_workflows": []},
    },
    "video": {
        "provider": "volcengine",   # openrouter | volcengine | byteplus | minimax | comfyui
        "openrouter": {"api_key": "", "model": "bytedance/seedance-2.0",
                       "custom_model": ""},
        "volcengine": {"api_key": "", "model": "doubao-seedance-2-0-260128",
                       "custom_model": ""},
        "byteplus": {"api_key": "", "model": "dreamina-seedance-2-0-260128",
                     "custom_model": ""},
        # MiniMax-H3:分辨率仅 768P/2K,genmedia 把项目档位(360p..4k)自动就近映射
        "minimax": {"api_key_io": "", "api_key_cn": "",
                    "api_base": "https://api.minimax.io",
                    "model": "MiniMax-H3", "custom_model": ""},
        "comfyui": {"mode": "local", "url": "http://127.0.0.1:8188", "cloud_api_key": "",
                    "workflow": "", "checkpoint": "",
                    "rh_api_key_cn": "", "rh_api_key_ai": "",
                    "rh_workflow_id": "", "rh_workflows": []},
    },
    "music": {
        "provider": "elevenlabs",   # openrouter(Lyria 3 系列)| elevenlabs(Eleven Music)| minimax
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
                    "rh_workflow_id": "", "rh_workflows": []},
    },
    "tts": {
        "provider": "volcengine",   # openrouter | volcengine(豆包语音) | minimax | elevenlabs
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
                    "rh_workflow_id": "", "rh_workflows": []},
    },
    # deepagents 文字模型:local=OpenAI 兼容本地端点(LM Studio/Ollama/vLLM…);
    # cloud=OpenAI 兼容云端端点(默认 DeepSeek 官方 API,可换任意兼容服务商);
    # openrouter=OpenRouter 云端(base_url 固定 https://openrouter.ai/api/v1)
    "deepagents": {
        "provider": "local",   # local | cloud | openrouter
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
    # 时长设置:每集目标时长(分钟)与单个分镜时长范围(秒)
    "duration": {"episode_minutes": 10, "shot_min_s": 4, "shot_max_s": 8},
    # 分镜组设置:生成组总时长上限与每组参考素材数量上限——须与所选视频生成模型的
    # 能力匹配(Seedance 2.0 系列:≤15s/9图/3视频/3音频;Seedance 2.5:≤30s/30图/
    # 10视频/10音频),默认值按 2.0 的保守口径;注入 Agent 系统提示词约束分组与
    # prompt 组装,模型侧硬限另由 genmedia 按 model id 强制校验
    "shot_group": {"max_group_s": 15, "max_ref_images": 9,
                   "max_ref_videos": 1, "max_ref_audios": 2},
    # 「模型策略」(设置菜单子菜单):global=全部跟随顶栏全局(初始化默认);
    # smart_claude / smart_codex=按 Agent 任务复杂度自动选对应引擎的模型
    "agentmodel_mode": "global",
    # 输出设置(设置菜单「输出设置」):画幅预设 youtube=16:9(默认)/douyin=9:16/custom;
    # 语言约束剧本/台词/旁白/字幕/配音/发布物料;
    # 视频分辨率按用途分档:draft=草稿/迭代/待审版本,final=审核确认后的成片终稿;
    # platforms=发布平台(可多选,默认全选):只决定 Phase 11 发布目标与画幅矩阵/封面/字幕的平台清单,
    #   主生产画幅仍由 aspect_preset 单选决定;与主画幅不同画幅的平台由 platform-adapter 发布期裁/补适配;
    # subtitle_burn_in=内嵌字幕(默认关):开启后成片终稿自动把 subtitles.srt 烧录进画面
    "output": {"aspect_preset": "youtube", "aspect_custom": "", "language": "English",
               "draft_resolution": "480p", "final_resolution": "480p",
               "subtitle_burn_in": False,
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

# 输出画幅预设:preset -> (比例, 名称);custom 走 aspect_custom(格式 宽:高)
OUTPUT_ASPECTS = {"youtube": ("16:9", "YouTube 横屏"), "douyin": ("9:16", "抖音竖屏")}
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


def resolve_output(cfg: dict) -> tuple[str, str, str]:
    """genconfig -> (画幅比例, 画幅名称, 输出语言)。"""
    out = cfg.get("output") or {}
    preset = out.get("aspect_preset") or "youtube"
    if preset == "custom":
        aspect = re.sub(r"\s", "", out.get("aspect_custom") or "") or "16:9"
        name = "自定义"
    else:
        aspect, name = OUTPUT_ASPECTS.get(preset, OUTPUT_ASPECTS["youtube"])
    lang = out.get("language") or "English"
    return aspect, name, lang


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
    for kind in ("image", "video", "music", "tts"):
        section = config.get(kind, {})
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


def active_video_model(cfg: dict | None = None) -> str:
    """「生成模型」页当前生效的视频模型 id(custom_model 优先;comfyui 等无模型渠道返 "")。"""
    v = (cfg or load_genconfig()).get("video") or {}
    pc = v.get(v.get("provider") or "volcengine") or {}
    return str(pc.get("custom_model") or pc.get("model") or "")


def is_seedance25(model: str) -> bool:
    """Seedance 2.5 判定(与 genmedia._seedance_gen 同口径:大小写不敏感,兼容点号写法)。"""
    m = (model or "").lower()
    return "seedance-2-5" in m or "seedance-2.5" in m


# Seedance 2.5 官方提示词优化 skill(sd25-pe):仅当生效视频模型为 2.5 时注入
# 加载指令给 prompt agent;文件经官方 .well-known 索引下载并 sha256 校验后随仓库安装
SD25_PE_SKILL = "agents/08-video-gen/prompt/skills/sd25-pe/SKILL.md"


def is_minimax_h3(model: str) -> bool:
    """MiniMax H3 判定(命中 minimax-h3 / MiniMax: H3 / MiniMax-H3 等写法,大小写不敏感)。"""
    m = re.sub(r"[\s_:]+", "-", (model or "").lower())
    return "minimax-h3" in m


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
    """生效视频渠道是否 MiniMax H3:OpenRouter/MiniMax 等按模型 id 关键字,
    ComfyUI 本地/Comfy Cloud 按所选工作流文件名(如 comfy/video-minimax-h3-ref2va-api.json),
    RunningHub 运行方式按缓存的云端工作流 JSON 是否含 MiniMaxH3ReferenceToVideo 节点。"""
    cfg = cfg or load_genconfig()
    v = cfg.get("video") or {}
    if (v.get("provider") or "volcengine") == "comfyui":
        comfy = v.get("comfyui") or {}
        if (comfy.get("mode") or "local") in RH_BASES:
            return "MiniMaxH3ReferenceToVideo" in _rh_cached_workflow(comfy)
        return is_minimax_h3(comfy.get("workflow") or "")
    return is_minimax_h3(active_video_model(cfg))


# MiniMax H3 官方提示词写作 skill(h3-prompt-writing):仅当生效视频渠道为 H3 时注入
# 加载指令给 prompt agent;经 npx skills add MiniMax-AI/MiniMax-H3 安装后随仓库分发
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


DEEPAGENTS_OPENROUTER_URL = "https://openrouter.ai/api/v1"
DEEPAGENTS_CLOUD_URL = "https://api.deepseek.com"


def resolve_deepagents(cfg: dict | None = None) -> dict:
    """deepagents 配置 -> 生效渠道的 {provider, base_url, api_key, model}。"""
    da = (cfg or load_genconfig()).get("deepagents") or {}
    if (da.get("provider") or "local") == "openrouter":
        o = da.get("openrouter") or {}
        return {"provider": "openrouter", "base_url": DEEPAGENTS_OPENROUTER_URL,
                "api_key": o.get("api_key") or "",
                "model": o.get("model") or o.get("custom_model") or ""}
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
                         "packaging", "versioning")


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
        ep = float(d.get("episode_minutes", 10))
        mn = float(d.get("shot_min_s", 4))
        mx = float(d.get("shot_max_s", 8))
        assert ep > 0 and 0 < mn <= mx
    except (TypeError, ValueError, AssertionError):
        raise ServiceError(400, "Invalid duration settings: episode duration must be > 0; shot duration must satisfy 0 < min <= max") from None


def _validate_shot_group(g: dict):
    """分镜组设置:数值范围按当前支持的最强模型口径(Seedance 2.5)封顶。"""
    try:
        gs = float(g.get("max_group_s", 15))
        ni = int(g.get("max_ref_images", 9))
        nv = int(g.get("max_ref_videos", 1))
        na = int(g.get("max_ref_audios", 2))
        assert 4 <= gs <= 30 and 0 <= ni <= 30 and 0 <= nv <= 10 and 0 <= na <= 10
    except (TypeError, ValueError, AssertionError):
        raise ServiceError(400, "Invalid shot_group settings: max_group_s must be 4-30; "
                                "max_ref_images 0-30; max_ref_videos 0-10; max_ref_audios 0-10") from None


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

# 「模型策略」(genconfig.agentmodel_mode,设置菜单「模型策略」子菜单切换):
# 切换任一策略都会同时清空 agentmodels.json 里全部 Agent 级单独配置。
#   global       全部 Agent 跟随顶栏全局设置(系统初始化默认)
#   smart_claude 按任务复杂度自动选 claude 模型(high→opus-5 low→sonnet)
#   smart_codex  按任务复杂度自动选 codex 模型(high→gpt-5.6-sol low→gpt-5.6-terra)
#   smart_kimi   按任务复杂度自动选 kimi 模型(high→K3 low→K2.7 Coding)
AM_MODES = ("global", "smart_claude", "smart_codex", "smart_kimi")

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
    "00-orchestration/context": "low",                  # context 打包 = 机械活
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
    "smart_claude": {"high": {"engine": "claude", "model": "claude-opus-5"},
                     "low": {"engine": "claude", "model": "sonnet"}},
    "smart_codex": {"high": {"engine": "codex", "model": "gpt-5.6-sol"},
                    "low": {"engine": "codex", "model": "gpt-5.6-terra"}},
    "smart_kimi": {"high": {"engine": "kimi", "model": "kimi-code/k3"},
                   "low": {"engine": "kimi", "model": "kimi-code/kimi-for-coding"}},
}

AM_ENGINES = ("", "claude", "codex", "kimi", "pi", "deepagents")      # "" = 跟随全局
AM_IMAGE_PROVIDERS = ("", "openrouter", "ideogram", "volcengine", "byteplus", "minimax", "comfyui")
AM_VIDEO_PROVIDERS = ("", "openrouter", "volcengine", "byteplus", "minimax", "comfyui")


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
    return ov if isinstance(ov, dict) else default_agent_model(agent_id)


def global_model_pref() -> dict:
    """顶栏全局引擎/模型的服务端副本(经 /api/v1/config/global-model 同步)。
    顶栏选择本体存浏览器 localStorage,服务端自发对话(设置变更通知/看门狗唤醒/
    项目初始化)没有浏览器上下文,靠这份副本跟随顶栏全局。"""
    p = STATE.get("global_model") or {}
    eng = str(p.get("engine") or "").lower()
    model = str(p.get("model") or "").strip()
    # DeepAgents 顶栏的 model 选择器实际存的是渠道(local/cloud/openrouter)。旧 UI 会把
    # 渠道名写入 global_model，导致它覆盖 genconfig 中的真实模型 ID。
    if eng == "deepagents" and (not model or model in ("local", "cloud", "openrouter")):
        model = resolve_deepagents()["model"]
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
# codex:本地 ~/.codex/sessions/**/*.jsonl 会记录 rate_limits(primary=5h 窗口,secondary=周窗口)。
# claude:本地无用量文件,走 OAuth 探针 GET /api/oauth/usage 取 five_hour/seven_day.utilization;
#   token 读取顺序 env CLAUDE_CODE_OAUTH_TOKEN → macOS Keychain → ~/.claude/.credentials.json,
#   探测失败/token 过期一律返回 None(未知即放行,不冻结流水线)。
# kimi:官方用量接口 GET api.kimi.com/coding/v1/usages(Key 在 ⚙️ 资源消耗 设置里配),
#   usage=周配额,limits[](300min 窗口)=5h 会话配额。
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KIMI_USAGE_URL = "https://api.kimi.com/coding/v1/usages"
_USAGE_CACHE: dict = {}               # engine -> (ts, {"session":pct|None,"weekly":pct|None})
_USAGE_TTL = {"claude": 180, "codex": 180, "kimi": 180}   # claude 探针接口限流激进,≥180s 才安全;codex 探针要 rglob 扫 sessions 目录(秒级),TTL 太短资源面板每开必冷探
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


def claude_probe_enabled() -> bool:
    """Claude 用量探针开关:env VIDEOAGENTS_ENABLE_CLAUDE_USAGE_PROBE 或 ⚙️ 资源消耗 设置。"""
    return CLAUDE_USAGE_PROBE_ENABLED or bool(resource_cfg().get("claude_probe"))


def codex_probe_enabled() -> bool:
    """Codex 用量检查开关(⚙️ 资源消耗 设置);历史无此键时默认开启(保持旧行为)。"""
    v = resource_cfg().get("codex_probe")
    return True if v is None else bool(v)


def kimi_probe_enabled() -> bool:
    """KimiCode 用量检查开关(⚙️ 资源消耗 设置);历史无此键时按是否已配 Key 判定
    (老配置只填了 Key 没有开关,升级后面板行为不变)。"""
    cfg = resource_cfg()
    v = cfg.get("kimi_probe")
    if v is None:
        return bool((cfg.get("kimi_api_key") or "").strip())
    return bool(v)


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


def _kimi_usage_full() -> dict:
    """Kimi Code 官方用量接口:usage=周配额,limits[](300min 窗口)=5h 会话配额。
    重置时间实测字段名为驼峰 resetTime(ISO 带 9 位小数秒,参考 CodexBar docs/kimi.md),
    另按常见命名兼容扫描;取不到为 None。
    未配 Key/接口异常返回 None(资源消耗面板显示未知)。"""
    empty = {"session": None, "weekly": None,
             "session_resets_at": None, "weekly_resets_at": None}
    key = (resource_cfg().get("kimi_api_key") or "").strip()
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
            "kimi": _kimi_usage_full}[engine]()
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


def provider_balance(provider: str) -> dict | None:
    """openrouter/volc 账户余额,带 TTL 缓存;未配置/取不到返回 None。"""
    fn = {"openrouter": _openrouter_balance, "volc": _volc_balance}.get(provider)
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
        "先按片头实测时长(ffprobe intro.mp4)整体平移生成成片基准 subtitles_final.srt,再把**平移后的字幕**"
        "烧录进画面(ffmpeg subtitles 滤镜等)——final.mp4 含片头时严禁直接烧正片基准 SRT,否则字幕整体偏早;"
        "烧录样式严格按 subtitle SOUL.md 的烧录样式规范"
        "(小字号贴底、≤2 行、白字黑描边、禁大面积底板);orchestrator 排期须把该烧录步骤纳入本集必做项,"
        "platform-adapter 发布物料一律基于烧录版母版"
        if out.get("subtitle_burn_in") else
        "关闭(默认)—— 成片不烧录字幕,字幕仅以外挂形式交付:final.mp4 含片头时交付 edit 平移后的成片基准"
        " subtitles_final.srt(严禁把正片 0 秒基准的 subtitles.srt 直接配 final.mp4),发布期按平台字幕清单处理")
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
    ep_minutes = _fmt_num(dur.get("episode_minutes") or 10)
    ep_seconds = _fmt_num(float(dur.get("episode_minutes") or 10) * 60)
    shot_min = _fmt_num(dur.get("shot_min_s") or 4)
    shot_max = _fmt_num(dur.get("shot_max_s") or 8)
    sg = ps.get("shot_group") or {}
    sg_max = _fmt_num(sg.get("max_group_s") or 15)
    sg_img = int(sg.get("max_ref_images", 9))
    sg_vid = int(sg.get("max_ref_videos", 1))
    sg_aud = int(sg.get("max_ref_audios", 2))
    p = f"""你是「小说→视频」多 Agent 制作团队的成员,编号:{agent_id}。
以下 SOUL.md 是你的职责与边界的权威定义,必须严格遵守:

{soul}

## 运行环境
- 当前目录即工作区根目录;团队流程权威文件:agents/WORKFLOW.md、agents/workflow.yaml(需要时自行阅读相关章节)
- 当前项目目录:{proj_rel}/ —— 你的一切工作产物必须写入该目录下的对应子目录(布局见 WORKFLOW.md §2);目录不存在就创建
- 只做你 SOUL.md 职责内的事;越界的需求要说明应由哪个 Agent 负责,不要代劳
- 任务回执/评分/日志一律写 {proj_rel}/runs/<task_id>/(项目目录内);**严禁写工作区根 runs/**(文档中省略前缀的 runs/ 均指项目目录内)
- 发现设定冲突:记录到 {proj_rel}/qa/defects/,不要擅自改 bible/ 已确认内容
- 完成后:用{ui_lang}简要汇报做了什么、关键决策,并列出「创建/修改的文件路径」清单
- 一切面向用户的对话/汇报/进度说明一律使用 {ui_lang}(用户的界面语言设置);工作产物的内容语言不受此影响,仍按下方「输出语言」设定执行

## 用户时长设定(Web 客户端项目设置,当前项目实时生效,优先级高于文档中的示例值)
- 每集目标时长:{ep_minutes} 分钟(= {ep_seconds} 秒)—— 剧本分集(episode_plan 每集预算)、节奏(pacing)、剪辑(edit)一律以此为基准
- 单个分镜时长范围:{shot_min}–{shot_max} 秒 —— storyboard 的每镜时长建议与 shot-planning 的每镜终稿时长必须落在该区间
- 生成组(generation group)总时长上限:{sg_max} 秒(整数)—— storyboard 分组草案与 shot-planning 定稿的每组 Σ镜头时长必须 ≤{sg_max}s(项目「分镜组设置」,已由用户按所选视频模型的单次生成上限配置:Seedance 2.0 系列 15s、Seedance 2.5 30s;文档中出现的 15s 示例值一律以本设定为准,见 WORKFLOW.md §7A)
- 每组参考素材数量上限(项目「分镜组设置」,优先级高于文档示例值):参考图 ≤{sg_img} 张、参考视频 ≤{sg_vid} 个、参考音频 ≤{sg_aud} 段 —— prompt 组装与素材准备(refs/audio_refs/video_refs)不得超出该上限;模型侧硬限(Seedance 2.0:9图/3视频/3音频、参考音视频总时长各≤15s;Seedance 2.5:30图/10视频/10音频、总时长各≤30s)由 genmedia 提交前强制校验

## 用户输出设定(Web 客户端项目设置,当前项目实时生效,优先级高于文档示例与项目内旧规范)
- 输出画幅:{aspect}({aspect_name})—— 画幅规范(aspect_ratio.json)、分镜构图、关键帧、视频生成、剪辑成片一律按该画幅执行(生成时 genmedia 传 --aspect {aspect});发现项目内既有产物或规范与此冲突,新产出以本设定为准并在汇报中注明
- 输出语言:{out_lang} —— 剧本、台词、旁白、字幕、配音、成片文案、发布物料一律使用 {out_lang} 输出;仅提供给图像/音乐生成模型的英文 prompt 不受此限
- 视频生成 prompt 语言:提供给视频生成模型的 video_prompt **正文散文(镜头动作/画面/运镜描述等)用{ui_lang}书写,不必用英文**;但以下保持原样不翻译——结构锚点(`Overall visual style:`/`Shot N:`/`Global constraints:` 及 `[Image N]`/`[Audio N]`/`@Image N`/`@Audio N` 引用,机检与注释注入代码依赖这些英文锚点;素材指代只用这套英文锚点,禁写「图片N/音频N/视频N」等本地化变体)、上游逐字拼入的英文片段(style.json 风格串、space_fragment_en、prompt_fragment_en、visual_en、prompt_token、音效/环境声英文句)、固定英文约束句(Identity lock、非对白组静默句、Global constraints 负面清单)、台词(按剧本冻结版)
- 发布平台:{plat_list} —— Phase 11 发布(platform-adapter/seo/metadata/publisher)**仅面向这些平台**;aspect_ratio.json 平台矩阵、thumbnail 每平台封面、subtitle 每平台字幕以此清单为准。主生产画幅仍是上面的 {aspect}(母版按此原生生成){"" if not cross else f";与母版画幅不同的平台【{cross}】由 platform-adapter 在发布期从母版裁/补适配,不重新生成视频(现架构单母版)"}
- 内嵌字幕:{burn_in}
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
- 团队通用纪律对插件成员同等生效:工单格式(WORKFLOW.md §6)、运行记录四件套(§6.1)、质量三道闸与缺陷单(§7)、文件名 ASCII 红线(§1 原则 9)、Bible 冲突只上报不擅改"""
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
- 调度派单时须把本设定原文写入 title/edit 相关工单的 instruction"""
    if agent_id == "08-video-gen/prompt" and is_seedance25(active_video_model()):
        p += f"""

## Seedance 2.5 提示词优化 Skill(仅当生效视频模型为 Seedance 2.5 时注入,当前已生效)
当前项目的视频生成模型是 Seedance 2.5。撰写或优化组级 video_prompt 前,**先阅读官方提示词优化技能并按其方法执行**:
- Skill 文件:{SD25_PE_SKILL}(官方 sd25-pe,已随仓库安装,直接 Read 全文)
- 应用其中的:任务模板(文生视频/参考生视频/首尾帧/视频编辑/延长)、素材职责逐份映射与【未采用素材】清单、主体基数匹配、事件状态与因果保持、情绪表演/运镜/声音表达技法
- **优先级边界(冲突时以本团队规范为准)**:结构锚点(`Overall visual style:`/`Shot N:`/`Global constraints:`/`[Image N]`/`[Audio N]` 引用)、SOUL.md 机检清单、上游逐字拼入片段(风格串/光照 prompt_fragment_en/站位 space_fragment_en/道具 prompt_token)与冻结版台词一律保持不动——skill 用于提升散文表达质量、素材职责说明与模板化组织,不得以 skill 模板为由拆掉团队锚点结构
- skill 的「参数分离」原则与本仓库一致:画幅/时长/分辨率由 genmedia 命令行参数传递,不写进 prompt 正文"""
    if agent_id == "08-video-gen/prompt" and is_minimax_h3_active():
        p += f"""

## MiniMax H3 提示词写作 Skill(仅当生效视频渠道为 MiniMax H3 时注入,当前已生效)
当前项目的视频生成走 MiniMax H3(OpenRouter/MiniMax API 或 ComfyUI H3 工作流)。撰写或优化组级 video_prompt 前,**先阅读官方提示词写作技能并按其方法执行**:
- Skill 文件:{H3_PE_SKILL}(官方 h3-prompt-writing,已随仓库安装,直接 Read 全文,再按其指引读同目录 references/ 下对应模式的指南)
- 模式选择:带多参考图/参考音频的组级默认路径(--ref/--audio-ref)用 **Ref2VA 六段改写格式**(subject_definitions/summary/retention_analysis/detailed_description/overall_soundscape/non_diegetic_music,读 references/ref-en.txt);纯文本或首尾帧兜底路径用 **base 结构**(integrated_multimodal_description/overall_soundscape/non_diegetic_music,读 references/base-en.txt),按 T2VA/I2VA/FL2VA/L2VA 对号入座
- 参考标签纪律:skill 的 reference 标签体系与本团队 `[Image N]`/`[Audio N]` 序号约定(1-based,与 refs/audio_refs 数组顺序严格一致)必须同时满足——标签在各段间保持一致,严禁出现未定义/未解析的标签
- **优先级边界(冲突时以本团队规范为准)**:上游逐字拼入片段(风格串/光照 prompt_fragment_en/站位 space_fragment_en/道具 prompt_token)与冻结版台词一律原样保留;对白/歌词/画面内文字保持原语言,其余改写段用英文(与 skill 口径一致);SOUL.md 机检清单仍逐项过检
- skill 的「参数分离」原则与本仓库一致:画幅/时长/分辨率由 genmedia 命令行参数传递,不写进 prompt 正文;prompt 内时间标注须与工单组时长(Σ)吻合"""
    if agent_id == "08-video-gen/upscale" and is_minimax_upscale_available():
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
    if brief:
        p += f"""

## 用户设计构想(项目 {proj_rel}/brief.md,全片最高创作前提)
以下构想约束题材类型、叙事取舍等全部环节;其中「设计风格」一节(如有)是全片画面视觉风格的权威定义,
风格设定(style.json)、概念图、关键帧、视频生成等一切视觉产出及其 prompt 必须与之一致;
你的任何决策与构想冲突时,以构想为准或上报用户裁决:

{brief[:3000]}"""
    if agent_id.split("/")[0] in MEDIA_CATEGORIES:
        p += f"""

## 生成模型调用(环境已配置好)
图像/视频生成一律通过统一模块 modules/genmedia.py(渠道与模型已由用户在 Web 客户端配置,勿自行挑模型或直连各家 API):
- 查看当前渠道/模型:`python3 modules/genmedia.py info`(记入产物 meta,保证可复现)
- 生成图像:`python3 modules/genmedia.py image --prompt "<英文prompt>" --output <路径.png> [--negative "..."] [--aspect 16:9|--size 2560x1440] [--ref 参考图...] [--n 4] [--seed N]`
  (供视频参考的图——锚点/--ref/--first-frame/--last-frame——每张必须 ≥3,686,400 像素=火山硬限,16:9 用 2560x1440、9:16 用 1440x2560;小图提交即拒,严禁按视频草稿分辨率出小图)
- 生成视频(组级多镜头,默认路径):`python3 modules/genmedia.py video --prompt "<Shot 1:/Shot 2: 分镜结构>" --output <路径.mp4> --ref 锚点图... [--audio-ref 音色样本...] [--generate-audio on] [--return-last-frame tail.png] --duration <组Σ,4–{sg_max}整数> [--aspect 16:9] --resolution <草稿{draft_res}|成片{final_res}>`
- 生成视频(单镜首尾帧,兜底路径):`python3 modules/genmedia.py video --prompt "..." --output <路径.mp4> [--first-frame a.png] [--last-frame b.png] [--duration 4] [--aspect 16:9] --resolution <草稿{draft_res}|成片{final_res}>`(--ref 与首尾帧互斥)
- 生成音乐(BGM,仅音乐类工位):`python3 modules/genmedia.py music --prompt "<英文音乐描述:风格/情绪/乐器/节奏>" --output <路径.mp3> [--duration <秒>]`(渠道/模型由「🎨 生成模型」页音乐生成配置;OpenRouter:Lyria 3 Pro 完整歌曲、Lyria 3 Clip 30s 片段/Loop;ElevenLabs Eleven Music:--duration 3–600s 按 cue 精确出段;ComfyUI:ACE-Step 本地工作流、--duration 1–240s;默认纯音乐)
- TTS 旁白/音色样本(narrator/voice 类工位):`python3 modules/genmedia.py tts --text "<文本>" --output <路径.mp3> [--character CHAR-0001] [--variant child] [--voice <音色;仅云渠道>] [--speed 1.0] [--instructions "<语气/情绪指令>"]`(渠道/模型/默认音色由「🎨 生成模型」页 TTS语音模型配置,渠道可选 OpenRouter/火山豆包语音/ElevenLabs/ComfyUI。云渠道:**旁白不传 --voice**——自动用生效渠道配置的「默认音色」,即旁白声线,用户改设置即换声线;角色配音才按 casting 传 --voice 覆盖,语义随渠道:OpenRouter=音色名、火山=speaker 名、ElevenLabs=voice_id。ComfyUI:根据项目 voice/personality/appearance 从内置音色目录(远端 ComfyUI-Index-TTS/TimbreModel 音频库,首次使用自动下载缓存到 `data/TimbreModel/`)自动选参考音频,角色传 `--character`,旁白留空,禁止手填 `--voice`。instructions:OpenRouter 仅 OpenAI 系模型生效,火山注入情绪指令,ElevenLabs 忽略,ComfyUI 参与音色自动匹配、不注入合成)
- 详细纪律见 agents/WORKFLOW.md §9;生成失败如实上报,严禁伪造或占位产物

## 用户参考素材(视觉/配乐工作前必查)
用户通过 Web 客户端「参考文件」页把风格/角色/场景/道具/封面参考图、希望使用的音频与文本资料按分类上传到 {proj_rel}/refs/(style/ characters/ scenes/ props/ music/ thumbnail/ text/),并逐文件填写注释:
- **注释必读**:{proj_rel}/refs/NOTES.md(自动汇总用户逐图/逐曲注释,机器可读版 refs/annotations.json)说明每个文件管什么、想用在哪——有则必读并按注释执行
- 优先级:用户参考素材 > 你的自行发挥;与文字设定冲突时上报用户裁决,不擅自取舍
- 命中的参考图经 genmedia --ref 注入生成,并把所用路径记入产物 meta/prompts.json 的 user_refs 字段
- 配乐(09-audio/music)须先盘点 refs/music/,自行判断每首曲子适合用在视频的哪些位置并优先选用,选用/弃用情况写入 cue sheet(规则见 WORKFLOW.md §2 第 6 条)
- 目录为空则照常工作,不阻塞;详细约定见 agents/WORKFLOW.md §2"""
    if is_dispatcher_agent(agent_id):
        p += f"""

## 你的调度权(团队中仅调度型 Agent 拥有)
媒体工单配置认知:
- ComfyUI/IndexTTS2 的参考音频由 `modules/genmedia.py tts` 按内置音色目录 `modules/timbre_catalog.json`(索引远端 ComfyUI-Index-TTS/TimbreModel 音频库,首次使用自动下载缓存到 `data/TimbreModel/`)自动选择并上传,项目目录内没有 WAV/MP3 **不是阻塞条件**,不得要求用户手填默认参考音频
- voice-generation 工单必须调用 `genmedia.py tts --character <CHAR-ID> [--variant ...]`,narrator 工单不传 `--character`;两者均禁止传 `--voice`
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
   `python3 services/runtime/dispatch.py --wait-all <run_id...> --timeout 3600` 一次性等待全部完成
   (它会自动把等待进度实时上报到控制台,并在结束后打印每个子任务的结果摘要)。
   严禁自己写 sleep/轮询循环等待——那会让你的运行在界面上长时间无响应。
   等待类命令记得给 Bash 工具设置足够大的 timeout(如 3600000 毫秒)
3. 收到产物后做验收:检查文件存在、抽查内容是否达标;不达标就带着具体意见重新派单(最多 3 次)
4. 【重跑须先确认】每次准备让某个 Agent 重跑(返工/重新派单)之前,必须先征询用户:
   `python3 services/runtime/dispatch.py --confirm "任务<task_id>验收未过:<一句话原因>。是否重跑?" --timeout 60`
   该命令会阻塞直到用户在控制台点击「重跑」或「跳过」,60 秒无人答复则输出默认值「重跑」。
   命令输出「重跑」→ 正常重新派单;输出「跳过」→ 不再重跑,把该问题记入
   data/projects/<project>/qa/defects/ 并在最终汇报中说明跳过原因。首次派单不需要确认,只有重跑需要
5. 【人工签字点必须用 --sign】H1-H5 与每集 H3A 等人工签字闸门,必须用签字类确认:
   `python3 services/runtime/dispatch.py --confirm "【H1 <闸门名>】<要点与放行影响>" --sign`
   弹窗按钮为「签字/暂缓」,不倒计时、永不自动确认,保留到用户操作;命令默认最多等 4 小时。
   输出「签字」→ 闸门通过,走冻结流程;「暂缓」或「未签字」(等待超时)→ 记为等待人工,
   继续推进无依赖任务后正常结束运行。严禁把超时当签字通过,严禁用普通确认(60s 自动默认)代替签字
6. 你自己不做成员职责内的具体创作,你的产出是:任务拆解、派单、验收、向用户汇报进度与结果
7. 【blocker 挂起 ≠ 停机】某任务升级人工或等待裁决时,必须继续派发 DAG 上与它无依赖关系的
   其他可跑任务,禁止整条流水线待机干等(例:词典返工只应阻塞 merge,不应阻塞剧情理解/QA 预审)
8. 【赛马仅限用户明确指令,禁换引擎】严禁自行发起并行赛马(改派多个 Agent 并行重做同一任务、
   择优交付)。同一任务第 2 次返工仍未过,第 3 次必须走 --confirm 升级用户裁决;确有必要时可在
   --confirm 征询或升级说明中向用户**建议**赛马,只有用户明确下达赛马指令后,才可改派职责相近的
   Agent 并行重做、先达标者交付(赛马也不换引擎)。**任何情况下禁止切换执行引擎**(不得传 --engine 覆盖,
   不得因 GraphRecursionError/超时/API 5xx 等报错改用 claude/codex/kimi/pi/deepagents 中的另一个)。
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
- 插件任务同样走工单格式 §6、四件套 §6.1、评分与闸门 §7;人工签字点用 --sign,与 H1–H5 同规格
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
        "created", "started", "ended", "cost", "turns", "error",
        "engine", "model", "tokens", "progress") if k in run} | {
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


async def execute_run(run: dict, message: str, model: str | None):
    agent_id = run["agent"]
    # 调度型 Agent 要等整条流水线,超时放宽
    run_timeout = RUN_TIMEOUT * (4 if is_dispatcher_agent(agent_id) else 1)
    is_dispatcher = is_dispatcher_agent(agent_id)
    is_stateless = is_stateless_agent(agent_id)
    async with AsyncExitStack() as stack:
        # 调度器大部分时间在等子任务(--wait-all),不占工人槽;否则 8 个槽实际只剩 7 个干活
        if not is_dispatcher:
            await stack.enter_async_context(SEM)
        # 无状态服务型 agent 每次全新会话,同 agent 并发受「并发数量」额度约束;
        # 其余额度恒为 1(串行保护会话)。调度器不占槽但同样串行(同一总制片会话)
        limit = agent_concurrency() if is_stateless else 1
        await stack.enter_async_context(agent_sem(agent_id, limit))
        run["status"] = "running"
        run["started"] = time.time()
        publish_run(run)

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
        session_key = f"{engine}::{agent_id}::{run['project']}"
        # 记忆开关关闭时所有 agent 全新会话(session_id 仍照常回存,重新开启即恢复)
        session_id = (None if is_stateless or not agent_memory_enabled()
                      else STATE["sessions"].get(session_key))
        # 会话膨胀保险丝:历史过大时新开会话,避免 resume 每轮重发全史
        if session_id:
            resume_limit = (DEEPAGENTS_RESUME_LIMIT if engine == "deepagents"
                            else ORCHESTRATOR_RESUME_LIMIT if agent_id == ORCHESTRATOR_AGENT
                            else CHAT_RESUME_LIMIT)
            try:
                if chat_path(agent_id, run["project"]).stat().st_size > resume_limit:
                    session_id = None
                    run["session_reset"] = True
            except OSError:
                pass

        if engine == "deepagents":
            da = resolve_deepagents()
            requested_model = str(model or "").strip()
            # 兼容修复前已落盘/传入的渠道占位值，绝不把 local/openrouter 发给模型端点。
            use_model = (da["model"] if not requested_model
                         or requested_model == da["provider"] else requested_model)
            err = None
            if not use_model:
                err = ("deepagents 引擎未配置模型:请在 🎨 生成模型 页"
                       "「语言模型/DeepAgents」为生效渠道(本地模型/云端模型/OpenRouter)配置模型")
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
            # 有状态 agent 启用检查点(会话续接);记忆开关关闭时 session_id 为
            # None,runner 仍新开会话并回报 id,照常回存——与 claude/codex 语义
            # 一致(关闭期间照存,重新开启后从最近一次会话继续)
            if not is_stateless:
                da_cmd += ["--checkpoint-db", str(DEEPAGENTS_SESSIONS_DB)]

        def codex_stdin(sid: str | None) -> bytes | None:
            if engine != "codex":
                return None
            prompt = message if sid else f"{role}\n\n---\n\n## 当前工作指令\n\n{message}"
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
                    return base + ["-r", sid, "-p", message]
                return base + ["-p", f"{role}\n\n---\n\n## 当前工作指令\n\n{message}"]
            if engine == "pi":
                # pi 原生 JSON 事件流与持久会话。系统提示通过文件传入，既避免
                # Windows 命令行长度限制，也让续接进程每轮恢复同一 Agent 身份。
                base = [cli_executable, "--mode", "json", "--approve",
                        "--append-system-prompt", str(system_prompt_file)]
                if model:
                    base += ["--model", model]
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
                  "--max-turns", MAX_TURNS]
            if model:
                c += ["--model", model]
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
        if engine == "deepagents":   # 长文本走环境变量,避免超长 argv
            env["DA_SYSTEM"] = role
            env["DA_PROMPT"] = message
            # LangGraph recursion_limit 默认过低时,多工具任务会稳定 GraphRecursionError。
            # 用户已设 DEEPAGENTS_RECURSION_LIMIT 时尊重;否则工人 250 / 调度器 500。
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
                while True:
                    if time.time() > deadline:
                        raise TimeoutError(f"运行超过 {run_timeout}s")
                    raw = await asyncio.wait_for(read_jsonl_line(proc.stdout),
                                                 timeout=max(1, deadline - time.time()))
                    if not raw:
                        break
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
                    elif engine == "deepagents":
                        handle_deepagents_event(run, obj)
                    else:
                        handle_claude_event(run, obj)
                await proc.wait()
                stderr = (await stderr_task).decode("utf-8", "replace").strip()
                failed = ((proc.returncode != 0 and not run.get("result"))
                          or (engine == "pi" and bool(run.get("error"))))
                if failed:
                    # 会话失效回退:记录的会话已被引擎清理(换机/清缓存/引擎升级)时,
                    # resume 必然失败且下轮还会用同一失效 id;清掉记录换全新会话重试一次
                    if (session_id and not session_retried and re.search(
                            r"no conversation found|session[^\n]{0,80}not found"
                            r"|thread[^\n]{0,40}not found",
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
        except (TimeoutError, asyncio.TimeoutError):
            run["status"] = "error"
            run["error"] = f"超时({run_timeout}s),进程已终止"
            if proc:
                proc.kill()
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
            # 会话续用:记录本次会话 id(无状态服务型 agent 不留会话)
            if run.get("session_id") and not is_stateless:
                STATE["sessions"][session_key] = run["session_id"]
                save_state(STATE)
            reply = run.get("result") or run.get("text") or run.get("error") or "(无输出)"
            append_chat(agent_id, run["project"],
                        {"role": "assistant", "text": reply,
                         "run_id": run["id"], "status": run["status"]})
            publish_run(run)
            # Agent 结束前即使漏掉 dispatch.py --confirm --sign，也不能让已解锁的
            # 人工闸门静默留在 DAG 中。这里只补建签字单，绝不自动改 gate state。
            try:
                await ensure_human_gate_approvals(run["project"], parent=run["id"])
            except Exception as e:  # noqa: BLE001
                print(f"[approval] 运行结束闸门核对失败(不影响运行回执):{e}", flush=True)


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


MAX_SKETCH_REFS = 9   # 方舟多参考图上限(Seedance 2.0 口径;项目可经「分镜组设置」调整)


def max_group_ref_images(project: str) -> int:
    """每组参考图数量上限:项目「分镜组设置」max_ref_images(Seedance 2.5 最高 30),
    读不到回落 MAX_SKETCH_REFS=9(Seedance 2.0 口径)。"""
    try:
        sg = load_project_settings(project).get("shot_group") or {}
        return max(0, min(30, int(sg.get("max_ref_images", MAX_SKETCH_REFS))))
    except Exception:
        return MAX_SKETCH_REFS


SKETCHGEN_JOBS: dict[str, dict] = {}   # "project/ep/grp" -> 手绘生成任务状态(单机内存态)
SKETCHGEN_PROMPT_TMPL = (
    "Image 1 is a rough black-and-white hand-drawn layout sketch by the director. "
    "Generate one polished final frame that follows the sketch's spatial composition "
    "(subject placement, relative positions, framing) exactly; use the sketch ONLY for "
    "layout, never for art style or rendering. The remaining reference images are the "
    "project's official character/scene/prop designs — keep their identity, appearance "
    "and outfits strictly consistent.")


def _sketchgen_size(aspect: str) -> str:
    """按项目画幅算成图尺寸:成图会随重出作为视频参考图,须满足火山视频输入图
    最小像素 3,686,400(16:9 → 2560x1440);边长向上取 8 的倍数。"""
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
    ref_cap = max_group_ref_images(project)
    if len(pd.get("refs") or []) >= ref_cap:
        raise ServiceError(400, f"This group already has the maximum of {ref_cap} refs; cannot add more")
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
                      "assets/concepts/scenes/",
                      "assets/concepts/props/")


def _grpref_append(pf: Path, ref: str, src: str) -> int:
    """向组 prompt 的 refs 追加一张参考图(上限=项目「分镜组设置」max_ref_images,
    回落 MAX_SKETCH_REFS=9 的方舟 Seedance 2.0 口径);返回追加后的 refs 数量。"""
    d = json.loads(pf.read_text())
    refs = d.setdefault("refs", [])
    if ref in refs:
        raise ServiceError(400, "This image is already in the group's refs")
    # pf = <project>/assets/prompts/<ep>/<grp>.json → parents[3] 即项目根
    ref_cap = max_group_ref_images(pf.parents[3].name)
    if len(refs) >= ref_cap:
        raise ServiceError(400, f"This group already has the maximum of {ref_cap} refs; cannot add more")
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
        raise ServiceError(400, "ref must be an image under assets/concepts/(characters|scenes|props)/")
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


async def api_grpref_delete(body: dict):
    """从组 refs 移除一张用户手动加入的参考图(重出本组生效)。
    仅限用户加入的 ref,且其后不得残留非用户加入的 ref——线稿注入句按 [Image N]
    序号引用 refs 位置,删中间会错位(与线稿删除的末位约束同理);
    本地上传的图一并删除落盘文件。"""
    project, ep, grp, base, pf = _grpref_ctx(body)
    ref = (body.get("ref") or "").strip().lstrip("/")
    d = json.loads(pf.read_text())
    refs = d.get("refs") or []
    if ref not in refs:
        raise ServiceError(404, f"Ref not in this group's refs: {ref}")
    if not _grpref_user_added(d, ref):
        raise ServiceError(400, "Only user-added refs (asset pick / local upload) can be removed here; delete sketches via the group card instead")
    i = refs.index(ref)
    if not all(_grpref_user_added(d, r) for r in refs[i + 1:]):
        raise ServiceError(400, "A pipeline/sketch ref comes after this one; delete that first (to keep [Image N] numbering aligned)")
    refs.pop(i)
    d.setdefault("notes", []).append(
        f"用户移除组参考图:{ref}(原 refs 第 {i + 1} 张);重出本组时生效。由 storyboard service 自动补丁。")
    atomic_write_json(pf, d)
    if ref.startswith(f"assets/uploads/{ep}/{grp}/"):
        p = (base / ref).resolve()
        try:
            p.relative_to(base.resolve())
        except ValueError:
            p = None
        if p:
            p.unlink(missing_ok=True)
    return {"deleted": ref, "refs": len(refs)}


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
    chars = []
    for cid in sorted(ids):
        docs = {}
        if (bdir / cid).is_dir():
            for f in sorted((bdir / cid).glob("*.json")):
                docs[f.stem] = _read_json_safe(f)
        meta = info.get(cid) or {}
        chars.append({"id": cid, "name": meta.get("canonical_name") or cid,
                      "meta": meta, "docs": docs,
                      "voices": voices.get(cid, []),
                      "images": _asset_urls(base, adir / cid, IMG_EXTS)})
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

    def _shot_draft(s: dict) -> dict:
        m = re.match(r"^(.+?)/order:(\d+)$", s.get("storyboard_ref") or "")
        return (drafts.get((m.group(1), int(m.group(2)))) if m else None) or {}

    def _shot_content(s: dict) -> str:
        if s.get("content"):
            return s["content"]
        dr = _shot_draft(s)
        return dr.get("content") or dr.get("subject_action") or ""

    kroot = base / "assets" / "keyframes" / ep
    croot = base / "assets" / "clips" / ep
    clips = _asset_urls(base, croot, VIDEO_EXTS)
    shots = []
    for s in (sl.get("shots") or []):
        if not isinstance(s, dict):
            continue
        sid = s.get("shot_id") or ""
        shots.append({k: s.get(k) for k in (
            "shot_id", "scene_no", "scene_id", "duration_s", "size",
            "camera_position", "characters", "is_dialogue", "dialogue_ref",
            "beat")} | {
            "scene_no": s.get("scene_no") or s.get("scene_id"),
            "dialogue_ref": s.get("dialogue_ref") or _shot_draft(s).get("dialogue_ref"),
            "content": _shot_content(s),
            "keyframes": _asset_urls(base, _id_dir(kroot, sid), IMG_EXTS),
            "clips": [c for c in clips
                      if sid and _id_name_match(sid, c["name"], any_segment=True)],
        })
    data["shots"] = shots
    # 生成组(WORKFLOW.md §7A):组锚点包 keyframes/<grp>/、组视频 clips/<grp>.mp4、
    # 切变边界与尾帧来自 clips/<grp>.meta.json
    groups = []
    for g in (sl.get("generation_groups") or []):
        if not isinstance(g, dict):
            continue
        gid = g.get("group_id") or ""
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
        for r in (pd.get("refs") or []):
            f = base / r
            if not f.is_file():
                continue
            url = f"/projects/{base.name}/{r}?v={int(f.stat().st_mtime)}"
            if _grpref_user_added(pd, r):
                user_refs.append({"ref": r, "name": f.name, "url": url})
            elif (not r.startswith(kf_prefix)
                  and f.stat().st_size not in anchor_sizes):
                # 概念图文件名易撞名(如多个 three-quarter.png),取末两段路径作显示名
                pipeline_refs.append({"ref": r, "url": url,
                                      "name": "/".join(r.split("/")[-2:])})
        groups.append({k: g.get(k) for k in (
            "group_id", "scene_id", "shots", "total_duration_s",
            "characters_union", "has_dialogue", "continuity_from")} | {
            "anchors": _asset_urls(base, _id_dir(kroot, gid), IMG_EXTS),
            "user_refs": user_refs,
            "pipeline_refs": pipeline_refs,
            "clips": [c for c in clips if gid and _id_name_match(gid, c["name"])],
            "boundaries_s": meta.get("boundaries_s") or [],
            "sketches": _sketch_list(base.name, ep, gid),
            "user_note": _grpnote_get(base.name, ep, gid).get("text", ""),
        })
    data["generation_groups"] = groups
    # 配乐 cue:bgm/<ep>/cue_sheet.json → 预览页按 beat_ref/scene 对位试听
    bdir = base / "assets" / "audio" / "bgm" / ep
    cs = _read_json_safe(bdir / "cue_sheet.json") or {}
    data["bgm_cues"] = [
        {k: c.get(k) for k in ("cue_id", "in_s", "out_s", "scene",
                               "beat_ref", "mood", "loop_fill")} | {
            "audio": (_audio_url(base, bdir / c["file"])
                      if isinstance(c.get("file"), str) else None)}
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
    claude stream-json 末尾 result.usage(整个 run 的累计);codex exec 末尾
    turn.completed.usage;pi 的每个 assistant message_end 各带一次调用用量;
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
    口径 = 输入(含缓存写/读)+ 输出 的总和,含全部引擎(claude/codex/kimi/pi/deepagents)。
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


async def api_genconfig_set(body: dict):
    body = dict(body or {})
    # project 仅用于「设置变更」通知的会话归属(genconfig 本身是全局配置),不落盘
    project = safe_slug(body.pop("project", None))
    old = load_genconfig()
    cfg = _merge(load_genconfig(), body)
    for kind in ("image", "video", "music", "tts", "deepagents"):
        allowed = set(DEFAULT_GENCONFIG[kind]) - {"provider"}
        if cfg.get(kind, {}).get("provider") not in allowed:
            raise ServiceError(400, f"{kind}.provider must be one of {sorted(allowed)}")
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
    await _notify_settings_change(project, "界面语言" if lang_only else "生成模型",
                                  _flat_diff(old, cfg))
    return {"ok": True, "config": cfg}


BRIEF_HEADER = "# 主创构想"
BRIEF_SECTION = "## 主要构想"
STYLE_SECTION = "## 设计风格"


def parse_brief(text: str) -> tuple:
    """brief.md 正文 → (主要构想, 设计风格)。兼容旧格式(无小节标题=全文即主要构想)。"""
    text = (text or "").strip()
    if text.startswith(BRIEF_HEADER):
        text = text[len(BRIEF_HEADER):].strip()
    style = ""
    if STYLE_SECTION in text:
        text, style = text.split(STYLE_SECTION, 1)
        style = style.strip()
    brief = text.strip()
    if brief.startswith(BRIEF_SECTION):
        brief = brief[len(BRIEF_SECTION):].strip()
    return brief, style


def format_brief(brief: str, style: str) -> str:
    """(主要构想, 设计风格) → brief.md 全文;两者皆空返回 ''(表示应删除文件)。"""
    parts = [BRIEF_HEADER]
    if brief:
        parts.append(f"{BRIEF_SECTION}\n\n{brief}")
    if style:
        parts.append(f"{STYLE_SECTION}\n\n{style}")
    return "\n\n".join(parts) + "\n" if len(parts) > 1 else ""


async def api_brief_get(project: str = "demo"):
    """当前项目的设计构想:brief.md 的主要构想 + 设计风格两个字段。"""
    p = PROJECTS_DIR / safe_slug(project) / "brief.md"
    brief, style = parse_brief(p.read_text() if p.is_file() else "")
    return {"project": project, "brief": brief, "style": style}


async def api_brief_set(body: dict):
    """保存设计构想(主要构想+设计风格)到 data/projects/<项目>/brief.md;两者皆清空即移除该设定。"""
    project = safe_slug(body.get("project"))
    if not (PROJECTS_DIR / project).is_dir():
        raise ServiceError(404, f"Project not found: {project}")
    brief = str(body.get("brief") or "").strip()
    style = str(body.get("style") or "").strip()
    p = PROJECTS_DIR / project / "brief.md"
    old = p.read_text().strip() if p.is_file() else ""
    text = format_brief(brief, style)
    if text:
        p.write_text(text)
    elif p.is_file():
        p.unlink()
    new = p.read_text().strip() if p.is_file() else ""
    if new != old:
        await _notify_settings_change(project, "设计构想", [
            f"brief.md 已更新,最新全文:\n{new[:1500]}" if new
            else "brief.md 已清空(移除主创构想与设计风格设定)"])
    return {"ok": True, "project": project, "brief": brief, "style": style}


# ---------------- 参考文件页(refs/ 分类预览、上传、逐文件注释) ----------------
REF_CATEGORIES = ("style", "characters", "scenes", "props", "music", "thumbnail", "text")
REF_SKIP_FILES = {"README.md", "NOTES.md", "annotations.json"}
AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg")
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
                       "shot_group": "分镜组设置",
                       "review": "审核设置", "packaging": "片头片尾",
                       "versioning": "版本管理"}


async def api_projconfig_set(body: dict):
    project = safe_slug(body.get("project"))
    old = load_project_settings(project)
    cfg = _merge(load_project_settings(project),
                 {k: v for k, v in (body or {}).items()
                  if k in PROJECT_SETTINGS_KEYS})
    _validate_duration(cfg.get("duration") or {})
    _validate_shot_group(cfg.get("shot_group") or {})
    _validate_output(cfg.get("output") or {})
    _validate_review(cfg.get("review") or {})
    _validate_packaging(cfg.get("packaging") or {})
    _validate_versioning(cfg.get("versioning") or {})
    ensure_project(project)
    project_settings_path(project).write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2))
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
    key = (body.get("api_key") or "").strip()
    if not key:
        raise ServiceError(400, "api_key must not be empty")
    try:
        data = await asyncio.to_thread(
            _http_get_json, "https://openrouter.ai/api/v1/key",
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


async def api_test_comfyui(body: dict):
    """测试 ComfyUI 连接(本地/Comfy Cloud/RunningHub),并检查关键自定义节点是否可见。

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
    for node_type in ("ACEModelLoader", "ACEStepGen", "MiniMaxH3ReferenceToVideo"):
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


COMFY_WORKFLOW_KINDS = ("image", "video", "music", "tts")


async def api_comfy_workflows():
    """列出 comfy/ 目录中按文件名前缀分类的 API 工作流 JSON(image-/video-/music-/tts-),
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
    return {"ok": True, "id": wf_id, "tokens": tokens,
            "node_count": len(workflow) if isinstance(workflow, dict) else 0,
            "minimax_h3": h3}


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
    return {"mode": mode,
            "defaults": {a["id"]: default_agent_model(a["id"], mode)
                         for a in list_agents()},
            "overrides": load_agentmodels()}


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
    if eng == "deepagents" and (not model or model in ("local", "openrouter")):
        model = resolve_deepagents()["model"]
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
                                 or effective_model in ("local", "openrouter")):
        effective_model = resolve_deepagents()["model"]
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


async def api_soul(agent: str):
    d = agent_dir(safe_agent(agent))
    if not d:
        raise ServiceError(404, "no such agent")
    return {"agent": agent, "soul": (d / "SOUL.md").read_text()}


async def api_enginecheck(engine: str):
    """检测执行引擎 CLI 是否已安装(顶栏切换 claude/codex/kimi/pi 时前端调用)。
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
    # 新建项目的审核力度默认全 0(不审核),向导/调用方显式给值则覆盖
    settings = body.get("settings") or {}
    base = {k: DEFAULT_GENCONFIG[k] for k in PROJECT_SETTINGS_KEYS}
    base["review"] = {"evaluation": 60, **{k: 0 for k in REVIEW_DIMENSIONS}}
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
    if novel:
        nd = PROJECTS_DIR / name / "novel"
        nd.mkdir(parents=True, exist_ok=True)
        (nd / "original.txt").write_text(novel)
    brief_text = format_brief(brief, style)
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
            "3) 用户设计构想(主要构想 + 设计风格)已写入 brief.md(系统会把它自动注入团队每个成员的系统提示词,"
            "是后续全部工作的最高创作前提,其中设计风格约束全部画面视觉产出):"
            "请通读,并在汇报中简要复述你的理解以便用户纠偏")
    if cfg is not None:
        aspect, aspect_name, lang = resolve_output(cfg)
        dur = cfg["duration"]
        msg.append(
            f"另:用户已在新建向导完成项目初始设置并写入 settings.json——输出画幅 {aspect}({aspect_name})、"
            f"输出语言 {lang}、每集约 {dur['episode_minutes']} 分钟、各维度审核力度与片头片尾开关等,"
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
    kind=confirm(默认,重跑类):至多 60s 后自动落默认答案;
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
         min(60, max(5, int(body.get("timeout") or 60))),
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
    return {"confirm_id": c["id"]}


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


def _stop_run(run) -> bool:
    """停止单个 run:running 杀进程组,queued 取消排队;返回是否执行了停止。"""
    if run.get("status") == "running":
        run["error"] = "已被用户手动停止"
        proc = RUN_PROCS.get(run["id"])
        if proc and proc.returncode is None:
            _kill_proc_tree(proc)      # execute_run 读到 EOF 后按 error 收尾
        return True
    if run.get("status") == "queued":
        t = RUN_TASKS.pop(run["id"], None)
        if t:
            t.cancel()
        run["status"] = "error"
        run["error"] = "已被用户手动停止(排队中取消)"
        run["ended"] = time.time()
        append_chat(run["agent"], run["project"],
                    {"role": "assistant", "text": run["error"],
                     "run_id": run["id"], "status": "error"})
        publish_run(run)
        return True
    return False


async def api_stop_all():
    """停止全部排队/运行中的任务(运行面板「⏹ 停止」按钮)。"""
    stopped = [run["id"] for run in list(RUNS.values()) if _stop_run(run)]
    return {"stopped": stopped}


async def shutdown_runtime(timeout: float = 4) -> None:
    """Boundedly stop Agent tasks/process groups and release OS resources."""
    tasks = list(RUN_TASKS.values())
    await api_stop_all()

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
        return None
    proj = str(c["project"])
    checkpoint = c.get("checkpoint") or c["gate_id"]
    message = (
        f"[人工签字回执] 用户已在永久签字单 {c['id']} 对项目 {proj} 的 "
        f"{checkpoint}(DAG 节点 {c['gate_id']})明确选择「签字」。"
        "这是人工签字证据，不是自动放行。请立即复核该闸门的缺陷清零、到期缺陷、"
        "QA hold 与人工检查项；满足后写入完整 gate JSON、更新 DAG 并派 version 冻结。"
        "若机检不满足则保持 HOLD 并向用户说明，禁止重复发起同一签字。"
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


async def api_agent_concurrency_get():
    return {"agent_concurrency": agent_concurrency(),
            "default": AGENT_CONCURRENCY_DEFAULT,
            "max": MAX_CONCURRENT}


async def api_agent_concurrency_set(body: dict):
    """并发数量设置(设置菜单「高级→并发数量」):无状态扇出型 Agent(05-scenes/
    08-video-gen/11-qa/eval 等)的同 agent 并发额度;有状态 Agent 恒为 1,
    全局仍受 MAX_CONCURRENT 总闸。持久化,立即对后续排队的运行生效。"""
    try:
        n = int(body.get("agent_concurrency"))
    except (TypeError, ValueError):
        raise ServiceError(400, "agent_concurrency must be an integer") from None
    if not 1 <= n <= MAX_CONCURRENT:
        raise ServiceError(400, f"agent_concurrency must be between 1 and {MAX_CONCURRENT}")
    STATE["agent_concurrency"] = n
    save_state(STATE)
    return await api_agent_concurrency_get()


async def api_agent_memory_get():
    return {"agent_memory": agent_memory_enabled(),
            "resume_limit_kb": CHAT_RESUME_LIMIT // 1024}


async def api_agent_memory_set(body: dict):
    """Agent记忆设置(设置菜单「高级→Agent记忆」):开启=有状态 Agent 恢复上次
    会话(128KB 保险丝仍生效);关闭=所有 Agent 每次运行全新会话。持久化,
    立即对后续运行生效;关闭期间会话号照常回存,重新开启后从最近一次会话继续。"""
    if body.get("agent_memory") is None:
        raise ServiceError(400, "agent_memory must be a boolean")
    STATE["agent_memory"] = bool(body["agent_memory"])
    save_state(STATE)
    return await api_agent_memory_get()


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
    return {"claude_probe": bool(cfg.get("claude_probe")),
            "claude_probe_env": CLAUDE_USAGE_PROBE_ENABLED,
            "codex_probe": codex_probe_enabled(),
            "kimi_probe": kimi_probe_enabled(),
            "kimi_api_key": cfg.get("kimi_api_key") or "",
            "openrouter_key": cfg.get("openrouter_key") or "",
            "volc_enabled": bool(cfg.get("volc_enabled")),
            "volc_ak": cfg.get("volc_ak") or "",
            "volc_sk": cfg.get("volc_sk") or ""}


async def api_resources_config_set(body: dict):
    """保存资源消耗设置:只更新给出的字段;保存后清用量/余额缓存立即生效。"""
    cfg = STATE.setdefault("resources", {})
    for k in ("claude_probe", "codex_probe", "kimi_probe", "volc_enabled"):
        if body.get(k) is not None:
            cfg[k] = bool(body[k])
    for k in ("kimi_api_key", "openrouter_key", "volc_ak", "volc_sk"):
        if body.get(k) is not None:
            if not isinstance(body[k], str) or len(body[k]) > 500:
                raise ServiceError(400, f"{k} must be a string (≤500 chars)")
            cfg[k] = body[k].strip()
    save_state(STATE)
    _USAGE_CACHE.clear()
    _BALANCE_CACHE.clear()
    return await api_resources_config_get()


async def api_resources(fresh: bool = False):
    """资源消耗面板聚合数据:三引擎 session/weekly 用量与重置时间 + 已配置渠道的账户余额。
    只探测已开启用量检查的引擎,全部探测并发跑(各自带 TTL 缓存,fresh=1 清缓存
    强制重测);取不到的读数为 null。"""
    if fresh:
        _USAGE_CACHE.clear()
        _BALANCE_CACHE.clear()
    empty = {"session": None, "weekly": None,
             "session_resets_at": None, "weekly_resets_at": None}

    async def usage(engine, on):
        return await asyncio.to_thread(engine_usage_full, engine) if on else dict(empty)
    claude_on, codex_on, kimi_on = (
        claude_probe_enabled(), codex_probe_enabled(), kimi_probe_enabled())
    cu, co, ki, orb, vb = await asyncio.gather(
        usage("claude", claude_on),
        usage("codex", codex_on),
        usage("kimi", kimi_on),
        asyncio.to_thread(provider_balance, "openrouter"),
        asyncio.to_thread(provider_balance, "volc"))
    cfg = resource_cfg()
    return {"claude": {**cu, "enabled": claude_on},
            "codex": {**co, "enabled": codex_on},
            "kimi": {**ki, "enabled": kimi_on},
            "openrouter": {"configured": bool((cfg.get("openrouter_key") or "").strip()),
                           **(orb or {})},
            "volc": {"configured": bool(cfg.get("volc_enabled")), **(vb or {})}}


async def api_watchdog_get(project: str = ""):
    """按项目查看空转看门狗开关(缺省关闭)与常任指令。不带 project 时返回全部映射。"""
    wd = STATE.get("watchdog", {})
    if project:
        proj = safe_slug(project)
        return {"project": proj, "enabled": bool(wd.get(proj, False)),
                "orders": str(STATE.get("watchdog_orders", {}).get(proj) or "")}
    return {"watchdog": wd}


async def api_watchdog_set(body: dict):
    """按项目设置空转看门狗:enabled=开关(运行面板 🤖 按钮/飞书 /auto),
    orders=常任指令(设置菜单「自动运行」,随每条唤醒消息附带,空串清除)。
    两项均可选,只更新给出的项;逐项目独立,状态持久化,重启后保持。"""
    proj = safe_slug(body.get("project"))
    if body.get("enabled") is not None:
        STATE.setdefault("watchdog", {})[proj] = bool(body.get("enabled"))
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

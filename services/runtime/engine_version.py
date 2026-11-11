"""执行引擎 CLI 版本检查 + 一键更新(2026-11-11)。

顶栏切到 claude/codex/kimi/pi/opencode/grok/antigravity(或开屏恢复的引擎)且已装好、已登录时,
前端问 GET /engines/<e>/version:

- 本机版本 = `<cli> --version`(每次现测,各家 0.1–0.7 秒;不缓存——Windows 上 npm 的 .cmd 垫片
  更新后不变,按文件签名缓存会读到旧版本)。
- 最新版本 = 各家官方安装脚本 / 自更新命令所用的发布地址(2026-11-11 逐个下载脚本核对),
  按引擎(+ 渠道 / 主版本)缓存 6 小时,查询失败 15 分钟内不重试;查不到就不提示,不打扰用户。
  grok 直接用 CLI 自带的 `grok update --check --json`(认得用户选的 stable / alpha 渠道)。
  claude 按 settings.json 的 autoUpdatesChannel(latest / stable)取对应渠道。

一键更新 = 后台跑该 CLI **自带**的更新命令(claude update / codex update / kimi upgrade --yes /
pi update --self / opencode upgrade / grok update / agy update,它们自己认得安装方式:原生安装、
npm、Homebrew …),任务机制与一键安装共用 engine_setup.start_job(无 tty、剔除密钥、按无进展判超时、
日志实时回传);跑完重测版本号,没变按失败报(如 Homebrew 装的会提示改用 brew upgrade,日志里可见)。

Windows 与 macOS / Linux 的差别:
- Windows 上运行中的 .exe 被系统锁住、不能覆盖。npm 装的 CLI(npm 全局目录里的 .cmd 垫片)让它自我
  更新时,正在跑的自身文件(codex / opencode 的原生二进制)被占用,npm 换不掉 → 改为直接跑
  `npm install -g <包>@latest`;有任务正在用该引擎时也拒绝更新(API 回 409 busy),等任务结束。
- macOS / Linux 替换文件不影响已在运行的进程(旧 inode 照常可用):允许更新,在跑的任务用旧版跑完,
  之后的新任务用新版。
"""
from __future__ import annotations

import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable

from services.runtime import engine_setup

IS_WIN = os.name == "nt"
PLATFORM = "windows" if IS_WIN else "macos" if sys.platform == "darwin" else "linux"
# Windows:运行中的 .exe 不能覆盖,有任务在用该引擎时拒绝一键更新(macOS / Linux 允许)
BUSY_BLOCKS = IS_WIN

# 各 CLI 自带的更新命令(接在可执行文件后)。kimi 不加 --yes 会停在确认提问;
# pi 只更新自身(--self),不动用户装的扩展
UPDATE_ARGS: dict[str, tuple[str, ...]] = {
    "claude": ("update",),
    "codex": ("update",),
    "kimi": ("upgrade", "--yes"),
    "pi": ("update", "--self"),
    "opencode": ("upgrade",),
    "grok": ("update",),
    "antigravity": ("update",),
}
UPDATE_ENV: dict[str, dict[str, str]] = {"codex": {"CODEX_NON_INTERACTIVE": "1"}}
# npm 全局安装时的包名(Windows 上 npm 装的改为直接 npm install -g;opencode 1.x 是 opencode-ai)
NPM_PACKAGES: dict[str, str] = {
    "claude": "@anthropic-ai/claude-code",
    "codex": "@openai/codex",
    "pi": "@earendil-works/pi-coding-agent",
    "opencode": "@opencode/cli",
}

LATEST_TTL_S = 6 * 3600
LATEST_FAIL_TTL_S = 15 * 60
HTTP_TIMEOUT_S = 10
VERSION_TIMEOUT_S = 20
UPDATE_TIMEOUT_S = 3600
_UA = {"User-Agent": "VideoAgents-engine-version-check", "Accept": "*/*"}

_VER = re.compile(r"(?<![\w.])v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z][0-9A-Za-z.-]*))?")


def job_key(engine: str) -> str:
    return f"update:{engine}"


# ---------------------------------------------------------------- 版本号
def parse_version(text: str | None) -> str:
    """从 `--version` 输出里取第一个 x.y.z[-pre]:
    "2.1.322 (Claude Code)" / "codex-cli 0.166.0" / "opencode v2.1.2" / "grok 1.0.77 (50ca579f)"。"""
    m = _VER.search(text or "")
    if not m:
        return ""
    v = ".".join(m.group(i) for i in (1, 2, 3))
    return f"{v}-{m.group(4)}" if m.group(4) else v


def version_key(v: str) -> tuple:
    """semver 比较键:预发布版低于同号正式版;预发布段数字 < 字母。解析不了返回 ()。"""
    m = _VER.fullmatch((v or "").strip())
    if not m:
        return ()
    core = tuple(int(m.group(i)) for i in (1, 2, 3))
    pre = m.group(4)
    if not pre:
        return core + ((1,),)
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return core + ((0,) + parts,)


def is_outdated(installed: str, latest: str) -> bool:
    a, b = version_key(installed), version_key(latest)
    if not a or not b or a[:3] == (0, 0, 0):   # 0.0.0-xxx 是开发 / 预览构建,比不了,不提示
        return False
    return a < b


def _run(argv: list[str], timeout: float = VERSION_TIMEOUT_S, env: dict | None = None
         ) -> subprocess.CompletedProcess | None:
    kw: dict = {}
    if IS_WIN:
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              env={**(env or os.environ), "NO_COLOR": "1"},
                              cwd=str(engine_setup.home_dir()), **kw)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def installed_version(exe: str | None) -> str:
    if not exe:
        return ""
    r = _run([exe, "--version"])
    if r is None:
        return ""
    return parse_version(r.stdout) or parse_version(r.stderr)


# ---------------------------------------------------------------- 最新版本
def _http_text(url: str) -> str:
    # 走系统 / 环境代理(各家发布地址在部分地区要经代理才连得上);只发通用 UA,不带任何用户信息
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
        return r.read(4_000_000).decode("utf-8", "replace")


def _http_json(url: str):
    return json.loads(_http_text(url))


def _npm_latest(pkg: str) -> str:
    return str(_http_json(f"https://registry.npmjs.org/{pkg}/latest").get("version") or "")


def claude_channel() -> str:
    base = (Path(os.environ["CLAUDE_CONFIG_DIR"]) if os.environ.get("CLAUDE_CONFIG_DIR")
            else engine_setup.home_dir() / ".claude")
    try:
        d = json.loads((base / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = None
    ch = d.get("autoUpdatesChannel") if isinstance(d, dict) else None
    return ch if ch in ("latest", "stable") else "latest"


def antigravity_platform() -> str:
    m = platform.machine().lower()
    arch = "arm64" if m in ("arm64", "aarch64") else "amd64"
    os_name = "windows" if IS_WIN else "darwin" if sys.platform == "darwin" else "linux"
    return f"{os_name}_{arch}"


def _grok_check(exe: str) -> tuple[str, str]:
    r = _run([exe, "update", "--check", "--json"], timeout=30)
    d = None
    if r is not None and r.stdout.strip():
        try:
            d = json.loads(r.stdout.strip().splitlines()[-1])
        except ValueError:
            d = None
    if isinstance(d, dict) and d.get("latestVersion"):
        return str(d["latestVersion"]), str(d.get("channel") or "stable")
    raise RuntimeError("grok update --check failed")


def _sources(engine: str, exe: str, installed: str) -> tuple[str, list[Callable[[], str]]]:
    """(缓存用的变体名, 依次尝试的取数函数)。变体:claude 的渠道、opencode 的主版本。"""
    if engine == "claude":
        ch = claude_channel()
        return ch, [lambda: _http_text(f"https://downloads.claude.ai/claude-code-releases/{ch}"),
                    lambda: str(_http_json("https://registry.npmjs.org/-/package/@anthropic-ai/claude-code/dist-tags")
                                .get(ch) or "")]
    if engine == "codex":
        return "latest", [lambda: str(_http_json("https://releases.openai.com/codex/channels/latest")
                                      .get("tag_name") or "").removeprefix("rust-v"),
                          lambda: _npm_latest(NPM_PACKAGES["codex"])]
    if engine == "kimi":
        return "latest", [lambda: _http_text("https://code.kimi.com/kimi-code/latest")]
    if engine == "pi":
        return "latest", [lambda: str(_http_json("https://pi.dev/api/installer/releases/latest")
                                      .get("version") or ""),
                          lambda: _npm_latest(NPM_PACKAGES["pi"])]
    if engine == "opencode":
        if installed.startswith("1."):   # 1.x 线(npm 包 opencode-ai);2.x 走 v2 发布地址
            return "v1", [lambda: _npm_latest("opencode-ai")]
        return "v2", [lambda: str(_http_json("https://opencode.ai/update/api/latest/cli/npm")
                                  .get("version") or ""),
                      lambda: _npm_latest(NPM_PACKAGES["opencode"])]
    if engine == "grok":
        return "", [lambda: "|".join(_grok_check(exe)),
                    lambda: _http_text("https://x.ai/cli/stable"),
                    lambda: _http_text("https://storage.googleapis.com/grok-build-public-artifacts/cli/stable")]
    if engine == "antigravity":
        plat = antigravity_platform()
        return plat, [lambda: str(_http_json("https://antigravity-cli-auto-updater-974169037036"
                                             f".us-central1.run.app/manifests/{plat}.json")
                                  .get("version") or "")]
    return "", []


_CACHE: dict[str, tuple[float, str, str, str]] = {}    # key -> (时间, latest, channel, error)
_CACHE_LOCK = threading.Lock()


def latest_version(engine: str, exe: str, installed: str = "", refresh: bool = False) -> dict:
    """{"latest", "channel", "error"}。缓存:成功 6 小时、失败 15 分钟;refresh 强制重查。"""
    variant, fetchers = _sources(engine, exe, installed)
    if not fetchers:
        return {"latest": "", "channel": "", "error": "unsupported"}
    key = f"{engine}:{variant}"
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit and not refresh:
        ttl = LATEST_FAIL_TTL_S if hit[3] else LATEST_TTL_S
        if now - hit[0] < ttl:
            return {"latest": hit[1], "channel": hit[2], "error": hit[3]}
    latest, channel, err = "", variant, ""
    for fetch in fetchers:
        try:
            raw = fetch().strip()
        except Exception as e:   # 网络 / 解析失败换下一个来源
            err = f"{type(e).__name__}: {e}"[:200]
            continue
        if engine == "grok" and "|" in raw:     # CLI 自查:"版本|渠道"
            raw, channel = raw.split("|", 1)
        v = parse_version(raw)
        if v:
            latest, err = v, ""
            break
        err = "unparsable"
    if engine == "grok" and channel == "":
        channel = "stable"
    with _CACHE_LOCK:
        _CACHE[key] = (now, latest, channel, err)
    return {"latest": latest, "channel": channel, "error": err}


def reset_cache():
    """测试用。"""
    with _CACHE_LOCK:
        _CACHE.clear()


def check(engine: str, exe: str, refresh: bool = False) -> dict:
    """{"installed", "latest", "channel", "outdated", "error"};error 非空时 outdated 一律 False。"""
    installed = installed_version(exe)
    if not installed:
        return {"installed": "", "latest": "", "channel": "", "outdated": False, "error": "no_version"}
    lv = latest_version(engine, exe, installed, refresh)
    return {"installed": installed, "latest": lv["latest"], "channel": lv["channel"],
            "outdated": not lv["error"] and is_outdated(installed, lv["latest"]), "error": lv["error"]}


# ---------------------------------------------------------------- 一键更新
_NPM_PREFIX: dict[str, str] = {}


def _npm_global_prefix(npm: str) -> str:
    if npm not in _NPM_PREFIX:
        r = _run([npm, "prefix", "-g"], timeout=30)
        _NPM_PREFIX[npm] = (r.stdout.strip() if r is not None and r.returncode == 0 else "")
    return _NPM_PREFIX[npm]


def windows_npm_shim(exe: str, npm: str | None) -> bool:
    """Windows:exe 是 npm 全局目录里的 .cmd / .ps1 垫片(指向 node_modules 里的包)。
    pi 官方脚本的托管安装(~/.pi/agent/bin)不在 npm 全局目录,不算,仍走 pi update --self。"""
    p = Path(exe)
    if p.suffix.lower() not in (".cmd", ".ps1", ".bat") or not npm:
        return False
    try:
        body = p.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return False
    if "node_modules" not in body:
        return False
    prefix = _npm_global_prefix(npm)
    return bool(prefix) and os.path.normcase(os.path.normpath(str(p.parent))) == \
        os.path.normcase(os.path.normpath(prefix))


def _cli_name(exe: str) -> str:
    name = re.split(r"[\\/]", exe)[-1]
    for suf in (".exe", ".cmd", ".ps1", ".bat"):
        if name.lower().endswith(suf):
            return name[: -len(suf)]
    return name


def update_plan(engine: str, exe: str, windows: bool = IS_WIN, installed: str = "") -> dict:
    """{argv, display, terminal_line, env, method} 或 {unsupported}。
    method:self = CLI 自带更新命令;npm = Windows 上 npm 全局装的,直接 npm install -g。"""
    args = UPDATE_ARGS.get(engine)
    if args is None:
        return {"unsupported": "unknown_engine"}
    env = dict(UPDATE_ENV.get(engine) or {})
    pkg = NPM_PACKAGES.get(engine)
    if engine == "opencode" and installed.startswith("1."):
        pkg = "opencode-ai"
    if windows and pkg:
        npm = shutil.which("npm")
        if windows_npm_shim(exe, npm):
            argv = [npm, "install", "-g", f"{pkg}@latest"]
            return {"argv": argv, "display": f"npm install -g {pkg}@latest",
                    "terminal_line": "call " + subprocess.list2cmdline(argv), "env": env, "method": "npm"}
    argv = [exe, *args]
    return {"argv": argv, "display": " ".join([_cli_name(exe), *args]),
            "terminal_line": ("call " + subprocess.list2cmdline(argv)) if windows else shlex.join(argv),
            "env": env, "method": "self"}


def start_update(engine: str, plan: dict, resolve: Callable[[], str | None], before: str,
                 timeout_s: int = UPDATE_TIMEOUT_S, stall_s: int = engine_setup.INSTALL_STALL_S) -> dict:
    """后台跑更新命令;跑完重测版本号:变新 → ok(version);没变 → error unchanged(日志里有原因)。"""
    def verify() -> dict:
        exe = resolve()
        now = installed_version(exe) if exe else ""
        if now and before and version_key(now) > version_key(before):
            return {"state": "ok", "version": now, "previous": before, "path": exe or ""}
        return {"state": "error", "reason": "unchanged", "hint": "", "version": now or before,
                "previous": before}
    return engine_setup.start_job(job_key(engine), engine, plan, verify, timeout_s, stall_s)


def update_snapshot(engine: str) -> dict | None:
    return engine_setup.job_snapshot(job_key(engine))

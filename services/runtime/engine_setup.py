"""执行引擎 CLI 一键安装 + 登录引导(2026-10-09)。

顶栏切到 claude/codex/kimi/pi/opencode/grok 时前端检测本机 CLI,弹窗里三件事:

1. 一键安装:后台子进程跑各家**官方安装脚本**(固定命令,不经语言模型),日志实时回传。
   子进程开新会话(无控制终端,脚本里读 /dev/tty 的提问自动走非交互分支)、stdin 关闭,
   环境变量剔除本程序与各渠道的密钥(VIDEOAGENTS_*、*_API_KEY / *_TOKEN / *_SECRET …),
   远程脚本拿不到 Agentics 登录令牌。失败可「在终端中安装」:弹出终端跑同一条命令,
   用户能回答脚本的交互提问(如 pi 缺 Node.js 时询问是否代装)。
2. 登录:弹出终端窗口跑各家 CLI **自己的**登录命令,用户在浏览器里完成授权;
   本程序不经手、不保存任何令牌。
3. 登录状态:各家官方状态命令(claude auth status / codex login status)或凭证文件,
   前端轮询到已登录即关窗。判断不了(如改用自定义渠道 Key)返回 None,前端不打扰。

文案由前端按界面语言出(本模块只回 reason / hint 代号);终端窗口里的提示行按
zh 参数出中/英。仅本机后端可调(API 层按请求来源拦截)。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

IS_WIN = os.name == "nt"

# 官方安装命令(2026-10-09 逐个下载脚本核对过安装位置与交互行为):
#   sh  : (脚本 URL, 解释器) —— macOS / Linux,官方写法 curl -fsSL <url> | <sh>
#   ps1 : Windows PowerShell 官方写法 irm <url> | iex
#   npm : Windows 无官方 ps1 时的官方 npm 包(opencode 文档列的 Windows 方式之一)
#   env : 安装时附加的环境变量(codex 脚本认 CODEX_NON_INTERACTIVE 跳过提问)
INSTALLERS: dict[str, dict] = {
    "claude": {"sh": ("https://claude.ai/install.sh", "bash"),
               "ps1": "https://claude.ai/install.ps1"},
    "codex": {"sh": ("https://chatgpt.com/codex/install.sh", "sh"),
              "ps1": "https://chatgpt.com/codex/install.ps1",
              "env": {"CODEX_NON_INTERACTIVE": "1"}},
    "kimi": {"sh": ("https://code.kimi.com/kimi-code/install.sh", "bash"),
             "ps1": "https://code.kimi.com/kimi-code/install.ps1"},
    "pi": {"sh": ("https://pi.dev/install.sh", "sh"),
           "ps1": "https://pi.dev/install.ps1"},
    "opencode": {"sh": ("https://opencode.ai/install", "bash"),
                 "npm": "opencode-ai"},
    "grok": {"sh": ("https://x.ai/cli/install.sh", "bash"),
             "ps1": "https://x.ai/cli/install.ps1"},
}

# 官方脚本的默认安装位置:服务进程 PATH 不含这些目录(脚本只改用户 shell rc / 用户 PATH,
# 对已在运行的进程不生效)时据此兜底,装完不用重启服务即可检测到、派单可用。
# posix 为相对 HOME 的路径;Windows 为 (环境变量, 相对路径)。
BIN_FALLBACKS_POSIX: dict[str, tuple[str, ...]] = {
    "claude": (".local/bin/claude",),
    "codex": (".local/bin/codex",),
    "kimi": (".kimi-code/bin/kimi",),
    "pi": (".pi/agent/bin/pi", ".local/bin/pi"),
    "opencode": (".opencode/bin/opencode",),
    "grok": (".grok/bin/grok", ".local/bin/grok"),
}
BIN_FALLBACKS_WIN: dict[str, tuple[tuple[str, str], ...]] = {
    "claude": (("USERPROFILE", r".local\bin\claude.exe"),),
    "codex": (("LOCALAPPDATA", r"Programs\OpenAI\Codex\bin\codex.exe"),),
    "kimi": (("USERPROFILE", r".kimi-code\bin\kimi.exe"),),
    "pi": (("APPDATA", r"npm\pi.cmd"), ("USERPROFILE", r".pi\agent\bin\pi.cmd")),
    "opencode": (("APPDATA", r"npm\opencode.cmd"),),
    "grok": (("USERPROFILE", r".grok\bin\grok.exe"),),
}

# 登录命令(在弹出的终端里跑,接在可执行文件绝对路径后);pi 没有 login 子命令,
# 进 TUI 后由用户输入 /login(终端提示行写明)
LOGIN_ARGS: dict[str, tuple[str, ...]] = {
    "claude": ("auth", "login"),
    "codex": ("login",),
    "kimi": ("login",),
    "pi": (),
    "opencode": ("auth", "login"),
    "grok": ("login",),
}

# 超时按「无进展」判:claude 二进制约 240 MB,实测经代理 ~110–140 KB/s 要半小时以上,固定总时长会误杀
# 慢网下正常的安装。既无新输出(含不换行的进度动画)、下载字节数也不涨满 INSTALL_STALL_S 才中止;
# INSTALL_TIMEOUT_S 只是兜底总上限
INSTALL_TIMEOUT_S = 7200
INSTALL_STALL_S = 600
WATCH_INTERVAL_S = 5
# 官方脚本静默下载大文件期间不输出日志:按下载目录里本次新文件的大小报进度。
# 各家下载位置:claude / grok 下到 HOME 下固定目录(相对 HOME);kimi / codex 等用 mktemp,
# 安装子进程的 TMPDIR(Windows TEMP/TMP)指到本任务专属临时目录,一并统计
DOWNLOAD_DIRS = {"claude": ".claude/downloads", "grok": ".grok/downloads"}
STATUS_TIMEOUT_S = 20
LOG_LINES = 400

# 安装子进程剔除的环境变量:本程序自身(含桌面端 Agentics 登录令牌)与各类密钥
_SECRET_ENV = re.compile(r"(API_KEY|_TOKEN|TOKEN_|SECRET|PASSWORD|PASSWD|JWT|ACCESS_KEY|PRIVATE_KEY|"
                         r"CREDENTIAL|AUTH_KEY|DEPLOYMENT_KEY)", re.I)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


def supported(engine: str) -> bool:
    return engine in INSTALLERS


def home_dir() -> Path:
    return Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or Path.home())


def fallback_executable(engine: str) -> str | None:
    """官方脚本默认安装位置里找到的可执行文件(PATH 找不到时的兜底)。"""
    if IS_WIN:
        for var, rel in BIN_FALLBACKS_WIN.get(engine, ()):
            base = os.environ.get(var)
            if base and (Path(base) / rel).is_file():
                return str(Path(base) / rel)
        return None
    home = home_dir()
    for rel in BIN_FALLBACKS_POSIX.get(engine, ()):
        p = home / rel
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def installer_env(extra: dict | None = None, base: dict | None = None) -> dict:
    """安装子进程环境:去掉 VIDEOAGENTS_* 与各类密钥,保留 PATH / 代理等其余设置。"""
    src = os.environ if base is None else base
    env = {k: v for k, v in src.items()
           if not k.upper().startswith("VIDEOAGENTS_") and not _SECRET_ENV.search(k)}
    env["NO_COLOR"] = "1"
    env.setdefault("TERM", "dumb")
    env.update(extra or {})
    return env


def install_plan(engine: str, windows: bool = IS_WIN) -> dict:
    """{argv, display, terminal_line, env} 或 {unsupported: reason}。
    argv 后台子进程用;terminal_line 写进终端脚本(在终端中安装);display 弹窗展示。"""
    spec = INSTALLERS.get(engine)
    if not spec:
        return {"unsupported": "unknown_engine"}
    env = dict(spec.get("env") or {})
    if windows:
        if spec.get("ps1"):
            line = f"irm {spec['ps1']} | iex"
            ps = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass"]
            return {"argv": ps + ["-NonInteractive", "-Command", line], "display": line,
                    "terminal_line": subprocess.list2cmdline(ps + ["-Command", line]), "env": env}
        if spec.get("npm"):
            npm = shutil.which("npm")
            if not npm:
                return {"unsupported": "need_node"}
            argv = [npm, "install", "-g", spec["npm"]]
            return {"argv": argv, "display": f"npm install -g {spec['npm']}",
                    "terminal_line": "call " + subprocess.list2cmdline(argv), "env": env}
        return {"unsupported": "platform"}
    url, shell = spec["sh"]
    line = f"curl -fsSL {url} | {shell}"
    if not shutil.which("curl"):
        return {"unsupported": "need_curl"}
    return {"argv": ["/bin/bash", "-o", "pipefail", "-c", line], "display": line,
            "terminal_line": line, "env": env}


def clean_line(raw: str) -> str:
    """去 ANSI 控制序列;带 \\r 的进度刷新行只留最后一段。"""
    s = _ANSI.sub("", raw.rstrip("\n"))
    if "\r" in s:
        segs = [seg for seg in s.split("\r") if seg.strip()]
        s = segs[-1] if segs else ""
    return s.rstrip()


def diagnose(log: str, exit_code: int | None, engine: str = "") -> str:
    """失败原因代号(前端按界面语言出提示):region / node / network / ""。
    curl -f 遇 4xx 不打印正文,claude.ai 的地区拒绝只表现为 403,按 region 报。"""
    low = log.lower()
    if "unavailable in region" in low or "not available in your country" in low \
            or "unsupported_country" in low \
            or (engine == "claude" and "returned error: 403" in low):
        return "region"
    if "node.js 22" in low or "npm is required" in low or "requires node" in low:
        return "node"
    net_words = ("could not resolve host", "failed to connect", "connection reset",
                 "connection refused", "timed out", "ssl_connect", "ssl connect",
                 "unable to connect", "network is unreachable", "returned error: 403",
                 "returned error: 5", "the remote name could not be resolved",
                 "unable to connect to the remote server")
    if any(w in low for w in net_words) or exit_code in (6, 7, 22, 28, 35, 52, 56):
        return "network"
    return ""


# ---------------------------------------------------------------- 后台安装任务
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def install_snapshot(engine: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(engine)
        if not job:
            return None
        out = {k: v for k, v in job.items() if k not in ("lines", "proc", "last_output")}
        out["log"] = list(job["lines"])
        now = job.get("ended") or time.time()
        out["elapsed_s"] = round(now - job["started"], 1)
        out["quiet_s"] = round(now - job["last_output"], 1)    # 距上次输出的秒数(前端据此提示「下载中请耐心」)
    if out["state"] == "running":
        out["downloaded_bytes"] = downloaded_bytes(engine, out["started"], out.get("tmpdir"))
    out.pop("tmpdir", None)
    return out


def downloaded_bytes(engine: str, since: float, tmpdir: str | None = None) -> int | None:
    """本次安装已下载的字节数:下载目录与任务临时目录里 since 之后改动的最大文件;
    两处都没有可看的目录时返回 None(前端不显示)。"""
    dirs = [home_dir() / DOWNLOAD_DIRS[engine]] if engine in DOWNLOAD_DIRS else []
    if tmpdir:
        dirs.append(Path(tmpdir))
    if not dirs:
        return None
    best = 0
    for d in dirs:
        try:
            for f in d.rglob("*"):
                st = f.stat()
                if f.is_file() and st.st_mtime >= since - 1:
                    best = max(best, st.st_size)
        except OSError:
            continue
    return best


def start_install(engine: str, resolve: Callable[[], str | None],
                  timeout_s: int = INSTALL_TIMEOUT_S, plan: dict | None = None,
                  stall_s: int = INSTALL_STALL_S) -> dict:
    """起后台安装;同一引擎已在装则直接返回当前进度(不重复起)。"""
    plan = plan or install_plan(engine)
    with _LOCK:
        cur = _JOBS.get(engine)
        if not (cur and cur["state"] == "running"):
            job = {"engine": engine, "state": "running", "started": time.time(), "ended": None,
                   "exit_code": None, "reason": "", "hint": "", "path": "",
                   "command": plan.get("display", ""), "timeout_s": timeout_s, "stall_s": stall_s,
                   "last_output": time.time(),
                   "lines": deque(maxlen=LOG_LINES), "proc": None}
            _JOBS[engine] = job
            if plan.get("unsupported"):
                job.update(state="error", reason=plan["unsupported"], ended=time.time(),
                           hint="node" if plan["unsupported"] == "need_node" else "")
            else:
                threading.Thread(target=_run_install, args=(job, plan, resolve, timeout_s, stall_s),
                                 name=f"engine-install-{engine}", daemon=True).start()
    return install_snapshot(engine)


def _kill(proc: subprocess.Popen):
    try:
        if IS_WIN:
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


def _run_install(job: dict, plan: dict, resolve: Callable[[], str | None], timeout_s: int,
                 stall_s: int = INSTALL_STALL_S):
    kw: dict = {}
    if IS_WIN:
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kw["start_new_session"] = True     # 无控制终端:脚本的 /dev/tty 提问走非交互分支
    stopped = {"reason": ""}       # timeout / stalled:看门狗中止的原因
    tmpdir = tempfile.mkdtemp(prefix=f"videoagents-install-{job['engine']}-")
    tmp_env = {"TEMP": tmpdir, "TMP": tmpdir} if IS_WIN else {"TMPDIR": tmpdir}
    with _LOCK:
        job["tmpdir"] = tmpdir
    try:
        proc = subprocess.Popen(plan["argv"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=installer_env({**(plan.get("env") or {}), **tmp_env}),
                                cwd=str(home_dir()), **kw)
    except OSError as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        with _LOCK:
            job["lines"].append(str(e))
            job.update(state="error", reason="spawn_failed", ended=time.time(), tmpdir=None)
        return
    with _LOCK:
        job["proc"] = proc

    def _pump():
        # 按块读(read1)而非按行:不换行的 \r 进度动画也算「有进展」;整行才进日志
        buf = b""
        while True:
            chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            *done, buf = buf.split(b"\n")
            with _LOCK:
                job["last_output"] = time.time()
                for raw in done:
                    line = clean_line(raw.decode("utf-8", "replace"))
                    if line:
                        job["lines"].append(line)
        tail = clean_line(buf.decode("utf-8", "replace"))
        if tail:
            with _LOCK:
                job["lines"].append(tail)

    def _watch():
        last_bytes, last_change = -1, time.time()
        while proc.poll() is None:
            time.sleep(WATCH_INTERVAL_S)
            now = time.time()
            got = downloaded_bytes(job["engine"], job["started"], tmpdir) or 0
            if got != last_bytes:
                last_bytes, last_change = got, now
            with _LOCK:
                last_change = max(last_change, job["last_output"])
            why = ("timeout" if now - job["started"] > timeout_s else
                   "stalled" if now - last_change > stall_s else "")
            if why and proc.poll() is None:
                stopped["reason"] = why
                _kill(proc)
                return
    reader = threading.Thread(target=_pump, name=f"engine-install-log-{job['engine']}", daemon=True)
    reader.start()
    threading.Thread(target=_watch, name=f"engine-install-watch-{job['engine']}", daemon=True).start()
    try:
        code = proc.wait()
        # 脚本退出即收尾;残留的后台子进程若还占着输出管道,读线程最多再等 5 秒,不让任务一直挂在「安装中」
        reader.join(timeout=5)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    with _LOCK:
        job["exit_code"] = code
        log = "\n".join(job["lines"])
    engine = job["engine"]
    if stopped["reason"]:
        result = {"state": "error", "reason": stopped["reason"], "hint": diagnose(log, None, engine)}
    elif code != 0:
        result = {"state": "error", "reason": "exit", "hint": diagnose(log, code, engine)}
    else:
        path = resolve() or ""
        result = ({"state": "ok", "path": path} if path else
                  {"state": "error", "reason": "not_found_after_install", "hint": ""})
    with _LOCK:
        job.update(ended=time.time(), proc=None, tmpdir=None, **result)


def reset_jobs():
    """测试用:清空任务表(不杀进程)。"""
    with _LOCK:
        _JOBS.clear()


# ---------------------------------------------------------------- 登录状态
def _run_status(argv: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=STATUS_TIMEOUT_S, env={**os.environ, "NO_COLOR": "1"},
                              cwd=str(home_dir()))
    except (OSError, subprocess.SubprocessError):
        return None


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _env_any(*names: str) -> bool:
    return any((os.environ.get(n) or "").strip() for n in names)


def login_status(engine: str, executable: str | None) -> dict:
    """{"logged_in": True|False|None, "method": str}。None = 判断不了(不打扰用户)。"""
    try:
        return _login_status(engine, executable)
    except Exception:   # 状态探测失败绝不影响检测接口
        return {"logged_in": None, "method": ""}


def _login_status(engine: str, exe: str | None) -> dict:
    home = home_dir()
    if engine == "claude":
        if not exe:
            return {"logged_in": None, "method": ""}
        r = _run_status([exe, "auth", "status"])
        if r is None:
            return {"logged_in": None, "method": ""}
        try:
            d = json.loads(r.stdout or "{}")
        except ValueError:
            d = {}
        if isinstance(d, dict) and "loggedIn" in d:
            return {"logged_in": bool(d["loggedIn"]), "method": str(d.get("authMethod") or "")}
        return {"logged_in": r.returncode == 0 if r.returncode in (0, 1) else None, "method": ""}
    if engine == "codex":
        if not exe:
            return {"logged_in": None, "method": ""}
        r = _run_status([exe, "login", "status"])
        if r is None or r.returncode not in (0, 1):
            return {"logged_in": None, "method": ""}
        first = ((r.stdout or r.stderr or "").strip().splitlines() or [""])[0]
        return {"logged_in": r.returncode == 0, "method": first[:80] if r.returncode == 0 else ""}
    if engine == "kimi":
        base = Path(os.environ["KIMI_CODE_HOME"]) if os.environ.get("KIMI_CODE_HOME") else home / ".kimi-code"
        for d in (base, home / ".kimi"):
            cred = _read_json(d / "credentials" / "kimi-code.json")
            if isinstance(cred, dict) and any(str(cred.get(k) or "").strip()
                                              for k in ("refresh_token", "access_token")):
                return {"logged_in": True, "method": "oauth"}
        try:   # 改用自定义渠道 Key(kimi provider add):判断不了,不打扰
            cfg = (base / "config.toml").read_text(encoding="utf-8")
        except OSError:
            cfg = ""
        if re.search(r'^\s*api_key\s*=\s*"[^"]+"', cfg, re.M):
            return {"logged_in": None, "method": "api_key"}
        return {"logged_in": False, "method": ""}
    if engine == "pi":
        auth = _read_json(home / ".pi" / "agent" / "auth.json")
        if isinstance(auth, dict) and auth:
            return {"logged_in": True, "method": ",".join(sorted(auth))[:80]}
        if _env_any("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "XAI_API_KEY",
                    "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY"):
            return {"logged_in": None, "method": "env"}
        return {"logged_in": False, "method": ""}
    if engine == "opencode":
        data = Path(os.environ["XDG_DATA_HOME"]) if os.environ.get("XDG_DATA_HOME") else home / ".local" / "share"
        auth = _read_json(data / "opencode" / "auth.json")
        if isinstance(auth, dict) and auth:
            return {"logged_in": True, "method": ",".join(sorted(auth))[:80]}
        return {"logged_in": False, "method": ""}
    if engine == "grok":
        if _env_any("XAI_API_KEY", "GROK_DEPLOYMENT_KEY"):
            return {"logged_in": True, "method": "api_key"}
        auth = _read_json(home / ".grok" / "auth.json")
        if isinstance(auth, dict) and any(isinstance(v, dict) and (v.get("key") or v.get("refresh_token"))
                                          for v in auth.values()):
            return {"logged_in": True, "method": "oauth"}
        return {"logged_in": False, "method": ""}
    return {"logged_in": None, "method": ""}


# ---------------------------------------------------------------- 弹出终端
_TEXT = {
    "zh": {
        "login_title": "VideoAgents · 登录 {label}",
        "login_browser": "接下来会打开浏览器授权页面,请用你自己的账号登录并授权;授权完成后回到 VideoAgents 窗口,会自动检测。",
        "login_pi": "pi 启动后输入 /login 回车,按提示选择服务商登录(订阅账号或 API Key);完成后输入 /quit 退出。",
        "login_kimi": "如提示选择区域:中国大陆账号选 mainland-cn(kimi.com),海外账号选 global(kimi.ai)。",
        "install_title": "VideoAgents · 安装 {label}",
        "install_note": "正在运行官方安装命令;如脚本提问,请在此窗口回答。安装完成后回到 VideoAgents 窗口点「重新检测」。",
        "ok": "完成。可以关闭此窗口,回到 VideoAgents。",
        "fail": "命令以退出码 {code} 结束;请查看上面的输出。",
        "pause": "按回车键关闭此窗口…",
    },
    "en": {
        "login_title": "VideoAgents · Sign in to {label}",
        "login_browser": "A browser authorization page will open. Sign in with your own account and approve; then return to VideoAgents, which checks automatically.",
        "login_pi": "Once pi starts, type /login and press Enter, then pick a provider and sign in (subscription or API key). Type /quit when done.",
        "login_kimi": "If asked for a region: choose mainland-cn (kimi.com) for mainland China accounts, global (kimi.ai) otherwise.",
        "install_title": "VideoAgents · Install {label}",
        "install_note": "Running the official install command. Answer any questions the script asks in this window, then go back to VideoAgents and click \"Re-check\".",
        "ok": "Done. You can close this window and return to VideoAgents.",
        "fail": "The command exited with code {code}; see the output above.",
        "pause": "Press Enter to close this window…",
    },
}


def _cmd_echo(s: str) -> str:
    """cmd.exe echo 行转义(^ & | < > ( ) %)。"""
    s = s.replace("%", "%%")
    for ch in "^&|<>()":
        s = s.replace(ch, "^" + ch)
    return "echo " + s if s else "echo."


def terminal_script(lines: list[str], command_line: str, zh: bool,
                    windows: bool = IS_WIN, path_dirs: list[str] | None = None) -> str:
    """生成在终端里跑的脚本正文:提示行 → 命令 → 成功/失败一句 → 等回车关窗。"""
    t = _TEXT["zh" if zh else "en"]
    if windows:
        out = ["@echo off", "chcp 65001 >nul", "title VideoAgents"]
        if path_dirs:
            out.append('set "PATH=' + ";".join(path_dirs) + ';%PATH%"')
        out += [_cmd_echo(s) for s in lines] + ["echo.", command_line, "set CODE=%ERRORLEVEL%", "echo."]
        out += ["if %CODE%==0 (" + _cmd_echo(t["ok"]) + ") else (" +
                _cmd_echo(t["fail"].replace("{code}", "%CODE%")).replace("%%CODE%%", "%CODE%") + ")",
                "pause"]
        return "\r\n".join(out) + "\r\n"
    out = ["#!/bin/bash", "clear"]
    if path_dirs:
        out.append("export PATH=" + shlex.quote(":".join(path_dirs)) + ':"$PATH"')
    out += ["printf '%s\\n' " + " ".join(shlex.quote(s) for s in lines), "echo", command_line,
            "code=$?", "echo",
            'if [ "$code" -eq 0 ]; then printf \'%s\\n\' ' + shlex.quote(t["ok"]) +
            "; else printf '%s\\n' " + shlex.quote(t["fail"]).replace("{code}", "'\"$code\"'") + "; fi",
            "read -r -p " + shlex.quote(t["pause"]) + " _"]
    return "\n".join(out) + "\n"


def login_lines(engine: str, label: str, zh: bool) -> list[str]:
    t = _TEXT["zh" if zh else "en"]
    lines = [t["login_title"].format(label=label), ""]
    lines.append(t["login_pi"] if engine == "pi" else t["login_browser"])
    if engine == "kimi":
        lines.append(t["login_kimi"])
    return lines


def install_lines(label: str, display: str, zh: bool) -> list[str]:
    t = _TEXT["zh" if zh else "en"]
    return [t["install_title"].format(label=label), "", t["install_note"], "", "$ " + display]


def login_command_line(engine: str, exe: str, windows: bool = IS_WIN) -> str:
    argv = [exe, *LOGIN_ARGS.get(engine, ())]
    if windows:
        return "call " + subprocess.list2cmdline(argv)   # .cmd 垫片(npm 装的 pi/opencode)须 call
    return shlex.join(argv)


class TerminalUnavailable(RuntimeError):
    pass


def open_terminal(script_dir: Path, name: str, body: str, windows: bool = IS_WIN,
                  env: dict | None = None) -> Path:
    """把脚本写到 script_dir/<name>.(command|cmd|sh) 并在新终端窗口里运行。"""
    script_dir.mkdir(parents=True, exist_ok=True)
    if windows:
        path = script_dir / f"{name}.cmd"
        path.write_text(body, encoding="utf-8", newline="")
        subprocess.Popen(["cmd.exe", "/k", str(path)], env=env, cwd=str(home_dir()),
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        return path
    path = script_dir / f"{name}.command" if sys.platform == "darwin" else script_dir / f"{name}.sh"
    path.write_text(body, encoding="utf-8")
    path.chmod(0o700)
    if sys.platform == "darwin":
        # 用 open 交给 Terminal.app(不经 osascript:系统通知功能曾因 osascript 触发不稳定回滚)
        r = subprocess.run(["open", "-a", "Terminal", str(path)], capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            raise TerminalUnavailable((r.stderr or r.stdout or "").strip()[-300:])
        return path
    for argv in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"], ["konsole", "-e"],
                 ["xfce4-terminal", "-x"], ["xterm", "-e"]):
        if shutil.which(argv[0]):
            subprocess.Popen(argv + ["bash", str(path)], env=env, cwd=str(home_dir()),
                             start_new_session=True)
            return path
    raise TerminalUnavailable("no terminal emulator found")

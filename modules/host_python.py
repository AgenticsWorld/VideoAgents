"""宿主 CLI 的解释器兜底:成员命令里的 `python3` 落到缺依赖、或依赖装了却加载不了的解释器时,换宿主解释器重跑。

宿主派单注入 VIDEOAGENTS_PYTHON(= 服务自身 sys.executable,桌面端即自带运行时),并把只含 python3 / python
启动脚本的目录排到 PATH 最前(core.host_python_bin_dir)。但引擎多半在登录 / 交互 shell 里跑命令,
~/.zprofile、~/.zshrc(path_helper、brew shellenv、conda init)会把别的 python3 重新排到前面,
所以宿主 CLI 入口还要自己再兜一次——即本模块。
前科:2026-09-20 fengshen3 ep06 codex 的 python3 → Homebrew 3.13(缺 Playwright);
2026-10-22 macOS 27 拒绝加载 scipy 1.15.3 的扩展(dlopen:__thread_bss zero-fill section),conda base 里
`import scipy` 不报错、`import scipy.signal` 失败,代码作曲 / 混音跑不起来。

只用标准库、兼容 macOS 自带的 Python 3.9;须在入口任何第三方 import、任何输出之前调用。
探测要真 import(scipy.signal 一次约 0.7s),结果按解释器缓存在运行目录,包或系统版本变了才重测。
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# 宿主 CLI 共用的第三方依赖;只看装没装抓不到「装了但加载不了」,一律真 import
PROBE_MODULES = ("numpy", "scipy.signal", "scipy.fft", "scipy.ndimage", "scipy.optimize",
                 "soundfile", "PIL.Image", "yaml", "playwright")
REEXEC_ENV = "VIDEOAGENTS_PYTHON_REEXEC"
CACHE_NAME = "host-python-probe.json"


def _runtime_dir() -> Path:
    root = Path(__file__).resolve().parents[1]
    data = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", root / "data")).expanduser()
    return Path(os.environ.get("VIDEOAGENTS_RUNTIME_DIR", data / ".videoagents")).expanduser()


def same_env(a: str, b: str) -> bool:
    """同一解释器且同一 bin 目录才算同一环境:venv 的 python 是指向基础解释器的链接,只比 samefile 会把两者当成一个。"""
    try:
        return os.path.samefile(a, b) and os.path.samefile(os.path.dirname(a), os.path.dirname(b))
    except OSError:
        return False


def _signature() -> str:
    """缓存键:解释器版本 + 系统版本(系统升级可能让旧扩展加载不了,即本次前科)+ 各顶层包入口文件的 inode / 大小 / 修改时间(重装即变)。"""
    if hasattr(os, "uname"):
        u = os.uname()
        os_tag = f"{u.sysname} {u.release} {u.version}"
    else:
        os_tag = str(sys.getwindowsversion())
    parts = [sys.version, os_tag]
    for top in sorted({m.split(".")[0] for m in PROBE_MODULES}):
        try:
            origin = getattr(importlib.util.find_spec(top), "origin", None) or ""
            st = os.stat(origin) if origin else None
        except (ImportError, ValueError, OSError):
            origin, st = "", None
        parts.append(f"{top}={origin}:{st.st_ino}:{st.st_size}:{st.st_mtime_ns}" if st else f"{top}=")
    return "|".join(parts)


def probe() -> str:
    """逐个真实 import;返回首个失败的「模块: 错误」,全部成功返回空串。"""
    for name in PROBE_MODULES:
        try:
            importlib.import_module(name)
        except Exception as e:  # noqa: BLE001  ImportError / dlopen 的 OSError / 扩展初始化里的任意错误
            msg = (str(e).splitlines() or [""])[0][:300]
            return f"{name}: {type(e).__name__}: {msg}"
    return ""


def cached_probe() -> str:
    """probe() 的结果按解释器路径缓存;签名不变直接复用,缓存读写失败不影响判定。"""
    path = _runtime_dir() / CACHE_NAME
    key = os.path.abspath(sys.executable)
    sig = _signature()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    hit = data.get(key)
    if isinstance(hit, dict) and hit.get("sig") == sig:
        return str(hit.get("error") or "")
    err = probe()
    data[key] = {"sig": sig, "error": err, "at": int(time.time())}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{CACHE_NAME}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass
    return err


def ensure_host_python(argv: list | None = None) -> None:
    """当前解释器不是宿主解释器、且 PROBE_MODULES 有加载不了的,就用宿主解释器原样重跑并以其退出码退出。

    不满足条件(未注入 VIDEOAGENTS_PYTHON / 已重跑过 / 宿主解释器不存在 / 已是宿主环境 / 依赖都能加载)时原样返回——
    成员显式用别的健康解释器(如宿主环境本身坏了时绕路)照常跑。
    """
    host = os.environ.get("VIDEOAGENTS_PYTHON", "").strip()
    if not host or os.environ.get(REEXEC_ENV) or not os.path.isfile(host):
        return
    if same_env(host, sys.executable):
        return
    err = cached_probe()
    if not err:
        return
    print(f"{Path(sys.argv[0]).name}: {sys.executable} 加载依赖失败({err}),改用宿主解释器 {host} 重跑",
          file=sys.stderr, flush=True)
    sys.stdout.flush()
    args = list(argv if argv is not None else sys.argv)
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    if argv is None and spec is not None and spec.name:   # python3 -m modules.xxx 原样用 -m 重跑
        cmd = [host, "-m", spec.name, *args[1:]]
    else:
        cmd = [host, *args]
    sys.exit(subprocess.call(cmd, env={**os.environ, REEXEC_ENV: "1"}))

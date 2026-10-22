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
换之前宿主解释器也探测一次(`<宿主> host_python.py --probe ...`,缓存由宿主进程按自己的签名写):宿主的失败项是当前失败项的
真子集才换,否则留在当前解释器——2026-10-22 #160:宿主 conda base 的 scipy.signal 加载不了,成员换了个只缺 Playwright 的
健康环境跑代码作曲,反被换回 base 必败。入口按自己真用到的依赖传 modules(genmedia 不用 Playwright,见 NO_BROWSER_MODULES)。
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
# 不开无头浏览器的入口(genmedia 等)按这一组判定:只缺 Playwright 的解释器对它们是健康的
NO_BROWSER_MODULES = tuple(m for m in PROBE_MODULES if m != "playwright")
REEXEC_ENV = "VIDEOAGENTS_PYTHON_REEXEC"
CACHE_NAME = "host-python-probe.json"
HOST_PROBE_TIMEOUT = 120   # 宿主冷启动真 import scipy 等,首次可能要几秒


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


def _probe_set(modules) -> tuple:
    """实际探测的模块:PROBE_MODULES 全量(一条缓存同时服务各入口的子集)+ 调用方额外点名的。"""
    return PROBE_MODULES + tuple(m for m in modules if m not in PROBE_MODULES)


def _signature(modules=PROBE_MODULES) -> str:
    """缓存签名:解释器版本 + 系统版本(系统升级可能让旧扩展加载不了,即本次前科)+ 各顶层包入口文件的 inode / 大小 / 修改时间(重装即变)。"""
    if hasattr(os, "uname"):
        u = os.uname()
        os_tag = f"{u.sysname} {u.release} {u.version}"
    else:
        os_tag = str(sys.getwindowsversion())
    parts = [sys.version, os_tag]
    for top in sorted({m.split(".")[0] for m in modules}):
        try:
            origin = getattr(importlib.util.find_spec(top), "origin", None) or ""
            st = os.stat(origin) if origin else None
        except (ImportError, ValueError, OSError):
            origin, st = "", None
        parts.append(f"{top}={origin}:{st.st_ino}:{st.st_size}:{st.st_mtime_ns}" if st else f"{top}=")
    return "|".join(parts)


def _cache_key() -> str:
    """按解释器路径缓存;同一解释器换了 PYTHONPATH 就是另一套包,分开记。"""
    key = os.path.abspath(sys.executable)
    pythonpath = os.environ.get("PYTHONPATH", "")
    return f"{key}|PYTHONPATH={pythonpath}" if pythonpath else key


def probe(modules=PROBE_MODULES) -> dict:
    """逐个真实 import;返回全部失败项 {模块: "错误类型: 错误首行"}(按 modules 顺序),全部成功返回空字典。"""
    errors = {}
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as e:  # noqa: BLE001  ImportError / dlopen 的 OSError / 扩展初始化里的任意错误
            msg = (str(e).splitlines() or [""])[0][:300]
            errors[name] = f"{type(e).__name__}: {msg}"
    return errors


def cached_probe(modules=PROBE_MODULES) -> dict:
    """probe() 的结果按解释器缓存(存全量失败项),返回其中属于 modules 的;签名不变直接复用,缓存读写失败不影响判定。

    旧格式条目(只有首个失败项的 "error" 串)一律重测。
    """
    modules = tuple(modules)
    probed = _probe_set(modules)
    path = _runtime_dir() / CACHE_NAME
    key = _cache_key()
    sig = _signature(probed)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    hit = data.get(key)
    if isinstance(hit, dict) and hit.get("sig") == sig and isinstance(hit.get("errors"), dict):
        errors = hit["errors"]
    else:
        errors = probe(probed)
        first = next(iter(errors.items()), None)
        data[key] = {"sig": sig, "errors": errors,
                     "error": f"{first[0]}: {first[1]}" if first else "",   # 旧版本读的是首个失败项
                     "at": int(time.time())}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{CACHE_NAME}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass
    return {m: str(errors[m]) for m in modules if m in errors}


def _host_probe(host: str, modules: tuple):
    """在宿主解释器里跑 `host_python.py --probe`(带缓存);返回 (失败项字典, "") 或探测不成时 (None, 原因)。

    以脚本路径启动而非裸 -c:裸 -c 会把当前目录(项目目录,有 Agent 写的文件)排进 sys.path 首位,同名 yaml.py 能遮蔽真包;
    环境与真正重跑时一致(PYTHONPATH 等照传)。
    """
    cmd = [host, os.path.abspath(__file__), "--probe", *modules]
    try:
        r = subprocess.run(cmd, env={**os.environ, REEXEC_ENV: "1"}, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                           timeout=HOST_PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"{type(e).__name__}: {e}"
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    try:
        errors = json.loads(lines[-1]) if r.returncode == 0 and lines else None
    except ValueError:
        errors = None
    if not isinstance(errors, dict):
        tail = ((r.stderr or "").strip().splitlines() or [""])[-1][:300]
        return None, f"退出码 {r.returncode}{':' + tail if tail else ''}"
    return {m: str(errors[m]) for m in modules if m in errors}, ""


def _fmt(errors: dict) -> str:
    return ";".join(f"{m}: {e}" for m, e in errors.items())


def ensure_host_python(argv: list | None = None, modules=PROBE_MODULES) -> None:
    """当前解释器不是宿主解释器、modules 有加载不了的、且宿主解释器更好时,用宿主解释器原样重跑并以其退出码退出。

    「更好」= 宿主的失败项是当前失败项的真子集(偏旧的桌面运行时只缺 soundfile,对比全缺的 Homebrew 仍换过去);
    宿主同样差 / 更差 / 探测不成时留在当前解释器,stderr 列出两边失败项。
    不满足条件(未注入 VIDEOAGENTS_PYTHON / 已重跑过 / 宿主解释器不存在 / 已是宿主环境 / 依赖都能加载)时原样返回——
    成员显式用别的健康解释器(如宿主环境本身坏了时绕路)照常跑。
    """
    host = os.environ.get("VIDEOAGENTS_PYTHON", "").strip()
    if not host or os.environ.get(REEXEC_ENV) or not os.path.isfile(host):
        return
    if same_env(host, sys.executable):
        return
    modules = tuple(modules)
    errors = cached_probe(modules)
    if not errors:
        return
    name = Path(sys.argv[0]).name
    host_errors, why = _host_probe(host, modules)
    if host_errors is None or not set(host_errors) < set(errors):
        host_note = f"探测不成({why})" if host_errors is None else f"加载依赖失败({_fmt(host_errors)})"
        print(f"{name}: {sys.executable} 加载依赖失败({_fmt(errors)});宿主解释器 {host} {host_note},"
              f"不比当前好,留在当前解释器运行", file=sys.stderr, flush=True)
        return
    rest = f"(宿主仍缺:{_fmt(host_errors)})" if host_errors else ""
    print(f"{name}: {sys.executable} 加载依赖失败({_fmt(errors)}),改用宿主解释器 {host} 重跑{rest}",
          file=sys.stderr, flush=True)
    sys.stdout.flush()
    args = list(argv if argv is not None else sys.argv)
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    if argv is None and spec is not None and spec.name:   # python3 -m modules.xxx 原样用 -m 重跑
        cmd = [host, "-m", spec.name, *args[1:]]
    else:
        cmd = [host, *args]
    sys.exit(subprocess.call(cmd, env={**os.environ, REEXEC_ENV: "1"}))


if __name__ == "__main__":
    # 宿主侧探测入口(_host_probe 调用):`<解释器> host_python.py --probe [模块...]`,stdout 末行输出失败项 JSON
    if sys.argv[1:2] == ["--probe"]:
        print(json.dumps(cached_probe(tuple(sys.argv[2:]) or PROBE_MODULES), ensure_ascii=False), flush=True)
    else:
        print("usage: host_python.py --probe [module ...]", file=sys.stderr)
        sys.exit(2)

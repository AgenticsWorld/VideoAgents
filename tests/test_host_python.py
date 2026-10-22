"""成员命令的解释器兜底:modules/host_python.py(宿主 CLI 入口换宿主解释器重跑)+ core.host_python_bin_dir(派单 PATH 前置)。

前科 2026-10-22:conda base 的 scipy 1.15.3 在 macOS 27 上 `import scipy` 成功、`import scipy.signal` 因 dlopen 拒绝
扩展而失败,成员写 `python3` 跑代码作曲落到 base 上。这里用 PYTHONPATH 上的桩包模拟「装了但加载不了」,
宿主解释器用转到当前解释器的启动脚本模拟(另一路径 → 不算同一环境)。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import host_python as hp  # noqa: E402
from services.runtime import core  # noqa: E402
from test_run_steer import _READER, _loop_run, _run, _script, rt  # noqa: E402,F401  复用 execute_run 的最小运行环境

posix_only = pytest.mark.skipif(os.name == "nt", reason="posix shebang / 启动脚本")
DLOPEN = "dlopen(_spropack.so): section '__DATA/__thread_bss' has a zero-fill section type, but offset field is not zero"


def _stubs(base: Path, broken: tuple = ()) -> Path:
    """PROBE_MODULES 的桩包;broken 里的模块 import 时抛 dlopen 式 ImportError,并在 stderr 留 PROBED 记号。"""
    for name in hp.PROBE_MODULES:
        parts = name.split(".")
        pkg = base / parts[0]
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").touch()
        f = pkg / (f"{parts[1]}.py" if len(parts) > 1 else "__init__.py")
        f.write_text(f"import sys\nsys.stderr.write('PROBED {name}\\n')\nraise ImportError({DLOPEN!r})\n"
                     if name in broken else "", encoding="utf-8")
    return base


def _host(tmp: Path, broken: tuple = ()) -> Path:
    """模拟宿主解释器:另一路径的启动脚本,调用时记一笔(连同参数)、PYTHONPATH 换成宿主自己的桩包再转到当前解释器。

    broken 是宿主自己加载不了的模块(默认宿主健康);重跑前的宿主探测(host_python.py --probe)也经它走。
    """
    host_stubs = _stubs(tmp / "host_stubs", broken=broken)
    p = tmp / "host" / "python3"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'#!/bin/sh\necho "host $*" >> "{tmp / "host.log"}"\nexport PYTHONPATH="{host_stubs}"\n'
                 f'exec "{sys.executable}" "$@"\n', encoding="utf-8")
    p.chmod(0o755)
    return p


MEMBER = f"""import os, sys
sys.path.insert(0, {str(ROOT / "modules")!r})
from host_python import ensure_host_python
ensure_host_python()
print("RAN", os.environ.get("VIDEOAGENTS_PYTHON_REEXEC"), sys.argv[1:])
"""


def _env(tmp: Path, stubs: Path, host=None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("VIDEOAGENTS_PYTHON", "VIDEOAGENTS_PYTHON_REEXEC", "VIDEOAGENTS_DATA_DIR")}
    env.update(PYTHONPATH=str(stubs), VIDEOAGENTS_RUNTIME_DIR=str(tmp / "rt"))
    if host:
        env["VIDEOAGENTS_PYTHON"] = str(host)
    return env


def _member(tmp: Path, env: dict, *args):
    script = tmp / "member.py"
    script.write_text(MEMBER, encoding="utf-8")
    return subprocess.run([sys.executable, str(script), *args], env=env, capture_output=True, text=True, timeout=60)


def _host_lines(tmp: Path) -> list:
    log = tmp / "host.log"
    return log.read_text().splitlines() if log.exists() else []


def _host_calls(tmp: Path) -> int:
    """真正换到宿主解释器重跑的次数(不含重跑前的宿主探测)。"""
    return sum(1 for ln in _host_lines(tmp) if " --probe" not in ln)


def _host_probes(tmp: Path) -> int:
    return sum(1 for ln in _host_lines(tmp) if " --probe" in ln)


def _key(stubs: Path) -> str:
    return f"{os.path.abspath(sys.executable)}|PYTHONPATH={stubs}"


@posix_only
def test_broken_dependency_reexecs_with_host_and_caches(tmp_path):
    stubs = _stubs(tmp_path / "stubs", broken=("scipy.signal",))
    env = _env(tmp_path, stubs, _host(tmp_path))
    r = _member(tmp_path, env, "--ep", "ep01")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["RAN 1 ['--ep', 'ep01']"]          # 只在宿主解释器上跑了一次,参数原样
    assert _host_calls(tmp_path) == 1
    assert "加载依赖失败(scipy.signal: ImportError: dlopen" in r.stderr and "改用宿主解释器" in r.stderr
    cache = json.loads((tmp_path / "rt" / hp.CACHE_NAME).read_text(encoding="utf-8"))
    assert list(cache[_key(stubs)]["errors"]) == ["scipy.signal"]                  # 存全部失败项
    assert cache[_key(stubs)]["errors"]["scipy.signal"].startswith("ImportError: dlopen")
    assert cache[_key(tmp_path / "host_stubs")]["errors"] == {}                    # 宿主探测由宿主进程按自己的签名写
    assert _host_probes(tmp_path) == 1
    # 第二次命中缓存:不再真 import 探测,直接换解释器
    r = _member(tmp_path, env)
    assert r.stdout.splitlines() == ["RAN 1 []"] and _host_calls(tmp_path) == 2
    assert "PROBED" not in r.stderr


@posix_only
def test_healthy_interpreter_runs_in_place_and_reprobes_after_reinstall(tmp_path):
    stubs = _stubs(tmp_path / "stubs")
    env = _env(tmp_path, stubs, _host(tmp_path))
    r = _member(tmp_path, env)
    assert r.stdout.splitlines() == ["RAN None []"] and _host_calls(tmp_path) == 0   # 显式用别的健康解释器照常跑
    _stubs(stubs, broken=("numpy",))                                               # 包被改过 → 签名变 → 重测
    r = _member(tmp_path, env)
    assert "PROBED numpy" in r.stderr and r.stdout.splitlines() == ["RAN 1 []"] and _host_calls(tmp_path) == 1


@posix_only
def test_noop_without_host_or_when_already_host(tmp_path):
    stubs = _stubs(tmp_path / "stubs", broken=("numpy",))
    r = _member(tmp_path, _env(tmp_path, stubs))                                   # 未注入:不探测
    assert r.stdout.splitlines() == ["RAN None []"] and "PROBED" not in r.stderr
    r = _member(tmp_path, _env(tmp_path, stubs, sys.executable))                   # 已是宿主环境:不探测
    assert r.stdout.splitlines() == ["RAN None []"] and "PROBED" not in r.stderr
    env = {**_env(tmp_path, stubs, _host(tmp_path)), "VIDEOAGENTS_PYTHON_REEXEC": "1"}
    r = _member(tmp_path, env)                                                     # 已重跑过:不再换
    assert r.stdout.splitlines() == ["RAN 1 []"] and _host_calls(tmp_path) == 0
    assert not (tmp_path / "rt" / hp.CACHE_NAME).exists()


@posix_only
def test_dash_m_invocation_reexecs_with_dash_m(tmp_path):
    stubs = _stubs(tmp_path / "stubs", broken=("yaml",))
    pkg = tmp_path / "cwd"
    pkg.mkdir()
    (pkg / "member_mod.py").write_text(MEMBER.replace('print("RAN"', 'print(__spec__.name, "RAN"'), encoding="utf-8")
    env = _env(tmp_path, stubs, _host(tmp_path))
    r = subprocess.run([sys.executable, "-m", "member_mod", "x"], cwd=pkg, env=env, capture_output=True, text=True, timeout=60)
    assert r.stdout.splitlines() == ["member_mod RAN 1 ['x']"], r.stderr


@posix_only
def test_worse_or_equal_host_keeps_current_interpreter(tmp_path):
    """#160:成员换了只缺 Playwright 的环境,宿主自己 scipy.signal 加载不了 → 不换(换过去必败);两边一样差也不换。"""
    stubs = _stubs(tmp_path / "stubs", broken=("playwright",))
    env = _env(tmp_path, stubs, _host(tmp_path, broken=("scipy.signal",)))
    r = _member(tmp_path, env, "x")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["RAN None ['x']"] and _host_calls(tmp_path) == 0
    assert "留在当前解释器运行" in r.stderr and "改用宿主解释器" not in r.stderr
    assert "加载依赖失败(playwright: ImportError" in r.stderr and "加载依赖失败(scipy.signal: ImportError" in r.stderr
    r = _member(tmp_path, env)                                                     # 两边都命中缓存:不再真 import
    assert r.stdout.splitlines() == ["RAN None []"] and "PROBED" not in r.stderr
    assert _host_probes(tmp_path) == 2 and _host_calls(tmp_path) == 0

    tmp2 = tmp_path / "same"
    stubs = _stubs(tmp2 / "stubs", broken=("soundfile",))
    r = _member(tmp2, _env(tmp2, stubs, _host(tmp2, broken=("soundfile",))))
    assert r.stdout.splitlines() == ["RAN None []"] and _host_calls(tmp2) == 0 and "留在当前解释器运行" in r.stderr


@posix_only
def test_better_host_still_reexecs(tmp_path):
    """宿主失败项是当前失败项的真子集就换:偏旧的桌面运行时只缺 soundfile,对比全缺的 Homebrew 仍换过去。"""
    stubs = _stubs(tmp_path / "stubs", broken=("numpy", "scipy.signal", "soundfile", "PIL.Image", "yaml"))
    env = _env(tmp_path, stubs, _host(tmp_path, broken=("soundfile",)))
    r = _member(tmp_path, env, "--ep", "ep01")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["RAN 1 ['--ep', 'ep01']"] and _host_calls(tmp_path) == 1
    assert "加载依赖失败(numpy: ImportError" in r.stderr and ";soundfile: ImportError" in r.stderr
    assert "改用宿主解释器" in r.stderr and "宿主仍缺:soundfile: ImportError" in r.stderr


@posix_only
def test_genmedia_ignores_missing_playwright(tmp_path):
    """genmedia 不开无头浏览器:只缺 Playwright 的解释器照常跑,不换也不探测宿主;缓存仍记全量失败项供 code/ 入口用。"""
    stubs = _stubs(tmp_path / "stubs", broken=("playwright",))
    env = _env(tmp_path, stubs, _host(tmp_path))
    r = subprocess.run([sys.executable, str(ROOT / "modules" / "genmedia.py"), "--help"],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "usage" in r.stdout, r.stderr
    assert "加载依赖失败" not in r.stderr and "改用宿主解释器" not in r.stderr
    assert _host_lines(tmp_path) == []
    cache = json.loads((tmp_path / "rt" / hp.CACHE_NAME).read_text(encoding="utf-8"))
    assert list(cache[_key(stubs)]["errors"]) == ["playwright"]
    r = _member(tmp_path, env)                                                     # 全量判定的入口照旧换
    assert r.stdout.splitlines() == ["RAN 1 []"] and _host_calls(tmp_path) == 1


@posix_only
def test_same_env_tells_venv_link_from_base(tmp_path):
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python3").symlink_to(sys.executable)
    assert hp.same_env(sys.executable, sys.executable)
    assert not hp.same_env(str(venv_bin / "python3"), sys.executable)   # 同一二进制、不同环境
    assert not hp.same_env(str(tmp_path / "missing"), sys.executable)


@posix_only
def test_code_cli_reexecs_on_common_import(tmp_path):
    """code/ 下的 CLI import _common 即兜底(此前只在缺 Playwright 时、且只有少数入口显式调用)。"""
    stubs = _stubs(tmp_path / "stubs", broken=("scipy.signal",))
    env = _env(tmp_path, stubs, _host(tmp_path))
    r = subprocess.run([sys.executable, str(ROOT / "code" / "check_nsfw_hint.py"), "--help"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "usage" in r.stdout and "改用宿主解释器" in r.stderr and _host_calls(tmp_path) == 1


@posix_only
def test_host_python_bin_dir_scripts(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "HOST_PYTHON_BIN_DIR", tmp_path / "bin")
    d = core.host_python_bin_dir()
    assert d == tmp_path / "bin" and sorted(p.name for p in d.iterdir()) == ["python", "python3"]
    for name in ("python3", "python"):
        out = subprocess.run([str(d / name), "-c", "import sys; print(sys.executable)"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        assert out == os.path.abspath(sys.executable)
    mtime = (d / "python3").stat().st_mtime_ns
    assert core.host_python_bin_dir() == d and (d / "python3").stat().st_mtime_ns == mtime   # 内容没变不重写
    env = core.with_host_python_path({"PATH": os.pathsep.join(["/a", str(d), "/b"])})
    assert env["PATH"] == os.pathsep.join([str(d), "/a", "/b"])


FAKE_CLAUDE_PY = _READER + r'''
out({"type": "system", "subtype": "init", "session_id": "sess-py"})
read_line()
r = subprocess.run(["python3", "-c", "import sys; print(sys.executable)"], capture_output=True, text=True)
log("python3", r.stdout.strip())
out({"type": "result", "result": "OK", "session_id": "sess-py", "total_cost_usd": 0, "num_turns": 1})
while read_line():
    pass
'''


@posix_only
def test_dispatch_env_puts_host_python_first(rt, monkeypatch, tmp_path):
    monkeypatch.setattr(core, "HOST_PYTHON_BIN_DIR", tmp_path / "host-bin")
    monkeypatch.setattr(core, "resolve_cli_executable",
                        lambda e: _script(rt.tmp, "claude", "import subprocess\n" + FAKE_CLAUDE_PY))
    monkeypatch.setenv("PATH", os.pathsep.join([str(_host(tmp_path).parent), os.environ["PATH"]]))  # 用户 PATH 上另有 python3
    run = _run("claude")
    _loop_run(core.execute_run(run, "TASK", None))
    assert run["status"] == "done", run
    assert [e["text"] for e in rt.log() if e["kind"] == "python3"] == [os.path.abspath(sys.executable)]
    assert _host_calls(tmp_path) == 0

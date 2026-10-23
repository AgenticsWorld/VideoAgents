"""Agent 删除守卫(services/runtime/delete_guard.py + core.delete_guard_* + pi_delete_guard.mjs 接线)。

前科 2026-10-22:pi 把 @videoagents 手误写成 @videoAgents,APFS 不分大小写、write 回执原样回显错写法,
模型以为多出一棵「重复目录树」,`rm -rf '…/Application Support/@videoAgents' 2>/dev/null || true`
删光整个桌面端数据目录。这里用 tmp 里的同构目录重放。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from services.runtime import delete_guard as dg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.runtime import core  # noqa: E402
from test_run_steer import _READER, _loop_run, _run, _script, rt  # noqa: E402,F401  复用 execute_run 的最小运行环境

posix_only = pytest.mark.skipif(os.name == "nt", reason="posix shebang / sh 垫片")
SCRIPT = Path(dg.__file__)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """<tmp>/AppSup/@videoagents/desktop/{data/projects/{jiangwu,other},python-runtimes},受保护目录经环境变量给出。"""
    user = tmp_path / "AppSup" / "@videoagents" / "desktop"
    data = user / "data"
    proj = data / "projects" / "jiangwu"
    (proj / "runs" / "r1").mkdir(parents=True)
    (data / "projects" / "other").mkdir()
    (data / ".videoagents").mkdir()
    (user / "python-runtimes").mkdir()
    (proj / "brief.md").write_text("b", encoding="utf-8")
    (proj / "runs" / "r1" / "eval.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv(dg.ENV_ROOTS, os.pathsep.join([str(data), str(data / ".videoagents")]))
    monkeypatch.setenv(dg.ENV_CONTAINERS, str(data / "projects"))
    return {"tmp": tmp_path, "user": user, "data": data, "proj": proj}


def _case_insensitive(p: Path) -> bool:
    return (p.parent / p.name.swapcase()).exists()


def _hook(tool, inp, cwd="/"):
    return dg.hook({"tool": tool, "input": inp, "cwd": cwd})


def test_incident_command_blocked_and_explains_same_dir(tree):
    if not _case_insensitive(tree["user"].parent):
        pytest.skip("文件系统区分大小写:@videoAgents 是另一个目录,不构成事故")
    bad = tree["tmp"] / "AppSup" / "@videoAgents"
    out = _hook("bash", {"command": f"rm -rf '{bad}' 2>/dev/null || true; echo after"})
    assert out["block"] is True
    assert "@videoagents" in out["reason"] and "不区分大小写" in out["reason"]
    assert tree["proj"].is_dir()


def test_write_path_case_is_corrected(tree):
    if not _case_insensitive(tree["user"].parent):
        pytest.skip("文件系统区分大小写")
    bad = tree["tmp"] / "AppSup" / "@videoAgents" / "desktop" / "data" / "projects" / "jiangwu" / "runs" / "r1" / "eval.json"
    assert _hook("write", {"path": str(bad)}) == {"path": str(tree["proj"] / "runs" / "r1" / "eval.json")}
    assert _hook("write", {"path": str(tree["proj"] / "new" / "x.json")}) == {}   # 新文件、写法正确:不改
    assert _hook("read", {"path": "brief.md"}, cwd=str(tree["proj"])) == {}


@pytest.mark.parametrize("command", [
    "rm -rf {data}",
    "rm -r {data}/../",                      # 上级目录
    "rm -rf {user}",                         # userData 本身(数据目录的上级)
    "/bin/rm -rf {proj}",                    # 绝对路径 rm 绕过 PATH 垫片也照拦
    "cd {data}/projects && rm -rf ./jiangwu/",
    "cd {proj} && rm -rf *",                 # 清空项目
    "sudo rm -fr -- {data}/projects",
    "find {proj} -mindepth 1 -delete",
    "find {data} -type d -exec rm -rf {{}} +",
    "python3 -c \"import shutil; shutil.rmtree('{proj}')\"",
    "python3 - <<'PY'\nimport shutil\nshutil.rmtree(r'{data}')\nPY",
])
def test_bash_literal_deletes_blocked(tree, command):
    out = _hook("bash", {"command": command.format(**{k: str(v) for k, v in tree.items()})})
    assert out.get("block") is True, command


@pytest.mark.parametrize("command", [
    "rm -rf {proj}/runs/r1",                 # 项目里的子目录照常可删
    "rm -f {proj}/brief.md",
    "cd {proj} && rm -rf runs",
    "rm -rf /tmp/va-nonexistent && ls {data}",   # 只是提到受保护目录
    "find {proj} -name '*.tmp' -delete",     # 带条件的 find
    "D={proj}; rm -rf \"$D\"",               # 变量留给运行时的 rm 垫片
    "rm -rf {data}/projects/jiangwu-old",    # 不存在的同前缀目录
    "echo 'rm -rf {data}' > note.txt",
])
def test_bash_allowed(tree, command):
    assert _hook("bash", {"command": command.format(**{k: str(v) for k, v in tree.items()})}) == {}, command


def test_symlink_itself_is_not_protected_but_trailing_slash_is(tree):
    link = tree["tmp"] / "link"
    link.symlink_to(tree["data"])
    assert _hook("bash", {"command": f"rm {link}"}) == {}            # 只删链接
    assert _hook("bash", {"command": f"rm -rf {link}/"})["block"] is True


def test_powershell_remove_item(tree):
    assert _hook("powershell", {"command": f"Remove-Item -Recurse -Force '{tree['data']}'"})["block"] is True
    assert _hook("powershell", {"command": f"Remove-Item -Recurse '{tree['proj'] / 'runs'}'"}) == {}


def test_no_roots_is_noop(tree, monkeypatch):
    monkeypatch.delenv(dg.ENV_ROOTS)
    monkeypatch.delenv(dg.ENV_CONTAINERS)
    assert _hook("bash", {"command": f"rm -rf {tree['data']}"}) == {}


def test_shim_mode_checks_expanded_operands(tree):
    env = {**os.environ}
    def rm(*args, cwd=tree["proj"]):
        return subprocess.run([sys.executable, "-I", "-S", str(SCRIPT), "rm", "/bin/rm", *args],
                              cwd=cwd, env=env, capture_output=True, text=True)
    r = rm("-rf", "brief.md", "runs")       # `rm -rf *` 在 shell 里展开后的样子
    assert r.returncode == 1 and "已拒绝清空" in r.stderr
    assert (tree["proj"] / "brief.md").exists() and (tree["proj"] / "runs" / "r1").is_dir()
    r = rm("-rf", str(tree["data"] / "projects" / "other"), "runs/r1")   # 一个受保护即整条不删
    assert r.returncode == 1 and (tree["proj"] / "runs" / "r1").is_dir()
    assert rm("-rf", "runs/r1").returncode == 0 and not (tree["proj"] / "runs").joinpath("r1").exists()


@posix_only
def test_core_shim_blocks_runtime_deletes(tree, monkeypatch, tmp_path):
    monkeypatch.setattr(core, "DELETE_GUARD_BIN_DIR", tmp_path / "guard-bin")
    monkeypatch.setattr(core, "DATA_DIR", tree["data"])
    monkeypatch.setattr(core, "RUNTIME_DIR", tree["data"] / ".videoagents")
    monkeypatch.setattr(core, "PROJECTS_DIR", tree["data"] / "projects")
    monkeypatch.setenv("VIDEOAGENTS_SNAPSHOT_DIR", str(tmp_path / "snap"))
    env = core.delete_guard_env({**os.environ})
    assert env["PATH"].split(os.pathsep)[0] == str(tmp_path / "guard-bin")
    roots = env[dg.ENV_ROOTS].split(os.pathsep)
    assert str(tree["data"]) in roots and str(Path.home()) in roots

    def sh(cmd):
        return subprocess.run(["/bin/bash", "-c", cmd], cwd=tree["proj"], env=env, capture_output=True, text=True)
    r = sh(f'X="{tree["data"]}"; rm -rf "$X" 2>/dev/null || true; echo rc=$?')
    assert tree["proj"].is_dir() and tree["user"].joinpath("python-runtimes").is_dir()
    r = sh("rm -rf *")
    assert r.returncode == 1 and "已拒绝清空" in r.stderr and (tree["proj"] / "brief.md").exists()
    r = sh("touch a b && rm a b && rm -rf runs/r1 && echo ok")   # 只删文件走快路径,子目录照常
    assert r.stdout.strip() == "ok" and not (tree["proj"] / "a").exists() and not (tree["proj"] / "runs" / "r1").exists()
    shim = (tmp_path / "guard-bin" / "rm").read_text(encoding="utf-8")
    assert "VideoAgents 删除守卫" in shim and str(SCRIPT) in shim


def test_real_rm_skips_other_guard_shims(tmp_path, monkeypatch):
    other = tmp_path / "other-instance-bin"
    other.mkdir()
    (other / "rm").write_text("#!/bin/sh\n# VideoAgents 删除守卫(另一个实例)\n", encoding="utf-8")
    (other / "rm").chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(other), os.environ["PATH"]]))
    assert core._real_rm() != str(other / "rm")


def test_pi_extension_wired_for_both_pi_engines():
    assert core.delete_guard_pi_args() == ["-e", str(core.DELETE_GUARD_PI_EXTENSION)]
    src = core.DELETE_GUARD_PI_EXTENSION.read_text(encoding="utf-8")
    assert '"tool_call"' in src and "VIDEOAGENTS_GUARD_SCRIPT" in src
    assert core.DELETE_GUARD_SCRIPT == SCRIPT
    # 桌面端打包 services/** 整目录,扩展与守卫脚本随包
    pkg = json.loads((Path(core.ROOT) / "apps" / "desktop" / "package.json").read_text(encoding="utf-8"))
    entry = next(e for e in pkg["build"]["extraResources"] if e.get("to") == "backend/services")
    assert "**/*" in entry["filter"]


FAKE_CLAUDE_ENV = _READER + r'''
import subprocess
out({"type": "system", "subtype": "init", "session_id": "sess-g"})
read_line()
log("path0", os.environ["PATH"].split(os.pathsep)[0])
log("roots", os.environ.get("VIDEOAGENTS_GUARD_ROOTS", ""))
r = subprocess.run(["/bin/bash", "-c", 'rm -rf "$VIDEOAGENTS_PROJECT_ROOT"'], capture_output=True, text=True)
log("rm", r.stderr)
out({"type": "result", "result": "OK", "session_id": "sess-g", "total_cost_usd": 0, "num_turns": 1})
while read_line():
    pass
'''


@posix_only
def test_dispatch_env_has_guard_and_snapshot(rt, monkeypatch, tmp_path):
    proj = core.PROJECTS_DIR / "demo"
    (proj / "bible").mkdir(parents=True)
    (proj / "bible" / "style.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(core, "project_prompt_path", lambda p: str(core.PROJECTS_DIR / p))
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: _script(rt.tmp, "claude", FAKE_CLAUDE_ENV))
    run = _run("claude")
    _loop_run(core.execute_run(run, "TASK", None))
    assert run["status"] == "done", run
    log = {e["kind"]: e["text"] for e in rt.log()}
    assert log["path0"] == str(tmp_path / "delete-guard-bin")
    assert str(core.PROJECTS_DIR) in log["roots"] or str(core.DATA_DIR) in log["roots"]
    assert "已拒绝删除" in log["rm"] and (proj / "bible" / "style.json").exists()
    snaps = list((tmp_path / "snapshots").glob("*/demo/*"))
    assert len(snaps) == 1 and (snaps[0] / "bible" / "style.json").read_text(encoding="utf-8") == "{}"

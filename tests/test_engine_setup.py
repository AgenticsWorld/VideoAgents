"""执行引擎 CLI 一键安装 / 登录引导(services/runtime/engine_setup.py + core / API 接线)。"""
import asyncio
import json
import os
import shlex
import stat
import sys
import time
from pathlib import Path

import pytest

from services.runtime import engine_setup as es

posix_only = pytest.mark.skipif(os.name == "nt", reason="posix shell")


@pytest.fixture(autouse=True)
def _clean_jobs():
    es.reset_jobs()
    yield
    es.reset_jobs()


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    for k in ("XDG_DATA_HOME", "KIMI_CODE_HOME", "XAI_API_KEY", "GROK_DEPLOYMENT_KEY", "ANTHROPIC_API_KEY",
              "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return h


def _exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _wait(engine: str, timeout: float = 20) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        snap = es.install_snapshot(engine)
        if snap and snap["state"] != "running":
            return snap
        time.sleep(0.05)
    raise AssertionError("install job did not finish")


# ---------------------------------------------------------------- 安装命令
def test_install_plans_use_official_commands(monkeypatch):
    monkeypatch.setattr(es.shutil, "which", lambda name: "/usr/bin/" + name)
    expect = {
        "claude": "curl -fsSL https://claude.ai/install.sh | bash",
        "codex": "curl -fsSL https://chatgpt.com/codex/install.sh | sh",
        "kimi": "curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash",
        "pi": "curl -fsSL https://pi.dev/install.sh | sh",
        "opencode": "curl -fsSL https://opencode.ai/install | bash",
        "grok": "curl -fsSL https://x.ai/cli/install.sh | bash",
    }
    for engine, line in expect.items():
        plan = es.install_plan(engine, windows=False)
        assert plan["display"] == line and plan["terminal_line"] == line
        assert plan["argv"] == ["/bin/bash", "-o", "pipefail", "-c", line]   # pipefail:curl 失败不被 | bash 吞掉
    assert es.install_plan("codex", windows=False)["env"] == {"CODEX_NON_INTERACTIVE": "1"}
    assert es.install_plan("nope", windows=False) == {"unsupported": "unknown_engine"}


def test_install_plans_windows(monkeypatch):
    monkeypatch.setattr(es.shutil, "which", lambda name: None)
    claude = es.install_plan("claude", windows=True)
    assert claude["display"] == "irm https://claude.ai/install.ps1 | iex"
    assert "-NonInteractive" in claude["argv"] and claude["argv"][-1] == claude["display"]
    assert "-NonInteractive" not in claude["terminal_line"]          # 终端里跑:允许脚本提问
    assert es.install_plan("opencode", windows=True) == {"unsupported": "need_node"}   # 无官方 ps1、无 npm
    monkeypatch.setattr(es.shutil, "which", lambda name: r"C:\node\npm.cmd" if name == "npm" else None)
    oc = es.install_plan("opencode", windows=True)
    assert oc["argv"] == [r"C:\node\npm.cmd", "install", "-g", "opencode-ai"]
    assert oc["terminal_line"].startswith("call ")


def test_install_plan_needs_curl(monkeypatch):
    monkeypatch.setattr(es.shutil, "which", lambda name: None)
    assert es.install_plan("claude", windows=False) == {"unsupported": "need_curl"}


def test_installer_env_strips_secrets():
    env = es.installer_env({"CODEX_NON_INTERACTIVE": "1"}, base={
        "PATH": "/usr/bin", "HOME": "/h", "HTTPS_PROXY": "http://127.0.0.1:6152",
        "VIDEOAGENTS_USER_JWT": "jwt", "VIDEOAGENTS_PORT": "8630", "OPENAI_API_KEY": "sk",
        "GITHUB_TOKEN": "gh", "AWS_SECRET_ACCESS_KEY": "aws", "ARK_API_KEY": "ark"})
    assert env["PATH"] == "/usr/bin" and env["HTTPS_PROXY"] == "http://127.0.0.1:6152"
    assert env["CODEX_NON_INTERACTIVE"] == "1" and env["NO_COLOR"] == "1"
    for k in ("VIDEOAGENTS_USER_JWT", "VIDEOAGENTS_PORT", "OPENAI_API_KEY", "GITHUB_TOKEN",
              "AWS_SECRET_ACCESS_KEY", "ARK_API_KEY"):
        assert k not in env


def test_clean_line_and_diagnose():
    assert es.clean_line("\x1b[1;32m✔ done\x1b[0m\n") == "✔ done"
    assert es.clean_line("10%\r50%\r100%\r\n") == "100%"
    assert es.diagnose("curl: (22) The requested URL returned error: 403", 22, "claude") == "region"
    assert es.diagnose("curl: (22) The requested URL returned error: 403", 22, "kimi") == "network"
    assert es.diagnose("curl: (6) Could not resolve host: claude.ai", 6) == "network"
    assert es.diagnose("error: Node.js 22.19.0 or newer is required to install Pi.", 1) == "node"
    assert es.diagnose("something else broke", 1) == ""


# ---------------------------------------------------------------- 后台安装任务
@posix_only
def test_install_job_success_streams_log_and_resolves(tmp_path):
    exe = _exe(tmp_path / "bin" / "fakecli", "exit 0")
    plan = {"argv": ["/bin/sh", "-c", "printf '\\033[32mstep 1\\033[0m\\n'; printf 'a\\rb\\n'; echo done"],
            "display": "fake install", "env": {}}
    snap = es.start_install("claude", lambda: str(exe), plan=plan)
    assert snap["state"] == "running" and snap["command"] == "fake install"
    snap = _wait("claude")
    assert snap["state"] == "ok" and snap["exit_code"] == 0 and snap["path"] == str(exe)
    assert snap["log"] == ["step 1", "b", "done"]


@posix_only
def test_install_job_failure_hint_and_not_found(tmp_path):
    plan = {"argv": ["/bin/sh", "-c", "echo 'curl: (7) Failed to connect to claude.ai'; exit 7"],
            "display": "x", "env": {}}
    es.start_install("claude", lambda: None, plan=plan)
    snap = _wait("claude")
    assert (snap["state"], snap["reason"], snap["hint"], snap["exit_code"]) == ("error", "exit", "network", 7)
    es.start_install("kimi", lambda: None, plan={"argv": ["/bin/sh", "-c", "true"], "display": "x", "env": {}})
    assert _wait("kimi")["reason"] == "not_found_after_install"


@posix_only
def test_install_job_no_tty_no_secrets_and_no_duplicate(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEOAGENTS_USER_JWT", "secret-jwt")
    marker = tmp_path / "ran"
    script = ("if ( : <>/dev/tty ) 2>/dev/null; then echo TTY; else echo NOTTY; fi; "
              f"echo jwt=[$VIDEOAGENTS_USER_JWT] extra=[$EXTRA]; echo x >> {shlex.quote(str(marker))}; sleep 0.5")
    plan = {"argv": ["/bin/sh", "-c", script], "display": "x", "env": {"EXTRA": "1"}}
    es.start_install("codex", lambda: "/bin/sh", plan=plan)
    again = es.start_install("codex", lambda: "/bin/sh", plan=plan)       # 在装:返回同一任务,不重复起
    assert again["state"] == "running"
    snap = _wait("codex")
    assert snap["log"][:2] == ["NOTTY", "jwt=[] extra=[1]"]
    assert "tmpdir" not in snap
    assert marker.read_text().count("x") == 1


@posix_only
def test_install_job_timeout_and_stall_kill_process_group(monkeypatch, tmp_path):
    monkeypatch.setattr(es, "WATCH_INTERVAL_S", 0.2)
    plan = {"argv": ["/bin/sh", "-c", "echo start; sleep 30"], "display": "x", "env": {}}
    es.start_install("grok", lambda: None, timeout_s=1, plan=plan)
    snap = _wait("grok", timeout=15)
    assert snap["reason"] == "timeout" and snap["elapsed_s"] < 10
    es.start_install("codex", lambda: None, stall_s=1, plan=plan)       # 无输出、无下载增长
    snap = _wait("codex", timeout=15)
    assert snap["reason"] == "stalled" and snap["elapsed_s"] < 10


@posix_only
def test_install_progress_without_newlines_is_not_a_stall(monkeypatch):
    # \r 进度动画(不换行)持续有输出:不算卡住
    monkeypatch.setattr(es, "WATCH_INTERVAL_S", 0.2)
    script = "i=0; while [ $i -lt 15 ]; do printf '.'; sleep 0.2; i=$((i+1)); done; echo; echo ok"
    es.start_install("kimi", lambda: "/bin/sh", stall_s=1,
                     plan={"argv": ["/bin/sh", "-c", script], "display": "x", "env": {}})
    snap = _wait("kimi", timeout=20)
    assert snap["state"] == "ok" and snap["log"][-1] == "ok"


@posix_only
def test_download_growth_is_not_a_stall(monkeypatch, home):
    monkeypatch.setattr(es, "WATCH_INTERVAL_S", 0.2)
    target = home / ".claude/downloads/claude-bin"
    script = (f"mkdir -p {shlex.quote(str(target.parent))}; i=0; while [ $i -lt 12 ]; do "
              f"printf 'xxxxxxxx' >> {shlex.quote(str(target))}; sleep 0.25; i=$((i+1)); done")
    es.start_install("claude", lambda: "/bin/sh", stall_s=1,
                     plan={"argv": ["/bin/sh", "-c", script], "display": "x", "env": {}})
    snap = _wait("claude", timeout=20)
    assert snap["state"] == "ok"


def test_unsupported_plan_fails_immediately():
    snap = es.start_install("opencode", lambda: None, plan={"unsupported": "need_node"})
    assert (snap["state"], snap["reason"], snap["hint"]) == ("error", "need_node", "node")


# ---------------------------------------------------------------- 登录状态
@posix_only
def test_login_status_claude_and_codex_commands(tmp_path):
    claude_in = _exe(tmp_path / "c1", 'echo \'{"loggedIn": true, "authMethod": "claude.ai"}\'')
    claude_out = _exe(tmp_path / "c2", 'echo \'{"loggedIn": false, "authMethod": "none"}\'; exit 1')
    assert es.login_status("claude", str(claude_in)) == {"logged_in": True, "method": "claude.ai"}
    assert es.login_status("claude", str(claude_out))["logged_in"] is False
    codex_in = _exe(tmp_path / "x1", 'echo "Logged in using ChatGPT"')
    codex_out = _exe(tmp_path / "x2", 'echo "Not logged in"; exit 1')
    codex_odd = _exe(tmp_path / "x3", "exit 3")
    assert es.login_status("codex", str(codex_in)) == {"logged_in": True, "method": "Logged in using ChatGPT"}
    assert es.login_status("codex", str(codex_out))["logged_in"] is False
    assert es.login_status("codex", str(codex_odd))["logged_in"] is None       # 判断不了不打扰
    assert es.login_status("claude", str(tmp_path / "missing"))["logged_in"] is None


def test_login_status_credential_files(home, monkeypatch):
    for engine in ("kimi", "pi", "opencode", "grok"):
        assert es.login_status(engine, "/x")["logged_in"] is False, engine
    (home / ".kimi-code/credentials").mkdir(parents=True)
    (home / ".kimi-code/credentials/kimi-code.json").write_text(json.dumps({"refresh_token": "r", "expires_at": 1}))
    (home / ".pi/agent").mkdir(parents=True)
    (home / ".pi/agent/auth.json").write_text(json.dumps({"openai-codex": {"type": "oauth"}}))
    (home / ".local/share/opencode").mkdir(parents=True)
    (home / ".local/share/opencode/auth.json").write_text(json.dumps({"opencode-go": {"type": "api"}}))
    (home / ".grok").mkdir()
    (home / ".grok/auth.json").write_text(json.dumps({"https://auth.x.ai::u": {"key": "k", "refresh_token": "r"}}))
    assert es.login_status("kimi", "/x") == {"logged_in": True, "method": "oauth"}
    assert es.login_status("pi", "/x") == {"logged_in": True, "method": "openai-codex"}
    assert es.login_status("opencode", "/x") == {"logged_in": True, "method": "opencode-go"}
    assert es.login_status("grok", "/x") == {"logged_in": True, "method": "oauth"}


def test_login_status_alternatives_are_unknown_or_true(home, monkeypatch):
    (home / ".kimi-code").mkdir()
    (home / ".kimi-code/config.toml").write_text('[providers."my"]\napi_key = "sk-x"\n')
    assert es.login_status("kimi", "/x")["logged_in"] is None      # 改用自定义渠道 Key:不打扰
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert es.login_status("pi", "/x")["logged_in"] is None
    monkeypatch.setenv("XAI_API_KEY", "k")
    assert es.login_status("grok", "/x") == {"logged_in": True, "method": "api_key"}
    (home / "data/opencode").mkdir(parents=True)
    (home / "data/opencode/auth.json").write_text('{"x": {}}')
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
    assert es.login_status("opencode", "/x")["logged_in"] is True


# ---------------------------------------------------------------- 兜底路径 / core 接线
@posix_only
def test_fallback_executable_and_core_resolve(home, monkeypatch):
    from services.runtime import core
    assert es.fallback_executable("claude") is None
    exe = _exe(home / ".local/bin/claude", "exit 0")
    _exe(home / ".grok/bin/grok", "exit 0")
    assert es.fallback_executable("claude") == str(exe)
    assert es.fallback_executable("grok") == str(home / ".grok/bin/grok")
    monkeypatch.setenv("PATH", "/nonexistent")
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.setitem(core.CLI_BINS, "claude", "claude")
    assert core.resolve_cli_executable("claude") == str(exe)       # PATH 找不到 → 官方默认位置
    monkeypatch.setenv("CLAUDE_BIN", "/custom/claude")               # 用户显式指定路径:不兜底
    monkeypatch.setitem(core.CLI_BINS, "claude", "/custom/claude")
    assert core.resolve_cli_executable("claude") is None


@posix_only
def test_api_enginecheck_reports_login(home, monkeypatch):
    from services.runtime import core
    exe = _exe(home / ".local/bin/codex", 'echo "Not logged in"; exit 1')
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: str(exe))
    r = asyncio.run(core.api_enginecheck("codex"))
    assert r["available"] and r["logged_in"] is False and r["label"] == "Codex CLI"
    assert r["install_command"].endswith("codex/install.sh | sh") and r["install"] is None
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: None)
    r = asyncio.run(core.api_enginecheck("codex"))
    assert not r["available"] and r["logged_in"] is None
    assert asyncio.run(core.api_enginecheck("deepagents"))["available"] is True


def test_api_engine_login_and_terminal_install(monkeypatch, tmp_path):
    from services.runtime import core
    opened = []
    monkeypatch.setattr(core, "ENGINE_SETUP_DIR", tmp_path)
    monkeypatch.setattr(es, "open_terminal", lambda d, name, body, windows=es.IS_WIN, env=None:
                        opened.append((d, name, body, env)) or d / name)
    monkeypatch.setattr(core, "ui_lang_code", lambda cfg=None: "en")
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: None)
    with pytest.raises(core.ServiceError) as ei:
        asyncio.run(core.api_engine_login("kimi"))
    assert ei.value.status_code == 409
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: "/opt/kimi/bin/kimi")
    assert asyncio.run(core.api_engine_login("kimi"))["opened"]
    _, name, body, env = opened[-1]
    assert name == "login-kimi" and "Sign in to Kimi Code" in body and "mainland-cn" in body
    assert "kimi login" in body and env and "VIDEOAGENTS_USER_JWT" not in env
    assert asyncio.run(core.api_engine_install("claude", {"mode": "terminal"}))["opened"]
    assert opened[-1][1] == "install-claude" and "claude.ai/install" in opened[-1][2]
    with pytest.raises(core.ServiceError) as ei:
        asyncio.run(core.api_engine_install("deepagents", {}))
    assert ei.value.status_code == 404


# ---------------------------------------------------------------- 终端脚本
@posix_only
def test_terminal_script_posix_runs(tmp_path):
    body = es.terminal_script(["Line 'one'", "第二行"], "(echo RAN; exit 3)", zh=False, windows=False,
                              path_dirs=["/opt/x bin"])
    assert body.startswith("#!/bin/bash") and "read -r -p" in body
    path = tmp_path / "t.sh"
    path.write_text(body.replace("clear\n", "").replace("read -r -p", "true || read -r -p"))
    import subprocess
    out = subprocess.run(["/bin/bash", str(path)], capture_output=True, text=True, timeout=10).stdout
    assert "Line 'one'" in out and "第二行" in out and "RAN" in out
    assert "exited with code 3" in out


def test_terminal_script_windows_escapes():
    body = es.terminal_script(["a & b (c) 100%"], 'call "C:\\x\\kimi.exe" login', zh=True, windows=True,
                              path_dirs=[r"C:\x"])
    lines = body.split("\r\n")
    assert lines[0] == "@echo off" and "chcp 65001 >nul" in lines
    assert "echo a ^& b ^(c^) 100%%" in lines
    assert 'call "C:\\x\\kimi.exe" login' in lines and "pause" in lines
    assert any(ln.startswith("if %CODE%==0 (") and "%CODE%" in ln for ln in lines)


def test_login_command_lines():
    assert es.login_command_line("claude", "/a b/claude", windows=False) == "'/a b/claude' auth login"
    assert es.login_command_line("pi", "/x/pi", windows=False) == "/x/pi"
    assert es.login_command_line("opencode", r"C:\npm\opencode.cmd", windows=True) == r"call C:\npm\opencode.cmd auth login"
    lines = es.login_lines("pi", "Pi Coding Agent", zh=True)
    assert any("/login" in ln for ln in lines)


# ---------------------------------------------------------------- API:仅本机
def _req(client_host: str, xff: str | None = None):
    from starlette.requests import Request
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers,
                    "client": (client_host, 1234), "query_string": b""})


def test_local_only_gate(monkeypatch):
    from services.api import app as api_app
    from services.runtime import core
    assert api_app._is_local_request(_req("127.0.0.1"))
    assert api_app._is_local_request(_req("127.0.0.1", "127.0.0.1"))
    assert not api_app._is_local_request(_req("127.0.0.1", "192.168.1.20"))   # 经代理的局域网请求
    assert not api_app._is_local_request(_req("192.168.1.20"))
    called = []
    monkeypatch.setattr(core, "api_engine_login", lambda e: called.append(e) or asyncio.sleep(0, {"ok": True}))
    assert asyncio.run(api_app.engine_login("claude", _req("::1"), {})) == {"ok": True}
    with pytest.raises(core.ServiceError) as ei:
        asyncio.run(api_app.engine_login("claude", _req("127.0.0.1", "10.0.0.5"), {}))
    assert ei.value.status_code == 403 and called == ["claude"]


def test_install_route_rejects_testclient():
    from fastapi.testclient import TestClient
    from services.api import app as api_app
    client = TestClient(api_app.app, raise_server_exceptions=False)
    r = client.post("/api/v1/engines/claude/install", json={"mode": "background"})
    assert r.status_code == 403 and r.json()["detail"] == "local_only"
    assert client.get("/api/v1/engines/claude/install").json() == {"engine": "claude", "install": None}


def test_downloaded_bytes_tracks_newest_file_since_start(home, tmp_path):
    assert es.downloaded_bytes("kimi", 0) is None                  # 无下载目录、无临时目录
    kt = tmp_path / "ktmp" / "tmp.X"
    kt.mkdir(parents=True)
    (kt / "kimi-code.tar.gz").write_bytes(b"x" * 4096)
    assert es.downloaded_bytes("kimi", time.time() - 5, str(tmp_path / "ktmp")) == 4096
    d = home / ".claude/downloads"
    d.mkdir(parents=True)
    old = d / "claude-old"
    old.write_bytes(b"x" * 10)
    os.utime(old, (1, 1))
    start = time.time()
    assert es.downloaded_bytes("claude", start) == 0               # 旧文件不算本次
    (d / "claude-2.1.295-darwin-arm64").write_bytes(b"x" * 2048)
    assert es.downloaded_bytes("claude", start) == 2048


@posix_only
def test_snapshot_reports_quiet_and_timeout(tmp_path):
    plan = {"argv": ["/bin/sh", "-c", "echo hi; sleep 1.2"], "display": "x", "env": {}}
    es.start_install("claude", lambda: None, timeout_s=60, plan=plan)
    time.sleep(1.0)
    snap = es.install_snapshot("claude")
    assert snap["state"] == "running" and snap["timeout_s"] == 60 and snap["quiet_s"] >= 0.5
    assert "downloaded_bytes" in snap and "last_output" not in snap


@posix_only
def test_install_job_private_tmpdir_removed_after(tmp_path):
    out = tmp_path / "tmpdir.txt"
    plan = {"argv": ["/bin/sh", "-c", f'echo "$TMPDIR" > {shlex.quote(str(out))}; mktemp -d >/dev/null; sleep 0.3'],
            "display": "x", "env": {}}
    es.start_install("kimi", lambda: None, plan=plan)
    _wait("kimi")
    used = out.read_text().strip()
    assert "videoagents-install-kimi-" in used and not Path(used).exists()


@posix_only
def test_install_job_finishes_when_orphan_holds_pipe(tmp_path):
    # 脚本自己退出了,但留下的后台进程还占着 stdout:任务仍应在数秒内收尾
    plan = {"argv": ["/bin/sh", "-c", "echo before; (sleep 20 &) ; echo after"], "display": "x", "env": {}}
    t0 = time.time()
    es.start_install("pi", lambda: "/bin/sh", plan=plan)
    snap = _wait("pi", timeout=15)
    assert snap["state"] == "ok" and time.time() - t0 < 12
    assert snap["log"][:2] == ["before", "after"]

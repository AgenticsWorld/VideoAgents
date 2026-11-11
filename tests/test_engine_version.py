"""执行引擎 CLI 版本检查 + 一键更新(services/runtime/engine_version.py + core / API 接线)。"""
import asyncio
import json
import os
import stat
import time
from pathlib import Path

import pytest

from services.runtime import engine_setup as es
from services.runtime import engine_version as ev

posix_only = pytest.mark.skipif(os.name == "nt", reason="posix shell")


@pytest.fixture(autouse=True)
def _clean():
    es.reset_jobs()
    ev.reset_cache()
    ev._NPM_PREFIX.clear()
    yield
    es.reset_jobs()
    ev.reset_cache()


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return h


def _exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _wait(engine: str, timeout: float = 20) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        snap = ev.update_snapshot(engine)
        if snap and snap["state"] != "running":
            return snap
        time.sleep(0.05)
    raise AssertionError("update job did not finish")


# ---------------------------------------------------------------- 版本号
def test_parse_version_real_outputs():
    # 2026-11-11 本机各 CLI `--version` 实际输出
    assert ev.parse_version("2.1.322 (Claude Code)") == "2.1.322"
    assert ev.parse_version("codex-cli 0.166.0") == "0.166.0"
    assert ev.parse_version("2.4.0\n") == "2.4.0"
    assert ev.parse_version("opencode v2.1.2") == "2.1.2"
    assert ev.parse_version("1.18.23") == "1.18.23"
    assert ev.parse_version("grok 1.0.77 (50ca579fa996)") == "1.0.77"
    assert ev.parse_version("1.5.5") == "1.5.5"
    assert ev.parse_version("v1.0.0-beta.2") == "1.0.0-beta.2"
    assert ev.parse_version("no version here") == "" and ev.parse_version(None) == ""


def test_version_compare():
    assert ev.is_outdated("0.99.2", "1.5.2") and ev.is_outdated("0.166.0", "0.167.1")
    assert ev.is_outdated("1.0.9", "1.0.10")                   # 按数字比,不按字符串
    assert ev.is_outdated("1.0.0-beta.2", "1.0.0")             # 预发布低于正式版
    assert ev.is_outdated("1.0.0-beta.2", "1.0.0-beta.10")
    assert ev.is_outdated("1.0.0-1", "1.0.0-alpha")            # 数字段 < 字母段
    assert not ev.is_outdated("2.1.322", "2.1.322")
    assert not ev.is_outdated("2.1.322", "2.1.311")            # 本机比渠道新(如切回 stable)不提示
    assert not ev.is_outdated("0.0.0-beta-20261001", "2.2.7")  # 开发构建比不了
    assert not ev.is_outdated("", "1.0.0") and not ev.is_outdated("1.0.0", "")


# ---------------------------------------------------------------- 最新版本
def _fake_http(monkeypatch, table: dict):
    calls = []

    def fake(url):
        calls.append(url)
        v = table.get(url)
        if v is None or isinstance(v, Exception):
            raise v or OSError("unreachable")
        return v if isinstance(v, str) else json.dumps(v)
    monkeypatch.setattr(ev, "_http_text", fake)
    return calls


def test_latest_sources_per_engine(home, monkeypatch):
    plat = ev.antigravity_platform()
    calls = _fake_http(monkeypatch, {
        "https://downloads.claude.ai/claude-code-releases/latest": "2.1.322\n",
        "https://releases.openai.com/codex/channels/latest": {"tag_name": "rust-v0.167.1", "assets": []},
        "https://code.kimi.com/kimi-code/latest": "2.5.3\n",
        "https://pi.dev/api/installer/releases/latest": {"schemaVersion": 1, "version": "1.5.2"},
        "https://opencode.ai/update/api/latest/cli/npm": {"channel": "latest", "version": "2.2.7"},
        "https://registry.npmjs.org/opencode-ai/latest": {"version": "1.19.5"},
        "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/"
        f"{plat}.json": {"version": "1.5.5", "url": "x", "sha512": "y"},
    })
    got = {e: ev.latest_version(e, "/x/" + e, "1.0.0" if e != "opencode" else "2.1.2")["latest"]
           for e in ("claude", "codex", "kimi", "pi", "opencode", "antigravity")}
    assert got == {"claude": "2.1.322", "codex": "0.167.1", "kimi": "2.5.3", "pi": "1.5.2",
                   "opencode": "2.2.7", "antigravity": "1.5.5"}
    r = ev.latest_version("opencode", "/x/opencode", "1.18.23")     # 1.x 线看 npm opencode-ai
    assert (r["latest"], r["channel"]) == ("1.19.5", "v1")
    assert calls[-1] == "https://registry.npmjs.org/opencode-ai/latest"


def test_claude_channel_from_settings_and_fallback(home, monkeypatch):
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps({"autoUpdatesChannel": "stable"}))
    calls = _fake_http(monkeypatch, {
        "https://registry.npmjs.org/-/package/@anthropic-ai/claude-code/dist-tags":
            {"stable": "2.1.311", "latest": "2.1.322"},
    })
    r = ev.latest_version("claude", "/x/claude")
    assert (r["latest"], r["channel"], r["error"]) == ("2.1.311", "stable", "")
    assert calls[0].endswith("/claude-code-releases/stable")     # 首选失败,换 npm dist-tags
    assert ev.check.__name__ and not ev.is_outdated("2.1.311", r["latest"])


def test_latest_cache_failure_and_refresh(home, monkeypatch):
    table = {}
    calls = _fake_http(monkeypatch, table)
    r = ev.latest_version("kimi", "/x/kimi")
    assert r["latest"] == "" and r["error"]
    ev.latest_version("kimi", "/x/kimi")                       # 失败结果 15 分钟内不重查
    assert len(calls) == 1
    table["https://code.kimi.com/kimi-code/latest"] = "2.5.3"
    assert ev.latest_version("kimi", "/x/kimi", refresh=True)["latest"] == "2.5.3"
    ev.latest_version("kimi", "/x/kimi")
    assert len(calls) == 2
    monkeypatch.setattr(ev, "LATEST_FAIL_TTL_S", 0)
    monkeypatch.setattr(ev, "LATEST_TTL_S", 0)
    ev.latest_version("kimi", "/x/kimi")
    assert len(calls) == 3


@posix_only
def test_grok_uses_cli_check_then_http_fallback(home, tmp_path, monkeypatch):
    grok = _exe(tmp_path / "grok", """case "$1 $2 $3" in
  "update --check --json") echo '{"currentVersion":"1.0.70","latestVersion":"1.0.80-alpha.1","updateAvailable":true,"channel":"alpha","error":null}';;
  "--version  ") echo "grok 1.0.70 (deadbeef)";;
esac""")
    calls = _fake_http(monkeypatch, {"https://x.ai/cli/stable": "1.0.77\n"})
    r = ev.check("grok", str(grok))
    assert r == {"installed": "1.0.70", "latest": "1.0.80-alpha.1", "channel": "alpha",
                 "outdated": True, "error": ""}
    assert calls == []
    broken = _exe(tmp_path / "grok2", 'echo "grok 1.0.70"; exit 2')
    ev.reset_cache()
    r = ev.check("grok", str(broken))
    assert (r["latest"], r["channel"], r["outdated"]) == ("1.0.77", "stable", True)


def test_check_without_version_or_network(home, monkeypatch):
    monkeypatch.setattr(ev, "installed_version", lambda exe: "")
    assert ev.check("kimi", "/x/kimi")["error"] == "no_version"
    monkeypatch.setattr(ev, "installed_version", lambda exe: "2.4.0")
    _fake_http(monkeypatch, {})
    r = ev.check("kimi", "/x/kimi")
    assert r["installed"] == "2.4.0" and r["error"] and r["outdated"] is False   # 断网不提示


# ---------------------------------------------------------------- 更新命令
def test_update_plans_posix(monkeypatch):
    want = {"claude": "claude update", "codex": "codex update", "kimi": "kimi upgrade --yes",
            "pi": "pi update --self", "opencode": "opencode upgrade", "grok": "grok update",
            "antigravity": "agy update"}
    bins = {"antigravity": "agy"}
    for e, disp in want.items():
        exe = f"/home/u/.local/bin/{bins.get(e, e)}"
        p = ev.update_plan(e, exe, windows=False)
        assert (p["display"], p["method"]) == (disp, "self")
        assert p["argv"][0] == exe and p["terminal_line"].startswith(exe)
    assert ev.update_plan("codex", "/x/codex", windows=False)["env"] == {"CODEX_NON_INTERACTIVE": "1"}
    assert ev.update_plan("deepagents", "/x", windows=False) == {"unsupported": "unknown_engine"}


def test_update_plans_windows_npm_shim(tmp_path, monkeypatch):
    npm_dir = tmp_path / "AppData" / "Roaming" / "npm"
    npm_dir.mkdir(parents=True)
    shim = npm_dir / "codex.cmd"
    shim.write_text('@ECHO off\r\n"%dp0%\\node_modules\\@openai\\codex\\bin\\codex.js" %*\r\n')
    npm = r"C:\Program Files\nodejs\npm.cmd"
    monkeypatch.setattr(ev.shutil, "which", lambda name: npm if name == "npm" else None)
    monkeypatch.setattr(ev, "_npm_global_prefix", lambda n: str(npm_dir))
    p = ev.update_plan("codex", str(shim), windows=True)
    assert p["method"] == "npm" and p["display"] == "npm install -g @openai/codex@latest"
    assert p["argv"] == [npm, "install", "-g", "@openai/codex@latest"]
    assert p["terminal_line"].startswith("call ") and "@openai/codex@latest" in p["terminal_line"]
    oc = npm_dir / "opencode.cmd"
    oc.write_text("node_modules\\opencode-ai\\bin\\opencode")
    assert ev.update_plan("opencode", str(oc), windows=True, installed="1.18.23")["display"] \
        == "npm install -g opencode-ai@latest"
    assert ev.update_plan("opencode", str(oc), windows=True, installed="2.1.2")["display"] \
        == "npm install -g @opencode/cli@latest"
    # pi 官方脚本的托管安装(不在 npm 全局目录)→ pi 自己的 update
    managed = tmp_path / ".pi" / "agent" / "bin" / "pi.cmd"
    managed.parent.mkdir(parents=True)
    managed.write_text("node_modules\\@earendil-works\\pi-coding-agent\\dist\\cli.js")
    p = ev.update_plan("pi", str(managed), windows=True)
    assert p["method"] == "self" and p["display"] == "pi update --self"
    assert p["terminal_line"].startswith("call ")
    # 原生 exe(claude 官方安装)→ 自带 update
    p = ev.update_plan("claude", r"C:\Users\u\.local\bin\claude.exe", windows=True)
    assert (p["method"], p["display"]) == ("self", "claude update")
    # 找不到 npm → 退回 CLI 自带命令
    monkeypatch.setattr(ev.shutil, "which", lambda name: None)
    assert ev.update_plan("codex", str(shim), windows=True)["method"] == "self"


# ---------------------------------------------------------------- 一键更新任务
def _versioned_cli(tmp_path: Path, start: str, update_body: str) -> Path:
    (tmp_path / "ver").write_text(start)
    return _exe(tmp_path / "bin" / "claude", f"""D=$(dirname "$0")/..
case "$1" in
  --version) echo "$(cat "$D/ver") (Claude Code)";;
  update) {update_body};;
esac""")


@posix_only
def test_update_job_ok_unchanged_and_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEOAGENTS_USER_JWT", "secret-jwt")
    exe = _versioned_cli(tmp_path, "2.1.300",
                         'echo "jwt=[$VIDEOAGENTS_USER_JWT]"; echo "Updating to 2.1.322"; echo 2.1.322 > "$D/ver"')
    plan = ev.update_plan("claude", str(exe), windows=False)
    snap = ev.start_update("claude", plan, lambda: str(exe), "2.1.300")
    assert snap["state"] == "running" and snap["command"] == "claude update" and "key" not in snap
    snap = _wait("claude")
    assert (snap["state"], snap["version"], snap["previous"]) == ("ok", "2.1.322", "2.1.300")
    assert snap["log"] == ["jwt=[]", "Updating to 2.1.322"]
    assert es.install_snapshot("claude") is None                 # 与一键安装任务互不干扰

    es.reset_jobs()
    exe = _versioned_cli(tmp_path, "2.1.300", 'echo "Claude Code is managed by Homebrew. Run: brew upgrade"')
    ev.start_update("claude", ev.update_plan("claude", str(exe), windows=False), lambda: str(exe), "2.1.300")
    snap = _wait("claude")
    assert (snap["state"], snap["reason"], snap["version"]) == ("error", "unchanged", "2.1.300")
    assert "brew upgrade" in snap["log"][-1]

    es.reset_jobs()
    exe = _versioned_cli(tmp_path, "2.1.300", "echo 'curl: (7) Failed to connect'; exit 7")
    ev.start_update("claude", ev.update_plan("claude", str(exe), windows=False), lambda: str(exe), "2.1.300")
    snap = _wait("claude")
    assert (snap["state"], snap["reason"], snap["hint"], snap["exit_code"]) == ("error", "exit", "network", 7)


def test_on_success_exception_ends_job(tmp_path):
    def boom():
        raise RuntimeError("x")
    import sys
    es.start_job("update:kimi", "kimi", {"argv": [sys.executable, "-c", "pass"], "display": "x", "env": {}}, boom)
    snap = _wait("kimi")
    assert (snap["state"], snap["reason"]) == ("error", "check_failed")


# ---------------------------------------------------------------- core / API
class _Proc:
    returncode = None


def test_api_engine_version_and_busy(monkeypatch, tmp_path):
    from services.runtime import core
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: "/opt/bin/kimi")
    monkeypatch.setattr(ev, "check", lambda e, exe, refresh=False: {
        "installed": "2.4.0", "latest": "2.5.3", "channel": "latest", "outdated": True, "error": ""})
    monkeypatch.setitem(core.RUNS, "r1", {"engine": "kimi"})
    monkeypatch.setitem(core.RUN_PROCS, "r1", _Proc())
    monkeypatch.setattr(ev, "BUSY_BLOCKS", False)
    r = asyncio.run(core.api_engine_version("kimi"))
    assert (r["installed"], r["latest"], r["outdated"]) == ("2.4.0", "2.5.3", True)
    assert r["update_command"] == "kimi upgrade --yes" and r["update"] is None
    assert (r["busy_runs"], r["busy_blocks"], r["label"]) == (1, False, "Kimi Code")

    started = []
    monkeypatch.setattr(ev, "installed_version", lambda exe: "2.4.0")
    monkeypatch.setattr(ev, "start_update", lambda e, plan, resolve, before:
                        started.append((e, plan["display"], before)) or {"state": "running"})
    r = asyncio.run(core.api_engine_update("kimi", {}))          # macOS:有任务在跑也允许
    assert r["update"] == {"state": "running"} and started == [("kimi", "kimi upgrade --yes", "2.4.0")]

    monkeypatch.setattr(ev, "BUSY_BLOCKS", True)                 # Windows:有任务在用 → 拒绝
    assert asyncio.run(core.api_engine_version("kimi"))["busy_blocks"] is True
    with pytest.raises(core.ServiceError) as ei:
        asyncio.run(core.api_engine_update("kimi", {}))
    assert (ei.value.status_code, ei.value.detail) == (409, "busy")
    core.RUN_PROCS["r1"].returncode = 0                          # 任务结束 → 放行
    assert asyncio.run(core.api_engine_version("kimi"))["busy_blocks"] is False
    assert asyncio.run(core.api_engine_update("kimi", {}))["mode"] == "background"

    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: None)
    r = asyncio.run(core.api_engine_version("kimi"))
    assert r["available"] is False and r["outdated"] is False
    with pytest.raises(core.ServiceError) as ei:
        asyncio.run(core.api_engine_update("kimi", {}))
    assert ei.value.status_code == 409
    with pytest.raises(core.ServiceError) as ei:
        asyncio.run(core.api_engine_version("deepagents"))
    assert ei.value.status_code == 404


def test_api_engine_update_terminal(monkeypatch, tmp_path):
    from services.runtime import core
    opened = []
    monkeypatch.setattr(core, "ENGINE_SETUP_DIR", tmp_path)
    monkeypatch.setattr(es, "open_terminal", lambda d, name, body, windows=es.IS_WIN, env=None:
                        opened.append((name, body, env)) or d / name)
    monkeypatch.setattr(core, "ui_lang_code", lambda cfg=None: "en")
    monkeypatch.setattr(core, "resolve_cli_executable", lambda e: "/opt/codex/bin/codex")
    monkeypatch.setattr(ev, "installed_version", lambda exe: "0.166.0")
    monkeypatch.setattr(ev, "BUSY_BLOCKS", False)
    r = asyncio.run(core.api_engine_update("codex", {"mode": "terminal"}))
    assert r["opened"] and r["previous"] == "0.166.0"
    name, body, env = opened[-1]
    assert name == "update-codex" and "Update Codex CLI" in body and "codex update" in body
    assert env["CODEX_NON_INTERACTIVE"] == "1" and "VIDEOAGENTS_USER_JWT" not in env


def test_update_route_local_only():
    from fastapi.testclient import TestClient
    from services.api import app as api_app
    client = TestClient(api_app.app, raise_server_exceptions=False)
    r = client.post("/api/v1/engines/claude/update", json={"mode": "background"})
    assert r.status_code == 403 and r.json()["detail"] == "local_only"
    assert client.get("/api/v1/engines/claude/update").json() == {"engine": "claude", "update": None}


def test_version_route_reports_local_backend(monkeypatch):
    from fastapi.testclient import TestClient
    from services.api import app as api_app
    from services.runtime import core
    seen = []

    async def fake(engine, refresh=False):
        seen.append((engine, refresh))
        return {"engine": engine, "outdated": False}
    monkeypatch.setattr(core, "api_engine_version", fake)
    client = TestClient(api_app.app, raise_server_exceptions=False)
    r = client.get("/api/v1/engines/grok/version?refresh=true")
    assert r.status_code == 200 and r.json()["local_backend"] is False and seen == [("grok", True)]

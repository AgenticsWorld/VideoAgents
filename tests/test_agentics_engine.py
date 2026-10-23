"""Agentics 引擎(engine id deepagents = 随安装包打包的 pi):启动命令解析、启动前检查、
按「语言模型/Agentics」生效渠道生成 pi 配置目录、引擎检测接口、素材库看图分支。"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI_REL = Path("node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js")


@pytest.fixture
def core():
    # 在用例里才 import:模块级 import 会在收集阶段就定死 core.DATA_DIR,
    # 让后面靠环境变量指定数据目录的测试(test_post_apply 等)找不到项目
    from services.runtime import core
    return core


@pytest.fixture
def vendor(core, tmp_path, monkeypatch):
    """假的 vendor/pi:launch.mjs + cli.js 都在,VIDEOAGENTS_NODE 指向一个固定路径。"""
    d = tmp_path / "vendor-pi"
    (d / CLI_REL).parent.mkdir(parents=True)
    (d / CLI_REL).write_text("// cli\n")
    (d / "launch.mjs").write_text("// launcher\n")
    monkeypatch.setattr(core, "AGENTICS_PI_DIR", d)
    monkeypatch.setattr(core, "AGENTICS_PI_HOME", tmp_path / "agentics-pi")
    monkeypatch.setattr(core, "AGENTICS_PI_SESSIONS", tmp_path / "agentics-pi" / "sessions")
    monkeypatch.setenv("VIDEOAGENTS_NODE", "/opt/VideoAgents/VideoAgents")
    monkeypatch.setattr(core, "ui_lang_code", lambda cfg=None: "zh")
    return d


def _channel(core, monkeypatch, provider, model="m-1", api_key="k-secret", base_url="https://example.test/v1"):
    da = {"provider": provider, "base_url": base_url, "api_key": api_key, "model": model}
    monkeypatch.setattr(core, "resolve_deepagents", lambda cfg=None: dict(da))
    return da


def test_command_needs_vendor_files_and_node(core, vendor, monkeypatch):
    assert core.agentics_pi_command() == ["/opt/VideoAgents/VideoAgents", str(vendor / "launch.mjs")]
    monkeypatch.delenv("VIDEOAGENTS_NODE")
    monkeypatch.setattr(core.shutil, "which", lambda name: None)
    assert core.agentics_pi_command() is None                 # 源码版 PATH 上没有 node
    monkeypatch.setattr(core.shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None)
    assert core.agentics_pi_command()[0] == "/usr/bin/node"
    (vendor / CLI_REL).unlink()
    assert core.agentics_pi_command() is None                 # 没 make install-pi


def test_preflight_reasons(core, vendor, monkeypatch):
    _channel(core, monkeypatch, "agentics", model="deepseek/deepseek-v4.1-flash-1017")
    pre = core.agentics_preflight()
    assert pre["reason"] == "" and pre["model"] == "deepseek/deepseek-v4.1-flash-1017"
    assert core.agentics_preflight("anthropic/claude-sonnet-5")["model"] == "anthropic/claude-sonnet-5"
    assert core.agentics_preflight("local")["model"] == "deepseek/deepseek-v4.1-flash-1017"   # 渠道占位值不当模型

    def no_login(cfg=None):
        raise core.ServiceError(401, "AgenticsLLM requires a signed-in desktop account")
    monkeypatch.setattr(core, "resolve_deepagents", no_login)
    pre = core.agentics_preflight()
    assert pre["reason"] == "login" and "登录" in pre["error"] and "本地模型" in pre["error"]

    _channel(core, monkeypatch, "cloud", api_key="")
    pre = core.agentics_preflight()
    assert pre["reason"] == "key" and "云端模型" in pre["error"]
    _channel(core, monkeypatch, "local", model="")
    assert core.agentics_preflight()["reason"] == "model"

    shutil.rmtree(vendor)
    _channel(core, monkeypatch, "agentics")
    pre = core.agentics_preflight()
    assert pre["reason"] == "missing" and "make install-pi" in pre["error"]
    monkeypatch.setattr(core, "ui_lang_code", lambda cfg=None: "en")
    assert core.agentics_preflight()["error"].startswith("The built-in Agentics engine (pi) is missing")


def test_launch_agentics_channel_writes_openrouter_compat_without_secret(core, vendor, tmp_path):
    da = {"provider": "agentics", "base_url": "https://api.agentics.world/wrapper/openrouter/api/v1",
          "api_key": "jwt-secret", "model": "anthropic/claude-sonnet-5"}
    home = tmp_path / "run1"
    args, env = core.agentics_pi_launch(da["model"], da, home, thinking=True)
    assert args[-4:] == ["--provider", "agentics", "--model", "anthropic/claude-sonnet-5"]
    for flag in ("--mode", "--approve", "--no-extensions", "--no-skills", "--no-prompt-templates",
                 "--no-context-files"):
        assert flag in args
    models = json.loads((home / "models.json").read_text())
    prov = models["providers"]["agentics"]
    assert prov["baseUrl"] == da["base_url"] and prov["api"] == "openai-completions"
    assert prov["apiKey"] == "$VIDEOAGENTS_LLM_API_KEY"
    m = prov["models"][0]
    assert m["id"] == da["model"] and m["reasoning"] is True and m["input"] == ["text"]
    assert m["compat"]["thinkingFormat"] == "openrouter"
    assert m["compat"]["cacheControlFormat"] == "anthropic" and m["compat"]["supportsDeveloperRole"] is True
    on_disk = "".join(p.read_text() for p in home.iterdir())
    assert "jwt-secret" not in on_disk                                    # Key 只经环境变量传入
    assert env["VIDEOAGENTS_LLM_API_KEY"] == "jwt-secret" and env["ELECTRON_RUN_AS_NODE"] == "1"
    assert env["PI_CODING_AGENT_DIR"] == str(home)
    assert env["PI_CODING_AGENT_SESSION_DIR"] == str(core.AGENTICS_PI_SESSIONS)
    assert env["PI_OFFLINE"] == "1" and env["PI_TELEMETRY"] == "0"
    assert json.loads((home / "settings.json").read_text())["enableInstallTelemetry"] is False


def test_launch_local_and_cloud_channels(core, vendor, tmp_path, monkeypatch):
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    local = {"provider": "local", "base_url": "http://192.168.1.20:1234/v1", "api_key": "lm-studio", "model": "qwen3"}
    args, env = core.agentics_pi_launch("qwen3", local, tmp_path / "l", thinking=True, vision=True)
    m = json.loads((tmp_path / "l" / "models.json").read_text())["providers"]["local"]["models"][0]
    assert m["reasoning"] is False and m["maxTokens"] == 8192 and "compat" not in m   # 本地端点不发推理参数
    assert m["input"] == ["text", "image"]
    assert "192.168.1.20" in env["NO_PROXY"].split(",") and env["no_proxy"] == env["NO_PROXY"]
    cloud = {"provider": "cloud", "base_url": "https://api.deepseek.com", "api_key": "sk-x", "model": "deepseek-v4-flash"}
    monkeypatch.setenv("AGENTICS_MAX_OUTPUT_TOKENS", "32768")
    args, env = core.agentics_pi_launch("deepseek-v4-flash", cloud, tmp_path / "c", thinking=False)
    m = json.loads((tmp_path / "c" / "models.json").read_text())["providers"]["cloud"]["models"][0]
    assert m["reasoning"] is False and m["maxTokens"] == 32768 and "NO_PROXY" not in env
    assert args[-3:] == ["cloud", "--model", "deepseek-v4-flash"]


def test_effort_args(core):
    assert core.engine_effort_args("deepagents", "high") == ["--thinking", "high"]
    assert core.engine_effort_args("deepagents", "high", local_endpoint=True) == []
    assert core.engine_effort_args("deepagents", "") == []


def test_enginecheck_reports_agentics_login(core, vendor, monkeypatch):
    def no_login(cfg=None):
        raise core.ServiceError(401, "AgenticsLLM requires a signed-in desktop account")
    monkeypatch.setattr(core, "resolve_deepagents", no_login)
    r = asyncio.run(core.api_enginecheck("deepagents"))
    assert r["available"] is True and r["logged_in"] is False and r["reason"] == "login"
    assert r["label"] == "Agentics" and r["provider"] == "agentics"
    _channel(core, monkeypatch, "agentics")
    r = asyncio.run(core.api_enginecheck("deepagents"))
    assert r["logged_in"] is True and r["reason"] == "" and r["error"] == ""
    _channel(core, monkeypatch, "local")
    assert asyncio.run(core.api_enginecheck("deepagents"))["logged_in"] is None     # 本地渠道不涉及登录
    shutil.rmtree(vendor)
    r = asyncio.run(core.api_enginecheck("deepagents"))
    assert r["available"] is False and r["reason"] == "missing"


def test_footage_spec_prepares_pi_only_for_analysis(core, vendor, monkeypatch):
    _channel(core, monkeypatch, "agentics", model="google/gemini-3-flash")
    monkeypatch.setattr(core, "global_model_pref", lambda: {"engine": "deepagents", "model": ""})
    spec = core.footage_engine_spec()
    assert spec["engine"] == "deepagents" and spec["model"] == "google/gemini-3-flash"
    assert "agentics" not in spec and not (core.AGENTICS_PI_HOME / "footage").exists()
    spec = core.footage_engine_spec(prepare=True)
    assert spec["agentics"]["cmd"][:2] == core.agentics_pi_command()
    assert spec["agentics"]["env"]["VIDEOAGENTS_LLM_API_KEY"] == "k-secret"
    m = json.loads((core.AGENTICS_PI_HOME / "footage" / "models.json").read_text())
    assert m["providers"]["agentics"]["models"][0]["input"] == ["text", "image"]     # 看图


def test_footage_vision_runs_pi_command(tmp_path, monkeypatch):
    from modules import footage_library as fl
    seen = {}

    def fake_run(cmd, cwd, env, input, capture_output, timeout):
        seen.update(cmd=cmd, env=env)
        out = json.dumps({"type": "message_end", "message": {"role": "assistant",
                                                             "content": [{"type": "text", "text": "两人对峙"}]}})
        return subprocess.CompletedProcess(cmd, 0, out.encode(), b"")
    monkeypatch.setattr(fl.subprocess, "run", fake_run)
    img = tmp_path / "sheet.jpg"
    img.write_bytes(b"x")
    spec = {"engine": "deepagents", "model": "m", "executable": None,
            "agentics": {"cmd": ["/node", "/launch.mjs", "--provider", "agentics", "--model", "m"],
                         "env": {"VIDEOAGENTS_LLM_API_KEY": "k"}}}
    r = fl.run_engine_vision(img, "看看这张图", spec, tmp_path)
    assert r["text"] == "两人对峙"
    assert seen["cmd"][-2:] == ["--no-session", "看看这张图"] and seen["env"]["VIDEOAGENTS_LLM_API_KEY"] == "k"
    with pytest.raises(fl.FootageLibError) as ei:
        fl.run_engine_vision(img, "p", {"engine": "deepagents", "agentics": {"error": "请先登录"}}, tmp_path)
    assert ei.value.status == 400 and "请先登录" in ei.value.detail


@pytest.mark.skipif(not (ROOT / "vendor/pi" / CLI_REL).is_file() or not shutil.which("node"),
                    reason="vendor/pi 未安装(make install-pi)或没有 node")
def test_bundled_pi_version_matches_pin():
    pinned = json.loads((ROOT / "vendor/pi/package.json").read_text())["dependencies"]["@earendil-works/pi-coding-agent"]
    env = {**os.environ, "PI_OFFLINE": "1", "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0", "NO_COLOR": "1"}
    out = subprocess.run([shutil.which("node"), str(ROOT / "vendor/pi/launch.mjs"), "--version"],
                         capture_output=True, text=True, env=env, timeout=60, stdin=subprocess.DEVNULL)
    assert out.returncode == 0 and out.stdout.strip().splitlines()[-1] == pinned


def test_desktop_packaging_ships_vendor_pi():
    pkg = json.loads((ROOT / "apps/desktop/package.json").read_text())
    extra = {x.get("to"): x for x in pkg["build"]["extraResources"]}
    assert "launch.mjs" in extra["backend/vendor/pi"]["filter"]
    # electron-builder 会从 extraResources 里剔除 node_modules 子目录(filter 写了也不行,2026-10-22
    # 打包实测),必须以 node_modules 本身为源单列一条
    assert extra["backend/vendor/pi/node_modules"]["from"] == "../../vendor/pi/node_modules"
    main = (ROOT / "apps/desktop/src/main.ts").read_text(encoding="utf-8")
    # macOS 用 Electron Helper 当 Node 跑 pi(不进 Dock,273e691c),其余平台退回 process.execPath
    assert "VIDEOAGENTS_NODE: agenticsNodeExecutable()" in main
    assert "return existsSync(helper) ? helper : process.execPath" in main
    lock = (ROOT / "apps/desktop/runtime-requirements.lock").read_text()
    assert "deepagents" not in lock and "langgraph" not in lock          # 原 deepagents 栈已移出运行时
    assert not (ROOT / "modules/deepagents_runner.py").exists()
    workflow = (ROOT / ".github/workflows/desktop.yaml").read_text()
    assert "vendor_pi.py --prune" in workflow

import asyncio

import pytest

from services.api.__main__ import DEFAULT_HOST
from services.runtime import core as server
from services.runtime import dispatch


def test_public_release_security_defaults():
    assert DEFAULT_HOST == "127.0.0.1"
    assert server.PERMISSION_MODE == "acceptEdits"
    assert server.CLAUDE_USAGE_PROBE_ENABLED is False
    assert server._claude_oauth_token() is None


def test_agent_catalog_is_available():
    agents = asyncio.run(server.api_agents(refresh=True))
    core_agents = [agent for agent in agents if not agent.get("plugin")]
    assert len(core_agents) == 83
    assert {agent["id"] for agent in agents} >= {
        "00-orchestration/workflow-orchestrator",
        "07-directing/director",
        "11-qa/copyright",
        "13-derivative-fiction/prose-writer",
        "14-fusion/fusion-planner",
    }


def test_official_plugins_are_available():
    plugins = server.list_plugins(refresh=True)
    by_name = {plugin["name"]: plugin for plugin in plugins}
    assert by_name["derivative-fiction"]["active"]
    assert by_name["fusion-fiction"]["active"]
    assert by_name["derivative-fiction"]["builtin"]
    assert server.agent_dir("13-derivative-fiction/prose-writer")
    assert server.is_stateless_agent("11-qa/prose-qa")


def test_agent_path_validation_rejects_traversal():
    with pytest.raises(server.ServiceError):
        server.safe_agent("../server")


def test_project_prompt_path_uses_persistent_projects_dir(tmp_path, monkeypatch):
    persistent_projects = tmp_path / "persistent data" / "projects"
    monkeypatch.setattr(server, "PROJECTS_DIR", persistent_projects)

    assert server.project_prompt_path("guitusaopao") == (
        persistent_projects / "guitusaopao"
    ).resolve().as_posix()


def test_kill_proc_tree_uses_taskkill_on_windows(monkeypatch):
    calls = []

    class Process:
        pid = 1234

        def kill(self):
            raise AssertionError("taskkill succeeded, fallback must not run")

    class Result:
        returncode = 0

    monkeypatch.setattr(server.os, "name", "nt")
    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)) or Result(),
    )

    server._kill_proc_tree(Process())

    assert calls[0][0][0] == [
        "taskkill.exe", "/pid", "1234", "/t", "/f",
    ]
    assert calls[0][1]["check"] is False


def test_notify_user_uses_windows_native_toast(monkeypatch):
    calls = []
    monkeypatch.setattr(server.os, "name", "nt")
    monkeypatch.setattr(
        server.subprocess, "Popen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    server.notify_user("?? Windows notification")

    args = calls[0][0][0]
    assert args[0] == "powershell.exe"
    assert args[-2] == "-EncodedCommand"
    script = server.base64.b64decode(args[-1]).decode("utf-16le")
    payload = server.base64.b64encode("?? Windows notification".encode()).decode()
    assert payload in script


def test_project_slug_rejects_directory_paths():
    assert server.require_project_slug("guitusaipao") == "guitusaipao"
    assert dispatch.project_slug("guitusaipao") == "guitusaipao"

    invalid = [
        "C:/Users/test/data/projects/guitusaipao",
        r"C:\Users\test\data\projects\guitusaipao",
        "data/projects/guitusaipao",
        "../guitusaipao",
    ]
    for value in invalid:
        with pytest.raises(server.ServiceError):
            server.require_project_slug(value)
        with pytest.raises(dispatch.argparse.ArgumentTypeError):
            dispatch.project_slug(value)

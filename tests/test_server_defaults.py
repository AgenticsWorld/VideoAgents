import asyncio

import pytest

from services.api.__main__ import DEFAULT_HOST
from services.runtime import core as server


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

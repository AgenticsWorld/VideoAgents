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
    assert len(agents) == 83
    assert {agent["id"] for agent in agents} >= {
        "00-orchestration/workflow-orchestrator",
        "07-directing/director",
        "11-qa/copyright",
    }


def test_agent_path_validation_rejects_traversal():
    with pytest.raises(server.ServiceError):
        server.safe_agent("../server")

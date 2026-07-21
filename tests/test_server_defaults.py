import asyncio

import pytest
from fastapi import HTTPException

from webui import server


def test_public_release_security_defaults():
    assert server.HOST == "127.0.0.1"
    assert server.PERMISSION_MODE == "acceptEdits"
    assert server.CLAUDE_USAGE_PROBE_ENABLED is False
    assert server._claude_oauth_token() is None


def test_agent_catalog_is_available():
    agents = asyncio.run(server.api_agents(refresh=True))
    core = [a for a in agents if not a.get("plugin")]
    assert len(core) == 83
    assert {agent["id"] for agent in agents} >= {
        "00-orchestration/workflow-orchestrator",
        "07-directing/director",
        "11-qa/copyright",
        "13-derivative-fiction/prose-writer",   # derivative-fiction 插件成员
    }


def test_plugin_catalog_loads_derivative_fiction():
    plugins = server.list_plugins(refresh=True)
    df = next(p for p in plugins if p["name"] == "derivative-fiction")
    assert df["errors"] == []
    assert len(df["agents"]) == 8
    assert df["workflows"] == ["workflows/novel.yaml"]
    assert df["outputs_ns"] == "derivative"
    # 插件 Agent 解析:内置优先、插件兜底、非法 id 拒绝
    assert server.agent_dir("13-derivative-fiction/prose-writer")
    assert server.agent_dir("00-orchestration/workflow-orchestrator") == \
        server.AGENTS_DIR / "00-orchestration/workflow-orchestrator"
    assert server.agent_dir("../../etc/passwd") is None
    # 11-qa/ 前缀纪律对插件成员同样生效(无状态可并发)
    assert server.is_stateless_agent("11-qa/prose-qa")


def test_agent_path_validation_rejects_traversal():
    with pytest.raises(HTTPException):
        server.safe_agent("../server")

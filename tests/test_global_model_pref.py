"""Global model preference behavior after the API/runtime split."""

import asyncio

import pytest

from services.runtime import core


ORCHESTRATOR = "00-orchestration/workflow-orchestrator"


def _global_mode(monkeypatch):
    monkeypatch.setattr(core, "load_agentmodels", lambda: {})
    monkeypatch.setattr(core, "load_genconfig", lambda: {"agentmodel_mode": "global"})


def test_global_mode_follows_toolbar_preference(monkeypatch):
    _global_mode(monkeypatch)
    monkeypatch.setitem(core.STATE, "global_model", {"engine": "kimi", "model": "kimi-for-coding"})
    assert core.agent_effective_model(ORCHESTRATOR) == {
        "engine": "kimi", "model": "kimi-for-coding",
    }


def test_global_mode_without_preference_falls_back_to_claude(monkeypatch):
    _global_mode(monkeypatch)
    monkeypatch.delitem(core.STATE, "global_model", raising=False)
    assert core.agent_effective_model(ORCHESTRATOR)["engine"] == "claude"


def test_agent_configuration_beats_toolbar_preference(monkeypatch):
    monkeypatch.setattr(core, "load_agentmodels", lambda: {})
    monkeypatch.setattr(core, "load_genconfig", lambda: {"agentmodel_mode": "smart_claude"})
    monkeypatch.setitem(core.STATE, "global_model", {"engine": "kimi", "model": "kimi-for-coding"})
    assert core.agent_effective_model(ORCHESTRATOR) == {"engine": "claude", "model": "opus"}


def test_global_model_endpoint_validates_and_normalizes(monkeypatch):
    monkeypatch.setattr(core, "save_state", lambda state: None)
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_globalmodel_set({"engine": "gpt4"}))
    result = asyncio.run(core.api_globalmodel_set({"engine": "Codex", "model": " gpt-5.6-sol "}))
    assert result["global_model"] == {"engine": "codex", "model": "gpt-5.6-sol"}

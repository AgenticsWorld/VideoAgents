"""顶栏全局引擎/模型的服务端副本(/api/globalmodel + agent_effective_model)。

顶栏选择本体存浏览器 localStorage,服务端自发对话(设置变更通知/看门狗唤醒/
项目初始化)看不到它;global 模式下靠 STATE.global_model 副本跟随顶栏,
Agent 级配置(UI 覆盖或智能策略)仍最优先,两头都取不到才回退 claude。
"""
import asyncio

import pytest
from fastapi import HTTPException

from webui import server

ORCH = "00-orchestration/workflow-orchestrator"


def _global_mode(monkeypatch):
    monkeypatch.setattr(server, "load_agentmodels", lambda: {})
    monkeypatch.setattr(server, "load_genconfig",
                        lambda: {"agentmodel_mode": "global"})


def test_global_mode_follows_toolbar_pref(monkeypatch):
    _global_mode(monkeypatch)
    monkeypatch.setitem(server.STATE, "global_model",
                        {"engine": "kimi", "model": "kimi-for-coding"})
    assert server.agent_effective_model(ORCH) == \
        {"engine": "kimi", "model": "kimi-for-coding"}


def test_global_mode_without_pref_falls_back_claude(monkeypatch):
    _global_mode(monkeypatch)
    monkeypatch.delitem(server.STATE, "global_model", raising=False)
    assert server.agent_effective_model(ORCH)["engine"] == "claude"


def test_invalid_stored_engine_ignored(monkeypatch):
    _global_mode(monkeypatch)
    monkeypatch.setitem(server.STATE, "global_model",
                        {"engine": "gpt4", "model": "x"})
    assert server.global_model_pref()["engine"] == ""
    assert server.agent_effective_model(ORCH)["engine"] == "claude"


def test_agent_level_config_beats_toolbar_pref(monkeypatch):
    monkeypatch.setattr(server, "load_agentmodels", lambda: {})
    monkeypatch.setattr(server, "load_genconfig",
                        lambda: {"agentmodel_mode": "smart_claude"})
    monkeypatch.setitem(server.STATE, "global_model",
                        {"engine": "kimi", "model": "kimi-for-coding"})
    assert server.agent_effective_model(ORCH) == \
        {"engine": "claude", "model": "opus"}


def test_endpoint_validates_engine(monkeypatch):
    monkeypatch.setattr(server, "save_state", lambda s: None)
    with pytest.raises(HTTPException):
        asyncio.run(server.api_globalmodel_set({"engine": "gpt4"}))


def test_endpoint_roundtrip(monkeypatch):
    monkeypatch.setattr(server, "save_state", lambda s: None)
    monkeypatch.setitem(server.STATE, "global_model", None)   # 占位,teardown 自动还原
    r = asyncio.run(server.api_globalmodel_set(
        {"engine": "Codex", "model": " gpt-5.6-sol "}))
    assert r["global_model"] == {"engine": "codex", "model": "gpt-5.6-sol"}
    assert asyncio.run(server.api_globalmodel_get())["global_model"] == \
        {"engine": "codex", "model": "gpt-5.6-sol"}

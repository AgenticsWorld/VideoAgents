"""顶栏「智能分配」⚙ 按钮改选高/低档模型(genconfig.smart_models):
覆盖叠加内置默认、空串回到内置默认、非法键/模型 id 拒收、不清 Agent 级单独配置、接口回带档位信息。"""
import asyncio
import json

import pytest

from services.runtime import core


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    path = tmp_path / "genconfig.json"
    monkeypatch.setattr(core, "GENCONFIG_PATH", path)
    monkeypatch.setattr(core, "AGENTMODELS_PATH", tmp_path / "agentmodels.json")
    monkeypatch.delenv("VIDEOAGENTS_USER_JWT", raising=False)
    monkeypatch.setattr(core, "save_state", lambda *a, **k: None)
    monkeypatch.setitem(core.STATE, "ui_lang", "")

    async def no_refresh(cfg):
        raise AssertionError("smart_models 单键保存不应同步 RunningHub 工作流缓存")
    monkeypatch.setattr(core, "_refresh_rh_wf_caches", no_refresh)
    return path


def _save(body):
    return asyncio.run(core.api_genconfig_set(body))


HIGH_AGENT = "01-story/screenplay"     # 01-story 分类 = high
LOW_AGENT = "11-qa/logic-qa"           # 11-qa 分类 = low


def test_builtin_defaults_when_nothing_saved(cfg_file):
    assert core.smart_tier_models("smart_claude") == core.AM_MODE_MODELS["smart_claude"]
    assert core.smart_tier_models("global") == {}
    assert core.default_agent_model(HIGH_AGENT, "smart_claude") == core.AM_MODE_MODELS["smart_claude"]["high"]
    assert core.default_agent_model(HIGH_AGENT, "global") == {"engine": "", "model": ""}


def test_override_one_tier_then_reset(cfg_file):
    _save({"agentmodel_mode": "smart_claude"})
    _save({"smart_models": {"smart_claude": {"high": "claude-opus-5-5", "low": ""}}})
    saved = json.loads(cfg_file.read_text())
    assert saved["smart_models"] == {"smart_claude": {"high": "claude-opus-5-5"}}   # 空串不落盘
    assert core.default_agent_model(HIGH_AGENT) == {"engine": "claude", "model": "claude-opus-5-5"}
    assert core.default_agent_model(LOW_AGENT) == {"engine": "claude", "model": "sonnet"}   # 低档仍内置默认
    # 其它策略不受影响
    assert core.default_agent_model(HIGH_AGENT, "smart_codex") == core.AM_MODE_MODELS["smart_codex"]["high"]
    # 两档都回空串 = 恢复内置默认,整个策略键去掉
    _save({"smart_models": {"smart_claude": {"high": "", "low": ""}}})
    assert json.loads(cfg_file.read_text())["smart_models"] == {}
    assert core.default_agent_model(HIGH_AGENT) == {"engine": "claude", "model": "opus"}


def test_saving_tiers_keeps_agent_level_overrides(cfg_file):
    core.atomic_write_json(core.AGENTMODELS_PATH, {LOW_AGENT: {"engine": "codex", "model": "gpt-6-luna"}})
    _save({"smart_models": {"smart_claude": {"low": "haiku"}}})
    assert core.agent_model_config(LOW_AGENT) == {"engine": "codex", "model": "gpt-6-luna"}


@pytest.mark.parametrize("body", [
    {"smart_models": ["x"]},
    {"smart_models": {"global": {"high": "opus"}}},
    {"smart_models": {"smart_nope": {"high": "opus"}}},
    {"smart_models": {"smart_claude": {"mid": "opus"}}},
    {"smart_models": {"smart_claude": "opus"}},
    {"smart_models": {"smart_claude": {"high": "--dangerously"}}},
    {"smart_models": {"smart_claude": {"high": "opus 5"}}},
])
def test_rejects_bad_payloads(cfg_file, body):
    with pytest.raises(core.ServiceError) as e:
        _save(body)
    assert e.value.status_code == 400


def test_agentmodels_api_reports_tiers_and_effective_models(cfg_file, monkeypatch):
    monkeypatch.setattr(core, "list_agents", lambda *a, **k: [{"id": HIGH_AGENT}, {"id": LOW_AGENT}])
    monkeypatch.setattr(core, "load_agentmodels", lambda: {})
    _save({"agentmodel_mode": "smart_grok"})
    _save({"smart_models": {"smart_grok": {"low": "grok-4.5"}}})
    r = asyncio.run(core.api_agentmodels())
    assert r["mode"] == "smart_grok"
    assert r["tiers"] == {HIGH_AGENT: "high", LOW_AGENT: "low"}
    assert r["smart"]["smart_grok"] == {"high": core.AM_MODE_MODELS["smart_grok"]["high"]["model"],
                                        "low": "grok-4.5"}
    assert r["smart_builtin"]["smart_grok"]["low"] == core.AM_MODE_MODELS["smart_grok"]["low"]["model"]
    assert r["defaults"][LOW_AGENT] == {"engine": "grok", "model": "grok-4.5"}


def test_footage_engine_uses_overridden_low_tier(cfg_file, monkeypatch):
    monkeypatch.setattr(core, "global_model_pref", lambda: {"engine": "codex", "model": "__smart"})
    _save({"smart_models": {"smart_codex": {"low": "gpt-6-luna"}}})
    assert core.footage_engine_spec()["model"] == "gpt-6-luna"

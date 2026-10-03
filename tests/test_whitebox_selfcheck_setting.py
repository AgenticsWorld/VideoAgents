"""Agent 高级设置「白模自检」:默认开、可持久化关闭、只注入白模调度工位的运行提示词。"""
import asyncio
import os
import tempfile

os.environ.setdefault("VIDEOAGENTS_DATA_DIR", tempfile.mkdtemp(prefix="va-wbcheck-"))

from services.runtime import core  # noqa: E402

PROJ = "wbchecktest"


def test_default_on_and_api_roundtrip(monkeypatch):
    monkeypatch.setattr(core, "save_state", lambda state: None)
    monkeypatch.delitem(core.STATE, "whitebox_selfcheck", raising=False)
    assert core.whitebox_selfcheck_setting() is True
    assert asyncio.run(core.api_agent_advanced_get())["whitebox_selfcheck"] is True
    out = asyncio.run(core.api_agent_advanced_set({"whitebox_selfcheck": False}))
    assert out["whitebox_selfcheck"] is False and core.whitebox_selfcheck_setting() is False
    monkeypatch.delitem(core.STATE, "whitebox_selfcheck", raising=False)


def test_prompt_injected_only_for_whitebox_staging(monkeypatch):
    core.ensure_project(PROJ)
    monkeypatch.setitem(core.STATE, "whitebox_selfcheck", True)
    on = core.build_role_prompt(core.WHITEBOX_REEL_AGENT, PROJ)
    assert "白模自检:**开启**" in on and "--stills" in on
    monkeypatch.setitem(core.STATE, "whitebox_selfcheck", False)
    off = core.build_role_prompt(core.WHITEBOX_REEL_AGENT, PROJ)
    assert "白模自检:**关闭**" in off and "白模自检:**开启**" not in off
    assert "白模自检设定" not in core.build_role_prompt("06-art/prop", PROJ)

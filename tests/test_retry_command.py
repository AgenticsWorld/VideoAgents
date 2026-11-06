# -*- coding: utf-8 -*-
"""/retry 聊天命令(2026-11-06):与 /auto、/stop 同一机制在 api_chat 本地拦截、不派发运行,
/retry 查看、/retry N 改「Agent 高级设置→重跑次数」(api_agent_advanced_set 同一入口)。"""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.runtime import core  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def env(monkeypatch, tmp_path):
    saved, chats = [], []
    monkeypatch.setattr(core, "STATE", {"max_retries": 2})
    monkeypatch.setattr(core, "save_state", lambda st: saved.append(dict(st)))
    monkeypatch.setattr(core, "RUNS", {})
    monkeypatch.setattr(core, "ui_lang_code", lambda *a: "zh")
    monkeypatch.setattr(core, "agent_dir", lambda agent: tmp_path)
    monkeypatch.setattr(core, "append_chat", lambda agent, project, msg: chats.append(msg))

    async def _no_run(*a, **k):
        raise AssertionError("/retry must not dispatch a run")
    monkeypatch.setattr(core, "execute_run", _no_run)
    return {"saved": saved, "chats": chats}


def _chat(text):
    return _run(core.api_chat({"agent": "production/producer", "project": "demo", "message": text}))


def test_retry_show(env):
    res = _chat("/retry")
    assert res == {"ok": True, "max_retries": 2}
    assert env["chats"][0] == {"role": "user", "text": "/retry", "source": "user"}
    assert "当前重跑次数:2" in env["chats"][1]["text"] and not env["saved"]


def test_retry_set(env):
    res = _chat("/retry 1")
    assert res["max_retries"] == 1 and core.STATE["max_retries"] == 1
    assert env["saved"] and env["saved"][-1]["max_retries"] == 1
    assert "从 2 改为 1" in env["chats"][-1]["text"]
    _chat("/RETRY 0")
    assert core.max_retries_setting() == 0 and "不自动重跑" in env["chats"][-1]["text"]


def test_retry_same_value_and_running_warning(env):
    _chat("/retry 2")
    assert "本就是 2" in env["chats"][-1]["text"] and "⚠️" not in env["chats"][-1]["text"]
    core.RUNS["r1"] = {"status": "running"}
    _chat("/retry 3")
    assert "⚠️" in env["chats"][-1]["text"]


@pytest.mark.parametrize("text", ["/retry -1", f"/retry {core.MAX_RETRIES_MAX + 1}", "/retry abc", "/retry 1 2"])
def test_retry_rejects_bad_input(env, text):
    res = _chat(text)
    assert res["max_retries"] == 2 and not env["saved"]
    assert "无法识别的命令" in env["chats"][-1]["text"]


def test_retry_english(env, monkeypatch):
    monkeypatch.setattr(core, "ui_lang_code", lambda *a: "en")
    _chat("/retry 4")
    assert env["chats"][-1]["text"].startswith("🔁 Rerun count changed from 2 to 4")

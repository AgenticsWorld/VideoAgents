"""CI smoke tests: package imports and release version consistency.

The full regression suite in this directory lives outside version control
(local-only since v1.0.4); this file is the one tracked exception — a minimal
always-versioned check that keeps CI meaningful without shipping tests that
depend on private local data.
"""

import asyncio
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_api_package_imports():
    from services.api import __version__, app

    assert __version__
    assert app.app.title


def test_web_server_imports():
    import apps.web.server  # noqa: F401


def test_release_versions_consistent():
    from services.api import __version__

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert match and match.group(1) == __version__

    root_pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert root_pkg["version"] == __version__

    desktop_pkg = json.loads((ROOT / "apps/desktop/package.json").read_text(encoding="utf-8"))
    assert desktop_pkg["version"] == __version__

    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    match = re.search(r"^version:\s*(\S+)", citation, re.MULTILINE)
    assert match and match.group(1) == __version__


def test_pi_engine_models_and_event_stream(monkeypatch):
    from services.api.schemas import GlobalModelUpdate, RunCreate
    from services.runtime import core

    assert GlobalModelUpdate(engine="pi", model="openai-codex/gpt-5.5").engine == "pi"
    assert RunCreate(agent="00-orchestration/context", message="hello", engine="pi").engine == "pi"
    assert "pi" in core.ENGINES and "pi" in core.AM_ENGINES

    table = """provider       model                    context  max-out  thinking  images
openai-codex  gpt-5.5                  400K     128K     yes       yes
anthropic     claude-sonnet-4-5        200K     64K      yes       yes
"""
    models = core._parse_pi_models_table(table)
    assert [m["id"] for m in models] == [
        "openai-codex/gpt-5.5", "anthropic/claude-sonnet-4-5"]
    assert models[0]["thinking"] is True and models[0]["images"] is True

    published = []

    class Hub:
        def publish(self, event):
            published.append(event)

    monkeypatch.setattr(core, "HUB", Hub())
    monkeypatch.setattr(core, "publish_run", lambda run: None)
    run = {"id": "run-1", "agent": "00-orchestration/context"}
    core.handle_pi_event(run, {"type": "session", "id": "session-1"})
    core.handle_pi_event(run, {"type": "message_start", "message": {"role": "assistant"}})
    core.handle_pi_event(run, {"type": "message_update", "assistantMessageEvent": {
        "type": "text_delta", "delta": "done"}})
    core.handle_pi_event(run, {"type": "message_end", "message": {
        "role": "assistant", "content": [{"type": "text", "text": "done"}],
        "usage": {"input": 10, "output": 2, "cacheRead": 3, "cacheWrite": 1},
        "stopReason": "stop"}})
    core.handle_pi_event(run, {"type": "tool_execution_start", "toolName": "write",
                               "args": {"path": "out.txt"}})

    assert run["session_id"] == "session-1"
    assert run["result"] == "done" and run["tokens"] == 16
    assert run["files"] == ["out.txt"]
    assert any(e.get("type") == "text" and e.get("text") == "done" for e in published)


def test_web_streaming_deltas_are_coalesced():
    html = (ROOT / "apps/web/static/index.html").read_text(encoding="utf-8")
    assert "function appendLiveText(container,text)" in html
    assert "appendLiveText(d,ev.text)" in html
    assert "t.className='livetext';t.textContent=ev.text" not in html
    assert ".run .eng{" in html and "text-overflow:ellipsis" in html
    assert '<span class="eng" title="${esc(engLabel)}">' in html


def test_api_event_stream_stops_on_shutdown():
    from services.api import app as api_app

    async def probe():
        api_app._shutdown_event = asyncio.Event()
        response = await api_app.events()
        stream = response.body_iterator
        assert "hello" in await anext(stream)
        waiting = asyncio.create_task(anext(stream))
        api_app.request_shutdown()
        try:
            await asyncio.wait_for(waiting, timeout=1)
        except StopAsyncIteration:
            pass
        else:
            raise AssertionError("SSE stream did not close during shutdown")
        finally:
            api_app._shutdown_event = None

    asyncio.run(probe())


def test_opencode_engine_strategy_and_event_stream(monkeypatch):
    from services.api.schemas import GlobalModelUpdate, RunCreate
    from services.runtime import core

    assert GlobalModelUpdate(engine="opencode",
                             model="opencode-go/deepseek-v4-pro").engine == "opencode"
    assert RunCreate(agent="00-orchestration/context", message="hello",
                     engine="opencode").engine == "opencode"
    assert "opencode" in core.ENGINES and "opencode" in core.AM_ENGINES
    assert "smart_deepseek" in core.AM_MODES
    assert core.default_agent_model("01-story/x", "smart_deepseek")["model"] \
        == "opencode-go/deepseek-v4-pro"
    assert core.default_agent_model("11-qa/x", "smart_deepseek")["model"] \
        == "opencode-go/deepseek-v4-flash"

    published = []

    class Hub:
        def publish(self, event):
            published.append(event)

    monkeypatch.setattr(core, "HUB", Hub())
    monkeypatch.setattr(core, "publish_run", lambda run: None)
    run = {"id": "run-1", "agent": "00-orchestration/context"}
    sid = "ses_0001"
    core.handle_opencode_event(run, {"type": "step_start", "sessionID": sid,
                                     "part": {"type": "step-start"}})
    core.handle_opencode_event(run, {"type": "tool_use", "sessionID": sid, "part": {
        "type": "tool", "tool": "write",
        "state": {"status": "completed",
                  "input": {"filePath": "out.txt", "content": "hi"}}}})
    core.handle_opencode_event(run, {"type": "step_finish", "sessionID": sid, "part": {
        "type": "step-finish", "reason": "tool-calls",
        "tokens": {"total": 100, "input": 60, "output": 30, "reasoning": 5,
                   "cache": {"write": 0, "read": 5}}, "cost": 0}})
    core.handle_opencode_event(run, {"type": "text", "sessionID": sid, "part": {
        "type": "text", "text": "done"}})
    core.handle_opencode_event(run, {"type": "step_finish", "sessionID": sid, "part": {
        "type": "step-finish", "reason": "stop",
        "tokens": {"total": 50, "input": 40, "output": 10, "reasoning": 0,
                   "cache": {"write": 0, "read": 0}}, "cost": 0}})

    assert run["session_id"] == sid
    assert run["result"] == "done" and run["tokens"] == 150
    assert run["files"] == ["out.txt"]
    assert any(a.startswith("✎ write:") for a in run["activity"])
    assert any(e.get("type") == "text" and e.get("text") == "done" for e in published)

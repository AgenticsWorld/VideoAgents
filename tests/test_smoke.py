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


def test_human_gate_guard_creates_one_persistent_signoff_without_passing_dag(
        tmp_path, monkeypatch):
    from services.runtime import core

    project = "gate-test"
    runs_dir = tmp_path / project / "runs"
    runs_dir.mkdir(parents=True)
    dag_path = runs_dir / "dag.json"
    dag_path.write_text(json.dumps({"nodes": [
        {"id": "work", "state": "done", "depends_on": [], "human": False},
        {"id": "h2", "state": "pending", "depends_on": ["work"],
         "human": True, "gate": {"checkpoint": "H2-美术风格锁定"}},
        {"id": "template", "state": "template", "depends_on": ["work"],
         "human": True, "gate": {"checkpoint": "H3A-分镜确认"}},
    ]}), encoding="utf-8")
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(core, "CONFIRMS", {})
    monkeypatch.setattr(core, "RUNS", {})
    monkeypatch.setattr(core.HUB, "publish", lambda event: None)
    monkeypatch.setattr(core, "notify_user", lambda text: None)

    first = asyncio.run(core.ensure_human_gate_approvals(project, parent="ended"))
    second = asyncio.run(core.ensure_human_gate_approvals(project, parent="ended"))

    assert len(first) == 1
    assert second == []
    approval = core.CONFIRMS[first[0]]
    assert approval["kind"] == "sign"
    assert approval["timeout"] is None
    assert approval["project"] == project
    assert approval["gate_id"] == "h2"
    assert core._dag_runnable(project)[1] == ["h2"]
    assert json.loads(dag_path.read_text(encoding="utf-8"))["nodes"][1]["state"] == "pending"


def test_signoff_answer_wakes_orchestrator_but_does_not_auto_pass_gate(
        tmp_path, monkeypatch):
    from services.runtime import core

    project = "gate-answer-test"
    runs_dir = tmp_path / project / "runs"
    runs_dir.mkdir(parents=True)
    dag_path = runs_dir / "dag.json"
    dag_path.write_text(json.dumps({"nodes": [
        {"id": "work", "state": "done", "depends_on": [], "human": False},
        {"id": "h2", "state": "pending", "depends_on": ["work"],
         "human": True, "gate": {"checkpoint": "H2-美术风格锁定"}},
    ]}), encoding="utf-8")
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(core, "CONFIRMS", {})
    monkeypatch.setattr(core, "RUNS", {})
    monkeypatch.setattr(core.HUB, "publish", lambda event: None)
    monkeypatch.setattr(core, "notify_user", lambda text: None)
    resumed = []

    async def fake_chat(body):
        resumed.append(body)
        return {"run_id": "resume-123"}

    monkeypatch.setattr(core, "api_chat", fake_chat)
    ids = asyncio.run(core.ensure_human_gate_approvals(project, parent="ended"))
    result = asyncio.run(core.api_confirm_answer(ids[0], {"answer": "签字"}))

    assert result == {"ok": True, "answer": "签字"}
    assert core.CONFIRMS[ids[0]]["continuation_run_id"] == "resume-123"
    assert resumed[0]["agent"] == core.ORCHESTRATOR_AGENT
    assert "不是自动放行" in resumed[0]["message"]
    assert json.loads(dag_path.read_text(encoding="utf-8"))["nodes"][1]["state"] == "pending"


def test_dispatch_signoff_sends_project_for_durable_gate_binding(monkeypatch):
    from services.runtime import dispatch

    calls = []

    def fake_api(path, payload=None):
        calls.append((path, payload))
        if path == "/approvals":
            return {"confirm_id": "approval-1"}
        return {"answer": "签字"}

    monkeypatch.setattr(dispatch, "api", fake_api)
    monkeypatch.setattr(dispatch.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(dispatch, "heartbeat", lambda note: None)
    dispatch.confirm("【H2-美术风格锁定】请审阅", 10, ["签字", "暂缓"],
                     "签字", sign=True, project="race")

    assert calls[0][1]["project"] == "race"
    assert calls[0][1]["kind"] == "sign"


def test_comfy_ace_step_error_includes_shared_model_path_hint():
    from modules import genmedia

    error = genmedia._comfy_execution_error({
        "messages": [["execution_error", {
            "node_id": "1",
            "node_type": "ACEModelLoader",
            "exception_type": "FileNotFoundError",
            "exception_message": (
                "[WinError 3] cannot find path "
                r"E:\\Comfy-Desktop\\ComfyUI-Installs\\Test\\ComfyUI\\models\\TTS\\"
                "ACE-Step-v1-3.5B"
            ),
        }]],
    })

    assert "ACE-Step 节点按 ComfyUI 安装目录的 models/TTS 查找" in error
    assert "extra_model_paths.yaml" in error


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

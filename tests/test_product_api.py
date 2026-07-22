import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from services.api.app import app
from services.api.runtime_bridge import install_runtime_store
from services.runtime import core
from apps.web.server import _upstream_chunk


def test_product_api_is_versioned_and_legacy_api_is_absent():
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["api_version"] == "v1"
        assert client.get("/api/agents").status_code == 404


def test_openapi_only_publishes_v1_operations():
    spec = app.openapi()
    assert spec["paths"]
    assert all(path.startswith("/api/v1/") for path in spec["paths"])
    assert not any(
        method in operations
        for operations in spec["paths"].values()
        for method in ("put", "patch")
    )


def test_api_service_does_not_serve_the_web_client():
    with TestClient(app) as client:
        assert client.get("/").status_code == 404
        agents = client.get("/api/v1/agents")
        assert agents.status_code == 200
        assert len([agent for agent in agents.json() if not agent.get("plugin")]) == 83


def test_plugin_and_global_model_resources_are_versioned(monkeypatch):
    monkeypatch.setattr(core, "save_state", lambda state: None)
    monkeypatch.setitem(core.STATE, "global_model", core.STATE.get("global_model"))
    with TestClient(app) as client:
        plugins = client.get("/api/v1/plugins")
        assert plugins.status_code == 200
        assert {plugin["name"] for plugin in plugins.json()} >= {
            "derivative-fiction", "fusion-fiction",
        }
        saved = client.post(
            "/api/v1/config/global-model",
            json={"engine": "codex", "model": "gpt-5.6-sol"},
        )
        assert saved.status_code == 200
        assert saved.json()["global_model"] == {
            "engine": "codex", "model": "gpt-5.6-sol",
        }
        assert client.get("/api/globalmodel").status_code == 404


def test_plugin_install_toggle_and_delete_use_writable_runtime_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "PLUGINS_DIR", tmp_path)
    monkeypatch.setattr(core, "save_state", lambda state: None)
    monkeypatch.setitem(core.STATE, "plugins_disabled", list(core.STATE.get("plugins_disabled") or []))
    core._expire_agent_caches()
    package = io.BytesIO()
    manifest = {
        "name": "test-plugin",
        "version": "1",
        "description": "test",
        "categories": {"99-test": "Test"},
        "agents": [{"id": "99-test/worker"}],
    }
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("plugin.json", json.dumps(manifest))
        archive.writestr("agents/99-test/worker/SOUL.md", "# SOUL.md — Test\n")

    try:
        with TestClient(app) as client:
            installed = client.post("/api/v1/plugins", content=package.getvalue())
            assert installed.status_code == 200
            assert (tmp_path / "test-plugin" / "plugin.json").is_file()
            disabled = client.post(
                "/api/v1/plugins/test-plugin",
                json={"name": "test-plugin", "enabled": False},
            )
            assert disabled.status_code == 200
            assert not next(p for p in disabled.json()["plugins"] if p["name"] == "test-plugin")["enabled"]
            deleted = client.post(
                "/api/v1/plugins/test-plugin/delete", json={"name": "test-plugin"}
            )
            assert deleted.status_code == 200
            assert not (tmp_path / "test-plugin").exists()
    finally:
        core._expire_agent_caches()


def test_project_create_preserves_original_webui_payload(monkeypatch):
    received = {}

    async def create(payload):
        received.update(payload)
        return {"ok": True}

    monkeypatch.setattr(core, "api_projects_create", create)
    payload = {
        "name": "contract-test",
        "novel": "source",
        "brief": "brief",
        "style": "style",
        "settings": {
            "duration": {"episode_minutes": 12, "shot_min_s": 3, "shot_max_s": 9},
            "output": {"language": "English"},
            "review": {"logic": 80},
            "packaging": {"intro_enabled": True},
        },
    }
    with TestClient(app) as client:
        response = client.post("/api/v1/projects", json=payload)
    assert response.status_code == 200
    assert received == payload


def test_static_webui_keeps_original_post_contracts():
    root = Path(__file__).resolve().parents[1]
    static = root / "apps" / "web" / "static"
    index = (static / "index.html").read_text(encoding="utf-8")
    assert "function apiMethod" not in index
    assert "const o=body?{method:'POST'" in index
    assert "style:$('#np-style').value,settings:npCollectSettings()" in index
    for name in ("index.html", "models.html", "storage.html", "versions.html", "preview_refs.html", "preview_storyboard.html"):
        text = (static / name).read_text(encoding="utf-8")
        assert "method:'PATCH'" not in text
        assert "method:'PUT'" not in text
    dispatch = (root / "services" / "runtime" / "dispatch.py").read_text(encoding="utf-8")
    assert 'method="PUT"' not in dispatch
    assert 'resp["confirm_id"]' in dispatch


def test_web_proxy_uses_non_buffering_reads_for_sse():
    class Upstream:
        def __init__(self):
            self.calls = 0

        def read1(self, size):
            self.calls += 1
            assert size == 64 * 1024
            return b'data: {"type":"hello"}\n\n'

        def read(self, size):  # pragma: no cover - a regression would call this
            raise AssertionError("buffering read() must not be used when read1() exists")

    upstream = Upstream()
    assert _upstream_chunk(upstream) == b'data: {"type":"hello"}\n\n'
    assert upstream.calls == 1


def test_runtime_store_does_not_change_live_event_payload(monkeypatch):
    class Store:
        def load_runs(self):
            return []

        def load_approvals(self):
            return []

        def append_event(self, event):
            self.saved = dict(event)
            return 42

    hub = core.Hub()
    queue = hub.subscribe()
    monkeypatch.setattr(core, "HUB", hub)
    store = Store()
    install_runtime_store(store)
    event = {"type": "run", "run": {"id": "r1", "status": "running"}}
    monkeypatch.setitem(core.RUNS, "r1", event["run"])
    store.upsert_run = lambda run: None
    core.HUB.publish(event)
    assert queue.get_nowait() == event
    assert store.saved == event

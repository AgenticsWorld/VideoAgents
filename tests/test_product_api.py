import io
import json
import zipfile

from fastapi.testclient import TestClient

from services.api.app import app
from services.runtime import core


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
    assert len(spec.get("components", {}).get("schemas", {})) >= 20


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
        saved = client.put(
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
            assert installed.status_code == 201
            assert (tmp_path / "test-plugin" / "plugin.json").is_file()
            disabled = client.put("/api/v1/plugins/test-plugin", json={"enabled": False})
            assert disabled.status_code == 200
            assert not next(p for p in disabled.json()["plugins"] if p["name"] == "test-plugin")["enabled"]
            deleted = client.delete("/api/v1/plugins/test-plugin")
            assert deleted.status_code == 200
            assert not (tmp_path / "test-plugin").exists()
    finally:
        core._expire_agent_caches()

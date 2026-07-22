from fastapi.testclient import TestClient

from services.api.app import app


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
        assert len(agents.json()) == 83

from fastapi.testclient import TestClient

from apps.web import server


def test_original_static_pages_are_served_from_clean_urls():
    server.START_API = False
    with TestClient(server.app) as client:
        for path in ["/", "/models", "/storage", "/versions", "/preview/storyboard"]:
            response = client.get(path)
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]


def test_web_gateway_exposes_no_legacy_api_route():
    server.START_API = False
    with TestClient(server.app) as client:
        assert client.get("/api/agents").status_code == 404

from fastapi.testclient import TestClient

from app import __version__
from app.main import create_app


def test_welcome(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"name": "SmartCattle Backend", "version": __version__, "docs": "/docs"}


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_status(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "version": __version__,
        "ai_service": {"configured": False}, "storage": "memory",
    }


def test_configured_status_hides_secrets(settings):
    configured = settings.model_copy(update={
        "smartcattle_ai_url": "https://smartcattle-ai.example.com",
        "ai_api_key": "private-test-key",
        "read_api_key": "private-read-key",
        "mysql_password": "private-db-password",
    })
    with TestClient(create_app(configured)) as client:
        response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["ai_service"] == {"configured": True}
    assert "https://smartcattle-ai.example.com" not in response.text
    assert "private-test-key" not in response.text
    assert "private-read-key" not in response.text
    assert "private-db-password" not in response.text


def test_cors(client):
    for origin, allowed in [("http://localhost:3000", True), ("https://untrusted.example.com", False)]:
        response = client.options("/api/events", headers={
            "Origin": origin, "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Content-Type,X-API-Key",
        })
        if allowed:
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == origin
        else:
            assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-credentials" not in response.headers


def test_unhandled_error_is_generic(settings, caplog):
    app = create_app(settings)

    @app.get("/test-error")
    def fail():
        raise RuntimeError("Internal diagnostic")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test-error")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "Unhandled application error" in caplog.text


def test_api_key_is_documented(client):
    schema = client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["APIKeyHeader"] == {
        "type": "apiKey", "in": "header", "name": "X-API-Key",
    }
    for path, method in [
        ("/api/ai/events", "post"), ("/api/events", "get"), ("/api/animals", "get"),
    ]:
        assert schema["paths"][path][method]["security"] == [{"APIKeyHeader": []}]
    for path in ["/", "/health", "/api/status"]:
        assert "security" not in schema["paths"][path]["get"]

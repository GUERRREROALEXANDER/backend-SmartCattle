import pytest
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
        "auth_enabled": False,
    }


def test_configured_status_hides_secrets(settings):
    configured = settings.model_copy(update={
        "smartcattle_ai_url": "https://smartcattle-ai.example.com",
        "ai_api_key": "private-test-key",
    })
    with TestClient(create_app(configured)) as client:
        response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["ai_service"] == {"configured": True}
    assert "https://smartcattle-ai.example.com" not in response.text
    assert "private-test-key" not in response.text


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
    body = response.json()
    # The message stays generic: an exception string can carry a connection
    # string or a row of data.
    assert body["detail"] == "Internal server error"
    assert "Internal diagnostic" not in response.text
    assert "Unhandled application error" in caplog.text

    # And it is correlatable. The id in the body matches the header, which is
    # what lets a user quote it without opening developer tools.
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_api_key_is_documented(client):
    schema = client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["APIKeyHeader"] == {
        "type": "apiKey", "in": "header", "name": "X-API-Key",
    }
    assert schema["paths"]["/api/ai/events"]["post"]["security"] == [{"APIKeyHeader": []}]


def test_every_response_carries_a_request_id(client):
    response = client.get("/health")
    request_id = response.headers["X-Request-ID"]
    assert len(request_id) == 32
    # A different id per request, or correlation means nothing.
    assert client.get("/health").headers["X-Request-ID"] != request_id


def test_a_supplied_request_id_is_kept(client):
    # So a retrying frontend, or a proxy, keeps one id across the hop instead
    # of the chain breaking at our door.
    response = client.get("/health", headers={"X-Request-ID": "frontend-retry-7"})
    assert response.headers["X-Request-ID"] == "frontend-retry-7"


@pytest.mark.parametrize("supplied", [
    "",
    "   ",
    "with space",
    "line\nbreak",
    'quote"and{brace}',
    "x" * 65,
])
def test_an_unusable_request_id_is_replaced(client, supplied):
    # Echoing arbitrary caller text into every log line lets whoever sends it
    # forge log entries, and an oversized one bloats every line it touches.
    response = client.get("/health", headers={"X-Request-ID": supplied})
    returned = response.headers["X-Request-ID"]
    assert returned != supplied
    assert len(returned) == 32


def test_the_request_id_is_logged_with_the_access_line(client, caplog):
    with caplog.at_level("INFO", logger="app.middleware"):
        response = client.get("/health")
    request_id = response.headers["X-Request-ID"]

    # In the formatted message, not only in extra. Render shows uvicorn's
    # default format, which drops extras, so an id only in extra is invisible
    # exactly where it is needed.
    assert request_id in caplog.text
    assert "/health" in caplog.text

    # And in extra, for a structured handler if one is ever configured.
    access = [record for record in caplog.records if record.name == "app.middleware"][-1]
    assert access.request_id == request_id
    assert access.status == 200
    assert access.path == "/health"


def test_status_reports_authentication_off(client):
    # The frontend needs this before offering a sign-in form: with
    # authentication off, a login attempt gets a 503 that looks like a server
    # fault rather than an unconfigured deployment.
    assert client.get("/api/status").json()["auth_enabled"] is False


def test_status_reports_authentication_on(auth_client):
    assert auth_client.get("/api/status").json()["auth_enabled"] is True


def test_status_never_leaks_the_secret(auth_client):
    from tests.conftest import TEST_JWT_SECRET

    # A boolean, not the value. This endpoint is public.
    body = auth_client.get("/api/status").text
    assert TEST_JWT_SECRET not in body

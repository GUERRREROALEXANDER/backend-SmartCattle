"""Checks on the published contract.

The OpenAPI document and the CORS headers are what a client actually
integrates against, and both can break without any handler changing.
"""

ORIGIN = "http://localhost:5173"


def test_both_security_schemes_are_published(client):
    schemes = client.get("/openapi.json").json()["components"]["securitySchemes"]
    # Two credentials that are not interchangeable: a bearer token is a
    # person, X-API-Key is the AI service. A client that cannot tell them apart
    # from the document tries the wrong one and reads the 401 as a bad token.
    assert schemes["HTTPBearer"]["scheme"] == "bearer"
    assert schemes["APIKeyHeader"]["name"] == "X-API-Key"


def test_the_protected_endpoints_declare_it(client):
    paths = client.get("/openapi.json").json()["paths"]

    def schemes(path: str, method: str) -> set[str]:
        return {
            name
            for requirement in paths[path][method].get("security", [])
            for name in requirement
        }

    # The herd writes need a user; the AI ingestion needs the shared key.
    assert schemes("/api/animals", "post") == {"HTTPBearer"}
    assert schemes("/api/zones/camera/{camera_id}", "post") == {"HTTPBearer"}
    assert schemes("/api/videos/analyses", "post") == {"HTTPBearer"}
    assert schemes("/api/ai/events", "post") == {"APIKeyHeader"}
    assert schemes("/api/animals/{tag}/detection", "put") == {"APIKeyHeader"}


def test_the_open_endpoints_declare_nothing(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path, method in [("/health", "get"), ("/api/status", "get"), ("/api/events", "get")]:
        assert not paths[path][method].get("security"), f"{method} {path} should be open"


def test_authentication_endpoints_are_documented(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in ["/api/auth/register", "/api/auth/login"]:
        assert "post" in paths[path]
    assert "get" in paths["/api/auth/me"]


def test_a_preflight_allows_the_authorization_header(client):
    # Without Authorization in the allowed headers the preflight succeeds and
    # the real request arrives with the header stripped, so every authenticated
    # call fails as unauthenticated with nothing in the logs to explain it.
    response = client.options("/api/auth/me", headers={
        "Origin": ORIGIN,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    })
    assert response.status_code == 200
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed


def test_the_request_id_header_is_readable_by_the_browser(client):
    # Returned on every response, and useless to the frontend unless CORS
    # exposes it: a cross-origin page cannot read an unexposed header.
    response = client.get("/health", headers={"Origin": ORIGIN})
    exposed = response.headers.get("access-control-expose-headers", "").lower()
    assert "x-request-id" in exposed


def test_an_unlisted_origin_is_not_allowed(client):
    response = client.get("/health", headers={"Origin": "https://somewhere-else.example"})
    # The request still succeeds -- CORS is enforced by the browser, not the
    # server -- but the allow header must not name an origin that is not
    # configured.
    assert response.headers.get("access-control-allow-origin") != "https://somewhere-else.example"

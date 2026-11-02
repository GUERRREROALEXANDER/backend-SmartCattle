"""The limits a public deployment relies on."""

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.guard import LOGIN_LIMIT, MAX_BODY_BYTES, REGISTER_LIMIT, RateLimiter, client_address
from app.main import create_app
from app.routes import live as live_route

ACCOUNT = {"email": "ana@finca.example", "password": "una-clave-larga", "full_name": "Ana"}
SECRET = "x" * 48


def app_with(**overrides):
    values = {
        "_env_file": None, "allowed_origins": "http://localhost:5173", "database_url": None,
        "jwt_secret": SECRET, "ai_api_key": "the-key", "rate_limit_enabled": True,
    }
    return TestClient(create_app(Settings(**{**values, **overrides})), raise_server_exceptions=False)


def test_behind_the_proxy_each_visitor_is_told_apart():
    headers = {"cf-connecting-ip": "1.1.1.1", "x-forwarded-for": "9.9.9.9"}
    assert client_address(headers, "10.0.0.1", True) == "1.1.1.1"
    assert client_address({"x-forwarded-for": "2.2.2.2, 10.0.0.2"}, "10.0.0.1", True) == "2.2.2.2"
    assert client_address({}, "10.0.0.1", True) == "10.0.0.1"


def test_without_a_proxy_those_headers_are_ignored():
    assert client_address({"cf-connecting-ip": "1.1.1.1"}, "10.0.0.1", False) == "10.0.0.1"


def test_the_limiter_starts_again_in_the_next_window():
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])
    assert limiter.hit("b", "a", (1, 60)) == 0
    assert limiter.hit("b", "a", (1, 60)) > 0
    assert limiter.hit("b", "other", (1, 60)) == 0
    now[0] = 61.0
    assert limiter.hit("b", "a", (1, 60)) == 0


def test_a_flood_of_logins_is_refused_with_a_time_to_wait_and_a_browser_can_read_it():
    with app_with() as client:
        origin = {"Origin": "http://localhost:5173"}
        # A different address each time: the lockout per email is another protection, and not the one under test.
        attempts = [{"email": f"a{number}@finca.example", "password": "otra-clave-larga"}
                    for number in range(LOGIN_LIMIT[0] + 1)]
        for attempt in attempts[:-1]:
            assert client.post("/api/auth/login", json=attempt, headers=origin).status_code == 401
        refused = client.post("/api/auth/login", json=attempts[-1], headers=origin)
    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) > 0
    assert refused.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_registrations_from_one_address_are_limited_but_another_address_is_not_affected():
    with app_with(trust_proxy_headers=True) as client:
        attacker = {"CF-Connecting-IP": "6.6.6.6"}
        for number in range(REGISTER_LIMIT[0]):
            client.post("/api/auth/register", json={**ACCOUNT, "email": f"a{number}@finca.example"}, headers=attacker)
        assert client.post("/api/auth/register", json={**ACCOUNT, "email": "b@finca.example"},
                           headers=attacker).status_code == 429
        assert client.post("/api/auth/register", json={**ACCOUNT, "email": "c@finca.example"},
                           headers={"CF-Connecting-IP": "7.7.7.7"}).status_code == 201


def test_health_is_never_limited(monkeypatch):
    monkeypatch.setattr("app.guard.GENERAL_LIMIT", (1, 60))
    with app_with() as client:
        assert [client.get("/health").status_code for _ in range(5)] == [200] * 5
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/status").status_code == 429


def test_the_ai_service_with_its_key_is_not_limited_but_a_wrong_key_is(monkeypatch):
    monkeypatch.setattr("app.guard.WRITE_LIMIT", (1, 60))
    report = {"status": "online", "observed_at": "2026-11-01T10:00:00Z"}
    with app_with() as client:
        url = "/api/ai/cameras/camera-01/status"
        assert [client.put(url, json=report, headers={"X-API-Key": "the-key"}).status_code
                for _ in range(3)] == [200] * 3
        wrong = [client.put(url, json=report, headers={"X-API-Key": "nope"}).status_code for _ in range(2)]
    assert wrong == [401, 429]


def test_an_oversized_body_is_refused_whether_or_not_it_declares_its_length():
    with app_with() as client:
        huge = b"x" * (MAX_BODY_BYTES + 1)
        declared = client.post("/api/auth/login", content=huge, headers={"Content-Type": "application/json"})
        chunked = client.post("/api/auth/login", content=iter([huge]), headers={"Content-Type": "application/json"})
    assert declared.status_code == 413
    assert chunked.status_code == 413


def test_a_chunked_body_under_the_cap_still_reaches_the_route():
    with app_with() as client:
        body = json.dumps(ACCOUNT).encode("utf-8")
        answer = client.post("/api/auth/register", content=iter([body[:10], body[10:]]),
                             headers={"Content-Type": "application/json"})
    assert answer.status_code == 201


def test_every_answer_carries_the_security_headers():
    with app_with() as client:
        headers = client.get("/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"


def test_after_the_first_account_a_closed_deployment_only_lets_the_owner_create_accounts():
    with app_with(public_registration=False) as client:
        first = client.post("/api/auth/register", json=ACCOUNT)
        second = client.post("/api/auth/register", json={**ACCOUNT, "email": "otro@finca.example"})
        created = client.post("/api/users", json={**ACCOUNT, "email": "trabajador@finca.example", "role": "worker"},
                              headers={"Authorization": f"Bearer {first.json()['access_token']}"})
    assert first.status_code == 201
    assert first.json()["user"]["role"] == "owner"
    assert second.status_code == 403
    assert "closed" in second.json()["detail"]
    assert created.status_code == 201


@pytest.mark.parametrize("path", [
    "/api/animals", "/api/events", "/api/cameras", "/api/videos/analyses", "/api/metrics/summary", "/api/catalogs",
])
def test_a_private_deployment_shows_nothing_without_a_session(path):
    with app_with(require_auth_for_reads=True) as client:
        refused = client.get(path)
        forged = client.get(path, headers={"Authorization": "Bearer forged"})
        token = client.post("/api/auth/register", json=ACCOUNT).json()["access_token"]
        allowed = client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert refused.status_code == 401
    assert forged.status_code == 401
    assert allowed.status_code == 200


def test_a_private_deployment_keeps_its_health_and_the_ai_services_reports():
    report = {"status": "online", "observed_at": "2026-11-01T10:00:00Z"}
    with app_with(require_auth_for_reads=True) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/status").status_code == 200
        assert client.put("/api/ai/cameras/camera-01/status", json=report,
                          headers={"X-API-Key": "the-key"}).status_code == 200


def test_the_api_documents_can_be_switched_off():
    with app_with(api_docs_enabled=False) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_live_sockets_from_one_address_are_bounded_and_given_back(monkeypatch):
    monkeypatch.setattr(live_route, "MAX_SOCKETS_PER_ADDRESS", 1)
    with app_with(rate_limit_enabled=False) as client:
        token = client.post("/api/auth/register", json=ACCOUNT).json()["access_token"]
        with client.websocket_connect("/ws/live") as first:
            first.send_json({"type": "auth", "token": token})
            assert first.receive_json()["type"] == "hello"
            with client.websocket_connect("/ws/live") as second, pytest.raises(WebSocketDisconnect) as closed:
                second.receive_json()
            assert closed.value.code == live_route.CLOSE_TRY_AGAIN_LATER
        with client.websocket_connect("/ws/live") as again:
            again.send_json({"type": "auth", "token": token})
            assert again.receive_json()["type"] == "hello"

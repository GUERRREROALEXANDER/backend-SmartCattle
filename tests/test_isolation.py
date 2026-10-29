"""Checks that two applications do not share state.

Every store has an in-memory implementation, and that is what runs when
DATABASE_URL is unset -- the default. State leaking between application
instances would make the test suite pass or fail depending on file order, and
would be invisible until it did.
"""

from fastapi.testclient import TestClient

from app.main import create_app

RECTANGLE = {"name": "Safe zone", "x_min": 0.1, "y_min": 0.1, "x_max": 0.9, "y_max": 0.9}


def token_for(client: TestClient, email: str = "ana@finca.example") -> dict[str, str]:
    body = client.post("/api/auth/register", json={
        "email": email, "password": "una-clave-larga", "full_name": "Ana",
    }).json()
    return {"Authorization": f"Bearer {body['access_token']}"}


def test_accounts_do_not_leak_between_applications(auth_settings, auth_client):
    token_for(auth_client)
    with TestClient(create_app(auth_settings)) as other:
        # The same address registers cleanly on a second application, and its
        # first account is an owner again.
        body = other.post("/api/auth/register", json={
            "email": "ana@finca.example", "password": "una-clave-larga", "full_name": "Ana",
        })
        assert body.status_code == 201
        assert body.json()["user"]["role"] == "owner"


def test_a_token_is_not_accepted_by_another_application(auth_settings, auth_client):
    headers = token_for(auth_client)
    with TestClient(create_app(auth_settings)) as other:
        # Same signing secret, so the signature verifies -- and it still fails,
        # because the user is resolved against this application's store and
        # there is nobody with that id.
        assert other.get("/api/auth/me", headers=headers).status_code == 401


def test_the_herd_does_not_leak_between_applications(auth_settings, auth_client):
    headers = token_for(auth_client)
    auth_client.post("/api/animals", json={"tag": "A-441"}, headers=headers)
    assert auth_client.get("/api/animals").json()["total"] == 1

    with TestClient(create_app(auth_settings)) as other:
        assert other.get("/api/animals").json()["total"] == 0


def test_zones_do_not_leak_between_applications(auth_settings, auth_client):
    headers = token_for(auth_client)
    auth_client.post("/api/zones/camera/camera-01", json=RECTANGLE, headers=headers)

    with TestClient(create_app(auth_settings)) as other:
        other_headers = token_for(other)
        assert other.get("/api/zones", headers=other_headers).json()["total"] == 0


def test_the_login_throttle_does_not_leak_between_applications(auth_settings, auth_client):
    token_for(auth_client)
    for _ in range(auth_settings.max_login_attempts):
        auth_client.post("/api/auth/login", json={
            "email": "ana@finca.example", "password": "incorrecta",
        })
    assert auth_client.post("/api/auth/login", json={
        "email": "ana@finca.example", "password": "incorrecta",
    }).status_code == 429

    with TestClient(create_app(auth_settings)) as other:
        # Per process and per application. A restart clears it, which is
        # documented behaviour rather than an accident.
        assert other.post("/api/auth/login", json={
            "email": "ana@finca.example", "password": "incorrecta",
        }).status_code == 401

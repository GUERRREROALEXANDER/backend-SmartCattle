"""GET /api/ai/cameras/{id}/zones: the safe zones the AI service polls."""

import pytest

KEY = {"X-API-Key": "test-shared-key"}
RECTANGLE = {"name": "Safe zone", "x_min": 0.1, "y_min": 0.1, "x_max": 0.9, "y_max": 0.9}


@pytest.fixture
def owner_headers(secured_auth_client, owner_payload):
    token = secured_auth_client.post("/api/auth/register", json=owner_payload).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def add_zone(client, headers, camera_id, **changes):
    response = client.post(f"/api/zones/camera/{camera_id}", json={**RECTANGLE, **changes}, headers=headers)
    assert response.status_code == 201
    return response.json()


def test_the_ai_reads_the_active_zones_of_its_camera(secured_auth_client, owner_headers):
    pen = add_zone(secured_auth_client, owner_headers, "camera-01")
    outline = [{"x": 0.2, "y": 0.2}, {"x": 0.6, "y": 0.2}, {"x": 0.4, "y": 0.6}]
    corral = add_zone(secured_auth_client, owner_headers, "camera-01", name="Corral",
                      x_min=0.2, y_min=0.2, x_max=0.6, y_max=0.6, outline=outline)
    add_zone(secured_auth_client, owner_headers, "camera-01", name="Retired", active=False)
    add_zone(secured_auth_client, owner_headers, "camera-02", name="Elsewhere")

    response = secured_auth_client.get("/api/ai/cameras/camera-01/zones", headers=KEY)

    assert response.status_code == 200
    assert response.json() == {"items": [pen, corral], "total": 2}


def test_a_camera_without_zones_gets_an_empty_list(secured_auth_client):
    response = secured_auth_client.get("/api/ai/cameras/camera-09/zones", headers=KEY)
    assert response.json() == {"items": [], "total": 0}


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_the_shared_key_is_required(secured_auth_client, headers):
    assert secured_auth_client.get("/api/ai/cameras/camera-01/zones", headers=headers).status_code == 401


def test_a_user_session_is_not_enough(secured_auth_client, owner_headers):
    # The browser has its own endpoint; this one is the AI service's.
    response = secured_auth_client.get("/api/ai/cameras/camera-01/zones", headers=owner_headers)
    assert response.status_code == 401


def test_a_malformed_camera_id_is_rejected(secured_auth_client):
    assert secured_auth_client.get("/api/ai/cameras/bad%20id/zones", headers=KEY).status_code == 422

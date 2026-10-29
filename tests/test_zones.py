import pytest

ZONES_URL = "/api/zones"
CAMERA_URL = "/api/zones/camera/camera-01"

RECTANGLE = {"name": "Safe zone", "x_min": 0.1, "y_min": 0.1, "x_max": 0.9, "y_max": 0.9}


@pytest.fixture
def zone(auth_client, owner_headers):
    return auth_client.post(CAMERA_URL, json=RECTANGLE, headers=owner_headers).json()


def test_zones_start_empty(auth_client, owner_headers):
    assert auth_client.get(ZONES_URL, headers=owner_headers).json() == {"items": [], "total": 0}


def test_a_zone_is_created_and_listed(auth_client, owner_headers, zone):
    assert zone["camera_id"] == "camera-01"
    assert zone["name"] == "Safe zone"
    assert zone["active"] is True
    assert zone["outline"] is None
    assert auth_client.get(ZONES_URL, headers=owner_headers).json() == {"items": [zone], "total": 1}
    assert auth_client.get(f"{ZONES_URL}/{zone['id']}", headers=owner_headers).json() == zone


def test_a_zone_can_be_configured_before_the_camera_reports(auth_client, owner_headers, zone):
    # The zone is what the AI service needs in order to do anything useful, so
    # requiring the camera to have reported first is the wrong order.
    assert auth_client.get("/api/cameras").json()["total"] == 0
    assert zone["camera_id"] == "camera-01"


def test_a_polygon_is_stored_and_read_back(auth_client, owner_headers):
    outline = [{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.2}, {"x": 0.8, "y": 0.9}, {"x": 0.1, "y": 0.8}]
    body = auth_client.post(
        CAMERA_URL, json=RECTANGLE | {"outline": outline}, headers=owner_headers,
    ).json()
    assert body["outline"] == outline


@pytest.mark.parametrize("corners", [
    # Inverted: the whole frame reads as outside, so every visible animal
    # raises an alert and it looks like the detector has broken.
    {"x_min": 0.9, "y_min": 0.9, "x_max": 0.1, "y_max": 0.1},
    # Flat in one axis: zero area.
    {"x_min": 0.5, "y_min": 0.1, "x_max": 0.5, "y_max": 0.9},
    {"x_min": 0.1, "y_min": 0.5, "x_max": 0.9, "y_max": 0.5},
])
def test_a_rectangle_without_area_is_rejected(auth_client, owner_headers, corners):
    response = auth_client.post(CAMERA_URL, json=RECTANGLE | corners, headers=owner_headers)
    assert response.status_code == 422


@pytest.mark.parametrize("corners", [
    {"x_min": -0.1}, {"y_min": -0.1}, {"x_max": 1.5}, {"y_max": 1.5},
])
def test_a_coordinate_outside_the_frame_is_rejected(auth_client, owner_headers, corners):
    response = auth_client.post(CAMERA_URL, json=RECTANGLE | corners, headers=owner_headers)
    assert response.status_code == 422


def test_an_outline_outside_the_rectangle_is_rejected(auth_client, owner_headers):
    # The rectangle is the cheap rejection test applied before the polygon
    # test. If it does not contain the polygon, that shortcut discards points
    # that are genuinely inside and animals in the zone are reported as out.
    response = auth_client.post(CAMERA_URL, json={
        "name": "Poly", "x_min": 0.1, "y_min": 0.1, "x_max": 0.5, "y_max": 0.5,
        "outline": [{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.2}, {"x": 0.3, "y": 0.4}],
    }, headers=owner_headers)
    assert response.status_code == 422


@pytest.mark.parametrize("outline", [
    # Two vertices enclose no area.
    [{"x": 0.2, "y": 0.2}, {"x": 0.8, "y": 0.8}],
    # A coordinate outside the frame.
    [{"x": 0.2, "y": 0.2}, {"x": 1.4, "y": 0.3}, {"x": 0.5, "y": 0.6}],
    # A vertex missing a coordinate.
    [{"x": 0.2}, {"x": 0.8, "y": 0.3}, {"x": 0.5, "y": 0.6}],
])
def test_a_malformed_outline_is_rejected(auth_client, owner_headers, outline):
    response = auth_client.post(
        CAMERA_URL, json=RECTANGLE | {"outline": outline}, headers=owner_headers,
    )
    assert response.status_code == 422


def test_two_zones_of_one_camera_cannot_share_a_name(auth_client, owner_headers, zone):
    response = auth_client.post(CAMERA_URL, json=RECTANGLE, headers=owner_headers)
    assert response.status_code == 409
    assert "camera-01" in response.json()["detail"]


def test_two_cameras_may_each_have_a_safe_zone(auth_client, owner_headers, zone):
    # UNIQUE is (camera_id, name), not name alone.
    response = auth_client.post(
        "/api/zones/camera/camera-02", json=RECTANGLE, headers=owner_headers,
    )
    assert response.status_code == 201


def test_the_camera_filter_selects(auth_client, owner_headers, zone):
    auth_client.post("/api/zones/camera/camera-02", json=RECTANGLE, headers=owner_headers)
    assert auth_client.get(
        ZONES_URL, params={"camera_id": "camera-01"}, headers=owner_headers,
    ).json()["total"] == 1
    assert auth_client.get(ZONES_URL, headers=owner_headers).json()["total"] == 2

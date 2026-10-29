import pytest

ZONES_URL = "/api/zones"
CAMERA_URL = "/api/zones/camera/camera-01"
RECTANGLE = {"name": "Safe zone", "x_min": 0.1, "y_min": 0.1, "x_max": 0.9, "y_max": 0.9}


def call(client, method, url, body, headers):
    """Issues a request, omitting the body for the methods that take none.

    httpx's get() and delete() have no json parameter, so passing one raises a
    TypeError that reads as a test bug rather than a response.
    """
    kwargs = {} if headers is None else {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    return getattr(client, method)(url, **kwargs)


@pytest.fixture
def zone(auth_client, owner_headers):
    return auth_client.post(CAMERA_URL, json=RECTANGLE, headers=owner_headers).json()


def test_renaming_keeps_the_geometry(auth_client, owner_headers, zone):
    updated = auth_client.patch(
        f"{ZONES_URL}/{zone['id']}", json={"name": "Potrero norte"}, headers=owner_headers,
    ).json()
    assert updated["name"] == "Potrero norte"
    # The classic PATCH bug: an omitted field must keep its value, and here
    # blanking the geometry would mean losing the safe zone entirely.
    assert updated["x_min"] == 0.1
    assert updated["x_max"] == 0.9


def test_the_geometry_is_replaced_as_a_whole(auth_client, owner_headers, zone):
    updated = auth_client.patch(f"{ZONES_URL}/{zone['id']}", json={"geometry": {
        "name": "Safe zone", "x_min": 0.2, "y_min": 0.2, "x_max": 0.8, "y_max": 0.8,
    }}, headers=owner_headers).json()
    assert (updated["x_min"], updated["x_max"]) == (0.2, 0.8)


def test_an_invalid_geometry_update_is_rejected(auth_client, owner_headers, zone):
    # Validated on the way in, not only at creation: an inverted rectangle is
    # as harmful arriving through an update.
    response = auth_client.patch(f"{ZONES_URL}/{zone['id']}", json={"geometry": {
        "name": "Safe zone", "x_min": 0.9, "y_min": 0.9, "x_max": 0.1, "y_max": 0.1,
    }}, headers=owner_headers)
    assert response.status_code == 422


def test_a_zone_can_be_deactivated_without_being_deleted(auth_client, owner_headers, zone):
    updated = auth_client.patch(
        f"{ZONES_URL}/{zone['id']}", json={"active": False}, headers=owner_headers,
    ).json()
    assert updated["active"] is False
    assert auth_client.get(f"{ZONES_URL}/{zone['id']}", headers=owner_headers).status_code == 200


def test_updating_an_unknown_zone_is_a_404(auth_client, owner_headers):
    response = auth_client.patch(f"{ZONES_URL}/999", json={"active": False}, headers=owner_headers)
    assert response.status_code == 404


def test_a_zone_can_be_deleted(auth_client, owner_headers, zone):
    # A real delete, unlike animals: a zone is configuration and not history,
    # and a retired rectangle left behind would leave one camera with two
    # zones and no way to say which the AI service should use.
    assert auth_client.delete(f"{ZONES_URL}/{zone['id']}", headers=owner_headers).status_code == 204
    assert auth_client.get(f"{ZONES_URL}/{zone['id']}", headers=owner_headers).status_code == 404


def test_deleting_an_unknown_zone_is_a_404(auth_client, owner_headers):
    assert auth_client.delete(f"{ZONES_URL}/999", headers=owner_headers).status_code == 404


def test_deleting_frees_the_name(auth_client, owner_headers, zone):
    auth_client.delete(f"{ZONES_URL}/{zone['id']}", headers=owner_headers)
    assert auth_client.post(CAMERA_URL, json=RECTANGLE, headers=owner_headers).status_code == 201


def test_a_worker_may_read_the_zones(auth_client, worker_headers, zone):
    assert auth_client.get(ZONES_URL, headers=worker_headers).status_code == 200
    assert auth_client.get(f"{ZONES_URL}/{zone['id']}", headers=worker_headers).status_code == 200


@pytest.mark.parametrize("method,path,body", [
    ("post", "/camera/camera-02", RECTANGLE),
    ("patch", "/1", {"active": False}),
    ("delete", "/1", None),
])
def test_a_worker_may_not_change_the_zones(auth_client, worker_headers, zone, method, path, body):
    # The widest-effect write in the API: the zone is what every detection is
    # compared against.
    response = call(auth_client, method, f"{ZONES_URL}{path}", body, worker_headers)
    assert response.status_code == 403


@pytest.mark.parametrize("method,path,body", [
    ("get", "", None),
    ("get", "/1", None),
    ("post", "/camera/camera-02", RECTANGLE),
    ("patch", "/1", {"active": False}),
    ("delete", "/1", None),
])
def test_zones_are_not_public(auth_client, zone, method, path, body):
    # Reads included: a zone describes where the farm's animals are meant to
    # be, which is not something to hand to an anonymous caller.
    response = call(auth_client, method, f"{ZONES_URL}{path}", body, None)
    assert response.status_code == 401

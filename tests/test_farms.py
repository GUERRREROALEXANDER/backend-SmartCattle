from datetime import UTC, datetime

import pytest

from app.schemas.farm import FarmCreate
from app.services.farm_store import FarmNameTakenError, InMemoryFarmStore

FARMS_URL = "/api/farms"
FARM = {"name": "La Esperanza", "location": "Montería", "area_hectares": 120.5,
        "coordinates": {"latitude": 8.75, "longitude": -75.88}}
LOT = {"name": "North paddock", "kind": "paddock", "area_square_meters": 25000,
       "capacity": 40, "camera_id": "camera-01"}
NOW = datetime(2026, 10, 31, 8, tzinfo=UTC)


def call(client, method, url, body, headers):
    kwargs = {} if headers is None else {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    return getattr(client, method)(url, **kwargs)


def square(points: int) -> list[dict[str, float]]:
    return [{"latitude": 8.0 + i * 1e-4, "longitude": -75.0 + (i % 2) * 1e-4} for i in range(points)]


@pytest.fixture
def farm(auth_client, owner_headers):
    return auth_client.post(FARMS_URL, json=FARM, headers=owner_headers).json()


@pytest.fixture
def lot(auth_client, owner_headers, farm):
    return auth_client.post(f"{FARMS_URL}/{farm['id']}/lots", json=LOT, headers=owner_headers).json()


def test_an_owner_registers_a_farm(auth_client, owner, owner_headers):
    response = auth_client.post(FARMS_URL, json=FARM, headers=owner_headers)
    assert response.status_code == 201
    body = response.json()
    assert body["owner_id"] == owner["user"]["id"]
    assert body["coordinates"] == FARM["coordinates"]
    assert body["lot_count"] == 0
    assert body["created_at"] == body["updated_at"]


def test_the_name_is_trimmed_and_a_blank_location_is_none(auth_client, owner_headers):
    body = auth_client.post(
        FARMS_URL, json={"name": "  El Roble  ", "location": "   "}, headers=owner_headers,
    ).json()
    assert body["name"] == "El Roble"
    assert body["location"] is None


def test_a_blank_name_is_rejected(auth_client, owner_headers):
    assert auth_client.post(FARMS_URL, json={"name": "   "}, headers=owner_headers).status_code == 422


def test_an_owner_cannot_have_two_farms_with_one_name(auth_client, owner_headers, farm):
    assert auth_client.post(FARMS_URL, json=FARM, headers=owner_headers).status_code == 409


def test_two_owners_may_use_the_same_farm_name():
    store = InMemoryFarmStore()
    store.create_farm(1, FarmCreate(name="La Esperanza"), NOW)
    assert store.create_farm(2, FarmCreate(name="La Esperanza"), NOW).owner_id == 2
    with pytest.raises(FarmNameTakenError):
        store.create_farm(1, FarmCreate(name="La Esperanza"), NOW)


@pytest.mark.parametrize("coordinates,expected", [
    ({"latitude": 90, "longitude": 180}, 201),
    ({"latitude": -90, "longitude": -180}, 201),
    ({"latitude": 90.0001, "longitude": 0}, 422),
    ({"latitude": 0, "longitude": -180.0001}, 422),
    ({"latitude": 8.75}, 422),
])
def test_coordinates_are_validated(auth_client, owner_headers, coordinates, expected):
    response = auth_client.post(
        FARMS_URL, json={"name": "Edge", "coordinates": coordinates}, headers=owner_headers,
    )
    assert response.status_code == expected


@pytest.mark.parametrize("area,expected", [(0, 422), (0.01, 201), (1_000_000, 201), (1_000_000.1, 422)])
def test_the_area_must_be_positive_and_plausible(auth_client, owner_headers, area, expected):
    response = auth_client.post(FARMS_URL, json={"name": "Edge", "area_hectares": area}, headers=owner_headers)
    assert response.status_code == expected


def test_a_worker_may_read_farms_and_lots(auth_client, worker_headers, farm, lot):
    assert auth_client.get(FARMS_URL, headers=worker_headers).json()["total"] == 1
    assert auth_client.get(f"{FARMS_URL}/{farm['id']}", headers=worker_headers).json()["lot_count"] == 1
    assert auth_client.get(f"{FARMS_URL}/{farm['id']}/lots", headers=worker_headers).json()["total"] == 1
    assert auth_client.get(f"/api/lots/{lot['id']}", headers=worker_headers).status_code == 200


@pytest.mark.parametrize("method,url,body", [
    ("post", FARMS_URL, FARM),
    ("patch", f"{FARMS_URL}/1", {"name": "Other"}),
    ("delete", f"{FARMS_URL}/1", None),
    ("post", f"{FARMS_URL}/1/lots", LOT),
    ("patch", "/api/lots/1", {"capacity": 5}),
    ("delete", "/api/lots/1", None),
])
def test_a_worker_may_not_configure_farms(auth_client, worker_headers, lot, method, url, body):
    assert call(auth_client, method, url, body, worker_headers).status_code == 403


@pytest.mark.parametrize("method,url,body", [
    ("get", FARMS_URL, None),
    ("get", f"{FARMS_URL}/1", None),
    ("get", f"{FARMS_URL}/1/lots", None),
    ("get", "/api/lots/1", None),
    ("post", FARMS_URL, FARM),
])
def test_farms_are_not_public(auth_client, lot, method, url, body):
    assert call(auth_client, method, url, body, None).status_code == 401


def test_another_owners_farm_cannot_be_changed(auth_client, owner_headers):
    foreign = auth_client.app.state.farm_store.create_farm(999, FarmCreate(name="Foreign"), NOW)
    assert auth_client.patch(
        f"{FARMS_URL}/{foreign.id}", json={"name": "Mine"}, headers=owner_headers,
    ).status_code == 403
    assert auth_client.post(
        f"{FARMS_URL}/{foreign.id}/lots", json=LOT, headers=owner_headers,
    ).status_code == 403


def test_an_omitted_field_keeps_its_value_and_null_clears_it(auth_client, owner_headers, farm):
    updated = auth_client.patch(
        f"{FARMS_URL}/{farm['id']}", json={"location": None, "coordinates": None}, headers=owner_headers,
    ).json()
    assert updated["location"] is None and updated["coordinates"] is None
    assert updated["name"] == FARM["name"]
    assert updated["area_hectares"] == FARM["area_hectares"]


@pytest.mark.parametrize("body", [{}, {"name": None}])
def test_an_update_must_change_something_valid(auth_client, owner_headers, farm, body):
    assert auth_client.patch(f"{FARMS_URL}/{farm['id']}", json=body, headers=owner_headers).status_code == 422


def test_renaming_onto_another_farm_is_a_conflict(auth_client, owner_headers, farm):
    auth_client.post(FARMS_URL, json={"name": "El Roble"}, headers=owner_headers)
    response = auth_client.patch(f"{FARMS_URL}/{farm['id']}", json={"name": "El Roble"}, headers=owner_headers)
    assert response.status_code == 409


@pytest.mark.parametrize("method,url,body", [
    ("get", f"{FARMS_URL}/999", None),
    ("patch", f"{FARMS_URL}/999", {"name": "X"}),
    ("delete", f"{FARMS_URL}/999", None),
    ("get", f"{FARMS_URL}/999/lots", None),
    ("post", f"{FARMS_URL}/999/lots", LOT),
    ("get", "/api/lots/999", None),
    ("patch", "/api/lots/999", {"capacity": 1}),
    ("delete", "/api/lots/999", None),
])
def test_unknown_ids_are_a_404(auth_client, owner_headers, method, url, body):
    assert call(auth_client, method, url, body, owner_headers).status_code == 404


def test_a_farm_with_lots_cannot_be_deleted(auth_client, owner_headers, farm, lot):
    assert auth_client.delete(f"{FARMS_URL}/{farm['id']}", headers=owner_headers).status_code == 409
    assert auth_client.delete(f"/api/lots/{lot['id']}", headers=owner_headers).status_code == 204
    assert auth_client.delete(f"{FARMS_URL}/{farm['id']}", headers=owner_headers).status_code == 204
    assert auth_client.get(f"{FARMS_URL}/{farm['id']}", headers=owner_headers).status_code == 404


def test_a_lot_is_created_inside_its_farm(auth_client, owner_headers, farm, lot):
    assert lot["farm_id"] == farm["id"]
    assert lot["camera_id"] == "camera-01"
    assert lot["active"] is True


@pytest.mark.parametrize("points,expected", [(2, 422), (3, 201), (100, 201), (101, 422)])
def test_a_boundary_needs_between_3_and_100_points(auth_client, owner_headers, farm, points, expected):
    response = auth_client.post(
        f"{FARMS_URL}/{farm['id']}/lots",
        json={"name": f"Lot {points}", "kind": "pasture", "boundary": square(points)},
        headers=owner_headers,
    )
    assert response.status_code == expected


@pytest.mark.parametrize("field,value,expected", [
    ("capacity", -1, 422), ("capacity", 0, 201),
    ("area_square_meters", 0, 422), ("kind", "barn", 422),
    ("camera_id", "camera 01", 422),
])
def test_lot_fields_are_validated(auth_client, owner_headers, farm, field, value, expected):
    body = {"name": "Edge", "kind": "pen"} | {field: value}
    assert auth_client.post(
        f"{FARMS_URL}/{farm['id']}/lots", json=body, headers=owner_headers,
    ).status_code == expected


def test_lot_names_are_unique_per_farm_only(auth_client, owner_headers, farm, lot):
    assert auth_client.post(f"{FARMS_URL}/{farm['id']}/lots", json=LOT, headers=owner_headers).status_code == 409
    other = auth_client.post(FARMS_URL, json={"name": "El Roble"}, headers=owner_headers).json()
    assert auth_client.post(f"{FARMS_URL}/{other['id']}/lots", json=LOT, headers=owner_headers).status_code == 201


def test_lots_can_be_filtered_by_active(auth_client, owner_headers, farm, lot):
    auth_client.post(
        f"{FARMS_URL}/{farm['id']}/lots", json={"name": "Old stable", "kind": "stable", "active": False},
        headers=owner_headers,
    )
    url = f"{FARMS_URL}/{farm['id']}/lots"
    assert auth_client.get(url, headers=owner_headers).json()["total"] == 2
    active = auth_client.get(url, params={"active": True}, headers=owner_headers).json()
    assert [item["name"] for item in active["items"]] == [LOT["name"]]


def test_a_lot_update_is_partial(auth_client, owner_headers, lot):
    updated = auth_client.patch(
        f"/api/lots/{lot['id']}", json={"camera_id": None, "capacity": 55}, headers=owner_headers,
    ).json()
    assert updated["camera_id"] is None and updated["capacity"] == 55
    assert updated["name"] == LOT["name"] and updated["kind"] == LOT["kind"]


@pytest.mark.parametrize("body", [{}, {"kind": None}, {"active": None}])
def test_a_lot_update_must_change_something_valid(auth_client, owner_headers, lot, body):
    assert auth_client.patch(f"/api/lots/{lot['id']}", json=body, headers=owner_headers).status_code == 422


def test_renaming_a_lot_onto_a_sibling_is_a_conflict(auth_client, owner_headers, farm, lot):
    auth_client.post(f"{FARMS_URL}/{farm['id']}/lots", json={"name": "Pen 2", "kind": "pen"}, headers=owner_headers)
    response = auth_client.patch(f"/api/lots/{lot['id']}", json={"name": "Pen 2"}, headers=owner_headers)
    assert response.status_code == 409


# --- A lot with only a name, and lots described by their measurements ---


def lots_url(farm):
    return f"{FARMS_URL}/{farm['id']}/lots"


def test_a_lot_needs_nothing_but_a_name(auth_client, owner_headers, farm):
    response = auth_client.post(lots_url(farm), json={"name": "Lote 1"}, headers=owner_headers)
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "paddock"
    for unknown in ("area_square_meters", "length_meters", "width_meters", "capacity", "boundary", "camera_id"):
        assert body[unknown] is None


def test_the_area_follows_from_length_and_width(auth_client, owner_headers, farm):
    body = auth_client.post(
        lots_url(farm), json={"name": "Corral", "length_meters": 40, "width_meters": 25}, headers=owner_headers,
    ).json()
    assert (body["length_meters"], body["width_meters"], body["area_square_meters"]) == (40, 25, 1000)


def test_a_declared_area_close_to_length_times_width_is_kept(auth_client, owner_headers, farm):
    body = auth_client.post(lots_url(farm), json={
        "name": "Corral", "length_meters": 40, "width_meters": 25, "area_square_meters": 1005,
    }, headers=owner_headers).json()
    assert body["area_square_meters"] == 1005


@pytest.mark.parametrize("dimensions", [
    {"length_meters": 40},
    {"width_meters": 25},
    {"length_meters": 40, "width_meters": 25, "area_square_meters": 2000},
    {"length_meters": 0, "width_meters": 25},
    {"length_meters": -1, "width_meters": 25},
])
def test_inconsistent_dimensions_are_rejected(auth_client, owner_headers, farm, dimensions):
    response = auth_client.post(lots_url(farm), json={"name": "Corral", **dimensions}, headers=owner_headers)
    assert response.status_code == 422


def test_new_dimensions_bring_their_area(auth_client, owner_headers, lot):
    body = auth_client.patch(
        f"/api/lots/{lot['id']}", json={"length_meters": 100, "width_meters": 50}, headers=owner_headers,
    ).json()
    assert (body["length_meters"], body["width_meters"], body["area_square_meters"]) == (100, 50, 5000)


def test_a_new_area_alone_clears_the_dimensions(auth_client, owner_headers, farm):
    created = auth_client.post(
        lots_url(farm), json={"name": "Corral", "length_meters": 40, "width_meters": 25}, headers=owner_headers,
    ).json()
    body = auth_client.patch(
        f"/api/lots/{created['id']}", json={"area_square_meters": 1200}, headers=owner_headers,
    ).json()
    assert (body["length_meters"], body["width_meters"], body["area_square_meters"]) == (None, None, 1200)


def test_clearing_the_dimensions_keeps_the_area(auth_client, owner_headers, farm):
    created = auth_client.post(
        lots_url(farm), json={"name": "Corral", "length_meters": 40, "width_meters": 25}, headers=owner_headers,
    ).json()
    body = auth_client.patch(
        f"/api/lots/{created['id']}", json={"length_meters": None, "width_meters": None}, headers=owner_headers,
    ).json()
    assert (body["length_meters"], body["width_meters"], body["area_square_meters"]) == (None, None, 1000)


def test_one_side_cannot_be_changed_alone(auth_client, owner_headers, lot):
    response = auth_client.patch(f"/api/lots/{lot['id']}", json={"length_meters": 10}, headers=owner_headers)
    assert response.status_code == 422

"""Two people who register must never see, change or be told about each other's data."""

import pytest
from alembic import command
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.conftest import TEST_JWT_SECRET
from tests.test_database import alembic_config

NOW = "2026-11-02T12:00:00Z"
ZONE = {"name": "Corral", "x_min": 0.1, "y_min": 0.1, "x_max": 0.9, "y_max": 0.9}


@pytest.fixture(params=["memory", "database"])
def api(request, tmp_path):
    """Both stores: a filter that only exists in one of them would leak in the other."""
    url = None
    if request.param == "database":
        url = f"sqlite:///{(tmp_path / 'accounts.db').as_posix()}"
        command.upgrade(alembic_config(url), "head")
    settings = Settings(
        _env_file=None, allowed_origins="http://localhost:5173", smartcattle_ai_url=None, ai_api_key=None,
        database_url=url, jwt_secret=TEST_JWT_SECRET, account_isolation=True,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def sign_up(api, email):
    body = {"email": email, "password": "una-clave-larga", "full_name": "Persona"}
    response = api.post("/api/auth/register", json=body)
    assert response.status_code == 201
    return response.json()["user"], {"Authorization": f"Bearer {response.json()['access_token']}"}


def save(api, headers, name="Potrero", source="192.168.1.20:8080"):
    response = api.post("/api/cameras/saved", json={"name": name, "source": source}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


def report(api, camera_id):
    body = {"status": "online", "observed_at": NOW}
    assert api.put(f"/api/ai/cameras/{camera_id}/status", json=body).status_code == 200


def raise_event(api, camera_id):
    return api.post("/api/ai/events", json={
        "event_type": "cattle_out_of_zone", "camera_id": camera_id, "detected_object": "cow",
        "confidence": 0.95, "timestamp": NOW,
    })


@pytest.fixture
def two(api):
    _, ana = sign_up(api, "ana@finca.example")
    _, beto = sign_up(api, "beto@otra.example")
    return ana, beto


def test_each_registration_owns_a_new_empty_space(api):
    first, _ = sign_up(api, "ana@finca.example")
    second, headers = sign_up(api, "beto@otra.example")
    assert first["role"] == second["role"] == "owner"
    assert first["id"] != second["id"]
    assert api.get("/api/cameras/saved", headers=headers).json()["total"] == 0


def test_registering_the_same_address_twice_makes_no_second_account(api):
    sign_up(api, "ana@finca.example")
    again = api.post("/api/auth/register", json={
        "email": "ANA@finca.example", "password": "otra-clave-larga", "full_name": "Otra",
    })
    assert again.status_code == 409


@pytest.mark.parametrize("path", [
    "/api/cameras", "/api/cameras/saved", "/api/events", "/api/animals", "/api/metrics/summary", "/api/videos/analyses",
])
def test_nothing_is_readable_without_a_session(api, path):
    assert api.get(path).status_code == 401


def test_a_camera_nobody_saved_is_shown_to_nobody(api, two):
    ana, _ = two
    report(api, "camera-01")
    assert api.get("/api/cameras", headers=ana).json()["total"] == 0


def test_a_saved_camera_opens_nothing_and_is_listed_offline(api, two):
    ana, _ = two
    save(api, ana)
    assert api.get("/api/cameras/saved", headers=ana).json()["items"][0]["status"] == "offline"
    assert api.get("/api/cameras", headers=ana).json()["total"] == 0


def test_a_saved_camera_never_keeps_a_password(api, two):
    ana, _ = two
    body = {"name": "x", "source": "rtsp://admin:clave@192.168.1.9/live"}
    response = api.post("/api/cameras/saved", json=body, headers=ana)
    assert response.status_code == 422


def test_cameras_stay_in_their_account(api, two):
    ana, beto = two
    camera = save(api, ana, name="Potrero norte")
    report(api, camera)
    listed = api.get("/api/cameras", headers=ana).json()["items"]
    assert [(item["id"], item["display_name"]) for item in listed] == [(camera, "Potrero norte")]
    assert api.get("/api/cameras", headers=beto).json()["total"] == 0
    assert api.get("/api/cameras/saved", headers=beto).json()["total"] == 0
    assert api.get(f"/api/cameras/{camera}", headers=beto).status_code == 404
    assert api.get(f"/api/cameras/saved/{camera}", headers=beto).status_code == 404
    assert api.delete(f"/api/cameras/saved/{camera}", headers=beto).status_code == 404
    assert api.get(f"/api/cameras/saved/{camera}", headers=ana).status_code == 200


def test_disconnecting_keeps_the_camera_saved(api, two):
    ana, _ = two
    camera = save(api, ana)
    report(api, camera)
    assert api.put(f"/api/ai/cameras/{camera}/source", json={"source": "x", "active": False}).status_code == 204
    assert api.get("/api/cameras", headers=ana).json()["total"] == 0
    assert api.get("/api/cameras/saved", headers=ana).json()["items"][0]["status"] == "offline"


def test_deleting_takes_it_off_the_page(api, two):
    ana, _ = two
    camera = save(api, ana)
    assert api.delete(f"/api/cameras/saved/{camera}", headers=ana).status_code == 204
    assert api.get("/api/cameras/saved", headers=ana).json()["total"] == 0


def test_events_alerts_and_metrics_stay_in_their_account(api, two):
    ana, beto = two
    camera = save(api, ana)
    assert raise_event(api, camera).status_code == 201
    assert api.get("/api/events", headers=ana).json()["total"] == 1
    assert api.get("/api/events", headers=beto).json()["total"] == 0
    assert api.get(f"/api/events?camera_id={camera}", headers=beto).json()["total"] == 0
    alert = api.get("/api/alerts", headers=ana).json()["items"][0]["id"]
    assert api.get("/api/alerts", headers=beto).json()["total"] == 0
    assert api.get(f"/api/alerts/{alert}", headers=beto).status_code == 404
    assert api.post(f"/api/alerts/{alert}/resolve", headers=beto).status_code == 404
    assert api.get(f"/api/alerts/{alert}", headers=ana).json()["status"] == "open"
    assert api.get("/api/metrics/live", headers=ana).json()["open_alerts"] == 1
    assert api.get("/api/metrics/live", headers=beto).json()["open_alerts"] == 0
    assert api.get("/api/reports/alerts", headers=beto).json()["total"] == 0


def test_animals_stay_in_their_account(api, two):
    ana, beto = two
    assert api.post("/api/animals", json={"tag": "A-1", "name": "Lola"}, headers=ana).status_code == 201
    assert api.get("/api/animals", headers=ana).json()["total"] == 1
    assert api.get("/api/animals", headers=beto).json()["total"] == 0
    assert api.get("/api/animals/A-1", headers=beto).status_code == 404
    assert api.patch("/api/animals/A-1", json={"name": "Robada"}, headers=beto).status_code == 404
    assert api.post("/api/animals/A-1/deactivate", headers=beto).status_code == 404
    assert api.get("/api/animals/A-1/exists", headers=beto).json() == {"exists": False}


def test_an_animal_cannot_be_placed_on_another_accounts_camera(api, two):
    ana, beto = two
    camera = save(api, ana)
    assert api.post("/api/animals", json={"tag": "B-1", "camera_id": camera}, headers=beto).status_code == 404


def test_farms_and_lots_stay_in_their_account(api, two):
    ana, beto = two
    farm = api.post("/api/farms", json={"name": "La Esperanza"}, headers=ana).json()["id"]
    lot = api.post(f"/api/farms/{farm}/lots", json={"name": "Norte"}, headers=ana).json()["id"]
    assert api.get("/api/farms", headers=beto).json()["total"] == 0
    assert api.get(f"/api/farms/{farm}", headers=beto).status_code == 404
    assert api.get(f"/api/farms/{farm}/lots", headers=beto).status_code == 404
    assert api.delete(f"/api/farms/{farm}", headers=beto).status_code == 404
    assert api.get(f"/api/lots/{lot}", headers=beto).status_code == 404
    assert api.post(f"/api/farms/{farm}/lots", json={"name": "Mio"}, headers=beto).status_code == 404
    # Two farms may share a name across accounts: nobody can tell the other exists.
    assert api.post("/api/farms", json={"name": "La Esperanza"}, headers=beto).status_code == 201


def test_a_zone_cannot_be_drawn_or_read_on_another_accounts_camera(api, two):
    ana, beto = two
    camera = save(api, ana)
    created = api.post(f"/api/zones/camera/{camera}", json=ZONE, headers=ana)
    assert created.status_code == 201, created.text
    zone = created.json()["id"]
    assert api.post(f"/api/zones/camera/{camera}", json={**ZONE, "name": "Otra"}, headers=beto).status_code == 404
    assert api.get("/api/zones", headers=beto).json()["total"] == 0
    assert api.get(f"/api/zones/{zone}", headers=beto).status_code == 404
    assert api.delete(f"/api/zones/{zone}", headers=beto).status_code == 404
    assert api.get(f"/api/zones/{zone}", headers=ana).status_code == 200


def test_staff_share_their_owners_space_and_no_other(api, two):
    ana, beto = two
    camera = save(api, ana)
    created = api.post("/api/users", json={
        "email": "peon@finca.example", "password": "una-clave-larga", "full_name": "Peon", "role": "worker",
    }, headers=ana)
    assert created.status_code == 201
    token = api.post("/api/auth/login", json={"email": "peon@finca.example", "password": "una-clave-larga"}).json()
    worker = {"Authorization": f"Bearer {token['access_token']}"}
    assert [item["id"] for item in api.get("/api/cameras/saved", headers=worker).json()["items"]] == [camera]
    assert api.post("/api/cameras/saved", json={"name": "x", "source": "1"}, headers=worker).status_code == 403
    assert api.get("/api/users", headers=ana).json()["total"] == 2
    assert api.get("/api/users", headers=beto).json()["total"] == 1
    assert api.get(f"/api/users/{created.json()['id']}", headers=beto).status_code == 404
    assert api.patch(f"/api/users/{created.json()['id']}", json={"is_active": False}, headers=beto).status_code == 404


def test_each_owner_is_the_last_of_their_own_account(api, two):
    ana, _ = two
    me = api.get("/api/auth/me", headers=ana).json()["id"]
    assert api.patch(f"/api/users/{me}", json={"role": "worker"}, headers=ana).status_code == 409


def test_the_audit_log_shows_only_the_account(api, two):
    ana, beto = two
    save(api, ana)
    actions = [item["action"] for item in api.get("/api/audit-log", headers=beto).json()["items"]]
    assert "camera.saved" not in actions
    assert "camera.saved" in [item["action"] for item in api.get("/api/audit-log", headers=ana).json()["items"]]


def test_uploaded_analyses_stay_in_their_account(api, two, video_analysis_payload):
    ana, beto = two
    created = api.post("/api/videos/analyses", json=video_analysis_payload, headers=ana)
    assert created.status_code == 201
    assert api.get("/api/videos/analyses", headers=beto).json()["total"] == 0
    assert api.get(f"/api/videos/analyses/{created.json()['id']}", headers=beto).status_code == 404
    assert api.get("/api/events", headers=ana).json()["total"] == 1
    assert api.get("/api/events", headers=beto).json()["total"] == 0
    assert api.get("/api/metrics/summary", headers=beto).json()["animals_inside"] is None


def live_messages(api, headers, act):
    with api.websocket_connect("/ws/live") as socket:
        socket.send_json({"type": "auth", "token": headers["Authorization"].removeprefix("Bearer ")})
        assert socket.receive_json()["type"] == "hello"
        act()
        socket.send_json({"type": "ping"})
        received = []
        while True:
            message = socket.receive_json()
            if message["type"] == "pong":
                return received
            received.append(message["type"])


def test_the_live_channel_only_carries_the_account(api, two):
    ana, beto = two
    camera = save(api, ana)
    assert live_messages(api, beto, lambda: (report(api, camera), raise_event(api, camera))) == []
    received = live_messages(api, ana, lambda: (report(api, camera), raise_event(api, camera)))
    assert {"camera.status", "event.created", "alert.created"} <= set(received)


def test_a_qr_code_saves_one_camera_in_the_account_that_asked_for_it(api, two):
    ana, beto = two
    code = api.post("/api/cameras/pairing", headers=ana).json()["code"]
    body = {"code": code, "name": "Celular de Ana", "source": "192.168.1.20:8080"}
    assert api.post("/api/cameras/paired", json=body).status_code == 201
    assert [item["name"] for item in api.get("/api/cameras/saved", headers=ana).json()["items"]] == ["Celular de Ana"]
    assert api.get("/api/cameras/saved", headers=beto).json()["total"] == 0
    # Once only: a photographed QR is worth nothing afterwards.
    assert api.post("/api/cameras/paired", json=body).status_code == 403


def test_a_made_up_qr_code_is_refused(api):
    body = {"code": "inventado", "name": "x", "source": "1"}
    assert api.post("/api/cameras/paired", json=body).status_code == 403


def test_a_worker_cannot_ask_for_a_qr(api, two):
    ana, _ = two
    api.post("/api/users", json={
        "email": "peon@finca.example", "password": "una-clave-larga", "full_name": "Peon", "role": "worker",
    }, headers=ana)
    token = api.post("/api/auth/login", json={"email": "peon@finca.example", "password": "una-clave-larga"}).json()
    worker = {"Authorization": f"Bearer {token['access_token']}"}
    assert api.post("/api/cameras/pairing", headers=worker).status_code == 403

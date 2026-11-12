"""Imou cameras connected from the dashboard: recommended from the Imou cloud, watchable from anywhere."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.schemas.camera import CameraConnect
from app.services.imou_cloud import ImouCloud, ImouDevice, ImouError

SERIAL = "04797CEPSF59DA6"


class FakeCloud:
    def __init__(self, devices=None, fail=False):
        self.items = devices if devices is not None else [
            ImouDevice(serial=SERIAL, name="Corral", model="IPC-F22", online=True),
        ]
        self.fail = fail

    def devices(self):
        if self.fail:
            raise ImouError("The Imou cloud refused the request (OP1009)")
        return list(self.items)

    def device(self, serial):
        return next((item for item in self.devices() if item.serial == serial), None)


@pytest.fixture
def cloud(auth_client):
    fake = FakeCloud()
    auth_client.app.state.imou_cloud = fake
    return fake


def test_a_lan_address_recommends_the_imou_camera(auth_client, owner_headers, cloud):
    answer = auth_client.post("/api/cameras/connect", json={"address": "192.168.100.30"}, headers=owner_headers)
    assert answer.status_code == 200
    body = answer.json()
    assert body["outcome"] == "choose"
    assert body["recommended"] == SERIAL
    assert "inside the farm's Wi-Fi" in body["message"]
    assert [item["serial"] for item in body["suggestions"]] == [SERIAL]


def test_connecting_the_recommendation_keeps_the_typed_address(auth_client, owner_headers, cloud):
    answer = auth_client.post(
        "/api/cameras/connect", json={"serial": SERIAL, "address": "192.168.100.30"}, headers=owner_headers,
    )
    assert answer.status_code == 200
    camera = answer.json()["camera"]
    assert answer.json()["outcome"] == "connected"
    assert camera["name"] == "Corral"
    assert camera["address"] == "192.168.100.30"
    assert camera["cloud"] is True
    assert camera["source"] == f"imou:{SERIAL}"

    saved = auth_client.get("/api/cameras/saved", headers=owner_headers).json()["items"]
    assert [(item["id"], item["address"]) for item in saved] == [(camera["id"], "192.168.100.30")]
    suggestion = auth_client.get("/api/cameras/suggestions", headers=owner_headers).json()["items"][0]
    assert suggestion["camera_id"] == camera["id"]


def test_typing_the_serial_connects_it_and_twice_is_the_same_camera(auth_client, owner_headers, cloud):
    first = auth_client.post("/api/cameras/connect", json={"address": SERIAL.lower()}, headers=owner_headers).json()
    second = auth_client.post(
        "/api/cameras/connect", json={"serial": SERIAL, "address": "192.168.100.31"}, headers=owner_headers,
    ).json()
    assert first["camera"]["id"] == second["camera"]["id"]
    assert first["camera"]["address"] is None
    assert second["camera"]["address"] == "192.168.100.31"
    assert len(auth_client.get("/api/cameras/saved", headers=owner_headers).json()["items"]) == 1


def test_a_connected_camera_is_no_longer_recommended(auth_client, owner_headers, cloud):
    auth_client.post("/api/cameras/connect", json={"serial": SERIAL}, headers=owner_headers)
    body = auth_client.post("/api/cameras/connect", json={"address": "192.168.1.9"}, headers=owner_headers).json()
    assert body["outcome"] == "unreachable"
    assert body["suggestions"] == []


def test_an_unknown_serial_is_a_404(auth_client, owner_headers, cloud):
    answer = auth_client.post("/api/cameras/connect", json={"serial": "ZZZ999ZZZ"}, headers=owner_headers)
    assert answer.status_code == 404


def test_without_an_imou_account_an_address_is_explained(auth_client, owner_headers):
    body = auth_client.post("/api/cameras/connect", json={"address": "192.168.100.30"}, headers=owner_headers).json()
    assert body["outcome"] == "unreachable"
    assert auth_client.post("/api/cameras/connect", json={"serial": SERIAL}, headers=owner_headers).status_code == 503
    listing = auth_client.get("/api/cameras/suggestions", headers=owner_headers).json()
    assert listing == {"available": False, "message": "No Imou account is configured on this server", "items": []}


def test_an_imou_outage_does_not_break_the_page(auth_client, owner_headers, cloud):
    cloud.fail = True
    listing = auth_client.get("/api/cameras/suggestions", headers=owner_headers).json()
    assert listing["available"] is False
    assert auth_client.post("/api/cameras/connect", json={"serial": SERIAL}, headers=owner_headers).status_code == 502
    body = auth_client.post("/api/cameras/connect", json={"address": "10.0.0.5"}, headers=owner_headers).json()
    assert body["outcome"] == "unreachable"


@pytest.fixture
def isolated_client(auth_settings):
    with TestClient(create_app(auth_settings.model_copy(update={"account_isolation": True}))) as client:
        client.app.state.imou_cloud = FakeCloud()
        yield client


def _register(client, email):
    token = client.post("/api/auth/register", json={
        "email": email, "password": "una-clave-larga", "full_name": "Finca",
    }).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_another_accounts_camera_is_neither_shown_nor_taken(isolated_client):
    first, second = _register(isolated_client, "ana@finca.example"), _register(isolated_client, "otra@finca.example")
    connected = isolated_client.post("/api/cameras/connect", json={"serial": SERIAL}, headers=first)
    assert connected.json()["outcome"] == "connected"
    assert isolated_client.get("/api/cameras/suggestions", headers=first).json()["items"][0]["camera_id"]
    assert isolated_client.get("/api/cameras/suggestions", headers=second).json()["items"] == []
    assert isolated_client.post("/api/cameras/connect", json={"serial": SERIAL}, headers=second).status_code == 409


def test_only_owners_connect(auth_client, worker_headers, cloud):
    answer = auth_client.post("/api/cameras/connect", json={"serial": SERIAL}, headers=worker_headers)
    assert answer.status_code == 403


@pytest.mark.parametrize("address", ["rtsp://admin:clave@192.168.1.5/live", "admin@192.168.1.5", "a b"])
def test_an_address_with_a_password_or_junk_is_refused(auth_client, owner_headers, cloud, address):
    answer = auth_client.post("/api/cameras/connect", json={"address": address}, headers=owner_headers)
    assert answer.status_code == 422
    assert "clave" not in answer.text


@pytest.mark.parametrize(("typed", "host"), [
    ("rtsp://192.168.1.5:554/cam/realmonitor", "192.168.1.5"), ("192.168.1.5:554", "192.168.1.5"),
    (" camara.local ", "camara.local"),
])
def test_only_the_host_of_a_pasted_link_is_kept(typed, host):
    assert CameraConnect(address=typed).address == host


@pytest.fixture
def keyed_client(auth_settings):
    settings = auth_settings.model_copy(update={"ai_api_key": auth_settings.jwt_secret})
    with TestClient(create_app(settings)) as client:
        client.app.state.imou_cloud = FakeCloud()
        yield client


def test_the_cloud_ai_lists_the_imou_cameras_with_its_key(keyed_client, owner_payload):
    token = keyed_client.post("/api/auth/register", json=owner_payload).json()["access_token"]
    keyed_client.post("/api/cameras/connect", json={"serial": SERIAL}, headers={"Authorization": f"Bearer {token}"})
    key = keyed_client.app.state.settings.ai_api_key.get_secret_value()
    assert keyed_client.get("/api/ai/cameras/cloud").status_code == 401
    items = keyed_client.get("/api/ai/cameras/cloud", headers={"X-API-Key": key}).json()["items"]
    assert [item["source"] for item in items] == [f"imou:{SERIAL}"]


def test_the_cloud_list_is_shut_without_a_configured_key(auth_client):
    assert auth_client.get("/api/ai/cameras/cloud").status_code == 503


def test_imou_settings_come_in_pairs():
    with pytest.raises(ValueError, match="IMOU_APP_ID"):
        Settings(_env_file=None, imou_app_id="lc123")


def test_the_client_signs_lists_pages_and_renews_an_expired_token():
    calls = []
    answers = iter([
        {"result": {"code": "0", "data": {"accessToken": "t1", "expireTime": 3600}}},
        {"result": {"code": "TK1002", "msg": "token expired"}},
        {"result": {"code": "0", "data": {"accessToken": "t2", "expireTime": 3600}}},
        {"result": {"code": "0", "data": {"deviceList": [
            {"deviceId": "B2", "deviceName": "Potrero", "deviceStatus": "offline"},
            {"deviceId": "A1", "deviceName": "Corral", "deviceStatus": "online", "deviceModel": "F22"},
            {"deviceName": "sin serie"},
        ]}}},
    ])

    def post(url, body):
        calls.append((url.rsplit("/", 1)[1], body["params"], body["system"]["appId"]))
        return next(answers)

    cloud = ImouCloud("lc-app", "secret", "https://imou.example/openapi", post=post, clock=lambda: 1000.0)
    devices = cloud.devices()
    assert [(d.serial, d.online) for d in devices] == [("A1", True), ("B2", False)]
    assert [call[0] for call in calls] == ["accessToken", "listDeviceDetailsByPage", "accessToken",
                                           "listDeviceDetailsByPage"]
    assert calls[-1][1]["token"] == "t2" and calls[-1][1]["page"] == 1
    assert cloud.devices() == devices  # cached
    assert len(calls) == 4


def test_the_client_reports_a_refusal_without_its_message():
    cloud = ImouCloud("lc-app", "secret", post=lambda url, body: {"result": {"code": "OP1009", "msg": "appId lc-app"}})
    with pytest.raises(ImouError) as raised:
        cloud.devices()
    assert raised.value.code == "OP1009"
    assert "lc-app" not in str(raised.value)

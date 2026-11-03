"""A PC at each farm: linked with a code, and with a key that only opens the cameras of its own account."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.dependencies import get_clock
from app.main import create_app
from app.services.farm_agents import account_of, farm_key
from tests.conftest import TEST_JWT_SECRET
from tests.test_accounts import NOW, save, sign_up

SHARED = "la-clave-compartida-del-servicio"
URL = "https://vaca-feliz.trycloudflare.com"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def api(clock):
    settings = Settings(
        _env_file=None, allowed_origins="https://panel.example", smartcattle_ai_url=None, ai_api_key=SHARED,
        database_url=None, jwt_secret=TEST_JWT_SECRET, account_isolation=True, require_auth_for_reads=True,
    )
    app = create_app(settings)
    app.dependency_overrides[get_clock] = lambda: clock
    with TestClient(app) as client:
        yield client


def link(api, owner):
    code = api.post("/api/farm-pc/pairing", headers=owner).json()["code"]
    response = api.post("/api/farm-pc/paired", json={"code": code})
    assert response.status_code == 201
    return {"X-API-Key": response.json()["key"]}


@pytest.fixture
def ana(api):
    return sign_up(api, "ana@finca.example")[1]


@pytest.fixture
def beto(api):
    return sign_up(api, "beto@lejos.example")[1]


def status_report():
    return {"status": "online", "observed_at": NOW}


def test_a_short_code_links_the_pc_once_and_tells_it_the_panels_address(api, ana):
    code = api.post("/api/farm-pc/pairing", headers=ana).json()["code"]
    assert len(code) == 8 and code.isalnum()
    first = api.post("/api/farm-pc/paired", json={"code": f" {code.lower()} "})
    assert first.status_code == 201
    assert first.json()["origins"] == ["https://panel.example"]
    assert api.post("/api/farm-pc/paired", json={"code": code}).status_code == 403


def test_only_someone_signed_in_can_ask_for_a_code(api):
    assert api.post("/api/farm-pc/pairing").status_code == 401


def test_a_code_expires(api, ana, clock):
    code = api.post("/api/farm-pc/pairing", headers=ana).json()["code"]
    clock.now += timedelta(minutes=11)
    assert api.post("/api/farm-pc/paired", json={"code": code}).status_code == 403


def test_a_made_up_key_is_refused(api, ana):
    forged = farm_key(1, "otro-secreto-que-no-es-el-del-servidor-en-absoluto")
    camera = save(api, ana)
    body = status_report()
    assert api.put(f"/api/ai/cameras/{camera}/status", json=body, headers={"X-API-Key": forged}).status_code == 401
    assert api.put(f"/api/ai/cameras/{camera}/status", json=body, headers={"X-API-Key": "farm-x-y"}).status_code == 401


def test_a_key_opens_the_cameras_of_its_account_and_no_others(api, ana, beto):
    key = link(api, ana)
    own, other = save(api, ana), save(api, beto, source="192.168.9.9:8080")
    body = status_report()
    assert api.put(f"/api/ai/cameras/{own}/status", json=body, headers=key).status_code == 200
    assert api.put(f"/api/ai/cameras/{other}/status", json=body, headers=key).status_code == 401
    assert api.get(f"/api/ai/cameras/{own}/zones", headers=key).status_code == 200
    assert api.get(f"/api/ai/cameras/{other}/zones", headers=key).status_code == 401


def test_a_key_cannot_raise_an_event_on_someone_elses_camera(api, ana, beto):
    key = link(api, ana)
    own, other = save(api, ana), save(api, beto, source="192.168.9.9:8080")
    event = {"event_type": "cattle_out_of_zone", "detected_object": "cow", "confidence": 0.95, "timestamp": NOW}
    assert api.post("/api/ai/events", json={**event, "camera_id": own}, headers=key).status_code == 201
    assert api.post("/api/ai/events", json={**event, "camera_id": other}, headers=key).status_code == 401


def test_a_key_is_not_good_for_uploaded_videos_or_animals(api, ana):
    key = link(api, ana)
    assert api.post("/api/ai/video-jobs/claim", json={}, headers=key).status_code == 401


def test_the_shared_key_still_works_for_a_service_of_your_own(api, ana):
    camera = save(api, ana)
    shared = {"X-API-Key": SHARED}
    assert api.put(f"/api/ai/cameras/{camera}/status", json=status_report(), headers=shared).status_code == 200


def test_each_account_finds_only_its_own_pc(api, ana, beto):
    assert api.get("/api/farm-pc", headers=ana).json() == {"url": None}
    assert api.put("/api/ai/farm-pc", json={"url": URL}, headers=link(api, ana)).status_code == 204
    assert api.get("/api/farm-pc", headers=ana).json() == {"url": URL}
    assert api.get("/api/farm-pc", headers=beto).json() == {"url": None}


def test_a_pc_that_stops_reporting_stops_being_offered(api, ana, clock):
    api.put("/api/ai/farm-pc", json={"url": URL}, headers=link(api, ana))
    clock.now += timedelta(seconds=91)
    assert api.get("/api/farm-pc", headers=ana).json() == {"url": None}


def test_the_new_address_replaces_the_old_one(api, ana):
    key = link(api, ana)
    api.put("/api/ai/farm-pc", json={"url": URL}, headers=key)
    api.put("/api/ai/farm-pc", json={"url": "https://otra.trycloudflare.com/"}, headers=key)
    assert api.get("/api/farm-pc", headers=ana).json() == {"url": "https://otra.trycloudflare.com"}


def test_the_shared_key_cannot_claim_to_be_a_farm(api):
    assert api.put("/api/ai/farm-pc", json={"url": URL}, headers={"X-API-Key": SHARED}).status_code == 403


def test_only_the_accounts_people_may_look_around_its_network(api, ana, beto):
    key = link(api, ana)
    assert api.get("/api/ai/farm-pc/access", headers={**key, **ana}).status_code == 204
    assert api.get("/api/ai/farm-pc/access", headers={**key, **beto}).status_code == 403
    assert api.get("/api/ai/farm-pc/access", headers=key).status_code in (401, 403)


def test_keys_can_be_read_back_only_with_the_secret():
    key = farm_key(7, TEST_JWT_SECRET)
    assert account_of(key, TEST_JWT_SECRET) == 7
    assert account_of(key.replace("farm-7-", "farm-8-"), TEST_JWT_SECRET) is None
    assert account_of(key, TEST_JWT_SECRET + "x") is None

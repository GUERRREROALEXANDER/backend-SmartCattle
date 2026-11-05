"""WhatsApp notices: a verified number, a queue the sending service claims from, and limits on how often it speaks."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.dependencies import get_clock
from app.main import create_app
from app.services.whatsapp_verification import HOURLY_LIMIT, MAX_ATTEMPTS, PhoneVerifications, TooManyCodesError
from tests.conftest import TEST_JWT_SECRET
from tests.test_farm_pc import Clock

SERVICE = {"X-API-Key": "whatsapp-service-key"}
PHONE = "+573001234567"
PASSWORD = "una-clave-larga"
MOMENT = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture(params=["memory", "database"])
def api(request, tmp_path, monkeypatch, clock):
    """Both stores: the queue's rules have to hold in the one that happens to be running."""
    url = None
    if request.param == "database":
        url = f"sqlite:///{tmp_path / 'whatsapp.db'}"
        monkeypatch.setenv("DATABASE_URL", url)
        command.upgrade(Config("alembic.ini"), "head")
    settings = Settings(
        _env_file=None, database_url=url, ai_api_key=SERVICE["X-API-Key"], jwt_secret=TEST_JWT_SECRET,
        account_isolation=True, rate_limit_enabled=False,
    )
    app = create_app(settings)
    app.dependency_overrides[get_clock] = lambda: clock
    with TestClient(app) as client:
        yield client


def sign_up(api, email):
    body = {"email": email, "password": PASSWORD, "full_name": "Persona De Prueba"}
    return {"Authorization": f"Bearer {api.post('/api/auth/register', json=body).json()['access_token']}"}


def claim(api, **params):
    response = api.post("/api/ai/whatsapp/claim", headers=SERVICE, params=params)
    assert response.status_code == 200
    return response.json()["items"]


def settle_all(api):
    for message in claim(api):
        assert api.post(f"/api/ai/whatsapp/{message['id']}/sent", headers=SERVICE).status_code == 204


def code_in(message):
    return re.search(r"\b\d{6}\b", message["body"]).group()


def link_phone(api, headers, clock, phone=PHONE):
    assert api.post("/api/auth/me/whatsapp/verification", json={"phone": phone}, headers=headers).status_code == 202
    message = claim(api)[-1]
    response = api.post("/api/auth/me/whatsapp/verify", json={"code": code_in(message)}, headers=headers)
    assert response.status_code == 200
    assert api.post(f"/api/ai/whatsapp/{message['id']}/sent", headers=SERVICE).status_code == 204
    clock.now += timedelta(seconds=61)


def save_camera(api, headers, name="Potrero norte"):
    body = {"name": name, "source": "rtsp://192.168.1.50:554/live"}
    return api.post("/api/cameras/saved", json=body, headers=headers).json()["id"]


def detect(api, camera_id, confidence=0.95):
    body = {
        "event_type": "cattle_out_of_zone", "camera_id": camera_id, "detected_object": "cow",
        "confidence": confidence, "timestamp": "2026-11-04T12:00:00Z",
    }
    assert api.post("/api/ai/events", json=body, headers=SERVICE).status_code == 201


@pytest.fixture
def ana(api):
    return sign_up(api, "ana@finca.example")


@pytest.fixture
def camera_id(api, ana):
    return save_camera(api, ana)


def test_a_verified_number_receives_the_alert_with_the_camera_name(api, ana, camera_id, clock):
    link_phone(api, ana, clock)
    detect(api, camera_id)
    [message] = claim(api)
    assert message["phone"] == PHONE
    assert "Potrero norte" in message["body"] and "ganado fuera de la zona" in message["body"]


def test_the_alert_shows_the_farm_local_time(api, ana, camera_id, clock):
    link_phone(api, ana, clock)
    clock.now = datetime(2026, 11, 4, 20, 42, tzinfo=UTC)
    detect(api, camera_id)
    assert "03:42 PM" in claim(api)[0]["body"]


@pytest.mark.parametrize(("delay", "shown"), [
    (timedelta(hours=1), "02:00 PM"),
    (timedelta(hours=1, seconds=1), "03:00 PM"),
    (timedelta(hours=-1), "02:00 PM"),
    (timedelta(hours=-1, seconds=-1), "12:59 PM"),
])
def test_the_alert_shows_when_it_was_detected_unless_that_clock_looks_wrong(api, ana, camera_id, clock, delay, shown):
    """Detected at 19:00 UTC (02:00 PM on the farm); what is shown depends on how far that is from the arrival."""
    link_phone(api, ana, clock)
    clock.now = datetime(2026, 11, 4, 19, 0, tzinfo=UTC) + delay
    body = {
        "event_type": "cattle_out_of_zone", "camera_id": camera_id, "detected_object": "cow",
        "confidence": 0.95, "timestamp": "2026-11-04T19:00:00Z",
    }
    assert api.post("/api/ai/events", json=body, headers=SERVICE).status_code == 201
    assert shown in claim(api)[0]["body"]


def test_a_number_that_was_not_verified_receives_no_alert(api, ana, camera_id):
    assert api.post("/api/auth/me/whatsapp/verification", json={"phone": PHONE}, headers=ana).status_code == 202
    settle_all(api)
    detect(api, camera_id)
    assert claim(api) == []


def test_notices_switched_off_send_nothing_and_back_on_send_again(api, ana, camera_id, clock):
    link_phone(api, ana, clock)
    assert api.patch("/api/auth/me/whatsapp", json={"enabled": False}, headers=ana).json()["enabled"] is False
    detect(api, camera_id)
    assert claim(api) == []
    assert api.patch("/api/auth/me/whatsapp", json={"enabled": True}, headers=ana).json()["enabled"] is True
    detect(api, camera_id)
    assert len(claim(api)) == 1


def test_a_removed_number_receives_nothing(api, ana, camera_id, clock):
    link_phone(api, ana, clock)
    assert api.delete("/api/auth/me/whatsapp", headers=ana).status_code == 204
    assert api.get("/api/auth/me/whatsapp", headers=ana).status_code == 404
    assert api.delete("/api/auth/me/whatsapp", headers=ana).status_code == 404
    detect(api, camera_id)
    assert claim(api) == []


def test_another_accounts_camera_does_not_message_this_number(api, ana, clock):
    link_phone(api, ana, clock)
    other_camera = save_camera(api, sign_up(api, "beto@lejos.example"), "Otra")
    detect(api, other_camera)
    assert claim(api) == []


def test_a_camera_nobody_owns_messages_nobody(api, ana, clock):
    link_phone(api, ana, clock)
    detect(api, "camera-without-owner")
    assert claim(api) == []


def test_a_deactivated_account_stops_receiving(api, ana, camera_id, clock):
    body = {"email": "beto@finca.example", "password": PASSWORD, "full_name": "Beto Salazar", "role": "worker"}
    created = api.post("/api/users", json=body, headers=ana).json()
    token = api.post("/api/auth/login", json={"email": body["email"], "password": PASSWORD}).json()["access_token"]
    link_phone(api, {"Authorization": f"Bearer {token}"}, clock)
    assert api.patch(f"/api/users/{created['id']}", json={"is_active": False}, headers=ana).status_code == 200
    detect(api, camera_id)
    assert claim(api) == []


def test_two_people_with_one_number_get_one_message(api, ana, camera_id, clock):
    link_phone(api, ana, clock)
    body = {"email": "beto@finca.example", "password": PASSWORD, "full_name": "Beto Salazar", "role": "worker"}
    api.post("/api/users", json=body, headers=ana)
    token = api.post("/api/auth/login", json={"email": body["email"], "password": PASSWORD}).json()["access_token"]
    link_phone(api, {"Authorization": f"Bearer {token}"}, clock)
    detect(api, camera_id)
    assert len(claim(api)) == 1


@pytest.mark.parametrize(("confidence", "notified"), [(0.59, False), (0.6, True)])
def test_a_low_confidence_detection_stays_on_the_dashboard(api, ana, camera_id, clock, confidence, notified):
    link_phone(api, ana, clock)
    detect(api, camera_id, confidence)
    assert bool(claim(api)) is notified
    assert api.get("/api/alerts", headers=ana).json()["total"] == 1


def test_a_camera_speaks_once_per_quiet_period_and_again_after_it(api, ana, camera_id, clock):
    link_phone(api, ana, clock)
    detect(api, camera_id)
    settle_all(api)
    clock.now += timedelta(minutes=10) - timedelta(seconds=1)
    detect(api, camera_id)
    assert claim(api) == []
    clock.now += timedelta(seconds=1)
    detect(api, camera_id)
    assert len(claim(api)) == 1


def test_the_quiet_period_is_per_camera(api, ana, camera_id, clock):
    link_phone(api, ana, clock)
    detect(api, camera_id)
    detect(api, save_camera(api, ana, "Potrero sur"))
    assert len(claim(api)) == 2


def test_every_detection_still_raises_its_alert_during_the_quiet_period(api, ana, camera_id, clock):
    link_phone(api, ana, clock)
    detect(api, camera_id)
    detect(api, camera_id)
    assert api.get("/api/alerts", headers=ana).json()["total"] == 2


def test_a_failure_to_queue_never_fails_the_detection(api, ana, camera_id, clock, monkeypatch):
    link_phone(api, ana, clock)
    def broken(*_args, **_kwargs):
        raise RuntimeError("queue unavailable")
    monkeypatch.setattr(api.app.state.whatsapp_store, "queue_camera_notice", broken)
    detect(api, camera_id)
    assert api.get("/api/alerts", headers=ana).json()["total"] == 1


@pytest.mark.parametrize("written", ["+57 300 123-4567", "+57(300)1234567"])
def test_a_number_written_with_separators_is_normalised(api, ana, clock, written):
    link_phone(api, ana, clock, written)
    assert api.get("/api/auth/me/whatsapp", headers=ana).json()["phone"] == PHONE


@pytest.mark.parametrize("phone", ["3001234567", "+0573001234567", "+1234567", "+1234567890123456", "+57abc1234567"])
def test_a_number_without_a_valid_country_code_is_refused(api, ana, phone):
    response = api.post("/api/auth/me/whatsapp/verification", json={"phone": phone}, headers=ana)
    assert response.status_code == 422
    assert "input" not in response.json()["detail"][0]


@pytest.mark.parametrize("phone", ["+12345678", "+123456789012345"])
def test_the_shortest_and_longest_numbers_are_accepted(api, ana, phone):
    assert api.post("/api/auth/me/whatsapp/verification", json={"phone": phone}, headers=ana).status_code == 202


def test_a_wrong_code_links_nothing(api, ana):
    api.post("/api/auth/me/whatsapp/verification", json={"phone": PHONE}, headers=ana)
    real = code_in(claim(api)[0])
    wrong = "000000" if real != "000000" else "111111"
    assert api.post("/api/auth/me/whatsapp/verify", json={"code": wrong}, headers=ana).status_code == 400
    assert api.get("/api/auth/me/whatsapp", headers=ana).status_code == 404


def test_someone_elses_code_does_not_work_for_you(api, ana):
    api.post("/api/auth/me/whatsapp/verification", json={"phone": PHONE}, headers=ana)
    code = code_in(claim(api)[0])
    beto = sign_up(api, "beto@lejos.example")
    assert api.post("/api/auth/me/whatsapp/verify", json={"code": code}, headers=beto).status_code == 400


def test_a_code_works_until_it_expires_and_not_after():
    verifications = PhoneVerifications()
    code = verifications.start(1, PHONE, MOMENT)
    assert verifications.confirm(1, code, MOMENT + timedelta(minutes=10)) is None
    later = MOMENT + timedelta(hours=1)
    code = verifications.start(1, PHONE, later)
    assert verifications.confirm(1, code, later + timedelta(minutes=10) - timedelta(seconds=1)) == PHONE


def test_a_code_works_only_once():
    verifications = PhoneVerifications()
    code = verifications.start(1, PHONE, MOMENT)
    assert verifications.confirm(1, code, MOMENT) == PHONE
    assert verifications.confirm(1, code, MOMENT) is None


def test_too_many_wrong_tries_burn_the_code():
    verifications = PhoneVerifications()
    code = verifications.start(1, PHONE, MOMENT)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(MAX_ATTEMPTS - 1):
        assert verifications.confirm(1, wrong, MOMENT) is None
    assert verifications.confirm(1, code, MOMENT) == PHONE
    code = verifications.start(1, PHONE, MOMENT + timedelta(minutes=1))
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(MAX_ATTEMPTS):
        assert verifications.confirm(1, wrong, MOMENT + timedelta(minutes=1)) is None
    assert verifications.confirm(1, code, MOMENT + timedelta(minutes=1)) is None


def test_codes_to_one_number_wait_a_minute_whoever_asks(api, ana, clock):
    beto = sign_up(api, "beto@lejos.example")
    url = "/api/auth/me/whatsapp/verification"
    assert api.post(url, json={"phone": PHONE}, headers=ana).status_code == 202
    clock.now += timedelta(seconds=59)
    response = api.post(url, json={"phone": PHONE}, headers=beto)
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    clock.now += timedelta(seconds=1)
    assert api.post(url, json={"phone": PHONE}, headers=beto).status_code == 202


def test_one_number_receives_a_limited_number_of_codes_per_hour():
    verifications = PhoneVerifications()
    for attempt in range(HOURLY_LIMIT):
        verifications.start(attempt, PHONE, MOMENT + timedelta(minutes=attempt * 2))
    with pytest.raises(TooManyCodesError):
        verifications.start(99, PHONE, MOMENT + timedelta(minutes=30))
    verifications.start(99, PHONE, MOMENT + timedelta(hours=1))


def test_a_message_without_an_answer_comes_back_after_the_lease_and_not_before(api, ana, clock):
    api.post("/api/auth/me/whatsapp/verification", json={"phone": PHONE}, headers=ana)
    assert len(claim(api)) == 1
    clock.now += timedelta(seconds=59)
    assert claim(api) == []
    clock.now += timedelta(seconds=1)
    assert len(claim(api)) == 1


def test_a_message_is_given_up_after_three_attempts(api, ana, clock):
    api.post("/api/auth/me/whatsapp/verification", json={"phone": PHONE}, headers=ana)
    for _ in range(3):
        assert len(claim(api)) == 1
        clock.now += timedelta(seconds=60)
    assert claim(api) == []


def test_a_sent_message_is_not_offered_again(api, ana, clock):
    api.post("/api/auth/me/whatsapp/verification", json={"phone": PHONE}, headers=ana)
    settle_all(api)
    clock.now += timedelta(minutes=2)
    assert claim(api) == []


def test_a_message_the_service_gave_up_on_is_not_offered_again(api, ana, clock):
    api.post("/api/auth/me/whatsapp/verification", json={"phone": PHONE}, headers=ana)
    [message] = claim(api)
    response = api.post(f"/api/ai/whatsapp/{message['id']}/failed", json={"error": "not on WhatsApp"}, headers=SERVICE)
    assert response.status_code == 204
    clock.now += timedelta(minutes=2)
    assert claim(api) == []


def test_reporting_a_message_twice_changes_nothing(api, ana):
    api.post("/api/auth/me/whatsapp/verification", json={"phone": PHONE}, headers=ana)
    [message] = claim(api)
    assert api.post(f"/api/ai/whatsapp/{message['id']}/sent", headers=SERVICE).status_code == 204
    assert api.post(f"/api/ai/whatsapp/{message['id']}/sent", headers=SERVICE).status_code == 204


def test_an_unknown_message_is_a_404(api):
    assert api.post("/api/ai/whatsapp/999/sent", headers=SERVICE).status_code == 404
    assert api.post("/api/ai/whatsapp/999/failed", json={"error": "x"}, headers=SERVICE).status_code == 404


def test_a_failure_reason_cannot_carry_a_url(api, ana):
    api.post("/api/auth/me/whatsapp/verification", json={"phone": PHONE}, headers=ana)
    [message] = claim(api)
    body = {"error": "failed https://user:secret@example.com"}
    response = api.post(f"/api/ai/whatsapp/{message['id']}/failed", json=body, headers=SERVICE)
    assert response.status_code == 422
    assert "secret" not in response.text


def test_a_verification_code_expires_before_it_can_be_claimed_late(api, ana, clock):
    api.post("/api/auth/me/whatsapp/verification", json={"phone": PHONE}, headers=ana)
    clock.now += timedelta(minutes=10)
    assert claim(api) == []


def test_an_alert_older_than_an_hour_is_not_sent_late(api, ana, camera_id, clock):
    link_phone(api, ana, clock)
    detect(api, camera_id)
    clock.now += timedelta(hours=1) - timedelta(seconds=1)
    assert len(claim(api)) == 1
    clock.now += timedelta(seconds=1)
    assert claim(api) == []


def test_messages_come_out_oldest_first_and_up_to_the_limit(api, ana, camera_id, clock):
    link_phone(api, ana, clock)
    other = save_camera(api, ana, "Potrero sur")
    detect(api, camera_id)
    detect(api, other)
    [first] = claim(api, limit=1)
    [second] = claim(api, limit=1)
    assert first["id"] < second["id"]


@pytest.mark.parametrize("limit", [0, 51])
def test_the_batch_size_is_bounded(api, limit):
    assert api.post("/api/ai/whatsapp/claim", headers=SERVICE, params={"limit": limit}).status_code == 422


def test_the_queue_needs_the_service_key(api, ana):
    assert api.post("/api/ai/whatsapp/claim").status_code == 401
    assert api.post("/api/ai/whatsapp/claim", headers={"X-API-Key": "wrong"}).status_code == 401
    assert api.post("/api/ai/whatsapp/claim", headers=ana).status_code == 401


def test_the_queue_stays_shut_when_no_key_is_configured():
    settings = Settings(_env_file=None, database_url=None, ai_api_key=None, jwt_secret=TEST_JWT_SECRET)
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/ai/whatsapp/claim").status_code == 503


def test_your_number_needs_a_session(api):
    assert api.get("/api/auth/me/whatsapp").status_code == 401
    assert api.post("/api/auth/me/whatsapp/verification", json={"phone": PHONE}).status_code == 401
    assert api.get("/api/auth/me/whatsapp", headers=SERVICE).status_code == 401


def test_each_person_sees_only_their_own_number(api, ana, clock):
    link_phone(api, ana, clock)
    beto = sign_up(api, "beto@lejos.example")
    assert api.get("/api/auth/me/whatsapp", headers=beto).status_code == 404


def test_linking_is_audited_without_the_number(api, ana, clock):
    link_phone(api, ana, clock)
    entries = api.get("/api/audit-log", params={"action": "user.whatsapp_changed"}, headers=ana).json()["items"]
    assert [entry["detail"] for entry in entries] == [{"state": "linked"}]
    assert PHONE not in str(entries)

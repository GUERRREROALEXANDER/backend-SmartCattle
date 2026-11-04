"""Alerts must reach the right person once, and never leak a secret or a phone number on the way."""

import json
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.schemas.alert import Alert, AlertStatus
from app.schemas.event import EventLevel, EventType, level_for_event
from app.schemas.notification import Channel, NotificationSettings
from app.services.notification_store import InMemoryNotificationStore
from app.services.notifier import AlertNotifier, ChannelError, TelegramChannel, WhatsAppChannel, channels_from
from tests.conftest import TEST_JWT_SECRET
from tests.test_database import alembic_config

NOW = datetime(2026, 11, 3, 3, 0, tzinfo=UTC)


class FakeChannel:
    name = Channel.TELEGRAM

    def __init__(self, failures=()):
        self.sent = []
        self.failures = list(failures)

    def send(self, recipient, text, camera, moment):
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append((recipient, text, camera, moment))


def alert(severity=EventLevel.HIGH, kind=EventType.CATTLE_DOWN, camera="camera-01"):
    return Alert(id=uuid4(), event_id=None, camera_id=camera, alert_type=kind, severity=severity,
                 status=AlertStatus.OPEN, message="x", created_at=NOW)


def notifier_with(channel, wanted=None, now=NOW, cooldown=timedelta(minutes=15)):
    store = InMemoryNotificationStore()
    store.save_settings(1, wanted or NotificationSettings(telegram_chat_id="123"), now)
    clock = {"now": now}
    delays = []
    notifier = AlertNotifier(store, [channel], lambda: clock["now"], cooldown, sleep=delays.append)
    return notifier, store, clock, delays


def test_a_critical_alert_reaches_the_recipient_in_spanish():
    channel = FakeChannel()
    notifier, store, _, _ = notifier_with(channel)
    notifier.deliver(alert(), 1)
    recipient, text, camera, _ = channel.sent[0]
    assert (recipient, camera) == ("123", "camera-01")
    assert "caído" in text
    items, total = store.deliveries(1, 10)
    assert total == 1 and items[0].status == "sent" and items[0].attempts == 1


@pytest.mark.parametrize("severity, minimum, told", [
    (EventLevel.LOW, EventLevel.MEDIUM, False),
    (EventLevel.MEDIUM, EventLevel.MEDIUM, True),
    (EventLevel.MEDIUM, EventLevel.HIGH, False),
    (EventLevel.HIGH, EventLevel.HIGH, True),
])
def test_alerts_below_the_chosen_severity_stay_on_the_dashboard(severity, minimum, told):
    channel = FakeChannel()
    notifier, _, _, _ = notifier_with(channel, NotificationSettings(telegram_chat_id="123", min_severity=minimum))
    notifier.deliver(alert(severity), 1)
    assert bool(channel.sent) is told


def test_nothing_is_sent_when_switched_off():
    channel = FakeChannel()
    notifier, _, _, _ = notifier_with(channel, NotificationSettings(telegram_chat_id="123", enabled=False))
    notifier.deliver(alert(), 1)
    assert channel.sent == []


def test_an_account_without_a_recipient_gets_no_delivery_record():
    channel = FakeChannel()
    notifier, store, _, _ = notifier_with(channel, NotificationSettings())
    notifier.deliver(alert(), 1)
    assert store.deliveries(1, 10) == ([], 0)


def test_a_herd_walking_out_is_one_message_until_the_cooldown_ends():
    channel = FakeChannel()
    notifier, _, clock, _ = notifier_with(channel)
    kind = EventType.CATTLE_OUT_OF_ZONE
    notifier.deliver(alert(kind=kind), 1)
    notifier.deliver(alert(kind=kind), 1)
    clock["now"] = NOW + timedelta(minutes=14, seconds=59)
    notifier.deliver(alert(kind=kind), 1)
    assert len(channel.sent) == 1
    clock["now"] = NOW + timedelta(minutes=15)
    notifier.deliver(alert(kind=kind), 1)
    assert len(channel.sent) == 2


def test_another_camera_or_another_kind_of_alert_is_news():
    channel = FakeChannel()
    notifier, _, _, _ = notifier_with(channel)
    notifier.deliver(alert(), 1)
    notifier.deliver(alert(camera="camera-02"), 1)
    notifier.deliver(alert(kind=EventType.POSSIBLE_INTRUSION), 1)
    assert len(channel.sent) == 3


def test_a_network_failure_is_retried_with_a_pause():
    channel = FakeChannel([ChannelError("Could not reach the channel")])
    notifier, store, _, delays = notifier_with(channel)
    notifier.deliver(alert(), 1)
    assert len(channel.sent) == 1 and delays == [5.0]
    assert store.deliveries(1, 10)[0][0].attempts == 2


def test_a_refused_token_is_not_retried_and_stays_on_the_record():
    channel = FakeChannel([ChannelError("The channel answered HTTP 401", final=True)])
    notifier, store, _, delays = notifier_with(channel)
    notifier.deliver(alert(), 1)
    item = store.deliveries(1, 10)[0][0]
    assert delays == [] and item.status == "failed" and item.detail == "The channel answered HTTP 401"


def test_a_failed_message_does_not_silence_the_next_alert():
    channel = FakeChannel([ChannelError("refused", final=True)])
    notifier, _, _, _ = notifier_with(channel)
    notifier.deliver(alert(), 1)
    notifier.deliver(alert(), 1)
    assert len(channel.sent) == 1


def test_giving_up_after_every_retry_is_recorded():
    channel = FakeChannel([ChannelError("down")] * 3)
    notifier, store, _, delays = notifier_with(channel)
    notifier.deliver(alert(), 1)
    item = store.deliveries(1, 10)[0][0]
    assert item.status == "failed" and item.attempts == 3 and delays == [5.0, 30.0]


def test_an_unexpected_failure_never_reaches_the_caller():
    class Broken(FakeChannel):
        def send(self, *_):
            raise RuntimeError("bug")

    notifier, _, _, _ = notifier_with(Broken())
    notifier.deliver(alert(), 1)


@pytest.fixture
def fake_api():
    """A real HTTP server standing in for WhatsApp and Telegram: the bytes on the wire are the contract."""
    seen = {"status": 200, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["requests"].append((self.path, self.headers.get("Authorization"), body))
            self.send_response(seen["status"])
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    seen["url"] = f"http://127.0.0.1:{server.server_address[1]}"
    yield seen
    server.shutdown()
    server.server_close()


def test_whatsapp_sends_a_template_because_free_text_is_refused_outside_a_conversation(fake_api):
    channel = WhatsAppChannel(fake_api["url"], "555", "secret-token", "smartcattle_alert", "es")
    channel.send("+573001234567", "Un animal", "camera-01", "2026-11-03 03:00 UTC")
    path, authorization, body = fake_api["requests"][0]
    assert path == "/555/messages" and authorization == "Bearer secret-token"
    assert body["to"] == "573001234567" and body["type"] == "template"
    assert body["template"]["name"] == "smartcattle_alert"
    assert [item["text"] for item in body["template"]["components"][0]["parameters"]] == [
        "Un animal", "camera-01", "2026-11-03 03:00 UTC",
    ]


def test_telegram_sends_the_whole_message(fake_api):
    TelegramChannel("tok", fake_api["url"]).send("-99", "Un animal", "camera-01", "ahora")
    path, _, body = fake_api["requests"][0]
    assert path == "/bottok/sendMessage" and body["chat_id"] == "-99"
    assert "Un animal" in body["text"] and "camera-01" in body["text"]


@pytest.mark.parametrize("code, final", [
    (400, True), (401, True), (404, True), (429, False), (500, False), (503, False),
])
def test_only_answers_that_can_change_are_retried(fake_api, code, final):
    fake_api["status"] = code
    with pytest.raises(ChannelError) as error:
        TelegramChannel("tok", fake_api["url"]).send("1", "a", "b", "c")
    assert error.value.final is final


def test_a_channel_error_never_carries_the_token(fake_api):
    fake_api["status"] = 401
    with pytest.raises(ChannelError) as error:
        TelegramChannel("very-secret", fake_api["url"]).send("1", "a", "b", "c")
    assert "very-secret" not in error.value.detail and "very-secret" not in str(error.value)


def test_an_unreachable_channel_is_worth_retrying():
    with pytest.raises(ChannelError) as error:
        TelegramChannel("very-secret", "http://127.0.0.1:9").send("1", "a", "b", "c")
    assert error.value.final is False and "very-secret" not in error.value.detail


def test_half_of_the_whatsapp_settings_is_refused_at_startup():
    with pytest.raises(ValueError, match="WHATSAPP"):
        Settings(_env_file=None, whatsapp_access_token="t")
    with pytest.raises(ValueError, match="WHATSAPP"):
        Settings(_env_file=None, whatsapp_phone_number_id="1")


def test_only_channels_with_credentials_exist():
    assert channels_from(Settings(_env_file=None)) == []
    names = [c.name for c in channels_from(Settings(
        _env_file=None, whatsapp_access_token="t", whatsapp_phone_number_id="1", telegram_bot_token="b",
    ))]
    assert names == [Channel.WHATSAPP, Channel.TELEGRAM]


@pytest.mark.parametrize("kind, confidence, level", [
    (EventType.CATTLE_DOWN, 0.4, EventLevel.HIGH),
    (EventType.POSSIBLE_INTRUSION, 0.4, EventLevel.HIGH),
    (EventType.EXTERNAL_ANIMAL_DETECTED, 0.95, EventLevel.MEDIUM),
    (EventType.CATTLE_OUT_OF_ZONE, 0.95, EventLevel.HIGH),
    (EventType.CATTLE_OUT_OF_ZONE, 0.4, EventLevel.LOW),
])
def test_what_an_event_means_decides_its_level_except_for_an_escape(kind, confidence, level):
    assert level_for_event(kind, confidence) is level


@pytest.fixture(params=["memory", "database"])
def api(request, tmp_path):
    url = None
    if request.param == "database":
        url = f"sqlite:///{(tmp_path / 'notifications.db').as_posix()}"
        command.upgrade(alembic_config(url), "head")
    settings = Settings(
        _env_file=None, allowed_origins="http://localhost:5173", smartcattle_ai_url=None, ai_api_key=None,
        database_url=url, jwt_secret=TEST_JWT_SECRET, account_isolation=True,
    )
    with TestClient(create_app(settings)) as client:
        client.channel = FakeChannel()
        client.app.state.notifier._channels = [client.channel]
        yield client


def sign_up(api, email):
    response = api.post("/api/auth/register", json={"email": email, "password": "una-clave-larga", "full_name": "P"})
    assert response.status_code == 201
    return response.json()["user"], {"Authorization": f"Bearer {response.json()['access_token']}"}


def own_camera(api, headers):
    response = api.post("/api/cameras/saved", json={"name": "Potrero", "source": "192.168.1.20:8080"}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


def event(api, camera, kind="cattle_down", obj="cow"):
    return api.post("/api/ai/events", json={
        "event_type": kind, "camera_id": camera, "detected_object": obj, "confidence": 0.5,
        "timestamp": "2026-11-03T03:00:00Z",
    })


def test_an_event_from_the_ai_service_reaches_the_owner_of_its_camera_only(api):
    _, ana = sign_up(api, "ana@finca.example")
    _, beto = sign_up(api, "beto@otra.example")
    assert api.put("/api/notifications/settings", json={"telegram_chat_id": "111"}, headers=ana).status_code == 200
    assert api.put("/api/notifications/settings", json={"telegram_chat_id": "222"}, headers=beto).status_code == 200
    camera = own_camera(api, ana)
    assert event(api, camera).status_code == 201
    assert [item[0] for item in api.channel.sent] == ["111"]
    assert api.get("/api/notifications/deliveries", headers=ana).json()["total"] == 1
    assert api.get("/api/notifications/deliveries", headers=beto).json()["total"] == 0


def test_a_camera_nobody_saved_tells_nobody(api):
    _, ana = sign_up(api, "ana@finca.example")
    api.put("/api/notifications/settings", json={"telegram_chat_id": "111"}, headers=ana)
    assert event(api, "camera-sin-dueno").status_code == 201
    assert api.channel.sent == []


@pytest.mark.parametrize("kind", ["cattle_down", "possible_intrusion", "external_animal_detected"])
def test_the_new_events_become_alerts_with_their_own_message(api, kind):
    _, ana = sign_up(api, "ana@finca.example")
    camera = own_camera(api, ana)
    assert event(api, camera, kind).status_code == 201
    alert_item = api.get("/api/alerts", headers=ana).json()["items"][0]
    assert alert_item["alert_type"] == kind and camera in alert_item["message"]


def test_settings_keep_what_was_saved_and_say_which_channels_work(api):
    _, ana = sign_up(api, "ana@finca.example")
    body = {"enabled": True, "whatsapp_phone": "+573001234567", "telegram_chat_id": None, "min_severity": "high"}
    assert api.put("/api/notifications/settings", json=body, headers=ana).status_code == 200
    read = api.get("/api/notifications/settings", headers=ana).json()
    assert read == {**body, "channels_available": ["telegram"]}


@pytest.mark.parametrize("field, value", [
    ("whatsapp_phone", "3001234567"), ("whatsapp_phone", "+0123456789"), ("whatsapp_phone", "+57 300"),
    ("telegram_chat_id", "abc"), ("min_severity", "urgent"), ("unknown", "x"),
])
def test_bad_settings_are_refused(api, field, value):
    _, ana = sign_up(api, "ana@finca.example")
    assert api.put("/api/notifications/settings", json={field: value}, headers=ana).status_code == 422


def test_the_settings_need_a_session(api):
    assert api.get("/api/notifications/settings").status_code == 401
    assert api.put("/api/notifications/settings", json={}).status_code == 401
    assert api.post("/api/notifications/test").status_code == 401
    assert api.get("/api/notifications/deliveries").status_code == 401


def test_the_test_message_goes_out_and_reports_how_it_went(api):
    _, ana = sign_up(api, "ana@finca.example")
    api.put("/api/notifications/settings", json={"telegram_chat_id": "111"}, headers=ana)
    answer = api.post("/api/notifications/test", headers=ana)
    assert answer.status_code == 200
    assert answer.json()["results"] == [{"channel": "telegram", "status": "sent", "detail": None}]
    assert api.channel.sent[0][0] == "111"


def test_a_test_with_nowhere_to_send_says_so(api):
    _, ana = sign_up(api, "ana@finca.example")
    assert api.post("/api/notifications/test", headers=ana).status_code == 409


def test_the_audit_log_records_the_change_without_the_number(api):
    _, ana = sign_up(api, "ana@finca.example")
    api.put("/api/notifications/settings", json={"whatsapp_phone": "+573001234567"}, headers=ana)
    log = api.get("/api/audit", headers=ana)
    if log.status_code == 200:
        assert "notifications.updated" in log.text
        assert "573001234567" not in log.text

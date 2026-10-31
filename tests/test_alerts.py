from datetime import UTC, datetime, timedelta
from itertools import count

import pytest

from app.schemas.event import EventType
from app.services.alert_store import MESSAGES

ALERTS_URL = "/api/alerts"


@pytest.fixture
def ticking(auth_client):
    """A clock that advances a second per reading, so 'newest first' is unambiguous."""
    start = datetime.now(UTC)
    ticks = count()
    auth_client.app.state.clock = lambda: start + timedelta(seconds=next(ticks))
    return auth_client


def raise_alert(client, payload, **changes):
    return client.post("/api/ai/events", json=payload | changes).json()


def first_alert(client, headers):
    return client.get(ALERTS_URL, headers=headers).json()["items"][0]


def test_an_ai_event_raises_one_open_alert(auth_client, owner_headers, event_payload):
    event = raise_alert(auth_client, event_payload)
    page = auth_client.get(ALERTS_URL, headers=owner_headers).json()
    assert page["total"] == 1
    alert = page["items"][0]
    assert alert["event_id"] == event["id"]
    assert alert["camera_id"] == "camera-01"
    assert alert["alert_type"] == "cattle_out_of_zone"
    assert alert["severity"] == event["level"] == "high"
    assert alert["status"] == "open"
    assert alert["message"] == "Cattle left the safe zone on camera-01"


def test_every_event_type_has_a_message():
    assert set(MESSAGES) == set(EventType)


def test_a_video_analysis_raises_no_alert(auth_client, owner_headers, video_analysis_payload):
    # Recorded footage: the events land in the feed, but nothing is happening now.
    auth_client.post("/api/videos/analyses", json=video_analysis_payload, headers=owner_headers)
    assert auth_client.get("/api/events").json()["total"] == 1
    assert auth_client.get(ALERTS_URL, headers=owner_headers).json()["total"] == 0


def test_alerts_are_listed_newest_first_and_paged(ticking, owner_headers, event_payload):
    for camera in ("camera-01", "camera-02", "camera-03"):
        raise_alert(ticking, event_payload, camera_id=camera)
    page = ticking.get(ALERTS_URL, params={"limit": 2}, headers=owner_headers).json()
    assert page["total"] == 3
    assert [alert["camera_id"] for alert in page["items"]] == ["camera-03", "camera-02"]
    rest = ticking.get(ALERTS_URL, params={"limit": 2, "offset": 2}, headers=owner_headers).json()
    assert [alert["camera_id"] for alert in rest["items"]] == ["camera-01"]


def test_alerts_are_filtered(ticking, owner_headers, event_payload):
    raise_alert(ticking, event_payload, confidence=0.95)
    raise_alert(ticking, event_payload, confidence=0.5, camera_id="camera-02")
    first = first_alert(ticking, owner_headers)
    ticking.post(f"{ALERTS_URL}/{first['id']}/resolve", headers=owner_headers)

    def total(**params):
        return ticking.get(ALERTS_URL, params=params, headers=owner_headers).json()["total"]

    assert total(status="open") == 1
    assert total(status="resolved") == 1
    assert total(severity="low") == 1
    assert total(severity="medium") == 0
    assert total(camera_id="camera-01") == 1


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"status": "closed"}])
def test_bad_filters_are_rejected(auth_client, owner_headers, params):
    assert auth_client.get(ALERTS_URL, params=params, headers=owner_headers).status_code == 422


def test_an_alert_is_acknowledged_then_resolved(
    auth_client, owner, owner_headers, worker, worker_headers, event_payload,
):
    raise_alert(auth_client, event_payload)
    alert = first_alert(auth_client, owner_headers)
    url = f"{ALERTS_URL}/{alert['id']}"

    # Workers handle alerts: that is the everyday job, not farm configuration.
    acknowledged = auth_client.post(f"{url}/acknowledge", headers=worker_headers).json()
    assert acknowledged["status"] == "acknowledged"
    assert acknowledged["acknowledged_by"] == worker["user"]["id"]
    assert acknowledged["acknowledged_at"] is not None

    resolved = auth_client.post(f"{url}/resolve", headers=owner_headers).json()
    assert resolved["status"] == "resolved"
    assert resolved["resolved_by"] == owner["user"]["id"]
    assert resolved["acknowledged_by"] == worker["user"]["id"]
    assert auth_client.get(url, headers=owner_headers).json() == resolved


def test_an_open_alert_can_be_resolved_directly(auth_client, owner_headers, event_payload):
    raise_alert(auth_client, event_payload)
    alert = first_alert(auth_client, owner_headers)
    resolved = auth_client.post(f"{ALERTS_URL}/{alert['id']}/resolve", headers=owner_headers).json()
    assert resolved["status"] == "resolved" and resolved["acknowledged_at"] is None


@pytest.mark.parametrize("first,second", [
    ("acknowledge", "acknowledge"), ("resolve", "acknowledge"), ("resolve", "resolve"),
])
def test_a_transition_that_is_no_longer_possible_is_a_conflict(
    auth_client, owner_headers, event_payload, first, second,
):
    raise_alert(auth_client, event_payload)
    url = f"{ALERTS_URL}/{first_alert(auth_client, owner_headers)['id']}"
    auth_client.post(f"{url}/{first}", headers=owner_headers)
    response = auth_client.post(f"{url}/{second}", headers=owner_headers)
    assert response.status_code == 409
    assert response.json()["detail"].startswith("The alert is already")


@pytest.mark.parametrize("method,suffix", [("get", ""), ("post", "/acknowledge"), ("post", "/resolve")])
def test_an_unknown_alert_is_a_404(auth_client, owner_headers, method, suffix):
    url = f"{ALERTS_URL}/00000000-0000-4000-8000-000000000000{suffix}"
    assert getattr(auth_client, method)(url, headers=owner_headers).status_code == 404


def test_a_malformed_alert_id_is_a_422(auth_client, owner_headers):
    assert auth_client.get(f"{ALERTS_URL}/42", headers=owner_headers).status_code == 422


@pytest.mark.parametrize("method,suffix", [("get", ""), ("post", "/acknowledge"), ("post", "/resolve")])
def test_alerts_are_not_public(auth_client, owner_headers, event_payload, method, suffix):
    raise_alert(auth_client, event_payload)
    alert_id = first_alert(auth_client, owner_headers)["id"]
    assert getattr(auth_client, method)(f"{ALERTS_URL}/{alert_id}{suffix}").status_code == 401
    assert auth_client.get(ALERTS_URL).status_code == 401

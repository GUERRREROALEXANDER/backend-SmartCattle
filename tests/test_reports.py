"""Alert and event summaries, cut into days in the caller's time zone."""

from datetime import UTC, datetime, timedelta

import pytest

NOW = datetime(2026, 10, 31, 18, 0, tzinfo=UTC)
WINDOW = {"start": "2026-10-29T00:00:00Z", "end": "2026-11-01T00:00:00Z"}


@pytest.fixture
def clock(auth_client):
    """A fixed, movable clock, set before anyone signs in.

    Tokens are issued and checked against the application clock, so the owner
    registers at NOW and authenticated calls must happen within the token's
    lifetime after it. Calls from the AI service carry no token and may happen
    at any time.
    """
    state = {"now": NOW}
    auth_client.app.state.clock = lambda: state["now"]
    return state


@pytest.fixture
def client(auth_client, clock, owner_payload):
    token = auth_client.post("/api/auth/register", json=owner_payload).json()["access_token"]
    auth_client.headers.update({"Authorization": f"Bearer {token}"})
    return auth_client


def raise_alert_at(client, clock, moment, confidence=0.95, camera="camera-01"):
    signed_in_at = clock["now"]
    clock["now"] = moment
    client.post("/api/ai/events", json={
        "event_type": "cattle_out_of_zone", "camera_id": camera, "detected_object": "cow",
        "confidence": confidence, "timestamp": moment.isoformat(),
    })
    clock["now"] = signed_in_at
    return client.get("/api/alerts", params={"limit": 1}).json()["items"][0]


def test_alerts_are_counted_per_local_day(client, clock):
    # 03:00 UTC on the 31st is 22:00 on the 30th in Bogotá (UTC-5).
    raise_alert_at(client, clock, datetime(2026, 10, 31, 3, 0, tzinfo=UTC), confidence=0.95)
    raise_alert_at(client, clock, datetime(2026, 10, 31, 15, 0, tzinfo=UTC), confidence=0.5)
    raise_alert_at(client, clock, datetime(2026, 10, 31, 16, 0, tzinfo=UTC), confidence=0.7)

    body = client.get("/api/reports/alerts", params=WINDOW).json()
    assert body["time_zone"] == "America/Bogota"
    assert body["total"] == 3
    assert [(day["day"], day["total"]) for day in body["days"]] == [("2026-10-30", 1), ("2026-10-31", 2)]
    assert body["days"][1] | {"day": None} == {
        "day": None, "total": 2, "low": 1, "medium": 1, "high": 0, "open": 2, "acknowledged": 0, "resolved": 0,
    }

    in_utc = client.get("/api/reports/alerts", params=WINDOW | {"tz": "UTC"}).json()
    assert [(day["day"], day["total"]) for day in in_utc["days"]] == [("2026-10-31", 3)]


def test_handling_times_are_averaged_over_the_handled_alerts(client, clock):
    # After NOW, where the owner's token is valid.
    first = raise_alert_at(client, clock, datetime(2026, 10, 31, 18, 0, tzinfo=UTC))
    second = raise_alert_at(client, clock, datetime(2026, 10, 31, 18, 10, tzinfo=UTC))
    raise_alert_at(client, clock, datetime(2026, 10, 31, 18, 20, tzinfo=UTC))

    clock["now"] = datetime(2026, 10, 31, 18, 30, tzinfo=UTC)
    client.post(f"/api/alerts/{first['id']}/acknowledge")      # 30 min
    client.post(f"/api/alerts/{second['id']}/acknowledge")     # 20 min
    clock["now"] = datetime(2026, 10, 31, 18, 50, tzinfo=UTC)
    client.post(f"/api/alerts/{first['id']}/resolve")          # 50 min

    body = client.get("/api/reports/alerts", params=WINDOW).json()
    assert body["avg_minutes_to_acknowledge"] == 25.0
    assert body["avg_minutes_to_resolve"] == 50.0
    assert body["days"][0] | {"day": None} == {
        "day": None, "total": 3, "low": 0, "medium": 0, "high": 3, "open": 1, "acknowledged": 1, "resolved": 1,
    }


def test_an_empty_window_has_no_averages(client, clock):
    body = client.get("/api/reports/alerts", params=WINDOW).json()
    assert body["days"] == [] and body["total"] == 0
    assert body["avg_minutes_to_acknowledge"] is None and body["avg_minutes_to_resolve"] is None


def test_events_are_counted_per_day_and_camera(client, clock):
    raise_alert_at(client, clock, datetime(2026, 10, 30, 14, 0, tzinfo=UTC), camera="camera-02")
    raise_alert_at(client, clock, datetime(2026, 10, 31, 14, 0, tzinfo=UTC), confidence=0.5)
    raise_alert_at(client, clock, datetime(2026, 10, 31, 15, 0, tzinfo=UTC))
    # Outside the window: it must not be counted.
    raise_alert_at(client, clock, datetime(2026, 11, 2, 15, 0, tzinfo=UTC))

    body = client.get("/api/reports/events", params=WINDOW).json()
    assert body["total"] == 3
    assert body["days"] == [
        {"day": "2026-10-30", "camera_id": "camera-02", "total": 1, "low": 0, "medium": 0, "high": 1},
        {"day": "2026-10-31", "camera_id": "camera-01", "total": 2, "low": 1, "medium": 0, "high": 1},
    ]


@pytest.mark.parametrize("url", ["/api/reports/alerts", "/api/reports/events"])
def test_an_unknown_time_zone_is_rejected(client, clock, url):
    response = client.get(url, params=WINDOW | {"tz": "Mars/Olympus"})
    assert response.status_code == 422


@pytest.mark.parametrize("url", ["/api/reports/alerts", "/api/reports/events"])
def test_reports_default_to_the_last_week(client, clock, url):
    body = client.get(url).json()
    assert body["end"] == "2026-10-31T18:00:00Z"
    assert body["start"] == (NOW - timedelta(days=7)).isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize("url", ["/api/reports/alerts", "/api/reports/events"])
def test_reports_require_a_signed_in_user(auth_client, url):
    assert auth_client.get(url).status_code == 401

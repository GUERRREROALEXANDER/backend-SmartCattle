"""The per-minute head count and the camera status history."""

from datetime import UTC, datetime, timedelta

import pytest

OCCUPANCY_URL = "/api/ai/cameras/{camera}/occupancy"
STATUS_URL = "/api/ai/cameras/{camera}/status"
NOW = datetime(2026, 10, 31, 16, 0, tzinfo=UTC)


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


def count(client, observed_at, inside, outside=0, camera="camera-01"):
    return client.put(OCCUPANCY_URL.format(camera=camera), json={
        "cattle_inside": inside, "cattle_outside": outside, "observed_at": observed_at,
    })


def report(client, clock, status, error=None, camera="camera-01"):
    body = {"status": status, "observed_at": clock["now"].isoformat()} | ({"error": error} if error else {})
    response = client.put(STATUS_URL.format(camera=camera), json=body)
    assert response.status_code == 200, response.text
    return response


def window(start="2026-10-31T00:00:00Z", end="2026-11-01T00:00:00Z", **params):
    return {"start": start, "end": end} | params


def test_one_sample_per_minute_keeps_the_last_count_of_that_minute(client, clock):
    count(client, "2026-10-31T15:30:05Z", 10)
    count(client, "2026-10-31T15:30:50Z", 12, 1)
    count(client, "2026-10-31T15:31:10Z", 11, 2)
    items = client.get("/api/reports/occupancy", params=window()).json()["items"]
    assert [(item["minute"], item["cattle_inside"], item["cattle_outside"]) for item in items] == [
        ("2026-10-31T15:30:00Z", 12, 1),
        ("2026-10-31T15:31:00Z", 11, 2),
    ]


def test_a_late_report_does_not_rewrite_the_history(client, clock):
    count(client, "2026-10-31T15:31:10Z", 11)
    assert count(client, "2026-10-31T15:30:30Z", 99).status_code == 204
    items = client.get("/api/reports/occupancy", params=window()).json()["items"]
    assert [item["cattle_inside"] for item in items] == [11]


def test_occupancy_history_filters_by_camera_and_window(client, clock):
    count(client, "2026-10-30T23:59:00Z", 1)
    count(client, "2026-10-31T10:00:00Z", 2)
    count(client, "2026-10-31T10:00:00Z", 3, camera="camera-02")
    body = client.get("/api/reports/occupancy", params=window(camera_id="camera-01")).json()
    assert body["total"] == 1
    assert body["items"][0] == {
        "camera_id": "camera-01", "minute": "2026-10-31T10:00:00Z", "cattle_inside": 2, "cattle_outside": 0,
    }


def test_the_default_window_is_the_last_week(client, clock):
    count(client, (NOW - timedelta(days=8)).isoformat(), 1)
    count(client, (NOW - timedelta(days=6)).isoformat(), 2)
    items = client.get("/api/reports/occupancy").json()["items"]
    assert [item["cattle_inside"] for item in items] == [2]


@pytest.mark.parametrize("params", [
    window(start="2026-10-31T00:00:00Z", end="2026-10-31T00:00:00Z"),
    window(start="2026-11-01T00:00:00Z", end="2026-10-31T00:00:00Z"),
    window(start="2026-06-01T00:00:00Z", end="2026-10-31T00:00:00Z"),
    {"start": "not-a-date"},
    {"camera_id": "camera 01"},
])
@pytest.mark.parametrize("url", ["/api/reports/occupancy", "/api/reports/camera-status"])
def test_an_unusable_window_is_rejected(client, clock, url, params):
    assert client.get(url, params=params).status_code == 422


def test_a_status_is_recorded_only_when_it_changes(client, clock):
    for status, error in [
        ("online", None), ("online", None), ("error", "Stream read timeout"),
        ("error", "Stream read timeout"), ("error", "Camera unplugged"), ("online", None),
    ]:
        report(client, clock, status, error)
        clock["now"] += timedelta(minutes=1)
    items = client.get("/api/reports/camera-status", params=window()).json()["items"]
    assert [(item["status"], item["error"]) for item in items] == [
        ("online", None), ("error", "Stream read timeout"), ("error", "Camera unplugged"), ("online", None),
    ]
    assert items[0]["changed_at"] == "2026-10-31T16:00:00Z"
    assert items[-1]["changed_at"] == "2026-10-31T16:05:00Z"


def test_status_history_filters_by_camera(client, clock):
    report(client, clock, "online")
    report(client, clock, "offline", camera="camera-02")
    items = client.get("/api/reports/camera-status", params=window(camera_id="camera-02")).json()["items"]
    assert [(item["camera_id"], item["status"]) for item in items] == [("camera-02", "offline")]


@pytest.mark.parametrize("url", ["/api/reports/occupancy", "/api/reports/camera-status"])
def test_history_requires_a_signed_in_user(auth_client, url):
    assert auth_client.get(url).status_code == 401

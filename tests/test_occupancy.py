from datetime import UTC, datetime, timedelta

import pytest

from app.schemas.camera import CameraRecord, CameraStatus
from app.schemas.occupancy import Occupancy, SystemStatus
from app.services.live_metrics import compute_live_metrics

OCCUPANCY_URL = "/api/ai/cameras/{camera}/occupancy"
LIVE_METRICS_URL = "/api/metrics/live"
OFFLINE_AFTER = 60
NOW = datetime(2026, 10, 31, 15, 30, tzinfo=UTC)
BOX = {"track_id": 7, "label": "cow", "confidence": 0.93, "bbox": [0.7, 0.4, 0.8, 0.6], "inside_zone": False}


def count(inside=10, outside=1, observed_at="2026-10-31T15:30:00Z", **extra):
    return {"cattle_inside": inside, "cattle_outside": outside, "observed_at": observed_at} | extra


def put(client, body, camera="camera-01", **kwargs):
    return client.put(OCCUPANCY_URL.format(camera=camera), json=body, **kwargs)


class MovableClock:
    """The app clock, anchored to real time so issued tokens stay valid."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock(auth_client):
    movable = MovableClock()
    auth_client.app.state.clock = movable
    return movable


def camera(status=CameraStatus.ONLINE, reported_seconds_ago=0):
    return CameraRecord(id="camera-01", reported_status=status,
                        last_report_at=NOW - timedelta(seconds=reported_seconds_ago))


def snapshot(camera_id="camera-01", inside=10, outside=2, received_seconds_ago=0.0):
    return Occupancy(camera_id=camera_id, cattle_inside=inside, cattle_outside=outside,
                     observed_at=NOW, received_at=NOW - timedelta(seconds=received_seconds_ago))


def metrics(cameras=(), counts=(), open_alerts=0, events=0):
    return compute_live_metrics(cameras, counts, open_alerts, events, NOW, OFFLINE_AFTER)


def test_nothing_reported_is_no_data():
    result = metrics()
    assert result.system_status is SystemStatus.NO_DATA
    assert (result.cameras_total, result.cattle_total) == (0, 0)


def test_every_camera_online_and_fresh_is_operational():
    result = metrics(cameras=[camera()], counts=[snapshot(inside=10, outside=2)], open_alerts=3, events=4)
    assert result.system_status is SystemStatus.OPERATIONAL
    assert (result.cattle_inside, result.cattle_outside, result.cattle_total) == (10, 2, 12)
    assert (result.cameras_online, result.open_alerts, result.events_last_hour) == (1, 3, 4)
    assert result.generated_at == NOW


def test_an_offline_camera_degrades_the_system():
    result = metrics(cameras=[camera(), camera(reported_seconds_ago=OFFLINE_AFTER + 1)])
    assert result.cameras_online == 1
    assert result.system_status is SystemStatus.DEGRADED


def test_a_camera_reporting_an_error_degrades_the_system():
    assert metrics(cameras=[camera(status=CameraStatus.ERROR)]).system_status is SystemStatus.DEGRADED


@pytest.mark.parametrize("age,fresh", [(OFFLINE_AFTER, True), (OFFLINE_AFTER + 0.001, False)])
def test_a_count_is_current_up_to_the_offline_threshold(age, fresh):
    result = metrics(cameras=[camera()], counts=[snapshot(received_seconds_ago=age)])
    assert result.stale_cameras == (0 if fresh else 1)
    assert result.cattle_total == (12 if fresh else 0)
    assert result.system_status is (SystemStatus.OPERATIONAL if fresh else SystemStatus.DEGRADED)


def test_counts_add_up_across_cameras():
    result = metrics(counts=[snapshot("camera-01", 10, 2), snapshot("camera-02", 5, 0)])
    assert (result.cattle_inside, result.cattle_outside) == (15, 2)


def test_the_ai_reports_a_count(auth_client, owner_headers, clock):
    assert put(auth_client, count(inside=12, outside=1)).status_code == 204
    live = auth_client.get(LIVE_METRICS_URL, headers=owner_headers).json()
    assert (live["cattle_inside"], live["cattle_outside"], live["cattle_total"]) == (12, 1, 13)


def test_a_newer_count_replaces_the_previous_one(auth_client, owner_headers, clock):
    put(auth_client, count(inside=12))
    put(auth_client, count(inside=9, observed_at="2026-10-31T15:30:01Z"))
    assert auth_client.get(LIVE_METRICS_URL, headers=owner_headers).json()["cattle_inside"] == 9


def test_an_older_count_arriving_late_is_ignored(auth_client, owner_headers, clock):
    put(auth_client, count(inside=12, observed_at="2026-10-31T15:30:05Z"))
    assert put(auth_client, count(inside=3, observed_at="2026-10-31T15:30:00Z")).status_code == 204
    assert auth_client.get(LIVE_METRICS_URL, headers=owner_headers).json()["cattle_inside"] == 12


def test_a_camera_that_stops_counting_drops_out_of_the_totals(auth_client, owner_headers, clock):
    put(auth_client, count(inside=12), camera="camera-01")
    clock.advance(30)
    put(auth_client, count(inside=5), camera="camera-02")
    clock.advance(OFFLINE_AFTER - 29)
    live = auth_client.get(LIVE_METRICS_URL, headers=owner_headers).json()
    assert (live["cattle_inside"], live["stale_cameras"], live["system_status"]) == (5, 1, "degraded")


def test_live_metrics_count_only_open_alerts_and_recent_events(auth_client, owner_headers, clock, event_payload):
    for _ in range(2):
        auth_client.post("/api/ai/events", json=event_payload | {"timestamp": clock.now.isoformat()})
    alert_id = auth_client.get("/api/alerts", headers=owner_headers).json()["items"][0]["id"]
    auth_client.post(f"/api/alerts/{alert_id}/resolve", headers=owner_headers)
    live = auth_client.get(LIVE_METRICS_URL, headers=owner_headers).json()
    assert (live["open_alerts"], live["events_last_hour"]) == (1, 2)


def test_live_metrics_are_not_public(auth_client, owner):
    assert auth_client.get(LIVE_METRICS_URL).status_code == 401


def test_live_metrics_are_open_to_workers(auth_client, worker_headers):
    assert auth_client.get(LIVE_METRICS_URL, headers=worker_headers).json()["system_status"] == "no_data"


def test_the_ai_key_guards_the_count(secured_client):
    assert put(secured_client, count()).status_code == 401
    assert put(secured_client, count(), headers={"X-API-Key": "test-shared-key"}).status_code == 204


@pytest.mark.parametrize("body", [
    count(inside=-1),
    count(outside=10_001),
    count(observed_at="2026-10-31T15:30:00"),
    count(extra_field=1),
    count(detections=[BOX | {"bbox": [0.8, 0.4, 0.7, 0.6]}]),
    count(detections=[BOX | {"bbox": [0.7, 0.4, 0.8, 1.01]}]),
    count(detections=[BOX | {"label": "  "}]),
    count(detections=[BOX | {"confidence": 1.01}]),
    count(detections=[BOX] * 201),
])
def test_a_malformed_count_is_rejected(client, body):
    assert put(client, body).status_code == 422


@pytest.mark.parametrize("body", [count(inside=0, outside=10_000), count(detections=[BOX] * 200)])
def test_the_limits_themselves_are_accepted(client, body):
    assert put(client, body).status_code == 204


@pytest.mark.parametrize("name", ["camera 01", "c" * 65])
def test_the_camera_id_is_validated(client, name):
    assert put(client, count(), camera=name).status_code == 422


def test_counts_and_boxes_reach_the_dashboard(auth_client, owner, clock):
    with auth_client.websocket_connect("/ws/live") as socket:
        socket.send_json({"type": "auth", "token": owner["access_token"]})
        socket.receive_json()
        put(auth_client, count(inside=12, detections=[BOX]))
        updated = socket.receive_json()
        boxes = socket.receive_json()
    assert updated["type"] == "occupancy.updated"
    assert updated["data"]["camera_id"] == "camera-01" and updated["data"]["cattle_inside"] == 12
    assert boxes["type"] == "detections"
    assert boxes["data"]["detections"] == [BOX]


def test_an_ignored_or_boxless_count_pushes_only_what_changed(auth_client, owner, clock):
    with auth_client.websocket_connect("/ws/live") as socket:
        socket.send_json({"type": "auth", "token": owner["access_token"]})
        socket.receive_json()
        put(auth_client, count(observed_at="2026-10-31T15:30:05Z"))
        put(auth_client, count(observed_at="2026-10-31T15:30:00Z", detections=[BOX]))
        socket.send_json({"type": "ping"})
        received = [socket.receive_json()["type"] for _ in range(2)]
    # No detections message for the first (no boxes), nothing for the stale second.
    assert received == ["occupancy.updated", "pong"]

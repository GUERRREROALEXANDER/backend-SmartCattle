from datetime import UTC, datetime, timedelta

import pytest

from app.schemas.camera import CameraRecord, CameraStatus, effective_status

STATUS_URL = "/api/ai/cameras/camera-01/status"


@pytest.fixture
def online_report():
    return {"status": "online", "frame_width": 1280, "frame_height": 720, "fps": 14.8,
            "observed_at": "2026-10-06T15:30:00Z"}


def test_cameras_start_empty(client):
    assert client.get("/api/cameras").json() == {"items": [], "total": 0}


def test_report_online(client, online_report):
    response = client.put(STATUS_URL, json=online_report)
    assert response.status_code == 200
    camera = response.json()
    assert camera["id"] == "camera-01"
    assert camera["status"] == camera["reported_status"] == "online"
    assert (camera["frame_width"], camera["frame_height"], camera["fps"]) == (1280, 720, 14.8)
    assert camera["last_online_at"] == camera["last_report_at"]
    assert client.get("/api/cameras").json() == {"items": [camera], "total": 1}


def test_report_error_keeps_last_online_time(client, online_report):
    online = client.put(STATUS_URL, json=online_report).json()
    error = client.put(STATUS_URL, json={"status": "error", "error": "Stream read timeout",
                                         "observed_at": "2026-10-06T15:31:00Z"}).json()
    assert error["status"] == "error"
    assert error["last_error"] == "Stream read timeout"
    assert error["last_online_at"] == online["last_online_at"]
    back = client.put(STATUS_URL, json=online_report).json()
    assert back["status"] == "online" and back["last_error"] is None


def test_stale_report_is_offline(client, online_report):
    client.put(STATUS_URL, json=online_report)
    client.app.state.clock = lambda: datetime.now(UTC) + timedelta(seconds=61)
    camera = client.get("/api/cameras").json()["items"][0]
    assert camera["status"] == "offline"
    assert camera["reported_status"] == "online"


def test_cameras_sorted_by_id(client, online_report):
    for camera_id in ("cam-b", "cam-a"):
        client.put(f"/api/ai/cameras/{camera_id}/status", json=online_report)
    assert [item["id"] for item in client.get("/api/cameras").json()["items"]] == ["cam-a", "cam-b"]


@pytest.mark.parametrize("camera_id", ["bad.id", "with space", "x" * 65])
def test_invalid_camera_id(client, online_report, camera_id):
    assert client.put(f"/api/ai/cameras/{camera_id}/status", json=online_report).status_code == 422


def test_stream_url_only_while_online(client, online_report):
    url = "https://abc-def.trycloudflare.com/"
    camera = client.put(STATUS_URL, json=online_report | {"stream_url": url}).json()
    assert camera["stream_url"] == "https://abc-def.trycloudflare.com"
    assert client.get("/api/cameras").json()["items"][0]["stream_url"] == camera["stream_url"]
    error = client.put(STATUS_URL, json={"status": "error", "error": "Stream read timeout", "stream_url": url,
                                         "observed_at": "2026-10-06T15:31:00Z"}).json()
    assert error["stream_url"] is None
    assert client.put(STATUS_URL, json=online_report).json()["stream_url"] is None


@pytest.mark.parametrize("changes", [
    {"stream_url": "https://admin:secret@example.com"}, {"stream_url": "ftp://example.com"},
    {"stream_url": "https://example.com/" + "x" * 300},
    {"status": "connecting"}, {"error": "boom"}, {"observed_at": "2026-10-06T15:30:00"},
    {"status": "error", "error": "failed rtsp://admin:secret@10.0.0.2:554/x"},
    {"status": "error", "error": "x" * 301}, {"fps": 0}, {"fps": 241}, {"frame_width": 0},
    {"unexpected": True},
])
def test_invalid_report(client, online_report, changes):
    response = client.put(STATUS_URL, json=online_report | changes)
    assert response.status_code == 422
    assert "secret" not in response.text
    assert client.get("/api/cameras").json()["total"] == 0


@pytest.mark.parametrize("key,expected", [(None, 401), ("incorrect", 401), ("test-shared-key", 200)])
def test_report_requires_api_key(secured_client, online_report, key, expected):
    headers = {} if key is None else {"X-API-Key": key}
    assert secured_client.put(STATUS_URL, json=online_report, headers=headers).status_code == expected
    assert secured_client.get("/api/cameras").status_code == 200


@pytest.mark.parametrize("age,reported,expected", [
    (59, CameraStatus.ONLINE, CameraStatus.ONLINE),
    (61, CameraStatus.ONLINE, CameraStatus.OFFLINE),
    (10, CameraStatus.ERROR, CameraStatus.ERROR),
    (61, CameraStatus.ERROR, CameraStatus.OFFLINE),
])
def test_effective_status(age, reported, expected):
    now = datetime(2026, 10, 6, 12, tzinfo=UTC)
    record = CameraRecord(id="c", reported_status=reported, last_report_at=now - timedelta(seconds=age))
    assert effective_status(record, now, 60) is expected


def test_event_bbox_is_stored(client, event_payload):
    response = client.post("/api/ai/events", json=event_payload | {"bbox": [10, 20.5, 110, 220]})
    assert response.status_code == 201
    assert response.json()["bbox"] == [10.0, 20.5, 110.0, 220.0]
    assert client.post("/api/ai/events", json=event_payload).json()["bbox"] is None


@pytest.mark.parametrize("bbox", [[1, 2, 3], [1, 2, 3, 4, 5], [5, 2, 3, 4], [1, 4, 3, 4], [-1, 2, 3, 4]])
def test_invalid_bbox(client, event_payload, bbox):
    assert client.post("/api/ai/events", json=event_payload | {"bbox": bbox}).status_code == 422


def test_non_finite_bbox(client):
    body = ('{"event_type":"cattle_out_of_zone","camera_id":"camera-01","detected_object":"cow",'
            '"confidence":0.9,"timestamp":"2026-10-03T15:30:00Z","bbox":[1,2,NaN,4]}')
    response = client.post("/api/ai/events", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422


def test_one_camera_can_be_fetched_on_its_own(client, online_report):
    client.put("/api/ai/cameras/camera-01/status", json=online_report)
    response = client.get("/api/cameras/camera-01")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "camera-01"
    assert body["status"] == "online"


def test_an_unreported_camera_is_a_404(client):
    response = client.get("/api/cameras/camera-99")
    assert response.status_code == 404
    assert "camera-99" in response.json()["detail"]


def test_the_detail_view_expires_a_stale_report(client, online_report):
    from datetime import timedelta

    client.put("/api/ai/cameras/camera-01/status", json=online_report)
    moved = client.app.state.clock() + timedelta(seconds=61)
    client.app.state.clock = lambda: moved

    body = client.get("/api/cameras/camera-01").json()
    # The same rule as the list view: the backend cannot claim a camera is
    # online unless the AI service keeps confirming it.
    assert body["status"] == "offline"
    assert body["reported_status"] == "online"


@pytest.mark.parametrize("camera_id", ["with space", "a" * 65, "camera.01"])
def test_an_unusable_camera_id_is_rejected(client, camera_id):
    assert client.get(f"/api/cameras/{camera_id}").status_code == 422


def test_a_camera_id_with_a_slash_cannot_reach_the_route(client):
    # Not a 422: the slash splits the path, so the request is for
    # /api/cameras/camera/01, which matches no route at all. Worth pinning,
    # because it is the reason the pattern excludes the character rather than
    # merely rejecting it.
    assert client.get("/api/cameras/camera/01").status_code == 404


SOURCE_URL = "/api/ai/cameras/cam-1/source"


@pytest.fixture
def camera_store():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app.services.camera_store import SqlCameraStore

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield SqlCameraStore(sessionmaker(engine, expire_on_commit=False))
    engine.dispose()


def test_source_is_stored_and_not_public(client):
    assert client.put(SOURCE_URL, json={"source": "rtsp://192.168.1.50:554/live"}).status_code == 204
    assert client.app.state.camera_store.registration("cam-1").source == "rtsp://192.168.1.50:554/live"
    assert "192.168.1.50" not in client.get("/api/cameras").text


@pytest.mark.parametrize("source", ["rtsp://admin:secret@192.168.1.50:554/live", "rtsp://admin@cam/live", ""])
def test_source_with_credentials_is_rejected(client, source):
    assert client.put(SOURCE_URL, json={"source": source}).status_code == 422


def test_source_needs_the_ai_key(secured_client):
    assert secured_client.put(SOURCE_URL, json={"source": "0"}).status_code == 401
    accepted = secured_client.put(SOURCE_URL, json={"source": "0"}, headers={"X-API-Key": "test-shared-key"})
    assert accepted.status_code == 204


def test_sql_store_keeps_the_source_and_a_removal_keeps_the_row(camera_store):
    from app.schemas.camera import CameraSourceReport

    assert camera_store.registration("cam-1") is None
    camera_store.register("cam-1", CameraSourceReport(source="rtsp://192.168.1.50/live"))
    assert camera_store.registration("cam-1").model_dump() == {"source": "rtsp://192.168.1.50/live", "active": True}
    camera_store.register("cam-1", CameraSourceReport(source="rtsp://192.168.1.50/live", active=False))
    assert camera_store.registration("cam-1").active is False
    assert camera_store.list() == []  # Registered but never reported: no status to show yet.


def test_a_removed_camera_leaves_the_list_and_returns_when_added_again(client, online_report):
    client.put("/api/ai/cameras/cam-1/status", json=online_report)
    assert [item["id"] for item in client.get("/api/cameras").json()["items"]] == ["cam-1"]
    client.put(SOURCE_URL, json={"source": "rtsp://192.168.1.50/live", "active": False})
    assert client.get("/api/cameras").json()["items"] == []
    assert client.get("/api/cameras/cam-1").status_code == 404
    client.put(SOURCE_URL, json={"source": "rtsp://192.168.1.50/live"})
    assert [item["id"] for item in client.get("/api/cameras").json()["items"]] == ["cam-1"]


def test_sql_store_leaves_a_removed_camera_out_of_the_list(camera_store):
    from app.schemas.camera import CameraSourceReport, CameraStatusReport

    at = datetime(2026, 11, 1, tzinfo=UTC)
    camera_store.report("cam-1", CameraStatusReport(status="online", observed_at=at), at)
    assert [item.id for item in camera_store.list()] == ["cam-1"]
    camera_store.register("cam-1", CameraSourceReport(source="0", active=False))
    assert camera_store.list() == []

from datetime import datetime, timedelta, timezone

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
    client.app.state.clock = lambda: datetime.now(timezone.utc) + timedelta(seconds=61)
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


@pytest.mark.parametrize("changes", [
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
    now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
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

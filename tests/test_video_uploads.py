from datetime import UTC, datetime, timedelta

import pytest
from botocore.stub import Stubber
from pydantic import ValidationError

from app.core.config import Settings
from app.services.video_storage import PresignedUpload, S3VideoStorage
from app.services.video_upload_store import JOB_LEASE, MAX_ATTEMPTS

UPLOADS_URL = "/api/videos/uploads"
JOBS_URL = "/api/ai/video-jobs"
GIB = 1024 ** 3
VIDEO = {"file_name": "north-paddock.mp4", "size_bytes": 5_000_000, "content_type": "video/mp4"}
BOX = {"track_id": 3, "label": "cow", "confidence": 0.9, "bbox": [0.1, 0.1, 0.2, 0.3], "inside_zone": True}
STORAGE = {
    "video_storage_endpoint": "http://minio:9000", "video_storage_bucket": "videos",
    "video_storage_access_key_id": "key-id", "video_storage_secret_access_key": "secret-key",
}


class FakeStorage:
    """Stands in for the bucket: records what was presigned and what was 'uploaded'."""

    def __init__(self) -> None:
        self.objects: dict[str, int] = {}
        self.deleted: list[str] = []

    def presign_upload(self, key, content_type, size_bytes, expires_in, now):  # noqa: ARG002 - the bucket enforces it
        return PresignedUpload(url=f"https://bucket.test/{key}?signed", expires_at=now + expires_in,
                               headers={"Content-Type": content_type})

    def presign_download(self, key):
        return f"https://bucket.test/{key}?download"

    def object_size(self, key):
        return self.objects.get(key)

    def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)


class MovableClock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def storage(auth_client):
    fake = FakeStorage()
    auth_client.app.state.video_storage = fake
    return fake


@pytest.fixture
def clock(auth_client):
    movable = MovableClock()
    auth_client.app.state.clock = movable
    return movable


def start(client, headers, **changes):
    return client.post(UPLOADS_URL, json=VIDEO | changes, headers=headers)


def uploaded(client, headers, storage, **changes):
    """An upload whose file reached the bucket and that was confirmed: queued."""
    ticket = start(client, headers, **changes).json()
    upload = ticket["upload"]
    storage.objects[ticket["upload_url"].split("bucket.test/")[1].split("?")[0]] = upload["size_bytes"]
    assert client.post(f"{UPLOADS_URL}/{upload['id']}/complete", headers=headers).status_code == 200
    return upload


def claim(client):
    return client.post(f"{JOBS_URL}/claim")


# --- Browser side -----------------------------------------------------------

def test_starting_an_upload_returns_a_signed_url(auth_client, owner_headers, storage):
    response = start(auth_client, owner_headers)
    assert response.status_code == 201
    ticket = response.json()
    assert ticket["upload"]["status"] == "pending_upload"
    assert ticket["upload_method"] == "PUT"
    assert ticket["upload_headers"] == {"Content-Type": "video/mp4"}
    # The key is ours, not the user's file name.
    assert ticket["upload_url"].startswith(f"https://bucket.test/videos/{ticket['upload']['id']}.mp4")
    assert "storage_key" not in ticket["upload"]


@pytest.mark.parametrize("size,code", [(GIB, 201), (GIB + 1, 413)])
def test_the_size_limit_is_one_gibibyte(auth_client, owner_headers, storage, size, code):
    assert start(auth_client, owner_headers, size_bytes=size).status_code == code


@pytest.mark.parametrize("changes", [
    {"content_type": "application/pdf", "file_name": "notes.pdf"},
    {"content_type": "video/mp4", "file_name": "clip.mov"},
    {"content_type": "video/mp4", "file_name": "clip"},
])
def test_only_matching_video_types_are_accepted(auth_client, owner_headers, storage, changes):
    assert start(auth_client, owner_headers, **changes).status_code == 415


def test_content_type_is_case_insensitive(auth_client, owner_headers, storage):
    assert start(auth_client, owner_headers, content_type=" Video/QuickTime ", file_name="a.MOV").status_code == 201


@pytest.mark.parametrize("changes", [
    {"file_name": "../etc/passwd.mp4"},
    {"file_name": "dir\\clip.mp4"},
    {"file_name": "clip\n.mp4"},
    {"file_name": "   "},
    {"size_bytes": 0},
    {"lot_id": 0},
    {"bucket": "other"},
])
def test_malformed_upload_requests_are_rejected(auth_client, owner_headers, storage, changes):
    assert start(auth_client, owner_headers, **changes).status_code == 422


def test_an_unknown_lot_is_rejected(auth_client, owner_headers, storage):
    assert start(auth_client, owner_headers, lot_id=99).status_code == 422


def test_uploads_need_a_signed_in_user(auth_client, owner, storage):
    assert auth_client.post(UPLOADS_URL, json=VIDEO).status_code == 401
    assert auth_client.get(UPLOADS_URL).status_code == 401


def test_without_a_bucket_uploads_answer_503(auth_client, owner_headers):
    assert start(auth_client, owner_headers).status_code == 503
    assert claim(auth_client).status_code == 503


def test_completing_before_the_file_arrives_is_a_conflict(auth_client, owner_headers, storage):
    upload = start(auth_client, owner_headers).json()["upload"]
    assert auth_client.post(f"{UPLOADS_URL}/{upload['id']}/complete", headers=owner_headers).status_code == 409


def test_completing_queues_the_video_once(auth_client, owner_headers, storage):
    upload = uploaded(auth_client, owner_headers, storage)
    stored = auth_client.get(f"{UPLOADS_URL}/{upload['id']}", headers=owner_headers).json()
    assert stored["status"] == "queued" and stored["uploaded_at"] is not None
    assert auth_client.post(f"{UPLOADS_URL}/{upload['id']}/complete", headers=owner_headers).status_code == 409


def test_a_file_of_the_wrong_size_is_deleted_and_failed(auth_client, owner_headers, storage):
    ticket = start(auth_client, owner_headers).json()
    key = f"videos/{ticket['upload']['id']}.mp4"
    storage.objects[key] = 10
    response = auth_client.post(f"{UPLOADS_URL}/{ticket['upload']['id']}/complete", headers=owner_headers)
    assert response.status_code == 422
    assert storage.deleted == [key]
    stored = auth_client.get(f"{UPLOADS_URL}/{ticket['upload']['id']}", headers=owner_headers).json()
    assert stored["status"] == "failed" and "10 bytes" in stored["error"]


def test_only_the_uploader_or_an_owner_completes(auth_client, owner_headers, worker_headers, storage):
    upload = start(auth_client, owner_headers).json()["upload"]
    storage.objects[f"videos/{upload['id']}.mp4"] = upload["size_bytes"]
    assert auth_client.post(f"{UPLOADS_URL}/{upload['id']}/complete", headers=worker_headers).status_code == 403
    mine = start(auth_client, worker_headers).json()["upload"]
    storage.objects[f"videos/{mine['id']}.mp4"] = mine["size_bytes"]
    assert auth_client.post(f"{UPLOADS_URL}/{mine['id']}/complete", headers=owner_headers).status_code == 200


def test_an_unknown_upload_is_404(auth_client, owner_headers, storage):
    missing = "00000000-0000-4000-8000-000000000000"
    assert auth_client.get(f"{UPLOADS_URL}/{missing}", headers=owner_headers).status_code == 404
    assert auth_client.post(f"{UPLOADS_URL}/{missing}/complete", headers=owner_headers).status_code == 404


def test_uploads_are_listed_newest_first_and_filtered(auth_client, owner_headers, storage, clock):
    first = uploaded(auth_client, owner_headers, storage)
    clock.advance(timedelta(seconds=1))
    second = start(auth_client, owner_headers).json()["upload"]
    listing = auth_client.get(UPLOADS_URL, headers=owner_headers).json()
    assert [item["id"] for item in listing["items"]] == [second["id"], first["id"]]
    queued = auth_client.get(UPLOADS_URL, params={"status": "queued"}, headers=owner_headers).json()
    assert (queued["total"], queued["items"][0]["id"]) == (1, first["id"])
    paged = auth_client.get(UPLOADS_URL, params={"limit": 1, "offset": 1}, headers=owner_headers).json()
    assert (paged["total"], [item["id"] for item in paged["items"]]) == (2, [first["id"]])


# --- AI worker side ---------------------------------------------------------

def test_an_empty_queue_answers_204(auth_client, storage):
    assert claim(auth_client).status_code == 204


def test_a_worker_gets_the_oldest_video_with_its_lot_camera(auth_client, owner_headers, storage, clock):
    farm = auth_client.post("/api/farms", json={"name": "La Esperanza"}, headers=owner_headers).json()
    lot = auth_client.post(f"/api/farms/{farm['id']}/lots", headers=owner_headers, json={
        "name": "North", "kind": "paddock", "camera_id": "camera-07",
    }).json()
    oldest = uploaded(auth_client, owner_headers, storage, lot_id=lot["id"])
    clock.advance(timedelta(seconds=1))
    uploaded(auth_client, owner_headers, storage)
    job = claim(auth_client).json()
    assert job["job_id"] == oldest["id"]
    assert (job["camera_id"], job["lot_id"], job["attempt"]) == ("camera-07", lot["id"], 1)
    assert job["download_url"] == f"https://bucket.test/videos/{oldest['id']}.mp4?download"
    stored = auth_client.get(f"{UPLOADS_URL}/{oldest['id']}", headers=owner_headers).json()
    assert stored["status"] == "processing" and stored["started_at"] is not None


def test_two_claims_never_get_the_same_video(auth_client, owner_headers, storage):
    uploaded(auth_client, owner_headers, storage)
    assert claim(auth_client).status_code == 200
    assert claim(auth_client).status_code == 204


def test_pending_uploads_are_not_handed_out(auth_client, owner_headers, storage):
    start(auth_client, owner_headers)
    assert claim(auth_client).status_code == 204


def test_progress_is_stored_and_pushed_live(auth_client, owner, owner_headers, storage):
    upload = uploaded(auth_client, owner_headers, storage)
    job_id = claim(auth_client).json()["job_id"]
    with auth_client.websocket_connect("/ws/live") as socket:
        socket.send_json({"type": "auth", "token": owner["access_token"]})
        socket.receive_json()
        response = auth_client.put(f"{JOBS_URL}/{job_id}/progress", json={
            "frames_processed": 240, "frames_total": 1200, "fps": 22.5, "video_time_sec": 9.6, "detections": [BOX],
        })
        message = socket.receive_json()
    assert response.status_code == 204
    assert message["type"] == "video.progress"
    assert message["data"] == {
        "upload_id": upload["id"], "frames_processed": 240, "frames_total": 1200, "fps": 22.5,
        "video_time_sec": 9.6, "detections": [BOX],
    }
    stored = auth_client.get(f"{UPLOADS_URL}/{upload['id']}", headers=owner_headers).json()
    assert (stored["frames_processed"], stored["frames_total"], stored["fps"]) == (240, 1200, 22.5)


@pytest.mark.parametrize("body", [
    {"frames_processed": -1},
    {"frames_processed": 11, "frames_total": 10},
    {"frames_processed": 1, "fps": 0},
    {"frames_processed": 1, "detections": [BOX] * 201},
    {"frames_processed": 1, "extra": True},
])
def test_malformed_progress_is_rejected(auth_client, owner_headers, storage, body):
    uploaded(auth_client, owner_headers, storage)
    job_id = claim(auth_client).json()["job_id"]
    assert auth_client.put(f"{JOBS_URL}/{job_id}/progress", json=body).status_code == 422


def test_completing_a_job_files_the_analysis(auth_client, owner_headers, storage, video_analysis_payload):
    upload = uploaded(auth_client, owner_headers, storage)
    job_id = claim(auth_client).json()["job_id"]
    done = auth_client.post(f"{JOBS_URL}/{job_id}/complete", json=video_analysis_payload)
    assert done.status_code == 200
    body = done.json()
    assert (body["status"], body["frames_processed"]) == ("completed", 125)
    analysis = auth_client.get(f"/api/videos/analyses/{body['analysis_id']}", headers=owner_headers).json()
    assert analysis["video_name"] == "pasture-test.mp4"
    events = auth_client.get("/api/events", headers=owner_headers).json()
    assert [event["camera_id"] for event in events["items"]] == ["video-upload"]
    # Recorded footage describes the past: no live security alert.
    assert auth_client.get("/api/alerts", headers=owner_headers).json()["total"] == 0
    assert auth_client.get(f"{UPLOADS_URL}/{upload['id']}", headers=owner_headers).json()["status"] == "completed"


def test_a_finished_job_cannot_be_reported_on(auth_client, owner_headers, storage, video_analysis_payload):
    uploaded(auth_client, owner_headers, storage)
    job_id = claim(auth_client).json()["job_id"]
    auth_client.post(f"{JOBS_URL}/{job_id}/complete", json=video_analysis_payload)
    assert auth_client.post(f"{JOBS_URL}/{job_id}/complete", json=video_analysis_payload).status_code == 409
    assert auth_client.put(f"{JOBS_URL}/{job_id}/progress", json={"frames_processed": 1}).status_code == 409
    assert auth_client.post(f"{JOBS_URL}/{job_id}/fail", json={"error": "late"}).status_code == 409
    assert auth_client.get("/api/videos/analyses", headers=owner_headers).json()["total"] == 1


def test_an_unknown_job_is_404(auth_client, storage, video_analysis_payload):
    missing = "00000000-0000-4000-8000-000000000000"
    assert auth_client.put(f"{JOBS_URL}/{missing}/progress", json={"frames_processed": 1}).status_code == 404
    assert auth_client.post(f"{JOBS_URL}/{missing}/complete", json=video_analysis_payload).status_code == 404
    assert auth_client.post(f"{JOBS_URL}/{missing}/fail", json={"error": "x"}).status_code == 404


def test_a_worker_can_give_up_on_a_video(auth_client, owner_headers, storage):
    upload = uploaded(auth_client, owner_headers, storage)
    job_id = claim(auth_client).json()["job_id"]
    failed = auth_client.post(f"{JOBS_URL}/{job_id}/fail", json={"error": "  Cannot decode H.265 stream  "}).json()
    assert (failed["status"], failed["error"]) == ("failed", "Cannot decode H.265 stream")
    assert auth_client.get(f"{UPLOADS_URL}/{upload['id']}", headers=owner_headers).json()["finished_at"]


@pytest.mark.parametrize("error", ["", "fetch https://user:pass@bucket failed", "x" * 301])
def test_failure_messages_are_checked(auth_client, owner_headers, storage, error):
    uploaded(auth_client, owner_headers, storage)
    job_id = claim(auth_client).json()["job_id"]
    assert auth_client.post(f"{JOBS_URL}/{job_id}/fail", json={"error": error}).status_code == 422


def test_a_silent_worker_loses_the_job_after_the_lease(auth_client, owner_headers, storage, clock):
    uploaded(auth_client, owner_headers, storage)
    job_id = claim(auth_client).json()["job_id"]
    clock.advance(JOB_LEASE)
    assert claim(auth_client).status_code == 204  # exactly at the lease it is still held
    clock.advance(timedelta(seconds=1))
    retry = claim(auth_client).json()
    assert (retry["job_id"], retry["attempt"]) == (job_id, 2)


def test_progress_keeps_the_lease(auth_client, owner_headers, storage, clock):
    uploaded(auth_client, owner_headers, storage)
    job_id = claim(auth_client).json()["job_id"]
    clock.advance(JOB_LEASE)
    auth_client.put(f"{JOBS_URL}/{job_id}/progress", json={"frames_processed": 10})
    clock.advance(timedelta(seconds=1))
    assert claim(auth_client).status_code == 204


def test_a_video_that_keeps_killing_workers_is_failed(auth_client, owner_headers, storage, clock):
    upload = uploaded(auth_client, owner_headers, storage)
    for _ in range(MAX_ATTEMPTS):
        assert claim(auth_client).status_code == 200
        clock.advance(JOB_LEASE + timedelta(seconds=1))
    assert claim(auth_client).status_code == 204
    stored = auth_client.get(f"{UPLOADS_URL}/{upload['id']}", headers=owner_headers).json()
    assert stored["status"] == "failed" and f"{MAX_ATTEMPTS} times" in stored["error"]


def test_the_ai_key_guards_the_job_endpoints(secured_client):
    secured_client.app.state.video_storage = FakeStorage()
    assert claim(secured_client).status_code == 401
    assert secured_client.post(f"{JOBS_URL}/claim", headers={"X-API-Key": "test-shared-key"}).status_code == 204


def test_upload_status_changes_reach_the_dashboard(auth_client, owner, owner_headers, storage):
    with auth_client.websocket_connect("/ws/live") as socket:
        socket.send_json({"type": "auth", "token": owner["access_token"]})
        socket.receive_json()
        uploaded(auth_client, owner_headers, storage)
        claim(auth_client)
        statuses = [socket.receive_json()["data"]["status"] for _ in range(2)]
    assert statuses == ["queued", "processing"]


# --- Configuration and the S3 adapter ---------------------------------------

def test_a_half_configured_bucket_refuses_to_start():
    with pytest.raises(ValidationError, match="VIDEO_STORAGE_BUCKET"):
        Settings(_env_file=None, video_storage_endpoint="http://minio:9000")


def test_empty_storage_variables_mean_unset():
    settings = Settings(_env_file=None, **dict.fromkeys(STORAGE, ""))
    assert not settings.video_storage_enabled


def s3_storage(**extra):
    return S3VideoStorage(Settings(_env_file=None, **STORAGE, **extra))


def test_urls_are_signed_for_the_public_host():
    storage = s3_storage(video_storage_public_endpoint="http://localhost:9000")
    now = datetime(2026, 10, 31, tzinfo=UTC)
    upload = storage.presign_upload("videos/a.mp4", "video/mp4", 42, timedelta(minutes=5), now)
    assert upload.url.startswith("http://localhost:9000/videos/videos/a.mp4?")
    assert "X-Amz-Signature=" in upload.url and "X-Amz-Expires=300" in upload.url
    assert upload.expires_at == now + timedelta(minutes=5)
    assert upload.headers == {"Content-Type": "video/mp4"}
    assert storage.presign_download("videos/a.mp4").startswith("http://localhost:9000/videos/videos/a.mp4?")


def test_object_size_reads_the_bucket():
    storage = s3_storage()
    with Stubber(storage._internal) as stub:
        stub.add_response("head_object", {"ContentLength": 42}, {"Bucket": "videos", "Key": "videos/a.mp4"})
        stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
        stub.add_client_error("head_object", service_error_code="AccessDenied", http_status_code=403)
        stub.add_response("delete_object", {}, {"Bucket": "videos", "Key": "videos/a.mp4"})
        assert storage.object_size("videos/a.mp4") == 42
        assert storage.object_size("videos/missing.mp4") is None
        with pytest.raises(Exception, match="AccessDenied"):
            storage.object_size("videos/forbidden.mp4")
        storage.delete("videos/a.mp4")

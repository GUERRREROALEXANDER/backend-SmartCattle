import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.schemas.video_analysis import VideoAnalysisCreate

ANALYSES_URL = "/api/videos/analyses"


def test_analyses_start_empty(analysis_client):
    response = analysis_client.get(ANALYSES_URL)
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


def test_create_and_retrieve_analysis(analysis_client, video_analysis_payload):
    response = analysis_client.post(ANALYSES_URL, json=video_analysis_payload)
    assert response.status_code == 201
    analysis = response.json()
    assert UUID(analysis["id"]).version == 4
    assert datetime.fromisoformat(analysis["created_at"]).utcoffset() == timedelta(0)
    assert analysis["alert_count"] == 2
    assert analysis["video_name"] == video_analysis_payload["video_name"]
    assert [alert["id"] for alert in analysis["alerts"]] == [1, 2]
    assert [
        {key: value for key, value in alert.items() if key != "id"}
        for alert in analysis["alerts"]
    ] == video_analysis_payload["alerts"]
    assert analysis_client.get(ANALYSES_URL).json() == {"items": [analysis], "total": 1}
    assert analysis_client.get(f"{ANALYSES_URL}/{analysis['id']}").json() == analysis


def test_cattle_alerts_become_events_but_people_do_not(analysis_client, video_analysis_payload):
    analysis = analysis_client.post(ANALYSES_URL, json=video_analysis_payload).json()
    events = analysis_client.get("/api/events").json()
    assert events["total"] == 1
    event = events["items"][0]
    assert event["event_type"] == "cattle_out_of_zone"
    assert event["camera_id"] == "video-upload"
    assert event["detected_object"] == "cow"
    assert event["confidence"] == video_analysis_payload["avg_confidence"]
    assert event["timestamp"] == analysis["created_at"]


def test_analyses_are_latest_first_and_limited(analysis_client, video_analysis_payload):
    first = analysis_client.post(ANALYSES_URL, json=video_analysis_payload).json()
    second = analysis_client.post(
        ANALYSES_URL, json=video_analysis_payload | {"video_name": "second.mp4", "alerts": []},
    ).json()
    assert analysis_client.get(ANALYSES_URL).json() == {"items": [second, first], "total": 2}
    assert analysis_client.get(ANALYSES_URL, params={"limit": 1}).json() == {"items": [second], "total": 1}


@pytest.mark.parametrize("limit", [0, 101, "not-a-number"])
def test_invalid_analysis_list_limit(analysis_client, limit):
    assert analysis_client.get(ANALYSES_URL, params={"limit": limit}).status_code == 422


def test_missing_analysis_is_json_404(analysis_client):
    response = analysis_client.get(f"{ANALYSES_URL}/{uuid4()}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Video analysis not found"}
    assert analysis_client.get(f"{ANALYSES_URL}/not-a-uuid").status_code == 422


@pytest.mark.parametrize("changes", [
    {"video_name": ""}, {"video_name": "   "}, {"video_name": "x" * 201},
    {"duration_sec": -0.1}, {"frames_analyzed": -1}, {"max_cattle": -1}, {"max_people": -1},
    {"avg_confidence": -0.1}, {"avg_confidence": 1.1},
    {"outside_ratio": -0.1}, {"outside_ratio": 1.1},
    {"alerts": [{"track_id": 0, "cls": "cattle", "time_sec": 0}] * 501},
    {"alerts": [{"track_id": -1, "cls": "cattle", "time_sec": 0}]},
    {"alerts": [{"track_id": 0, "cls": "cattle", "time_sec": -1}]},
    {"alerts": [{"track_id": 0, "cls": "horse", "time_sec": 0}]},
    {"alert_count": 0}, {"extra_field": "unexpected"},
])
def test_invalid_analysis(analysis_client, video_analysis_payload, changes):
    response = analysis_client.post(ANALYSES_URL, json=video_analysis_payload | changes)
    assert response.status_code == 422
    assert analysis_client.get(ANALYSES_URL).json()["total"] == 0
    assert analysis_client.get("/api/events").json()["total"] == 0


@pytest.mark.parametrize("field", ["duration_sec", "avg_confidence", "outside_ratio"])
def test_non_finite_analysis_numbers(analysis_client, video_analysis_payload, field):
    payload = json.dumps(video_analysis_payload)
    body = payload.replace(f'"{field}": {video_analysis_payload[field]}', f'"{field}": NaN')
    response = analysis_client.post(ANALYSES_URL, content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert all("input" not in error for error in response.json()["detail"])


def test_video_name_is_trimmed_and_500_alerts_are_allowed(analysis_client, video_analysis_payload):
    payload = video_analysis_payload | {
        "video_name": "  pasture-test.mp4  ",
        "alerts": [{"track_id": index, "cls": "person", "time_sec": 0} for index in range(500)],
    }
    response = analysis_client.post(ANALYSES_URL, json=payload)
    assert response.status_code == 201
    assert response.json()["video_name"] == "pasture-test.mp4"
    assert response.json()["alert_count"] == 500
    assert analysis_client.get("/api/events").json()["total"] == 0


def test_in_memory_store_is_bounded(video_analysis_payload):
    from app.services.video_analysis_store import MAX_STORED_ANALYSES, InMemoryVideoAnalysisStore

    store = InMemoryVideoAnalysisStore(max_analyses=3)
    created_at = datetime(2026, 10, 27, 12, tzinfo=UTC)
    kept = [
        store.add(VideoAnalysisCreate(**(video_analysis_payload | {"video_name": f"clip-{index}.mp4"})),
                  created_at + timedelta(seconds=index))
        for index in range(5)
    ]
    listed = store.list(10)
    # The three newest survive, newest first; the two oldest are dropped.
    assert [analysis.video_name for analysis in listed] == ["clip-4.mp4", "clip-3.mp4", "clip-2.mp4"]
    # Dropped analyses are no longer retrievable, and alert ids were never reused.
    assert store.get(kept[0].id) is None
    assert len({alert.id for analysis in listed for alert in analysis.alerts}) == 6
    assert MAX_STORED_ANALYSES >= 1


def test_saving_an_analysis_requires_a_signed_in_user(auth_client, video_analysis_payload):
    # The endpoint writes into the shared events feed, so leaving it open made
    # it the one path into that table that asked for nothing at all.
    response = auth_client.post(ANALYSES_URL, json=video_analysis_payload)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    # And nothing was written.
    assert auth_client.get("/api/events").json()["total"] == 0
    assert auth_client.get(ANALYSES_URL).json()["total"] == 0


def test_a_worker_may_also_save_an_analysis(auth_client, worker_headers, video_analysis_payload):
    # Deliberately not owner-only: analysing an uploaded clip is the everyday
    # job of whoever is watching the herd, not an administrative action.
    response = auth_client.post(ANALYSES_URL, json=video_analysis_payload, headers=worker_headers)
    assert response.status_code == 201


def test_reading_analyses_stays_open(auth_client, video_analysis_payload, owner_headers):
    # Only the write path is protected. The dashboard reads these without a
    # token today, and tightening that is a separate decision from stopping
    # anonymous writes into the events table.
    auth_client.post(ANALYSES_URL, json=video_analysis_payload, headers=owner_headers)
    assert auth_client.get(ANALYSES_URL).status_code == 200

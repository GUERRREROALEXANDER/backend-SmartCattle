from datetime import UTC, datetime, timedelta

import pytest

METRICS_URL = "/api/metrics/summary"
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def test_metrics_start_empty(client):
    assert client.get(METRICS_URL).json() == {
        "animals_inside": None, "animals_outside": None,
        "alerts_today": 0, "alerts_since": None, "detections_per_minute": 0.0,
    }


def test_metrics_use_utc_detection_windows(client, event_payload):
    client.app.state.clock = lambda: NOW
    timestamps = [NOW - timedelta(minutes=30), NOW - timedelta(minutes=90), NOW - timedelta(days=1)]
    for timestamp in timestamps:
        response = client.post("/api/ai/events", json=event_payload | {"timestamp": timestamp.isoformat()})
        assert response.status_code == 201
    client.post("/api/ai/events", json=event_payload | {"timestamp": (NOW + timedelta(seconds=1)).isoformat()})

    response = client.get(METRICS_URL, params={"since": (NOW - timedelta(minutes=45)).isoformat()})
    assert response.status_code == 200
    assert response.json() == {
        "animals_inside": None, "animals_outside": None,
        "alerts_today": 2, "alerts_since": 1, "detections_per_minute": pytest.approx(1 / 60),
    }


def test_metrics_without_since_omit_that_count(client, event_payload):
    client.app.state.clock = lambda: NOW
    client.post("/api/ai/events", json=event_payload | {"timestamp": NOW.isoformat()})
    metrics = client.get(METRICS_URL).json()
    assert metrics["alerts_today"] == 1
    assert metrics["alerts_since"] is None
    assert metrics["detections_per_minute"] == pytest.approx(1 / 60)


def test_animal_counts_come_from_latest_analysis(client, video_analysis_payload):
    first = video_analysis_payload | {
        "max_cattle": 5,
        "alerts": [
            {"track_id": 1, "cls": "cattle", "time_sec": 1},
            {"track_id": 2, "cls": "cattle", "time_sec": 2},
        ],
    }
    second = video_analysis_payload | {
        "video_name": "latest.mp4", "max_cattle": 4,
        "alerts": [{"track_id": 8, "cls": "cattle", "time_sec": 1}],
    }
    client.post("/api/videos/analyses", json=first)
    client.post("/api/videos/analyses", json=second)
    metrics = client.get(METRICS_URL).json()
    assert metrics["animals_inside"] == 3
    assert metrics["animals_outside"] == 1


@pytest.mark.parametrize("since", ["2026-10-27T10:00:00", "not-a-timestamp"])
def test_metrics_since_requires_an_aware_iso_timestamp(client, since):
    response = client.get(METRICS_URL, params={"since": since})
    assert response.status_code == 422

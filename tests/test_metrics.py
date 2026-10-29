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
        "alerts_today": 2, "alerts_since": 1, # Rounded to three decimals: 1/60 is 0.0167, and the API does not
            # publish sixteen digits for a count over a fixed window.
            "detections_per_minute": 0.017,
    }


def test_metrics_without_since_omit_that_count(client, event_payload):
    client.app.state.clock = lambda: NOW
    client.post("/api/ai/events", json=event_payload | {"timestamp": NOW.isoformat()})
    metrics = client.get(METRICS_URL).json()
    assert metrics["alerts_today"] == 1
    assert metrics["alerts_since"] is None
    assert metrics["detections_per_minute"] == 0.017


def test_animal_counts_come_from_latest_analysis(auth_client, video_analysis_payload):
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
    # The clock is moved first, then the account is registered, so the token is
    # issued at the same instant the request is judged against. Registering
    # beforehand would date the token two days ahead of this test's clock and
    # it would be refused as not yet valid.
    auth_client.app.state.clock = lambda: NOW
    token = auth_client.post("/api/auth/register", json={
        "email": "ana@finca.example", "password": "una-clave-larga", "full_name": "Ana",
    }).json()["access_token"]
    auth_client.headers.update({"Authorization": f"Bearer {token}"})

    auth_client.post("/api/videos/analyses", json=first)
    auth_client.post("/api/videos/analyses", json=second)
    metrics = auth_client.get(METRICS_URL).json()
    assert metrics["animals_inside"] == 3
    assert metrics["animals_outside"] == 1


@pytest.mark.parametrize("since", ["2026-10-27T10:00:00", "not-a-timestamp"])
def test_metrics_since_requires_an_aware_iso_timestamp(client, since):
    response = client.get(METRICS_URL, params={"since": since})
    assert response.status_code == 422


def test_counts_exclude_rows_the_feed_cannot_show(tmp_path, event_payload):
    """The metrics and the event feed must agree about which rows exist.

    A row another service wrote without the columns the Event schema requires
    is not listed, so counting it would report more alerts than the dashboard
    can display, with no way to explain the gap from outside.
    """
    from uuid import uuid4

    from alembic import command
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.db import create_db_engine
    from app.main import create_app
    from tests.test_database import alembic_config, database_settings, sign_in

    url = f"sqlite:///{(tmp_path / 'metrics.db').as_posix()}"
    command.upgrade(alembic_config(url), "head")
    engine = create_db_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO cameras (name) VALUES ('camera-09')"))
            camera = connection.execute(
                text("SELECT id FROM cameras WHERE name = 'camera-09'")
            ).scalar_one()
            # Complete except for confidence: not listable, so not countable.
            connection.execute(text(
                "INSERT INTO events (type, camera_id, detected_object, confidence,"
                " detected_at, received_at, ai_event_id)"
                " VALUES ('cattle_out_of_zone', :c, 'cow', NULL, :t, :t, :e)"
            ), {"c": camera, "t": "2026-10-29 00:30:00+00:00", "e": str(uuid4())})

        settings = database_settings(url)
        with TestClient(create_app(settings)) as client:
            sign_in(client)
            assert client.get("/api/events").json()["total"] == 0
            assert client.get("/api/metrics/summary").json()["alerts_today"] == 0

            # And an event this API did ingest is listed.
            client.post("/api/ai/events", json=event_payload)
            assert client.get("/api/events").json()["total"] == 1
    finally:
        engine.dispose()


def test_the_detection_rate_is_rounded(client, event_payload):
    # The raw division serialises as 0.03333333333333333: sixteen digits of
    # precision for a count of whole events over a fixed window.
    client.post("/api/ai/events", json=event_payload | {
        "timestamp": datetime.now(UTC).isoformat(),
    })
    rate = client.get(METRICS_URL).json()["detections_per_minute"]
    assert rate == 0.017
    assert len(str(rate).split(".")[1]) <= 3


def test_an_empty_window_is_zero_not_null(client):
    # Zero is a measurement; null would mean "unknown" and the frontend draws
    # the two differently.
    assert client.get(METRICS_URL).json()["detections_per_minute"] == 0.0

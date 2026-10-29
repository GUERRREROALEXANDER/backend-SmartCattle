import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.core.config import Settings
from app.db import create_db_engine
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    return config


def database_settings(url: str) -> Settings:
    return Settings(_env_file=None, smartcattle_ai_url=None, ai_api_key=None, database_url=url)


def exercise_app(settings: Settings, event_payload: dict) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/status").json()["storage"] == settings.storage_name
        first = client.post("/api/ai/events", json=event_payload | {"bbox": [1, 2, 30, 40]}).json()
        second = client.post("/api/ai/events", json=event_payload | {"camera_id": "camera-02"}).json()
        assert client.get("/api/events").json() == {"items": [second, first], "total": 2}

        report = {"status": "online", "frame_width": 640, "frame_height": 360, "fps": 10,
                  "observed_at": "2026-10-06T15:30:00Z"}
        assert client.put("/api/ai/cameras/camera-01/status", json=report).status_code == 200
        error = client.put("/api/ai/cameras/camera-01/status",
                           json={"status": "error", "error": "Read timeout", "observed_at": "2026-10-06T15:31:00Z"})
        assert error.status_code == 200
        cameras = client.get("/api/cameras").json()
        assert cameras["total"] == 1
        camera = cameras["items"][0]
        assert camera["status"] == "error" and camera["last_error"] == "Read timeout"
        assert camera["last_online_at"] is not None and camera["frame_width"] is None

    # A new app on the same database sees the persisted data.
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/events").json()["total"] == 2
        assert client.get("/api/cameras").json()["total"] == 1


def test_sqlite_migrations_and_persistence(tmp_path, event_payload, video_analysis_payload):
    url = f"sqlite:///{(tmp_path / 'smartcattle.db').as_posix()}"
    command.upgrade(alembic_config(url), "head")
    assert {
        "cameras", "events", "video_analyses", "video_analysis_alerts", "alembic_version",
    } <= set(inspect(create_db_engine(url)).get_table_names())
    settings = database_settings(url)
    exercise_app(settings, event_payload)
    with TestClient(create_app(settings)) as client:
        analysis = client.post("/api/videos/analyses", json=video_analysis_payload).json()
        assert client.get("/api/events").json()["total"] == 3
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/videos/analyses/{analysis['id']}").json() == analysis
        assert client.get("/api/videos/analyses").json() == {"items": [analysis], "total": 1}
        metrics = client.get("/api/metrics/summary", params={"since": analysis["created_at"]}).json()
        assert metrics["animals_inside"] == 3
        assert metrics["animals_outside"] == 1
        assert metrics["alerts_since"] == 1
    command.downgrade(alembic_config(url), "base")
    # Only the backend-owned additions go; the shared tables and their rows stay.
    engine = create_db_engine(url)
    assert "stream_url" not in {column["name"] for column in inspect(engine).get_columns("cameras")}
    assert not {"video_analyses", "video_analysis_alerts"} & set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM events")).scalar_one() == 3


def test_rows_from_other_services_are_kept_and_not_listed(tmp_path, event_payload):
    url = f"sqlite:///{(tmp_path / 'smartcattle.db').as_posix()}"
    command.upgrade(alembic_config(url), "0001_shared_schema")
    with create_db_engine(url).begin() as connection:
        connection.execute(text("INSERT INTO cameras (name) VALUES ('Main camera')"))
        connection.execute(text("INSERT INTO events (type, camera_id) VALUES ('cattle_out_of_zone', 1)"))
    command.upgrade(alembic_config(url), "head")
    with TestClient(create_app(database_settings(url))) as client:
        # A seed camera without AI status and an event without ai_event_id are not API records.
        assert client.get("/api/cameras").json()["total"] == 0
        assert client.get("/api/events").json()["total"] == 0
        assert client.post("/api/ai/events", json=event_payload).status_code == 201
        assert client.get("/api/events").json()["total"] == 1
    with create_db_engine(url).connect() as connection:
        assert connection.execute(text("SELECT name FROM cameras ORDER BY id")).scalars().all() == [
            "Main camera", "camera-01"]


def foreign_event(connection, camera: str, **columns) -> None:
    """Insert an event the way another service would: no API id unless asked, nullable columns empty."""
    connection.execute(text("INSERT INTO cameras (name) VALUES (:name)"), {"name": camera})
    camera_id = connection.execute(text("SELECT id FROM cameras WHERE name = :name"), {"name": camera}).scalar_one()
    values = {
        "type": "cattle_out_of_zone", "camera_id": camera_id, "detected_object": "cow",
        "confidence": 0.9, "detected_at": "2026-10-27 12:00:00+00:00",
        "received_at": "2026-10-27 12:00:01+00:00", "ai_event_id": str(uuid4()),
    } | columns
    connection.execute(text(
        "INSERT INTO events (type, camera_id, detected_object, confidence, detected_at, received_at, ai_event_id)"
        " VALUES (:type, :camera_id, :detected_object, :confidence, :detected_at, :received_at, :ai_event_id)"
    ), values)


@pytest.mark.parametrize("camera,columns", [
    # The shared events table is written by other services, and these columns are
    # all nullable there. The Event schema requires every one of them.
    ("camera-n1", {"detected_object": None}),
    ("camera-n2", {"confidence": None}),
    # A type this backend does not model, e.g. an event kind added by the AI service.
    ("camera-n3", {"type": "person_detected"}),
])
def test_unrepresentable_rows_do_not_break_the_feed(tmp_path, event_payload, camera, columns):
    url = f"sqlite:///{(tmp_path / 'foreign.db').as_posix()}"
    command.upgrade(alembic_config(url), "head")
    with create_db_engine(url).begin() as connection:
        foreign_event(connection, camera, **columns)
    settings = database_settings(url)
    with TestClient(create_app(settings)) as client:
        # The row is skipped, not listed half-empty, and above all it does not
        # take the whole response down with it.
        assert client.get("/api/events").json() == {"items": [], "total": 0}
        assert client.get("/api/metrics/summary").status_code == 200
        # An event this API did ingest is still listed alongside the foreign row.
        created = client.post("/api/ai/events", json=event_payload).json()
        assert client.get("/api/events").json() == {"items": [created], "total": 1}


def test_camera_without_a_reported_status_is_not_listed(tmp_path, event_payload):
    url = f"sqlite:///{(tmp_path / 'cameras.db').as_posix()}"
    command.upgrade(alembic_config(url), "head")
    with create_db_engine(url).begin() as connection:
        # A timestamp without a status: what a direct UPDATE or another writer leaves behind.
        connection.execute(text(
            "INSERT INTO cameras (name, last_report_at) VALUES ('camera-07', '2026-10-27 12:00:00+00:00')"
        ))
    with TestClient(create_app(database_settings(url))) as client:
        assert client.get("/api/cameras").json() == {"items": [], "total": 0}
        # Reporting a real status brings the same row into the list, with no stale carry-over.
        report = {"status": "online", "observed_at": "2026-10-27T12:05:00Z"}
        assert client.put("/api/ai/cameras/camera-07/status", json=report).status_code == 200
        listed = client.get("/api/cameras").json()
        assert listed["total"] == 1 and listed["items"][0]["reported_status"] == "online"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set")
def test_postgresql_migrations_and_persistence(event_payload):
    # Use an empty test database: the first upgrade applies database/schema.sql to it.
    settings = database_settings(os.environ["TEST_DATABASE_URL"])
    assert settings.storage_name == "postgresql"
    config = alembic_config(settings.sqlalchemy_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_db_engine(settings.sqlalchemy_url)
    try:
        exercise_app(settings, event_payload)
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM events WHERE ai_event_id IS NOT NULL"))
            connection.execute(text("DELETE FROM cameras WHERE name IN ('camera-01', 'camera-02')"))
        command.downgrade(config, "base")


@pytest.mark.parametrize("url,expected", [
    ("postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
    ("postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ("sqlite:///local.db", "sqlite:///local.db"),
])
def test_database_url_driver(url, expected):
    assert database_settings(url).sqlalchemy_url == expected


def test_database_url_is_secret():
    settings = database_settings("postgresql://user:very-secret@host/db")
    assert "very-secret" not in repr(settings)
    assert database_settings("").sqlalchemy_url is None
    assert database_settings("").storage_name == "memory"

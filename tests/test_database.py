import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect

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


def test_sqlite_migrations_and_persistence(tmp_path, event_payload):
    url = f"sqlite:///{(tmp_path / 'smartcattle.db').as_posix()}"
    command.upgrade(alembic_config(url), "head")
    assert {"cameras", "events", "alembic_version"} <= set(inspect(create_db_engine(url)).get_table_names())
    exercise_app(database_settings(url), event_payload)
    command.downgrade(alembic_config(url), "base")
    assert set(inspect(create_db_engine(url)).get_table_names()) == {"alembic_version"}


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set")
def test_postgresql_migrations_and_persistence(event_payload):
    settings = database_settings(os.environ["TEST_DATABASE_URL"])
    assert settings.storage_name == "postgresql"
    config = alembic_config(settings.sqlalchemy_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    try:
        exercise_app(settings, event_payload)
    finally:
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

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app

INGEST_KEY = "test-ingest-key"
READ_KEY = "test-read-key"


@pytest.fixture
def settings():
    # event_storage="memory" keeps this suite independent of a running MySQL.
    # tests/test_mysql_event_store.py covers the MySQL backend separately.
    return Settings(
        _env_file=None,
        allowed_origins="http://localhost:3000,http://localhost:5173,http://127.0.0.1:5500",
        smartcattle_ai_url=None,
        ai_api_key=None,
        read_api_key=None,
        event_storage="memory",
    )


@pytest.fixture
def client(settings):
    """Both keys unset: every endpoint is open, as in local development."""
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def secured_client(settings):
    """Both keys configured, to the distinct values INGEST_KEY and READ_KEY."""
    configured = settings.model_copy(update={
        "ai_api_key": SecretStr(INGEST_KEY),
        "read_api_key": SecretStr(READ_KEY),
    })
    with TestClient(create_app(configured)) as client:
        yield client


@pytest.fixture
def event_payload():
    # A fresh ai_event_id per test: reusing one would trigger idempotent reuse.
    return {
        "ai_event_id": str(uuid4()),
        "event_type": "cattle_out_of_zone", "camera_id": "camera-01",
        "detected_object": "cow", "confidence": 0.95,
        "timestamp": "2026-10-03T15:30:00Z",
    }

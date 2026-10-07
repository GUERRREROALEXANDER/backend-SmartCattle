import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        allowed_origins="http://localhost:3000,http://localhost:5173,http://127.0.0.1:5500",
        smartcattle_ai_url=None,
        ai_api_key=None,
        database_url=None,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def secured_client(settings):
    configured = Settings(
        _env_file=None, allowed_origins=settings.allowed_origins,
        smartcattle_ai_url=None, ai_api_key="test-shared-key", database_url=None,
    )
    with TestClient(create_app(configured)) as client:
        yield client


@pytest.fixture
def event_payload():
    return {
        "event_type": "cattle_out_of_zone", "camera_id": "camera-01",
        "detected_object": "cow", "confidence": 0.95,
        "timestamp": "2026-10-03T15:30:00Z",
    }

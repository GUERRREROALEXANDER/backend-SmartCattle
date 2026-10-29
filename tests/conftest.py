import pytest
from fastapi.testclient import TestClient

from app.core import security
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


@pytest.fixture
def video_analysis_payload():
    return {
        "video_name": "pasture-test.mp4", "duration_sec": 12.5, "frames_analyzed": 125,
        "max_cattle": 4, "max_people": 1,
        "alerts": [
            {"track_id": 7, "cls": "cattle", "time_sec": 3.2},
            {"track_id": 2, "cls": "person", "time_sec": 4.5},
        ],
        "avg_confidence": 0.91, "outside_ratio": 0.25,
    }


# A 32-byte secret, the minimum Settings accepts. Fixed rather than random so a
# failure is reproducible from the test output alone.
TEST_JWT_SECRET = "test-secret-for-the-suite-0123456789"


@pytest.fixture
def auth_settings(settings):
    """Settings with authentication switched on."""
    return Settings(
        _env_file=None, allowed_origins=settings.allowed_origins,
        smartcattle_ai_url=None, ai_api_key=None, database_url=None,
        jwt_secret=TEST_JWT_SECRET,
    )


@pytest.fixture
def auth_client(auth_settings):
    with TestClient(create_app(auth_settings)) as client:
        yield client


@pytest.fixture
def owner_payload():
    return {
        "email": "ana@finca.example",
        "password": "una-clave-larga",
        "full_name": "Ana Restrepo",
    }


@pytest.fixture
def owner(auth_client, owner_payload):
    """The first registered account, which is therefore the owner."""
    return auth_client.post("/api/auth/register", json=owner_payload).json()


@pytest.fixture
def owner_headers(owner):
    return {"Authorization": f"Bearer {owner['access_token']}"}


@pytest.fixture
def worker(auth_client, owner):
    """A second account, which is therefore a worker."""
    return auth_client.post("/api/auth/register", json={
        "email": "beto@finca.example",
        "password": "otra-clave-larga",
        "full_name": "Beto Salazar",
    }).json()


@pytest.fixture
def worker_headers(worker):
    return {"Authorization": f"Bearer {worker['access_token']}"}


@pytest.fixture(autouse=True)
def cheap_password_hashing(monkeypatch):
    """Runs bcrypt at its minimum cost for the whole suite.

    The work factor is the entire point of bcrypt in production and buys
    nothing in a test: cost 12 is around a tenth of a second per hash, and the
    auth fixtures hash several times per test. At cost 4 the suite is a few
    seconds instead of half a minute.

    What is deliberately NOT changed is the algorithm or the verification path,
    so every property the tests assert -- per-row salt, the 72-byte limit,
    rejecting a malformed hash -- still holds. test_security pins the
    production default separately, so lowering it here cannot hide a change to
    the real value.
    """
    monkeypatch.setattr(security, "BCRYPT_ROUNDS", 4)

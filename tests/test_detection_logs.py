"""What the AI saw per camera and minute, on both stores."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.main import create_app
from tests.conftest import TEST_JWT_SECRET
from tests.test_accounts import save, sign_up
from tests.test_persistence import migrated

HISTORY_URL = "/api/reports/detections"
WHOLE_DAY = {"start": "2026-11-03T00:00:00Z", "end": "2026-11-04T00:00:00Z"}


def log_url(camera_id: str) -> str:
    return f"/api/ai/cameras/{camera_id}/detection-log"


@pytest.fixture(params=["memory", "database"])
def api(request, tmp_path):
    """Both stores: they must keep the same entry and return the same order."""
    if request.param == "database":
        settings = migrated(tmp_path, "detections.db")
    else:
        settings = Settings(
            _env_file=None, smartcattle_ai_url=None, ai_api_key=None, database_url=None, jwt_secret=TEST_JWT_SECRET,
        )
    # conftest switches the separation off for the single-farm suites; production has it on.
    with TestClient(create_app(settings.model_copy(update={"account_isolation": True}))) as client:
        yield client


@pytest.fixture
def ana(api):
    return sign_up(api, "ana@finca.example")[1]


@pytest.fixture
def camera(api, ana):
    return save(api, ana)


def history(api, headers, **params):
    return api.get(HISTORY_URL, params=WHOLE_DAY | params, headers=headers).json()


def test_a_report_is_kept_with_its_postures(api, ana, camera):
    response = api.put(log_url(camera), json={
        "cattle_lying": 7, "cattle_standing": 4, "cattle_walking": 1, "cattle_unknown": 2, "people": 3,
        "avg_confidence": 0.87, "observed_at": "2026-11-03T15:30:20Z",
    })
    assert response.status_code == 204
    assert history(api, ana) == {"total": 1, "items": [{
        "camera_id": camera, "minute": "2026-11-03T15:30:00Z", "cattle_lying": 7, "cattle_standing": 4,
        "cattle_walking": 1, "cattle_unknown": 2, "people": 3, "avg_confidence": 0.87,
    }]}


def test_the_last_report_of_a_minute_replaces_the_earlier_one_instead_of_adding_to_it(api, ana, camera):
    # The same five animals seen twice are five animals, not ten, and one that got
    # up during the minute must not stay counted as lying.
    api.put(log_url(camera), json={"cattle_lying": 5, "observed_at": "2026-11-03T15:30:10Z"})
    api.put(log_url(camera), json={"cattle_standing": 5, "observed_at": "2026-11-03T15:30:50Z"})
    item = history(api, ana)["items"][0]
    assert (item["cattle_lying"], item["cattle_standing"]) == (0, 5)
    assert history(api, ana)["total"] == 1


def test_a_late_retry_does_not_overwrite_a_newer_report_of_its_minute(api, ana, camera):
    api.put(log_url(camera), json={"cattle_lying": 9, "observed_at": "2026-11-03T15:30:50Z"})
    assert api.put(log_url(camera), json={"cattle_lying": 1, "observed_at": "2026-11-03T15:30:10Z"}).status_code == 204
    assert history(api, ana)["items"][0]["cattle_lying"] == 9


def test_a_late_report_of_an_earlier_minute_is_still_kept(api, ana, camera):
    # A farm link that comes back delivers its backlog out of order; those minutes are not duplicates.
    api.put(log_url(camera), json={"cattle_lying": 9, "observed_at": "2026-11-03T15:31:00Z"})
    api.put(log_url(camera), json={"cattle_lying": 1, "observed_at": "2026-11-03T15:30:59Z"})
    assert [item["cattle_lying"] for item in history(api, ana)["items"]] == [1, 9]


def test_an_empty_scene_is_recorded(api, ana, camera):
    # It separates a quiet pasture from a camera that was not watching.
    assert api.put(log_url(camera), json={"observed_at": "2026-11-03T15:30:00Z"}).status_code == 204
    assert history(api, ana)["total"] == 1


def test_the_window_includes_its_start_and_excludes_its_end(api, ana, camera):
    for moment in ("2026-11-03T09:59:59Z", "2026-11-03T10:00:00Z", "2026-11-03T10:59:59Z", "2026-11-03T11:00:00Z"):
        api.put(log_url(camera), json={"observed_at": moment})
    items = history(api, ana, start="2026-11-03T10:00:00Z", end="2026-11-03T11:00:00Z")["items"]
    assert [item["minute"] for item in items] == ["2026-11-03T10:00:00Z", "2026-11-03T10:59:00Z"]


def test_entries_come_by_camera_and_then_by_minute(api, ana):
    first, second = sorted([save(api, ana, name="Norte"), save(api, ana, name="Sur")])
    api.put(log_url(second), json={"observed_at": "2026-11-03T10:00:00Z"})
    api.put(log_url(first), json={"observed_at": "2026-11-03T11:00:00Z"})
    api.put(log_url(first), json={"observed_at": "2026-11-03T10:00:00Z"})
    assert [(item["camera_id"], item["minute"][11:16]) for item in history(api, ana)["items"]] == [
        (first, "10:00"), (first, "11:00"), (second, "10:00"),
    ]


def test_the_history_can_be_limited_to_one_camera(api, ana):
    north, south = save(api, ana, name="Norte"), save(api, ana, name="Sur")
    api.put(log_url(north), json={"observed_at": "2026-11-03T10:00:00Z"})
    api.put(log_url(south), json={"observed_at": "2026-11-03T10:00:00Z"})
    assert [item["camera_id"] for item in history(api, ana, camera_id=south)["items"]] == [south]


def test_another_account_never_sees_the_log(api, ana, camera):
    beto = sign_up(api, "beto@otra.example")[1]
    api.put(log_url(camera), json={"cattle_lying": 3, "observed_at": "2026-11-03T10:00:00Z"})
    assert history(api, beto) == {"items": [], "total": 0}
    assert history(api, beto, camera_id=camera) == {"items": [], "total": 0}


def test_the_history_needs_a_session(api):
    assert api.get(HISTORY_URL).status_code == 401


@pytest.mark.parametrize("body,expected", [
    ({"cattle_lying": 0}, 204),
    ({"cattle_lying": -1}, 422),
    ({"people": 10_000}, 204),
    ({"people": 10_001}, 422),
    ({"avg_confidence": 0}, 204),
    ({"avg_confidence": 1}, 204),
    ({"avg_confidence": -0.01}, 422),
    ({"avg_confidence": 1.01}, 422),
    ({"bbox": [0, 0, 1, 1]}, 422),
])
def test_the_report_is_validated_at_the_edge(api, camera, body, expected):
    assert api.put(log_url(camera), json=body | {"observed_at": "2026-11-03T10:00:00Z"}).status_code == expected


def test_a_time_without_a_zone_is_rejected(api, camera):
    assert api.put(log_url(camera), json={"observed_at": "2026-11-03T10:00:00"}).status_code == 422


def test_the_ai_key_is_required_once_configured(secured_client):
    body = {"observed_at": "2026-11-03T10:00:00Z"}
    assert secured_client.put(log_url("camera-01"), json=body).status_code == 401
    assert secured_client.put(
        log_url("camera-01"), json=body, headers={"X-API-Key": "test-shared-key"},
    ).status_code == 204


def test_the_database_refuses_what_the_edge_refuses(tmp_path, engine_for):
    # The test database has to reject the rows production rejects, or the two disagree silently.
    settings = migrated(tmp_path, "checks.db")
    engine = engine_for(settings.sqlalchemy_url)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO cameras (name) VALUES ('camera-01')"))
    insert = text(
        "INSERT INTO detection_logs (camera_id, minute, cattle_lying, cattle_standing, cattle_walking, "
        "cattle_unknown, people, avg_confidence, observed_at) SELECT id, '2026-11-03 10:00:00+00', :lying, 0, 0, 0, "
        "0, :confidence, '2026-11-03 10:00:00+00' FROM cameras",
    )
    for values in ({"lying": -1, "confidence": None}, {"lying": 0, "confidence": 1.5}):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(insert, values)

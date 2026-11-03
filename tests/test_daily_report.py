"""The daily report: its rules on their own, then through the API on both stores."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.time_window import day_window
from app.main import create_app
from app.schemas.detection import DetectionLogEntry
from app.services.daily_report import MIN_OBSERVED_MINUTES_FOR_HEALTH, DailyReportBuilder
from tests.conftest import TEST_JWT_SECRET
from tests.test_accounts import save, sign_up
from tests.test_persistence import migrated

URL = "/api/reports/daily"
BOGOTA = ZoneInfo("America/Bogota")
DAY = date(2026, 11, 3)
SPAN = day_window(DAY, BOGOTA)
NOW = datetime(2026, 11, 3, 18, 0, tzinfo=UTC)


def entry(minute: int = 0, camera: str = "cam-a", **counts: int) -> DetectionLogEntry:
    values = {"cattle_lying": 0, "cattle_standing": 0, "cattle_walking": 0, "cattle_unknown": 0, "people": 0}
    return DetectionLogEntry(
        camera_id=camera, minute=SPAN.start + timedelta(minutes=minute), avg_confidence=None, **(values | counts),
    )


def build(entries, now=NOW):
    return DailyReportBuilder(DAY, "America/Bogota", SPAN, now).build(entries, [], 0)


def minutes_with_share(lying: int, standing: int, minutes: int = MIN_OBSERVED_MINUTES_FOR_HEALTH):
    return [entry(minute, cattle_lying=lying, cattle_standing=standing) for minute in range(minutes)]


def test_posture_figures_are_head_minutes():
    report = build([entry(0, cattle_lying=3, cattle_standing=1), entry(1, cattle_lying=3, cattle_walking=2)])
    assert (report.herd.lying_minutes, report.herd.standing_minutes, report.herd.walking_minutes) == (6, 1, 2)
    assert report.herd.observed_minutes == 2


def test_the_herd_peak_adds_up_cameras_in_the_same_minute():
    report = build([entry(0, "cam-a", cattle_lying=3), entry(0, "cam-b", cattle_standing=4), entry(1, "cam-a")])
    assert report.herd.peak_cattle == 7
    assert [camera.peak_cattle for camera in report.cameras] == [3, 4]


def test_two_cameras_watching_one_minute_are_one_observed_minute_for_the_herd():
    report = build([entry(0, "cam-a"), entry(0, "cam-b")])
    assert report.herd.observed_minutes == 1
    assert [camera.observed_minutes for camera in report.cameras] == [1, 1]


def test_minutes_with_people_only_count_when_someone_was_there():
    report = build([entry(0, people=2), entry(1, people=0), entry(2, people=1)])
    assert report.herd.minutes_with_people == 2
    assert report.herd.peak_people == 2


def test_an_unknown_posture_is_counted_but_stays_out_of_the_share():
    report = build([entry(0, cattle_lying=1, cattle_standing=1, cattle_unknown=8)])
    assert report.herd.lying_share == 0.5
    assert report.herd.unknown_posture_minutes == 8
    assert report.herd.peak_cattle == 10


def test_a_day_without_postures_has_no_share_rather_than_zero():
    assert build([entry(0, cattle_unknown=4)]).herd.lying_share is None
    assert build([]).herd.lying_share is None


def test_an_empty_day_is_a_report_of_zeros():
    report = build([])
    assert report.herd.observed_minutes == 0
    assert report.cameras == []
    assert report.health == []


@pytest.mark.parametrize("lying,standing,expected", [
    (81, 19, "prolonged_lying"),
    (80, 20, None),
    (20, 80, None),
    (19, 81, "little_lying"),
])
def test_a_notice_appears_only_past_each_threshold(lying, standing, expected):
    notices = build(minutes_with_share(lying, standing)).health
    assert [notice.kind for notice in notices] == ([] if expected is None else [expected])


def test_too_little_footage_gives_no_notice():
    just_short = minutes_with_share(9, 1, MIN_OBSERVED_MINUTES_FOR_HEALTH - 1)
    assert build(just_short).health == []
    enough = minutes_with_share(9, 1, MIN_OBSERVED_MINUTES_FOR_HEALTH)
    assert [notice.kind for notice in build(enough).health] == ["prolonged_lying"]


def test_a_notice_names_its_camera_and_what_it_rests_on():
    notice = build(minutes_with_share(9, 1)).health[0]
    assert notice.camera_id == "cam-a"
    assert notice.lying_share == 0.9
    assert notice.observed_minutes == MIN_OBSERVED_MINUTES_FOR_HEALTH


def test_a_day_is_complete_only_once_it_has_ended():
    assert build([], now=SPAN.end - timedelta(seconds=1)).complete is False
    assert build([], now=SPAN.end).complete is True


def test_a_local_day_starts_at_local_midnight():
    assert SPAN.start == datetime(2026, 11, 3, 5, 0, tzinfo=UTC)
    assert SPAN.end == datetime(2026, 11, 4, 5, 0, tzinfo=UTC)


def test_the_day_the_clocks_change_lasts_25_hours():
    span = day_window(date(2026, 11, 1), ZoneInfo("America/New_York"))
    assert span.end - span.start == timedelta(hours=25)


@pytest.fixture(params=["memory", "database"])
def api(request, tmp_path):
    if request.param == "database":
        settings = migrated(tmp_path, "daily.db")
    else:
        settings = Settings(
            _env_file=None, smartcattle_ai_url=None, ai_api_key=None, database_url=None, jwt_secret=TEST_JWT_SECRET,
        )
    # conftest switches the separation off for the single-farm suites; production has it on.
    with TestClient(create_app(settings.model_copy(update={"account_isolation": True}))) as client:
        client.app.state.clock = lambda: NOW
        yield client


@pytest.fixture
def ana(api):
    return sign_up(api, "ana@finca.example")[1]


def log(api, camera, moment, **counts):
    response = api.put(f"/api/ai/cameras/{camera}/detection-log", json={"observed_at": moment, **counts})
    assert response.status_code == 204


def raise_alert(api, camera, moment):
    response = api.post("/api/ai/events", json={
        "event_type": "cattle_out_of_zone", "camera_id": camera, "detected_object": "cow",
        "confidence": 0.95, "timestamp": moment,
    })
    assert response.status_code == 201


def test_the_report_brings_activity_and_alerts_together(api, ana):
    camera = save(api, ana)
    log(api, camera, "2026-11-03T15:00:00Z", cattle_lying=5, cattle_standing=5, people=1)
    raise_alert(api, camera, "2026-11-03T15:00:00Z")
    body = api.get(URL, params={"day": "2026-11-03"}, headers=ana).json()
    assert body["day"] == "2026-11-03"
    assert body["time_zone"] == "America/Bogota"
    assert body["complete"] is False
    assert body["herd"]["lying_share"] == 0.5
    assert body["cameras"][0]["camera_id"] == camera
    assert body["alerts"]["total"] == body["alerts"]["open"] == 1
    assert body["events_total"] == 1


def test_it_defaults_to_today_in_the_requested_zone(api):
    # Set before signing in: tokens are checked against this clock, and it is 22:00 on the 3rd in Bogotá.
    api.app.state.clock = lambda: datetime(2026, 11, 4, 3, 0, tzinfo=UTC)
    headers = sign_up(api, "ana@finca.example")[1]
    assert api.get(URL, headers=headers).json()["day"] == "2026-11-03"
    assert api.get(URL, params={"tz": "UTC"}, headers=headers).json()["day"] == "2026-11-04"


def test_the_day_is_cut_at_local_midnight(api, ana):
    camera = save(api, ana)
    # 04:59 UTC is still the 2nd in Bogotá, and 05:00 UTC the 3rd.
    log(api, camera, "2026-11-03T04:59:00Z", cattle_lying=1)
    log(api, camera, "2026-11-03T05:00:00Z", cattle_lying=2)
    log(api, camera, "2026-11-04T04:59:00Z", cattle_lying=4)
    log(api, camera, "2026-11-04T05:00:00Z", cattle_lying=8)
    body = api.get(URL, params={"day": "2026-11-03"}, headers=ana).json()
    assert body["herd"]["lying_minutes"] == 6


def test_events_of_the_next_day_are_not_counted(api, ana):
    camera = save(api, ana)
    raise_alert(api, camera, "2026-11-03T05:00:00Z")
    raise_alert(api, camera, "2026-11-04T05:00:00Z")
    assert api.get(URL, params={"day": "2026-11-03"}, headers=ana).json()["events_total"] == 1


def test_another_account_gets_nothing_of_it(api, ana):
    camera = save(api, ana)
    log(api, camera, "2026-11-03T15:00:00Z", cattle_lying=5)
    raise_alert(api, camera, "2026-11-03T15:00:00Z")
    beto = sign_up(api, "beto@otra.example")[1]
    body = api.get(URL, params={"day": "2026-11-03"}, headers=beto).json()
    assert body["herd"]["observed_minutes"] == 0
    assert body["cameras"] == []
    assert body["alerts"]["total"] == 0
    assert body["events_total"] == 0


def test_it_needs_a_session(api):
    assert api.get(URL).status_code == 401


def test_today_is_allowed_and_tomorrow_is_not(api, ana):
    assert api.get(URL, params={"day": "2026-11-03"}, headers=ana).status_code == 200
    assert api.get(URL, params={"day": "2026-11-04"}, headers=ana).status_code == 422


@pytest.mark.parametrize("day,expected", [("2020-01-01", 200), ("2019-12-31", 422), ("0001-01-01", 422)])
def test_a_very_old_day_is_refused_rather_than_overflowing(api, ana, day, expected):
    assert api.get(URL, params={"day": day}, headers=ana).status_code == expected


def test_an_unknown_time_zone_is_refused(api, ana):
    assert api.get(URL, params={"tz": "Mars/Olympus"}, headers=ana).status_code == 422

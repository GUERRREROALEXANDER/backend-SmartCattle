"""MySqlEventStore against a real server, in a throwaway database.

The whole module is skipped when MySQL is unreachable, so the rest of the suite
still runs on a machine without a database.
"""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.schemas.event import AIEventCreate

mysql_connector = pytest.importorskip("mysql.connector")

from app.services.mysql_event_store import MySqlEventStore, create_pool  # noqa: E402

TEST_DATABASE = "smartcattle_test"
SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema.sql"

EVENT = {
    "event_type": "cattle_out_of_zone", "camera_id": "camera-01",
    "detected_object": "cow", "confidence": 0.95,
    "timestamp": "2026-10-03T15:30:00Z",
}


def event(**changes) -> AIEventCreate:
    """A valid event with a fresh ai_event_id unless the test pins one."""
    return AIEventCreate(**(EVENT | {"ai_event_id": str(uuid4())} | changes))


@pytest.fixture(scope="module")
def pool():
    """Build the throwaway database from db/schema.sql, the single source of truth."""
    settings = Settings()
    admin_config = settings.mysql_connection_config | {"database": None, "autocommit": True}
    try:
        connection = mysql_connector.connect(**admin_config)
    except mysql_connector.Error as error:
        pytest.skip(f"MySQL is not available: {error}")

    statements = SCHEMA.read_text(encoding="utf-8").replace("smartcattle", TEST_DATABASE)
    with connection.cursor() as cursor:
        for statement in (part.strip() for part in statements.split(";")):
            if statement:
                cursor.execute(statement)
    connection.close()

    yield create_pool(settings.model_copy(update={"mysql_database": TEST_DATABASE}))

    connection = mysql_connector.connect(**admin_config)
    with connection.cursor() as cursor:
        cursor.execute(f"DROP DATABASE IF EXISTS {TEST_DATABASE}")
    connection.close()


@pytest.fixture
def store(pool):
    connection = pool.get_connection()
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM events")
    connection.commit()
    connection.close()
    return MySqlEventStore(pool)


def test_events_start_empty(store):
    assert store.list() == []


def test_round_trip_preserves_every_field(store):
    created, was_created = store.add(event())
    assert was_created
    stored = store.list()
    assert stored == [created]
    assert stored[0].id.version == 4
    assert stored[0].ai_event_id == created.ai_event_id
    assert stored[0].received_at.microsecond == created.received_at.microsecond
    assert isinstance(stored[0].confidence, float)


def test_timestamps_come_back_as_aware_utc(store):
    created, _ = store.add(event())
    stored = store.list()[0]
    assert stored.timestamp.utcoffset() == timezone.utc.utcoffset(None)
    assert stored.received_at.tzinfo is timezone.utc
    assert stored.timestamp == created.timestamp


def test_offsets_are_normalized_to_the_same_instant(store):
    utc, _ = store.add(event(timestamp="2026-10-03T15:30:00Z"))
    bogota, _ = store.add(event(timestamp="2026-10-03T10:30:00-05:00"))
    assert utc.timestamp == bogota.timestamp
    assert {stored.timestamp for stored in store.list()} == {utc.timestamp}


def test_events_are_listed_newest_first(store):
    created = [store.add(event(camera_id=f"camera-{index}"))[0] for index in range(5)]
    assert store.list() == list(reversed(created))


def test_pages_cover_every_event_exactly_once(store):
    created = [store.add(event(camera_id=f"camera-{index}"))[0] for index in range(25)]
    expected = list(reversed(created))
    collected = []
    for offset in range(0, 25, 10):
        page = store.list(limit=10, offset=offset)
        assert len(page) == min(10, 25 - offset)
        collected.extend(page)
    assert collected == expected


def test_count_is_independent_of_the_page(store):
    for index in range(25):
        store.add(event(camera_id=f"camera-{index}"))
    assert len(store.list(limit=5)) == 5
    assert store.count() == 25


def test_limit_does_not_delete_the_rows_it_hides(store):
    for index in range(5):
        store.add(event(camera_id=f"camera-{index}"))
    assert len(store.list(limit=2)) == 2
    assert store.count() == 5
    assert len(store.list(limit=10)) == 5


def test_offset_past_the_end_returns_an_empty_page(store):
    store.add(event())
    assert store.list(offset=10) == []
    assert store.count() == 1


@pytest.mark.parametrize("identifier", [
    "camara-ñandú-🐄", "x'; DROP TABLE events;--", "<script>alert(1)</script>", "a\\b",
])
def test_identifiers_are_stored_verbatim(store, identifier):
    store.add(event(camera_id=identifier))
    assert store.list()[0].camera_id == identifier


def test_data_outlives_the_connection_pool(pool, store):
    created, _ = store.add(event())
    settings = Settings().model_copy(update={"mysql_database": TEST_DATABASE})
    assert MySqlEventStore(create_pool(settings)).list() == [created]


def test_retrying_the_same_ai_event_id_returns_the_stored_event(store):
    first, created = store.add(event(ai_event_id="0f8bc0f2-8e4d-4c6e-9a3b-5d7c1e2f4a60"))
    assert created
    for _ in range(3):
        retry, created_again = store.add(
            event(ai_event_id="0f8bc0f2-8e4d-4c6e-9a3b-5d7c1e2f4a60", camera_id="camera-99")
        )
        assert created_again is False
        # The stored event wins: a retry never overwrites what is already there.
        assert retry == first
        assert retry.camera_id == "camera-01"
    assert len(store.list()) == 1


def test_identical_payloads_with_distinct_ids_are_both_stored(store):
    """Two animals detected in the same second are two events, not a duplicate."""
    first, _ = store.add(event())
    second, _ = store.add(event())
    assert first.ai_event_id != second.ai_event_id
    assert len(store.list()) == 2


def test_retry_survives_a_different_process(pool, store):
    pinned = event(ai_event_id="7c9e1a44-2b6f-4d58-8e31-9f0a6c5b2d13")
    first, _ = store.add(pinned)
    settings = Settings().model_copy(update={"mysql_database": TEST_DATABASE})
    other = MySqlEventStore(create_pool(settings))
    stored, created = other.add(pinned)
    assert created is False
    assert stored == first
    assert len(store.list()) == 1


def test_duplicate_primary_key_is_rejected_by_the_database(store, pool):
    stored, _ = store.add(event())
    connection = pool.get_connection()
    try:
        with connection.cursor() as cursor, pytest.raises(mysql_connector.IntegrityError):
            cursor.execute(
                "INSERT INTO events (id, ai_event_id, event_type, camera_id,"
                " detected_object, confidence, `timestamp`, received_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (str(stored.id), str(uuid4()), "cattle_out_of_zone", "duplicate", "cow",
                 0.5, datetime(2026, 1, 1), datetime(2026, 1, 1)),
            )
    finally:
        connection.rollback()
        connection.close()
    assert len(store.list()) == 1


def test_failed_insert_leaves_no_partial_row(store):
    store.add(event())
    with pytest.raises(Exception):
        store.add(event(camera_id="x" * 65))
    assert len(store.list()) == 1

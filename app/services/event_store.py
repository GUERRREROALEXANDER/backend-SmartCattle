from collections import deque
from datetime import UTC, datetime
from threading import Lock
from typing import Protocol
from uuid import uuid4

from sqlalchemy import ColumnElement, Select, func, select

from app.db import SessionFactory
from app.models import CameraRow, EventRow
from app.schemas.event import (
    AIEventCreate,
    Event,
    EventLevel,
    EventType,
    level_for_confidence,
)
from app.services.camera_store import camera_row

MAX_LISTED_EVENTS = 1000

# Module-level alias. Inside these classes the name `list` is taken by the
# method of the same name, so a bare `list[Event]` annotation would read as a
# subscript of that method. The alias resolves in module scope, where `list`
# still means the builtin, and keeps the signatures checkable.
Events = list[Event]


class EventStore(Protocol):
    def add(self, data: AIEventCreate, received_at: datetime) -> Event: ...

    def list(self) -> Events: ...

    def list_since(self, start: datetime) -> Events: ...

    def count_between(self, start: datetime, end: datetime) -> int: ...


class InMemoryEventStore:
    """Thread-safe event storage used when DATABASE_URL is not set."""

    def __init__(self) -> None:
        self._events: deque[Event] = deque(maxlen=MAX_LISTED_EVENTS)
        self._lock = Lock()

    def add(self, data: AIEventCreate, received_at: datetime) -> Event:
        with self._lock:
            event = Event(
                **data.model_dump(), id=uuid4(), received_at=received_at,
                level=level_for_confidence(data.confidence),
            )
            self._events.append(event)
            return event

    def list(self) -> Events:
        with self._lock:
            return list(reversed(self._events))

    def list_since(self, start: datetime) -> Events:
        with self._lock:
            return [event for event in self._events if event.timestamp >= start]

    def count_between(self, start: datetime, end: datetime) -> int:
        with self._lock:
            # Counted without building a list: the caller only wants the number.
            return sum(1 for event in self._events if start <= event.timestamp <= end)


def _listable_events() -> Select[EventRow, str]:
    """Events this API can represent, joined to the camera name it reports as camera_id.

    The shared events table is written by other services too, and most of its
    columns are nullable there. A row missing any field the Event schema
    requires is left out rather than listed half-empty, which is also what
    keeps one foreign row from failing the whole response.
    """
    return (
        select(EventRow, CameraRow.name)
        .join(CameraRow, EventRow.camera_id == CameraRow.id)
        .where(*_listable_conditions())
    )


def _listable_conditions() -> list[ColumnElement[bool]]:
    """The columns the Event schema requires, as reusable WHERE conditions.

    Shared by the listing queries and by count_between, so the feed and the
    metrics cannot disagree about which rows exist.
    """
    return [
        EventRow.ai_event_id.is_not(None),
        EventRow.received_at.is_not(None),
        EventRow.detected_object.is_not(None),
        EventRow.confidence.is_not(None),
        EventRow.event_type.in_([kind.value for kind in EventType]),
    ]


def _to_event(row: EventRow, camera: str) -> Event:
    assert row.ai_event_id is not None and row.received_at is not None  # noqa: S101 - guaranteed by _listable_events
    assert row.detected_object is not None and row.confidence is not None  # noqa: S101
    # confidence is REAL (float4) in the shared schema: 0.95 reads back as 0.949999988.
    confidence = float(f"{row.confidence:.6g}")
    return Event(
        id=row.ai_event_id, event_type=EventType(row.event_type), camera_id=camera,
        detected_object=row.detected_object,
        confidence=confidence,
        # The stored level is authoritative. Rows written before this backend
        # populated the column carry the schema default, which is indistinguishable
        # from a genuine 'medium', so they are read as stored rather than
        # recomputed -- recomputing would silently rewrite history.
        level=_level(row.level, confidence),
        timestamp=_aware(row.timestamp), received_at=_aware(row.received_at), bbox=row.bbox,
    )


def _level(stored: str, confidence: float) -> EventLevel:
    """The stored severity, or a derived one for a value this API does not model.

    events.level is NOT NULL DEFAULT 'medium' in the shared schema, so there is
    always a value; it may be the default rather than a considered one. It is
    returned as stored either way, because the default and a genuine 'medium'
    cannot be told apart and recomputing would rewrite history.

    A value outside the CHECK can only come from a database predating that
    constraint. It is derived rather than raised, so one odd row does not take
    down the whole feed -- the same rule the nullability filter follows.
    """
    try:
        return EventLevel(stored)
    except ValueError:
        return level_for_confidence(confidence)


def _aware(value: datetime) -> datetime:
    # SQLite drops the timezone; PostgreSQL returns timestamptz in the session time zone.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SqlEventStore:
    """PostgreSQL event storage in the shared database's events table."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    def add(self, data: AIEventCreate, received_at: datetime) -> Event:
        event = Event(
            **data.model_dump(), id=uuid4(), received_at=received_at,
            level=level_for_confidence(data.confidence),
        )
        with self._sessions.begin() as session:
            # events.camera_id references cameras.id, so a camera seen for the first time gets a row.
            camera = camera_row(session, event.camera_id)
            session.add(EventRow(
                ai_event_id=event.id, event_type=event.event_type.value,
                level=event.level.value, camera_id=camera.id,
                detected_object=event.detected_object, confidence=event.confidence, bbox=event.bbox,
                timestamp=event.timestamp, received_at=event.received_at,
            ))
        return event

    def list(self) -> Events:
        query = (
            _listable_events()
            .order_by(EventRow.received_at.desc(), EventRow.id.desc()).limit(MAX_LISTED_EVENTS)
        )
        return self._fetch(query)

    def list_since(self, start: datetime) -> Events:
        # Metrics use detection time, and unlike the event feed this query has no 1000-row limit in SQL.
        query = _listable_events().where(EventRow.timestamp >= start).order_by(EventRow.timestamp, EventRow.id)
        return self._fetch(query)

    def count_between(self, start: datetime, end: datetime) -> int:
        """COUNT(*) over the window, so the database returns one row.

        Shares _listable_conditions() with the listing queries, so a row the
        feed will not show is not counted either. Those two disagreeing is how
        a dashboard ends up reporting more alerts than it can display.
        """
        query = (
            select(func.count())
            .select_from(EventRow)
            .where(
                *_listable_conditions(),
                EventRow.timestamp >= start,
                EventRow.timestamp <= end,
            )
        )
        with self._sessions() as session:
            return int(session.scalar(query) or 0)

    def _fetch(self, query: Select[EventRow, str]) -> Events:
        with self._sessions() as session:
            return [_to_event(row, camera) for row, camera in session.execute(query)]

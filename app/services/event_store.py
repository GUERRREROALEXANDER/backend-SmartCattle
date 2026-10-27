from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import CameraRow, EventRow
from app.schemas.event import AIEventCreate, Event
from app.services.camera_store import camera_row

MAX_LISTED_EVENTS = 1000


class EventStore(Protocol):
    def add(self, data: AIEventCreate) -> Event: ...

    def list(self) -> list[Event]: ...

    def list_since(self, start: datetime) -> "list[Event]": ...


class InMemoryEventStore:
    """Thread-safe event storage used when DATABASE_URL is not set."""

    def __init__(self) -> None:
        self._events: deque[Event] = deque(maxlen=MAX_LISTED_EVENTS)
        self._lock = Lock()

    def add(self, data: AIEventCreate) -> Event:
        with self._lock:
            event = Event(**data.model_dump(), id=uuid4(), received_at=datetime.now(timezone.utc))
            self._events.append(event)
            return event

    def list(self) -> list[Event]:
        with self._lock:
            return list(reversed(self._events))

    def list_since(self, start: datetime) -> "list[Event]":
        with self._lock:
            return [event for event in self._events if event.timestamp >= start]


def _aware(value: datetime) -> datetime:
    # SQLite drops the timezone; PostgreSQL returns timestamptz in the session time zone.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class SqlEventStore:
    """PostgreSQL event storage in the shared database's events table."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._sessions = session_factory

    def add(self, data: AIEventCreate) -> Event:
        event = Event(**data.model_dump(), id=uuid4(), received_at=datetime.now(timezone.utc))
        with self._sessions.begin() as session:
            # events.camera_id references cameras.id, so a camera seen for the first time gets a row.
            camera = camera_row(session, event.camera_id)
            session.add(EventRow(
                ai_event_id=event.id, event_type=event.event_type.value, camera_id=camera.id,
                detected_object=event.detected_object, confidence=event.confidence, bbox=event.bbox,
                timestamp=event.timestamp, received_at=event.received_at,
            ))
        return event

    def list(self) -> list[Event]:
        # Only events ingested through this API have the id, camera and fields the Event schema requires.
        query = (
            select(EventRow, CameraRow.name).join(CameraRow, EventRow.camera_id == CameraRow.id)
            .where(EventRow.ai_event_id.is_not(None), EventRow.received_at.is_not(None))
            .order_by(EventRow.received_at.desc(), EventRow.id.desc()).limit(MAX_LISTED_EVENTS)
        )
        with self._sessions() as session:
            return [
                # confidence is REAL (float4) in the shared schema: 0.95 reads back as 0.949999988.
                Event(id=row.ai_event_id, event_type=row.event_type, camera_id=camera,
                      detected_object=row.detected_object, confidence=float(f"{row.confidence:.6g}"),
                      timestamp=_aware(row.timestamp), received_at=_aware(row.received_at), bbox=row.bbox)
                for row, camera in session.execute(query)
            ]

    def list_since(self, start: datetime) -> "list[Event]":
        # Metrics use detection time, and unlike the event feed this query has no 1000-row limit in SQL.
        query = (
            select(EventRow, CameraRow.name).join(CameraRow, EventRow.camera_id == CameraRow.id)
            .where(
                EventRow.ai_event_id.is_not(None), EventRow.received_at.is_not(None),
                EventRow.timestamp >= start,
            )
            .order_by(EventRow.timestamp, EventRow.id)
        )
        with self._sessions() as session:
            return [
                Event(id=row.ai_event_id, event_type=row.event_type, camera_id=camera,
                      detected_object=row.detected_object, confidence=float(f"{row.confidence:.6g}"),
                      timestamp=_aware(row.timestamp), received_at=_aware(row.received_at), bbox=row.bbox)
                for row, camera in session.execute(query)
            ]

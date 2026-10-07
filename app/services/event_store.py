from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import EventRow
from app.schemas.event import AIEventCreate, Event

MAX_LISTED_EVENTS = 1000


class EventStore(Protocol):
    def add(self, data: AIEventCreate) -> Event: ...

    def list(self) -> list[Event]: ...


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


def _aware(value: datetime) -> datetime:
    # SQLite drops the timezone; PostgreSQL timestamptz keeps it.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class SqlEventStore:
    """PostgreSQL event storage; the schema comes from Alembic migrations."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._sessions = session_factory

    def add(self, data: AIEventCreate) -> Event:
        event = Event(**data.model_dump(), id=uuid4(), received_at=datetime.now(timezone.utc))
        with self._sessions.begin() as session:
            session.add(EventRow(**event.model_dump(mode="python") | {"event_type": event.event_type.value}))
        return event

    def list(self) -> list[Event]:
        query = select(EventRow).order_by(EventRow.received_at.desc()).limit(MAX_LISTED_EVENTS)
        with self._sessions() as session:
            return [
                Event(id=row.id, event_type=row.event_type, camera_id=row.camera_id,
                      detected_object=row.detected_object, confidence=row.confidence,
                      timestamp=_aware(row.timestamp), received_at=_aware(row.received_at), bbox=row.bbox)
                for row in session.scalars(query)
            ]

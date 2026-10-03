from collections import deque
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from app.schemas.event import AIEventCreate, Event


class InMemoryEventStore:
    """Thread-safe event storage and replacement point for PostgreSQL."""

    def __init__(self) -> None:
        self._events: deque[Event] = deque(maxlen=1000)
        self._lock = Lock()

    def add(self, data: AIEventCreate) -> Event:
        with self._lock:
            event = Event(**data.model_dump(), id=uuid4(), received_at=datetime.now(timezone.utc))
            self._events.append(event)
            return event

    def list(self) -> list[Event]:
        with self._lock:
            return list(reversed(self._events))

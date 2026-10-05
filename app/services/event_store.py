from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Protocol
from uuid import uuid4

from app.schemas.event import DEFAULT_PAGE_SIZE, AIEventCreate, Event


class EventStore(Protocol):
    """The methods the routes depend on. See docs/ARCHITECTURE.md section 5."""

    def add(self, data: AIEventCreate) -> tuple[Event, bool]:
        """Store the event, or return the one already stored under its ai_event_id.

        The boolean is True when a new event was stored and False when an earlier
        one was returned, which the route turns into 201 or 200.
        """
        ...

    def list(self, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0) -> list[Event]:
        """One page of events, most recently received first."""
        ...

    def count(self) -> int:
        """Events stored in total, so a client can tell how many pages exist."""
        ...


class InMemoryEventStore:
    """Thread-safe event storage for development: data is lost on restart."""

    def __init__(self) -> None:
        self._events: deque[Event] = deque(maxlen=1000)
        self._lock = Lock()

    def add(self, data: AIEventCreate) -> tuple[Event, bool]:
        with self._lock:
            for stored in self._events:
                if stored.ai_event_id == data.ai_event_id:
                    return stored, False
            event = Event(**data.model_dump(), id=uuid4(), received_at=datetime.now(timezone.utc))
            self._events.append(event)
            return event, True

    def list(self, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0) -> list[Event]:
        with self._lock:
            newest_first = list(reversed(self._events))
        return newest_first[offset:offset + limit]

    def count(self) -> int:
        with self._lock:
            return len(self._events)

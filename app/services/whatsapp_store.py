"""Who receives WhatsApp messages, and the messages waiting for the service that sends them.

The sending service claims from this table instead of being called. It runs on a
PC that the backend cannot reach, and a message written here survives that PC
being off. Same design as the video queue (ARCHITECTURE section 12).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Protocol

from sqlalchemy import select

from app.db import SessionFactory
from app.models import WhatsappContactRow, WhatsappMessageRow
from app.schemas.whatsapp import WhatsappContact, WhatsappMessage
from app.services.camera_store import camera_row

PENDING = "pending"
SENT = "sent"
FAILED = "failed"
#: A claimed message that gets no answer in this long goes back to the queue: the service may have died mid-send.
LEASE = timedelta(seconds=60)
MAX_ATTEMPTS = 3

Contacts = list[WhatsappContact]
Messages = list[WhatsappMessage]


class WhatsappStore(Protocol):
    def contact(self, user_id: int) -> WhatsappContact | None: ...

    def link(self, user_id: int, phone: str, now: datetime) -> WhatsappContact:
        """Records a verified number, with notices switched on: asking for them is why it was verified."""
        ...

    def set_enabled(self, user_id: int, enabled: bool) -> WhatsappContact | None: ...

    def unlink(self, user_id: int) -> bool: ...

    def enabled_contacts(self, user_ids: frozenset[int]) -> Contacts:
        """By user id."""
        ...

    def queue(self, phone: str, body: str, now: datetime, expires_at: datetime) -> None: ...

    def queue_camera_notice(
        self, camera_id: str, phones: Iterable[str], body: str, now: datetime, expires_at: datetime, quiet: timedelta,
    ) -> int:
        """Queues one message per number, unless this camera already queued some within `quiet`. Returns how many."""
        ...

    def claim(self, now: datetime, limit: int) -> Messages:
        """The oldest messages ready to send, leased to the caller."""
        ...

    def finish(self, message_id: int, error: str | None, now: datetime) -> bool:
        """Records the outcome; False if there is no such message. Reporting one twice changes nothing."""
        ...


def _aware(value: datetime) -> datetime:
    # SQLite drops the timezone; PostgreSQL returns timestamptz in the session time zone.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass
class _Message:
    """The same attributes as WhatsappMessageRow, so both stores run the same rules over them."""

    id: int
    phone: str
    body: str
    camera_id: str | None
    status: str
    attempts: int
    created_at: datetime
    expires_at: datetime
    claimed_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


def _close(message: _Message | WhatsappMessageRow, status: str, error: str | None, now: datetime) -> None:
    # The body can be a verification code, and the alert it describes is already stored.
    message.status, message.error, message.finished_at, message.body = status, error, now, ""


def _take(message: _Message | WhatsappMessageRow, now: datetime) -> bool:
    """Whether the caller may send this pending message now, leasing it if so."""
    if _aware(message.expires_at) <= now:
        _close(message, FAILED, "expired", now)
        return False
    if message.claimed_at is not None and now - _aware(message.claimed_at) < LEASE:
        return False
    if message.attempts >= MAX_ATTEMPTS:
        _close(message, FAILED, "too many attempts", now)
        return False
    message.claimed_at, message.attempts = now, message.attempts + 1
    return True


def _to_outgoing(message: _Message | WhatsappMessageRow) -> WhatsappMessage:
    return WhatsappMessage(id=message.id, phone=message.phone, body=message.body)


class InMemoryWhatsappStore:
    def __init__(self) -> None:
        self._contacts: dict[int, WhatsappContact] = {}
        self._messages: dict[int, _Message] = {}
        self._next_id = 1
        self._lock = Lock()

    def contact(self, user_id: int) -> WhatsappContact | None:
        with self._lock:
            return self._contacts.get(user_id)

    def link(self, user_id: int, phone: str, now: datetime) -> WhatsappContact:
        contact = WhatsappContact(user_id=user_id, phone=phone, enabled=True, verified_at=now)
        with self._lock:
            self._contacts[user_id] = contact
        return contact

    def set_enabled(self, user_id: int, enabled: bool) -> WhatsappContact | None:
        with self._lock:
            contact = self._contacts.get(user_id)
            if contact is None:
                return None
            self._contacts[user_id] = contact.model_copy(update={"enabled": enabled})
            return self._contacts[user_id]

    def unlink(self, user_id: int) -> bool:
        with self._lock:
            return self._contacts.pop(user_id, None) is not None

    def enabled_contacts(self, user_ids: frozenset[int]) -> Contacts:
        with self._lock:
            return [
                self._contacts[key] for key in sorted(self._contacts)
                if key in user_ids and self._contacts[key].enabled
            ]

    def _add(self, phone: str, body: str, camera_id: str | None, now: datetime, expires_at: datetime) -> None:
        self._messages[self._next_id] = _Message(
            id=self._next_id, phone=phone, body=body, camera_id=camera_id, status=PENDING, attempts=0,
            created_at=now, expires_at=expires_at,
        )
        self._next_id += 1

    def queue(self, phone: str, body: str, now: datetime, expires_at: datetime) -> None:
        with self._lock:
            self._add(phone, body, None, now, expires_at)

    def queue_camera_notice(
        self, camera_id: str, phones: Iterable[str], body: str, now: datetime, expires_at: datetime, quiet: timedelta,
    ) -> int:
        with self._lock:
            if any(item.camera_id == camera_id and item.created_at > now - quiet for item in self._messages.values()):
                return 0
            numbers = list(phones)
            for phone in numbers:
                self._add(phone, body, camera_id, now, expires_at)
            return len(numbers)

    def claim(self, now: datetime, limit: int) -> Messages:
        taken: Messages = []
        with self._lock:
            for key in sorted(self._messages):
                message = self._messages[key]
                if len(taken) < limit and message.status == PENDING and _take(message, now):
                    taken.append(_to_outgoing(message))
        return taken

    def finish(self, message_id: int, error: str | None, now: datetime) -> bool:
        with self._lock:
            message = self._messages.get(message_id)
            if message is None:
                return False
            if message.status == PENDING:
                _close(message, SENT if error is None else FAILED, error, now)
            return True


def _to_contact(row: WhatsappContactRow) -> WhatsappContact:
    return WhatsappContact(
        user_id=row.user_id, phone=row.phone, enabled=row.enabled, verified_at=_aware(row.verified_at),
    )


class SqlWhatsappStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    def contact(self, user_id: int) -> WhatsappContact | None:
        with self._sessions() as session:
            row = session.get(WhatsappContactRow, user_id)
            return None if row is None else _to_contact(row)

    def link(self, user_id: int, phone: str, now: datetime) -> WhatsappContact:
        with self._sessions.begin() as session:
            row = session.merge(WhatsappContactRow(user_id=user_id, phone=phone, enabled=True, verified_at=now))
            session.flush()
            return _to_contact(row)

    def set_enabled(self, user_id: int, enabled: bool) -> WhatsappContact | None:
        with self._sessions.begin() as session:
            row = session.get(WhatsappContactRow, user_id)
            if row is None:
                return None
            row.enabled = enabled
            return _to_contact(row)

    def unlink(self, user_id: int) -> bool:
        with self._sessions.begin() as session:
            row = session.get(WhatsappContactRow, user_id)
            if row is None:
                return False
            session.delete(row)
            return True

    def enabled_contacts(self, user_ids: frozenset[int]) -> Contacts:
        query = (
            select(WhatsappContactRow)
            .where(WhatsappContactRow.user_id.in_(user_ids), WhatsappContactRow.enabled.is_(True))
            .order_by(WhatsappContactRow.user_id)
        )
        with self._sessions() as session:
            return [_to_contact(row) for row in session.scalars(query)]

    def queue(self, phone: str, body: str, now: datetime, expires_at: datetime) -> None:
        with self._sessions.begin() as session:
            session.add(WhatsappMessageRow(
                phone=phone, body=body, camera_id=None, status=PENDING, attempts=0,
                created_at=now, expires_at=expires_at,
            ))

    def queue_camera_notice(
        self, camera_id: str, phones: Iterable[str], body: str, now: datetime, expires_at: datetime, quiet: timedelta,
    ) -> int:
        recent = select(WhatsappMessageRow.id).where(
            WhatsappMessageRow.camera_id == camera_id, WhatsappMessageRow.created_at > now - quiet,
        ).limit(1)
        with self._sessions.begin() as session:
            # The camera's row lock serialises its alerts, so two at once cannot both find the window empty.
            camera_row(session, camera_id, lock=True)
            if session.scalar(recent) is not None:
                return 0
            numbers = list(phones)
            session.add_all(
                WhatsappMessageRow(
                    phone=phone, body=body, camera_id=camera_id, status=PENDING, attempts=0,
                    created_at=now, expires_at=expires_at,
                )
                for phone in numbers
            )
            return len(numbers)

    def claim(self, now: datetime, limit: int) -> Messages:
        query = (
            select(WhatsappMessageRow)
            .where(WhatsappMessageRow.status == PENDING)
            .order_by(WhatsappMessageRow.id)
            .with_for_update(skip_locked=True)
        )
        taken: Messages = []
        with self._sessions.begin() as session:
            for row in session.scalars(query):
                if len(taken) < limit and _take(row, now):
                    taken.append(_to_outgoing(row))
        return taken

    def finish(self, message_id: int, error: str | None, now: datetime) -> bool:
        query = select(WhatsappMessageRow).where(WhatsappMessageRow.id == message_id).with_for_update()
        with self._sessions.begin() as session:
            row = session.scalars(query).first()
            if row is None:
                return False
            if row.status == PENDING:
                _close(row, SENT if error is None else FAILED, error, now)
            return True

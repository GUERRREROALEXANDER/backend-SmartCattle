"""Who did what, and when.

Written after the action it describes, in its own transaction. Folding it
into every store's transaction would couple each store to the log; the price
is that a crash between the two loses the entry, never the action. A failed
write is logged and swallowed for the same reason: the farm change already
happened, and answering 500 would make the client retry something that
succeeded.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Protocol

from sqlalchemy import ColumnElement, func, select

from app.db import SessionFactory
from app.models import AuditLogRow
from app.schemas.audit import AuditAction, AuditEntry, AuditPage, AuditRecord

logger = logging.getLogger(__name__)

#: In-memory mode keeps a bounded tail, like the in-memory event store.
MAX_IN_MEMORY_ENTRIES = 5000


@dataclass(frozen=True)
class AuditQuery:
    actor_id: int | None = None
    action: AuditAction | None = None
    target_type: str | None = None
    target_id: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    limit: int = 50
    offset: int = 0
    actor_ids: frozenset[int] | None = None

    def matches(self, entry: AuditEntry) -> bool:
        return (
            (self.actor_ids is None or entry.actor_id in self.actor_ids)
            and (self.actor_id is None or entry.actor_id == self.actor_id)
            and (self.action is None or entry.action is self.action)
            and (self.target_type is None or entry.target_type == self.target_type)
            and (self.target_id is None or entry.target_id == self.target_id)
            and (self.start is None or entry.created_at >= self.start)
            and (self.end is None or entry.created_at < self.end)
        )


class AuditLog(Protocol):
    def record(self, record: AuditRecord) -> AuditEntry: ...

    def page(self, query: AuditQuery) -> AuditPage: ...


class InMemoryAuditLog:
    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._next_id = 1
        self._lock = Lock()

    def record(self, record: AuditRecord) -> AuditEntry:
        with self._lock:
            entry = AuditEntry(**record.model_dump(), id=self._next_id)
            self._next_id += 1
            self._entries.append(entry)
            del self._entries[:-MAX_IN_MEMORY_ENTRIES]
            return entry

    def page(self, query: AuditQuery) -> AuditPage:
        with self._lock:
            matching = [entry for entry in reversed(self._entries) if query.matches(entry)]
        return AuditPage(items=matching[query.offset:query.offset + query.limit], total=len(matching))


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _to_entry(row: AuditLogRow) -> AuditEntry:
    return AuditEntry(
        id=row.id, actor_id=row.actor_id, actor_email=row.actor_email, action=AuditAction(row.action),
        target_type=row.target_type, target_id=row.target_id, detail=row.detail,
        ip_address=row.ip_address, created_at=_aware(row.created_at),
    )


class SqlAuditLog:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    def record(self, record: AuditRecord) -> AuditEntry:
        row = AuditLogRow(**record.model_dump(mode="python") | {"action": record.action.value})
        with self._sessions.begin() as session:
            session.add(row)
            session.flush()
            return _to_entry(row)

    def page(self, query: AuditQuery) -> AuditPage:
        conditions: list[ColumnElement[bool]] = [
            AuditLogRow.action.in_([action.value for action in AuditAction]),
        ]
        if query.actor_ids is not None:
            conditions.append(AuditLogRow.actor_id.in_(query.actor_ids))
        if query.actor_id is not None:
            conditions.append(AuditLogRow.actor_id == query.actor_id)
        if query.action is not None:
            conditions.append(AuditLogRow.action == query.action.value)
        if query.target_type is not None:
            conditions.append(AuditLogRow.target_type == query.target_type)
        if query.target_id is not None:
            conditions.append(AuditLogRow.target_id == query.target_id)
        if query.start is not None:
            conditions.append(AuditLogRow.created_at >= query.start)
        if query.end is not None:
            conditions.append(AuditLogRow.created_at < query.end)
        rows = (
            select(AuditLogRow).where(*conditions)
            .order_by(AuditLogRow.created_at.desc(), AuditLogRow.id.desc())
            .limit(query.limit).offset(query.offset)
        )
        total = select(func.count()).select_from(AuditLogRow).where(*conditions)
        with self._sessions() as session:
            return AuditPage(
                items=[_to_entry(row) for row in session.scalars(rows)],
                total=int(session.scalar(total) or 0),
            )


@dataclass(frozen=True)
class Auditor:
    """The log bound to one request: its clock reading and its client address."""

    log: AuditLog
    now: datetime
    ip_address: str | None

    def record(
        self,
        action: AuditAction,
        *,
        actor_id: int | None,
        actor_email: str | None,
        target_type: str | None = None,
        target_id: object = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.log.record(AuditRecord(
                actor_id=actor_id, actor_email=actor_email, action=action, target_type=target_type,
                target_id=None if target_id is None else str(target_id), detail=detail,
                ip_address=self.ip_address, created_at=self.now,
            ))
        except Exception:
            logger.exception("Could not write the audit entry for %s", action.value)

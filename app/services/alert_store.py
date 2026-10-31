"""Security alert storage and its state machine."""

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, func, select

from app.db import SessionFactory
from app.models import CameraRow, SecurityAlertRow
from app.schemas.alert import TRANSITIONS, Alert, AlertStatus
from app.schemas.event import Event, EventLevel, EventType
from app.services.camera_store import camera_row

#: How each event type reads on the dashboard. A mapping rather than an
#: f-string at the call site so adding an event type fails loudly here
#: (KeyError in the tests) instead of producing a vague message.
MESSAGES: dict[EventType, str] = {
    EventType.CATTLE_OUT_OF_ZONE: "Cattle left the safe zone on {camera}",
}


class AlertTransitionError(Exception):
    """The alert is not in a state that allows the requested change."""

    def __init__(self, current: AlertStatus, target: AlertStatus) -> None:
        super().__init__(f"{current} -> {target}")
        self.current = current
        self.target = target


@dataclass(frozen=True)
class AlertQuery:
    status: AlertStatus | None = None
    severity: EventLevel | None = None
    camera_id: str | None = None
    limit: int = 50
    offset: int = 0

    def matches(self, alert: Alert) -> bool:
        return (
            (self.status is None or alert.status is self.status)
            and (self.severity is None or alert.severity is self.severity)
            and (self.camera_id is None or alert.camera_id == self.camera_id)
        )


@dataclass(frozen=True)
class AlertPage:
    items: list[Alert]
    total: int


class AlertStore(Protocol):
    def raise_for(self, event: Event, now: datetime) -> Alert: ...

    def get(self, alert_id: UUID) -> Alert | None: ...

    def page(self, query: AlertQuery) -> AlertPage: ...

    def transition(self, alert_id: UUID, target: AlertStatus, user_id: int, now: datetime) -> Alert | None: ...


def alert_for(event: Event, now: datetime) -> Alert:
    return Alert(
        id=uuid4(), event_id=event.id, camera_id=event.camera_id, alert_type=event.event_type,
        severity=event.level, status=AlertStatus.OPEN,
        message=MESSAGES[event.event_type].format(camera=event.camera_id), created_at=now,
    )


def advance(alert: Alert, target: AlertStatus, user_id: int, now: datetime) -> Alert:
    """The alert after moving it to `target`, or AlertTransitionError."""
    if target not in TRANSITIONS[alert.status]:
        raise AlertTransitionError(alert.status, target)
    changes: dict[str, object] = {"status": target}
    if target is AlertStatus.ACKNOWLEDGED:
        changes |= {"acknowledged_at": now, "acknowledged_by": user_id}
    else:
        changes |= {"resolved_at": now, "resolved_by": user_id}
    return alert.model_copy(update=changes)


def _newest_first(alert: Alert) -> tuple[datetime, str]:
    return alert.created_at, str(alert.id)


class InMemoryAlertStore:
    """Thread-safe storage used when DATABASE_URL is not set."""

    def __init__(self) -> None:
        self._alerts: dict[UUID, Alert] = {}
        self._lock = Lock()

    def raise_for(self, event: Event, now: datetime) -> Alert:
        alert = alert_for(event, now)
        with self._lock:
            self._alerts[alert.id] = alert
        return alert

    def get(self, alert_id: UUID) -> Alert | None:
        with self._lock:
            return self._alerts.get(alert_id)

    def page(self, query: AlertQuery) -> AlertPage:
        with self._lock:
            matching = sorted(
                (alert for alert in self._alerts.values() if query.matches(alert)), key=_newest_first, reverse=True,
            )
        return AlertPage(items=matching[query.offset:query.offset + query.limit], total=len(matching))

    def transition(self, alert_id: UUID, target: AlertStatus, user_id: int, now: datetime) -> Alert | None:
        with self._lock:
            current = self._alerts.get(alert_id)
            if current is None:
                return None
            updated = advance(current, target, user_id, now)
            self._alerts[alert_id] = updated
            return updated


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _to_alert(row: SecurityAlertRow, camera: str | None) -> Alert:
    created_at = _aware(row.created_at)
    assert created_at is not None  # noqa: S101 - NOT NULL in the table
    return Alert(
        id=row.id, event_id=row.event_id, camera_id=camera, alert_type=EventType(row.alert_type),
        severity=EventLevel(row.severity), status=AlertStatus(row.status), message=row.message,
        created_at=created_at, acknowledged_at=_aware(row.acknowledged_at), acknowledged_by=row.acknowledged_by,
        resolved_at=_aware(row.resolved_at), resolved_by=row.resolved_by,
    )


_WITH_CAMERA = select(SecurityAlertRow, CameraRow.name).outerjoin(
    CameraRow, SecurityAlertRow.camera_id == CameraRow.id,
)


class SqlAlertStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    def raise_for(self, event: Event, now: datetime) -> Alert:
        alert = alert_for(event, now)
        with self._sessions.begin() as session:
            session.add(SecurityAlertRow(
                id=alert.id, event_id=alert.event_id, camera_id=camera_row(session, event.camera_id).id,
                alert_type=alert.alert_type.value, severity=alert.severity.value, status=alert.status.value,
                message=alert.message, created_at=alert.created_at,
            ))
        return alert

    def get(self, alert_id: UUID) -> Alert | None:
        with self._sessions() as session:
            found = session.execute(_WITH_CAMERA.where(SecurityAlertRow.id == alert_id)).first()
            return None if found is None else _to_alert(found[0], found[1])

    def page(self, query: AlertQuery) -> AlertPage:
        conditions: list[ColumnElement[bool]] = []
        if query.status is not None:
            conditions.append(SecurityAlertRow.status == query.status.value)
        if query.severity is not None:
            conditions.append(SecurityAlertRow.severity == query.severity.value)
        if query.camera_id is not None:
            conditions.append(CameraRow.name == query.camera_id)
        rows = (
            _WITH_CAMERA.where(*conditions)
            .order_by(SecurityAlertRow.created_at.desc(), SecurityAlertRow.id.desc())
            .limit(query.limit).offset(query.offset)
        )
        total = (
            select(func.count()).select_from(SecurityAlertRow)
            .outerjoin(CameraRow, SecurityAlertRow.camera_id == CameraRow.id).where(*conditions)
        )
        with self._sessions() as session:
            items = [_to_alert(row, camera) for row, camera in session.execute(rows)]
            return AlertPage(items=items, total=int(session.scalar(total) or 0))

    def transition(self, alert_id: UUID, target: AlertStatus, user_id: int, now: datetime) -> Alert | None:
        with self._sessions.begin() as session:
            # Locked so two people acknowledging at once cannot both succeed:
            # the second must see the first's change and get the 409.
            row = session.get(SecurityAlertRow, alert_id, with_for_update=True)
            if row is None:
                return None
            camera = None if row.camera_id is None else session.get(CameraRow, row.camera_id)
            updated = advance(_to_alert(row, None if camera is None else camera.name), target, user_id, now)
            row.status = updated.status.value
            row.acknowledged_at, row.acknowledged_by = updated.acknowledged_at, updated.acknowledged_by
            row.resolved_at, row.resolved_by = updated.resolved_at, updated.resolved_by
            return updated

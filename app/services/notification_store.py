"""Where each account wants its alerts, and the record of every message sent."""

from datetime import UTC, datetime
from threading import Lock
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select

from app.db import SessionFactory
from app.models import NotificationDeliveryRow, NotificationSettingsRow
from app.schemas.event import EventLevel
from app.schemas.notification import Channel, Delivery, DeliveryStatus, NotificationSettings

#: The account of a deployment without account separation, where cameras need no owner to reach someone.
NO_SEPARATION_ACCOUNT = 0
#: In memory only: the database keeps them all, the page shows the latest.
MAX_IN_MEMORY_DELIVERIES = 1000

Deliveries = list[Delivery]


class NotificationStore(Protocol):
    def settings_of(self, account_id: int) -> NotificationSettings: ...

    def save_settings(self, account_id: int, settings: NotificationSettings, now: datetime) -> None: ...

    def add_delivery(self, account_id: int, delivery: Delivery) -> None: ...

    def finish_delivery(
        self, delivery_id: UUID, status: DeliveryStatus, attempts: int, detail: str | None, sent_at: datetime | None,
    ) -> None: ...

    def was_told_since(self, account_id: int, camera_id: str | None, alert_type: str, since: datetime) -> bool:
        """Whether a message about this kind of alert on this camera went out or is on its way. Failures do not count:
        the next alert must get its chance."""
        ...

    def deliveries(self, account_id: int, limit: int) -> tuple[Deliveries, int]:
        """The latest, newest first, and how many there are."""
        ...


class InMemoryNotificationStore:
    def __init__(self) -> None:
        self._settings: dict[int, NotificationSettings] = {}
        self._deliveries: list[tuple[int, Delivery]] = []
        self._lock = Lock()

    def settings_of(self, account_id: int) -> NotificationSettings:
        with self._lock:
            return self._settings.get(account_id, NotificationSettings())

    def save_settings(self, account_id: int, settings: NotificationSettings, now: datetime) -> None:  # noqa: ARG002
        with self._lock:
            self._settings[account_id] = settings

    def add_delivery(self, account_id: int, delivery: Delivery) -> None:
        with self._lock:
            self._deliveries.append((account_id, delivery))
            del self._deliveries[:-MAX_IN_MEMORY_DELIVERIES]

    def finish_delivery(
        self, delivery_id: UUID, status: DeliveryStatus, attempts: int, detail: str | None, sent_at: datetime | None,
    ) -> None:
        with self._lock:
            for index, (account, delivery) in enumerate(self._deliveries):
                if delivery.id == delivery_id:
                    self._deliveries[index] = (account, delivery.model_copy(update={
                        "status": status, "attempts": attempts, "detail": detail, "sent_at": sent_at,
                    }))
                    return

    def was_told_since(self, account_id: int, camera_id: str | None, alert_type: str, since: datetime) -> bool:
        with self._lock:
            return any(
                account == account_id and item.camera_id == camera_id and item.alert_type == alert_type
                and item.created_at > since and item.status is not DeliveryStatus.FAILED
                for account, item in self._deliveries
            )

    def deliveries(self, account_id: int, limit: int) -> tuple[Deliveries, int]:
        with self._lock:
            mine = [item for account, item in self._deliveries if account == account_id]
        newest = sorted(mine, key=lambda item: (item.created_at, str(item.id)), reverse=True)
        return newest[:limit], len(mine)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _to_delivery(row: NotificationDeliveryRow) -> Delivery:
    created_at = _aware(row.created_at)
    assert created_at is not None  # noqa: S101 - NOT NULL in the table
    return Delivery(
        id=row.id, alert_id=row.alert_id, camera_id=row.camera_id, alert_type=row.alert_type,
        channel=Channel(row.channel), status=DeliveryStatus(row.status), attempts=row.attempts, detail=row.detail,
        created_at=created_at, sent_at=_aware(row.sent_at),
    )


class SqlNotificationStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    def settings_of(self, account_id: int) -> NotificationSettings:
        with self._sessions() as session:
            row = session.get(NotificationSettingsRow, account_id)
            if row is None:
                return NotificationSettings()
            return NotificationSettings(
                enabled=row.enabled, whatsapp_phone=row.whatsapp_phone, telegram_chat_id=row.telegram_chat_id,
                min_severity=EventLevel(row.min_severity),
            )

    def save_settings(self, account_id: int, settings: NotificationSettings, now: datetime) -> None:
        with self._sessions.begin() as session:
            row = session.get(NotificationSettingsRow, account_id)
            if row is None:
                row = NotificationSettingsRow(account_id=account_id)
                session.add(row)
            row.enabled = settings.enabled
            row.whatsapp_phone = settings.whatsapp_phone
            row.telegram_chat_id = settings.telegram_chat_id
            row.min_severity = settings.min_severity.value
            row.updated_at = now

    def add_delivery(self, account_id: int, delivery: Delivery) -> None:
        with self._sessions.begin() as session:
            session.add(NotificationDeliveryRow(
                id=delivery.id, account_id=account_id, alert_id=delivery.alert_id, camera_id=delivery.camera_id,
                alert_type=delivery.alert_type, channel=delivery.channel.value, status=delivery.status.value,
                attempts=delivery.attempts, detail=delivery.detail, created_at=delivery.created_at,
                sent_at=delivery.sent_at,
            ))

    def finish_delivery(
        self, delivery_id: UUID, status: DeliveryStatus, attempts: int, detail: str | None, sent_at: datetime | None,
    ) -> None:
        with self._sessions.begin() as session:
            row = session.get(NotificationDeliveryRow, delivery_id)
            if row is not None:
                row.status, row.attempts, row.detail, row.sent_at = status.value, attempts, detail, sent_at

    def was_told_since(self, account_id: int, camera_id: str | None, alert_type: str, since: datetime) -> bool:
        query = select(func.count()).select_from(NotificationDeliveryRow).where(
            NotificationDeliveryRow.account_id == account_id,
            NotificationDeliveryRow.alert_type == alert_type,
            NotificationDeliveryRow.created_at > since,
            NotificationDeliveryRow.status != DeliveryStatus.FAILED.value,
            NotificationDeliveryRow.camera_id.is_(None) if camera_id is None
            else NotificationDeliveryRow.camera_id == camera_id,
        )
        with self._sessions() as session:
            return bool(session.scalar(query))

    def deliveries(self, account_id: int, limit: int) -> tuple[Deliveries, int]:
        rows = (
            select(NotificationDeliveryRow).where(NotificationDeliveryRow.account_id == account_id)
            .order_by(NotificationDeliveryRow.created_at.desc(), NotificationDeliveryRow.id.desc()).limit(limit)
        )
        total = select(func.count()).select_from(NotificationDeliveryRow).where(
            NotificationDeliveryRow.account_id == account_id,
        )
        with self._sessions() as session:
            return [_to_delivery(row) for row in session.scalars(rows)], int(session.scalar(total) or 0)

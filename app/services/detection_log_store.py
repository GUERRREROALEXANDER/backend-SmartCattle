"""What the AI saw per camera and minute, cattle split by posture."""

from datetime import UTC, datetime
from threading import Lock
from typing import Protocol

from sqlalchemy import select

from app.db import SessionFactory
from app.models import CameraRow, DetectionLogRow
from app.schemas.detection import DetectionLogEntry, DetectionLogReport
from app.services.camera_store import camera_row
from app.services.occupancy_store import minute_of

Entries = list[DetectionLogEntry]

# Copied verbatim from the report onto the row and the entry.
LOGGED_FIELDS = (
    "cattle_lying", "cattle_standing", "cattle_walking", "cattle_unknown", "people", "avg_confidence",
)


class DetectionLogStore(Protocol):
    def record(self, camera_id: str, report: DetectionLogReport) -> None:
        """Keeps the report as its minute's entry, unless a later one of that minute is already stored.

        The whole entry is replaced and never added to: summing a minute's
        reports would count the same animal once per report.
        """
        ...

    def entries(self, camera_id: str | None, start: datetime, end: datetime) -> Entries:
        """The entries in [start, end), by camera and then by minute."""
        ...


def _aware(value: datetime) -> datetime:
    # SQLite drops the timezone; PostgreSQL returns timestamptz in the session time zone.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class InMemoryDetectionLogStore:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, datetime], tuple[datetime, DetectionLogEntry]] = {}
        self._lock = Lock()

    def record(self, camera_id: str, report: DetectionLogReport) -> None:
        minute = minute_of(report.observed_at)
        with self._lock:
            stored = self._entries.get((camera_id, minute))
            if stored is not None and stored[0] > report.observed_at:
                return
            entry = DetectionLogEntry(
                camera_id=camera_id, minute=minute, **report.model_dump(include=set(LOGGED_FIELDS)),
            )
            self._entries[(camera_id, minute)] = (report.observed_at, entry)

    def entries(self, camera_id: str | None, start: datetime, end: datetime) -> Entries:
        with self._lock:
            return [
                entry for (camera, minute), (_, entry) in sorted(self._entries.items(), key=lambda item: item[0])
                if (camera_id is None or camera == camera_id) and start <= minute < end
            ]


class SqlDetectionLogStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    def record(self, camera_id: str, report: DetectionLogReport) -> None:
        minute = minute_of(report.observed_at)
        with self._sessions.begin() as session:
            # The camera's row lock serialises its reports, so two of one minute
            # cannot both insert and the later-wins comparison sees the other's commit.
            camera = camera_row(session, camera_id, lock=True)
            row = session.get(DetectionLogRow, (camera.id, minute))
            if row is not None and _aware(row.observed_at) > report.observed_at:
                return
            if row is None:
                row = DetectionLogRow(camera_id=camera.id, minute=minute)
                session.add(row)
            for field in LOGGED_FIELDS:
                setattr(row, field, getattr(report, field))
            row.observed_at = report.observed_at

    def entries(self, camera_id: str | None, start: datetime, end: datetime) -> Entries:
        query = (
            select(DetectionLogRow, CameraRow.name)
            .join(CameraRow, DetectionLogRow.camera_id == CameraRow.id)
            .where(DetectionLogRow.minute >= start, DetectionLogRow.minute < end)
        )
        if camera_id is not None:
            query = query.where(CameraRow.name == camera_id)
        with self._sessions() as session:
            items = [
                DetectionLogEntry(
                    camera_id=name, minute=_aware(row.minute),
                    **{field: getattr(row, field) for field in LOGGED_FIELDS},
                )
                for row, name in session.execute(query)
            ]
        # Sorted here rather than in SQL: SQLite and PostgreSQL collate text
        # differently, and both stores must return the same order.
        return sorted(items, key=lambda entry: (entry.camera_id, entry.minute))

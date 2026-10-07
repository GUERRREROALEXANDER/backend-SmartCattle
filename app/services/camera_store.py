from datetime import datetime, timezone
from threading import Lock
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import CameraRow
from app.schemas.camera import CameraRecord, CameraStatusReport, apply_report


class CameraStore(Protocol):
    def report(self, camera_id: str, report: CameraStatusReport) -> CameraRecord: ...

    def list(self) -> list[CameraRecord]: ...


class InMemoryCameraStore:
    def __init__(self) -> None:
        self._cameras: dict[str, CameraRecord] = {}
        self._lock = Lock()

    def report(self, camera_id: str, report: CameraStatusReport) -> CameraRecord:
        with self._lock:
            record = apply_report(self._cameras.get(camera_id), camera_id, report, datetime.now(timezone.utc))
            self._cameras[camera_id] = record
            return record

    def list(self) -> list[CameraRecord]:
        with self._lock:
            return [self._cameras[key] for key in sorted(self._cameras)]


def _to_record(row: CameraRow) -> CameraRecord:
    return CameraRecord(
        id=row.id, reported_status=row.reported_status,
        last_report_at=_aware(row.last_report_at), last_online_at=_aware(row.last_online_at),
        last_error=row.last_error, frame_width=row.frame_width, frame_height=row.frame_height, fps=row.fps,
    )


def _aware(value: datetime | None) -> datetime | None:
    # SQLite drops the timezone; PostgreSQL timestamptz keeps it.
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class SqlCameraStore:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._sessions = session_factory

    def report(self, camera_id: str, report: CameraStatusReport) -> CameraRecord:
        now = datetime.now(timezone.utc)
        with self._sessions.begin() as session:
            row = session.get(CameraRow, camera_id, with_for_update=True)
            record = apply_report(_to_record(row) if row else None, camera_id, report, now)
            if row is None:
                row = CameraRow(id=camera_id, created_at=now)
                session.add(row)
            row.reported_status = record.reported_status.value
            for field in ("last_report_at", "last_online_at", "last_error", "frame_width", "frame_height", "fps"):
                setattr(row, field, getattr(record, field))
            return record

    def list(self) -> list[CameraRecord]:
        with self._sessions() as session:
            return [_to_record(row) for row in session.scalars(select(CameraRow).order_by(CameraRow.id))]

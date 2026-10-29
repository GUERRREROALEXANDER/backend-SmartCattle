from datetime import UTC, datetime
from threading import Lock
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

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
            record = apply_report(self._cameras.get(camera_id), camera_id, report, datetime.now(UTC))
            self._cameras[camera_id] = record
            return record

    def list(self) -> list[CameraRecord]:
        with self._lock:
            return [self._cameras[key] for key in sorted(self._cameras)]


def camera_row(session: Session, name: str, *, lock: bool = False) -> CameraRow:
    """Returns the camera with this API id, creating it if needed; safe when two requests race."""
    insert = (pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert)(CameraRow)
    session.execute(insert.values(name=name).on_conflict_do_nothing(index_elements=["name"]))
    query = select(CameraRow).where(CameraRow.name == name)
    return session.scalars(query.with_for_update() if lock else query).one()


def _to_record(row: CameraRow) -> CameraRecord:
    return CameraRecord(
        id=row.name, reported_status=row.reported_status,
        last_report_at=_aware(row.last_report_at), last_online_at=_aware(row.last_online_at),
        last_error=row.last_error, frame_width=row.frame_width, frame_height=row.frame_height, fps=row.fps,
        stream_url=row.stream_url,
    )


def _aware(value: datetime | None) -> datetime | None:
    # SQLite drops the timezone; PostgreSQL returns timestamptz in the session time zone.
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SqlCameraStore:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._sessions = session_factory

    def report(self, camera_id: str, report: CameraStatusReport) -> CameraRecord:
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            row = camera_row(session, camera_id, lock=True)
            previous = _to_record(row) if row.last_report_at is not None else None
            record = apply_report(previous, camera_id, report, now)
            row.reported_status = record.reported_status.value
            for field in ("last_report_at", "last_online_at", "last_error", "frame_width", "frame_height", "fps", "stream_url"):
                setattr(row, field, getattr(record, field))
            return record

    def list(self) -> list[CameraRecord]:
        # Cameras the AI service never reported (seed rows, cameras only named by events) have no status.
        query = select(CameraRow).where(CameraRow.last_report_at.is_not(None)).order_by(CameraRow.name)
        with self._sessions() as session:
            return [_to_record(row) for row in session.scalars(query)]

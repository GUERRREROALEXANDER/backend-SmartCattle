from datetime import UTC, datetime
from threading import Lock
from typing import Protocol

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db import SessionFactory
from app.models import CameraRow
from app.schemas.camera import CameraRecord, CameraStatus, CameraStatusReport, apply_report

# Copied verbatim from the record onto the row; reported_status needs .value.
REPORTED_FIELDS = (
    "last_report_at", "last_online_at", "last_error",
    "frame_width", "frame_height", "fps", "stream_url",
)


class CameraStore(Protocol):
    def report(self, camera_id: str, report: CameraStatusReport, received_at: datetime) -> CameraRecord: ...

    def list(self) -> list[CameraRecord]: ...


class InMemoryCameraStore:
    def __init__(self) -> None:
        self._cameras: dict[str, CameraRecord] = {}
        self._lock = Lock()

    def report(self, camera_id: str, report: CameraStatusReport, received_at: datetime) -> CameraRecord:
        with self._lock:
            record = apply_report(self._cameras.get(camera_id), camera_id, report, received_at)
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


def _reported_rows() -> Select[CameraRow]:
    """Cameras the AI service has actually reported on.

    Rows exist for cameras nobody reported yet: the seed row in
    database/schema.sql, and the ones camera_row() creates the first time an
    event names a camera. Both status columns stay NULL there, and a camera
    with no status cannot be described by CameraRecord, so it is left out
    rather than reported as a guess.
    """
    return select(CameraRow).where(
        CameraRow.last_report_at.is_not(None),
        CameraRow.reported_status.is_not(None),
    )


def _to_record(row: CameraRow) -> CameraRecord:
    assert row.reported_status is not None  # noqa: S101 - guaranteed by _reported_rows
    assert row.last_report_at is not None  # noqa: S101
    return CameraRecord(
        id=row.name, reported_status=CameraStatus(row.reported_status),
        last_report_at=_aware_required(row.last_report_at), last_online_at=_aware(row.last_online_at),
        last_error=row.last_error, frame_width=row.frame_width, frame_height=row.frame_height, fps=row.fps,
        stream_url=row.stream_url,
    )


def _aware_required(value: datetime) -> datetime:
    # SQLite drops the timezone; PostgreSQL returns timestamptz in the session time zone.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _aware(value: datetime | None) -> datetime | None:
    return None if value is None else _aware_required(value)


class SqlCameraStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    def report(self, camera_id: str, report: CameraStatusReport, received_at: datetime) -> CameraRecord:
        with self._sessions.begin() as session:
            row = camera_row(session, camera_id, lock=True)
            # A row with no status yet has nothing to carry over.
            reported_before = row.last_report_at is not None and row.reported_status is not None
            previous = _to_record(row) if reported_before else None
            record = apply_report(previous, camera_id, report, received_at)
            row.reported_status = record.reported_status.value
            for field in REPORTED_FIELDS:
                setattr(row, field, getattr(record, field))
            return record

    def list(self) -> list[CameraRecord]:
        query = _reported_rows().order_by(CameraRow.name)
        with self._sessions() as session:
            return [_to_record(row) for row in session.scalars(query)]

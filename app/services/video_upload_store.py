"""Uploaded videos and the processing queue built on them.

The queue is the table itself: a worker claims the oldest queued upload, and
keeps it by reporting progress. An upload whose worker went quiet for longer
than the lease is handed to the next worker, up to MAX_ATTEMPTS times, so a
crashed farm PC delays a video instead of losing it.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import func, or_, select

from app.db import SessionFactory
from app.models import VideoUploadRow
from app.schemas.video_upload import StoredVideoUpload, UploadStatus

#: Without progress for this long, a worker is presumed dead. Progress is sent
#: every few seconds, so ten minutes is generous even for a stalled download.
JOB_LEASE = timedelta(minutes=10)
#: A video that has killed three workers is probably what is killing them.
MAX_ATTEMPTS = 3
#: Candidate rows one SQL claim looks at.
CLAIM_SCAN = 20


class UploadStateError(Exception):
    """The upload is not in a state that allows the requested change."""

    def __init__(self, current: UploadStatus) -> None:
        super().__init__(str(current))
        self.current = current


@dataclass(frozen=True)
class UploadPage:
    items: list[StoredVideoUpload]
    total: int


class VideoUploadStore(Protocol):
    def create(self, upload: StoredVideoUpload) -> StoredVideoUpload: ...

    def get(self, upload_id: UUID) -> StoredVideoUpload | None: ...

    def page(
        self, status: UploadStatus | None, limit: int, offset: int, uploaded_by: frozenset[int] | None = None,
    ) -> UploadPage: ...

    def change(
        self, upload_id: UUID, allowed_from: frozenset[UploadStatus], changes: dict[str, Any],
    ) -> StoredVideoUpload | None:
        """Applies `changes` if the upload is in one of `allowed_from`, else UploadStateError."""
        ...

    def claim(self, now: datetime) -> StoredVideoUpload | None: ...


def _claimable(upload: StoredVideoUpload, now: datetime) -> bool:
    if upload.status is UploadStatus.QUEUED:
        return True
    return (
        upload.status is UploadStatus.PROCESSING
        and upload.heartbeat_at is not None
        and now - upload.heartbeat_at > JOB_LEASE
    )


def _claim_changes(upload: StoredVideoUpload, now: datetime) -> dict[str, Any]:
    """What claiming does to an upload: a failure if it exhausted its attempts, a lease otherwise."""
    if upload.attempts >= MAX_ATTEMPTS:
        return {
            "status": UploadStatus.FAILED, "finished_at": now,
            "error": f"Processing stopped responding {upload.attempts} times",
        }
    return {
        "status": UploadStatus.PROCESSING, "attempts": upload.attempts + 1, "heartbeat_at": now,
        "started_at": upload.started_at or now,
    }


def _oldest_first(upload: StoredVideoUpload) -> tuple[datetime, str]:
    return upload.created_at, str(upload.id)


class InMemoryVideoUploadStore:
    def __init__(self) -> None:
        self._uploads: dict[UUID, StoredVideoUpload] = {}
        self._lock = Lock()

    def create(self, upload: StoredVideoUpload) -> StoredVideoUpload:
        with self._lock:
            self._uploads[upload.id] = upload
            return upload

    def get(self, upload_id: UUID) -> StoredVideoUpload | None:
        with self._lock:
            return self._uploads.get(upload_id)

    def page(
        self, status: UploadStatus | None, limit: int, offset: int, uploaded_by: frozenset[int] | None = None,
    ) -> UploadPage:
        with self._lock:
            matching = sorted(
                (
                    upload for upload in self._uploads.values()
                    if (status is None or upload.status is status)
                    and (uploaded_by is None or upload.uploaded_by in uploaded_by)
                ),
                key=_oldest_first, reverse=True,
            )
        return UploadPage(items=matching[offset:offset + limit], total=len(matching))

    def change(
        self, upload_id: UUID, allowed_from: frozenset[UploadStatus], changes: dict[str, Any],
    ) -> StoredVideoUpload | None:
        with self._lock:
            current = self._uploads.get(upload_id)
            if current is None:
                return None
            if current.status not in allowed_from:
                raise UploadStateError(current.status)
            updated = current.model_copy(update=changes)
            self._uploads[upload_id] = updated
            return updated

    def claim(self, now: datetime) -> StoredVideoUpload | None:
        with self._lock:
            for upload in sorted(self._uploads.values(), key=_oldest_first):
                if not _claimable(upload, now):
                    continue
                updated = upload.model_copy(update=_claim_changes(upload, now))
                self._uploads[upload.id] = updated
                if updated.status is UploadStatus.PROCESSING:
                    return updated
            return None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


#: Columns stored as-is; enum and datetime columns are converted on the way.
_COLUMNS = (
    "id", "file_name", "content_type", "size_bytes", "lot_id", "uploaded_by", "frames_total",
    "frames_processed", "fps", "error", "analysis_id", "storage_key", "attempts",
)
_TIMES = ("created_at", "uploaded_at", "started_at", "finished_at", "heartbeat_at")


def _to_upload(row: VideoUploadRow) -> StoredVideoUpload:
    values: dict[str, Any] = {name: getattr(row, name) for name in _COLUMNS}
    values |= {name: _aware(getattr(row, name)) for name in _TIMES}
    return StoredVideoUpload(**values, status=UploadStatus(row.status))


def _apply(row: VideoUploadRow, changes: Iterable[tuple[str, Any]]) -> None:
    for name, value in changes:
        setattr(row, name, value.value if isinstance(value, UploadStatus) else value)


class SqlVideoUploadStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    def create(self, upload: StoredVideoUpload) -> StoredVideoUpload:
        row = VideoUploadRow()
        _apply(row, upload.model_dump().items())
        with self._sessions.begin() as session:
            session.add(row)
        return upload

    def get(self, upload_id: UUID) -> StoredVideoUpload | None:
        with self._sessions() as session:
            row = session.get(VideoUploadRow, upload_id)
            return None if row is None else _to_upload(row)

    def page(
        self, status: UploadStatus | None, limit: int, offset: int, uploaded_by: frozenset[int] | None = None,
    ) -> UploadPage:
        conditions = [] if status is None else [VideoUploadRow.status == status.value]
        if uploaded_by is not None:
            conditions.append(VideoUploadRow.uploaded_by.in_(uploaded_by))
        rows = (
            select(VideoUploadRow).where(*conditions)
            .order_by(VideoUploadRow.created_at.desc(), VideoUploadRow.id.desc()).limit(limit).offset(offset)
        )
        total = select(func.count()).select_from(VideoUploadRow).where(*conditions)
        with self._sessions() as session:
            return UploadPage(
                items=[_to_upload(row) for row in session.scalars(rows)], total=int(session.scalar(total) or 0),
            )

    def change(
        self, upload_id: UUID, allowed_from: frozenset[UploadStatus], changes: dict[str, Any],
    ) -> StoredVideoUpload | None:
        with self._sessions.begin() as session:
            row = session.get(VideoUploadRow, upload_id, with_for_update=True)
            if row is None:
                return None
            if UploadStatus(row.status) not in allowed_from:
                raise UploadStateError(UploadStatus(row.status))
            _apply(row, changes.items())
            session.flush()
            return _to_upload(row)

    def claim(self, now: datetime) -> StoredVideoUpload | None:
        candidates = (
            select(VideoUploadRow)
            .where(or_(
                VideoUploadRow.status == UploadStatus.QUEUED.value,
                (VideoUploadRow.status == UploadStatus.PROCESSING.value)
                & (VideoUploadRow.heartbeat_at < now - JOB_LEASE),
            ))
            .order_by(VideoUploadRow.created_at, VideoUploadRow.id)
            # SKIP LOCKED lets two workers claim at once without both taking
            # the same video or one waiting on the other's lock. The limit
            # bounds one claim's work; exhausted rows are failed on the way,
            # so the next claim reaches whatever lies past them.
            .limit(CLAIM_SCAN).with_for_update(skip_locked=True)
        )
        with self._sessions.begin() as session:
            for row in session.scalars(candidates):
                upload = _to_upload(row)
                if not _claimable(upload, now):
                    continue
                changes = _claim_changes(upload, now)
                _apply(row, changes.items())
                if changes["status"] is UploadStatus.PROCESSING:
                    session.flush()
                    return _to_upload(row)
        return None

from datetime import datetime
from enum import StrEnum
from pathlib import PurePath
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.occupancy import MAX_DETECTIONS, Detection

#: 1 GiB. The ceiling the farm agreed on: a long pasture clip at 1080p.
MAX_VIDEO_BYTES = 1024 ** 3

#: Accepted containers and the extensions that go with each. Both are checked,
#: because a mismatch is usually a renamed file the AI service cannot decode.
VIDEO_TYPES: dict[str, frozenset[str]] = {
    "video/mp4": frozenset({".mp4", ".m4v"}),
    "video/quicktime": frozenset({".mov"}),
    "video/x-matroska": frozenset({".mkv"}),
    "video/webm": frozenset({".webm"}),
}


class UploadStatus(StrEnum):
    PENDING_UPLOAD = "pending_upload"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class VideoUploadCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "file_name": "north-paddock-dawn.mp4", "size_bytes": 734003200,
            "content_type": "video/mp4", "lot_id": 1,
        }]},
    )

    file_name: str = Field(min_length=1, max_length=200)
    # Bounded in the route rather than here, so too large is 413 and not 422.
    size_bytes: int = Field(gt=0)
    # A plain string checked in the route, so an unsupported type is 415.
    content_type: str = Field(max_length=100)
    lot_id: int | None = Field(default=None, ge=1, description="Lot the footage was recorded in")

    @field_validator("file_name", mode="before")
    @classmethod
    def strip_file_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("file_name")
    @classmethod
    def plain_file_name(cls, value: str) -> str:
        """A bare name: it is shown back to users and must not smuggle a path or control bytes."""
        if "/" in value or "\\" in value or any(ord(character) < 32 for character in value):
            raise ValueError("file_name must be a plain file name without folders or control characters")
        return value

    @field_validator("content_type")
    @classmethod
    def lowercase_content_type(cls, value: str) -> str:
        return value.strip().lower()

    @property
    def extension(self) -> str:
        return PurePath(self.file_name).suffix.lower()


class VideoUpload(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "0d6f5a1e-7f2b-4b8e-9a51-2a3c4d5e6f70", "file_name": "north-paddock-dawn.mp4",
        "content_type": "video/mp4", "size_bytes": 734003200, "lot_id": 1, "uploaded_by": 2,
        "status": "processing", "frames_total": 54000, "frames_processed": 12000, "fps": 24.5,
        "error": None, "analysis_id": None, "created_at": "2026-10-31T15:00:00Z",
        "uploaded_at": "2026-10-31T15:06:10Z", "started_at": "2026-10-31T15:06:12Z", "finished_at": None,
    }]})

    id: UUID
    file_name: str
    content_type: str
    size_bytes: int
    lot_id: int | None
    uploaded_by: int
    status: UploadStatus
    frames_total: int | None = None
    frames_processed: int = 0
    fps: float | None = None
    error: str | None = None
    analysis_id: UUID | None = None
    created_at: datetime
    uploaded_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class StoredVideoUpload(VideoUpload):
    """An upload plus the bookkeeping that never leaves the backend."""

    storage_key: str
    heartbeat_at: datetime | None = None
    attempts: int = 0

    def public(self) -> VideoUpload:
        return VideoUpload(**self.model_dump(exclude={"storage_key", "heartbeat_at", "attempts"}))


class UploadTicket(BaseModel):
    """Where and how the browser sends the file."""

    upload: VideoUpload
    upload_url: str
    upload_method: str
    upload_headers: dict[str, str] = Field(description="Send exactly these headers; they are signed")
    expires_at: datetime


class VideoUploadList(BaseModel):
    items: list[VideoUpload]
    total: int


class VideoJob(BaseModel):
    """What a worker needs to process one upload."""

    job_id: UUID
    download_url: str
    file_name: str
    content_type: str
    size_bytes: int
    lot_id: int | None
    camera_id: str | None = Field(description="Camera of the lot, whose safe zone applies to this footage")
    attempt: int


class JobProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frames_processed: int = Field(ge=0)
    frames_total: int | None = Field(default=None, ge=1)
    fps: float | None = Field(default=None, gt=0, le=1000, allow_inf_nan=False)
    video_time_sec: float | None = Field(
        default=None, ge=0, allow_inf_nan=False,
        description="Playback position these detections belong to, so the dashboard can sync them to the video",
    )
    detections: list[Detection] = Field(default_factory=list, max_length=MAX_DETECTIONS)

    @model_validator(mode="after")
    def processed_within_total(self) -> Self:
        if self.frames_total is not None and self.frames_processed > self.frames_total:
            raise ValueError("frames_processed cannot exceed frames_total")
        return self


class JobFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: str = Field(min_length=1, max_length=300)

    @field_validator("error", mode="before")
    @classmethod
    def clean_error(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            # Same rule as camera errors: a URL may carry credentials, and this text is shown to users.
            if "://" in value:
                raise ValueError("error must not contain URLs")
        return value


class VideoProgress(BaseModel):
    """The `video.progress` live message."""

    upload_id: UUID
    frames_processed: int
    frames_total: int | None
    fps: float | None
    video_time_sec: float | None
    detections: list[Detection]

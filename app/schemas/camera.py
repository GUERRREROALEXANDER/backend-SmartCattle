from datetime import datetime, timedelta
from enum import Enum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class CameraStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"


class CameraStatusReport(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "status": "online", "error": None, "frame_width": 1280, "frame_height": 720,
            "fps": 14.8, "observed_at": "2026-10-06T15:30:00Z",
        }]},
    )

    status: CameraStatus = Field(description="Camera state observed by the AI service")
    error: str | None = Field(default=None, max_length=300, description="Short error message; only with status error")
    frame_width: int | None = Field(default=None, ge=1, le=10000)
    frame_height: int | None = Field(default=None, ge=1, le=10000)
    fps: float | None = Field(default=None, gt=0, le=240, allow_inf_nan=False)
    observed_at: AwareDatetime = Field(description="When the AI service observed this state")

    @field_validator("error", mode="before")
    @classmethod
    def clean_error(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip() or None
            # Stream URLs may carry credentials; they must never be stored or shown.
            if value is not None and "://" in value:
                raise ValueError("error must not contain URLs")
        return value

    @model_validator(mode="after")
    def error_only_with_error_status(self) -> "CameraStatusReport":
        if self.error is not None and self.status is not CameraStatus.ERROR:
            raise ValueError("error is only allowed when status is error")
        return self


class CameraRecord(BaseModel):
    """Stored camera state as last reported by the AI service."""

    id: str
    reported_status: CameraStatus
    last_report_at: datetime
    last_online_at: datetime | None = None
    last_error: str | None = None
    frame_width: int | None = None
    frame_height: int | None = None
    fps: float | None = None


class Camera(CameraRecord):
    status: CameraStatus = Field(description="Effective status: offline when the AI service stopped reporting")


class CameraList(BaseModel):
    items: list[Camera]
    total: int


def effective_status(record: CameraRecord, now: datetime, offline_after_seconds: int) -> CameraStatus:
    """A camera is only online while the AI service keeps confirming it."""
    if now - record.last_report_at > timedelta(seconds=offline_after_seconds):
        return CameraStatus.OFFLINE
    return record.reported_status


def apply_report(record: CameraRecord | None, camera_id: str, report: CameraStatusReport,
                 received_at: datetime) -> CameraRecord:
    online = report.status is CameraStatus.ONLINE
    return CameraRecord(
        id=camera_id,
        reported_status=report.status,
        last_report_at=received_at,
        last_online_at=received_at if online else (record.last_online_at if record else None),
        last_error=report.error if report.status is CameraStatus.ERROR else None,
        frame_width=report.frame_width, frame_height=report.frame_height, fps=report.fps,
    )

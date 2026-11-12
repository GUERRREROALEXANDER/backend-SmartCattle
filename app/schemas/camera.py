import re
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class CameraStatus(StrEnum):
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
    stream_url: HttpUrl | None = Field(
        default=None,
        description="Public base URL of the AI service video (serves /video.mjpg and /status); only kept while online",
    )

    @field_validator("stream_url")
    @classmethod
    def safe_stream_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        # The URL is public through GET /api/cameras, so it must never carry user:password.
        if value is not None and (value.username or value.password):
            raise ValueError("stream_url must not contain credentials")
        if value is not None and len(str(value)) > 300:
            raise ValueError("stream_url must be at most 300 characters")
        return value

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


class CameraSourceReport(BaseModel):
    """Where one camera is reached, sent by the AI service when the camera is added, restored or removed."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"source": "rtsp://192.168.1.50:554/live", "active": True}]},
    )

    source: str = Field(min_length=1, max_length=500, description="Webcam index or stream URL, without credentials")
    active: bool = Field(default=True, description="False when the camera was removed")

    @field_validator("source")
    @classmethod
    def no_credentials(cls, value: str) -> str:
        # Passwords stay on the PC that opens the camera; the database never holds one.
        authority = urlsplit(value).netloc
        if "@" in authority:
            raise ValueError("source must not contain credentials")
        return value


class SavedCameraCreate(BaseModel):
    """A camera someone adds to their account. Saving opens nothing: that takes connecting it."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"name": "Potrero norte", "source": "rtsp://192.168.1.50:554/live"}]},
    )

    name: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=500, description="IP camera link, phone app address or webcam number")

    @field_validator("name", "source", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("source")
    @classmethod
    def no_credentials(cls, value: str) -> str:
        if "@" in urlsplit(value).netloc:
            raise ValueError("source must not contain credentials")
        return value


class CameraPairing(BaseModel):
    code: str
    expires_in: int = Field(description="Seconds the code stays valid")


class PairedCameraCreate(SavedCameraCreate):
    """A camera sent from a phone that scanned the QR: the code says whose it is."""

    code: str = Field(min_length=1, max_length=100)


class SavedCamera(BaseModel):
    id: str
    name: str
    source: str
    account_id: int | None = None
    address: str | None = None


class SavedCameraView(BaseModel):
    id: str
    name: str
    source: str
    status: CameraStatus = Field(description="online only while the AI service is sending it")
    address: str | None = Field(default=None, description="LAN address its owner typed, shown in the dashboard")
    cloud: bool = Field(default=False, description="Opened through the Imou cloud: watchable from anywhere")


IMOU_PREFIX = "imou:"
IMOU_SERIAL = re.compile(r"^[A-Z0-9]{8,32}$")


def imou_source(serial: str) -> str:
    return f"{IMOU_PREFIX}{serial}"


def imou_serial_of(source: str) -> str | None:
    """The serial number of an Imou cloud camera, or None for any other kind of source."""
    if not source.startswith(IMOU_PREFIX):
        return None
    serial = source.removeprefix(IMOU_PREFIX)
    return serial if IMOU_SERIAL.fullmatch(serial) else None


class CameraConnect(BaseModel):
    """What the Cameras page asks: the camera's address, or the Imou camera it recommended."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"address": "192.168.100.30"}, {"serial": "04797CEPSF59DA6",
                                                                          "address": "192.168.100.30"}]},
    )

    address: str | None = Field(default=None, max_length=255, description="IP address, host name or Imou serial")
    serial: str | None = Field(default=None, description="Serial number of a recommended Imou camera")
    name: str | None = Field(default=None, max_length=80)

    @field_validator("address", "serial", "name", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("address")
    @classmethod
    def plain_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # People paste links; only the host matters, and a password must never reach the server's logs.
        if "://" in value:
            parts = urlsplit(value)
            if parts.username or parts.password:
                raise ValueError("address must not contain a user or password")
            value = parts.hostname or ""
        else:
            value = value.split("/", 1)[0]
            if "@" in value:
                raise ValueError("address must not contain a user or password")
            if value.count(":") == 1:
                value = value.split(":", 1)[0]
        if not re.fullmatch(r"[A-Za-z0-9.\-]{1,253}", value):
            raise ValueError("address must be an IP address or a host name")
        return value

    @field_validator("serial")
    @classmethod
    def serial_format(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.upper()
            if not IMOU_SERIAL.fullmatch(value):
                raise ValueError("serial must be the camera's serial number, letters and digits")
        return value

    @model_validator(mode="after")
    def something_to_connect(self) -> "CameraConnect":
        if self.address is None and self.serial is None:
            raise ValueError("Give the camera's address or serial number")
        return self


class CameraSuggestion(BaseModel):
    serial: str
    name: str
    model: str | None = None
    online: bool = Field(description="Whether the Imou cloud sees it connected right now")
    camera_id: str | None = Field(default=None, description="Its saved camera in this account, if already connected")


class CameraSuggestionList(BaseModel):
    available: bool = Field(description="False when this server has no Imou account or could not reach it")
    message: str | None = None
    items: list[CameraSuggestion]


class CameraConnectResult(BaseModel):
    outcome: Literal["connected", "choose", "unreachable"] = Field(
        description="connected: saved and opening from the cloud; choose: pick one of the suggestions; "
                    "unreachable: the cloud cannot open this address",
    )
    message: str
    camera: SavedCameraView | None = None
    suggestions: list[CameraSuggestion] = Field(default_factory=list)
    recommended: str | None = Field(default=None, description="Serial of the suggestion to connect, when obvious")


class CloudCamera(BaseModel):
    camera_id: str
    source: str


class CloudCameraList(BaseModel):
    items: list[CloudCamera]


class SavedCameraList(BaseModel):
    items: list[SavedCameraView]
    total: int


class CameraLiveAccess(BaseModel):
    camera_id: str
    hls_url: str = Field(description="HLS playlist on the farm's media server")
    token: str = Field(description="Send as Authorization: Bearer on the player's requests")
    expires_in: int = Field(description="Seconds the token can open the video; video already open keeps playing")


class CameraRegistration(BaseModel):
    source: str
    active: bool


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
    stream_url: str | None = None


class Camera(CameraRecord):
    status: CameraStatus = Field(description="Effective status: offline when the AI service stopped reporting")
    display_name: str | None = Field(default=None, description="The name its owner gave it")


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
        # A camera that is not streaming has no video to link to.
        stream_url=str(report.stream_url).rstrip("/") if online and report.stream_url else None,
    )

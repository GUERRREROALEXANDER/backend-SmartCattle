from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

#: Detections per report. A frame with more boxes than this is a detector
#: misfiring, and the message would be too large to push to every dashboard.
MAX_DETECTIONS = 200
#: Sanity ceiling per camera, so a counter bug in the AI is a 422 and not a
#: dashboard claiming a herd of millions.
MAX_HEAD_COUNT = 10_000

Normalised = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
HeadCount = Annotated[int, Field(ge=0, le=MAX_HEAD_COUNT)]


class Detection(BaseModel):
    """One box in one frame, as the dashboard and the 3D view draw it."""

    model_config = ConfigDict(extra="forbid")

    track_id: int | None = Field(default=None, ge=0, description="Tracker id, stable across frames")
    label: str = Field(min_length=1, max_length=32, examples=["cow"])
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    bbox: list[Normalised] = Field(
        min_length=4, max_length=4,
        description="[x1, y1, x2, y2] normalised to the frame, like zones, so the view can scale it",
    )
    inside_zone: bool
    posture: Literal["standing", "walking", "lying"] | None = Field(
        default=None, description="What the AI estimates the animal is doing; absent when it cannot tell",
    )

    @field_validator("label", mode="before")
    @classmethod
    def strip_label(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("bbox")
    @classmethod
    def ordered_corners(cls, value: list[float]) -> list[float]:
        if not (value[0] < value[2] and value[1] < value[3]):
            raise ValueError("bbox must satisfy x1 < x2 and y1 < y2")
        return value


class OccupancyReport(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "cattle_inside": 12, "cattle_outside": 1, "observed_at": "2026-10-31T15:30:00Z",
            "detections": [{
                "track_id": 7, "label": "cow", "confidence": 0.93,
                "bbox": [0.71, 0.40, 0.82, 0.58], "inside_zone": False,
            }],
        }]},
    )

    cattle_inside: HeadCount
    cattle_outside: HeadCount
    observed_at: AwareDatetime = Field(description="When the AI service counted, by its own clock")
    detections: list[Detection] = Field(
        default_factory=list, max_length=MAX_DETECTIONS,
        description="Boxes for live overlays. Pushed to dashboards, never stored.",
    )


class Occupancy(BaseModel):
    """The latest head count of one camera."""

    camera_id: str
    cattle_inside: int
    cattle_outside: int
    observed_at: datetime
    received_at: datetime


class DetectionFrame(BaseModel):
    """What the `detections` live message carries."""

    camera_id: str
    observed_at: datetime
    detections: list[Detection]


class SystemStatus(StrEnum):
    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    NO_DATA = "no_data"


class LiveMetrics(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "cameras_total": 3, "cameras_online": 2, "stale_cameras": 1,
        "cattle_inside": 37, "cattle_outside": 2, "cattle_total": 39,
        "open_alerts": 2, "events_last_hour": 5, "system_status": "degraded",
        "generated_at": "2026-10-31T15:30:05Z",
    }]})

    cameras_total: int = Field(description="Cameras the AI service has reported on")
    cameras_online: int = Field(description="Cameras whose effective status is online")
    stale_cameras: int = Field(
        description="Head counts older than CAMERA_OFFLINE_AFTER_SECONDS, left out of the totals",
    )
    cattle_inside: int
    cattle_outside: int
    cattle_total: int
    open_alerts: int
    events_last_hour: int
    system_status: SystemStatus
    generated_at: datetime

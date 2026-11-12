from datetime import datetime
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.schemas.occupancy import HeadCount


class DetectionLogReport(BaseModel):
    """What YOLO saw on one camera, already counted by the AI service. Absent counts are zero."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "cattle_lying": 7, "cattle_standing": 4, "cattle_walking": 1, "cattle_unknown": 0, "people": 1,
            "avg_confidence": 0.87, "observed_at": "2026-11-03T15:30:00Z",
        }]},
    )

    cattle_lying: HeadCount = 0
    cattle_standing: HeadCount = 0
    cattle_walking: HeadCount = 0
    cattle_unknown: HeadCount = Field(default=0, description="Animals seen whose posture the AI could not tell")
    people: HeadCount = 0
    avg_confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None = None
    observed_at: AwareDatetime = Field(description="When the AI service counted, by its own clock")


class DetectionLogEntry(BaseModel):
    """One camera's minute: the last report observed in it."""

    camera_id: str
    minute: datetime
    cattle_lying: int
    cattle_standing: int
    cattle_walking: int
    cattle_unknown: int
    people: int
    avg_confidence: float | None


class DetectionLogHistory(BaseModel):
    items: list[DetectionLogEntry]
    total: int

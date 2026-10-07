from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class EventType(str, Enum):
    CATTLE_OUT_OF_ZONE = "cattle_out_of_zone"


class EventBase(BaseModel):
    event_type: EventType = Field(description="Type of detected event", examples=["cattle_out_of_zone"])
    camera_id: str = Field(min_length=1, max_length=64, description="Source camera identifier", examples=["camera-01"])
    detected_object: str = Field(min_length=1, max_length=64, description="Detected object class", examples=["cow"])
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False, description="Detection confidence from zero to one", examples=[0.95])
    timestamp: AwareDatetime = Field(description="Detection time with a timezone", examples=["2026-10-03T15:30:00Z"])
    bbox: list[Annotated[float, Field(ge=0, allow_inf_nan=False)]] | None = Field(
        default=None, min_length=4, max_length=4,
        description="Optional bounding box [x1, y1, x2, y2] in frame pixels", examples=[[120.0, 80.5, 340.0, 300.0]],
    )

    @field_validator("camera_id", "detected_object", mode="before")
    @classmethod
    def strip_whitespace(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("bbox")
    @classmethod
    def ordered_corners(cls, value: list[float] | None) -> list[float] | None:
        if value is not None and not (value[0] < value[2] and value[1] < value[3]):
            raise ValueError("bbox must satisfy x1 < x2 and y1 < y2")
        return value


class AIEventCreate(EventBase):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "event_type": "cattle_out_of_zone",
            "camera_id": "camera-01",
            "detected_object": "cow",
            "confidence": 0.95,
            "timestamp": "2026-10-03T15:30:00Z",
        }]},
    )


class Event(EventBase):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "event_type": "cattle_out_of_zone", "camera_id": "camera-01",
        "detected_object": "cow", "confidence": 0.95,
        "timestamp": "2026-10-03T15:30:00Z",
        "id": "123e4567-e89b-42d3-a456-426614174000",
        "received_at": "2026-10-03T15:30:01Z",
    }]})
    id: UUID = Field(description="Backend event identifier")
    received_at: AwareDatetime = Field(description="UTC receipt time assigned by the backend")


class EventList(BaseModel):
    items: list[Event]
    total: int

from enum import Enum
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

    @field_validator("camera_id", "detected_object", mode="before")
    @classmethod
    def strip_whitespace(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


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

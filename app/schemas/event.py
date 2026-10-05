from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class EventType(str, Enum):
    CATTLE_OUT_OF_ZONE = "cattle_out_of_zone"


class AIEventCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "ai_event_id": "0f8bc0f2-8e4d-4c6e-9a3b-5d7c1e2f4a60",
            "event_type": "cattle_out_of_zone",
            "camera_id": "camera-01",
            "detected_object": "cow",
            "confidence": 0.95,
            "timestamp": "2026-10-03T15:30:00Z",
        }]},
    )

    ai_event_id: UUID = Field(
        description=(
            "Identifier generated once by the AI service for a detection and reused on "
            "every retry of that same detection. Re-sending it returns the stored event "
            "instead of creating a second one."
        ),
        examples=["0f8bc0f2-8e4d-4c6e-9a3b-5d7c1e2f4a60"],
    )
    event_type: EventType = Field(description="Type of detected event", examples=["cattle_out_of_zone"])
    camera_id: str = Field(min_length=1, max_length=64, description="Source camera identifier", examples=["camera-01"])
    detected_object: str = Field(min_length=1, max_length=64, description="Detected object class", examples=["cow"])
    confidence: float = Field(ge=0, le=1, description="Detection confidence from zero to one", examples=[0.95])
    timestamp: AwareDatetime = Field(description="Detection time with a timezone", examples=["2026-10-03T15:30:00Z"])

    @field_validator("camera_id", "detected_object", mode="before")
    @classmethod
    def strip_whitespace(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("timestamp")
    @classmethod
    def normalize_to_utc(cls, value: datetime) -> datetime:
        # Every instant is stored and returned as UTC. MySQL DATETIME carries no
        # offset, so mixing incoming offsets would corrupt chronological order.
        return value.astimezone(timezone.utc)


class Event(AIEventCreate):
    id: UUID = Field(description="Backend event identifier")
    received_at: AwareDatetime = Field(description="UTC receipt time assigned by the backend")


# Page sizes for GET /api/events. The default keeps an unfiltered request small;
# the maximum keeps an unbounded table from producing an unbounded response.
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1000


class EventList(BaseModel):
    items: list[Event] = Field(description="One page of events, most recently received first")
    total: int = Field(description="Events stored in total, not the size of this page")
    limit: int = Field(description="Page size that was applied")
    offset: int = Field(description="Events skipped before this page")

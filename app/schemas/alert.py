from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.event import EventLevel, EventType


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


#: Where each status may go next. Resolving straight from open is allowed:
#: a false alarm seen and dismissed in one step needs no acknowledgement first.
TRANSITIONS: dict[AlertStatus, frozenset[AlertStatus]] = {
    AlertStatus.OPEN: frozenset({AlertStatus.ACKNOWLEDGED, AlertStatus.RESOLVED}),
    AlertStatus.ACKNOWLEDGED: frozenset({AlertStatus.RESOLVED}),
    AlertStatus.RESOLVED: frozenset(),
}


class Alert(BaseModel):
    """A security incident someone on the farm has to look at."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "6f1c2a7e-6d3b-4c55-9a0e-1b2c3d4e5f60",
        "event_id": "123e4567-e89b-42d3-a456-426614174000",
        "camera_id": "camera-01", "alert_type": "cattle_out_of_zone", "severity": "high",
        "status": "open", "message": "Cattle left the safe zone on camera-01",
        "created_at": "2026-10-31T15:30:01Z",
        "acknowledged_at": None, "acknowledged_by": None, "resolved_at": None, "resolved_by": None,
    }]})

    id: UUID
    event_id: UUID | None
    camera_id: str | None
    alert_type: EventType
    severity: EventLevel
    status: AlertStatus
    message: str
    created_at: datetime
    acknowledged_at: datetime | None = None
    acknowledged_by: int | None = None
    resolved_at: datetime | None = None
    resolved_by: int | None = None


class AlertList(BaseModel):
    items: list[Alert]
    total: int

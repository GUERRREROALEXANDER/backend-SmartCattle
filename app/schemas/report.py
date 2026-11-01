from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.camera import CameraStatus


class OccupancySample(BaseModel):
    """The head count a camera reported in one minute: the last one observed in it."""

    camera_id: str
    minute: datetime
    cattle_inside: int
    cattle_outside: int


class OccupancyHistory(BaseModel):
    items: list[OccupancySample]
    total: int


class CameraStatusChange(BaseModel):
    """A change in the status the AI reported for a camera.

    Only reported statuses: a camera that goes silent shows offline on the
    dashboard without anyone reporting it, and that silence is not an event
    to record here.
    """

    camera_id: str
    status: CameraStatus
    error: str | None
    changed_at: datetime


class CameraStatusHistory(BaseModel):
    items: list[CameraStatusChange]
    total: int


class _Window(BaseModel):
    start: datetime
    end: datetime
    time_zone: str = Field(description="IANA name the days were cut in")


class AlertDay(BaseModel):
    day: date
    total: int
    low: int
    medium: int
    high: int
    open: int
    acknowledged: int
    resolved: int


class AlertReport(_Window):
    """Alerts raised in the window, per day, and how quickly they were handled."""

    days: list[AlertDay]
    total: int
    avg_minutes_to_acknowledge: float | None = Field(description="Over the alerts acknowledged so far")
    avg_minutes_to_resolve: float | None = Field(description="Over the alerts resolved so far")


class EventDay(BaseModel):
    day: date
    camera_id: str
    total: int
    low: int
    medium: int
    high: int


class EventReport(_Window):
    """Detections in the window, per day and camera."""

    days: list[EventDay]
    total: int

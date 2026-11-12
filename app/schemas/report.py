from datetime import date, datetime
from enum import StrEnum

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


class ActivitySummary(BaseModel):
    """What was watched. The posture figures are head-minutes: one animal seen for one minute is 1."""

    observed_minutes: int = Field(description="Minutes with a report; the rest of the day nobody was watching")
    lying_minutes: int
    standing_minutes: int
    walking_minutes: int
    unknown_posture_minutes: int
    peak_cattle: int = Field(description="Most animals seen in one minute")
    peak_people: int
    minutes_with_people: int
    lying_share: float | None = Field(
        description="Lying head-minutes over those with a known posture; null when no posture was seen",
    )


class CameraActivity(ActivitySummary):
    camera_id: str


class HealthNoticeKind(StrEnum):
    PROLONGED_LYING = "prolonged_lying"
    LITTLE_LYING = "little_lying"


class HealthNotice(BaseModel):
    """Something worth going to look at. Derived from posture counts, so it is never a diagnosis."""

    camera_id: str
    kind: HealthNoticeKind
    lying_share: float
    observed_minutes: int
    message: str


class AlertSummary(BaseModel):
    total: int
    low: int
    medium: int
    high: int
    open: int
    acknowledged: int
    resolved: int


class DailyReport(BaseModel):
    """One calendar day of activity, health indicators and alerts, computed when it is asked for."""

    day: date
    time_zone: str = Field(description="IANA name the day was cut in")
    start: datetime
    end: datetime
    complete: bool = Field(description="False while the day is still running, so its figures can grow")
    generated_at: datetime
    herd: ActivitySummary = Field(description="Every camera together; peaks can count an animal two cameras share")
    cameras: list[CameraActivity]
    health: list[HealthNotice]
    alerts: AlertSummary
    events_total: int

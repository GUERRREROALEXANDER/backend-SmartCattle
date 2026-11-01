"""History and summaries for the dashboard's report pages.

Aggregated here rather than in SQL: the windows are bounded (time_window),
the same code then serves the in-memory and the database stores, and days
can be cut in the caller's time zone, which SQLite cannot do. Whoever needs
the same figures straight from PostgreSQL has the views of migration 0017.
"""

from collections import Counter
from datetime import date, datetime
from statistics import fmean
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query

from app.core.time_window import DEFAULT_TIME_ZONE, time_zone, window
from app.dependencies import (
    Clock,
    CurrentUser,
    get_alert_store,
    get_camera_store,
    get_clock,
    get_event_store,
    get_occupancy_store,
)
from app.schemas.alert import Alert, AlertStatus
from app.schemas.event import EventLevel
from app.schemas.report import (
    AlertDay,
    AlertReport,
    CameraStatusHistory,
    EventDay,
    EventReport,
    OccupancyHistory,
)
from app.services.alert_store import AlertStore
from app.services.camera_store import CameraStore
from app.services.event_store import EventStore
from app.services.occupancy_store import OccupancyStore

router = APIRouter(prefix="/api/reports", tags=["reports"])

Now = Annotated[Clock, Depends(get_clock)]
CameraFilter = Annotated[str | None, Query(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
TimeZoneName = Annotated[str, Query(max_length=64, description="IANA time zone the days are cut in")]


@router.get("/occupancy", response_model=OccupancyHistory)
def occupancy_history(
    _user: CurrentUser,
    store: Annotated[OccupancyStore, Depends(get_occupancy_store)],
    clock: Now,
    camera_id: CameraFilter = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> OccupancyHistory:
    """The head count per camera and minute. Defaults to the last 7 days, at most 92."""
    span = window(start, end, clock())
    items = store.samples(camera_id, span.start, span.end)
    return OccupancyHistory(items=items, total=len(items))


@router.get("/camera-status", response_model=CameraStatusHistory)
def camera_status_history(
    _user: CurrentUser,
    store: Annotated[CameraStore, Depends(get_camera_store)],
    clock: Now,
    camera_id: CameraFilter = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> CameraStatusHistory:
    """Each time a camera's reported status changed, oldest first."""
    span = window(start, end, clock())
    items = store.status_changes(camera_id, span.start, span.end)
    return CameraStatusHistory(items=items, total=len(items))


def _minutes(alerts: list[Alert], field: str) -> float | None:
    waits = [
        (getattr(alert, field) - alert.created_at).total_seconds() / 60
        for alert in alerts if getattr(alert, field) is not None
    ]
    return round(fmean(waits), 1) if waits else None


def _day(moment: datetime, zone: ZoneInfo) -> date:
    return moment.astimezone(zone).date()


@router.get("/alerts", response_model=AlertReport)
def alert_report(
    _user: CurrentUser,
    store: Annotated[AlertStore, Depends(get_alert_store)],
    clock: Now,
    start: datetime | None = None,
    end: datetime | None = None,
    tz: TimeZoneName = DEFAULT_TIME_ZONE,
) -> AlertReport:
    """Alerts raised per day, by severity and current status, and the average time to handle them."""
    zone = time_zone(tz)
    span = window(start, end, clock())
    alerts = store.created_between(span.start, span.end)
    per_day: dict[date, Counter[str]] = {}
    for alert in alerts:
        counts = per_day.setdefault(_day(alert.created_at, zone), Counter())
        counts.update(["total", alert.severity.value, alert.status.value])
    days = [
        AlertDay(
            day=day, total=counts["total"],
            **{level.value: counts[level.value] for level in EventLevel},
            **{state.value: counts[state.value] for state in AlertStatus},
        )
        for day, counts in sorted(per_day.items())
    ]
    return AlertReport(
        start=span.start, end=span.end, time_zone=tz, days=days, total=len(alerts),
        avg_minutes_to_acknowledge=_minutes(alerts, "acknowledged_at"),
        avg_minutes_to_resolve=_minutes(alerts, "resolved_at"),
    )


@router.get("/events", response_model=EventReport)
def event_report(
    _user: CurrentUser,
    store: Annotated[EventStore, Depends(get_event_store)],
    clock: Now,
    start: datetime | None = None,
    end: datetime | None = None,
    tz: TimeZoneName = DEFAULT_TIME_ZONE,
) -> EventReport:
    """Detections per day and camera, by severity. Days follow the detection time."""
    zone = time_zone(tz)
    span = window(start, end, clock())
    events = [event for event in store.list_since(span.start) if event.timestamp < span.end]
    per_day: dict[tuple[date, str], Counter[str]] = {}
    for event in events:
        counts = per_day.setdefault((_day(event.timestamp, zone), event.camera_id), Counter())
        counts.update(["total", event.level.value])
    days = [
        EventDay(
            day=day, camera_id=camera, total=counts["total"],
            **{level.value: counts[level.value] for level in EventLevel},
        )
        for (day, camera), counts in sorted(per_day.items())
    ]
    return EventReport(start=span.start, end=span.end, time_zone=tz, days=days, total=len(events))

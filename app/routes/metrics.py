from datetime import UTC, timedelta

from fastapi import APIRouter, Depends
from pydantic import AwareDatetime

from app.core.config import Settings
from app.dependencies import (
    Clock,
    CurrentUser,
    get_alert_store,
    get_app_settings,
    get_camera_store,
    get_clock,
    get_event_store,
    get_occupancy_store,
    get_video_analysis_store,
)
from app.schemas.metrics import MetricsSummary
from app.schemas.occupancy import LiveMetrics
from app.schemas.video_analysis import VideoAlertClass
from app.services.alert_store import AlertStore
from app.services.camera_store import CameraStore
from app.services.event_store import EventStore
from app.services.live_metrics import compute_live_metrics
from app.services.occupancy_store import OccupancyStore
from app.services.video_analysis_store import VideoAnalysisStore

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/summary", response_model=MetricsSummary)
def metrics_summary(
    since: AwareDatetime | None = None,
    event_store: EventStore = Depends(get_event_store),
    analysis_store: VideoAnalysisStore = Depends(get_video_analysis_store),
    clock: Clock = Depends(get_clock),
) -> MetricsSummary:
    """Counts for the dashboard, over three windows.

    Each window is counted by the store rather than here. The previous version
    loaded every event since the earliest window into memory and summed Python
    booleans over it three times, which on a farm with months of history meant
    fetching tens of thousands of rows to answer with three integers.
    """
    now = clock().astimezone(UTC)
    hour_start = now - timedelta(hours=1)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since_utc = since.astimezone(UTC) if since is not None else None

    latest = analysis_store.list(1)
    animals_inside = animals_outside = None
    if latest:
        analysis = latest[0]
        outside_tracks = {
            alert.track_id for alert in analysis.alerts if alert.cls is VideoAlertClass.CATTLE
        }
        animals_outside = min(len(outside_tracks), analysis.max_cattle)
        animals_inside = analysis.max_cattle - animals_outside

    detections = event_store.count_between(hour_start, now)
    return MetricsSummary(
        animals_inside=animals_inside,
        animals_outside=animals_outside,
        alerts_today=event_store.count_between(today_start, now),
        alerts_since=(
            event_store.count_between(since_utc, now) if since_utc is not None else None
        ),
        # Rounded: the raw division serialises as 0.03333333333333333, which
        # is sixteen digits of precision the measurement does not have -- it
        # is a count of whole events over a fixed window.
        detections_per_minute=round(detections / 60, 3),
    )


@router.get("/live", response_model=LiveMetrics)
def live_metrics(
    _user: CurrentUser,
    cameras: CameraStore = Depends(get_camera_store),
    occupancy: OccupancyStore = Depends(get_occupancy_store),
    alerts: AlertStore = Depends(get_alert_store),
    events: EventStore = Depends(get_event_store),
    settings: Settings = Depends(get_app_settings),
    clock: Clock = Depends(get_clock),
) -> LiveMetrics:
    """What is happening now, from the stores rather than estimates.

    Unlike /summary, the head counts here are the AI's live counts per camera,
    not an inference from the latest uploaded video. Counts older than
    CAMERA_OFFLINE_AFTER_SECONDS are left out and reported in stale_cameras, so
    a camera that stopped reporting cannot freeze its last number on screen.

    Behind authentication, unlike /summary: where the herd is right now is the
    one number a thief would want.
    """
    now = clock().astimezone(UTC)
    return compute_live_metrics(
        cameras=cameras.list(), counts=occupancy.list(), open_alerts=alerts.count_open(),
        events_last_hour=events.count_between(now - timedelta(hours=1), now),
        now=now, offline_after_seconds=settings.camera_offline_after_seconds,
    )

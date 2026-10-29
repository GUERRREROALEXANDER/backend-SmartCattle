from datetime import UTC, timedelta

from fastapi import APIRouter, Depends
from pydantic import AwareDatetime

from app.dependencies import Clock, get_clock, get_event_store, get_video_analysis_store
from app.schemas.metrics import MetricsSummary
from app.schemas.video_analysis import VideoAlertClass
from app.services.event_store import EventStore
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
        detections_per_minute=detections / 60,
    )

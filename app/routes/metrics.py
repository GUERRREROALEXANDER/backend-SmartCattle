from datetime import UTC, datetime, timedelta

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
    now = clock().astimezone(UTC)
    hour_start = now - timedelta(hours=1)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since_utc = since.astimezone(UTC) if since is not None else None
    earliest = min(hour_start, today_start, since_utc) if since_utc is not None else min(hour_start, today_start)
    events = event_store.list_since(earliest)

    def in_window(event_timestamp: datetime, start: datetime) -> bool:
        return start <= event_timestamp <= now

    latest = analysis_store.list(1)
    animals_inside = animals_outside = None
    if latest:
        analysis = latest[0]
        outside_tracks = {
            alert.track_id for alert in analysis.alerts if alert.cls is VideoAlertClass.CATTLE
        }
        animals_outside = min(len(outside_tracks), analysis.max_cattle)
        animals_inside = analysis.max_cattle - animals_outside

    detections = sum(in_window(event.timestamp, hour_start) for event in events)
    return MetricsSummary(
        animals_inside=animals_inside,
        animals_outside=animals_outside,
        alerts_today=sum(in_window(event.timestamp, today_start) for event in events),
        alerts_since=(
            sum(in_window(event.timestamp, since_utc) for event in events)
            if since_utc is not None else None
        ),
        detections_per_minute=detections / 60,
    )

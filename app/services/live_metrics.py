"""The dashboard's live counters, computed from what the stores hold right now."""

from collections.abc import Sequence
from datetime import datetime, timedelta

from app.schemas.camera import CameraRecord, CameraStatus, effective_status
from app.schemas.occupancy import LiveMetrics, Occupancy, SystemStatus


def is_fresh(occupancy: Occupancy, now: datetime, max_age: timedelta) -> bool:
    """Whether a head count is recent enough to show as current.

    Measured from received_at, the backend's own clock, for the same reason
    camera status is: a skewed clock on the farm PC must not decide what the
    dashboard believes.
    """
    return now - occupancy.received_at <= max_age


def compute_live_metrics(
    cameras: Sequence[CameraRecord],
    counts: Sequence[Occupancy],
    open_alerts: int,
    events_last_hour: int,
    now: datetime,
    offline_after_seconds: int,
) -> LiveMetrics:
    max_age = timedelta(seconds=offline_after_seconds)
    online = sum(1 for camera in cameras if effective_status(camera, now, offline_after_seconds) is CameraStatus.ONLINE)
    fresh = [count for count in counts if is_fresh(count, now, max_age)]
    stale = len(counts) - len(fresh)
    inside = sum(count.cattle_inside for count in fresh)
    outside = sum(count.cattle_outside for count in fresh)

    if not cameras and not counts:
        system_status = SystemStatus.NO_DATA
    elif online < len(cameras) or stale:
        # Some numbers on the dashboard are incomplete. Saying so beats
        # showing a confident total that silently leaves a camera out.
        system_status = SystemStatus.DEGRADED
    else:
        system_status = SystemStatus.OPERATIONAL

    return LiveMetrics(
        cameras_total=len(cameras), cameras_online=online, stale_cameras=stale,
        cattle_inside=inside, cattle_outside=outside, cattle_total=inside + outside,
        open_alerts=open_alerts, events_last_hour=events_last_hour,
        system_status=system_status, generated_at=now,
    )

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.core.config import Settings
from app.dependencies import (
    Clock,
    get_app_settings,
    get_broadcaster,
    get_camera_store,
    get_clock,
    get_occupancy_store,
    get_zone_store,
    verify_api_key,
)
from app.schemas.camera import (
    Camera,
    CameraList,
    CameraRecord,
    CameraSourceReport,
    CameraStatusReport,
    effective_status,
)
from app.schemas.occupancy import DetectionFrame, OccupancyReport
from app.schemas.zone import ZoneList
from app.services.broadcaster import Broadcaster
from app.services.camera_store import CameraStore
from app.services.occupancy_store import OccupancyStore
from app.services.zone_store import ZoneStore

router = APIRouter(tags=["cameras"])
CameraId = Annotated[str, Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


def _camera(record: CameraRecord, now: datetime, settings: Settings) -> Camera:
    status_now = effective_status(record, now, settings.camera_offline_after_seconds)
    return Camera(**record.model_dump(), status=status_now)


@router.get("/api/cameras", response_model=CameraList)
def list_cameras(
    store: CameraStore = Depends(get_camera_store),
    settings: Settings = Depends(get_app_settings),
    clock: Clock = Depends(get_clock),
) -> CameraList:
    now = clock()
    items = [_camera(record, now, settings) for record in store.list()]
    return CameraList(items=items, total=len(items))


@router.get("/api/cameras/{camera_id}", response_model=Camera)
def get_camera(
    camera_id: CameraId,
    store: CameraStore = Depends(get_camera_store),
    settings: Settings = Depends(get_app_settings),
    clock: Clock = Depends(get_clock),
) -> Camera:
    """One camera's current state.

    404 covers two different situations that the API cannot separate: a camera
    that does not exist, and one that exists but has never been reported on.
    Both mean "there is no state to show", and the list endpoint omits the
    second for the same reason.
    """
    now = clock()
    record = next((item for item in store.list() if item.id == camera_id), None)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No reported camera with id {camera_id}",
        )
    return _camera(record, now, settings)


@router.put("/api/ai/cameras/{camera_id}/status", response_model=Camera, dependencies=[Depends(verify_api_key)])
def report_camera_status(
    camera_id: CameraId,
    report: CameraStatusReport,
    store: CameraStore = Depends(get_camera_store),
    settings: Settings = Depends(get_app_settings),
    broadcaster: Broadcaster = Depends(get_broadcaster),
    clock: Clock = Depends(get_clock),
) -> Camera:
    # One reading of the clock: the stored timestamp and the status derived
    # from it must agree, or a fresh report could come back already offline.
    now = clock()
    camera = _camera(store.report(camera_id, report, now), now, settings)
    # A removed camera can still have a report on its way; it must not come back on anyone's screen.
    registration = store.registration(camera_id)
    if registration is None or registration.active:
        broadcaster.publish("camera.status", camera, now)
    return camera


@router.put(
    "/api/ai/cameras/{camera_id}/source", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_api_key)],
)
def register_camera_source(
    camera_id: CameraId,
    report: CameraSourceReport,
    store: CameraStore = Depends(get_camera_store),
    broadcaster: Broadcaster = Depends(get_broadcaster),
    clock: Clock = Depends(get_clock),
) -> None:
    """Records where a camera is reached, and whether it is still in use.

    Separate from the status heartbeat, which sends it once instead of several
    times a minute. It is not returned by GET /api/cameras, which needs no
    session: a LAN address is nobody else's business.
    """
    store.register(camera_id, report)
    if not report.active:
        broadcaster.publish("camera.removed", {"id": camera_id}, clock())


@router.get("/api/ai/cameras/{camera_id}/zones", response_model=ZoneList, dependencies=[Depends(verify_api_key)])
def camera_safe_zones(camera_id: CameraId, store: ZoneStore = Depends(get_zone_store)) -> ZoneList:
    """The active safe zones of one camera, for the AI service.

    GET /api/zones serves the same data to people, behind a user session the
    AI service does not have. This one takes its shared key, is limited to one
    camera and leaves out inactive zones: those are the ones it must ignore.
    The AI polls it, so a zone drawn in the dashboard reaches the detector
    without restarting anything.
    """
    items = [zone for zone in store.list(camera_id) if zone.active]
    return ZoneList(items=items, total=len(items))


@router.put(
    "/api/ai/cameras/{camera_id}/occupancy", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_api_key)],
)
def report_occupancy(
    camera_id: CameraId,
    report: OccupancyReport,
    store: OccupancyStore = Depends(get_occupancy_store),
    broadcaster: Broadcaster = Depends(get_broadcaster),
    clock: Clock = Depends(get_clock),
) -> None:
    """The AI's head count for one camera, with the boxes behind it.

    The counts are stored and feed GET /api/metrics/live. The boxes are only
    pushed to open dashboards, where the video overlay and the 3D view draw
    them; they are never stored (ARCHITECTURE section 8).

    204 even when the report is older than the stored one and is ignored: the
    AI did nothing wrong by retrying, and there is nothing for it to fix.
    """
    now = clock()
    occupancy = store.record(camera_id, report, now)
    if occupancy is None:
        return
    broadcaster.publish("occupancy.updated", occupancy, now)
    if report.detections:
        frame = DetectionFrame(camera_id=camera_id, observed_at=report.observed_at, detections=report.detections)
        broadcaster.publish("detections", frame, now)

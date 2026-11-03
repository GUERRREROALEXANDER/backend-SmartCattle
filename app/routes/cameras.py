from datetime import datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.core.config import Settings
from app.dependencies import (
    AuditTrail,
    Clock,
    OwnerUser,
    Visible,
    get_app_settings,
    get_broadcaster,
    get_camera_pairings,
    get_camera_store,
    get_clock,
    get_occupancy_store,
    get_zone_store,
    verify_api_key,
)
from app.schemas.audit import AuditAction
from app.schemas.camera import (
    Camera,
    CameraList,
    CameraPairing,
    CameraRecord,
    CameraSourceReport,
    CameraStatus,
    CameraStatusReport,
    PairedCameraCreate,
    SavedCamera,
    SavedCameraCreate,
    SavedCameraList,
    SavedCameraView,
    effective_status,
)
from app.schemas.occupancy import DetectionFrame, OccupancyReport
from app.schemas.zone import ZoneList
from app.services.broadcaster import Broadcaster
from app.services.camera_pairing import LIFETIME, CameraPairings
from app.services.camera_store import CameraStore
from app.services.occupancy_store import OccupancyStore
from app.services.zone_store import ZoneStore

router = APIRouter(tags=["cameras"])
CameraId = Annotated[str, Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


#: A farm has a few cameras. A loop saving them has no reason to reach the database thousands of times.
MAX_SAVED_CAMERAS = 50


def _camera(record: CameraRecord, now: datetime, settings: Settings, name: str | None = None) -> Camera:
    status_now = effective_status(record, now, settings.camera_offline_after_seconds)
    return Camera(**record.model_dump(), status=status_now, display_name=name)


def _saved_view(saved: SavedCamera, store: CameraStore, now: datetime, settings: Settings) -> SavedCameraView:
    record = next((item for item in store.list() if item.id == saved.id), None)
    status_now = CameraStatus.OFFLINE if record is None else _camera(record, now, settings).status
    return SavedCameraView(id=saved.id, name=saved.name, source=saved.source, status=status_now)


@router.get("/api/cameras/saved", response_model=SavedCameraList)
def list_saved_cameras(
    scope: Visible,
    store: CameraStore = Depends(get_camera_store),
    settings: Settings = Depends(get_app_settings),
    clock: Clock = Depends(get_clock),
) -> SavedCameraList:
    """The cameras of this account, connected or not. Saving one opens nothing."""
    now = clock()
    items = [_saved_view(item, store, now, settings) for item in store.saved(scope.account_id)]
    return SavedCameraList(items=items, total=len(items))


@router.post("/api/cameras/saved", response_model=SavedCameraView, status_code=status.HTTP_201_CREATED)
def save_camera(
    data: SavedCameraCreate,
    owner: OwnerUser,
    audit: AuditTrail,
    store: CameraStore = Depends(get_camera_store),
    settings: Settings = Depends(get_app_settings),
    clock: Clock = Depends(get_clock),
) -> SavedCameraView:
    """Adds a camera to the account. The id comes from here, so nobody can claim one another account already uses."""
    if len(store.saved(owner.account_id)) >= MAX_SAVED_CAMERAS:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This account already has too many cameras")
    saved = SavedCamera(id=f"cam-{uuid4().hex[:12]}", name=data.name, source=data.source, account_id=owner.account_id)
    store.save(saved)
    audit.record(
        AuditAction.CAMERA_SAVED, actor_id=owner.id, actor_email=owner.email,
        target_type="camera", target_id=saved.id, detail={"name": saved.name},
    )
    return _saved_view(saved, store, clock(), settings)


@router.post("/api/cameras/pairing", response_model=CameraPairing, status_code=status.HTTP_201_CREATED)
def start_pairing(
    owner: OwnerUser,
    pairings: CameraPairings = Depends(get_camera_pairings),
    clock: Clock = Depends(get_clock),
) -> CameraPairing:
    """A code for the QR that lets a phone add a camera to this account."""
    return CameraPairing(code=pairings.issue(owner.account_id, clock()), expires_in=int(LIFETIME.total_seconds()))


@router.post("/api/cameras/paired", response_model=SavedCameraView, status_code=status.HTTP_201_CREATED)
def save_paired_camera(
    data: PairedCameraCreate,
    pairings: CameraPairings = Depends(get_camera_pairings),
    store: CameraStore = Depends(get_camera_store),
    settings: Settings = Depends(get_app_settings),
    clock: Clock = Depends(get_clock),
) -> SavedCameraView:
    """Saves the camera sent by a phone. Needs the code instead of a session; 403 and not 401, as there is no session
    to lose. Connecting it stays a separate step, from the Cameras page."""
    now = clock()
    account_id = pairings.redeem(data.code, now)
    if account_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This QR has expired; show a new one")
    if len(store.saved(account_id)) >= MAX_SAVED_CAMERAS:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This account already has too many cameras")
    saved = SavedCamera(id=f"cam-{uuid4().hex[:12]}", name=data.name, source=data.source, account_id=account_id)
    store.save(saved)
    return _saved_view(saved, store, now, settings)


@router.get("/api/cameras/saved/{camera_id}", response_model=SavedCameraView)
def get_saved_camera(
    camera_id: CameraId,
    scope: Visible,
    store: CameraStore = Depends(get_camera_store),
    settings: Settings = Depends(get_app_settings),
    clock: Clock = Depends(get_clock),
) -> SavedCameraView:
    """One saved camera. The AI service asks this to learn whether a person may run or watch it."""
    saved = store.saved_one(camera_id)
    if saved is None or not scope.sees_account(saved.account_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No saved camera with id {camera_id}")
    return _saved_view(saved, store, clock(), settings)


@router.delete("/api/cameras/saved/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved_camera(
    camera_id: CameraId,
    owner: OwnerUser,
    scope: Visible,
    audit: AuditTrail,
    store: CameraStore = Depends(get_camera_store),
    broadcaster: Broadcaster = Depends(get_broadcaster),
    clock: Clock = Depends(get_clock),
) -> None:
    saved = store.saved_one(camera_id)
    if saved is None or not scope.sees_account(saved.account_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No saved camera with id {camera_id}")
    store.forget(camera_id)
    broadcaster.publish("camera.removed", {"id": camera_id}, clock(), account_id=saved.account_id)
    audit.record(
        AuditAction.CAMERA_DELETED, actor_id=owner.id, actor_email=owner.email,
        target_type="camera", target_id=camera_id, detail={"name": saved.name},
    )


@router.get("/api/cameras", response_model=CameraList)
def list_cameras(
    scope: Visible,
    store: CameraStore = Depends(get_camera_store),
    settings: Settings = Depends(get_app_settings),
    clock: Clock = Depends(get_clock),
) -> CameraList:
    now = clock()
    names = {item.id: item.name for item in store.saved(scope.account_id)}
    items = [
        _camera(record, now, settings, names.get(record.id))
        for record in store.list() if scope.sees_camera(record.id)
    ]
    return CameraList(items=items, total=len(items))


@router.get("/api/cameras/{camera_id}", response_model=Camera)
def get_camera(
    camera_id: CameraId,
    scope: Visible,
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
    if record is None or not scope.sees_camera(camera_id):
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
        broadcaster.publish("camera.status", camera, now, account_id=store.owner_of(camera_id))
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
        broadcaster.publish("camera.removed", {"id": camera_id}, clock(), account_id=store.owner_of(camera_id))


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
    cameras: CameraStore = Depends(get_camera_store),
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
    account = cameras.owner_of(camera_id)
    broadcaster.publish("occupancy.updated", occupancy, now, account_id=account)
    if report.detections:
        frame = DetectionFrame(camera_id=camera_id, observed_at=report.observed_at, detections=report.detections)
        broadcaster.publish("detections", frame, now, account_id=account)

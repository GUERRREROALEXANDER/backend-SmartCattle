import ipaddress
import logging
from datetime import datetime, timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from app.core.config import Settings
from app.core.security import create_media_ticket
from app.dependencies import (
    AuditTrail,
    Clock,
    CurrentUser,
    OwnerUser,
    Scope,
    Visible,
    get_app_settings,
    get_broadcaster,
    get_camera_pairings,
    get_camera_store,
    get_clock,
    get_detection_log_store,
    get_farm_services,
    get_occupancy_store,
    get_zone_store,
    require_jwt_secret,
    verify_camera_reporter,
    verify_configured_api_key,
)
from app.schemas.audit import AuditAction
from app.schemas.camera import (
    IMOU_SERIAL,
    Camera,
    CameraConnect,
    CameraConnectResult,
    CameraList,
    CameraLiveAccess,
    CameraPairing,
    CameraRecord,
    CameraSourceReport,
    CameraStatus,
    CameraStatusReport,
    CameraSuggestion,
    CameraSuggestionList,
    CloudCamera,
    CloudCameraList,
    PairedCameraCreate,
    SavedCamera,
    SavedCameraCreate,
    SavedCameraList,
    SavedCameraView,
    effective_status,
    imou_serial_of,
    imou_source,
)
from app.schemas.detection import DetectionLogReport
from app.schemas.occupancy import DetectionFrame, OccupancyReport
from app.schemas.zone import ZoneList
from app.services.broadcaster import Broadcaster
from app.services.camera_pairing import LIFETIME, CameraPairings
from app.services.camera_store import CameraNameTakenError, CameraStore
from app.services.detection_log_store import DetectionLogStore
from app.services.farm_agents import FarmServices
from app.services.imou_cloud import ImouCloud, ImouError
from app.services.occupancy_store import OccupancyStore
from app.services.zone_store import ZoneStore

logger = logging.getLogger(__name__)
router = APIRouter(tags=["cameras"])
CameraId = Annotated[str, Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


#: A farm has a few cameras. A loop saving them has no reason to reach the database thousands of times.
MAX_SAVED_CAMERAS = 50

#: How long a ticket can open the video. A session it opened outlives it (ARCHITECTURE section 14).
MEDIA_TICKET_LIFETIME = timedelta(minutes=10)


def _camera(record: CameraRecord, now: datetime, settings: Settings, name: str | None = None) -> Camera:
    status_now = effective_status(record, now, settings.camera_offline_after_seconds)
    return Camera(**record.model_dump(), status=status_now, display_name=name)


def _saved_name(store: CameraStore, camera_id: str) -> str | None:
    saved = store.saved_one(camera_id)
    return saved.name if saved is not None else None


def _saved_view(saved: SavedCamera, store: CameraStore, now: datetime, settings: Settings) -> SavedCameraView:
    record = next((item for item in store.list() if item.id == saved.id), None)
    status_now = CameraStatus.OFFLINE if record is None else _camera(record, now, settings).status
    return SavedCameraView(
        id=saved.id, name=saved.name, source=saved.source, status=status_now,
        address=saved.address, cloud=imou_serial_of(saved.source) is not None,
    )


def _save(store: CameraStore, camera: SavedCamera) -> None:
    try:
        store.save(camera)
    except CameraNameTakenError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This account already has a camera with that name",
        ) from error


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


def get_imou_cloud(request: Request) -> ImouCloud | None:
    return getattr(request.app.state, "imou_cloud", None)


def _imou_cameras(store: CameraStore) -> dict[str, SavedCamera]:
    """Every saved Imou cloud camera, by serial number, whatever its account."""
    found: dict[str, SavedCamera] = {}
    for saved in store.saved(None):
        serial = imou_serial_of(saved.source)
        if serial is not None:
            found.setdefault(serial, saved)
    return found


def _suggestions(cloud: ImouCloud, store: CameraStore, scope: Scope) -> list[CameraSuggestion]:
    """The Imou cameras this account can connect, or already did. Those of another account are not shown."""
    taken = _imou_cameras(store)
    items = []
    for device in cloud.devices():
        saved = taken.get(device.serial)
        if saved is not None and not scope.sees_account(saved.account_id):
            continue
        items.append(CameraSuggestion(
            serial=device.serial, name=device.name, model=device.model, online=device.online,
            camera_id=None if saved is None else saved.id,
        ))
    return items


def _looks_like_serial(text: str) -> bool:
    upper = text.upper()
    return bool(IMOU_SERIAL.fullmatch(upper)) and any(c.isdigit() for c in upper) and any(c.isalpha() for c in upper)


def _is_inside_a_network(address: str) -> bool:
    """A LAN address, which only exists inside the farm's Wi-Fi: no server on the internet can open it."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return "." not in address or address.lower().endswith((".local", ".lan", ".home"))
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _free_name(wanted: str, serial: str, store: CameraStore, account_id: int | None) -> str:
    taken = {item.name.casefold() for item in store.saved(account_id)}
    if wanted.casefold() not in taken:
        return wanted
    return f"{wanted[:70]} {serial[-4:]}"


@router.get("/api/cameras/suggestions", response_model=CameraSuggestionList)
def camera_suggestions(
    scope: Visible,
    store: CameraStore = Depends(get_camera_store),
    cloud: ImouCloud | None = Depends(get_imou_cloud),
) -> CameraSuggestionList:
    """Cameras the system recommends connecting: those of the Imou account, watchable from anywhere."""
    if cloud is None:
        return CameraSuggestionList(available=False, message="No Imou account is configured on this server", items=[])
    try:
        items = _suggestions(cloud, store, scope)
    except ImouError as error:
        logger.warning("Imou cloud unavailable: %s", error)
        return CameraSuggestionList(available=False, message="The Imou cloud could not be reached; try again", items=[])
    return CameraSuggestionList(available=True, items=items)


@router.post("/api/cameras/connect", response_model=CameraConnectResult)
def connect_camera(
    data: CameraConnect,
    owner: OwnerUser,
    scope: Visible,
    audit: AuditTrail,
    store: CameraStore = Depends(get_camera_store),
    cloud: ImouCloud | None = Depends(get_imou_cloud),
    settings: Settings = Depends(get_app_settings),
    clock: Clock = Depends(get_clock),
) -> CameraConnectResult:
    """Connects a camera from its address, or from the Imou camera the system recommended.

    A LAN address (192.168.x.x) cannot be opened from the internet, so it is never tried: the answer recommends the
    account's Imou cameras instead, which open through Imou's cloud by serial number. The address the owner typed is
    kept with the camera to show it.
    """
    typed_serial = data.address is not None and data.serial is None and _looks_like_serial(data.address)
    serial = data.address.upper() if typed_serial and data.address is not None else data.serial
    label = None if typed_serial else data.address

    if serial is None:
        assert label is not None  # noqa: S101 - CameraConnect requires one of the two
        suggestions: list[CameraSuggestion] = []
        if cloud is not None:
            try:
                suggestions = [item for item in _suggestions(cloud, store, scope) if item.camera_id is None]
            except ImouError as error:
                logger.warning("Imou cloud unavailable: %s", error)
        online = [item for item in suggestions if item.online]
        recommended = (online[0] if len(online) == 1 else suggestions[0] if len(suggestions) == 1 else None)
        where = (
            f"{label} is an address inside the farm's Wi-Fi, so a server on the internet cannot open it."
            if _is_inside_a_network(label)
            else f"{label} cannot be opened from the cloud by its address yet."
        )
        if suggestions:
            return CameraConnectResult(
                outcome="choose", suggestions=suggestions,
                recommended=None if recommended is None else recommended.serial,
                message=f"{where} Connect it through the Imou cloud instead, and it can be watched from anywhere.",
            )
        return CameraConnectResult(
            outcome="unreachable",
            message=f"{where} Add the camera to the Imou account of SmartCattle and try again.",
        )

    if cloud is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="No Imou account is configured on this server")
    try:
        device = cloud.device(serial)
    except ImouError as error:
        logger.warning("Imou cloud unavailable: %s", error)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="The Imou cloud could not be reached; try again") from error
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"The Imou account of SmartCattle has no camera with serial {serial}")

    existing = _imou_cameras(store).get(serial)
    if existing is not None and not scope.sees_account(existing.account_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This camera is already connected to another account")
    if existing is not None:
        saved = existing if label is None else existing.model_copy(update={"address": label})
        _save(store, saved)
    else:
        if len(store.saved(owner.account_id)) >= MAX_SAVED_CAMERAS:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="This account already has too many cameras")
        saved = SavedCamera(
            id=f"cam-{uuid4().hex[:12]}", name=_free_name(data.name or device.name, serial, store, owner.account_id),
            source=imou_source(serial), account_id=owner.account_id, address=label,
        )
        _save(store, saved)
        audit.record(
            AuditAction.CAMERA_SAVED, actor_id=owner.id, actor_email=owner.email,
            target_type="camera", target_id=saved.id, detail={"name": saved.name},
        )
    message = ("Connected. The video opens from the Imou cloud in a few seconds."
               if device.online else "Connected. The Imou cloud sees the camera offline: check its power and Wi-Fi.")
    return CameraConnectResult(outcome="connected", message=message,
                               camera=_saved_view(saved, store, clock(), settings))


@router.get("/api/ai/cameras/cloud", response_model=CloudCameraList, dependencies=[Depends(verify_configured_api_key)])
def cloud_cameras(store: CameraStore = Depends(get_camera_store)) -> CloudCameraList:
    """The cameras the AI service in the cloud keeps open: every saved Imou camera. Needs the service's own key."""
    return CloudCameraList(items=[
        CloudCamera(camera_id=saved.id, source=saved.source) for saved in _imou_cameras(store).values()
    ])


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
    _save(store, saved)
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
    _save(store, saved)
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


@router.post("/api/cameras/{camera_id}/live", response_model=CameraLiveAccess)
def open_live_video(
    camera_id: CameraId,
    _user: CurrentUser,
    scope: Visible,
    secret: str = Depends(require_jwt_secret),
    store: CameraStore = Depends(get_camera_store),
    services: FarmServices = Depends(get_farm_services),
    clock: Clock = Depends(get_clock),
) -> CameraLiveAccess:
    """Where to watch a camera from anywhere, with a ticket for that camera only.

    The video goes from the farm's media server to the browser and never passes
    through here (ARCHITECTURE section 14). CurrentUser as well as Visible: without
    account isolation Visible lets anyone through, and video is never open to anyone.
    """
    saved = store.saved_one(camera_id)
    if saved is None or not scope.sees_account(saved.account_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No saved camera with id {camera_id}")
    now = clock()
    media_url = services.media_url_for(saved.account_id, now)
    if media_url is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The farm PC is not sharing video right now")
    return CameraLiveAccess(
        camera_id=camera_id,
        hls_url=f"{media_url}/{camera_id}/index.m3u8",
        token=create_media_ticket(camera_id=camera_id, secret=secret, expires_in=MEDIA_TICKET_LIFETIME, now=now),
        expires_in=int(MEDIA_TICKET_LIFETIME.total_seconds()),
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
    return _camera(record, now, settings, _saved_name(store, camera_id))


@router.put("/api/ai/cameras/{camera_id}/status", response_model=Camera, dependencies=[Depends(verify_camera_reporter)])
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
    camera = _camera(store.report(camera_id, report, now), now, settings, _saved_name(store, camera_id))
    # A removed camera can still have a report on its way; it must not come back on anyone's screen.
    registration = store.registration(camera_id)
    if registration is None or registration.active:
        broadcaster.publish("camera.status", camera, now, account_id=store.owner_of(camera_id))
    return camera


@router.put(
    "/api/ai/cameras/{camera_id}/source", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_camera_reporter)],
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


@router.get(
    "/api/ai/cameras/{camera_id}/zones", response_model=ZoneList, dependencies=[Depends(verify_camera_reporter)],
)
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
    dependencies=[Depends(verify_camera_reporter)],
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


@router.put(
    "/api/ai/cameras/{camera_id}/detection-log", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_camera_reporter)],
)
def report_detection_log(
    camera_id: CameraId,
    report: DetectionLogReport,
    store: DetectionLogStore = Depends(get_detection_log_store),
) -> None:
    """What YOLO saw on one camera, counted by posture. Feeds GET /api/reports/daily.

    The AI service can send it as often as it counts: only the last report of each
    minute is kept, which is what keeps per-frame detections out of the database
    (ARCHITECTURE section 8). An empty scene is worth reporting too, because it
    tells a quiet pasture apart from a camera that was not watching.
    """
    store.record(camera_id, report)

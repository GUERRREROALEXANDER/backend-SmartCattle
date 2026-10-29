from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.core.config import Settings
from app.dependencies import Clock, get_app_settings, get_camera_store, get_clock, verify_api_key
from app.schemas.camera import Camera, CameraList, CameraRecord, CameraStatusReport, effective_status
from app.services.camera_store import CameraStore

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
    clock: Clock = Depends(get_clock),
) -> Camera:
    # One reading of the clock: the stored timestamp and the status derived
    # from it must agree, or a fresh report could come back already offline.
    now = clock()
    return _camera(store.report(camera_id, report, now), now, settings)

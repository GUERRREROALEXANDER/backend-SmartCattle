from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.core.config import Settings
from app.dependencies import Clock, get_app_settings, get_camera_store, get_clock, verify_api_key
from app.schemas.camera import Camera, CameraList, CameraRecord, CameraStatusReport, effective_status
from app.services.camera_store import CameraStore

router = APIRouter(tags=["cameras"])
CameraId = Annotated[str, Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


def _camera(record: CameraRecord, now: datetime, settings: Settings) -> Camera:
    status = effective_status(record, now, settings.camera_offline_after_seconds)
    return Camera(**record.model_dump(), status=status)


@router.get("/api/cameras", response_model=CameraList)
def list_cameras(
    store: CameraStore = Depends(get_camera_store),
    settings: Settings = Depends(get_app_settings),
    clock: Clock = Depends(get_clock),
) -> CameraList:
    now = clock()
    items = [_camera(record, now, settings) for record in store.list()]
    return CameraList(items=items, total=len(items))


@router.put("/api/ai/cameras/{camera_id}/status", response_model=Camera, dependencies=[Depends(verify_api_key)])
def report_camera_status(
    camera_id: CameraId,
    report: CameraStatusReport,
    store: CameraStore = Depends(get_camera_store),
    settings: Settings = Depends(get_app_settings),
) -> Camera:
    return _camera(store.report(camera_id, report), datetime.now(UTC), settings)

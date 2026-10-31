"""Browser side of video uploads: ask for a URL, upload to it, confirm."""

from datetime import timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import Settings
from app.dependencies import (
    Clock,
    CurrentUser,
    get_app_settings,
    get_broadcaster,
    get_clock,
    get_farm_store,
    get_video_upload_store,
    require_video_storage,
)
from app.schemas.auth import UserRole
from app.schemas.video_upload import (
    MAX_VIDEO_BYTES,
    VIDEO_TYPES,
    StoredVideoUpload,
    UploadStatus,
    UploadTicket,
    VideoUpload,
    VideoUploadCreate,
    VideoUploadList,
)
from app.services.broadcaster import Broadcaster
from app.services.farm_store import FarmStore
from app.services.video_storage import VideoStorage
from app.services.video_upload_store import UploadStateError, VideoUploadStore

router = APIRouter(prefix="/api/videos/uploads", tags=["video uploads"])

Store = Annotated[VideoUploadStore, Depends(get_video_upload_store)]
Storage = Annotated[VideoStorage, Depends(require_video_storage)]
Hub = Annotated[Broadcaster, Depends(get_broadcaster)]
Now = Annotated[Clock, Depends(get_clock)]


def _upload_or_404(store: VideoUploadStore, upload_id: UUID) -> StoredVideoUpload:
    upload = store.get(upload_id)
    if upload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No upload with id {upload_id}")
    return upload


@router.post("", response_model=UploadTicket, status_code=status.HTTP_201_CREATED)
def start_upload(
    data: VideoUploadCreate,
    user: CurrentUser,
    store: Store,
    storage: Storage,
    clock: Now,
    farms: FarmStore = Depends(get_farm_store),
    settings: Settings = Depends(get_app_settings),
) -> UploadTicket:
    """Registers an upload and returns a presigned URL to PUT the file to.

    The file goes from the browser to the bucket; this API never sees the
    bytes. Send it with exactly `upload_headers`, then call /complete.
    """
    if data.size_bytes > MAX_VIDEO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Videos are limited to {MAX_VIDEO_BYTES // 1024 ** 2} MiB",
        )
    extensions = VIDEO_TYPES.get(data.content_type)
    if extensions is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Supported video types: {', '.join(sorted(VIDEO_TYPES))}",
        )
    if data.extension not in extensions:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"A {data.content_type} file must end in {', '.join(sorted(extensions))}",
        )
    if data.lot_id is not None and farms.get_lot(data.lot_id) is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"No lot with id {data.lot_id}")

    now = clock()
    upload_id = uuid4()
    upload = store.create(StoredVideoUpload(
        id=upload_id, file_name=data.file_name, content_type=data.content_type, size_bytes=data.size_bytes,
        lot_id=data.lot_id, uploaded_by=user.id, status=UploadStatus.PENDING_UPLOAD, created_at=now,
        # The key is ours, never the user's file name: no collisions, nothing to sanitise.
        storage_key=f"videos/{upload_id}{data.extension}",
    ))
    presigned = storage.presign_upload(
        upload.storage_key, data.content_type, data.size_bytes,
        timedelta(minutes=settings.video_upload_url_minutes), now,
    )
    return UploadTicket(
        upload=upload.public(), upload_url=presigned.url, upload_method=presigned.method,
        upload_headers=presigned.headers, expires_at=presigned.expires_at,
    )


@router.post("/{upload_id}/complete", response_model=VideoUpload)
def complete_upload(
    upload_id: UUID, user: CurrentUser, store: Store, storage: Storage, hub: Hub, clock: Now,
) -> VideoUpload:
    """Confirms the file reached the bucket and queues it for the AI service.

    The size is checked against what was declared, because the signed length
    is not enforced by every S3-compatible store. A mismatch deletes the
    object and fails the upload rather than queueing a file nobody vouched for.
    """
    upload = _upload_or_404(store, upload_id)
    if upload.uploaded_by != user.id and user.role is not UserRole.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the uploader or an owner can do this")
    if upload.status is not UploadStatus.PENDING_UPLOAD:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"The upload is already {upload.status}")

    stored_size = storage.object_size(upload.storage_key)
    if stored_size is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The file has not been uploaded yet")
    now = clock()
    try:
        if stored_size != upload.size_bytes:
            storage.delete(upload.storage_key)
            failed = store.change(upload_id, frozenset({UploadStatus.PENDING_UPLOAD}), {
                "status": UploadStatus.FAILED, "finished_at": now,
                "error": f"Uploaded {stored_size} bytes but declared {upload.size_bytes}",
            })
            assert failed is not None  # noqa: S101 - read above, and uploads are never deleted
            hub.publish("video.updated", failed.public(), now)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="The uploaded file does not match its size",
            )
        queued = store.change(upload_id, frozenset({UploadStatus.PENDING_UPLOAD}), {
            "status": UploadStatus.QUEUED, "uploaded_at": now,
        })
    except UploadStateError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"The upload is already {error.current}",
        ) from error
    assert queued is not None  # noqa: S101
    hub.publish("video.updated", queued.public(), now)
    return queued.public()


@router.get("", response_model=VideoUploadList)
def list_uploads(
    _user: CurrentUser,
    store: Store,
    upload_status: Annotated[UploadStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> VideoUploadList:
    """Uploads, newest first, with their processing progress."""
    page = store.page(upload_status, limit, offset)
    return VideoUploadList(items=[upload.public() for upload in page.items], total=page.total)


@router.get("/{upload_id}", response_model=VideoUpload)
def get_upload(upload_id: UUID, _user: CurrentUser, store: Store) -> VideoUpload:
    return _upload_or_404(store, upload_id).public()

"""AI-service side of video uploads: claim a video, report progress, deliver the result."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.config import Settings
from app.dependencies import (
    Clock,
    get_app_settings,
    get_broadcaster,
    get_camera_store,
    get_clock,
    get_event_store,
    get_farm_store,
    get_user_store,
    get_video_analysis_store,
    get_video_upload_store,
    require_video_storage,
    verify_api_key,
)
from app.schemas.video_analysis import VideoAnalysisCreate
from app.schemas.video_upload import (
    JobFailure,
    JobProgress,
    StoredVideoUpload,
    UploadStatus,
    VideoJob,
    VideoProgress,
    VideoUpload,
)
from app.services.broadcaster import Broadcaster
from app.services.camera_store import CameraStore
from app.services.event_store import EventStore
from app.services.farm_store import FarmStore
from app.services.user_store import UserStore
from app.services.video_analysis_recording import record_analysis
from app.services.video_analysis_store import VideoAnalysisStore
from app.services.video_storage import VideoStorage
from app.services.video_upload_store import UploadStateError, VideoUploadStore

router = APIRouter(prefix="/api/ai/video-jobs", tags=["video jobs"], dependencies=[Depends(verify_api_key)])

Store = Annotated[VideoUploadStore, Depends(get_video_upload_store)]
Hub = Annotated[Broadcaster, Depends(get_broadcaster)]
Now = Annotated[Clock, Depends(get_clock)]
Users = Annotated[UserStore, Depends(get_user_store)]

PROCESSING = frozenset({UploadStatus.PROCESSING})


def _account(users: UserStore, job: StoredVideoUpload) -> int | None:
    """The account a video belongs to: its progress goes to that account's dashboards only."""
    uploader = users.by_id(job.uploaded_by)
    return None if uploader is None else uploader.account_id


def _advance(store: VideoUploadStore, job_id: UUID, changes: dict[str, Any]) -> StoredVideoUpload:
    """Applies changes to a job the caller is processing, or answers 404/409."""
    try:
        job = store.change(job_id, PROCESSING, changes)
    except UploadStateError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"The job is {error.current}, not processing",
        ) from error
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No job with id {job_id}")
    return job


@router.post("/claim", response_model=VideoJob, responses={204: {"description": "Nothing to process"}})
def claim_job(
    store: Store,
    hub: Hub,
    clock: Now,
    users: Users,
    storage: VideoStorage = Depends(require_video_storage),
    farms: FarmStore = Depends(get_farm_store),
) -> VideoJob | Response:
    """Hands the oldest queued video to the calling worker.

    The worker keeps the job by sending progress at least every
    JOB_LEASE (ten minutes); past that, another worker may claim it.
    204 when there is nothing to do, so a worker can poll cheaply.
    """
    now = clock()
    job = store.claim(now)
    if job is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    lot = None if job.lot_id is None else farms.get_lot(job.lot_id)
    hub.publish("video.updated", job.public(), now, account_id=_account(users, job))
    return VideoJob(
        job_id=job.id, download_url=storage.presign_download(job.storage_key), file_name=job.file_name,
        content_type=job.content_type, size_bytes=job.size_bytes, lot_id=job.lot_id,
        camera_id=None if lot is None else lot.camera_id, attempt=job.attempts,
    )


@router.put("/{job_id}/progress", status_code=status.HTTP_204_NO_CONTENT)
def report_progress(job_id: UUID, progress: JobProgress, store: Store, hub: Hub, clock: Now, users: Users) -> None:
    """Renews the lease and streams the frame's detections to the dashboards.

    The boxes are not stored, for the same reason live ones are not: they
    exist so the dashboard can draw them over the playback in step.
    """
    now = clock()
    changes: dict[str, Any] = {"frames_processed": progress.frames_processed, "heartbeat_at": now}
    if progress.frames_total is not None:
        changes["frames_total"] = progress.frames_total
    if progress.fps is not None:
        changes["fps"] = progress.fps
    job = _advance(store, job_id, changes)
    hub.publish("video.progress", VideoProgress(
        upload_id=job.id, frames_processed=job.frames_processed, frames_total=job.frames_total, fps=job.fps,
        video_time_sec=progress.video_time_sec, detections=progress.detections,
    ), now, account_id=_account(users, job))


@router.post("/{job_id}/complete", response_model=VideoUpload)
def complete_job(
    job_id: UUID,
    summary: VideoAnalysisCreate,
    store: Store,
    hub: Hub,
    clock: Now,
    users: Users,
    settings: Annotated[Settings, Depends(get_app_settings)],
    analyses: VideoAnalysisStore = Depends(get_video_analysis_store),
    events: EventStore = Depends(get_event_store),
    cameras: CameraStore = Depends(get_camera_store),
) -> VideoUpload:
    """Delivers the analysis in the same shape the dashboard already reads.

    Checked before recording, so a worker whose lease was taken over cannot
    file a second analysis for a video someone else is finishing.
    """
    current = store.get(job_id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No job with id {job_id}")
    if current.status is not UploadStatus.PROCESSING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"The job is {current.status}, not processing")
    now = clock()
    account = _account(users, current)
    analysis = record_analysis(summary, analyses, events, now, account if settings.account_isolation else None, cameras)
    job = _advance(store, job_id, {
        "status": UploadStatus.COMPLETED, "analysis_id": analysis.id, "finished_at": now,
        "frames_processed": summary.frames_analyzed,
    })
    hub.publish("video.updated", job.public(), now, account_id=account)
    return job.public()


@router.post("/{job_id}/fail", response_model=VideoUpload)
def fail_job(job_id: UUID, failure: JobFailure, store: Store, hub: Hub, clock: Now, users: Users) -> VideoUpload:
    """Gives up on a video the worker cannot process, such as an undecodable file."""
    now = clock()
    job = _advance(store, job_id, {"status": UploadStatus.FAILED, "error": failure.error, "finished_at": now})
    hub.publish("video.updated", job.public(), now, account_id=_account(users, job))
    return job.public()

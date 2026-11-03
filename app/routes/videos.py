from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import (
    Clock,
    Visible,
    current_user,
    get_camera_store,
    get_clock,
    get_event_store,
    get_video_analysis_store,
)
from app.schemas.video_analysis import VideoAnalysis, VideoAnalysisCreate, VideoAnalysisList
from app.services.camera_store import CameraStore
from app.services.event_store import EventStore
from app.services.video_analysis_recording import record_analysis
from app.services.video_analysis_store import VideoAnalysisStore

router = APIRouter(prefix="/api/videos", tags=["videos"])


@router.post(
    "/analyses", response_model=VideoAnalysis, status_code=201,
    # Declared on the route rather than as a parameter: the handler does not
    # read the user, it only requires that there is one.
    dependencies=[Depends(current_user)],
)
def create_video_analysis(
    data: VideoAnalysisCreate,
    scope: Visible,
    store: VideoAnalysisStore = Depends(get_video_analysis_store),
    event_store: EventStore = Depends(get_event_store),
    cameras: CameraStore = Depends(get_camera_store),
    clock: Clock = Depends(get_clock),
) -> VideoAnalysis:
    """Stores a video analysis and logs an event for each cattle alert.

    Requires a signed-in user, not the AI key. This endpoint is called by the
    browser, and AI_API_KEY cannot live in a browser bundle -- the frontend's
    own .env.example says so. Until this was protected, an unauthenticated
    caller could write into the shared events feed through it, which is the one
    path into that table that asked for nothing at all.
    """
    return record_analysis(data, store, event_store, clock(), scope.account_id, cameras)


@router.get("/analyses", response_model=VideoAnalysisList)
def list_video_analyses(
    scope: Visible,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    store: VideoAnalysisStore = Depends(get_video_analysis_store),
) -> VideoAnalysisList:
    items = store.list(limit, scope.account_id)
    return VideoAnalysisList(items=items, total=len(items))


@router.get("/analyses/{analysis_id}", response_model=VideoAnalysis)
def get_video_analysis(
    analysis_id: UUID,
    scope: Visible,
    store: VideoAnalysisStore = Depends(get_video_analysis_store),
) -> VideoAnalysis:
    analysis = store.get(analysis_id)
    if analysis is None or not scope.sees_account(store.account_of(analysis_id)):
        raise HTTPException(status_code=404, detail="Video analysis not found")
    return analysis

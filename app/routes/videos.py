from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import Clock, get_clock, get_event_store, get_video_analysis_store
from app.schemas.event import AIEventCreate, EventType
from app.schemas.video_analysis import (
    VideoAlertClass,
    VideoAnalysis,
    VideoAnalysisCreate,
    VideoAnalysisList,
)
from app.services.event_store import EventStore
from app.services.video_analysis_store import VideoAnalysisStore

router = APIRouter(prefix="/api/videos", tags=["videos"])
VIDEO_CAMERA_ID = "video-upload"


@router.post("/analyses", response_model=VideoAnalysis, status_code=201)
def create_video_analysis(
    data: VideoAnalysisCreate,
    store: VideoAnalysisStore = Depends(get_video_analysis_store),
    event_store: EventStore = Depends(get_event_store),
    clock: Clock = Depends(get_clock),
) -> VideoAnalysis:
    created_at = clock()
    analysis = store.add(data, created_at)
    for alert in data.alerts:
        if alert.cls is VideoAlertClass.CATTLE:
            # time_sec is relative to an unknown recording time, so the upload time is the event timestamp.
            event_store.add(AIEventCreate(
                event_type=EventType.CATTLE_OUT_OF_ZONE, camera_id=VIDEO_CAMERA_ID,
                detected_object="cow", confidence=data.avg_confidence, timestamp=created_at,
            ), created_at)
    return analysis


@router.get("/analyses", response_model=VideoAnalysisList)
def list_video_analyses(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    store: VideoAnalysisStore = Depends(get_video_analysis_store),
) -> VideoAnalysisList:
    items = store.list(limit)
    return VideoAnalysisList(items=items, total=len(items))


@router.get("/analyses/{analysis_id}", response_model=VideoAnalysis)
def get_video_analysis(
    analysis_id: UUID,
    store: VideoAnalysisStore = Depends(get_video_analysis_store),
) -> VideoAnalysis:
    analysis = store.get(analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Video analysis not found")
    return analysis

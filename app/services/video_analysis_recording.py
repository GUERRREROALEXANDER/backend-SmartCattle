"""Recording a finished video analysis, whoever produced it.

Shared by the browser's simulated analysis and the AI worker's real one, so
both put the same rows in the same tables.
"""

from datetime import datetime

from app.schemas.event import AIEventCreate, EventType
from app.schemas.video_analysis import VideoAlertClass, VideoAnalysis, VideoAnalysisCreate
from app.services.event_store import EventStore
from app.services.video_analysis_store import VideoAnalysisStore

#: The camera id recorded footage is filed under in the events feed.
VIDEO_CAMERA_ID = "video-upload"


def record_analysis(
    data: VideoAnalysisCreate, analyses: VideoAnalysisStore, events: EventStore, now: datetime,
) -> VideoAnalysis:
    """Stores the analysis and one feed event per cattle alert in it.

    The events go through the store directly and never through
    POST /api/ai/events, so they raise no live security alert: the footage
    describes something that already happened.
    """
    analysis = analyses.add(data, now)
    for alert in data.alerts:
        if alert.cls is VideoAlertClass.CATTLE:
            # time_sec is relative to an unknown recording time, so the upload time is the event timestamp.
            events.add(AIEventCreate(
                event_type=EventType.CATTLE_OUT_OF_ZONE, camera_id=VIDEO_CAMERA_ID,
                detected_object="cow", confidence=data.avg_confidence, timestamp=now,
            ), now)
    return analysis

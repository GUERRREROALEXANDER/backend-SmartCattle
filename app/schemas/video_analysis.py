from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class VideoAlertClass(StrEnum):
    CATTLE = "cattle"
    PERSON = "person"


class VideoAnalysisAlertCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: int = Field(ge=0, description="Non-negative tracker identifier")
    cls: VideoAlertClass = Field(description="Detected object class")
    time_sec: float = Field(ge=0, allow_inf_nan=False, description="Alert position in the video in seconds")


class VideoAnalysisAlert(VideoAnalysisAlertCreate):
    id: int = Field(description="Backend alert identifier")


class VideoAnalysisFields(BaseModel):
    """Everything an analysis carries except its alerts.

    The alert list is the one field whose item type differs between the
    request and the response, so it is declared by each of them rather than
    inherited and then narrowed: list is invariant, and overriding it means
    the subclass is not substitutable for its parent.
    """

    model_config = ConfigDict(extra="forbid")

    video_name: str = Field(min_length=1, max_length=200)
    duration_sec: float = Field(ge=0, allow_inf_nan=False)
    frames_analyzed: int = Field(ge=0)
    max_cattle: int = Field(ge=0)
    max_people: int = Field(ge=0)
    avg_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    outside_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)

    @field_validator("video_name", mode="before")
    @classmethod
    def strip_video_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class VideoAnalysisCreate(VideoAnalysisFields):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "video_name": "pasture-test.mp4", "duration_sec": 12.5, "frames_analyzed": 125,
            "max_cattle": 4, "max_people": 1,
            "alerts": [{"track_id": 7, "cls": "cattle", "time_sec": 3.2}],
            "avg_confidence": 0.91, "outside_ratio": 0.25,
        }]},
    )

    alerts: list[VideoAnalysisAlertCreate] = Field(max_length=500)


class VideoAnalysis(VideoAnalysisFields):
    # Its own example: the inherited one described a request body, so the
    # response was documented without id, alert_count or created_at.
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "video_name": "pasture-test.mp4", "duration_sec": 12.5, "frames_analyzed": 125,
            "max_cattle": 4, "max_people": 1,
            "alerts": [{"id": 1, "track_id": 7, "cls": "cattle", "time_sec": 3.2}],
            "avg_confidence": 0.91, "outside_ratio": 0.25,
            "id": "123e4567-e89b-42d3-a456-426614174000",
            "alert_count": 1,
            "created_at": "2026-10-27T15:30:01Z",
        }]},
    )

    id: UUID = Field(description="Backend video analysis identifier")
    alerts: list[VideoAnalysisAlert]
    alert_count: int = Field(ge=0)
    created_at: AwareDatetime = Field(description="UTC receipt time assigned by the backend")


class VideoAnalysisList(BaseModel):
    items: list[VideoAnalysis]
    total: int

from enum import Enum
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class VideoAlertClass(str, Enum):
    CATTLE = "cattle"
    PERSON = "person"


class VideoAnalysisAlertCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: int = Field(ge=0, description="Non-negative tracker identifier")
    cls: VideoAlertClass = Field(description="Detected object class")
    time_sec: float = Field(ge=0, allow_inf_nan=False, description="Alert position in the video in seconds")


class VideoAnalysisAlert(VideoAnalysisAlertCreate):
    id: int = Field(description="Backend alert identifier")


class VideoAnalysisCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "video_name": "pasture-test.mp4", "duration_sec": 12.5, "frames_analyzed": 125,
            "max_cattle": 4, "max_people": 1,
            "alerts": [{"track_id": 7, "cls": "cattle", "time_sec": 3.2}],
            "avg_confidence": 0.91, "outside_ratio": 0.25,
        }]},
    )

    video_name: str = Field(min_length=1, max_length=200)
    duration_sec: float = Field(ge=0, allow_inf_nan=False)
    frames_analyzed: int = Field(ge=0)
    max_cattle: int = Field(ge=0)
    max_people: int = Field(ge=0)
    alerts: list[VideoAnalysisAlertCreate] = Field(max_length=500)
    avg_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    outside_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)

    @field_validator("video_name", mode="before")
    @classmethod
    def strip_video_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class VideoAnalysis(VideoAnalysisCreate):
    id: UUID = Field(description="Backend video analysis identifier")
    alerts: list[VideoAnalysisAlert]
    alert_count: int = Field(ge=0)
    created_at: AwareDatetime = Field(description="UTC receipt time assigned by the backend")


class VideoAnalysisList(BaseModel):
    items: list[VideoAnalysis]
    total: int

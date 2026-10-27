from pydantic import BaseModel, Field


class MetricsSummary(BaseModel):
    animals_inside: int | None = Field(
        description="Estimated cattle inside from the latest video analysis; null when no analysis exists",
    )
    animals_outside: int | None = Field(
        description="Distinct outside cattle tracks from the latest video analysis, capped at max_cattle",
    )
    alerts_today: int = Field(description="Stored events detected since 00:00 UTC today")
    alerts_since: int | None = Field(description="Stored events detected at or after the optional since timestamp")
    detections_per_minute: float = Field(description="Stored events in the trailing hour divided by 60")

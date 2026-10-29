from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Normalised coordinate. The zone is stored from 0 to 1 and not in pixels: a
#: zone in pixels is tied to the resolution it was drawn at, so swapping the
#: camera, or the AI service processing at 640x360 what was configured at
#: 1920x1080, moves the zone without anyone touching it.
Normalised = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]

#: Fewest vertices that enclose an area, and a ceiling that a hand-drawn zone
#: never reaches.
MIN_OUTLINE_POINTS = 3
MAX_OUTLINE_POINTS = 64


class ZonePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: Normalised
    y: Normalised


class ZoneBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    x_min: Normalised
    y_min: Normalised
    x_max: Normalised
    y_max: Normalised
    outline: list[ZonePoint] | None = Field(
        default=None, min_length=MIN_OUTLINE_POINTS, max_length=MAX_OUTLINE_POINTS,
        description=(
            "Optional polygon. When present it defines the zone and the rectangle "
            "is its bounding box."
        ),
    )
    active: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def rectangle_has_area(self) -> Self:
        """Rejects an inverted or flat rectangle.

        Corners the wrong way round make the whole frame read as outside the
        safe zone, which fires one alert per visible animal -- a failure that
        looks like a camera problem rather than a configuration mistake.
        """
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError("the zone needs x_min < x_max and y_min < y_max")
        return self

    @model_validator(mode="after")
    def outline_fits_the_bounding_box(self) -> Self:
        """The rectangle has to be the polygon's bounding box, not an unrelated box.

        The two representations are kept together on purpose: the rectangle is
        the cheap rejection test applied before the polygon test. If it does
        not contain the polygon, that shortcut discards points that are
        actually inside, and animals inside the zone are reported as outside.
        """
        if self.outline is None:
            return self
        xs = [point.x for point in self.outline]
        ys = [point.y for point in self.outline]
        if min(xs) < self.x_min or max(xs) > self.x_max or min(ys) < self.y_min or max(ys) > self.y_max:
            raise ValueError("the outline must lie inside x_min..x_max and y_min..y_max")
        return self


class ZoneCreate(ZoneBase):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "name": "Safe zone", "x_min": 0.1, "y_min": 0.1, "x_max": 0.9, "y_max": 0.9,
        }]},
    )


class ZoneUpdate(BaseModel):
    """A partial update. Omitted fields keep their values.

    The geometry is validated as a whole, so a caller changing one corner has
    to send the rectangle: a single corner cannot be checked against the
    others without knowing them.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    active: bool | None = None
    geometry: ZoneCreate | None = Field(
        default=None,
        description="The full rectangle, and the outline, replaced together",
    )


class Zone(ZoneBase):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": 1, "camera_id": "camera-01", "name": "Safe zone",
        "x_min": 0.1, "y_min": 0.1, "x_max": 0.9, "y_max": 0.9,
        "outline": None, "active": True, "created_at": "2026-10-01T08:00:00Z",
    }]})

    id: int
    camera_id: str
    created_at: datetime


class ZoneList(BaseModel):
    items: list[Zone]
    total: int

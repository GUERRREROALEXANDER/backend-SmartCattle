from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Fewest vertices that enclose an area, and a ceiling well above what anyone
#: traces by hand on a map, so a pasted GeoJSON with thousands of points is
#: refused instead of stored.
MIN_BOUNDARY_POINTS = 3
MAX_BOUNDARY_POINTS = 100

Name = Annotated[str, Field(min_length=1, max_length=120)]
CameraName = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
#: Upper bounds are sanity limits, not business rules: they turn a unit
#: mistake (square metres typed into hectares) into a 422 instead of a farm
#: larger than a country.
Hectares = Annotated[float, Field(gt=0, le=1_000_000, allow_inf_nan=False)]
SquareMeters = Annotated[float, Field(gt=0, le=100_000_000, allow_inf_nan=False)]
Meters = Annotated[float, Field(gt=0, le=100_000, allow_inf_nan=False)]
Capacity = Annotated[int, Field(ge=0, le=100_000)]


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _blank_to_none(value: object) -> object:
    value = _strip(value)
    return None if value == "" else value


class GeoPoint(BaseModel):
    """A WGS84 position.

    Farms and lots are geographic, unlike zones, which are normalised to one
    camera's frame: a lot exists on the ground whether or not a camera sees it.
    """

    model_config = ConfigDict(extra="forbid")

    latitude: Latitude
    longitude: Longitude


class LotKind(StrEnum):
    PADDOCK = "paddock"
    STABLE = "stable"
    PEN = "pen"
    PASTURE = "pasture"


class _FarmFields(BaseModel):
    @field_validator("name", mode="before", check_fields=False)
    @classmethod
    def strip_name(cls, value: object) -> object:
        return _strip(value)

    @field_validator("location", mode="before", check_fields=False)
    @classmethod
    def blank_location_is_none(cls, value: object) -> object:
        return _blank_to_none(value)


class FarmCreate(_FarmFields):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "name": "La Esperanza", "location": "Montería, Córdoba", "area_hectares": 120.5,
            "coordinates": {"latitude": 8.75, "longitude": -75.88},
        }]},
    )

    name: Name
    location: str | None = Field(default=None, max_length=200)
    area_hectares: Hectares | None = None
    coordinates: GeoPoint | None = None


class FarmUpdate(_FarmFields):
    """A partial update. Omitted fields keep their value; an explicit null clears it.

    Coordinates are one object, so latitude and longitude cannot be changed
    one without the other into a point that is neither the old place nor the
    new one.
    """

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    location: str | None = Field(default=None, max_length=200)
    area_hectares: Hectares | None = None
    coordinates: GeoPoint | None = None

    @model_validator(mode="after")
    def changes_something(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("the update must change at least one field")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be cleared")
        return self


class Farm(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": 1, "owner_id": 1, "name": "La Esperanza", "location": "Montería, Córdoba",
        "area_hectares": 120.5, "coordinates": {"latitude": 8.75, "longitude": -75.88},
        "lot_count": 3, "created_at": "2026-10-31T08:00:00Z", "updated_at": "2026-10-31T08:00:00Z",
    }]})

    id: int
    owner_id: int
    name: str
    location: str | None
    area_hectares: float | None
    coordinates: GeoPoint | None
    lot_count: int
    created_at: datetime
    updated_at: datetime


class FarmList(BaseModel):
    items: list[Farm]
    total: int


#: How far a declared area may stray from length x width before it is refused.
#: Loose enough for a figure rounded by hand, tight enough to catch a typo.
AREA_TOLERANCE = 0.01


def _check_dimensions(length: float | None, width: float | None, area: float | None) -> float | None:
    """The area the lot ends up with, or ValueError.

    Length and width come together: one alone describes no shape. With both,
    the area follows from them; a declared area must agree with that.
    """
    if (length is None) != (width is None):
        raise ValueError("length_meters and width_meters must be given together")
    if length is None or width is None:
        return area
    derived = length * width
    if area is not None and abs(area - derived) > derived * AREA_TOLERANCE:
        raise ValueError(
            f"area_square_meters ({area:g}) does not match length_meters x width_meters ({derived:g})",
        )
    return derived if area is None else area


class _LotFields(BaseModel):
    @field_validator("name", mode="before", check_fields=False)
    @classmethod
    def strip_name(cls, value: object) -> object:
        return _strip(value)


Boundary = Annotated[
    list[GeoPoint],
    Field(
        min_length=MIN_BOUNDARY_POINTS, max_length=MAX_BOUNDARY_POINTS,
        description="Polygon traced on the map; the first point is not repeated at the end.",
    ),
]


class LotCreate(_LotFields):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "name": "North paddock", "kind": "paddock", "length_meters": 250, "width_meters": 100,
            "capacity": 40, "camera_id": "camera-01",
        }]},
    )

    name: Name
    kind: LotKind = Field(
        default=LotKind.PADDOCK,
        description="Defaults to paddock, so a lot can be registered with nothing but its name.",
    )
    area_square_meters: SquareMeters | None = Field(
        default=None, description="Derived from length x width when those are given.",
    )
    length_meters: Meters | None = None
    width_meters: Meters | None = None
    capacity: Capacity | None = None
    boundary: Boundary | None = None
    camera_id: CameraName | None = Field(
        default=None, description="Camera watching this lot, by its API id.",
    )
    active: bool = True

    @model_validator(mode="after")
    def area_matches_dimensions(self) -> Self:
        self.area_square_meters = _check_dimensions(self.length_meters, self.width_meters, self.area_square_meters)
        return self


class LotUpdate(_LotFields):
    """A partial update with the same omitted-versus-null rule as FarmUpdate.

    Length and width are changed together. New dimensions bring their area
    with them; a new area sent alone clears the dimensions, because a lot
    whose area no longer equals length x width is not that rectangle any more.
    """

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    kind: LotKind | None = None
    area_square_meters: SquareMeters | None = None
    length_meters: Meters | None = None
    width_meters: Meters | None = None
    capacity: Capacity | None = None
    boundary: Boundary | None = None
    camera_id: CameraName | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def changes_something(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("the update must change at least one field")
        for required in ("name", "kind", "active"):
            if required in self.model_fields_set and getattr(self, required) is None:
                raise ValueError(f"{required} cannot be cleared")
        changed = self.model_fields_set
        if ("length_meters" in changed) != ("width_meters" in changed):
            raise ValueError("length_meters and width_meters must be changed together")
        # Assigning a field marks it as sent, so each assignment below is one
        # the update really makes.
        if "length_meters" in changed:
            area = self.area_square_meters if "area_square_meters" in changed else None
            derived = _check_dimensions(self.length_meters, self.width_meters, area)
            if self.length_meters is not None:
                self.area_square_meters = derived
        elif "area_square_meters" in changed:
            self.length_meters = None
            self.width_meters = None
        return self


class Lot(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": 1, "farm_id": 1, "name": "North paddock", "kind": "paddock",
        "area_square_meters": 25000, "length_meters": 250, "width_meters": 100,
        "capacity": 40, "boundary": None, "camera_id": "camera-01",
        "active": True, "created_at": "2026-10-31T08:00:00Z", "updated_at": "2026-10-31T08:00:00Z",
    }]})

    id: int
    farm_id: int
    name: str
    kind: LotKind
    area_square_meters: float | None
    length_meters: float | None = None
    width_meters: float | None = None
    capacity: int | None
    boundary: list[GeoPoint] | None
    camera_id: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class LotList(BaseModel):
    items: list[Lot]
    total: int

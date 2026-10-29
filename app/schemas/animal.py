from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class AnimalStatus(StrEnum):
    ACTIVE = "active"
    # An administrative decision: the animal left the farm, was sold, died.
    INACTIVE = "inactive"
    # The animal should be here and is not. This is the state the whole system
    # exists to detect, and it is not the same as inactive.
    LOST = "lost"


#: The ear tag is the primary key in the shared schema, so it is also the
#: identifier in the URL. Restricted to what survives a path segment without
#: escaping: a tag with a slash in it would split the route.
TagPath = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")]


class AnimalBase(BaseModel):
    name: str | None = Field(
        default=None, max_length=120,
        description="So staff recognise which animal an alert is about",
    )
    camera_id: str | None = Field(
        default=None, max_length=64,
        description="Camera the animal was last seen by; null when unknown",
    )

    @field_validator("name", "camera_id", mode="before")
    @classmethod
    def blank_to_none(cls, value: object) -> object:
        # An empty string is how a form submits "not set". Storing it would
        # make "" and null two different ways to say the same thing.
        if isinstance(value, str):
            return value.strip() or None
        return value


class AnimalCreate(AnimalBase):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [
            {"tag": "A-441", "name": "Lucera", "camera_id": "camera-01"}
        ]},
    )

    tag: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$",
        description="Ear tag. The identity of the animal, and its primary key",
    )
    status: AnimalStatus = AnimalStatus.ACTIVE

    @field_validator("tag", mode="before")
    @classmethod
    def strip_tag(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class AnimalUpdate(AnimalBase):
    """A partial update. Every field is optional, and the tag is not among them.

    Changing a tag is replacing the animal's identity, which cascades to its
    whole event history. That is a deliberate operation and not something a
    general update endpoint should do by accident, so it is not offered here.
    """

    model_config = ConfigDict(extra="forbid")

    status: AnimalStatus | None = None


class Animal(AnimalBase):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "tag": "A-441", "name": "Lucera", "status": "active",
        "camera_id": "camera-01", "last_detection": "2026-10-29T12:00:00Z",
        "created_at": "2026-10-01T08:00:00Z", "updated_at": "2026-10-29T12:00:00Z",
    }]})

    tag: str
    status: AnimalStatus
    last_detection: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AnimalList(BaseModel):
    items: list[Animal]
    total: int


class AnimalDetection(BaseModel):
    """What the AI service reports when it recognises an individual animal.

    Not used yet: the vision service identifies a class ('cow'), not an
    individual. The endpoint exists so that when it can, the contract is
    already the one the shared schema was designed for.
    """

    model_config = ConfigDict(extra="forbid")

    camera_id: str | None = Field(default=None, max_length=64)
    detected_at: AwareDatetime

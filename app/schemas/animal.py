from pydantic import BaseModel, Field


class Animal(BaseModel):
    id: str
    tag: str = Field(description="Ear tag identifier")


class AnimalList(BaseModel):
    items: list[Animal]
    total: int

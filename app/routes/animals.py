from fastapi import APIRouter

from app.schemas.animal import AnimalList

router = APIRouter(tags=["animals"])


@router.get("/api/animals", response_model=AnimalList)
def list_animals() -> AnimalList:
    return AnimalList(items=[], total=0)

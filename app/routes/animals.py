from fastapi import APIRouter, Depends

from app.core.security import verify_read_key
from app.schemas.animal import AnimalList

router = APIRouter(tags=["animals"])


@router.get(
    "/api/animals", response_model=AnimalList, dependencies=[Depends(verify_read_key)]
)
def list_animals() -> AnimalList:
    return AnimalList(items=[], total=0)

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.dependencies import Clock, CurrentUser, OwnerUser, get_clock, get_farm_store
from app.schemas.farm import Farm, FarmCreate, FarmList, FarmUpdate, Lot, LotCreate, LotList, LotUpdate
from app.services.farm_store import FarmHasLotsError, FarmNameTakenError, FarmStore, LotNameTakenError
from app.services.user_store import StoredUser

router = APIRouter(tags=["farms"])

FarmId = Annotated[int, Path(ge=1)]
LotId = Annotated[int, Path(ge=1)]
Store = Annotated[FarmStore, Depends(get_farm_store)]
Now = Annotated[Clock, Depends(get_clock)]


def _farm_or_404(store: FarmStore, farm_id: int) -> Farm:
    farm = store.get_farm(farm_id)
    if farm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No farm with id {farm_id}")
    return farm


def _lot_or_404(store: FarmStore, lot_id: int) -> Lot:
    lot = store.get_lot(lot_id)
    if lot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No lot with id {lot_id}")
    return lot


def _owned_farm(store: FarmStore, farm_id: int, user: StoredUser) -> Farm:
    """The farm, if the caller is the owner who registered it.

    The owner role lets someone configure farms; it does not let them
    reconfigure another owner's. 403 rather than 404 because farms are
    readable by every signed-in user, so hiding this one would hide nothing.
    """
    farm = _farm_or_404(store, farm_id)
    if farm.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the farm's owner can change it")
    return farm


def _farm_name_conflict(name: str | None) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"You already have a farm named {name}")


def _lot_name_conflict(name: str | None) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"This farm already has a lot named {name}")


@router.get("/api/farms", response_model=FarmList)
def list_farms(_user: CurrentUser, store: Store) -> FarmList:
    items = store.list_farms()
    return FarmList(items=items, total=len(items))


@router.post("/api/farms", response_model=Farm, status_code=status.HTTP_201_CREATED)
def create_farm(data: FarmCreate, user: OwnerUser, store: Store, clock: Now) -> Farm:
    try:
        return store.create_farm(user.id, data, clock())
    except FarmNameTakenError as error:
        raise _farm_name_conflict(data.name) from error


@router.get("/api/farms/{farm_id}", response_model=Farm)
def get_farm(farm_id: FarmId, _user: CurrentUser, store: Store) -> Farm:
    return _farm_or_404(store, farm_id)


@router.patch("/api/farms/{farm_id}", response_model=Farm)
def update_farm(farm_id: FarmId, data: FarmUpdate, user: OwnerUser, store: Store, clock: Now) -> Farm:
    _owned_farm(store, farm_id, user)
    try:
        updated = store.update_farm(farm_id, data, clock())
    except FarmNameTakenError as error:
        raise _farm_name_conflict(data.name) from error
    return _farm_or_404(store, farm_id) if updated is None else updated


@router.delete("/api/farms/{farm_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_farm(farm_id: FarmId, user: OwnerUser, store: Store) -> None:
    """Deletes an empty farm.

    Refused while it has lots: the database would cascade them away, and a
    farm's lot layout is hours of drawing on a map that one misclick should
    not be able to erase.
    """
    _owned_farm(store, farm_id, user)
    try:
        store.delete_farm(farm_id)
    except FarmHasLotsError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Delete or move the farm's lots before deleting it",
        ) from error


@router.get("/api/farms/{farm_id}/lots", response_model=LotList)
def list_lots(farm_id: FarmId, _user: CurrentUser, store: Store, active: bool | None = None) -> LotList:
    _farm_or_404(store, farm_id)
    items = store.list_lots(farm_id, active)
    return LotList(items=items, total=len(items))


@router.post("/api/farms/{farm_id}/lots", response_model=Lot, status_code=status.HTTP_201_CREATED)
def create_lot(farm_id: FarmId, data: LotCreate, user: OwnerUser, store: Store, clock: Now) -> Lot:
    _owned_farm(store, farm_id, user)
    try:
        return store.create_lot(farm_id, data, clock())
    except LotNameTakenError as error:
        raise _lot_name_conflict(data.name) from error


@router.get("/api/lots/{lot_id}", response_model=Lot)
def get_lot(lot_id: LotId, _user: CurrentUser, store: Store) -> Lot:
    return _lot_or_404(store, lot_id)


@router.patch("/api/lots/{lot_id}", response_model=Lot)
def update_lot(lot_id: LotId, data: LotUpdate, user: OwnerUser, store: Store, clock: Now) -> Lot:
    lot = _lot_or_404(store, lot_id)
    _owned_farm(store, lot.farm_id, user)
    try:
        updated = store.update_lot(lot_id, data, clock())
    except LotNameTakenError as error:
        raise _lot_name_conflict(data.name) from error
    return _lot_or_404(store, lot_id) if updated is None else updated


@router.delete("/api/lots/{lot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lot(lot_id: LotId, user: OwnerUser, store: Store) -> None:
    lot = _lot_or_404(store, lot_id)
    _owned_farm(store, lot.farm_id, user)
    store.delete_lot(lot_id)

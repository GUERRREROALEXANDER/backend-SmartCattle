from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.dependencies import AuditTrail, Clock, CurrentUser, OwnerUser, Scope, Visible, get_clock, get_farm_store
from app.schemas.audit import AuditAction
from app.schemas.farm import Farm, FarmCreate, FarmList, FarmUpdate, Lot, LotCreate, LotList, LotUpdate
from app.services.farm_store import FarmHasLotsError, FarmNameTakenError, FarmStore, LotNameTakenError
from app.services.user_store import StoredUser

router = APIRouter(tags=["farms"])

FarmId = Annotated[int, Path(ge=1)]
LotId = Annotated[int, Path(ge=1)]
Store = Annotated[FarmStore, Depends(get_farm_store)]
Now = Annotated[Clock, Depends(get_clock)]


def _farm_or_404(store: FarmStore, farm_id: int, scope: Scope) -> Farm:
    farm = store.get_farm(farm_id)
    if farm is None or not scope.sees_user(farm.owner_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No farm with id {farm_id}")
    return farm


def _lot_or_404(store: FarmStore, lot_id: int, scope: Scope) -> Lot:
    lot = store.get_lot(lot_id)
    farm = None if lot is None else store.get_farm(lot.farm_id)
    if lot is None or farm is None or not scope.sees_user(farm.owner_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No lot with id {lot_id}")
    return lot


def _owned_farm(store: FarmStore, farm_id: int, user: StoredUser, scope: Scope) -> Farm:
    """The farm, if the caller is the owner who registered it.

    The owner role lets someone configure farms; it does not let them
    reconfigure another owner's. 403 between owners of one account, who can
    read each other's farms; 404 for another account's, which do not exist here.
    """
    farm = _farm_or_404(store, farm_id, scope)
    if farm.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the farm's owner can change it")
    return farm


def _changed(data: FarmUpdate | LotUpdate) -> dict[str, object]:
    return {"changed": sorted(data.model_fields_set)}


def _farm_name_conflict(name: str | None) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"You already have a farm named {name}")


def _lot_name_conflict(name: str | None) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"This farm already has a lot named {name}")


def _check_lot_camera(data: LotCreate | LotUpdate, scope: Scope) -> None:
    if data.camera_id is not None and not scope.sees_camera(data.camera_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No camera with id {data.camera_id}")


@router.get("/api/farms", response_model=FarmList)
def list_farms(_user: CurrentUser, scope: Visible, store: Store) -> FarmList:
    items = [farm for farm in store.list_farms() if scope.sees_user(farm.owner_id)]
    return FarmList(items=items, total=len(items))


@router.post("/api/farms", response_model=Farm, status_code=status.HTTP_201_CREATED)
def create_farm(data: FarmCreate, user: OwnerUser, store: Store, clock: Now, audit: AuditTrail) -> Farm:
    try:
        farm = store.create_farm(user.id, data, clock())
    except FarmNameTakenError as error:
        raise _farm_name_conflict(data.name) from error
    audit.record(
        AuditAction.FARM_CREATED, actor_id=user.id, actor_email=user.email,
        target_type="farm", target_id=farm.id, detail={"name": farm.name},
    )
    return farm


@router.get("/api/farms/{farm_id}", response_model=Farm)
def get_farm(farm_id: FarmId, _user: CurrentUser, scope: Visible, store: Store) -> Farm:
    return _farm_or_404(store, farm_id, scope)


@router.patch("/api/farms/{farm_id}", response_model=Farm)
def update_farm(
    farm_id: FarmId, data: FarmUpdate, user: OwnerUser, scope: Visible, store: Store, clock: Now, audit: AuditTrail,
) -> Farm:
    _owned_farm(store, farm_id, user, scope)
    try:
        updated = store.update_farm(farm_id, data, clock())
    except FarmNameTakenError as error:
        raise _farm_name_conflict(data.name) from error
    audit.record(
        AuditAction.FARM_UPDATED, actor_id=user.id, actor_email=user.email,
        target_type="farm", target_id=farm_id, detail=_changed(data),
    )
    return _farm_or_404(store, farm_id, scope) if updated is None else updated


@router.delete("/api/farms/{farm_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_farm(farm_id: FarmId, user: OwnerUser, scope: Visible, store: Store, audit: AuditTrail) -> None:
    """Deletes an empty farm.

    Refused while it has lots: the database would cascade them away, and a
    farm's lot layout is hours of drawing on a map that one misclick should
    not be able to erase.
    """
    farm = _owned_farm(store, farm_id, user, scope)
    try:
        store.delete_farm(farm_id)
    except FarmHasLotsError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Delete or move the farm's lots before deleting it",
        ) from error
    audit.record(
        AuditAction.FARM_DELETED, actor_id=user.id, actor_email=user.email,
        target_type="farm", target_id=farm_id, detail={"name": farm.name},
    )


@router.get("/api/farms/{farm_id}/lots", response_model=LotList)
def list_lots(
    farm_id: FarmId, _user: CurrentUser, scope: Visible, store: Store, active: bool | None = None,
) -> LotList:
    _farm_or_404(store, farm_id, scope)
    items = store.list_lots(farm_id, active)
    return LotList(items=items, total=len(items))


@router.post("/api/farms/{farm_id}/lots", response_model=Lot, status_code=status.HTTP_201_CREATED)
def create_lot(
    farm_id: FarmId, data: LotCreate, user: OwnerUser, scope: Visible, store: Store, clock: Now, audit: AuditTrail,
) -> Lot:
    _owned_farm(store, farm_id, user, scope)
    _check_lot_camera(data, scope)
    try:
        lot = store.create_lot(farm_id, data, clock())
    except LotNameTakenError as error:
        raise _lot_name_conflict(data.name) from error
    audit.record(
        AuditAction.LOT_CREATED, actor_id=user.id, actor_email=user.email,
        target_type="lot", target_id=lot.id, detail={"farm_id": farm_id, "name": lot.name},
    )
    return lot


@router.get("/api/lots/{lot_id}", response_model=Lot)
def get_lot(lot_id: LotId, _user: CurrentUser, scope: Visible, store: Store) -> Lot:
    return _lot_or_404(store, lot_id, scope)


@router.patch("/api/lots/{lot_id}", response_model=Lot)
def update_lot(
    lot_id: LotId, data: LotUpdate, user: OwnerUser, scope: Visible, store: Store, clock: Now, audit: AuditTrail,
) -> Lot:
    lot = _lot_or_404(store, lot_id, scope)
    _owned_farm(store, lot.farm_id, user, scope)
    _check_lot_camera(data, scope)
    try:
        updated = store.update_lot(lot_id, data, clock())
    except LotNameTakenError as error:
        raise _lot_name_conflict(data.name) from error
    audit.record(
        AuditAction.LOT_UPDATED, actor_id=user.id, actor_email=user.email,
        target_type="lot", target_id=lot_id, detail=_changed(data),
    )
    return _lot_or_404(store, lot_id, scope) if updated is None else updated


@router.delete("/api/lots/{lot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lot(lot_id: LotId, user: OwnerUser, scope: Visible, store: Store, audit: AuditTrail) -> None:
    lot = _lot_or_404(store, lot_id, scope)
    _owned_farm(store, lot.farm_id, user, scope)
    store.delete_lot(lot_id)
    audit.record(
        AuditAction.LOT_DELETED, actor_id=user.id, actor_email=user.email,
        target_type="lot", target_id=lot_id, detail={"farm_id": lot.farm_id, "name": lot.name},
    )

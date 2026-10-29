from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.dependencies import (
    Clock,
    current_user,
    get_clock,
    get_zone_store,
    require_owner,
)
from app.schemas.zone import Zone, ZoneCreate, ZoneList, ZoneUpdate
from app.services.zone_store import ZoneNameTakenError, ZoneStore

router = APIRouter(prefix="/api/zones", tags=["zones"])

# Declared on the routes rather than as a parameter: these handlers do not read
# the user, they only require one with the right role.
OWNER_ONLY = [Depends(require_owner)]
SIGNED_IN = [Depends(current_user)]

CameraId = Annotated[str, Query(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
CameraIdPath = Annotated[str, Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
ZoneId = Annotated[int, Path(ge=1)]


def _found(zone: Zone | None, zone_id: int) -> Zone:
    if zone is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No zone with id {zone_id}",
        )
    return zone


@router.get("", response_model=ZoneList, dependencies=SIGNED_IN)
def list_zones(
    camera_id: CameraId | None = None,
    store: ZoneStore = Depends(get_zone_store),
) -> ZoneList:
    """The configured safe zones, optionally for one camera.

    Behind authentication: a zone describes where the farm's animals are meant
    to be, which is not something to publish to an anonymous caller.
    """
    items = store.list(camera_id)
    return ZoneList(items=items, total=len(items))


@router.get("/{zone_id}", response_model=Zone, dependencies=SIGNED_IN)
def get_zone(zone_id: ZoneId, store: ZoneStore = Depends(get_zone_store)) -> Zone:
    return _found(store.get(zone_id), zone_id)


@router.post(
    "/camera/{camera_id}", response_model=Zone,
    status_code=status.HTTP_201_CREATED, dependencies=OWNER_ONLY,
)
def create_zone(
    camera_id: CameraIdPath,
    data: ZoneCreate,
    store: ZoneStore = Depends(get_zone_store),
    clock: Clock = Depends(get_clock),
) -> Zone:
    """Configures a safe zone for a camera. Owner only.

    The camera is in the path rather than the body because a zone belongs to
    one camera and cannot be moved between them: zones.camera_id is NOT NULL
    and CASCADEs with the camera, so moving a zone is deleting and recreating
    it.

    Owner only, and this is the write with the widest effect in the API: the
    zone is what the AI service compares every detection against, so a bad one
    either floods the farm with false alerts or stops reporting real escapes.
    """
    try:
        return store.create(camera_id, data, clock())
    except ZoneNameTakenError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Camera {camera_id} already has a zone named {data.name}",
        ) from error


@router.patch("/{zone_id}", response_model=Zone, dependencies=OWNER_ONLY)
def update_zone(
    zone_id: ZoneId,
    data: ZoneUpdate,
    store: ZoneStore = Depends(get_zone_store),
) -> Zone:
    """Partially updates a zone. Owner only.

    The geometry is replaced as a whole when sent, because a single corner
    cannot be validated against the others without knowing them.
    """
    return _found(store.update(zone_id, data), zone_id)


@router.delete("/{zone_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=OWNER_ONLY)
def delete_zone(zone_id: ZoneId, store: ZoneStore = Depends(get_zone_store)) -> None:
    """Removes a zone. Owner only.

    A real delete, unlike animals. A zone is configuration and not history:
    nothing references it that would be orphaned, and keeping a retired
    rectangle around would leave two zones for one camera with no way to say
    which one the AI service should use. Deactivating is what `active` is for
    when the zone should be kept.
    """
    if not store.delete(zone_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No zone with id {zone_id}",
        )

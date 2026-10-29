from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.dependencies import (
    Clock,
    current_user,
    get_animal_store,
    get_clock,
    require_owner,
    verify_api_key,
)
from app.schemas.animal import (
    Animal,
    AnimalCreate,
    AnimalDetection,
    AnimalList,
    AnimalStatus,
    AnimalUpdate,
)
from app.services.animal_store import AnimalStore, TagAlreadyRegisteredError

router = APIRouter(prefix="/api/animals", tags=["animals"])

# Declared on the routes rather than as a parameter: these handlers do not read
# the user, they only require one with the right role.
OWNER_ONLY = [Depends(require_owner)]
SIGNED_IN = [Depends(current_user)]

#: The ear tag in the path. Restricted to what survives a path segment
#: unescaped; a tag with a slash would split the route.
TagParam = Annotated[str, Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")]


def _found(animal: Animal | None, tag: str) -> Animal:
    if animal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No animal with tag {tag}",
        )
    return animal


@router.get("", response_model=AnimalList)
def list_animals(
    status_filter: Annotated[AnimalStatus | None, Query(alias="status")] = None,
    store: AnimalStore = Depends(get_animal_store),
) -> AnimalList:
    """The herd, optionally filtered by status.

    Unfiltered by default rather than active-only. Animals are deactivated and
    never deleted, so a default filter would quietly hide rows and make the
    total disagree with the table for reasons the caller cannot see.
    """
    items = store.list(status_filter)
    return AnimalList(items=items, total=len(items))


@router.get("/{tag}", response_model=Animal)
def get_animal(tag: TagParam, store: AnimalStore = Depends(get_animal_store)) -> Animal:
    return _found(store.get(tag), tag)


@router.post(
    "", response_model=Animal, status_code=status.HTTP_201_CREATED,
    dependencies=OWNER_ONLY,
)
def create_animal(
    data: AnimalCreate,
    store: AnimalStore = Depends(get_animal_store),
    clock: Clock = Depends(get_clock),
) -> Animal:
    """Registers an animal. Owner only.

    Registering an animal is deciding what the herd is. A worker watches it and
    reviews its alerts, which is why reads are open to any signed-in user and
    this is not.
    """
    try:
        return store.create(data, clock())
    except TagAlreadyRegisteredError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An animal with tag {data.tag} is already registered",
        ) from error


@router.patch("/{tag}", response_model=Animal, dependencies=OWNER_ONLY)
def update_animal(
    tag: TagParam,
    data: AnimalUpdate,
    store: AnimalStore = Depends(get_animal_store),
    clock: Clock = Depends(get_clock),
) -> Animal:
    """Partially updates an animal. Owner only.

    PATCH and not PUT: a caller sends only what changed, and the fields it
    omits keep their values. A PUT would require the client to resend the whole
    row and would blank anything it forgot.

    The tag is not updatable here; see AnimalUpdate.
    """
    return _found(store.update(tag, data, clock()), tag)


@router.post("/{tag}/deactivate", response_model=Animal, dependencies=OWNER_ONLY)
def deactivate_animal(
    tag: TagParam,
    store: AnimalStore = Depends(get_animal_store),
    clock: Clock = Depends(get_clock),
) -> Animal:
    """Withdraws an animal from the active herd.

    There is no DELETE, and that is the point. A deleted animal leaves its
    alert history pointing at nothing, and a row that is gone cannot be told
    apart from one that never existed. The status becomes inactive and the row
    stays.
    """
    return _found(
        store.update(tag, AnimalUpdate(status=AnimalStatus.INACTIVE), clock()), tag,
    )


@router.put(
    "/{tag}/detection", response_model=Animal,
    dependencies=[Depends(verify_api_key)],
)
def record_detection(
    tag: TagParam,
    data: AnimalDetection,
    store: AnimalStore = Depends(get_animal_store),
) -> Animal:
    """Records that the AI service recognised this animal.

    Guarded by the AI key and not by a user token: the caller is the vision
    service, which holds the shared key, exactly like POST /api/ai/events.

    Not used yet -- the AI service identifies a class and not an individual --
    but the column it writes has existed since the schema was written, and the
    contract belongs next to the rest of the animal routes rather than being
    invented later under time pressure.
    """
    return _found(store.record_detection(tag, data.camera_id, data.detected_at), tag)


@router.get("/{tag}/exists", response_model=dict[str, bool], dependencies=SIGNED_IN)
def animal_exists(
    tag: TagParam,
    store: AnimalStore = Depends(get_animal_store),
) -> dict[str, bool]:
    """Whether a tag is taken, for the registration form.

    Behind authentication even though it reveals almost nothing: without it,
    anyone could enumerate the farm's ear tags one request at a time.
    """
    return {"exists": store.get(tag) is not None}

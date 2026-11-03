from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.dependencies import AuditTrail, Clock, OwnerUser, Visible, get_clock, get_user_store
from app.schemas.audit import AuditAction
from app.schemas.auth import User, UserRole
from app.schemas.user import UserAccountCreate, UserList, UserUpdate
from app.services.user_store import (
    EmailAlreadyRegisteredError,
    LastOwnerError,
    StoredUser,
    UserQuery,
    UserStore,
)

router = APIRouter(prefix="/api/users", tags=["users"])

Store = Annotated[UserStore, Depends(get_user_store)]
UserId = Annotated[int, Path(ge=1)]
Now = Annotated[Clock, Depends(get_clock)]

#: Same paging bounds as the event feed.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _public(user: StoredUser) -> User:
    return User(**user.model_dump(exclude={"password_hash"}))


def _not_found(user_id: int) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No user with id {user_id}")


@router.get("", response_model=UserList)
def list_users(
    _owner: OwnerUser,
    scope: Visible,
    store: Store,
    role: UserRole | None = None,
    is_active: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserList:
    """The accounts, oldest first. Owner only: the list is the staff's email addresses."""
    page = store.page(UserQuery(
        account_id=scope.account_id, role=role, is_active=is_active, limit=limit, offset=offset,
    ))
    return UserList(items=[_public(user) for user in page.items], total=page.total)


@router.post("", response_model=User, status_code=status.HTTP_201_CREATED)
def create_user(data: UserAccountCreate, owner: OwnerUser, store: Store, clock: Now, audit: AuditTrail) -> User:
    """Creates an account with an initial password the owner hands over.

    Returns the account and no token: the owner is not signing in as the new
    user, and a token for someone else would be a credential nobody asked for.
    """
    try:
        created = store.create(data, data.role, clock(), owner.account_id)
    except EmailAlreadyRegisteredError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="That email is already registered",
        ) from error
    audit.record(
        AuditAction.USER_CREATED, actor_id=owner.id, actor_email=owner.email,
        target_type="user", target_id=created.id, detail={"email": created.email, "role": created.role.value},
    )
    return _public(created)


@router.get("/{user_id}", response_model=User)
def get_user(user_id: UserId, _owner: OwnerUser, scope: Visible, store: Store) -> User:
    user = store.by_id(user_id)
    if user is None or not scope.sees_user(user_id):
        raise _not_found(user_id)
    return _public(user)


@router.patch("/{user_id}", response_model=User)
def update_user(
    user_id: UserId, data: UserUpdate, owner: OwnerUser, scope: Visible, store: Store, clock: Now, audit: AuditTrail,
) -> User:
    """Renames, promotes, demotes, deactivates or reactivates an account.

    Takes effect on that user's next request, because every request re-reads
    the account rather than trusting the role in the token.
    """
    if not scope.sees_user(user_id):
        raise _not_found(user_id)
    try:
        updated = store.update(user_id, data, clock())
    except LastOwnerError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="At least one active owner must remain; promote another account first",
        ) from error
    if updated is None:
        raise _not_found(user_id)
    audit.record(
        AuditAction.USER_UPDATED, actor_id=owner.id, actor_email=owner.email, target_type="user",
        target_id=user_id, detail=data.model_dump(mode="json", include=data.model_fields_set),
    )
    return _public(updated)

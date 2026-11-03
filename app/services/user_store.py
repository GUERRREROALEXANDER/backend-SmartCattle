"""User storage.

The only shared table this service both owns and writes. Unlike events and
cameras, no other service writes here, so the columns can be required.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db import SessionFactory, is_unique_violation
from app.models import UserRow
from app.schemas.auth import User, UserCreate, UserRole
from app.schemas.user import UserUpdate


class EmailAlreadyRegisteredError(Exception):
    """Raised when an address is already taken.

    Its own type rather than a bool return: the caller has to answer 409 and
    must not be able to confuse "taken" with "created".
    """


class LastOwnerError(Exception):
    """Raised when an update would leave no active owner.

    Nobody could then grant the role again through the API, and the farm's
    configuration would be frozen until someone edited the database by hand.
    """


class StoredUser(User):
    """A user plus the hash. Never leaves the store.

    Separate from User so the hash cannot reach a response by accident: the
    response model has no such field to populate.
    """

    password_hash: str
    # Whose space this account works in. Not in User: nobody needs it in a response.
    account_id: int


@dataclass(frozen=True)
class UserQuery:
    account_id: int | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True)
class UserPage:
    items: list[StoredUser]
    total: int


class UserStore(Protocol):
    def create(
        self, data: UserCreate, role: UserRole, created_at: datetime, account_id: int | None = None,
    ) -> StoredUser:
        """account_id None starts a new space, owned by this account."""
        ...

    def ids_in_account(self, account_id: int) -> frozenset[int]: ...

    def by_email(self, email: str) -> StoredUser | None: ...

    def by_id(self, user_id: int) -> StoredUser | None: ...

    def record_login(self, user_id: int, when: datetime) -> None: ...

    def count(self) -> int: ...

    def page(self, query: UserQuery) -> UserPage: ...

    def update(self, user_id: int, data: UserUpdate, now: datetime) -> StoredUser | None: ...

    def set_password(self, user_id: int, password: str, now: datetime) -> None: ...


def _removes_an_owner(user: User, data: UserUpdate) -> bool:
    """Whether the update takes an active owner out of that set."""
    if user.role is not UserRole.OWNER or not user.is_active:
        return False
    return data.role is UserRole.WORKER or data.is_active is False


def _aware(value: datetime | None) -> datetime | None:
    # SQLite drops the timezone; PostgreSQL returns timestamptz in the session time zone.
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class InMemoryUserStore:
    """Thread-safe user storage used when DATABASE_URL is not set.

    Accounts live only as long as the process. That is the honest behaviour for
    a backend with no database: the alternative, persisting to a file, would
    look like a real account system while being one restart from empty.
    """

    def __init__(self) -> None:
        self._by_id: dict[int, StoredUser] = {}
        self._next_id = 1
        self._lock = Lock()

    def ids_in_account(self, account_id: int) -> frozenset[int]:
        with self._lock:
            return frozenset(user.id for user in self._by_id.values() if user.account_id == account_id)

    def create(
        self, data: UserCreate, role: UserRole, created_at: datetime, account_id: int | None = None,
    ) -> StoredUser:
        # Hash outside the lock: bcrypt at cost 12 takes around a tenth of a
        # second, and holding the lock for it would serialise every other
        # request touching this store.
        password_hash = hash_password(data.password)
        with self._lock:
            if any(user.email == data.email for user in self._by_id.values()):
                raise EmailAlreadyRegisteredError(data.email)
            user = StoredUser(
                id=self._next_id, email=data.email, full_name=data.full_name,
                role=role, is_active=True, created_at=created_at,
                last_login_at=None, password_hash=password_hash,
                account_id=self._next_id if account_id is None else account_id,
            )
            self._by_id[user.id] = user
            self._next_id += 1
            return user

    def by_email(self, email: str) -> StoredUser | None:
        with self._lock:
            return next((user for user in self._by_id.values() if user.email == email), None)

    def by_id(self, user_id: int) -> StoredUser | None:
        with self._lock:
            return self._by_id.get(user_id)

    def record_login(self, user_id: int, when: datetime) -> None:
        with self._lock:
            user = self._by_id.get(user_id)
            if user is not None:
                self._by_id[user_id] = user.model_copy(update={"last_login_at": when})

    def count(self) -> int:
        with self._lock:
            return len(self._by_id)

    def page(self, query: UserQuery) -> UserPage:
        with self._lock:
            matching = [
                self._by_id[key] for key in sorted(self._by_id)
                if (query.account_id is None or self._by_id[key].account_id == query.account_id)
                and (query.role is None or self._by_id[key].role is query.role)
                and (query.is_active is None or self._by_id[key].is_active == query.is_active)
            ]
        return UserPage(items=matching[query.offset:query.offset + query.limit], total=len(matching))

    # `now` stamps users.updated_at in SQL; a User model has no such field to set.
    def update(self, user_id: int, data: UserUpdate, now: datetime) -> StoredUser | None:  # noqa: ARG002
        with self._lock:
            current = self._by_id.get(user_id)
            if current is None:
                return None
            if _removes_an_owner(current, data) and not any(
                user.id != user_id and user.account_id == current.account_id
                and user.role is UserRole.OWNER and user.is_active
                for user in self._by_id.values()
            ):
                raise LastOwnerError(user_id)
            updated = current.model_copy(update={field: getattr(data, field) for field in data.model_fields_set})
            self._by_id[user_id] = updated
            return updated

    def set_password(self, user_id: int, password: str, now: datetime) -> None:  # noqa: ARG002
        password_hash = hash_password(password)
        with self._lock:
            user = self._by_id.get(user_id)
            if user is not None:
                self._by_id[user_id] = user.model_copy(update={"password_hash": password_hash})


def _to_user(row: UserRow) -> StoredUser:
    created_at = _aware(row.created_at)
    assert created_at is not None  # noqa: S101 - created_at is NOT NULL in the schema
    return StoredUser(
        id=row.id, email=row.email, full_name=row.full_name,
        role=UserRole(row.role), is_active=row.is_active,
        created_at=created_at, last_login_at=_aware(row.last_login_at),
        password_hash=row.password_hash, account_id=row.account_id or row.id,
    )


class SqlUserStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    def ids_in_account(self, account_id: int) -> frozenset[int]:
        with self._sessions() as session:
            return frozenset(session.scalars(select(UserRow.id).where(UserRow.account_id == account_id)))

    def create(
        self, data: UserCreate, role: UserRole, created_at: datetime, account_id: int | None = None,
    ) -> StoredUser:
        password_hash = hash_password(data.password)
        row = UserRow(
            email=data.email, password_hash=password_hash, full_name=data.full_name,
            role=role.value, is_active=True, created_at=created_at, updated_at=created_at, account_id=account_id,
        )
        try:
            with self._sessions.begin() as session:
                session.add(row)
                session.flush()
                if row.account_id is None:
                    row.account_id = row.id
                return _to_user(row)
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            # The UNIQUE on email is what decides this, not a prior SELECT.
            # Two simultaneous registrations of the same address both pass a
            # check-then-insert; only one survives the constraint.
            raise EmailAlreadyRegisteredError(data.email) from error

    def by_email(self, email: str) -> StoredUser | None:
        with self._sessions() as session:
            row = session.scalars(select(UserRow).where(UserRow.email == email)).one_or_none()
            return None if row is None else _to_user(row)

    def by_id(self, user_id: int) -> StoredUser | None:
        with self._sessions() as session:
            row = session.get(UserRow, user_id)
            return None if row is None else _to_user(row)

    def record_login(self, user_id: int, when: datetime) -> None:
        with self._sessions.begin() as session:
            row = session.get(UserRow, user_id)
            if row is not None:
                row.last_login_at = when

    def count(self) -> int:
        with self._sessions() as session:
            return self._count(session)

    def page(self, query: UserQuery) -> UserPage:
        conditions = []
        if query.account_id is not None:
            conditions.append(UserRow.account_id == query.account_id)
        if query.role is not None:
            conditions.append(UserRow.role == query.role.value)
        if query.is_active is not None:
            conditions.append(UserRow.is_active.is_(query.is_active))
        with self._sessions() as session:
            total = session.scalar(select(func.count()).select_from(UserRow).where(*conditions)) or 0
            rows = session.scalars(
                select(UserRow).where(*conditions).order_by(UserRow.id).limit(query.limit).offset(query.offset)
            )
            return UserPage(items=[_to_user(row) for row in rows], total=int(total))

    def update(self, user_id: int, data: UserUpdate, now: datetime) -> StoredUser | None:
        with self._sessions.begin() as session:
            row = session.get(UserRow, user_id, with_for_update=True)
            if row is None:
                return None
            if _removes_an_owner(_to_user(row), data):
                # Locks the other active owners as well. Two owners demoting
                # each other at the same moment would otherwise each see the
                # other still in place, and both updates would succeed.
                other_owner = session.scalars(
                    select(UserRow.id).where(
                        UserRow.role == UserRole.OWNER.value, UserRow.is_active.is_(True), UserRow.id != user_id,
                        UserRow.account_id == row.account_id,
                    ).limit(1).with_for_update()
                ).first()
                if other_owner is None:
                    raise LastOwnerError(user_id)
            if data.full_name is not None:
                row.full_name = data.full_name
            if data.role is not None:
                row.role = data.role.value
            if data.is_active is not None:
                row.is_active = data.is_active
            # PostgreSQL's trigger overwrites this; SQLite has no trigger.
            row.updated_at = now
            session.flush()
            return _to_user(row)

    def set_password(self, user_id: int, password: str, now: datetime) -> None:
        password_hash = hash_password(password)
        with self._sessions.begin() as session:
            row = session.get(UserRow, user_id)
            if row is not None:
                row.password_hash = password_hash
                row.updated_at = now

    @staticmethod
    def _count(session: Session) -> int:
        total = session.scalar(select(func.count()).select_from(UserRow))
        return int(total or 0)

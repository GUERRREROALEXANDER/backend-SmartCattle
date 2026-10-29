"""User storage.

The only shared table this service both owns and writes. Unlike events and
cameras, no other service writes here, so the columns can be required.
"""

from datetime import UTC, datetime
from threading import Lock
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db import SessionFactory
from app.models import UserRow
from app.schemas.auth import User, UserCreate, UserRole


class EmailAlreadyRegisteredError(Exception):
    """Raised when an address is already taken.

    Its own type rather than a bool return: the caller has to answer 409 and
    must not be able to confuse "taken" with "created".
    """


class StoredUser(User):
    """A user plus the hash. Never leaves the store.

    Separate from User so the hash cannot reach a response by accident: the
    response model has no such field to populate.
    """

    password_hash: str


class UserStore(Protocol):
    def create(self, data: UserCreate, role: UserRole, created_at: datetime) -> StoredUser: ...

    def by_email(self, email: str) -> StoredUser | None: ...

    def by_id(self, user_id: int) -> StoredUser | None: ...

    def record_login(self, user_id: int, when: datetime) -> None: ...

    def count(self) -> int: ...


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

    def create(self, data: UserCreate, role: UserRole, created_at: datetime) -> StoredUser:
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


def _to_user(row: UserRow) -> StoredUser:
    created_at = _aware(row.created_at)
    assert created_at is not None  # noqa: S101 - created_at is NOT NULL in the schema
    return StoredUser(
        id=row.id, email=row.email, full_name=row.full_name,
        role=UserRole(row.role), is_active=row.is_active,
        created_at=created_at, last_login_at=_aware(row.last_login_at),
        password_hash=row.password_hash,
    )


class SqlUserStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    def create(self, data: UserCreate, role: UserRole, created_at: datetime) -> StoredUser:
        password_hash = hash_password(data.password)
        row = UserRow(
            email=data.email, password_hash=password_hash, full_name=data.full_name,
            role=role.value, is_active=True, created_at=created_at, updated_at=created_at,
        )
        try:
            with self._sessions.begin() as session:
                session.add(row)
                session.flush()
                return _to_user(row)
        except IntegrityError as error:
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

    @staticmethod
    def _count(session: Session) -> int:
        from sqlalchemy import func

        total = session.scalar(select(func.count()).select_from(UserRow))
        return int(total or 0)

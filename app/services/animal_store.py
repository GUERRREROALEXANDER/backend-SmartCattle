"""Animal storage.

The shared animals table. The farm staff write it through this service; the AI
service updates last_detection directly when it recognises an individual, which
it cannot do yet.
"""

from datetime import UTC, datetime
from threading import Lock
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionFactory
from app.models import AnimalRow, CameraRow
from app.schemas.animal import Animal, AnimalCreate, AnimalStatus, AnimalUpdate
from app.services.camera_store import camera_row

Animals = list[Animal]


class TagAlreadyRegisteredError(Exception):
    """Raised when the ear tag is taken.

    Its own type so the route can answer 409 without having to tell a
    constraint violation apart from any other database error.
    """


class AnimalStore(Protocol):
    def create(self, data: AnimalCreate, now: datetime) -> Animal: ...

    def update(self, tag: str, data: AnimalUpdate, now: datetime) -> Animal | None: ...

    def get(self, tag: str) -> Animal | None: ...

    def list(self, status: AnimalStatus | None = None) -> Animals: ...

    def record_detection(self, tag: str, camera_id: str | None, when: datetime) -> Animal | None: ...


def _aware(value: datetime | None) -> datetime | None:
    # SQLite drops the timezone; PostgreSQL returns timestamptz in the session time zone.
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class InMemoryAnimalStore:
    """Thread-safe animal storage used when DATABASE_URL is not set."""

    def __init__(self) -> None:
        self._animals: dict[str, Animal] = {}
        self._lock = Lock()

    def create(self, data: AnimalCreate, now: datetime) -> Animal:
        with self._lock:
            if data.tag in self._animals:
                raise TagAlreadyRegisteredError(data.tag)
            animal = Animal(
                **data.model_dump(), last_detection=None, created_at=now, updated_at=now,
            )
            self._animals[animal.tag] = animal
            return animal

    def update(self, tag: str, data: AnimalUpdate, now: datetime) -> Animal | None:
        with self._lock:
            current = self._animals.get(tag)
            if current is None:
                return None
            # exclude_unset so a field the caller did not send keeps its value;
            # without it every PATCH would blank the fields it omitted.
            changes = data.model_dump(exclude_unset=True)
            updated = current.model_copy(update={**changes, "updated_at": now})
            self._animals[tag] = updated
            return updated

    def get(self, tag: str) -> Animal | None:
        with self._lock:
            return self._animals.get(tag)

    def list(self, status: AnimalStatus | None = None) -> Animals:
        with self._lock:
            return [
                self._animals[tag] for tag in sorted(self._animals)
                if status is None or self._animals[tag].status is status
            ]

    def record_detection(self, tag: str, camera_id: str | None, when: datetime) -> Animal | None:
        with self._lock:
            current = self._animals.get(tag)
            if current is None:
                return None
            changes: dict[str, object] = {"last_detection": when, "updated_at": when}
            if camera_id is not None:
                changes["camera_id"] = camera_id
            updated = current.model_copy(update=changes)
            self._animals[tag] = updated
            return updated


def _to_animal(row: AnimalRow, camera_name: str | None) -> Animal:
    created_at = _aware(row.created_at)
    updated_at = _aware(row.updated_at)
    assert created_at is not None and updated_at is not None  # noqa: S101 - NOT NULL in the schema
    return Animal(
        tag=row.tag, name=row.name, status=AnimalStatus(row.status),
        camera_id=camera_name, last_detection=_aware(row.last_detection),
        created_at=created_at, updated_at=updated_at,
    )


class SqlAnimalStore:
    """The shared animals table.

    camera_id is a numeric foreign key in the database and the camera's name in
    the API, the same translation the event store makes. A camera named for the
    first time here gets a row, so registering an animal against a camera the
    AI service has not reported yet works.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    def create(self, data: AnimalCreate, now: datetime) -> Animal:
        try:
            with self._sessions.begin() as session:
                camera = camera_row(session, data.camera_id) if data.camera_id else None
                row = AnimalRow(
                    tag=data.tag, name=data.name, status=data.status.value,
                    camera_id=camera.id if camera else None,
                    created_at=now, updated_at=now,
                )
                session.add(row)
                session.flush()
                return _to_animal(row, data.camera_id)
        except IntegrityError as error:
            # The primary key decides this, not a prior SELECT: two
            # simultaneous registrations of the same tag both pass a
            # check-then-insert and only one survives.
            raise TagAlreadyRegisteredError(data.tag) from error

    def update(self, tag: str, data: AnimalUpdate, now: datetime) -> Animal | None:
        changes = data.model_dump(exclude_unset=True)
        with self._sessions.begin() as session:
            row = session.get(AnimalRow, tag)
            if row is None:
                return None
            if "status" in changes and changes["status"] is not None:
                row.status = AnimalStatus(changes["status"]).value
            if "name" in changes:
                row.name = changes["name"]
            camera_name = self._camera_name(session, row)
            if "camera_id" in changes:
                camera_name = changes["camera_id"]
                camera = camera_row(session, camera_name) if camera_name else None
                row.camera_id = camera.id if camera else None
            row.updated_at = now
            session.flush()
            return _to_animal(row, camera_name)

    def get(self, tag: str) -> Animal | None:
        with self._sessions() as session:
            row = session.get(AnimalRow, tag)
            return None if row is None else _to_animal(row, self._camera_name(session, row))

    def list(self, status: AnimalStatus | None = None) -> Animals:
        query = (
            select(AnimalRow, CameraRow.name)
            .outerjoin(CameraRow, AnimalRow.camera_id == CameraRow.id)
            .order_by(AnimalRow.tag)
        )
        if status is not None:
            query = query.where(AnimalRow.status == status.value)
        with self._sessions() as session:
            # outerjoin, not join: camera_id is nullable, and an inner join
            # would silently hide every animal nobody has located yet.
            return [_to_animal(row, camera) for row, camera in session.execute(query)]

    def record_detection(self, tag: str, camera_id: str | None, when: datetime) -> Animal | None:
        with self._sessions.begin() as session:
            row = session.get(AnimalRow, tag)
            if row is None:
                return None
            camera_name = self._camera_name(session, row)
            if camera_id is not None:
                camera_name = camera_id
                row.camera_id = camera_row(session, camera_id).id
            row.last_detection = when
            row.updated_at = when
            session.flush()
            return _to_animal(row, camera_name)

    @staticmethod
    def _camera_name(session: object, row: AnimalRow) -> str | None:
        if row.camera_id is None:
            return None
        camera = session.get(CameraRow, row.camera_id)  # type: ignore[attr-defined]
        return None if camera is None else camera.name

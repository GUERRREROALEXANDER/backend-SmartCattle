"""Safe zone storage.

The AI service reads these to decide whether an animal has left its zone, so a
zone written here changes what the vision service alerts on.
"""

from datetime import UTC, datetime
from threading import Lock
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import SessionFactory
from app.models import CameraRow, ZoneRow
from app.schemas.zone import Zone, ZoneCreate, ZonePoint, ZoneUpdate
from app.services.camera_store import camera_row

Zones = list[Zone]


class ZoneNameTakenError(Exception):
    """Raised when the camera already has a zone with that name.

    UNIQUE (camera_id, name): two cameras may each have a "Safe zone"; one
    camera may not have two.
    """


class ZoneStore(Protocol):
    def create(self, camera_id: str, data: ZoneCreate, now: datetime) -> Zone: ...

    def update(self, zone_id: int, data: ZoneUpdate) -> Zone | None: ...

    def delete(self, zone_id: int) -> bool: ...

    def get(self, zone_id: int) -> Zone | None: ...

    def list(self, camera_id: str | None = None) -> Zones: ...


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _outline_json(outline: list[ZonePoint] | None) -> list[dict[str, float]] | None:
    """The polygon as the shared schema stores it.

    The same [{"x": .., "y": ..}] shape the frontend already uses, and the one
    the database's is_valid_zone_outline() constraint checks.
    """
    return None if outline is None else [{"x": point.x, "y": point.y} for point in outline]


def _outline_points(stored: object) -> list[ZonePoint] | None:
    """Reads the polygon back, tolerating a row written by another service.

    JSONB accepts any shape. The database has a CHECK, but a database that
    predates it may hold something else, and a malformed outline must not fail
    the whole listing -- the rectangle is still usable.
    """
    if not isinstance(stored, list):
        return None
    points = []
    for item in stored:
        if not isinstance(item, dict):
            return None
        x, y = item.get("x"), item.get("y")
        if not isinstance(x, int | float) or not isinstance(y, int | float):
            return None
        if isinstance(x, bool) or isinstance(y, bool):
            return None
        points.append(ZonePoint(x=float(x), y=float(y)))
    return points or None


class InMemoryZoneStore:
    """Thread-safe zone storage used when DATABASE_URL is not set."""

    def __init__(self) -> None:
        self._zones: dict[int, Zone] = {}
        self._next_id = 1
        self._lock = Lock()

    def create(self, camera_id: str, data: ZoneCreate, now: datetime) -> Zone:
        with self._lock:
            if any(
                zone.camera_id == camera_id and zone.name == data.name
                for zone in self._zones.values()
            ):
                raise ZoneNameTakenError(data.name)
            zone = Zone(
                **data.model_dump(), id=self._next_id, camera_id=camera_id, created_at=now,
            )
            self._zones[zone.id] = zone
            self._next_id += 1
            return zone

    def update(self, zone_id: int, data: ZoneUpdate) -> Zone | None:
        with self._lock:
            current = self._zones.get(zone_id)
            if current is None:
                return None
            changes: dict[str, object] = {}
            if data.name is not None:
                changes["name"] = data.name
            if data.active is not None:
                changes["active"] = data.active
            if data.geometry is not None:
                changes.update(data.geometry.model_dump())
            updated = current.model_copy(update=changes)
            self._zones[zone_id] = updated
            return updated

    def delete(self, zone_id: int) -> bool:
        with self._lock:
            return self._zones.pop(zone_id, None) is not None

    def get(self, zone_id: int) -> Zone | None:
        with self._lock:
            return self._zones.get(zone_id)

    def list(self, camera_id: str | None = None) -> Zones:
        with self._lock:
            return [
                self._zones[key] for key in sorted(self._zones)
                if camera_id is None or self._zones[key].camera_id == camera_id
            ]


def _to_zone(row: ZoneRow, camera_name: str) -> Zone:
    return Zone(
        id=row.id, camera_id=camera_name, name=row.name,
        x_min=row.x_min, y_min=row.y_min, x_max=row.x_max, y_max=row.y_max,
        outline=_outline_points(row.outline), active=row.active,
        created_at=_aware(row.created_at),
    )


class SqlZoneStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    def create(self, camera_id: str, data: ZoneCreate, now: datetime) -> Zone:
        try:
            with self._sessions.begin() as session:
                # A camera named here for the first time gets a row, so a zone
                # can be configured before the AI service has ever reported.
                camera = camera_row(session, camera_id)
                row = ZoneRow(
                    camera_id=camera.id, name=data.name,
                    x_min=data.x_min, y_min=data.y_min, x_max=data.x_max, y_max=data.y_max,
                    outline=_outline_json(data.outline), active=data.active, created_at=now,
                )
                session.add(row)
                session.flush()
                return _to_zone(row, camera_id)
        except IntegrityError as error:
            raise ZoneNameTakenError(data.name) from error

    def update(self, zone_id: int, data: ZoneUpdate) -> Zone | None:
        with self._sessions.begin() as session:
            row = session.get(ZoneRow, zone_id)
            if row is None:
                return None
            if data.name is not None:
                row.name = data.name
            if data.active is not None:
                row.active = data.active
            if data.geometry is not None:
                row.x_min = data.geometry.x_min
                row.y_min = data.geometry.y_min
                row.x_max = data.geometry.x_max
                row.y_max = data.geometry.y_max
                row.outline = _outline_json(data.geometry.outline)
            session.flush()
            return _to_zone(row, self._camera_name(session, row))

    def delete(self, zone_id: int) -> bool:
        with self._sessions.begin() as session:
            row = session.get(ZoneRow, zone_id)
            if row is None:
                return False
            session.delete(row)
            return True

    def get(self, zone_id: int) -> Zone | None:
        with self._sessions() as session:
            row = session.get(ZoneRow, zone_id)
            return None if row is None else _to_zone(row, self._camera_name(session, row))

    def list(self, camera_id: str | None = None) -> Zones:
        query = (
            select(ZoneRow, CameraRow.name)
            .join(CameraRow, ZoneRow.camera_id == CameraRow.id)
            .order_by(ZoneRow.id)
        )
        if camera_id is not None:
            query = query.where(CameraRow.name == camera_id)
        with self._sessions() as session:
            # Inner join is correct here: zones.camera_id is NOT NULL and
            # CASCADEs with the camera, so a zone without one cannot exist.
            return [_to_zone(row, camera) for row, camera in session.execute(query)]

    @staticmethod
    def _camera_name(session: Session, row: ZoneRow) -> str:
        camera = session.get(CameraRow, row.camera_id)
        # camera_id is NOT NULL with ON DELETE CASCADE, so the camera is there.
        assert camera is not None  # noqa: S101
        return camera.name

"""Farm and lot storage.

One store for both because a lot only exists inside a farm: the farm is the
aggregate root, and the rule that a farm with lots cannot be deleted needs
both tables in one transaction.
"""

from datetime import UTC, datetime
from threading import Lock
from typing import Any, Protocol

from sqlalchemy import ScalarSelect, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import SessionFactory
from app.models import CameraRow, FarmRow, LotRow
from app.schemas.farm import Farm, FarmCreate, FarmUpdate, GeoPoint, Lot, LotCreate, LotKind, LotUpdate
from app.services.camera_store import camera_row


class FarmNameTakenError(Exception):
    """The owner already has a farm with that name."""


class LotNameTakenError(Exception):
    """The farm already has a lot with that name."""


class FarmHasLotsError(Exception):
    """Raised instead of cascading: deleting a farm must not silently delete its lots."""


class FarmStore(Protocol):
    def create_farm(self, owner_id: int, data: FarmCreate, now: datetime) -> Farm: ...

    def get_farm(self, farm_id: int) -> Farm | None: ...

    def list_farms(self) -> list[Farm]: ...

    def update_farm(self, farm_id: int, data: FarmUpdate, now: datetime) -> Farm | None: ...

    def delete_farm(self, farm_id: int) -> bool: ...

    def create_lot(self, farm_id: int, data: LotCreate, now: datetime) -> Lot: ...

    def get_lot(self, lot_id: int) -> Lot | None: ...

    def list_lots(self, farm_id: int, active: bool | None = None) -> list[Lot]: ...

    def update_lot(self, lot_id: int, data: LotUpdate, now: datetime) -> Lot | None: ...

    def delete_lot(self, lot_id: int) -> bool: ...


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _farm_changes(data: FarmUpdate) -> dict[str, Any]:
    """The columns an update touches, keyed by column name.

    Driven by model_fields_set so an omitted field keeps its value while an
    explicit null clears it -- the two look identical once dumped.
    """
    changes: dict[str, Any] = {}
    for field in data.model_fields_set:
        if field == "coordinates":
            point = data.coordinates
            changes["latitude"] = None if point is None else point.latitude
            changes["longitude"] = None if point is None else point.longitude
        else:
            changes[field] = getattr(data, field)
    return changes


def _lot_changes(data: LotUpdate) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for field in data.model_fields_set:
        value = getattr(data, field)
        changes[field] = _boundary_json(value) if field == "boundary" else value
    return changes


def _boundary_json(boundary: list[GeoPoint] | None) -> list[dict[str, float]] | None:
    return None if boundary is None else [point.model_dump() for point in boundary]


def _boundary_points(stored: object) -> list[GeoPoint] | None:
    """Reads a polygon back; a malformed one is dropped rather than failing the listing."""
    if not isinstance(stored, list):
        return None
    try:
        return [GeoPoint.model_validate(item) for item in stored] or None
    except ValueError:
        return None


class InMemoryFarmStore:
    """Thread-safe storage used when DATABASE_URL is not set."""

    def __init__(self) -> None:
        self._farms: dict[int, Farm] = {}
        self._lots: dict[int, Lot] = {}
        self._next_farm_id = 1
        self._next_lot_id = 1
        self._lock = Lock()

    def create_farm(self, owner_id: int, data: FarmCreate, now: datetime) -> Farm:
        with self._lock:
            if any(farm.owner_id == owner_id and farm.name == data.name for farm in self._farms.values()):
                raise FarmNameTakenError(data.name)
            farm = Farm(
                id=self._next_farm_id, owner_id=owner_id, name=data.name, location=data.location,
                area_hectares=data.area_hectares, coordinates=data.coordinates,
                lot_count=0, created_at=now, updated_at=now,
            )
            self._farms[farm.id] = farm
            self._next_farm_id += 1
            return farm

    def get_farm(self, farm_id: int) -> Farm | None:
        with self._lock:
            farm = self._farms.get(farm_id)
            return None if farm is None else self._counted(farm)

    def list_farms(self) -> list[Farm]:
        with self._lock:
            return [self._counted(self._farms[key]) for key in sorted(self._farms)]

    def update_farm(self, farm_id: int, data: FarmUpdate, now: datetime) -> Farm | None:
        with self._lock:
            current = self._farms.get(farm_id)
            if current is None:
                return None
            if data.name is not None and any(
                farm.id != farm_id and farm.owner_id == current.owner_id and farm.name == data.name
                for farm in self._farms.values()
            ):
                raise FarmNameTakenError(data.name)
            changes = {field: getattr(data, field) for field in data.model_fields_set}
            updated = current.model_copy(update=changes | {"updated_at": now})
            self._farms[farm_id] = updated
            return self._counted(updated)

    def delete_farm(self, farm_id: int) -> bool:
        with self._lock:
            if farm_id not in self._farms:
                return False
            if any(lot.farm_id == farm_id for lot in self._lots.values()):
                raise FarmHasLotsError(farm_id)
            del self._farms[farm_id]
            return True

    def create_lot(self, farm_id: int, data: LotCreate, now: datetime) -> Lot:
        with self._lock:
            if any(lot.farm_id == farm_id and lot.name == data.name for lot in self._lots.values()):
                raise LotNameTakenError(data.name)
            lot = Lot(**data.model_dump(), id=self._next_lot_id, farm_id=farm_id, created_at=now, updated_at=now)
            self._lots[lot.id] = lot
            self._next_lot_id += 1
            return lot

    def get_lot(self, lot_id: int) -> Lot | None:
        with self._lock:
            return self._lots.get(lot_id)

    def list_lots(self, farm_id: int, active: bool | None = None) -> list[Lot]:
        with self._lock:
            return [
                self._lots[key] for key in sorted(self._lots)
                if self._lots[key].farm_id == farm_id and (active is None or self._lots[key].active == active)
            ]

    def update_lot(self, lot_id: int, data: LotUpdate, now: datetime) -> Lot | None:
        with self._lock:
            current = self._lots.get(lot_id)
            if current is None:
                return None
            if data.name is not None and any(
                lot.id != lot_id and lot.farm_id == current.farm_id and lot.name == data.name
                for lot in self._lots.values()
            ):
                raise LotNameTakenError(data.name)
            changes = {field: getattr(data, field) for field in data.model_fields_set}
            updated = current.model_copy(update=changes | {"updated_at": now})
            self._lots[lot_id] = updated
            return updated

    def delete_lot(self, lot_id: int) -> bool:
        with self._lock:
            return self._lots.pop(lot_id, None) is not None

    def _counted(self, farm: Farm) -> Farm:
        count = sum(1 for lot in self._lots.values() if lot.farm_id == farm.id)
        return farm.model_copy(update={"lot_count": count})


def _to_farm(row: FarmRow, lot_count: int) -> Farm:
    coordinates = None
    if row.latitude is not None and row.longitude is not None:
        coordinates = GeoPoint(latitude=row.latitude, longitude=row.longitude)
    return Farm(
        id=row.id, owner_id=row.owner_id, name=row.name, location=row.location,
        area_hectares=row.area_hectares, coordinates=coordinates, lot_count=lot_count,
        created_at=_aware(row.created_at), updated_at=_aware(row.updated_at),
    )


def _to_lot(row: LotRow, camera_name: str | None) -> Lot:
    return Lot(
        id=row.id, farm_id=row.farm_id, name=row.name, kind=LotKind(row.kind),
        area_square_meters=row.area_square_meters, capacity=row.capacity,
        boundary=_boundary_points(row.boundary), camera_id=camera_name, active=row.active,
        created_at=_aware(row.created_at), updated_at=_aware(row.updated_at),
    )


def _lot_count() -> ScalarSelect[int]:
    return (
        select(func.count(LotRow.id)).where(LotRow.farm_id == FarmRow.id)
        .correlate(FarmRow).scalar_subquery()
    )


#: Outer join: a lot without a camera is the normal case for a stable.
#: Statements are immutable, so one module-level instance is safe to extend.
_LOTS_WITH_CAMERA = select(LotRow, CameraRow.name).outerjoin(CameraRow, LotRow.camera_id == CameraRow.id)


def _count_lots(session: Session, farm_id: int) -> int:
    return session.scalar(select(func.count(LotRow.id)).where(LotRow.farm_id == farm_id)) or 0


class SqlFarmStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    def create_farm(self, owner_id: int, data: FarmCreate, now: datetime) -> Farm:
        point = data.coordinates
        row = FarmRow(
            owner_id=owner_id, name=data.name, location=data.location, area_hectares=data.area_hectares,
            latitude=None if point is None else point.latitude,
            longitude=None if point is None else point.longitude,
            created_at=now, updated_at=now,
        )
        try:
            with self._sessions.begin() as session:
                session.add(row)
                session.flush()
                return _to_farm(row, 0)
        except IntegrityError as error:
            raise FarmNameTakenError(data.name) from error

    def get_farm(self, farm_id: int) -> Farm | None:
        with self._sessions() as session:
            row = session.get(FarmRow, farm_id)
            return None if row is None else _to_farm(row, _count_lots(session, farm_id))

    def list_farms(self) -> list[Farm]:
        with self._sessions() as session:
            rows = session.execute(select(FarmRow, _lot_count()).order_by(FarmRow.id))
            return [_to_farm(row, count) for row, count in rows]

    def update_farm(self, farm_id: int, data: FarmUpdate, now: datetime) -> Farm | None:
        try:
            with self._sessions.begin() as session:
                row = session.get(FarmRow, farm_id)
                if row is None:
                    return None
                for column, value in _farm_changes(data).items():
                    setattr(row, column, value)
                row.updated_at = now
                session.flush()
                return _to_farm(row, _count_lots(session, farm_id))
        except IntegrityError as error:
            raise FarmNameTakenError(data.name) from error

    def delete_farm(self, farm_id: int) -> bool:
        with self._sessions.begin() as session:
            row = session.get(FarmRow, farm_id, with_for_update=True)
            if row is None:
                return False
            if session.scalar(select(LotRow.id).where(LotRow.farm_id == farm_id).limit(1)) is not None:
                raise FarmHasLotsError(farm_id)
            session.delete(row)
            return True

    def create_lot(self, farm_id: int, data: LotCreate, now: datetime) -> Lot:
        try:
            with self._sessions.begin() as session:
                row = LotRow(
                    farm_id=farm_id, name=data.name, kind=data.kind.value,
                    area_square_meters=data.area_square_meters, capacity=data.capacity,
                    boundary=_boundary_json(data.boundary), camera_id=self._camera_id(session, data.camera_id),
                    active=data.active, created_at=now, updated_at=now,
                )
                session.add(row)
                session.flush()
                return _to_lot(row, data.camera_id)
        except IntegrityError as error:
            raise LotNameTakenError(data.name) from error

    def get_lot(self, lot_id: int) -> Lot | None:
        with self._sessions() as session:
            found = session.execute(_LOTS_WITH_CAMERA.where(LotRow.id == lot_id)).first()
            return None if found is None else _to_lot(found[0], found[1])

    def list_lots(self, farm_id: int, active: bool | None = None) -> list[Lot]:
        query = _LOTS_WITH_CAMERA.where(LotRow.farm_id == farm_id).order_by(LotRow.id)
        if active is not None:
            query = query.where(LotRow.active.is_(active))
        with self._sessions() as session:
            return [_to_lot(row, camera) for row, camera in session.execute(query)]

    def update_lot(self, lot_id: int, data: LotUpdate, now: datetime) -> Lot | None:
        try:
            with self._sessions.begin() as session:
                row = session.get(LotRow, lot_id)
                if row is None:
                    return None
                for column, value in _lot_changes(data).items():
                    if column == "camera_id":
                        row.camera_id = self._camera_id(session, value)
                    elif column == "kind":
                        row.kind = value.value
                    else:
                        setattr(row, column, value)
                row.updated_at = now
                session.flush()
                camera = None if row.camera_id is None else session.get(CameraRow, row.camera_id)
                return _to_lot(row, None if camera is None else camera.name)
        except IntegrityError as error:
            raise LotNameTakenError(data.name) from error

    def delete_lot(self, lot_id: int) -> bool:
        with self._sessions.begin() as session:
            row = session.get(LotRow, lot_id)
            if row is None:
                return False
            session.delete(row)
            return True

    @staticmethod
    def _camera_id(session: Session, camera_name: str | None) -> int | None:
        # Same rule as zones: a camera named here before the AI service ever
        # reported it gets its row, so a lot can be set up ahead of the hardware.
        return None if camera_name is None else camera_row(session, camera_name).id

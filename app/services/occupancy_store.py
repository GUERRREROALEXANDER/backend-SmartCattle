"""Latest head count per camera."""

from datetime import UTC, datetime
from threading import Lock
from typing import Protocol

from sqlalchemy import select

from app.db import SessionFactory
from app.models import CameraOccupancyRow, CameraRow
from app.schemas.occupancy import Occupancy, OccupancyReport
from app.services.camera_store import camera_row


class OccupancyStore(Protocol):
    def record(self, camera_id: str, report: OccupancyReport, received_at: datetime) -> Occupancy | None:
        """Stores the count, or returns None when a newer one is already stored.

        Reports can arrive out of order when the AI retries over a slow link.
        Applying an older count after a newer one would make the dashboard
        jump back in time.
        """
        ...

    def list(self) -> list[Occupancy]: ...


def _occupancy(camera_id: str, report: OccupancyReport, received_at: datetime) -> Occupancy:
    return Occupancy(
        camera_id=camera_id, cattle_inside=report.cattle_inside, cattle_outside=report.cattle_outside,
        observed_at=report.observed_at, received_at=received_at,
    )


class InMemoryOccupancyStore:
    def __init__(self) -> None:
        self._latest: dict[str, Occupancy] = {}
        self._lock = Lock()

    def record(self, camera_id: str, report: OccupancyReport, received_at: datetime) -> Occupancy | None:
        with self._lock:
            current = self._latest.get(camera_id)
            if current is not None and current.observed_at > report.observed_at:
                return None
            occupancy = _occupancy(camera_id, report, received_at)
            self._latest[camera_id] = occupancy
            return occupancy

    def list(self) -> list[Occupancy]:
        with self._lock:
            return [self._latest[key] for key in sorted(self._latest)]


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SqlOccupancyStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    def record(self, camera_id: str, report: OccupancyReport, received_at: datetime) -> Occupancy | None:
        with self._sessions.begin() as session:
            # Locking the camera row serialises reports for one camera, so two
            # first reports cannot both insert, and the newer-wins comparison
            # sees whatever the other transaction committed.
            camera = camera_row(session, camera_id, lock=True)
            row = session.get(CameraOccupancyRow, camera.id)
            if row is not None and _aware(row.observed_at) > report.observed_at:
                return None
            if row is None:
                row = CameraOccupancyRow(camera_id=camera.id)
                session.add(row)
            row.cattle_inside = report.cattle_inside
            row.cattle_outside = report.cattle_outside
            row.observed_at = report.observed_at
            row.received_at = received_at
        return _occupancy(camera_id, report, received_at)

    def list(self) -> list[Occupancy]:
        query = select(CameraOccupancyRow, CameraRow.name).join(
            CameraRow, CameraOccupancyRow.camera_id == CameraRow.id,
        )
        with self._sessions() as session:
            rows = [
                Occupancy(
                    camera_id=name, cattle_inside=row.cattle_inside, cattle_outside=row.cattle_outside,
                    observed_at=_aware(row.observed_at), received_at=_aware(row.received_at),
                )
                for row, name in session.execute(query)
            ]
        # Sorted here rather than in SQL: SQLite and PostgreSQL collate text
        # differently, and both stores must return the same order.
        return sorted(rows, key=lambda occupancy: occupancy.camera_id)

"""Head count per camera: the latest, and one sample per minute."""

from datetime import UTC, datetime
from threading import Lock
from typing import Protocol

from sqlalchemy import select

from app.db import SessionFactory
from app.models import CameraOccupancyRow, CameraRow, OccupancySampleRow
from app.schemas.occupancy import Occupancy, OccupancyReport
from app.schemas.report import OccupancySample
from app.services.camera_store import camera_row

Samples = list[OccupancySample]


class OccupancyStore(Protocol):
    def record(self, camera_id: str, report: OccupancyReport, received_at: datetime) -> Occupancy | None:
        """Stores the count, or returns None when a newer one is already stored.

        Reports can arrive out of order when the AI retries over a slow link.
        Applying an older count after a newer one would make the dashboard
        jump back in time.
        """
        ...

    def list(self) -> list[Occupancy]: ...

    def samples(self, camera_id: str | None, start: datetime, end: datetime) -> Samples:
        """The per-minute samples in [start, end), by camera and then by minute."""
        ...


def _occupancy(camera_id: str, report: OccupancyReport, received_at: datetime) -> Occupancy:
    return Occupancy(
        camera_id=camera_id, cattle_inside=report.cattle_inside, cattle_outside=report.cattle_outside,
        observed_at=report.observed_at, received_at=received_at,
    )


def minute_of(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(second=0, microsecond=0)


class InMemoryOccupancyStore:
    def __init__(self) -> None:
        self._latest: dict[str, Occupancy] = {}
        self._samples: dict[tuple[str, datetime], OccupancySample] = {}
        self._lock = Lock()

    def record(self, camera_id: str, report: OccupancyReport, received_at: datetime) -> Occupancy | None:
        with self._lock:
            current = self._latest.get(camera_id)
            if current is not None and current.observed_at > report.observed_at:
                return None
            occupancy = _occupancy(camera_id, report, received_at)
            self._latest[camera_id] = occupancy
            # Newer-wins held above, so this report is the latest of its minute.
            minute = minute_of(report.observed_at)
            self._samples[(camera_id, minute)] = OccupancySample(
                camera_id=camera_id, minute=minute,
                cattle_inside=report.cattle_inside, cattle_outside=report.cattle_outside,
            )
            return occupancy

    def list(self) -> list[Occupancy]:
        with self._lock:
            return [self._latest[key] for key in sorted(self._latest)]

    def samples(self, camera_id: str | None, start: datetime, end: datetime) -> Samples:
        with self._lock:
            return [
                sample for (camera, minute), sample in sorted(self._samples.items())
                if (camera_id is None or camera == camera_id) and start <= minute < end
            ]


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SqlOccupancyStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    def record(self, camera_id: str, report: OccupancyReport, received_at: datetime) -> Occupancy | None:
        with self._sessions.begin() as session:
            # Locking the camera row serialises reports for one camera, so two
            # first reports cannot both insert, and the newer-wins comparison
            # sees whatever the other transaction committed. The same lock
            # covers the minute's sample.
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

            # Newer-wins held above, so this report is the latest of its minute.
            minute = minute_of(report.observed_at)
            sample = session.get(OccupancySampleRow, (camera.id, minute))
            if sample is None:
                sample = OccupancySampleRow(camera_id=camera.id, minute=minute)
                session.add(sample)
            sample.cattle_inside = report.cattle_inside
            sample.cattle_outside = report.cattle_outside
            sample.observed_at = report.observed_at
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

    def samples(self, camera_id: str | None, start: datetime, end: datetime) -> Samples:
        query = (
            select(OccupancySampleRow, CameraRow.name)
            .join(CameraRow, OccupancySampleRow.camera_id == CameraRow.id)
            .where(OccupancySampleRow.minute >= start, OccupancySampleRow.minute < end)
        )
        if camera_id is not None:
            query = query.where(CameraRow.name == camera_id)
        with self._sessions() as session:
            items = [
                OccupancySample(
                    camera_id=name, minute=_aware(row.minute),
                    cattle_inside=row.cattle_inside, cattle_outside=row.cattle_outside,
                )
                for row, name in session.execute(query)
            ]
        return sorted(items, key=lambda sample: (sample.camera_id, sample.minute))

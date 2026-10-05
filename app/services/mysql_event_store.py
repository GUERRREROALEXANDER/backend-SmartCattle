from datetime import datetime, timezone
from uuid import UUID, uuid4

from mysql.connector.errors import IntegrityError
from mysql.connector.pooling import MySQLConnectionPool

from app.core.config import Settings
from app.schemas.event import DEFAULT_PAGE_SIZE, AIEventCreate, Event

_COLUMNS = (
    "id, ai_event_id, event_type, camera_id, detected_object, confidence, "
    "`timestamp`, received_at"
)

# %s placeholders are bound by the driver, never formatted into the statement:
# a camera_id such as "x'; DROP TABLE events;--" is stored as literal text.
_INSERT = f"INSERT INTO events ({_COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"

# id breaks ties so two events sharing a received_at keep a stable position and
# cannot appear on two pages or on none.
_SELECT_PAGE = (
    f"SELECT {_COLUMNS} FROM events ORDER BY received_at DESC, id DESC LIMIT %s OFFSET %s"
)

_SELECT_BY_AI_ID = f"SELECT {_COLUMNS} FROM events WHERE ai_event_id = %s"

_COUNT = "SELECT COUNT(*) FROM events"


def _strip_utc(moment: datetime) -> datetime:
    """Drop the offset of an already-UTC instant, as MySQL DATETIME expects."""
    return moment.replace(tzinfo=None)


def _attach_utc(moment: datetime) -> datetime:
    """Re-attach UTC to a value MySQL returned without timezone information."""
    return moment.replace(tzinfo=timezone.utc)


def _to_event(row: tuple) -> Event:
    return Event(
        id=row[0],
        ai_event_id=row[1],
        event_type=row[2],
        camera_id=row[3],
        detected_object=row[4],
        confidence=row[5],
        timestamp=_attach_utc(row[6]),
        received_at=_attach_utc(row[7]),
    )


def create_pool(settings: Settings) -> MySQLConnectionPool:
    """Open the connection pool, failing at startup if MySQL is unreachable."""
    return MySQLConnectionPool(
        pool_size=settings.mysql_pool_size, **settings.mysql_connection_config
    )


class MySqlEventStore:
    """MySQL-backed event storage exposing the same methods as the memory store."""

    def __init__(self, pool: MySQLConnectionPool) -> None:
        self._pool = pool

    def add(self, data: AIEventCreate) -> tuple[Event, bool]:
        event = Event(**data.model_dump(), id=uuid4(), received_at=datetime.now(timezone.utc))
        connection = self._pool.get_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(_INSERT, (
                    str(event.id),
                    str(event.ai_event_id),
                    event.event_type.value,
                    event.camera_id,
                    event.detected_object,
                    event.confidence,
                    _strip_utc(event.timestamp),
                    _strip_utc(event.received_at),
                ))
            connection.commit()
        except IntegrityError:
            # The UNIQUE constraint on ai_event_id rejected a retry. The database
            # settles the race, so two simultaneous retries cannot both insert.
            connection.rollback()
            stored = self._find_by_ai_event_id(connection, data.ai_event_id)
            if stored is None:
                raise
            return stored, False
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return event, True

    def _find_by_ai_event_id(self, connection, ai_event_id: UUID) -> Event | None:
        with connection.cursor() as cursor:
            cursor.execute(_SELECT_BY_AI_ID, (str(ai_event_id),))
            row = cursor.fetchone()
        return _to_event(row) if row else None

    def list(self, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0) -> list[Event]:
        connection = self._pool.get_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(_SELECT_PAGE, (limit, offset))
                rows = cursor.fetchall()
        finally:
            connection.close()
        return [_to_event(row) for row in rows]

    def count(self) -> int:
        connection = self._pool.get_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(_COUNT)
                return cursor.fetchone()[0]
        finally:
            connection.close()

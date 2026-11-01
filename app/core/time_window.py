"""Time windows for the history and report endpoints."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status

#: When the caller gives no start, the window covers the last week.
DEFAULT_WINDOW = timedelta(days=7)
#: A quarter. Wide enough for any report a farm asks for, narrow enough that
#: one request cannot make the server read years of history.
MAX_WINDOW = timedelta(days=92)
#: The farm staff are in Colombia; a caller elsewhere passes its own zone.
DEFAULT_TIME_ZONE = "America/Bogota"


def as_utc(value: datetime | None) -> datetime | None:
    """A time without a zone is read as UTC, so it compares with stored times."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime


def window(start: datetime | None, end: datetime | None, now: datetime) -> Window:
    """[start, end), defaulting to the last week, or 422 when it is empty or too wide."""
    end_at = as_utc(end) or now
    start_at = as_utc(start) or end_at - DEFAULT_WINDOW
    if start_at >= end_at:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="start must be before end")
    if end_at - start_at > MAX_WINDOW:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"The window may span at most {MAX_WINDOW.days} days",
        )
    return Window(start_at, end_at)


def time_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Unknown time zone: {name}",
        ) from error

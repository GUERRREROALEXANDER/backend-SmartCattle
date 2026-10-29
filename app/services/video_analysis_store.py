from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from datetime import UTC, datetime
from threading import Lock
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionFactory
from app.models import VideoAnalysisAlertRow, VideoAnalysisRow
from app.schemas.video_analysis import (
    VideoAlertClass,
    VideoAnalysis,
    VideoAnalysisAlert,
    VideoAnalysisAlertCreate,
    VideoAnalysisCreate,
)

#: How many analyses the in-memory store keeps. The SQL store has no such
#: limit: PostgreSQL holds the history and GET /api/videos/analyses pages it.
MAX_STORED_ANALYSES = 100


# Module-level aliases: inside these classes `list` is the method name, so a
# bare `list[...]` annotation would read as a subscript of that method.
Analyses = list[VideoAnalysis]
AlertRows = list[VideoAnalysisAlertRow]


class VideoAnalysisStore(Protocol):
    def add(self, data: VideoAnalysisCreate, created_at: datetime) -> VideoAnalysis: ...

    def list(self, limit: int) -> Analyses: ...

    def get(self, analysis_id: UUID) -> VideoAnalysis | None: ...


def _stored_alert(alert: VideoAnalysisAlertCreate, alert_id: int) -> VideoAnalysisAlert:
    return VideoAnalysisAlert(**alert.model_dump(), id=alert_id)


class InMemoryVideoAnalysisStore:
    """Thread-safe analysis storage used when DATABASE_URL is not set.

    Bounded like InMemoryEventStore: without DATABASE_URL every upload stays
    in the process, and each analysis carries up to 500 alerts. On Render's
    free instance an unbounded list is a slow leak that ends in the OOM
    killer, so the oldest analyses are dropped instead.
    """

    def __init__(self, max_analyses: int = MAX_STORED_ANALYSES) -> None:
        self._analyses: deque[VideoAnalysis] = deque(maxlen=max_analyses)
        self._next_alert_id = 1
        self._lock = Lock()

    def add(self, data: VideoAnalysisCreate, created_at: datetime) -> VideoAnalysis:
        with self._lock:
            alerts = []
            for alert in data.alerts:
                alerts.append(_stored_alert(alert, self._next_alert_id))
                self._next_alert_id += 1
            analysis = VideoAnalysis(
                **data.model_dump(exclude={"alerts"}), id=uuid4(), alerts=alerts,
                alert_count=len(alerts), created_at=created_at,
            )
            self._analyses.append(analysis)
            return analysis

    def list(self, limit: int) -> Analyses:
        with self._lock:
            return list(reversed(self._analyses))[:limit]

    def get(self, analysis_id: UUID) -> VideoAnalysis | None:
        with self._lock:
            return next((analysis for analysis in self._analyses if analysis.id == analysis_id), None)


def _aware(value: datetime) -> datetime:
    # SQLite drops the timezone; PostgreSQL returns timestamptz in the session time zone.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _to_analysis(row: VideoAnalysisRow, alerts: AlertRows) -> VideoAnalysis:
    return VideoAnalysis(
        id=row.id, video_name=row.video_name, duration_sec=row.duration_sec,
        frames_analyzed=row.frames_analyzed, max_cattle=row.max_cattle, max_people=row.max_people,
        avg_confidence=row.avg_confidence, outside_ratio=row.outside_ratio,
        alert_count=row.alert_count, created_at=_aware(row.created_at),
        alerts=[
            VideoAnalysisAlert(id=alert.id, track_id=alert.track_id,
                               cls=VideoAlertClass(alert.cls), time_sec=alert.time_sec)
            for alert in alerts
        ],
    )


class SqlVideoAnalysisStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    def add(self, data: VideoAnalysisCreate, created_at: datetime) -> VideoAnalysis:
        analysis_id = uuid4()
        with self._sessions.begin() as session:
            row = VideoAnalysisRow(
                **data.model_dump(exclude={"alerts"}), id=analysis_id,
                alert_count=len(data.alerts), created_at=created_at,
            )
            session.add(row)
            alert_rows = [
                VideoAnalysisAlertRow(
                    analysis_id=analysis_id, track_id=alert.track_id,
                    cls=alert.cls.value, time_sec=alert.time_sec,
                )
                for alert in data.alerts
            ]
            session.add_all(alert_rows)
            session.flush()
            return _to_analysis(row, alert_rows)

    def list(self, limit: int) -> Analyses:
        with self._sessions() as session:
            rows = session.scalars(
                select(VideoAnalysisRow).order_by(VideoAnalysisRow.created_at.desc(), VideoAnalysisRow.id.desc())
                .limit(limit)
            ).all()
            return self._with_alerts(session, rows)

    def get(self, analysis_id: UUID) -> VideoAnalysis | None:
        with self._sessions() as session:
            row = session.get(VideoAnalysisRow, analysis_id)
            if row is None:
                return None
            return self._with_alerts(session, [row])[0]

    @staticmethod
    def _with_alerts(session: Session, rows: Sequence[VideoAnalysisRow]) -> Analyses:
        if not rows:
            return []
        alerts_by_analysis: dict[UUID, AlertRows] = {row.id: [] for row in rows}
        alerts = session.scalars(
            select(VideoAnalysisAlertRow)
            .where(VideoAnalysisAlertRow.analysis_id.in_(alerts_by_analysis))
            .order_by(VideoAnalysisAlertRow.id)
        ).all()
        for alert in alerts:
            alerts_by_analysis[alert.analysis_id].append(alert)
        return [_to_analysis(row, alerts_by_analysis[row.id]) for row in rows]

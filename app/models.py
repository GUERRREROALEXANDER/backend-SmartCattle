from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, DateTime, Float, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CameraRow(Base):
    __tablename__ = "cameras"
    __table_args__ = (
        CheckConstraint("reported_status IN ('online', 'offline', 'error')", name="ck_cameras_reported_status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    reported_status: Mapped[str] = mapped_column(String(16))
    last_report_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_online_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(300))
    frame_width: Mapped[int | None] = mapped_column(Integer)
    frame_height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_events_confidence"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32))
    # No foreign key: events are accepted even if the camera never reported its status.
    camera_id: Mapped[str] = mapped_column(String(64), index=True)
    detected_object: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    bbox: Mapped[list[float] | None] = mapped_column(JSON)

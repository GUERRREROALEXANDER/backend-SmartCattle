from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# The tables belong to the shared SmartCattle database (database/schema.sql). The models map only
# the columns the backend uses, under the API names (camera name as id, event_type, timestamp, bbox).
BIGINT_ID = BigInteger().with_variant(Integer, "sqlite")


class CameraRow(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(BIGINT_ID, Identity(always=True), primary_key=True)
    # The identifier used by the API and the AI service, e.g. "camera-01".
    name: Mapped[str] = mapped_column(Text, unique=True)
    # Added by the backend migration; NULL until the AI service reports this camera.
    reported_status: Mapped[str | None] = mapped_column(String(16))
    last_report_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_online_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(300))
    frame_width: Mapped[int | None] = mapped_column(Integer)
    frame_height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    stream_url: Mapped[str | None] = mapped_column(String(300))


class EventRow(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BIGINT_ID, Identity(always=True), primary_key=True)
    # The API event id. Other services may write events without one; those are not listed.
    ai_event_id: Mapped[UUID | None] = mapped_column(Uuid, unique=True)
    event_type: Mapped[str] = mapped_column("type", Text)
    camera_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("cameras.id", ondelete="SET NULL"))
    detected_object: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    bbox: Mapped[list[float] | None] = mapped_column("box", JSON().with_variant(JSONB, "postgresql"))
    timestamp: Mapped[datetime] = mapped_column("detected_at", DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserRow(Base):
    """The shared users table. Written only by this service.

    password_hash holds a bcrypt hash and nothing else; see
    database/schema.sql, which has specified that since before any code could
    produce one.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BIGINT_ID, Identity(always=True), primary_key=True)
    # Stored lower-cased. The shared schema enforces it with
    # CHECK (email = lower(email)), which is what makes the UNIQUE mean
    # anything: without it one address registers twice under two spellings.
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, server_default="worker")
    # Access is withdrawn by clearing this, never by deleting the row: an
    # account that is gone cannot be told apart from one that never existed.
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=func.true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Added by migration 0005 in the database repository.
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VideoAnalysisRow(Base):
    __tablename__ = "video_analyses"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    video_name: Mapped[str] = mapped_column(String(200))
    duration_sec: Mapped[float] = mapped_column(Float)
    frames_analyzed: Mapped[int] = mapped_column(Integer)
    max_cattle: Mapped[int] = mapped_column(Integer)
    max_people: Mapped[int] = mapped_column(Integer)
    avg_confidence: Mapped[float] = mapped_column(Float)
    outside_ratio: Mapped[float] = mapped_column(Float)
    alert_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VideoAnalysisAlertRow(Base):
    __tablename__ = "video_analysis_alerts"

    id: Mapped[int] = mapped_column(BIGINT_ID, Identity(always=True), primary_key=True)
    analysis_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("video_analyses.id", ondelete="CASCADE"), index=True,
    )
    track_id: Mapped[int] = mapped_column(Integer)
    cls: Mapped[str] = mapped_column(String(16))
    time_sec: Mapped[float] = mapped_column(Float)

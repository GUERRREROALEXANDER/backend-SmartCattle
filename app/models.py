from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, BigInteger, DateTime, Float, ForeignKey, Identity, Integer, String, Text, Uuid
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

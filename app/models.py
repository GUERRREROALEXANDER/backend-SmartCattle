from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    String,
    Table,
    Text,
    Uuid,
    func,
    true,
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
    # Where the camera is reached, with no credentials: those stay on the PC that opens it.
    source: Mapped[str] = mapped_column(Text, server_default="0")
    # False once it was removed from the dashboard. The row stays, for its history.
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    # Added by the backend migration; NULL until the AI service reports this camera.
    reported_status: Mapped[str | None] = mapped_column(String(16))
    last_report_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_online_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(300))
    frame_width: Mapped[int | None] = mapped_column(Integer)
    frame_height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    stream_url: Mapped[str | None] = mapped_column(String(300))
    # The account it belongs to. NULL for a camera the AI service reports that nobody saved: nobody sees it.
    account_id: Mapped[int | None] = mapped_column(BIGINT_ID, index=True)
    # The name its owner gave it. Set while it is saved on the Cameras page.
    display_name: Mapped[str | None] = mapped_column(Text)
    # The LAN address its owner typed (192.168.1.50), only to show it: an Imou camera opens by serial number.
    address: Mapped[str | None] = mapped_column(String(255))


class EventRow(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BIGINT_ID, Identity(always=True), primary_key=True)
    # The API event id. Other services may write events without one; those are not listed.
    ai_event_id: Mapped[UUID | None] = mapped_column(Uuid, unique=True)
    event_type: Mapped[str] = mapped_column("type", Text)
    # NOT NULL DEFAULT 'medium' in the shared schema, so a row always has one;
    # it may be the default rather than a considered value.
    level: Mapped[str] = mapped_column(Text, server_default="medium")
    camera_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("cameras.id", ondelete="SET NULL"))
    detected_object: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    bbox: Mapped[list[float] | None] = mapped_column(
        "box", JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"),
    )
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
    # Whose space this account works in: its own id, or the owner's who created it.
    account_id: Mapped[int | None] = mapped_column(BIGINT_ID, index=True)


class AnimalRow(Base):
    """The shared animals table.

    No numeric id: the ear tag is the primary key. It is the real identity of
    the animal -- the one physically on its ear and the one the staff use --
    and a second identifier would mean translating between the two on every
    query. The price is that a natural key changes, which is why
    events.animal_tag carries ON UPDATE CASCADE.
    """

    __tablename__ = "animals"

    tag: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    camera_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("cameras.id", ondelete="SET NULL"),
    )
    name: Mapped[str | None] = mapped_column(Text)
    # Written by the AI service when it recognises an individual animal, which
    # it cannot do yet.
    last_detection: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    account_id: Mapped[int | None] = mapped_column(BIGINT_ID, index=True)


class ZoneRow(Base):
    """The shared zones table: the safe zone of a camera.

    Coordinates are normalised from 0 to 1 and not stored in pixels. A zone in
    pixels is tied to the resolution it was drawn at, so swapping the camera,
    or the AI service processing at a different size, moves the zone without
    anyone touching it.
    """

    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(BIGINT_ID, Identity(always=True), primary_key=True)
    # NOT NULL with ON DELETE CASCADE: a zone without a camera means nothing.
    camera_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("cameras.id", ondelete="CASCADE"),
    )
    name: Mapped[str] = mapped_column(Text)
    x_min: Mapped[float] = mapped_column(Float)
    y_min: Mapped[float] = mapped_column(Float)
    x_max: Mapped[float] = mapped_column(Float)
    y_max: Mapped[float] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, server_default=func.true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Optional polygon, added to the shared schema by its migration 0020. When
    # present it defines the zone and the rectangle is its bounding box.
    #
    # none_as_null: without it None is written as the JSON value null, which
    # the shared CHECK (is_valid_zone_outline) rejects, so every rectangle-only
    # zone failed on PostgreSQL.
    outline: Mapped[list[dict[str, float]] | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"),
    )


class FarmRow(Base):
    """A farm registered by an owner. Backend-owned, like video_analyses.

    Not in the shared schema because no other service reads it yet; the AI
    service works per camera and never needs to know which farm a camera is on.
    """

    __tablename__ = "farms"

    id: Mapped[int] = mapped_column(BIGINT_ID, Identity(always=True), primary_key=True)
    # RESTRICT: an owner account is deactivated, never deleted, so a delete
    # reaching this table is a mistake worth refusing.
    owner_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    area_hectares: Mapped[float | None] = mapped_column(Float)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LotRow(Base):
    """A paddock, stable, pen or pasture inside a farm."""

    __tablename__ = "lots"

    id: Mapped[int] = mapped_column(BIGINT_ID, Identity(always=True), primary_key=True)
    farm_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("farms.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    area_square_meters: Mapped[float | None] = mapped_column(Float)
    length_meters: Mapped[float | None] = mapped_column(Float)
    width_meters: Mapped[float | None] = mapped_column(Float)
    capacity: Mapped[int | None] = mapped_column(Integer)
    boundary: Mapped[list[dict[str, float]] | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"),
    )
    camera_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("cameras.id", ondelete="SET NULL"))
    active: Mapped[bool] = mapped_column(Boolean, server_default=func.true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SecurityAlertRow(Base):
    """A dashboard incident raised from a live AI event.

    Not the shared `alerts` table: that one models outbound notifications, and
    its CHECK only admits delivery channels (email, whatsapp, ...). An incident
    someone acknowledges and resolves on the dashboard is a different thing.
    """

    __tablename__ = "security_alerts"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    # The API event id. SET NULL: an event purged by another service must not
    # take the incident history with it.
    event_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("events.ai_event_id", ondelete="SET NULL"),
    )
    camera_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("cameras.id", ondelete="SET NULL"))
    alert_type: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("users.id", ondelete="SET NULL"))


class CameraOccupancyRow(Base):
    """The latest head count per camera. One row each, overwritten.

    History is deliberately not kept (ARCHITECTURE section 8): the AI counts
    several times a second, and nobody reads yesterday's per-second counts.
    """

    __tablename__ = "camera_occupancy"

    camera_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("cameras.id", ondelete="CASCADE"), primary_key=True,
    )
    cattle_inside: Mapped[int] = mapped_column(Integer)
    cattle_outside: Mapped[int] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OccupancySampleRow(Base):
    """The head count per camera, one row per minute.

    The per-second counts are still not kept. One sample a minute -- the last
    count observed in it -- is enough to chart a day and to answer "how many
    were out at 3 pm", at 1 440 rows per camera per day instead of 86 400.
    """

    __tablename__ = "occupancy_samples"

    camera_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("cameras.id", ondelete="CASCADE"), primary_key=True,
    )
    minute: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    cattle_inside: Mapped[int] = mapped_column(Integer)
    cattle_outside: Mapped[int] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DetectionLogRow(Base):
    """What the AI saw on one camera in one minute: the last report of that minute."""

    __tablename__ = "detection_logs"

    camera_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("cameras.id", ondelete="CASCADE"), primary_key=True,
    )
    minute: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    cattle_lying: Mapped[int] = mapped_column(Integer)
    cattle_standing: Mapped[int] = mapped_column(Integer)
    cattle_walking: Mapped[int] = mapped_column(Integer)
    cattle_unknown: Mapped[int] = mapped_column(Integer)
    people: Mapped[int] = mapped_column(Integer)
    avg_confidence: Mapped[float | None] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WhatsappContactRow(Base):
    """A WhatsApp number its owner verified, and whether they want notices on it."""

    __tablename__ = "whatsapp_contacts"

    user_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    phone: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WhatsappMessageRow(Base):
    """A message for the WhatsApp service to send. The body is emptied once the message is settled."""

    __tablename__ = "whatsapp_messages"

    id: Mapped[int] = mapped_column(BIGINT_ID, Identity(always=True), primary_key=True)
    phone: Mapped[str] = mapped_column(String(16))
    body: Mapped[str] = mapped_column(Text)
    camera_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(200))


class CameraStatusChangeRow(Base):
    """A camera's reported status, recorded only when it changes."""

    __tablename__ = "camera_status_changes"

    id: Mapped[int] = mapped_column(BIGINT_ID, Identity(always=True), primary_key=True)
    camera_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("cameras.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(300))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLogRow(Base):
    """Who did what: sign-ins, account changes, alert handling, farm and zone edits."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BIGINT_ID, Identity(always=True), primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("users.id", ondelete="SET NULL"))
    # A copy, not a join: a failed sign-in has an address and no account, and
    # the trail must still read correctly after an account's email changes.
    actor_email: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, object] | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"),
    )
    ip_address: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _catalog_table(name: str) -> Table:
    return Table(
        name, Base.metadata,
        Column("code", Text, primary_key=True),
        Column("label", Text, nullable=False),
        Column("label_es", Text, nullable=False),
        Column("description", Text, nullable=False),
        Column("sort_order", Integer, nullable=False),
    )


#: The reference tables (migration 0015). Plain tables, not mapped classes:
#: they are read whole and never written by the application.
CATALOG_TABLES: dict[str, Table] = {
    name: _catalog_table(name)
    for name in (
        "event_types", "severity_levels", "alert_statuses", "camera_statuses",
        "lot_kinds", "user_roles", "animal_statuses", "video_upload_statuses",
    )
}


class VideoUploadRow(Base):
    """An uploaded video file and its place in the processing queue."""

    __tablename__ = "video_uploads"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    file_name: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(300), unique=True)
    lot_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("lots.id", ondelete="SET NULL"))
    uploaded_by: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("users.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(Text)
    frames_total: Mapped[int | None] = mapped_column(Integer)
    frames_processed: Mapped[int] = mapped_column(Integer, server_default="0")
    fps: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(String(300))
    analysis_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("video_analyses.id", ondelete="SET NULL"))
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    account_id: Mapped[int | None] = mapped_column(BIGINT_ID, index=True)


class VideoAnalysisAlertRow(Base):
    __tablename__ = "video_analysis_alerts"

    id: Mapped[int] = mapped_column(BIGINT_ID, Identity(always=True), primary_key=True)
    analysis_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("video_analyses.id", ondelete="CASCADE"), index=True,
    )
    track_id: Mapped[int] = mapped_column(Integer)
    cls: Mapped[str] = mapped_column(String(16))
    time_sec: Mapped[float] = mapped_column(Float)

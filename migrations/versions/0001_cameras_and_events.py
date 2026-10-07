"""Create cameras and events tables

Revision ID: 0001_cameras_and_events
Revises:
Create Date: 2026-10-06
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_cameras_and_events"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cameras",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("reported_status", sa.String(16), nullable=False),
        sa.Column("last_report_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_online_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("frame_width", sa.Integer, nullable=True),
        sa.Column("frame_height", sa.Integer, nullable=True),
        sa.Column("fps", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("reported_status IN ('online', 'offline', 'error')", name="ck_cameras_reported_status"),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("camera_id", sa.String(64), nullable=False),
        sa.Column("detected_object", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bbox", sa.JSON, nullable=True),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_events_confidence"),
    )
    op.create_index("ix_events_camera_id", "events", ["camera_id"])
    op.create_index("ix_events_received_at", "events", ["received_at"])


def downgrade() -> None:
    op.drop_index("ix_events_received_at", table_name="events")
    op.drop_index("ix_events_camera_id", table_name="events")
    op.drop_table("events")
    op.drop_table("cameras")

"""Add the camera status reported by the AI service

All columns are nullable, so services that do not report status are unaffected.

Revision ID: 0002_camera_status
Revises: 0001_shared_schema
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_camera_status"
down_revision = "0001_shared_schema"
branch_labels = None
depends_on = None

COLUMNS = {
    "reported_status": sa.String(16),
    "last_report_at": sa.DateTime(timezone=True),
    "last_online_at": sa.DateTime(timezone=True),
    "last_error": sa.String(300),
    "frame_width": sa.Integer(),
    "frame_height": sa.Integer(),
    "fps": sa.Float(),
    # Public base URL of the AI service video (serves /video.mjpg and /status).
    "stream_url": sa.String(300),
}


def upgrade() -> None:
    with op.batch_alter_table("cameras") as batch:
        for name, type_ in COLUMNS.items():
            batch.add_column(sa.Column(name, type_))
        batch.create_check_constraint(
            "ck_cameras_reported_status", "reported_status IN ('online', 'offline', 'error')",
        )


def downgrade() -> None:
    with op.batch_alter_table("cameras") as batch:
        batch.drop_constraint("ck_cameras_reported_status", type_="check")
        for name in reversed(COLUMNS):
            batch.drop_column(name)

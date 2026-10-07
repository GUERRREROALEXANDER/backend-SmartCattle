"""Add the public video URL reported by the AI service

Revision ID: 0002_camera_stream_url
Revises: 0001_cameras_and_events
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_camera_stream_url"
down_revision = "0001_cameras_and_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cameras", sa.Column("stream_url", sa.String(300), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cameras") as batch:
        batch.drop_column("stream_url")

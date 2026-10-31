"""Uploaded videos and their processing queue

Backend-owned. The file itself lives in object storage; this row tracks it.

Revision ID: 0010_video_uploads
Revises: 0009_camera_occupancy
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_video_uploads"
down_revision = "0009_camera_occupancy"
branch_labels = None
depends_on = None

BIGINT_ID = sa.BigInteger().with_variant(sa.Integer, "sqlite")
MAX_VIDEO_BYTES = 1024 ** 3


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("video_uploads"):
        return
    op.create_table(
        "video_uploads",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("file_name", sa.String(200), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("storage_key", sa.String(300), nullable=False, unique=True),
        sa.Column("lot_id", BIGINT_ID, sa.ForeignKey("lots.id", ondelete="SET NULL")),
        sa.Column("uploaded_by", BIGINT_ID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("frames_total", sa.Integer),
        sa.Column("frames_processed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("fps", sa.Float),
        sa.Column("error", sa.String(300)),
        sa.Column("analysis_id", sa.Uuid, sa.ForeignKey("video_analyses.id", ondelete="SET NULL")),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            f"size_bytes > 0 AND size_bytes <= {MAX_VIDEO_BYTES}", name="video_uploads_size_check",
        ),
        sa.CheckConstraint(
            "status IN ('pending_upload', 'queued', 'processing', 'completed', 'failed')",
            name="video_uploads_status_check",
        ),
        sa.CheckConstraint("frames_processed >= 0", name="video_uploads_frames_check"),
    )
    # The claim query: oldest first among queued and processing uploads.
    op.create_index("video_uploads_status_created_idx", "video_uploads", ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("video_uploads")

"""Store simulated video analyses and their alerts

Revision ID: 0003_video_analyses
Revises: 0002_camera_status
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_video_analyses"
down_revision = "0002_camera_status"
branch_labels = None
depends_on = None

BIGINT_ID = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    op.create_table(
        "video_analyses",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("video_name", sa.String(200), nullable=False),
        sa.Column("duration_sec", sa.Float, nullable=False),
        sa.Column("frames_analyzed", sa.Integer, nullable=False),
        sa.Column("max_cattle", sa.Integer, nullable=False),
        sa.Column("max_people", sa.Integer, nullable=False),
        sa.Column("avg_confidence", sa.Float, nullable=False),
        sa.Column("outside_ratio", sa.Float, nullable=False),
        sa.Column("alert_count", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("duration_sec >= 0", name="ck_video_analyses_duration"),
        sa.CheckConstraint("frames_analyzed >= 0", name="ck_video_analyses_frames"),
        sa.CheckConstraint("max_cattle >= 0 AND max_people >= 0", name="ck_video_analyses_max_counts"),
        sa.CheckConstraint("avg_confidence BETWEEN 0 AND 1", name="ck_video_analyses_confidence"),
        sa.CheckConstraint("outside_ratio BETWEEN 0 AND 1", name="ck_video_analyses_outside_ratio"),
        sa.CheckConstraint("alert_count >= 0", name="ck_video_analyses_alert_count"),
    )
    op.create_index("ix_video_analyses_created_at", "video_analyses", ["created_at"])
    op.create_table(
        "video_analysis_alerts",
        sa.Column("id", BIGINT_ID, sa.Identity(always=True), primary_key=True),
        sa.Column(
            "analysis_id", sa.Uuid,
            sa.ForeignKey("video_analyses.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("track_id", sa.Integer, nullable=False),
        sa.Column("cls", sa.String(16), nullable=False),
        sa.Column("time_sec", sa.Float, nullable=False),
        sa.CheckConstraint("track_id >= 0", name="ck_video_analysis_alerts_track"),
        sa.CheckConstraint("cls IN ('cattle', 'person')", name="ck_video_analysis_alerts_class"),
        sa.CheckConstraint("time_sec >= 0", name="ck_video_analysis_alerts_time"),
    )
    op.create_index(
        "ix_video_analysis_alerts_analysis_id", "video_analysis_alerts", ["analysis_id"],
    )


def downgrade() -> None:
    op.drop_table("video_analysis_alerts")
    op.drop_table("video_analyses")

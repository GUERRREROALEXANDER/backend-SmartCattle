"""Latest head count per camera

Backend-owned. One row per camera, overwritten on every report.

Revision ID: 0009_camera_occupancy
Revises: 0008_security_alerts
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_camera_occupancy"
down_revision = "0008_security_alerts"
branch_labels = None
depends_on = None

BIGINT_ID = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("camera_occupancy"):
        return
    op.create_table(
        "camera_occupancy",
        sa.Column(
            "camera_id", BIGINT_ID, sa.ForeignKey("cameras.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("cattle_inside", sa.Integer, nullable=False),
        sa.Column("cattle_outside", sa.Integer, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("cattle_inside >= 0", name="camera_occupancy_inside_check"),
        sa.CheckConstraint("cattle_outside >= 0", name="camera_occupancy_outside_check"),
    )


def downgrade() -> None:
    op.drop_table("camera_occupancy")

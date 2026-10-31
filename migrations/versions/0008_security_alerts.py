"""Dashboard security alerts

Backend-owned. Deliberately separate from the shared `alerts` table, whose
CHECK only admits notification channels.

Revision ID: 0008_security_alerts
Revises: 0007_farms_and_lots
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_security_alerts"
down_revision = "0007_farms_and_lots"
branch_labels = None
depends_on = None

BIGINT_ID = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("security_alerts"):
        return
    op.create_table(
        "security_alerts",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("event_id", sa.Uuid, sa.ForeignKey("events.ai_event_id", ondelete="SET NULL")),
        sa.Column("camera_id", BIGINT_ID, sa.ForeignKey("cameras.id", ondelete="SET NULL")),
        sa.Column("alert_type", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("message", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_by", BIGINT_ID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by", BIGINT_ID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.CheckConstraint("severity IN ('low', 'medium', 'high')", name="security_alerts_severity_check"),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved')", name="security_alerts_status_check",
        ),
    )
    # The dashboard's question is "what is still open, newest first".
    op.create_index("security_alerts_status_created_idx", "security_alerts", ["status", sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_table("security_alerts")

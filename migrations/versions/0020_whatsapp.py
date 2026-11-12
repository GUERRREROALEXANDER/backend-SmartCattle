"""Verified WhatsApp numbers and the queue of messages to send to them

Backend-owned, like the video queue: no other service reads them. The sending
service claims messages through the API and never touches the database.

Created only if missing, so the order in which this and the database repository
reach a database does not matter.

Revision ID: 0020_whatsapp
Revises: 0019_detection_logs
Create Date: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_whatsapp"
down_revision = "0019_detection_logs"
branch_labels = None
depends_on = None

BIGINT_ID = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("whatsapp_contacts"):
        op.create_table(
            "whatsapp_contacts",
            sa.Column("user_id", BIGINT_ID, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("phone", sa.String(16), nullable=False),
            sa.Column("enabled", sa.Boolean, nullable=False),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        )
    if not inspector.has_table("whatsapp_messages"):
        op.create_table(
            "whatsapp_messages",
            sa.Column("id", BIGINT_ID, sa.Identity(always=True), primary_key=True),
            sa.Column("phone", sa.String(16), nullable=False),
            sa.Column("body", sa.Text, nullable=False),
            sa.Column("camera_id", sa.String(64)),
            sa.Column("status", sa.Text, nullable=False),
            sa.Column("attempts", sa.Integer, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("claimed_at", sa.DateTime(timezone=True)),
            sa.Column("finished_at", sa.DateTime(timezone=True)),
            sa.Column("error", sa.String(200)),
            sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name="whatsapp_messages_status_check"),
            sa.CheckConstraint("attempts >= 0", name="whatsapp_messages_attempts_check"),
        )
        # The service polls for pending messages; alerts look back over one camera's recent ones.
        op.create_index("whatsapp_messages_status_idx", "whatsapp_messages", ["status", "id"])
        op.create_index("whatsapp_messages_camera_idx", "whatsapp_messages", ["camera_id", "created_at"])


def downgrade() -> None:
    op.drop_table("whatsapp_messages")
    op.drop_table("whatsapp_contacts")

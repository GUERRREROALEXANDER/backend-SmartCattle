"""History tables: head count per minute, camera status changes, audit log

Until now the database kept only the present: the latest head count and the
current camera status, each overwritten. These keep the past, at a rate a
free-tier database can hold:

- occupancy_samples: one row per camera per minute, not per report.
- camera_status_changes: one row per change, not per report.
- audit_log: one row per action a person took.

The database repository creates the same tables in its migration 0027, which
0012 applies first on PostgreSQL, so each one is created only if missing.

Revision ID: 0016_history
Revises: 0015_catalogs
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016_history"
down_revision = "0015_catalogs"
branch_labels = None
depends_on = None

BIGINT_ID = sa.BigInteger().with_variant(sa.Integer, "sqlite")
JSON_DOCUMENT = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("occupancy_samples"):
        _occupancy_samples()
    if not inspector.has_table("camera_status_changes"):
        _camera_status_changes()
    if not inspector.has_table("audit_log"):
        _audit_log()


def _occupancy_samples() -> None:
    op.create_table(
        "occupancy_samples",
        # The primary key leads with camera_id, so it also serves the foreign key.
        sa.Column("camera_id", BIGINT_ID, sa.ForeignKey("cameras.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("minute", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("cattle_inside", sa.Integer, nullable=False),
        sa.Column("cattle_outside", sa.Integer, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("cattle_inside >= 0", name="occupancy_samples_inside_check"),
        sa.CheckConstraint("cattle_outside >= 0", name="occupancy_samples_outside_check"),
    )
    # Reports ask for every camera over a time range.
    op.create_index("occupancy_samples_minute_idx", "occupancy_samples", ["minute"])


def _camera_status_changes() -> None:
    op.create_table(
        "camera_status_changes",
        sa.Column("id", BIGINT_ID, sa.Identity(always=True), primary_key=True),
        sa.Column(
            "camera_id", BIGINT_ID, sa.ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("error", sa.String(300)),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('online', 'offline', 'error')", name="camera_status_changes_status_check",
        ),
    )
    op.create_index(
        "camera_status_changes_camera_id_changed_at_idx", "camera_status_changes", ["camera_id", "changed_at"],
    )


def _audit_log() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", BIGINT_ID, sa.Identity(always=True), primary_key=True),
        sa.Column("actor_id", BIGINT_ID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("actor_email", sa.Text),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("target_type", sa.Text),
        sa.Column("target_id", sa.Text),
        sa.Column("detail", JSON_DOCUMENT),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("audit_log_created_at_idx", "audit_log", ["created_at"])
    op.create_index("audit_log_actor_id_idx", "audit_log", ["actor_id"])
    op.create_index("audit_log_action_created_at_idx", "audit_log", ["action", "created_at"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("camera_status_changes")
    op.drop_table("occupancy_samples")

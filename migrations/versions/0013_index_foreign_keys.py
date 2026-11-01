"""Index every foreign key of the backend-owned tables

The database repository's rule, checked by its test 05: every foreign key is
served by an index whose first column is the key. PostgreSQL does not create
them, and without one, deleting the parent row scans the whole child table
to apply the SET NULL or CASCADE, with the parent row locked. These eight
were missing; the others are already the first column of a unique key.

IF NOT EXISTS, so a database that already has them is left alone.

Revision ID: 0013_index_foreign_keys
Revises: 0012_shared_migrations
Create Date: 2026-10-31
"""
from alembic import op

revision = "0013_index_foreign_keys"
down_revision = "0012_shared_migrations"
branch_labels = None
depends_on = None

INDEXES = {
    "lots_camera_id_idx": ("lots", "camera_id"),
    "security_alerts_event_id_idx": ("security_alerts", "event_id"),
    "security_alerts_camera_id_idx": ("security_alerts", "camera_id"),
    "security_alerts_acknowledged_by_idx": ("security_alerts", "acknowledged_by"),
    "security_alerts_resolved_by_idx": ("security_alerts", "resolved_by"),
    "video_uploads_lot_id_idx": ("video_uploads", "lot_id"),
    "video_uploads_uploaded_by_idx": ("video_uploads", "uploaded_by"),
    "video_uploads_analysis_id_idx": ("video_uploads", "analysis_id"),
}


def upgrade() -> None:
    for name, (table, column) in INDEXES.items():
        op.create_index(name, table, [column], if_not_exists=True)


def downgrade() -> None:
    for name, (table, _column) in INDEXES.items():
        op.drop_index(name, table_name=table, if_exists=True)

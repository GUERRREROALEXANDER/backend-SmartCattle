"""Length and width for lots

A rectangular paddock or stable is described by its measurements; its area
follows from them. Both or neither: one side alone describes no shape. The
area itself stays optional and independent, for lots that are not rectangles.

Batch mode, because SQLite can only add a CHECK by rebuilding the table.

Revision ID: 0014_lot_dimensions
Revises: 0013_index_foreign_keys
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_lot_dimensions"
down_revision = "0013_index_foreign_keys"
branch_labels = None
depends_on = None

CHECKS = {
    "lots_length_meters_check": "length_meters > 0",
    "lots_width_meters_check": "width_meters > 0",
    "lots_dimensions_pair_check": "(length_meters IS NULL) = (width_meters IS NULL)",
}


def upgrade() -> None:
    with op.batch_alter_table("lots") as batch:
        batch.add_column(sa.Column("length_meters", sa.Float))
        batch.add_column(sa.Column("width_meters", sa.Float))
        for name, condition in CHECKS.items():
            batch.create_check_constraint(name, condition)


def downgrade() -> None:
    with op.batch_alter_table("lots") as batch:
        for name in CHECKS:
            batch.drop_constraint(name, type_="check")
        batch.drop_column("width_meters")
        batch.drop_column("length_meters")

"""Make sure the shared animals table exists

Same shape as 0004_users. On PostgreSQL the table comes from
database/schema.sql, applied by revision 0001. SQLite -- the test database --
never had it, because 0001 only hand-builds cameras and events there.

Revision ID: 0005_animals
Revises: 0004_users
Create Date: 2026-10-29
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_animals"
down_revision = "0004_users"
branch_labels = None
depends_on = None

BIGINT_ID = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("animals"):
        # PostgreSQL: created by database/schema.sql in revision 0001.
        return

    op.create_table(
        "animals",
        # The ear tag is the primary key. No numeric id: the tag is the real
        # identity of the animal, and a second identifier would mean
        # translating between the two on every query.
        sa.Column("tag", sa.Text, primary_key=True),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("camera_id", BIGINT_ID, sa.ForeignKey("cameras.id", ondelete="SET NULL")),
        sa.Column("name", sa.Text),
        sa.Column("last_detection", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('active', 'inactive', 'lost')", name="animals_status_check"),
    )
    # Animals are deactivated and never deleted, so "the active herd" is the
    # common query and without this it scans the table.
    op.create_index("animals_status_idx", "animals", ["status"])
    op.create_index("animals_camera_id_idx", "animals", ["camera_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # The shared table holds other services' data; dropping it is not the
        # backend's call.
        return
    op.drop_index("animals_camera_id_idx", table_name="animals")
    op.drop_index("animals_status_idx", table_name="animals")
    op.drop_table("animals")

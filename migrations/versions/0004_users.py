"""Make sure the shared users table exists

On PostgreSQL it is already there: revision 0001 applies database/schema.sql,
which creates it. This revision is what gives SQLite -- the test database --
the same table, since 0001 only hand-builds cameras and events there.

Nothing is altered when the table exists, so an established shared database is
untouched.

Revision ID: 0004_users
Revises: 0003_video_analyses
Create Date: 2026-10-29
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_users"
down_revision = "0003_video_analyses"
branch_labels = None
depends_on = None

BIGINT_ID = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("users"):
        # PostgreSQL: created by database/schema.sql in revision 0001.
        return

    op.create_table(
        "users",
        sa.Column("id", BIGINT_ID, sa.Identity(always=True), primary_key=True),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("full_name", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False, server_default="worker"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # Present from the start here; the shared schema gained it in its own
        # migration 0005.
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        # Mirrors the shared schema, including the lower-case rule that is what
        # makes the UNIQUE above mean anything.
        sa.CheckConstraint("email = lower(email)", name="users_email_check"),
        sa.CheckConstraint("role IN ('owner', 'worker')", name="users_role_check"),
    )
    op.create_index("users_role_idx", "users", ["role"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # The shared table holds other services' data; dropping it is not the
        # backend's call.
        return
    op.drop_index("users_role_idx", table_name="users")
    op.drop_table("users")

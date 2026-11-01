"""Make sure users.last_login_at exists

The database repository added this column to the shared users table in its
own migration 0005, but database/schema.sql, which revision 0001 applies to an
empty PostgreSQL database, predates it, and 0004 leaves an existing users
table alone. A fresh PostgreSQL database therefore had no column for the
backend to write on registration and login, and every registration failed.
SQLite never showed it, because there 0004 creates the table with the column.

Adding it only when missing leaves a database that already has it untouched.

Revision ID: 0011_users_last_login
Revises: 0010_video_uploads
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_users_last_login"
down_revision = "0010_video_uploads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "last_login_at" not in columns:
        op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    # Nothing to undo: the column belongs to the shared schema, and on SQLite
    # revision 0004 created it. Dropping it here would break both.
    pass

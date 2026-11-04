"""Separate accounts: each registered person gets a space of their own

Until now every account shared one farm. An account now works in the space of
account_id: its own id when it registered, the owner's when an owner created it.
Cameras, animals and video analyses carry the account they belong to; the rest
follows from those (events and alerts through their camera, farms and uploads
through who made them).

Everything already stored was the one farm's, so it goes to its first owner.
A camera that was reporting stays on that owner's Cameras page.

Revision ID: 0018_accounts
Revises: 0017_report_views
Create Date: 2026-11-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_accounts"
down_revision = "0017_report_views"
branch_labels = None
depends_on = None

BIGINT_ID = sa.BigInteger().with_variant(sa.Integer, "sqlite")
TABLES = ("users", "cameras", "animals", "video_analyses")
FIRST_OWNER = "(SELECT MIN(id) FROM users WHERE role = 'owner')"


def upgrade() -> None:
    # The database repository creates cameras.account_id, display_name and this
    # index too, and 0012 applies its SQL first on a fresh PostgreSQL database.
    inspector = sa.inspect(op.get_bind())
    for table in TABLES:
        if "account_id" not in {column["name"] for column in inspector.get_columns(table)}:
            op.add_column(table, sa.Column("account_id", BIGINT_ID, nullable=True))
        index = f"ix_{table}_account_id"
        if index not in {item["name"] for item in inspector.get_indexes(table)}:
            op.create_index(index, table, ["account_id"])
    if "display_name" not in {column["name"] for column in inspector.get_columns("cameras")}:
        op.add_column("cameras", sa.Column("display_name", sa.Text(), nullable=True))
    # Only what is still empty, so a database that already has owners and names keeps them.
    for table in TABLES:
        op.execute(f"UPDATE {table} SET account_id = {FIRST_OWNER} WHERE account_id IS NULL")
    op.execute(
        "UPDATE cameras SET display_name = name "
        "WHERE display_name IS NULL AND active AND last_report_at IS NOT NULL AND reported_status IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("cameras", "display_name")
    for table in TABLES:
        op.drop_index(f"ix_{table}_account_id", table_name=table)
        op.drop_column(table, "account_id")

"""Apply the database repository's migrations to the shared schema

Revision 0001 applies database/schema.sql, which describes the shared
TABLES. Their indexes, views, comments, the event retention function and a
few later columns and constraints arrived afterwards, as numbered migrations
in the database repository. Its own install.sql warns that a database built
from schema.sql alone "is left without all of that". That was every fresh
deployment of this backend: CI, docker-compose, and anyone deploying from
scratch.

database/migrations/ is a copy of that repository's migrations folder. Every
file there is written to be safe to run twice, so applying all of them is a
no-op on Neon, where they already ran, and completes a fresh database.

Revision ID: 0012_shared_migrations
Revises: 0011_users_last_login
Create Date: 2026-10-31
"""
from pathlib import Path

from alembic import op

revision = "0012_shared_migrations"
down_revision = "0011_users_last_login"
branch_labels = None
depends_on = None

SHARED_MIGRATIONS = Path(__file__).resolve().parents[2] / "database" / "migrations"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # The files are PostgreSQL (DO blocks, views, plpgsql). SQLite, the
        # test database, only ever gets the columns the backend uses.
        return
    # The driver's own cursor, without parameters: through exec_driver_sql
    # psycopg reads every % in the files (format() patterns, LIKE) as a
    # placeholder. Same connection, so the same migration transaction.
    cursor = bind.connection.dbapi_connection.cursor()
    try:
        # Before this revision existed, 0006 created zones.outline as json,
        # which the shared outline validator (jsonb) cannot check. A database
        # built that way is brought in line first; on any other it is a no-op.
        cursor.execute(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = 'zones' AND column_name = 'outline' AND data_type = 'json') THEN "
            "ALTER TABLE zones ALTER COLUMN outline TYPE jsonb USING outline::jsonb; "
            "END IF; END $$"
        )
        for path in sorted(SHARED_MIGRATIONS.glob("*.sql")):
            cursor.execute(path.read_text(encoding="utf-8"))
    finally:
        cursor.close()


def downgrade() -> None:
    # Shared schema: the database repository owns it and has no down
    # migrations. Removing indexes and views other services read is not the
    # backend's call.
    pass

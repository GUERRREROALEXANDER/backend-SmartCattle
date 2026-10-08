"""Use the shared SmartCattle database schema

The schema belongs to the database repository (github.com/walterjojoa/BASEDATOSSmartCattle);
database/schema.sql is a copy of it. On PostgreSQL it is applied only when the database is
still empty, so an existing shared database is never modified here. Other databases (SQLite in
tests) get the cameras and events columns the backend uses.

Revision ID: 0001_shared_schema
Revises:
Create Date: 2026-10-07
"""
from pathlib import Path

from alembic import op
import sqlalchemy as sa

revision = "0001_shared_schema"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = Path(__file__).resolve().parents[2] / "database" / "schema.sql"


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("cameras"):
        return
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(SCHEMA.read_text(encoding="utf-8"))
        return
    op.create_table(
        "cameras",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("source", sa.Text, nullable=False, server_default="0"),
        sa.Column("location", sa.Text),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("level", sa.Text, nullable=False, server_default="medium"),
        sa.Column("source", sa.Text, nullable=False, server_default="camera"),
        sa.Column("camera_id", sa.Integer, sa.ForeignKey("cameras.id", ondelete="SET NULL")),
        sa.Column("detected_object", sa.Text),
        sa.Column("confidence", sa.Float),
        sa.Column("box", sa.JSON),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("ai_event_id", sa.Uuid, unique=True),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_events_confidence"),
    )


def downgrade() -> None:
    # The shared tables hold other services' data; dropping them is not the backend's call.
    pass

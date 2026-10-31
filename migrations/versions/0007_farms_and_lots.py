"""Farms and their lots

Backend-owned tables, like video_analyses: no other service reads or writes
them yet, so they are created here on every dialect and dropped on downgrade.

Revision ID: 0007_farms_and_lots
Revises: 0006_zones
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007_farms_and_lots"
down_revision = "0006_zones"
branch_labels = None
depends_on = None

BIGINT_ID = sa.BigInteger().with_variant(sa.Integer, "sqlite")
JSON_DOCUMENT = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("farms"):
        op.create_table(
            "farms",
            sa.Column("id", BIGINT_ID, sa.Identity(always=True), primary_key=True),
            sa.Column(
                "owner_id", BIGINT_ID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
            ),
            sa.Column("name", sa.Text, nullable=False),
            sa.Column("location", sa.Text),
            sa.Column("area_hectares", sa.Float),
            sa.Column("latitude", sa.Float),
            sa.Column("longitude", sa.Float),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("owner_id", "name", name="farms_owner_id_name_key"),
            sa.CheckConstraint("area_hectares > 0", name="farms_area_hectares_check"),
            sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="farms_latitude_check"),
            sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="farms_longitude_check"),
            # Half a coordinate is not a place: both or neither.
            sa.CheckConstraint(
                "(latitude IS NULL) = (longitude IS NULL)", name="farms_coordinates_pair_check",
            ),
        )
        op.create_index("farms_owner_id_idx", "farms", ["owner_id"])

    if not inspector.has_table("lots"):
        op.create_table(
            "lots",
            sa.Column("id", BIGINT_ID, sa.Identity(always=True), primary_key=True),
            sa.Column(
                "farm_id", BIGINT_ID, sa.ForeignKey("farms.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("name", sa.Text, nullable=False),
            sa.Column("kind", sa.Text, nullable=False),
            sa.Column("area_square_meters", sa.Float),
            sa.Column("capacity", sa.Integer),
            sa.Column("boundary", JSON_DOCUMENT),
            sa.Column("camera_id", BIGINT_ID, sa.ForeignKey("cameras.id", ondelete="SET NULL")),
            sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("farm_id", "name", name="lots_farm_id_name_key"),
            sa.CheckConstraint("kind IN ('paddock', 'stable', 'pen', 'pasture')", name="lots_kind_check"),
            sa.CheckConstraint("area_square_meters > 0", name="lots_area_square_meters_check"),
            sa.CheckConstraint("capacity >= 0", name="lots_capacity_check"),
        )
        op.create_index("lots_farm_id_idx", "lots", ["farm_id"])


def downgrade() -> None:
    op.drop_table("lots")
    op.drop_table("farms")

"""Make sure the shared zones table exists

Same shape as 0004_users and 0005_animals. On PostgreSQL the table comes from
database/schema.sql, applied by revision 0001; SQLite never had it.

Also adds the outline column, which the database repository added to the
shared schema in its own migration 0020.

Revision ID: 0006_zones
Revises: 0005_animals
Create Date: 2026-10-29
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_zones"
down_revision = "0005_animals"
branch_labels = None
depends_on = None

BIGINT_ID = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("zones"):
        op.create_table(
            "zones",
            sa.Column("id", BIGINT_ID, sa.Identity(always=True), primary_key=True),
            sa.Column(
                "camera_id", BIGINT_ID,
                sa.ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("name", sa.Text, nullable=False),
            sa.Column("x_min", sa.Float, nullable=False),
            sa.Column("y_min", sa.Float, nullable=False),
            sa.Column("x_max", sa.Float, nullable=False),
            sa.Column("y_max", sa.Float, nullable=False),
            sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            # One zone per name per camera. Two cameras may each have a
            # "Safe zone"; one camera may not have two.
            sa.UniqueConstraint("camera_id", "name", name="zones_camera_id_name_key"),
            sa.CheckConstraint("x_min BETWEEN 0 AND 1", name="zones_x_min_check"),
            sa.CheckConstraint("y_min BETWEEN 0 AND 1", name="zones_y_min_check"),
            sa.CheckConstraint("x_max BETWEEN 0 AND 1", name="zones_x_max_check"),
            sa.CheckConstraint("y_max BETWEEN 0 AND 1", name="zones_y_max_check"),
            # Rejects the inverted rectangle, which would make the whole frame
            # read as outside the zone.
            sa.CheckConstraint("x_min < x_max AND y_min < y_max", name="zones_area_check"),
        )

    # Present in the shared schema since its migration 0020; added here for
    # databases that predate it and for SQLite.
    if "outline" not in {column["name"] for column in inspector.get_columns("zones")}:
        op.add_column("zones", sa.Column("outline", sa.JSON))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # The shared table holds other services' data; dropping it is not the
        # backend's call. The outline column is shared too.
        return
    op.drop_table("zones")

"""What the AI saw per camera and minute, cattle split by posture

The daily report needs how long the herd spent lying or on its feet. One row per
camera per minute answers that at 1 440 rows a day, where one per detection would
store what ARCHITECTURE section 8 rules out: YOLO runs several times per second.

Created only if missing, so the order in which this and the database repository
reach a database does not matter.

Revision ID: 0019_detection_logs
Revises: 0018_accounts
Create Date: 2026-11-03
"""
from alembic import op
import sqlalchemy as sa

revision = "0019_detection_logs"
down_revision = "0018_accounts"
branch_labels = None
depends_on = None

BIGINT_ID = sa.BigInteger().with_variant(sa.Integer, "sqlite")
COUNTS = ("cattle_lying", "cattle_standing", "cattle_walking", "cattle_unknown", "people")


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("detection_logs"):
        return
    op.create_table(
        "detection_logs",
        # The primary key leads with camera_id, so it also serves the foreign key.
        sa.Column("camera_id", BIGINT_ID, sa.ForeignKey("cameras.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("minute", sa.DateTime(timezone=True), primary_key=True),
        *(sa.Column(name, sa.Integer, nullable=False) for name in COUNTS),
        sa.Column("avg_confidence", sa.Float),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        *(sa.CheckConstraint(f"{name} >= 0", name=f"detection_logs_{name}_check") for name in COUNTS),
        sa.CheckConstraint("avg_confidence BETWEEN 0 AND 1", name="detection_logs_avg_confidence_check"),
    )
    # The report asks for every camera of an account over one day.
    op.create_index("detection_logs_minute_idx", "detection_logs", ["minute"])


def downgrade() -> None:
    op.drop_table("detection_logs")

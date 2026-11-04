"""The new anomaly types, and alerts sent outside the dashboard

The catalogs get a row per new event type, because tests/test_catalogs.py compares them with the enum. The rows are a
frozen copy of app/services/catalog_store.py:CATALOGS as of this revision.

notification_settings and notification_deliveries belong to the backend, like farms and security_alerts.

Revision ID: 0020_anomalies_notifications
Revises: 0019_detection_logs
Create Date: 2026-11-03
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_anomalies_notifications"
down_revision = "0019_detection_logs"
branch_labels = None
depends_on = None

BIGINT_ID = sa.BigInteger().with_variant(sa.Integer, "sqlite")
EVENT_TYPES = (
    ("cattle_down", "Cattle down", "Animal caído",
     "An animal has been lying without moving for far longer than a cow rests", 2),
    ("possible_intrusion", "Possible intrusion", "Posible intrusión",
     "A person was seen during the camera's night hours", 3),
    ("external_animal_detected", "External animal", "Animal externo",
     "A dog or a predator was seen near the herd", 4),
)
SEVERITIES = {
    "low": ("Detector confidence below 0.6", "Doubtful detection, confidence below 0.6"),
    "medium": ("Detector confidence from 0.6 to 0.9", "Confidence from 0.6 to 0.9, or a dog or predator in view"),
    "high": ("Detector confidence of 0.9 or more", "Confidence of 0.9 or more, an animal down, or a person at night"),
}

CATALOG = sa.table(
    "event_types", sa.column("code", sa.Text), sa.column("label", sa.Text), sa.column("label_es", sa.Text),
    sa.column("description", sa.Text), sa.column("sort_order", sa.Integer),
)
SEVERITY = sa.table("severity_levels", sa.column("code", sa.Text), sa.column("description", sa.Text))


def upgrade() -> None:
    bind = op.get_bind()
    known = set(bind.scalars(sa.select(CATALOG.c.code)))
    for code, label, label_es, description, order in EVENT_TYPES:
        if code not in known:
            op.execute(CATALOG.insert().values(
                code=code, label=label, label_es=label_es, description=description, sort_order=order,
            ))
    for code, (_old, new) in SEVERITIES.items():
        op.execute(SEVERITY.update().where(SEVERITY.c.code == code).values(description=new))

    op.create_table(
        "notification_settings",
        sa.Column("account_id", BIGINT_ID, primary_key=True, autoincrement=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("whatsapp_phone", sa.String(16)),
        sa.Column("telegram_chat_id", sa.String(32)),
        sa.Column("min_severity", sa.Text, nullable=False, server_default="medium"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("min_severity IN ('low', 'medium', 'high')", name="notification_settings_severity_check"),
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("account_id", BIGINT_ID, nullable=False),
        sa.Column("alert_id", sa.Uuid, sa.ForeignKey("security_alerts.id", ondelete="SET NULL")),
        sa.Column("camera_id", sa.Text),
        sa.Column("alert_type", sa.Text, nullable=False),
        sa.Column("channel", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("detail", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("channel IN ('whatsapp', 'telegram')", name="notification_deliveries_channel_check"),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name="notification_deliveries_status_check"),
    )
    op.create_index("ix_notification_deliveries_account_id", "notification_deliveries", ["account_id"])
    op.create_index("ix_notification_deliveries_created_at", "notification_deliveries", ["created_at"])
    op.create_index("ix_notification_deliveries_alert_id", "notification_deliveries", ["alert_id"])


def downgrade() -> None:
    op.drop_table("notification_deliveries")
    op.drop_table("notification_settings")
    for code, (old, _new) in SEVERITIES.items():
        op.execute(SEVERITY.update().where(SEVERITY.c.code == code).values(description=old))
    op.execute(CATALOG.delete().where(CATALOG.c.code.in_([row[0] for row in EVENT_TYPES])))

"""Reserved revision kept so databases already upgraded past it still resolve.

It changes nothing. A fresh database gets no table from it.

Revision ID: 0020_whatsapp
Revises: 0019_detection_logs
"""

revision = "0020_whatsapp"
down_revision = "0019_detection_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
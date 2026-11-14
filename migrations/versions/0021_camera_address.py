"""Reserved revision kept so databases already upgraded past it still resolve.

It changes nothing. A fresh database gets no column from it.

Revision ID: 0021_camera_address
Revises: 0020_whatsapp
"""

revision = "0021_camera_address"
down_revision = "0020_whatsapp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
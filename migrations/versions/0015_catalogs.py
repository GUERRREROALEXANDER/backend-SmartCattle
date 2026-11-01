"""Reference tables for the coded columns

One table per catalog, each row a code with an English and a Spanish label.
The frontend builds its selects and badges from them (GET /api/catalogs)
instead of hard-coding the lists.

The coded columns keep their CHECK constraints and get no foreign key to
their catalog. The CHECK already refuses an unknown code, and a foreign key
would need an index of its own on every coded column (the database
repository's rule) to protect against deleting a catalog row, which nothing
does. What a foreign key would add -- that every stored code has a label --
is covered by tests/test_catalogs.py, which compares each catalog with the
enum the API validates against.

The rows are a frozen copy of app/services/catalog_store.py:CATALOGS as of
this revision; tests/test_catalogs.py checks the two still agree.

The database repository creates the same tables in its migration 0026, which
0012 applies first on PostgreSQL. A catalog that already exists is skipped,
rows included: they came from the same source.

Revision ID: 0015_catalogs
Revises: 0014_lot_dimensions
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_catalogs"
down_revision = "0014_lot_dimensions"
branch_labels = None
depends_on = None

ROWS: dict[str, list[tuple[str, str, str, str]]] = {
    "event_types": [
        ("cattle_out_of_zone", "Cattle out of zone", "Ganado fuera de zona",
         "An animal was detected outside its camera's safe zone"),
    ],
    "severity_levels": [
        ("low", "Low", "Baja", "Detector confidence below 0.6"),
        ("medium", "Medium", "Media", "Detector confidence from 0.6 to 0.9"),
        ("high", "High", "Alta", "Detector confidence of 0.9 or more"),
    ],
    "alert_statuses": [
        ("open", "Open", "Abierta", "Raised and not yet seen by anyone"),
        ("acknowledged", "Acknowledged", "Atendida", "Someone is handling it"),
        ("resolved", "Resolved", "Resuelta", "Closed, as handled or as a false alarm"),
    ],
    "camera_statuses": [
        ("online", "Online", "En línea", "Reporting and streaming"),
        ("offline", "Offline", "Desconectada", "Reported offline, or silent for longer than the offline threshold"),
        ("error", "Error", "Error", "Reporting a fault"),
    ],
    "lot_kinds": [
        ("paddock", "Paddock", "Potrero", "Fenced grazing area"),
        ("stable", "Stable", "Establo", "Roofed building"),
        ("pen", "Pen", "Corral", "Small enclosure for handling"),
        ("pasture", "Pasture", "Pastizal", "Open grazing land"),
    ],
    "user_roles": [
        ("owner", "Owner", "Dueño", "Manages farms, lots, zones and accounts"),
        ("worker", "Worker", "Trabajador", "Monitors and handles alerts"),
    ],
    "animal_statuses": [
        ("active", "Active", "Activo", "On the farm"),
        ("inactive", "Inactive", "Inactivo", "Left the farm: sold, moved or dead"),
        ("lost", "Lost", "Perdido", "Should be on the farm and is not"),
    ],
    "video_upload_statuses": [
        ("pending_upload", "Pending upload", "Subida pendiente", "Registered; the file has not been confirmed"),
        ("queued", "Queued", "En cola", "Uploaded and waiting for a worker"),
        ("processing", "Processing", "Procesando", "A worker is analysing it"),
        ("completed", "Completed", "Completado", "Analysis delivered"),
        ("failed", "Failed", "Fallido", "Could not be uploaded or processed"),
    ],
}

def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for name, rows in ROWS.items():
        if inspector.has_table(name):
            continue
        table = op.create_table(
            name,
            sa.Column("code", sa.Text, primary_key=True),
            sa.Column("label", sa.Text, nullable=False),
            sa.Column("label_es", sa.Text, nullable=False),
            sa.Column("description", sa.Text, nullable=False),
            sa.Column("sort_order", sa.Integer, nullable=False),
        )
        op.bulk_insert(table, [
            {"code": code, "label": label, "label_es": label_es, "description": description, "sort_order": index}
            for index, (code, label, label_es, description) in enumerate(rows, start=1)
        ])


def downgrade() -> None:
    for name in reversed(ROWS):
        op.drop_table(name)

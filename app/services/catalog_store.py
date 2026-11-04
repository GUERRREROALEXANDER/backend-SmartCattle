"""Reference tables for the coded columns.

CATALOGS is the source: migration 0015 seeds the tables from a frozen copy of
it, the in-memory store serves it directly, and a test checks that each
catalog matches the enum the code validates against. A value added to an enum
without its catalog row fails that test instead of reaching a dashboard with
no label.
"""

from typing import Protocol

from sqlalchemy import select

from app.db import SessionFactory
from app.models import CATALOG_TABLES
from app.schemas.catalog import CatalogEntry

#: name -> rows of (code, label, label_es, description)
CATALOGS: dict[str, list[tuple[str, str, str, str]]] = {
    "event_types": [
        ("cattle_out_of_zone", "Cattle out of zone", "Ganado fuera de zona",
         "An animal was detected outside its camera's safe zone"),
        ("cattle_down", "Cattle down", "Animal caído",
         "An animal has been lying without moving for far longer than a cow rests"),
        ("possible_intrusion", "Possible intrusion", "Posible intrusión",
         "A person was seen during the camera's night hours"),
        ("external_animal_detected", "External animal", "Animal externo",
         "A dog or a predator was seen near the herd"),
    ],
    "severity_levels": [
        ("low", "Low", "Baja", "Doubtful detection, confidence below 0.6"),
        ("medium", "Medium", "Media", "Confidence from 0.6 to 0.9, or a dog or predator in view"),
        ("high", "High", "Alta", "Confidence of 0.9 or more, an animal down, or a person at night"),
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


def entries(rows: list[tuple[str, str, str, str]]) -> list[CatalogEntry]:
    return [
        CatalogEntry(code=code, label=label, label_es=label_es, description=description, sort_order=index)
        for index, (code, label, label_es, description) in enumerate(rows, start=1)
    ]


class CatalogStore(Protocol):
    def all(self) -> dict[str, list[CatalogEntry]]: ...


class InMemoryCatalogStore:
    def all(self) -> dict[str, list[CatalogEntry]]:
        return {name: entries(rows) for name, rows in CATALOGS.items()}


class SqlCatalogStore:
    """Reads the tables, so rows added by hand in the database are served too."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    def all(self) -> dict[str, list[CatalogEntry]]:
        result: dict[str, list[CatalogEntry]] = {}
        with self._sessions() as session:
            for name, table in CATALOG_TABLES.items():
                rows = session.execute(select(table).order_by(table.c.sort_order, table.c.code)).mappings()
                result[name] = [CatalogEntry(**row) for row in rows]
        return result

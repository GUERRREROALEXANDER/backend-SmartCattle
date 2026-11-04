import importlib
from enum import StrEnum

import pytest

from app.schemas.alert import AlertStatus
from app.schemas.animal import AnimalStatus
from app.schemas.auth import UserRole
from app.schemas.camera import CameraStatus
from app.schemas.event import EventLevel, EventType
from app.schemas.farm import LotKind
from app.schemas.video_upload import UploadStatus
from app.services.catalog_store import CATALOGS

#: Each catalog and the enum the API validates that column against.
ENUMS: dict[str, type[StrEnum]] = {
    "event_types": EventType,
    "severity_levels": EventLevel,
    "alert_statuses": AlertStatus,
    "camera_statuses": CameraStatus,
    "lot_kinds": LotKind,
    "user_roles": UserRole,
    "animal_statuses": AnimalStatus,
    "video_upload_statuses": UploadStatus,
}


def test_every_catalog_is_checked_against_an_enum():
    assert set(CATALOGS) == set(ENUMS)


@pytest.mark.parametrize("name", sorted(ENUMS))
def test_a_catalog_lists_exactly_the_values_of_its_enum(name):
    """A value added to the enum without a catalog row fails here, not on a dashboard with no label."""
    codes = [code for code, *_labels in CATALOGS[name]]
    assert len(codes) == len(set(codes))
    assert set(codes) == {member.value for member in ENUMS[name]}


def test_the_migration_seeds_the_current_catalogs():
    """The migration keeps a frozen copy; a change to either must be made to both."""
    migration = importlib.import_module("migrations.versions.0015_catalogs")
    later = importlib.import_module("migrations.versions.0020_anomalies_and_notifications")
    seeded = dict(migration.ROWS)
    seeded["event_types"] = [*seeded["event_types"], *(row[:4] for row in later.EVENT_TYPES)]
    seeded["severity_levels"] = [
        (code, label, label_es, later.SEVERITIES[code][1]) for code, label, label_es, _ in seeded["severity_levels"]
    ]
    assert seeded == CATALOGS


def test_the_catalogs_are_public(client):
    response = client.get("/api/catalogs")
    assert response.status_code == 200
    lot_kinds = response.json()["catalogs"]["lot_kinds"]
    assert lot_kinds[0] == {
        "code": "paddock", "label": "Paddock", "label_es": "Potrero",
        "description": "Fenced grazing area", "sort_order": 1,
    }
    assert [entry["sort_order"] for entry in lot_kinds] == [1, 2, 3, 4]

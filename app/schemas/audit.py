from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditAction(StrEnum):
    """Everything a person can do that the audit log records.

    Reads are not recorded: who looked at what is not a question anyone on a
    farm has asked, and it would grow the table with every page view.
    """

    REGISTERED = "auth.registered"
    SIGNED_IN = "auth.signed_in"
    SIGN_IN_FAILED = "auth.sign_in_failed"
    PASSWORD_CHANGED = "auth.password_changed"  # noqa: S105 - an action name, not a password
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    ALERT_ACKNOWLEDGED = "alert.acknowledged"
    ALERT_RESOLVED = "alert.resolved"
    FARM_CREATED = "farm.created"
    FARM_UPDATED = "farm.updated"
    FARM_DELETED = "farm.deleted"
    LOT_CREATED = "lot.created"
    LOT_UPDATED = "lot.updated"
    LOT_DELETED = "lot.deleted"
    CAMERA_SAVED = "camera.saved"
    CAMERA_DELETED = "camera.deleted"
    ZONE_CREATED = "zone.created"
    ZONE_UPDATED = "zone.updated"
    ZONE_DELETED = "zone.deleted"
    ANIMAL_CREATED = "animal.created"
    ANIMAL_UPDATED = "animal.updated"
    ANIMAL_DEACTIVATED = "animal.deactivated"
    VIDEO_UPLOADED = "video.uploaded"
    NOTIFICATIONS_UPDATED = "notifications.updated"


class AuditRecord(BaseModel):
    """What a route hands to the log; the log adds the id."""

    actor_id: int | None
    actor_email: str | None
    action: AuditAction
    target_type: str | None = None
    target_id: str | None = None
    detail: dict[str, Any] | None = None
    ip_address: str | None = None
    created_at: datetime


class AuditEntry(AuditRecord):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": 42, "actor_id": 1, "actor_email": "ana@finca.example", "action": "alert.resolved",
        "target_type": "alert", "target_id": "6f1c2a7e-6d3b-4c55-9a0e-1b2c3d4e5f60",
        "detail": None, "ip_address": "190.25.1.10", "created_at": "2026-10-31T15:42:00Z",
    }]})

    id: int


class AuditPage(BaseModel):
    items: list[AuditEntry]
    total: int

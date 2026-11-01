from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.time_window import as_utc
from app.dependencies import OwnerUser, get_audit_log
from app.schemas.audit import AuditAction, AuditPage
from app.services.audit_log import AuditLog, AuditQuery

router = APIRouter(prefix="/api/audit-log", tags=["audit"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@router.get("", response_model=AuditPage)
def list_audit_log(
    _owner: OwnerUser,
    log: Annotated[AuditLog, Depends(get_audit_log)],
    actor_id: Annotated[int | None, Query(ge=1)] = None,
    action: AuditAction | None = None,
    target_type: Annotated[str | None, Query(max_length=40)] = None,
    target_id: Annotated[str | None, Query(max_length=100)] = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPage:
    """Who did what, newest first. Owner only: it names the staff and their addresses.

    start is inclusive and end exclusive. A time without a zone is read as UTC.
    """
    return log.page(AuditQuery(
        actor_id=actor_id, action=action, target_type=target_type, target_id=target_id,
        start=as_utc(start), end=as_utc(end), limit=limit, offset=offset,
    ))

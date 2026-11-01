from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import AuditTrail, Clock, CurrentUser, get_alert_store, get_broadcaster, get_clock
from app.schemas.alert import Alert, AlertList, AlertStatus
from app.schemas.audit import AuditAction
from app.schemas.event import EventLevel
from app.services.alert_store import AlertQuery, AlertStore, AlertTransitionError
from app.services.broadcaster import Broadcaster

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

Store = Annotated[AlertStore, Depends(get_alert_store)]
Hub = Annotated[Broadcaster, Depends(get_broadcaster)]
Now = Annotated[Clock, Depends(get_clock)]

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@router.get("", response_model=AlertList)
def list_alerts(
    _user: CurrentUser,
    store: Store,
    alert_status: Annotated[AlertStatus | None, Query(alias="status")] = None,
    severity: EventLevel | None = None,
    camera_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AlertList:
    """Alerts, newest first. Workers included: handling alerts is the everyday job."""
    page = store.page(AlertQuery(
        status=alert_status, severity=severity, camera_id=camera_id, limit=limit, offset=offset,
    ))
    return AlertList(items=page.items, total=page.total)


@router.get("/{alert_id}", response_model=Alert)
def get_alert(alert_id: UUID, _user: CurrentUser, store: Store) -> Alert:
    alert = store.get(alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No alert with id {alert_id}")
    return alert


def _move(
    alert_id: UUID, target: AlertStatus, user_id: int, store: AlertStore, hub: Broadcaster, clock: Clock,
) -> Alert:
    now = clock()
    try:
        alert = store.transition(alert_id, target, user_id, now)
    except AlertTransitionError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"The alert is already {error.current}",
        ) from error
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No alert with id {alert_id}")
    # Published so every other open dashboard stops showing it as unhandled.
    hub.publish("alert.updated", alert, now)
    return alert


@router.post("/{alert_id}/acknowledge", response_model=Alert)
def acknowledge_alert(
    alert_id: UUID, user: CurrentUser, store: Store, hub: Hub, clock: Now, audit: AuditTrail,
) -> Alert:
    """Marks the alert as seen by someone who is dealing with it."""
    alert = _move(alert_id, AlertStatus.ACKNOWLEDGED, user.id, store, hub, clock)
    audit.record(
        AuditAction.ALERT_ACKNOWLEDGED, actor_id=user.id, actor_email=user.email,
        target_type="alert", target_id=alert_id,
    )
    return alert


@router.post("/{alert_id}/resolve", response_model=Alert)
def resolve_alert(
    alert_id: UUID, user: CurrentUser, store: Store, hub: Hub, clock: Now, audit: AuditTrail,
) -> Alert:
    """Closes the alert, from open or acknowledged."""
    alert = _move(alert_id, AlertStatus.RESOLVED, user.id, store, hub, clock)
    audit.record(
        AuditAction.ALERT_RESOLVED, actor_id=user.id, actor_email=user.email,
        target_type="alert", target_id=alert_id,
    )
    return alert

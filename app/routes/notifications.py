"""Alerts outside the dashboard: where each account wants them, and whether they arrived."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import Settings
from app.dependencies import (
    AuditTrail,
    Clock,
    CurrentUser,
    OwnerUser,
    get_app_settings,
    get_clock,
    get_notification_store,
    get_notifier,
)
from app.schemas.audit import AuditAction
from app.schemas.notification import (
    DeliveryList,
    NotificationSettings,
    NotificationSettingsUpdate,
    NotificationSettingsView,
    TestResults,
)
from app.services.notification_store import NO_SEPARATION_ACCOUNT, NotificationStore
from app.services.notifier import AlertNotifier
from app.services.user_store import StoredUser

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

Store = Annotated[NotificationStore, Depends(get_notification_store)]
Notifier = Annotated[AlertNotifier, Depends(get_notifier)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]


def _account(user: StoredUser, settings: Settings) -> int:
    """The same key events.py delivers to. Without separation there is one farm, whatever the user's own space."""
    if not settings.account_isolation or user.account_id is None:
        return NO_SEPARATION_ACCOUNT
    return user.account_id


@router.get("/settings", response_model=NotificationSettingsView)
def read_settings(
    owner: OwnerUser, store: Store, notifier: Notifier, settings: AppSettings,
) -> NotificationSettingsView:
    """Owner only: it holds a person's phone number."""
    wanted = store.settings_of(_account(owner, settings))
    return NotificationSettingsView(**wanted.model_dump(), channels_available=notifier.available)


@router.put("/settings", response_model=NotificationSettingsView)
def write_settings(
    data: NotificationSettingsUpdate,
    owner: OwnerUser,
    store: Store,
    notifier: Notifier,
    settings: AppSettings,
    audit: AuditTrail,
    clock: Annotated[Clock, Depends(get_clock)],
) -> NotificationSettingsView:
    wanted = NotificationSettings(**data.model_dump())
    store.save_settings(_account(owner, settings), wanted, clock())
    # Not the number itself: the log is kept for far longer than anyone needs to know it.
    audit.record(AuditAction.NOTIFICATIONS_UPDATED, actor_id=owner.id, actor_email=owner.email,
                 target_type="notification_settings", target_id=str(_account(owner, settings)))
    return NotificationSettingsView(**wanted.model_dump(), channels_available=notifier.available)


@router.post("/test", response_model=TestResults)
def send_test(owner: OwnerUser, store: Store, notifier: Notifier, settings: AppSettings) -> TestResults:
    """Sends one message per channel with a recipient, to find a wrong number now rather than during an alarm."""
    results = notifier.test(store.settings_of(_account(owner, settings)))
    if not results:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="No channel has both server credentials and a recipient")
    return TestResults(results=results)


@router.get("/deliveries", response_model=DeliveryList)
def list_deliveries(
    user: CurrentUser,
    store: Store,
    settings: AppSettings,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DeliveryList:
    """Whether the messages arrived. Everyone on the farm may see it: it answers "was the owner told?"."""
    items, total = store.deliveries(_account(user, settings), limit)
    return DeliveryList(items=items, total=total)

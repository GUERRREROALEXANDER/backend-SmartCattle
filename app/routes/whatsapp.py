from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import (
    AuditTrail,
    Clock,
    CurrentUser,
    get_clock,
    get_phone_verifications,
    get_whatsapp_store,
    verify_configured_api_key,
)
from app.schemas.audit import AuditAction
from app.schemas.whatsapp import (
    WhatsappContact,
    WhatsappDeliveryFailure,
    WhatsappMessageBatch,
    WhatsappPreferences,
    WhatsappVerificationCode,
    WhatsappVerificationStart,
    WhatsappVerificationStarted,
)
from app.services.user_store import StoredUser
from app.services.whatsapp_notices import verification_text
from app.services.whatsapp_store import WhatsappStore
from app.services.whatsapp_verification import LIFETIME, PhoneVerifications, TooManyCodesError

router = APIRouter(prefix="/api/auth/me/whatsapp", tags=["whatsapp"])
service_router = APIRouter(
    prefix="/api/ai/whatsapp", tags=["whatsapp"], dependencies=[Depends(verify_configured_api_key)],
)

Store = Annotated[WhatsappStore, Depends(get_whatsapp_store)]
Now = Annotated[Clock, Depends(get_clock)]
Verifications = Annotated[PhoneVerifications, Depends(get_phone_verifications)]


def _audit(audit: AuditTrail, user: StoredUser, state: str) -> None:
    # The number itself stays out of the log: who changed theirs is the question, not what it is.
    audit.record(
        AuditAction.WHATSAPP_CHANGED, actor_id=user.id, actor_email=user.email,
        target_type="user", target_id=str(user.id), detail={"state": state},
    )


@router.get("", response_model=WhatsappContact)
def get_whatsapp(user: CurrentUser, store: Store) -> WhatsappContact:
    contact = store.contact(user.id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No WhatsApp number on this account")
    return contact


@router.post("/verification", response_model=WhatsappVerificationStarted, status_code=status.HTTP_202_ACCEPTED)
def start_whatsapp_verification(
    data: WhatsappVerificationStart, user: CurrentUser, store: Store, verifications: Verifications, clock: Now,
) -> WhatsappVerificationStarted:
    """Sends a code to the number. 202: it is queued, and only arrives while the WhatsApp service is running."""
    now = clock()
    try:
        code = verifications.start(user.id, data.phone, now)
    except TooManyCodesError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many codes requested; try again later",
            headers={"Retry-After": str(error.retry_after)},
        ) from error
    store.queue(data.phone, verification_text(code), now, now + LIFETIME)
    return WhatsappVerificationStarted(expires_in=int(LIFETIME.total_seconds()))


@router.post("/verify", response_model=WhatsappContact)
def verify_whatsapp(
    data: WhatsappVerificationCode, user: CurrentUser, store: Store, verifications: Verifications,
    audit: AuditTrail, clock: Now,
) -> WhatsappContact:
    now = clock()
    phone = verifications.confirm(user.id, data.code, now)
    if phone is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This code is wrong or has expired")
    contact = store.link(user.id, phone, now)
    _audit(audit, user, "linked")
    return contact


@router.patch("", response_model=WhatsappContact)
def set_whatsapp_preferences(
    data: WhatsappPreferences, user: CurrentUser, store: Store, audit: AuditTrail,
) -> WhatsappContact:
    contact = store.set_enabled(user.id, data.enabled)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No WhatsApp number on this account")
    _audit(audit, user, "enabled" if data.enabled else "disabled")
    return contact


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def remove_whatsapp(user: CurrentUser, store: Store, audit: AuditTrail) -> None:
    if not store.unlink(user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No WhatsApp number on this account")
    _audit(audit, user, "removed")


@service_router.post("/claim", response_model=WhatsappMessageBatch)
def claim_whatsapp_messages(
    store: Store, clock: Now, limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> WhatsappMessageBatch:
    """The next messages to send. A message with no answer within a minute is offered again, at most three times."""
    return WhatsappMessageBatch(items=store.claim(clock(), limit))


@service_router.post("/{message_id}/sent", status_code=status.HTTP_204_NO_CONTENT)
def report_whatsapp_sent(message_id: int, store: Store, clock: Now) -> None:
    if not store.finish(message_id, None, clock()):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No message with id {message_id}")


@service_router.post("/{message_id}/failed", status_code=status.HTTP_204_NO_CONTENT)
def report_whatsapp_failed(message_id: int, data: WhatsappDeliveryFailure, store: Store, clock: Now) -> None:
    """Gives up on a message that retrying cannot fix, such as a number without WhatsApp."""
    if not store.finish(message_id, data.error, clock()):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No message with id {message_id}")

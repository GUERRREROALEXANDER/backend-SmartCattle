"""What reaches a person's WhatsApp. The wording is in Spanish because that is what the people reading it speak."""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.schemas.alert import Alert
from app.schemas.event import EventLevel, EventType
from app.services.camera_store import CameraStore
from app.services.user_store import UserStore
from app.services.whatsapp_store import WhatsappStore

logger = logging.getLogger(__name__)

#: One animal outside can be detected many times a minute. Repeating each one would teach
#: people to mute the chat, and looks like spam to WhatsApp, which can block the number.
QUIET_PERIOD = timedelta(minutes=10)
#: After this, "cattle left the zone" no longer says anything someone can act on. It stays on the dashboard.
NOTICE_LIFETIME = timedelta(hours=1)
FARM_TIME = ZoneInfo("America/Bogota")


def verification_text(code: str) -> str:
    return (
        f"SmartCattle: tu código de verificación es {code}. "
        "Vence en 10 minutos. Si no lo pediste, ignora este mensaje."
    )


def _alert_text(camera_name: str, at: datetime) -> str:
    moment = at.astimezone(FARM_TIME).strftime("%d/%m/%Y %I:%M %p")
    return (
        "🚨 SmartCattle: ganado fuera de la zona\n"
        f"Cámara: {camera_name}\n"
        f"Hora: {moment}\n"
        "Revisa el panel para ver el video."
    )


def _moment_to_show(detected_at: datetime, now: datetime) -> datetime:
    """When it happened. A detection can arrive late after an outage, but a farm PC's clock can also just be wrong,
    and a time far from now says more about that clock than about the animal."""
    return detected_at if abs(now - detected_at) <= NOTICE_LIFETIME else now


def queue_alert_notice(
    alert: Alert, *, detected_at: datetime, whatsapp: WhatsappStore, users: UserStore, cameras: CameraStore,
    now: datetime,
) -> None:
    """Queues the notice for an alert. The alert already exists, so a failure here must not undo or repeat it."""
    try:
        # A low-confidence detection is often not an animal at all; it stays on the dashboard.
        if alert.alert_type is not EventType.CATTLE_OUT_OF_ZONE or alert.severity is EventLevel.LOW:
            return
        account = None if alert.camera_id is None else cameras.owner_of(alert.camera_id)
        if alert.camera_id is None or account is None:
            return
        contacts = whatsapp.enabled_contacts(users.ids_in_account(account))
        # Looked up after the contacts: most accounts have none, and this runs for every detection.
        active = [contact for contact in contacts if (user := users.by_id(contact.user_id)) and user.is_active]
        if not active:
            return
        saved = cameras.saved_one(alert.camera_id)
        whatsapp.queue_camera_notice(
            alert.camera_id, sorted({contact.phone for contact in active}),
            _alert_text(alert.camera_id if saved is None else saved.name, _moment_to_show(detected_at, now)),
            now, now + NOTICE_LIFETIME, QUIET_PERIOD,
        )
    except Exception:
        logger.exception("Could not queue the WhatsApp notice for an alert")

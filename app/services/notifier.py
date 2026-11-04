"""Tells someone outside the dashboard: an alert that nobody reads at 3 am has not done its job.

Runs after the AI service has its answer, so a channel that is slow or down costs it nothing. A channel error never
carries the token or the recipient: Telegram's token is part of its URL, and these details are stored and shown.
"""

import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from threading import Lock
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from app.core.config import Settings
from app.schemas.alert import Alert
from app.schemas.event import EventLevel, EventType
from app.schemas.notification import Channel, Delivery, DeliveryStatus, NotificationSettings, TestResult
from app.services.notification_store import NotificationStore

logger = logging.getLogger(__name__)

#: Seconds before each try: a brief network failure at a farm should not lose a message, and a long one is not
#: something to wait out on a request thread.
RETRY_DELAYS = (0.0, 5.0, 30.0)
REQUEST_TIMEOUT_S = 10.0
RANK = {EventLevel.LOW: 0, EventLevel.MEDIUM: 1, EventLevel.HIGH: 2}

# What the person reads, so it is in their language; the catalogs' label_es are the same kind of exception to the
# rule of English in the code. The dashboard has its own labels for the same types, in the frontend.
TEXT_ES: dict[EventType, str] = {
    EventType.CATTLE_OUT_OF_ZONE: "Un animal salió de la zona segura",
    EventType.CATTLE_DOWN: "Un animal lleva mucho tiempo caído sin moverse",
    EventType.POSSIBLE_INTRUSION: "Se vio una persona de noche cerca del ganado",
    EventType.EXTERNAL_ANIMAL_DETECTED: "Se vio un perro o un depredador cerca del ganado",
}
TEST_TEXT_ES = "Prueba de avisos: SmartCattle ya puede escribirte por este canal"


class ChannelError(Exception):
    """Safe to store and show. `final` means trying again cannot help, as with a refused token or a wrong number."""

    def __init__(self, detail: str, *, final: bool = False) -> None:
        super().__init__(detail)
        self.detail = detail
        self.final = final


def _post_json(url: str, body: dict[str, object], headers: dict[str, str] | None = None) -> None:
    request = Request(  # noqa: S310 - the URLs are this module's own, or a setting
        url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_S):  # noqa: S310
            return
    except HTTPError as error:
        error.close()  # It holds the connection open until closed.
        # 429 and server errors pass; any other answer will be the same next time.
        raise ChannelError(f"The channel answered HTTP {error.code}",
                           final=error.code < 500 and error.code != 429) from None
    except (URLError, OSError):
        raise ChannelError("Could not reach the channel") from None


class NotificationChannel(Protocol):
    name: Channel

    def send(self, recipient: str, text: str, camera: str, moment: str) -> None: ...


class WhatsAppChannel:
    """Meta's Cloud API. Always a template: anything else is refused outside a 24-hour conversation."""

    name = Channel.WHATSAPP

    def __init__(self, base_url: str, phone_number_id: str, token: str, template: str, language: str) -> None:
        self._url = f"{base_url.rstrip('/')}/{phone_number_id}/messages"
        self._token = token
        self._template = template
        self._language = language

    def send(self, recipient: str, text: str, camera: str, moment: str) -> None:
        _post_json(self._url, {
            "messaging_product": "whatsapp", "to": recipient.removeprefix("+"), "type": "template",
            "template": {
                "name": self._template, "language": {"code": self._language},
                "components": [{"type": "body", "parameters": [
                    {"type": "text", "text": value} for value in (text, camera, moment)
                ]}],
            },
        }, {"Authorization": f"Bearer {self._token}"})


class TelegramChannel:
    """Free and needs no approval, so a farm can have it working in minutes."""

    name = Channel.TELEGRAM

    def __init__(self, token: str, base_url: str = "https://api.telegram.org") -> None:
        self._url = f"{base_url.rstrip('/')}/bot{token}/sendMessage"

    def send(self, recipient: str, text: str, camera: str, moment: str) -> None:
        _post_json(self._url, {"chat_id": recipient, "text": f"SmartCattle: {text}\nCámara: {camera}\nHora: {moment}"})


def channels_from(settings: Settings) -> list[NotificationChannel]:
    channels: list[NotificationChannel] = []
    if settings.whatsapp_access_token is not None and settings.whatsapp_phone_number_id is not None:
        channels.append(WhatsAppChannel(
            str(settings.whatsapp_api_url), settings.whatsapp_phone_number_id,
            settings.whatsapp_access_token.get_secret_value(),
            settings.whatsapp_template_name, settings.whatsapp_template_language,
        ))
    if settings.telegram_bot_token is not None:
        channels.append(TelegramChannel(settings.telegram_bot_token.get_secret_value()))
    return channels


def _recipient(channel: Channel, wanted: NotificationSettings) -> str | None:
    return wanted.whatsapp_phone if channel is Channel.WHATSAPP else wanted.telegram_chat_id


class AlertNotifier:
    def __init__(
        self, store: NotificationStore, channels: list[NotificationChannel], clock: Callable[[], datetime],
        cooldown: timedelta = timedelta(minutes=15), sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._store = store
        self._channels = channels
        self._clock = clock
        self._cooldown = cooldown
        self._sleep = sleep
        # One herd walking out arrives as several requests at once: only one of them may claim the message.
        self._claiming = Lock()

    @property
    def available(self) -> list[Channel]:
        return [channel.name for channel in self._channels]

    def deliver(self, alert: Alert, account_id: int) -> None:
        """Never raises: the event is stored and on the dashboard, whatever happens to the message."""
        try:
            for channel, delivery, recipient in self._claim(alert, account_id):
                self._send(channel, delivery, recipient, alert)
        except Exception:
            logger.exception("Could not deliver alert %s", alert.id)

    def _claim(self, alert: Alert, account_id: int) -> list[tuple[NotificationChannel, Delivery, str]]:
        wanted = self._store.settings_of(account_id)
        if not wanted.enabled or RANK[alert.severity] < RANK[wanted.min_severity]:
            return []
        now = self._clock()
        with self._claiming:
            if self._store.was_told_since(account_id, alert.camera_id, alert.alert_type.value, now - self._cooldown):
                return []
            claimed = []
            for channel in self._channels:
                recipient = _recipient(channel.name, wanted)
                if recipient is None:
                    continue
                delivery = Delivery(
                    id=uuid4(), alert_id=alert.id, camera_id=alert.camera_id, alert_type=alert.alert_type.value,
                    channel=channel.name, status=DeliveryStatus.PENDING, attempts=0, detail=None, created_at=now,
                )
                self._store.add_delivery(account_id, delivery)
                claimed.append((channel, delivery, recipient))
            return claimed

    def _send(self, channel: NotificationChannel, delivery: Delivery, recipient: str, alert: Alert) -> None:
        moment = alert.created_at.strftime("%Y-%m-%d %H:%M UTC")
        status, attempts, detail, sent_at = DeliveryStatus.FAILED, 0, None, None
        for delay in RETRY_DELAYS:
            if delay:
                self._sleep(delay)
            attempts += 1
            try:
                channel.send(recipient, TEXT_ES[alert.alert_type], alert.camera_id or "sin cámara", moment)
            except ChannelError as error:
                detail = error.detail
                if error.final:
                    break
            else:
                status, detail, sent_at = DeliveryStatus.SENT, None, self._clock()
                break
        self._store.finish_delivery(delivery.id, status, attempts, detail, sent_at)

    def test(self, wanted: NotificationSettings) -> list[TestResult]:
        """One message per configured channel, right now, so a wrong number shows up before a night it matters."""
        results = []
        moment = self._clock().strftime("%Y-%m-%d %H:%M UTC")
        for channel in self._channels:
            recipient = _recipient(channel.name, wanted)
            if recipient is None:
                continue
            try:
                channel.send(recipient, TEST_TEXT_ES, "prueba", moment)
            except ChannelError as error:
                results.append(TestResult(channel=channel.name, status=DeliveryStatus.FAILED, detail=error.detail))
            else:
                results.append(TestResult(channel=channel.name, status=DeliveryStatus.SENT))
        return results

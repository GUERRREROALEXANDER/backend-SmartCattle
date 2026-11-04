from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.event import EventLevel


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


#: E.164, the only form the WhatsApp Cloud API accepts without guessing a country.
WhatsAppPhone = Annotated[str, StringConstraints(pattern=r"^\+[1-9]\d{7,14}$")]
TelegramChatId = Annotated[str, StringConstraints(pattern=r"^-?\d{1,20}$")]


class NotificationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    whatsapp_phone: WhatsAppPhone | None = None
    telegram_chat_id: TelegramChatId | None = None
    min_severity: EventLevel = Field(default=EventLevel.MEDIUM, description="Alerts below this stay on the dashboard")


class NotificationSettings(NotificationSettingsUpdate):
    """What one account asked for. Which channels work is the server's, in `channels_available`."""


class NotificationSettingsView(NotificationSettings):
    channels_available: list[Channel] = Field(description="Channels this server has credentials for")


class Delivery(BaseModel):
    """One message to one channel. Holds no recipient: the history is readable by workers, the phone number is not."""

    id: UUID
    alert_id: UUID | None
    camera_id: str | None
    alert_type: str
    channel: Channel
    status: DeliveryStatus
    attempts: int
    detail: str | None
    created_at: datetime
    sent_at: datetime | None = None


class DeliveryList(BaseModel):
    items: list[Delivery]
    total: int


class TestResult(BaseModel):
    channel: Channel
    status: DeliveryStatus
    detail: str | None = None


class TestResults(BaseModel):
    results: list[TestResult]

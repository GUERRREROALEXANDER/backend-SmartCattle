import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: E.164: what WhatsApp addresses. The country code cannot be guessed, and a wrong guess reaches a stranger.
PHONE = re.compile(r"\+[1-9]\d{7,14}")


class WhatsappVerificationStart(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"phone": "+573001234567"}]})

    phone: str = Field(max_length=32, description="With the country code; spaces and hyphens are ignored")

    @field_validator("phone")
    @classmethod
    def international_format(cls, value: str) -> str:
        cleaned = re.sub(r"[\s\-()]", "", value)
        if PHONE.fullmatch(cleaned) is None:
            raise ValueError("phone must include the country code, like +573001234567")
        return cleaned


class WhatsappVerificationStarted(BaseModel):
    expires_in: int = Field(description="Seconds the code stays valid")


class WhatsappVerificationCode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^\d{6}$")


class WhatsappPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class WhatsappContact(BaseModel):
    """A number its owner proved they hold. Only these ever receive an alert."""

    user_id: int
    phone: str
    enabled: bool
    verified_at: datetime


class WhatsappMessage(BaseModel):
    """One message for the WhatsApp service to send."""

    id: int
    phone: str
    body: str


class WhatsappMessageBatch(BaseModel):
    items: list[WhatsappMessage]


class WhatsappDeliveryFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: str = Field(min_length=1, max_length=200, description="Short reason, without URLs")

    @field_validator("error")
    @classmethod
    def no_urls(cls, value: str) -> str:
        if "://" in value:
            raise ValueError("error must not contain URLs")
        return value

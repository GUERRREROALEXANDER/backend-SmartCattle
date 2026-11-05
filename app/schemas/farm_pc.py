from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


class FarmPcPairing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)


class FarmPcKey(BaseModel):
    key: str = Field(description="What this PC sends as X-API-Key. It only works for the cameras of its account")
    origins: list[str] = Field(description="Pages the PC's service must let in: the dashboard's address")


class FarmPcPresence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: AnyHttpUrl = Field(description="Where browsers reach this PC's service right now")
    media_url: AnyHttpUrl | None = Field(
        default=None, description="Where browsers reach this PC's MediaMTX HLS server; omit when it is not running",
    )

    @field_validator("media_url")
    @classmethod
    def safe_media_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        # This address is handed to every browser that opens the video.
        if value is not None and (value.username or value.password):
            raise ValueError("media_url must not contain credentials")
        if value is not None and len(str(value)) > 300:
            raise ValueError("media_url must be at most 300 characters")
        return value


class FarmPcState(BaseModel):
    url: str | None = Field(description="Address of the account's PC; null while it is off or not linked")

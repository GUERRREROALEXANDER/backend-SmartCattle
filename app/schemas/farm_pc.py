from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class FarmPcPairing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)


class FarmPcKey(BaseModel):
    key: str = Field(description="What this PC sends as X-API-Key. It only works for the cameras of its account")
    origins: list[str] = Field(description="Pages the PC's service must let in: the dashboard's address")


class FarmPcPresence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: AnyHttpUrl = Field(description="Where browsers reach this PC's service right now")


class FarmPcState(BaseModel):
    url: str | None = Field(description="Address of the account's PC; null while it is off or not linked")

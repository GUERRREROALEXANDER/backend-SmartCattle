from functools import lru_cache

from pydantic import AnyHttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    allowed_origins: str = (
        "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5500"
    )
    smartcattle_ai_url: AnyHttpUrl | None = None
    ai_api_key: SecretStr | None = None

    @field_validator("allowed_origins")
    @classmethod
    def reject_wildcard(cls, value: str) -> str:
        if "*" in [origin.strip() for origin in value.split(",")]:
            raise ValueError("Wildcard CORS origins are not allowed")
        return value

    @field_validator("smartcattle_ai_url", "ai_api_key", mode="before")
    @classmethod
    def empty_to_none(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

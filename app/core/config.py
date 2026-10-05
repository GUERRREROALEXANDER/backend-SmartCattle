from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    allowed_origins: str = (
        "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5500"
    )
    smartcattle_ai_url: AnyHttpUrl | None = None
    # Service-to-service key for writing events; the AI service holds it.
    ai_api_key: SecretStr | None = None
    # Separate key for reading events and animals. Never ship it to a browser:
    # anything the browser holds is readable by whoever opens the devtools.
    read_api_key: SecretStr | None = None

    event_storage: Literal["memory", "mysql"] = "mysql"
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: SecretStr | None = None
    mysql_database: str = "smartcattle"
    mysql_pool_size: int = 5

    @field_validator("allowed_origins")
    @classmethod
    def reject_wildcard(cls, value: str) -> str:
        if "*" in [origin.strip() for origin in value.split(",")]:
            raise ValueError("Wildcard CORS origins are not allowed")
        return value

    @field_validator(
        "smartcattle_ai_url", "ai_api_key", "read_api_key", "mysql_password", mode="before"
    )
    @classmethod
    def empty_to_none(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def mysql_connection_config(self) -> dict[str, object]:
        """Keyword arguments for mysql.connector, with the password unwrapped."""
        return {
            "host": self.mysql_host,
            "port": self.mysql_port,
            "user": self.mysql_user,
            "password": self.mysql_password.get_secret_value() if self.mysql_password else "",
            "database": self.mysql_database,
            "charset": "utf8mb4",
            "collation": "utf8mb4_unicode_ci",
            # The store commits explicitly so a failed insert leaves no partial row.
            "autocommit": False,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()

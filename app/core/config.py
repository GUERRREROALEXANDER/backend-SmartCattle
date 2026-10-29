from functools import lru_cache

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: RFC 7518 section 3.2: an HMAC key should be at least the digest size,
#: which is 32 bytes for SHA-256. PyJWT warns below this; we refuse.
MIN_JWT_SECRET_BYTES = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    allowed_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173,http://localhost:3000,http://127.0.0.1:5500"
    )
    smartcattle_ai_url: AnyHttpUrl | None = None
    ai_api_key: SecretStr | None = None
    database_url: SecretStr | None = None
    camera_offline_after_seconds: int = Field(default=60, ge=10, le=3600)
    # Signs the access tokens. Unset means authentication is off: /api/auth/*
    # answers 503 and nothing is protected. That is the state of a deployment
    # that has not configured it yet, and create_app warns about it.
    jwt_secret: SecretStr | None = None
    # One hour. Long enough that the farm staff are not signing in repeatedly,
    # short enough that a stolen token stops working within the shift. There is
    # no refresh token and no revocation list, so this is the only thing that
    # ends a session server-side.
    access_token_minutes: int = Field(default=60, ge=5, le=1440)
    # Failed logins per address before it is refused for a while. Counted in
    # the process rather than the database: a counter that survives a restart
    # needs a table, and what this blocks is a fast loop, not a patient one.
    max_login_attempts: int = Field(default=5, ge=3, le=50)
    login_lockout_seconds: int = Field(default=300, ge=30, le=3600)

    @field_validator("allowed_origins")
    @classmethod
    def reject_wildcard(cls, value: str) -> str:
        origins = [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
        if "*" in origins:
            raise ValueError("Wildcard CORS origins are not allowed")
        if any(not origin.startswith(("http://", "https://")) for origin in origins):
            raise ValueError("CORS origins must start with http:// or https://")
        return value

    @field_validator("jwt_secret")
    @classmethod
    def secret_is_long_enough(cls, value: SecretStr | None) -> SecretStr | None:
        """HS256 keys shorter than the hash are weak; PyJWT warns, we refuse.

        RFC 7518 section 3.2 puts the floor at the digest size, 32 bytes for
        SHA-256. A shorter key still signs and verifies, so nothing fails: the
        tokens are just cheaper to forge, which is the kind of problem that
        does not announce itself. Refusing at startup is the only point where
        anyone is looking.
        """
        if value is None:
            return None
        if len(value.get_secret_value().encode("utf-8")) < MIN_JWT_SECRET_BYTES:
            raise ValueError(
                f"JWT_SECRET must be at least {MIN_JWT_SECRET_BYTES} bytes; generate one with "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return value

    @field_validator("smartcattle_ai_url", "ai_api_key", "database_url", "jwt_secret", mode="before")
    @classmethod
    def empty_to_none(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def sqlalchemy_url(self) -> str | None:
        """DATABASE_URL with the psycopg 3 driver; Render provides postgresql:// URLs."""
        if self.database_url is None:
            return None
        url = self.database_url.get_secret_value()
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url

    @property
    def auth_enabled(self) -> bool:
        """Whether JWT_SECRET is configured. False means /api/auth/* answers 503."""
        return self.jwt_secret is not None

    @property
    def storage_name(self) -> str:
        url = self.sqlalchemy_url
        if url is None:
            return "memory"
        return "postgresql" if url.startswith("postgresql") else url.split(":", 1)[0].split("+", 1)[0]


@lru_cache
def get_settings() -> Settings:
    return Settings()

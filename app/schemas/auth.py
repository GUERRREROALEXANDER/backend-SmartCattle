from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import MAX_PASSWORD_BYTES

#: Shortest password accepted. Low enough not to fight the farm staff, and the
#: real defence is bcrypt's cost plus the login rate limit, not length rules
#: that push people towards writing the password down.
MIN_PASSWORD_LENGTH = 8


class UserRole(StrEnum):
    OWNER = "owner"
    WORKER = "worker"


def _check_password_bytes(value: str) -> str:
    """bcrypt ignores everything past 72 bytes, so a longer password is rejected here.

    Measured in bytes and not characters: the farm staff write in Spanish, and
    one accented character is two bytes in UTF-8. A 70-character password with
    accents can exceed the limit.
    """
    encoded = value.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"password must be at most {MAX_PASSWORD_BYTES} bytes (it is {len(encoded)}); "
            "accented characters count as two"
        )
    return value


class UserCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "email": "ana@finca.example",
            "password": "una-clave-larga",
            "full_name": "Ana Restrepo",
        }]},
    )

    email: EmailStr = Field(description="Lower-cased before it is stored")
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)
    full_name: str = Field(min_length=1, max_length=120)
    # Not settable at registration. The first account could claim owner and
    # every later one would too, so role is assigned by the backend.

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, value: str) -> str:
        # users.email carries CHECK (email = lower(email)); normalising here
        # means the database never has to reject the row.
        return value.strip().lower()

    @field_validator("full_name", mode="before")
    @classmethod
    def strip_full_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        return _check_password_bytes(value)


class LoginRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [
            {"email": "ana@finca.example", "password": "una-clave-larga"}
        ]},
    )

    email: EmailStr
    # No min_length: a short password is a failed login, not a validation
    # error. A 422 here would tell the caller the password was too short to be
    # the real one, which is information about the account.
    password: str = Field(max_length=200)

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, value: str) -> str:
        return value.strip().lower()


class User(BaseModel):
    """A user as the API returns it. Never carries the hash."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": 1, "email": "ana@finca.example", "full_name": "Ana Restrepo",
        "role": "owner", "is_active": True,
        "created_at": "2026-10-29T12:00:00Z", "last_login_at": "2026-10-29T12:30:00Z",
    }]})

    id: int
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class TokenResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "access_token": "eyJhbGciOiJIUzI1NiIs...",
        "token_type": "bearer",
        "expires_in": 3600,
        "user": {
            "id": 1, "email": "ana@finca.example", "full_name": "Ana Restrepo",
            "role": "owner", "is_active": True,
            "created_at": "2026-10-29T12:00:00Z", "last_login_at": "2026-10-29T12:30:00Z",
        },
    }]})

    access_token: str
    # The suppression below: "bearer" is the OAuth 2 token type from
    # RFC 6750, which bandit reads as a hardcoded password.
    token_type: str = "bearer"  # noqa: S105
    expires_in: int = Field(description="Token lifetime in seconds")
    # Returned with the token so the frontend does not need a second request
    # just to know who it signed in.
    user: User

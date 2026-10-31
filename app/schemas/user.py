from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.auth import MIN_PASSWORD_LENGTH, User, UserCreate, UserRole, check_password_bytes


class UserAccountCreate(UserCreate):
    """An account created by an owner, who unlike a self-registering user may choose the role."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "email": "beto@finca.example", "password": "clave-inicial-1",
            "full_name": "Beto Salazar", "role": "worker",
        }]},
    )

    role: UserRole = UserRole.WORKER


class UserUpdate(BaseModel):
    """A partial update. None of these fields can be cleared, only changed."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: UserRole | None = None
    is_active: bool | None = None

    @field_validator("full_name", mode="before")
    @classmethod
    def strip_full_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def changes_something(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("the update must change at least one field")
        cleared = [field for field in self.model_fields_set if getattr(self, field) is None]
        if cleared:
            raise ValueError(f"{', '.join(sorted(cleared))} cannot be cleared")
        return self


class UserList(BaseModel):
    items: list[User]
    total: int = Field(description="Accounts matching the filters, not the page length")


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # No min_length on the current password, for the same reason as at login:
    # a 422 would reveal it was too short to be the real one.
    current_password: str = Field(max_length=200)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)

    @field_validator("new_password")
    @classmethod
    def new_password_fits_bcrypt(cls, value: str) -> str:
        return check_password_bytes(value)

    @model_validator(mode="after")
    def password_actually_changes(self) -> Self:
        if self.new_password == self.current_password:
            raise ValueError("the new password must differ from the current one")
        return self

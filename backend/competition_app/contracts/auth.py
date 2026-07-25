from __future__ import annotations

from datetime import date, datetime

from pydantic import EmailStr, Field, field_validator

from competition_app.contracts.base import ContractModel


class RegisterRequest(ContractModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=64)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        value = value.strip()
        if any(character.isspace() for character in value):
            raise ValueError("用户名不能包含空白字符")
        if any(character in "<>/\\" for character in value):
            raise ValueError("用户名包含不支持的字符")
        return value

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class LoginRequest(ContractModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class AuthUser(ContractModel):
    user_id: str
    username: str
    display_name: str
    role: str = "user"
    status: str = "active"
    onboarding_required: bool = False
    created_at: datetime


class StoredAuthUser(AuthUser):
    normalized_username: str
    password_hash: str
    password_salt: str
    password_iterations: int = Field(gt=0)


class AccountProfile(ContractModel):
    user_id: str
    display_name: str
    gender: str = "unspecified"
    birth_date: date | None = None
    region: str = ""
    contact_email: EmailStr | None = None
    signature: str = ""
    avatar_key: str | None = None
    avatar_version: int = Field(default=0, ge=0)
    updated_at: datetime | None = None


class AccountProfileUpdateRequest(ContractModel):
    display_name: str = Field(min_length=1, max_length=64)
    gender: str = Field(default="unspecified", pattern="^(male|female|unspecified)$")
    birth_date: date | None = None
    region: str = Field(default="", max_length=128)
    contact_email: EmailStr | None = None
    signature: str = Field(default="", max_length=240)

    @field_validator("display_name", "region", "signature", mode="before")
    @classmethod
    def normalize_text(cls, value: str | None) -> str:
        return str(value or "").strip()


class AuthSession(ContractModel):
    session_id: str
    user_id: str
    token_hash: str
    expires_at: datetime
    created_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None = None


class AuthResponse(ContractModel):
    user: AuthUser
    expires_at: datetime

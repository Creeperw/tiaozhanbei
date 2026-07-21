from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

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
    status: str = "active"
    created_at: datetime


class StoredAuthUser(AuthUser):
    normalized_username: str
    password_hash: str
    password_salt: str
    password_iterations: int = Field(gt=0)


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


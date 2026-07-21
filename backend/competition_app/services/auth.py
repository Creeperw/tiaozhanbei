from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from competition_app.contracts.auth import (
    AuthResponse,
    AuthSession,
    AuthUser,
    LoginRequest,
    RegisterRequest,
    StoredAuthUser,
)
from competition_app.repositories.auth import AuthRepository


class InvalidCredentialsError(ValueError):
    pass


class AuthenticationService:
    password_iterations = 310_000

    def __init__(
        self,
        repository: AuthRepository,
        session_ttl_hours: int = 24 * 30,
        *,
        admin_username: str | None = None,
        admin_password: str | None = None,
    ) -> None:
        self.repository = repository
        self.session_ttl = timedelta(hours=session_ttl_hours)
        if admin_username and admin_password:
            self._ensure_admin(admin_username, admin_password)

    def register(self, request: RegisterRequest) -> tuple[AuthResponse, str]:
        now = datetime.now(timezone.utc)
        user = self._build_user(
            request.username,
            request.password,
            request.display_name or request.username,
            role="user",
            now=now,
        )
        self.repository.create_user(user)
        return self._start_session(user, now)

    def _build_user(
        self,
        username: str,
        password: str,
        display_name: str,
        *,
        role: str,
        now: datetime,
    ) -> StoredAuthUser:
        salt = secrets.token_bytes(16)
        return StoredAuthUser(
            user_id=f"USER_{uuid4().hex}",
            username=username,
            normalized_username=self.normalize_username(username),
            display_name=display_name,
            role=role,
            password_hash=self._derive_password(
                password, salt, self.password_iterations
            ).hex(),
            password_salt=salt.hex(),
            password_iterations=self.password_iterations,
            created_at=now,
        )

    def _ensure_admin(self, username: str, password: str) -> None:
        existing = self.repository.get_user_by_normalized_username(
            self.normalize_username(username)
        )
        if existing is not None:
            if existing.role != "admin":
                raise RuntimeError("管理员用户名已被普通账号占用")
            return
        now = datetime.now(timezone.utc)
        self.repository.create_user(
            self._build_user(
                username,
                password,
                username,
                role="admin",
                now=now,
            )
        )

    def login(self, request: LoginRequest) -> tuple[AuthResponse, str]:
        user = self.repository.get_user_by_normalized_username(
            self.normalize_username(request.username)
        )
        if user is None or user.status != "active" or not self._verify_password(
            request.password, user
        ):
            raise InvalidCredentialsError("用户名或密码不正确")
        return self._start_session(user, datetime.now(timezone.utc))

    def authenticate(self, raw_token: str | None) -> AuthUser | None:
        if not raw_token:
            return None
        token_hash = self.hash_token(raw_token)
        session = self.repository.get_session(token_hash)
        now = datetime.now(timezone.utc)
        if session is None or session.revoked_at is not None:
            return None
        if self._as_utc(session.expires_at) <= now:
            self.repository.revoke_session(token_hash, now)
            return None
        user = self.repository.get_user(session.user_id)
        if user is None or user.status != "active":
            return None
        return self._public_user(user)

    def logout(self, raw_token: str | None) -> None:
        if raw_token:
            self.repository.revoke_session(
                self.hash_token(raw_token), datetime.now(timezone.utc)
            )

    def _start_session(
        self, user: StoredAuthUser, now: datetime
    ) -> tuple[AuthResponse, str]:
        raw_token = secrets.token_urlsafe(48)
        expires_at = now + self.session_ttl
        self.repository.create_session(
            AuthSession(
                session_id=f"SESSION_{uuid4().hex}",
                user_id=user.user_id,
                token_hash=self.hash_token(raw_token),
                expires_at=expires_at,
                created_at=now,
                last_seen_at=now,
            )
        )
        return AuthResponse(user=self._public_user(user), expires_at=expires_at), raw_token

    @staticmethod
    def normalize_username(username: str) -> str:
        return unicodedata.normalize("NFKC", username.strip()).casefold()

    @staticmethod
    def hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @staticmethod
    def _derive_password(password: str, salt: bytes, iterations: int) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )

    def _verify_password(self, password: str, user: StoredAuthUser) -> bool:
        actual = self._derive_password(
            password, bytes.fromhex(user.password_salt), user.password_iterations
        )
        return hmac.compare_digest(actual.hex(), user.password_hash)

    @staticmethod
    def _public_user(user: StoredAuthUser) -> AuthUser:
        return AuthUser.model_validate(
            user.model_dump(
                include={"user_id", "username", "display_name", "role", "status", "created_at"}
            )
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

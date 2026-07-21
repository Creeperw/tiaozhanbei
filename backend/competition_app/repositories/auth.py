from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Protocol

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from competition_app.contracts.auth import AuthSession, StoredAuthUser


class UsernameTakenError(ValueError):
    pass


class AuthRepository(Protocol):
    def create_user(self, user: StoredAuthUser) -> None: ...

    def get_user_by_normalized_username(
        self, normalized_username: str
    ) -> StoredAuthUser | None: ...

    def get_user(self, user_id: str) -> StoredAuthUser | None: ...

    def create_session(self, session: AuthSession) -> None: ...

    def get_session(self, token_hash: str) -> AuthSession | None: ...

    def revoke_session(self, token_hash: str, revoked_at: datetime) -> None: ...


class InMemoryAuthRepository:
    def __init__(self) -> None:
        self._users: dict[str, StoredAuthUser] = {}
        self._user_ids_by_name: dict[str, str] = {}
        self._sessions: dict[str, AuthSession] = {}
        self._lock = RLock()

    def create_user(self, user: StoredAuthUser) -> None:
        with self._lock:
            if user.normalized_username in self._user_ids_by_name:
                raise UsernameTakenError("该用户名已被注册")
            self._users[user.user_id] = user.model_copy(deep=True)
            self._user_ids_by_name[user.normalized_username] = user.user_id

    def get_user_by_normalized_username(
        self, normalized_username: str
    ) -> StoredAuthUser | None:
        with self._lock:
            user_id = self._user_ids_by_name.get(normalized_username)
            user = self._users.get(user_id) if user_id else None
            return user.model_copy(deep=True) if user else None

    def get_user(self, user_id: str) -> StoredAuthUser | None:
        with self._lock:
            user = self._users.get(user_id)
            return user.model_copy(deep=True) if user else None

    def create_session(self, session: AuthSession) -> None:
        with self._lock:
            self._sessions[session.token_hash] = session.model_copy(deep=True)

    def get_session(self, token_hash: str) -> AuthSession | None:
        with self._lock:
            session = self._sessions.get(token_hash)
            return session.model_copy(deep=True) if session else None

    def revoke_session(self, token_hash: str, revoked_at: datetime) -> None:
        with self._lock:
            session = self._sessions.get(token_hash)
            if session is not None:
                self._sessions[token_hash] = session.model_copy(
                    update={"revoked_at": revoked_at, "last_seen_at": revoked_at}
                )


class SqlAuthRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_user(self, user: StoredAuthUser) -> None:
        values = user.model_dump(mode="python")
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO app_users "
                        "(user_id, username, normalized_username, display_name, "
                        "password_hash, password_salt, password_iterations, role, status, created_at) "
                        "VALUES (:user_id, :username, :normalized_username, :display_name, "
                        ":password_hash, :password_salt, :password_iterations, :role, :status, :created_at)"
                    ),
                    values,
                )
                connection.execute(
                    text(
                        "INSERT INTO learners (learner_id, status, created_at) "
                        "VALUES (:user_id, :status, :created_at)"
                    ),
                    values,
                )
        except IntegrityError as exc:
            raise UsernameTakenError("该用户名已被注册") from exc

    def get_user_by_normalized_username(
        self, normalized_username: str
    ) -> StoredAuthUser | None:
        return self._get_user(
            "normalized_username=:identity", normalized_username
        )

    def get_user(self, user_id: str) -> StoredAuthUser | None:
        return self._get_user("user_id=:identity", user_id)

    def _get_user(self, where: str, identity: str) -> StoredAuthUser | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT user_id, username, normalized_username, display_name, "
                    "password_hash, password_salt, password_iterations, role, status, created_at "
                    f"FROM app_users WHERE {where}"
                ),
                {"identity": identity},
            ).mappings().first()
        if row is None:
            return None
        return StoredAuthUser.model_validate(dict(row))

    def create_session(self, session: AuthSession) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO auth_sessions "
                    "(session_id, user_id, token_hash, expires_at, created_at, last_seen_at) "
                    "VALUES (:session_id, :user_id, :token_hash, :expires_at, "
                    ":created_at, :last_seen_at)"
                ),
                session.model_dump(mode="python"),
            )

    def get_session(self, token_hash: str) -> AuthSession | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT session_id, user_id, token_hash, expires_at, created_at, "
                    "last_seen_at, revoked_at FROM auth_sessions "
                    "WHERE token_hash=:token_hash"
                ),
                {"token_hash": token_hash},
            ).mappings().first()
        if row is None:
            return None
        values = dict(row)
        for key in ("expires_at", "created_at", "last_seen_at", "revoked_at"):
            value = values.get(key)
            if value is not None and value.tzinfo is None:
                values[key] = value.replace(tzinfo=timezone.utc)
        return AuthSession.model_validate(values)

    def revoke_session(self, token_hash: str, revoked_at: datetime) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE auth_sessions SET revoked_at=:revoked_at, "
                    "last_seen_at=:revoked_at WHERE token_hash=:token_hash "
                    "AND revoked_at IS NULL"
                ),
                {"token_hash": token_hash, "revoked_at": revoked_at},
            )

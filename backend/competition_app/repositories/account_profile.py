from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Protocol

from sqlalchemy import Engine, text

from competition_app.contracts.auth import AccountProfile


def _as_utc_datetime(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class AccountProfileRepository(Protocol):
    def get(self, user_id: str, display_name: str) -> AccountProfile: ...

    def save(self, profile: AccountProfile) -> AccountProfile: ...


class InMemoryAccountProfileRepository:
    def __init__(self) -> None:
        self._profiles: dict[str, AccountProfile] = {}
        self._lock = RLock()

    def get(self, user_id: str, display_name: str) -> AccountProfile:
        with self._lock:
            profile = self._profiles.get(user_id)
            if profile is None:
                profile = AccountProfile(user_id=user_id, display_name=display_name)
            return profile.model_copy(deep=True)

    def save(self, profile: AccountProfile) -> AccountProfile:
        with self._lock:
            updated = profile.model_copy(update={"updated_at": datetime.now(timezone.utc)})
            self._profiles[profile.user_id] = updated
            return updated.model_copy(deep=True)


class SqlAccountProfileRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, user_id: str, display_name: str) -> AccountProfile:
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT user_id, gender, birth_date, region, contact_email, "
                    "signature, avatar_key, avatar_version, updated_at "
                    "FROM app_user_profiles WHERE user_id=:user_id"
                ),
                {"user_id": user_id},
            ).mappings().first()
        if row is None:
            return AccountProfile(user_id=user_id, display_name=display_name)
        values = dict(row)
        values["display_name"] = display_name
        values["updated_at"] = _as_utc_datetime(values.get("updated_at"))
        return AccountProfile.model_validate(values)

    def save(self, profile: AccountProfile) -> AccountProfile:
        values = profile.model_dump(mode="python", exclude={"display_name", "updated_at"})
        if values.get("contact_email") is not None:
            values["contact_email"] = str(values["contact_email"])
        upsert = (
            "INSERT INTO app_user_profiles "
            "(user_id, gender, birth_date, region, contact_email, signature, "
            "avatar_key, avatar_version) "
            "VALUES (:user_id, :gender, :birth_date, :region, :contact_email, "
            ":signature, :avatar_key, :avatar_version) "
        )
        if self.engine.dialect.name == "sqlite":
            upsert += (
                "ON CONFLICT(user_id) DO UPDATE SET gender=excluded.gender, "
                "birth_date=excluded.birth_date, region=excluded.region, "
                "contact_email=excluded.contact_email, signature=excluded.signature, "
                "avatar_key=excluded.avatar_key, avatar_version=excluded.avatar_version"
            )
        else:
            upsert += (
                "ON DUPLICATE KEY UPDATE gender=VALUES(gender), "
                "birth_date=VALUES(birth_date), region=VALUES(region), "
                "contact_email=VALUES(contact_email), signature=VALUES(signature), "
                "avatar_key=VALUES(avatar_key), avatar_version=VALUES(avatar_version)"
            )
        with self.engine.begin() as connection:
            connection.execute(text(upsert), values)
        return self.get(profile.user_id, profile.display_name)
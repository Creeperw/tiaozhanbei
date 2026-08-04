from __future__ import annotations
from APP.backend.time_utils import utc_now

import json
from datetime import datetime, timedelta
from pathlib import Path

from APP.backend.auth import get_password_hash
from APP.backend.database import (
    AgentEvent,
    DbMessage,
    DbSession,
    PersonalizationMemory,
    SessionLocal,
    UserModel,
    UserProfile,
)

SAMPLE_FILE = Path(__file__).resolve().parents[1] / "sample_data" / "phase3_dashboard_seed.json"


def _parse_seed() -> dict:
    return json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))


def _upsert_user(db, item: dict) -> UserModel:
    user = db.query(UserModel).filter(UserModel.username == item["username"]).first()
    if user:
        user.email = item["email"]
        user.hashed_password = get_password_hash(item["password"])
        user.role = "user"
        return user

    user = UserModel(
        username=item["username"],
        email=item["email"],
        hashed_password=get_password_hash(item["password"]),
        role="user",
    )
    db.add(user)
    db.flush()
    return user


def _upsert_profile(db, user_id: int, profile_data: dict) -> None:
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not profile:
        profile = UserProfile(user_id=user_id)
        db.add(profile)
    for key, value in profile_data.items():
        setattr(profile, key, value)


def _replace_user_dashboard_context(db, user_id: int) -> None:
    db.query(AgentEvent).filter(AgentEvent.user_id == user_id).delete()
    db.query(PersonalizationMemory).filter(PersonalizationMemory.user_id == user_id).delete()
    sessions = db.query(DbSession).filter(DbSession.user_id == user_id).all()
    for session in sessions:
        db.query(DbMessage).filter(DbMessage.session_id == session.id).delete()
    db.query(DbSession).filter(DbSession.user_id == user_id).delete()


def _create_memories(db, user_id: int, memories: list[dict], base_time: datetime) -> None:
    for index, item in enumerate(memories):
        db.add(PersonalizationMemory(
            user_id=user_id,
            category=item.get("category", "short_term"),
            importance=item.get("importance", "normal"),
            title=item.get("title", ""),
            content=item.get("content", ""),
            source="phase3_seed",
            is_active=True,
            confidence=0.9,
            created_at=base_time - timedelta(hours=index + 1),
            updated_at=base_time - timedelta(hours=index + 1),
        ))


def _create_sessions(db, user_id: int, sessions: list[dict], base_time: datetime) -> None:
    for index, item in enumerate(sessions):
        session_time = base_time - timedelta(days=index)
        session = DbSession(
            id=item["id"],
            user_id=user_id,
            title=item.get("title") or "演示学习会话",
            title_auto_enabled=False,
            created_at=session_time,
        )
        db.add(session)
        db.flush()

        parent_id = None
        latest_id = None
        for message_index, message in enumerate(item.get("messages", [])):
            row = DbMessage(
                session_id=session.id,
                parent_id=parent_id,
                role=message.get("role", "user"),
                content=message.get("content", ""),
                files="[]",
                timestamp=(session_time + timedelta(minutes=message_index)).isoformat(),
                created_at=session_time + timedelta(minutes=message_index),
            )
            db.add(row)
            db.flush()
            parent_id = row.id
            latest_id = row.id
        session.active_leaf_message_id = latest_id


def _create_agent_events(db, user_id: int, events: list[dict], base_time: datetime) -> None:
    for index, item in enumerate(events):
        db.add(AgentEvent(
            user_id=user_id,
            session_id=None,
            agent_name=item.get("agent_name", "demo_agent"),
            event_type=item.get("event_type", "run"),
            input_summary=item.get("input_summary", ""),
            output_summary=item.get("output_summary", ""),
            payload=json.dumps({"source": "phase3_seed"}, ensure_ascii=False),
            created_at=base_time - timedelta(minutes=index * 15),
        ))


def seed_phase3_dashboard_data() -> list[str]:
    seed = _parse_seed()
    db = SessionLocal()
    try:
        usernames = []
        base_time = utc_now().replace(microsecond=0)
        for item in seed.get("users", []):
            user = _upsert_user(db, item)
            db.flush()
            _replace_user_dashboard_context(db, user.id)
            _upsert_profile(db, user.id, item.get("profile", {}))
            _create_memories(db, user.id, item.get("memories", []), base_time)
            _create_sessions(db, user.id, item.get("sessions", []), base_time)
            _create_agent_events(db, user.id, item.get("agent_events", []), base_time)
            usernames.append(item["username"])
        db.commit()
        return usernames
    finally:
        db.close()


if __name__ == "__main__":
    seeded = seed_phase3_dashboard_data()
    print("Seeded Phase 3 dashboard users:")
    for username in seeded:
        print(f"- {username} / Demo@123456")

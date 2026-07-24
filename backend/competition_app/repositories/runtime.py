from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import Engine, text


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _copy_json(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False, default=_json_default))


class RunStateRepository(Protocol):
    def get(self, thread_id: str) -> dict[str, Any] | None: ...

    def get_by_execution_id(
        self,
        execution_id: str,
        learner_id: str,
    ) -> dict[str, Any] | None: ...

    def save(self, thread_id: str, state: dict[str, Any]) -> None: ...


class InMemoryRunStateRepository:
    def __init__(self, capacity: int = 100) -> None:
        self.capacity = capacity
        self._states: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def get(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._states.get(thread_id)
            return _copy_json(state) if state is not None else None

    def get_by_execution_id(
        self,
        execution_id: str,
        learner_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            state = next(
                (
                    value
                    for value in self._states.values()
                    if value.get("execution_id") == execution_id
                    and value.get("learner_id") == learner_id
                ),
                None,
            )
            return _copy_json(state) if state is not None else None

    def save(self, thread_id: str, state: dict[str, Any]) -> None:
        with self._lock:
            merged = {**self._states.get(thread_id, {}), **_copy_json(state)}
            self._states[thread_id] = merged
            while len(self._states) > self.capacity:
                self._states.pop(next(iter(self._states)))


class SqlRunStateRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, thread_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            payload = connection.execute(
                text(
                    "SELECT payload_json FROM workflow_run_states "
                    "WHERE thread_id=:thread_id"
                ),
                {"thread_id": thread_id},
            ).scalar_one_or_none()
        if payload is None:
            return None
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        return json.loads(payload) if isinstance(payload, str) else dict(payload)

    def get_by_execution_id(
        self,
        execution_id: str,
        learner_id: str,
    ) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            payload = connection.execute(
                text(
                    "SELECT payload_json FROM workflow_run_states "
                    "WHERE execution_id=:execution_id AND learner_id=:learner_id"
                ),
                {
                    "execution_id": execution_id,
                    "learner_id": learner_id,
                },
            ).scalar_one_or_none()
        if payload is None:
            return None
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        return json.loads(payload) if isinstance(payload, str) else dict(payload)

    def save(self, thread_id: str, state: dict[str, Any]) -> None:
        with self.engine.begin() as connection:
            existing_payload = connection.execute(
                text(
                    "SELECT payload_json FROM workflow_run_states "
                    "WHERE thread_id=:thread_id"
                ),
                {"thread_id": thread_id},
            ).scalar_one_or_none()
            if isinstance(existing_payload, (bytes, bytearray)):
                existing_payload = existing_payload.decode("utf-8")
            if isinstance(existing_payload, str):
                existing = json.loads(existing_payload)
            elif existing_payload:
                existing = dict(existing_payload)
            else:
                existing = {}
            merged = {**existing, **_copy_json(state)}
            values = {
                "thread_id": thread_id,
                "execution_id": merged.get("execution_id"),
                "case_id": merged.get("case_id"),
                "learner_id": merged.get("learner_id"),
                "status": str(merged.get("status", "unknown")),
                "payload_json": json.dumps(merged, ensure_ascii=False),
            }
            if existing_payload is None:
                connection.execute(
                    text(
                        "INSERT INTO workflow_run_states "
                        "(thread_id, execution_id, case_id, learner_id, status, payload_json) "
                        "VALUES (:thread_id, :execution_id, :case_id, :learner_id, :status, :payload_json)"
                    ),
                    values,
                )
            else:
                connection.execute(
                    text(
                        "UPDATE workflow_run_states SET execution_id=:execution_id, "
                        "case_id=:case_id, learner_id=:learner_id, status=:status, "
                        "payload_json=:payload_json, updated_at=CURRENT_TIMESTAMP "
                        "WHERE thread_id=:thread_id"
                    ),
                    values,
                )
            self._save_execution_summary(connection, merged)

    @staticmethod
    def _save_execution_summary(connection, state: dict[str, Any]) -> None:
        execution_id = state.get("execution_id")
        case_id = state.get("case_id")
        if not execution_id or not case_id:
            return
        exists = connection.execute(
            text("SELECT execution_id FROM execution_runs WHERE execution_id=:execution_id"),
            {"execution_id": execution_id},
        ).first()
        values = {
            "execution_id": execution_id,
            "case_id": case_id,
            "status": str(state.get("status", "unknown")),
        }
        if exists:
            connection.execute(
                text(
                    "UPDATE execution_runs SET status=:status "
                    "WHERE execution_id=:execution_id"
                ),
                values,
            )
        else:
            connection.execute(
                text(
                    "INSERT INTO execution_runs (execution_id, case_id, status) "
                    "VALUES (:execution_id, :case_id, :status)"
                ),
                values,
            )


class ConversationRepository(Protocol):
    def create_session(self, session_id: str, learner_id: str, title: str) -> None: ...

    def list_sessions(self, learner_id: str) -> list[dict[str, Any]]: ...

    def get_messages(self, session_id: str, learner_id: str) -> list[dict[str, Any]]: ...

    def rename_session(self, session_id: str, learner_id: str, title: str) -> bool: ...

    def delete_session(self, session_id: str, learner_id: str) -> bool: ...

    def save_messages(
        self,
        session_id: str,
        learner_id: str,
        messages: list[dict[str, Any]],
    ) -> None: ...


class InMemoryConversationRepository:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def create_session(self, session_id: str, learner_id: str, title: str) -> None:
        with self._lock:
            existing = self.sessions.get(session_id)
            if existing is not None and existing["learner_id"] != learner_id:
                raise ValueError("conversation session belongs to another learner")
            self.sessions.setdefault(
                session_id,
                {
                    "learner_id": learner_id,
                    "title": title,
                    "messages": {},
                    "created_at": datetime.utcnow().isoformat(),
                },
            )

    def list_sessions(self, learner_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                {
                    "id": session_id,
                    "title": session.get("title") or "新对话",
                    "created_at": session.get("created_at"),
                }
                for session_id, session in self.sessions.items()
                if session["learner_id"] == learner_id
            ]
        return list(reversed(rows))

    def get_messages(self, session_id: str, learner_id: str) -> list[dict[str, Any]]:
        with self._lock:
            session = self.sessions.get(session_id)
            if session is None or session["learner_id"] != learner_id:
                return []
            return [
                {"message_id": message_id, **_copy_json(message)}
                for message_id, message in session["messages"].items()
            ]

    def rename_session(self, session_id: str, learner_id: str, title: str) -> bool:
        with self._lock:
            session = self.sessions.get(session_id)
            if session is None or session["learner_id"] != learner_id:
                return False
            session["title"] = title
            return True

    def delete_session(self, session_id: str, learner_id: str) -> bool:
        with self._lock:
            session = self.sessions.get(session_id)
            if session is None or session["learner_id"] != learner_id:
                return False
            del self.sessions[session_id]
            return True

    def save_messages(
        self,
        session_id: str,
        learner_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        with self._lock:
            session = self.sessions.setdefault(
                session_id,
                {
                    "learner_id": learner_id,
                    "title": "新对话",
                    "messages": {},
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
            if session["learner_id"] != learner_id:
                raise ValueError("conversation session belongs to another learner")
            for index, message in enumerate(messages):
                message_id = _message_id(session_id, index, message)
                session["messages"][message_id] = _copy_json(message)


class SqlConversationRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_session(self, session_id: str, learner_id: str, title: str) -> None:
        with self.engine.begin() as connection:
            owner = connection.execute(
                text("SELECT learner_id FROM conversation_sessions WHERE session_id=:session_id"),
                {"session_id": session_id},
            ).scalar_one_or_none()
            if owner is not None:
                if owner != learner_id:
                    raise ValueError("conversation session belongs to another learner")
                return
            connection.execute(
                text(
                    "INSERT INTO conversation_sessions (session_id, learner_id, title) "
                    "VALUES (:session_id, :learner_id, :title)"
                ),
                {"session_id": session_id, "learner_id": learner_id, "title": title},
            )

    def list_sessions(self, learner_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT session_id, title, created_at FROM conversation_sessions "
                    "WHERE learner_id=:learner_id ORDER BY created_at DESC"
                ),
                {"learner_id": learner_id},
            ).mappings().all()
        return [
            {"id": row["session_id"], "title": row["title"] or "新对话", "created_at": row["created_at"]}
            for row in rows
        ]

    def get_messages(self, session_id: str, learner_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            owner = connection.execute(
                text("SELECT learner_id FROM conversation_sessions WHERE session_id=:session_id"),
                {"session_id": session_id},
            ).scalar_one_or_none()
            if owner != learner_id:
                return []
            rows = connection.execute(
                text(
                    "SELECT message_id, role, content, metadata_json, created_at FROM conversation_messages "
                    "WHERE session_id=:session_id ORDER BY created_at, message_id"
                ),
                {"session_id": session_id},
            ).mappings().all()
        messages: list[dict[str, Any]] = []
        for row in rows:
            metadata = row.get("metadata_json")
            if isinstance(metadata, str) and metadata.strip():
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            messages.append({
                "message_id": row["message_id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
                **metadata,
            })
        return messages

    def rename_session(self, session_id: str, learner_id: str, title: str) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(
                text(
                    "UPDATE conversation_sessions SET title=:title "
                    "WHERE session_id=:session_id AND learner_id=:learner_id"
                ),
                {"session_id": session_id, "learner_id": learner_id, "title": title},
            )
        return bool(result.rowcount)

    def delete_session(self, session_id: str, learner_id: str) -> bool:
        with self.engine.begin() as connection:
            owner = connection.execute(
                text(
                    "SELECT learner_id FROM conversation_sessions "
                    "WHERE session_id=:session_id"
                ),
                {"session_id": session_id},
            ).scalar_one_or_none()
            if owner != learner_id:
                return False
            connection.execute(
                text("DELETE FROM conversation_messages WHERE session_id=:session_id"),
                {"session_id": session_id},
            )
            connection.execute(
                text("DELETE FROM conversation_sessions WHERE session_id=:session_id"),
                {"session_id": session_id},
            )
        return True

    def save_messages(
        self,
        session_id: str,
        learner_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        with self.engine.begin() as connection:
            owner = connection.execute(
                text(
                    "SELECT learner_id FROM conversation_sessions "
                    "WHERE session_id=:session_id"
                ),
                {"session_id": session_id},
            ).scalar_one_or_none()
            if owner is None:
                connection.execute(
                    text(
                        "INSERT INTO conversation_sessions (session_id, learner_id, title) "
                        "VALUES (:session_id, :learner_id, :title)"
                    ),
                    {"session_id": session_id, "learner_id": learner_id, "title": "新对话"},
                )
            elif owner != learner_id:
                raise ValueError("conversation session belongs to another learner")
            for index, message in enumerate(messages):
                message_id = _message_id(session_id, index, message)
                exists = connection.execute(
                    text(
                        "SELECT message_id FROM conversation_messages "
                        "WHERE message_id=:message_id"
                    ),
                    {"message_id": message_id},
                ).first()
                if exists:
                    continue
                connection.execute(
                    text(
                        "INSERT INTO conversation_messages "
                        "(message_id, session_id, role, content, metadata_json) "
                        "VALUES (:message_id, :session_id, :role, :content, :metadata_json)"
                    ),
                    {
                        "message_id": message_id,
                        "session_id": session_id,
                        "role": str(message.get("role", "user")),
                        "content": str(message.get("content", "")),
                        "metadata_json": json.dumps(
                            {
                                key: value
                                for key, value in message.items()
                                if key not in {"message_id", "role", "content", "created_at"}
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    },
                )


def _message_id(session_id: str, index: int, message: dict[str, Any]) -> str:
    supplied = message.get("message_id")
    if supplied:
        return str(supplied)
    fingerprint = json.dumps(
        [session_id, index, message.get("role"), message.get("content")],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"MSG_{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:32]}"

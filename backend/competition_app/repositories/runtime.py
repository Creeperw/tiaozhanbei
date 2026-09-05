from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from competition_app.services.conversation_history import (
    sanitize_conversation_messages,
)
from competition_app.contracts.exam_scope import LEGACY_EXAM_SCOPE
from competition_app.exam_scope import current_exam_scope


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

    def request_cancellation(self, thread_id: str) -> dict[str, Any] | None: ...

    def mark_cancelled(self, thread_id: str) -> dict[str, Any] | None: ...

    def claim_active_run(
        self, learner_id: str, product_surface: str, thread_id: str
    ) -> str: ...

    def release_active_run(
        self, learner_id: str, product_surface: str, thread_id: str
    ) -> None: ...

    def recover_abandoned_active_runs(self) -> list[str]: ...


class InMemoryRunStateRepository:
    def __init__(self, capacity: int = 100) -> None:
        self.capacity = capacity
        self._states: dict[str, dict[str, Any]] = {}
        self._lock = RLock()
        self._active_runs: dict[tuple[str, str], str] = {}

    def claim_active_run(
        self, learner_id: str, product_surface: str, thread_id: str
    ) -> str:
        with self._lock:
            key = (learner_id, product_surface)
            existing = self._active_runs.get(key)
            if existing is None:
                self._active_runs[key] = thread_id
                return thread_id
            return existing

    def release_active_run(
        self, learner_id: str, product_surface: str, thread_id: str
    ) -> None:
        with self._lock:
            key = (learner_id, product_surface)
            if self._active_runs.get(key) == thread_id:
                self._active_runs.pop(key, None)

    def recover_abandoned_active_runs(self) -> list[str]:
        return []

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
            existing = self._states.get(thread_id, {})
            if existing.get("status") in {
                "cancelled",
                "cancellation_requested",
            } and state.get("status") not in {
                "cancelled",
                "cancellation_requested",
            }:
                return
            merged = {**existing, **_copy_json(state)}
            self._states[thread_id] = merged
            if merged.get("status") not in {"running", "cancellation_requested"} and merged.get("product_surface"):
                self.release_active_run(
                    str(merged.get("learner_id") or ""),
                    str(merged["product_surface"]),
                    thread_id,
                )
            while len(self._states) > self.capacity:
                self._states.pop(next(iter(self._states)))

    def request_cancellation(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock:
            existing = self._states.get(thread_id)
            if existing is None:
                return None
            if existing.get("status") in {"cancelled", "completed", "failed", "interrupted", "waiting_human_review"}:
                return _copy_json(existing)
            existing["status"] = "cancellation_requested"
            existing["cancellation_requested_at"] = datetime.utcnow().isoformat()
            return _copy_json(existing)

    def mark_cancelled(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock:
            existing = self._states.get(thread_id)
            if existing is None:
                return None
            if existing.get("status") in {"completed", "failed", "interrupted", "waiting_human_review"}:
                return _copy_json(existing)
            existing.update({
                "status": "cancelled",
                "cancelled_at": datetime.utcnow().isoformat(),
                "continuation": None,
                "error_type": None,
                "error_code": None,
                "retryable": False,
            })
            if existing.get("product_surface"):
                self.release_active_run(
                    str(existing.get("learner_id") or ""),
                    str(existing["product_surface"]),
                    thread_id,
                )
            return _copy_json(existing)


class SqlRunStateRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def claim_active_run(
        self, learner_id: str, product_surface: str, thread_id: str
    ) -> str:
        values = {
            "learner_id": learner_id,
            "product_surface": product_surface,
            "thread_id": thread_id,
        }
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO workflow_active_run_claims "
                        "(learner_id, product_surface, thread_id) "
                        "VALUES (:learner_id, :product_surface, :thread_id)"
                    ),
                    values,
                )
            return thread_id
        except IntegrityError:
            with self.engine.connect() as connection:
                existing = connection.execute(
                    text(
                        "SELECT thread_id FROM workflow_active_run_claims "
                        "WHERE learner_id=:learner_id "
                        "AND product_surface=:product_surface"
                    ),
                    values,
                ).scalar_one()
            return str(existing)

    def release_active_run(
        self, learner_id: str, product_surface: str, thread_id: str
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM workflow_active_run_claims "
                    "WHERE learner_id=:learner_id "
                    "AND product_surface=:product_surface "
                    "AND thread_id=:thread_id"
                ),
                {
                    "learner_id": learner_id,
                    "product_surface": product_surface,
                    "thread_id": thread_id,
                },
            )

    def recover_abandoned_active_runs(self) -> list[str]:
        """Close leases left by the previous application process.

        The service runs one workflow owner process. Graceful shutdown cancels
        its tasks; an ungraceful restart may leave durable rows marked running.
        Startup converts only claimed product runs to a retryable terminal
        state before accepting new requests.
        """

        with self.engine.begin() as connection:
            rows = list(
                connection.execute(
                    text(
                        "SELECT c.thread_id, s.payload_json "
                        "FROM workflow_active_run_claims c "
                        "LEFT JOIN workflow_run_states s "
                        "ON s.thread_id=c.thread_id"
                    )
                )
            )
            recovered: list[str] = []
            for thread_id, raw_payload in rows:
                if isinstance(raw_payload, (bytes, bytearray)):
                    raw_payload = raw_payload.decode("utf-8")
                if isinstance(raw_payload, str):
                    payload = json.loads(raw_payload)
                elif raw_payload:
                    payload = dict(raw_payload)
                else:
                    payload = {}
                if payload:
                    payload.update(
                        {
                            "status": "failed",
                            "error_type": "WorkflowOwnerRestarted",
                            "error_code": "workflow_restart",
                            "retryable": True,
                            "continuation": None,
                        }
                    )
                    connection.execute(
                        text(
                            "UPDATE workflow_run_states SET status='failed', "
                            "payload_json=:payload_json, updated_at=CURRENT_TIMESTAMP "
                            "WHERE thread_id=:thread_id"
                        ),
                        {
                            "thread_id": str(thread_id),
                            "payload_json": json.dumps(payload, ensure_ascii=False),
                        },
                    )
                recovered.append(str(thread_id))
            connection.execute(text("DELETE FROM workflow_active_run_claims"))
        return recovered

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
            if existing.get("status") in {
                "cancelled",
                "cancellation_requested",
            } and state.get("status") not in {
                "cancelled",
                "cancellation_requested",
            }:
                return
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
            if merged.get("status") not in {"running", "cancellation_requested"} and merged.get("product_surface"):
                connection.execute(
                    text(
                        "DELETE FROM workflow_active_run_claims "
                        "WHERE learner_id=:learner_id "
                        "AND product_surface=:product_surface "
                        "AND thread_id=:thread_id"
                    ),
                    {
                        "learner_id": str(merged.get("learner_id") or ""),
                        "product_surface": str(merged["product_surface"]),
                        "thread_id": thread_id,
                    },
                )

    def request_cancellation(self, thread_id: str) -> dict[str, Any] | None:
        return self._transition_cancellation(thread_id, terminal=False)

    def mark_cancelled(self, thread_id: str) -> dict[str, Any] | None:
        return self._transition_cancellation(thread_id, terminal=True)

    def _transition_cancellation(
        self, thread_id: str, *, terminal: bool
    ) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            lock_clause = "" if self.engine.dialect.name == "sqlite" else " FOR UPDATE"
            raw = connection.execute(
                text(
                    "SELECT payload_json FROM workflow_run_states "
                    "WHERE thread_id=:thread_id" + lock_clause
                ),
                {"thread_id": thread_id},
            ).scalar_one_or_none()
            if raw is None:
                return None
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            state = json.loads(raw) if isinstance(raw, str) else dict(raw)
            current = str(state.get("status") or "")
            terminal_states = {
                "completed", "failed", "interrupted", "waiting_human_review"
            }
            if current in terminal_states or current == "cancelled":
                return state
            if terminal:
                state.update({
                    "status": "cancelled",
                    "cancelled_at": datetime.utcnow().isoformat(),
                    "continuation": None,
                    "error_type": None,
                    "error_code": None,
                    "retryable": False,
                })
            else:
                state.update({
                    "status": "cancellation_requested",
                    "cancellation_requested_at": datetime.utcnow().isoformat(),
                })
            connection.execute(
                text(
                    "UPDATE workflow_run_states SET status=:status, "
                    "payload_json=:payload_json, updated_at=CURRENT_TIMESTAMP "
                    "WHERE thread_id=:thread_id"
                ),
                {
                    "thread_id": thread_id,
                    "status": state["status"],
                    "payload_json": json.dumps(state, ensure_ascii=False),
                },
            )
            if terminal and state.get("product_surface"):
                connection.execute(
                    text(
                        "DELETE FROM workflow_active_run_claims "
                        "WHERE learner_id=:learner_id "
                        "AND product_surface=:product_surface "
                        "AND thread_id=:thread_id"
                    ),
                    {
                        "learner_id": str(state.get("learner_id") or ""),
                        "product_surface": str(state["product_surface"]),
                        "thread_id": thread_id,
                    },
                )
            return state

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
    def create_session(
        self, session_id: str, learner_id: str, title: str, source: str = "user"
    ) -> None: ...

    def list_sessions(
        self, learner_id: str, include_system: bool = False
    ) -> list[dict[str, Any]]: ...

    def get_messages(self, session_id: str, learner_id: str) -> list[dict[str, Any]]: ...

    def rename_session(self, session_id: str, learner_id: str, title: str) -> bool: ...

    def delete_session(self, session_id: str, learner_id: str) -> bool: ...

    def save_messages(
        self,
        session_id: str,
        learner_id: str,
        messages: list[dict[str, Any]],
        source: str = "user",
    ) -> None: ...

    def get_latest_context_summary(
        self, session_id: str, learner_id: str
    ) -> dict[str, Any] | None: ...

    def save_context_summary(
        self,
        session_id: str,
        learner_id: str,
        execution_id: str,
        summary: dict[str, Any],
    ) -> None: ...


class InMemoryConversationRepository:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self._summaries: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def create_session(
        self, session_id: str, learner_id: str, title: str, source: str = "user"
    ) -> None:
        scope = current_exam_scope(learner_id)
        with self._lock:
            existing = self.sessions.get(session_id)
            if existing is not None and existing["learner_id"] != learner_id:
                raise ValueError("conversation session belongs to another learner")
            self.sessions.setdefault(
                session_id,
                {
                    "learner_id": learner_id,
                    "exam_track_id": scope,
                    "title": title,
                    "source": source,
                    "messages": {},
                    "created_at": datetime.utcnow().isoformat(),
                },
            )

    def list_sessions(
        self, learner_id: str, include_system: bool = False
    ) -> list[dict[str, Any]]:
        scope = current_exam_scope(learner_id)
        with self._lock:
            rows = [
                {
                    "id": session_id,
                    "title": session.get("title") or "新对话",
                    "created_at": session.get("created_at"),
                }
                for session_id, session in self.sessions.items()
                if session["learner_id"] == learner_id
                and session.get("exam_track_id", LEGACY_EXAM_SCOPE) == scope
                and (
                    include_system
                    or session.get("source", "user") == "user"
                )
            ]
        return list(reversed(rows))

    def get_messages(self, session_id: str, learner_id: str) -> list[dict[str, Any]]:
        scope = current_exam_scope(learner_id)
        with self._lock:
            session = self.sessions.get(session_id)
            if (
                session is None
                or session["learner_id"] != learner_id
                or session.get("exam_track_id", LEGACY_EXAM_SCOPE) != scope
            ):
                return []
            messages = [
                {"message_id": message_id, **_copy_json(message)}
                for message_id, message in session["messages"].items()
            ]
            return sanitize_conversation_messages(messages)

    def rename_session(self, session_id: str, learner_id: str, title: str) -> bool:
        scope = current_exam_scope(learner_id)
        with self._lock:
            session = self.sessions.get(session_id)
            if (
                session is None
                or session["learner_id"] != learner_id
                or session.get("exam_track_id", LEGACY_EXAM_SCOPE) != scope
            ):
                return False
            session["title"] = title
            return True

    def delete_session(self, session_id: str, learner_id: str) -> bool:
        scope = current_exam_scope(learner_id)
        with self._lock:
            session = self.sessions.get(session_id)
            if (
                session is None
                or session["learner_id"] != learner_id
                or session.get("exam_track_id", LEGACY_EXAM_SCOPE) != scope
            ):
                return False
            del self.sessions[session_id]
            return True

    def save_messages(
        self,
        session_id: str,
        learner_id: str,
        messages: list[dict[str, Any]],
        source: str = "user",
    ) -> None:
        scope = current_exam_scope(learner_id)
        with self._lock:
            session = self.sessions.setdefault(
                session_id,
                {
                    "learner_id": learner_id,
                    "exam_track_id": scope,
                    "title": "新对话",
                    "source": source,
                    "messages": {},
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
            if (
                session["learner_id"] != learner_id
                or session.get("exam_track_id", LEGACY_EXAM_SCOPE) != scope
            ):
                raise ValueError("conversation session belongs to another learner")
            for index, message in enumerate(sanitize_conversation_messages(messages)):
                message_id = _message_id(session_id, index, message)
                session["messages"][message_id] = _copy_json(message)

    def get_latest_context_summary(
        self, session_id: str, learner_id: str
    ) -> dict[str, Any] | None:
        with self._lock:
            summary = self._summaries.get(session_id)
            if summary is None or summary.get("learner_id") != learner_id:
                return None
            return _copy_json(summary.get("payload"))

    def save_context_summary(
        self,
        session_id: str,
        learner_id: str,
        execution_id: str,
        summary: dict[str, Any],
    ) -> None:
        with self._lock:
            self._summaries[session_id] = {
                "learner_id": learner_id,
                "execution_id": execution_id,
                "payload": _copy_json(summary),
                "created_at": datetime.utcnow().isoformat(),
            }


class SqlConversationRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_session(
        self, session_id: str, learner_id: str, title: str, source: str = "user"
    ) -> None:
        scope = current_exam_scope(learner_id)
        with self.engine.begin() as connection:
            owner = self._session_owner(connection, session_id, scope)
            if owner is not None:
                if owner["learner_id"] != learner_id or not self._scope_matches(
                    owner.get("exam_track_id"), scope
                ):
                    raise ValueError("conversation session belongs to another learner")
                return
            self._insert_session(connection, session_id, learner_id, title, scope, source)

    def list_sessions(
        self, learner_id: str, include_system: bool = False
    ) -> list[dict[str, Any]]:
        scope = current_exam_scope(learner_id)
        source_clause = "" if include_system else " AND source='user'"
        with self.engine.connect() as connection:
            if scope == LEGACY_EXAM_SCOPE:
                query = (
                    "SELECT session_id, title, created_at FROM conversation_sessions "
                    "WHERE learner_id=:learner_id"
                    + source_clause
                    + " ORDER BY created_at DESC"
                )
                params = {"learner_id": learner_id}
            else:
                query = (
                    "SELECT session_id, title, created_at FROM conversation_sessions "
                    "WHERE learner_id=:learner_id AND exam_track_id=:exam_track_id"
                    + source_clause
                    + " ORDER BY created_at DESC"
                )
                params = {"learner_id": learner_id, "exam_track_id": scope}
            rows = connection.execute(text(query), params).mappings().all()
        return [
            {"id": row["session_id"], "title": row["title"] or "新对话", "created_at": row["created_at"]}
            for row in rows
        ]

    def get_messages(self, session_id: str, learner_id: str) -> list[dict[str, Any]]:
        scope = current_exam_scope(learner_id)
        with self.engine.connect() as connection:
            owner = self._session_owner(connection, session_id, scope)
            if (
                owner is None
                or owner["learner_id"] != learner_id
                or not self._scope_matches(owner.get("exam_track_id"), scope)
            ):
                return []
            rows = connection.execute(
                text(
                    "SELECT message_id, role, content, metadata_json, created_at FROM conversation_messages "
                    "WHERE session_id=:session_id ORDER BY created_at, message_id"
                ),
                {"session_id": session_id},
            ).mappings().all()
        sequenced_messages: list[tuple[int, dict[str, Any]]] = []
        for row_index, row in enumerate(rows):
            metadata = row.get("metadata_json")
            if isinstance(metadata, str) and metadata.strip():
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            sequence = metadata.pop("_conversation_sequence", None)
            if not isinstance(sequence, int):
                # Legacy rows have no durable sequence. Preserve the order
                # returned by their existing created_at/message_id query.
                sequence = 1_000_000_000 + row_index
            sequenced_messages.append((sequence, {
                **metadata,
                "message_id": row["message_id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
            }))
        messages = [item for _, item in sorted(sequenced_messages, key=lambda pair: pair[0])]
        return sanitize_conversation_messages(messages)

    def rename_session(self, session_id: str, learner_id: str, title: str) -> bool:
        scope = current_exam_scope(learner_id)
        with self.engine.begin() as connection:
            if scope == LEGACY_EXAM_SCOPE:
                query = (
                    "UPDATE conversation_sessions SET title=:title "
                    "WHERE session_id=:session_id AND learner_id=:learner_id"
                )
                params = {"session_id": session_id, "learner_id": learner_id, "title": title}
            else:
                query = (
                    "UPDATE conversation_sessions SET title=:title "
                    "WHERE session_id=:session_id AND learner_id=:learner_id "
                    "AND exam_track_id=:exam_track_id"
                )
                params = {
                    "session_id": session_id,
                    "learner_id": learner_id,
                    "title": title,
                    "exam_track_id": scope,
                }
            result = connection.execute(text(query), params)
        return bool(result.rowcount)

    def delete_session(self, session_id: str, learner_id: str) -> bool:
        scope = current_exam_scope(learner_id)
        with self.engine.begin() as connection:
            owner = self._session_owner(connection, session_id, scope)
            if (
                owner is None
                or owner["learner_id"] != learner_id
                or not self._scope_matches(owner.get("exam_track_id"), scope)
            ):
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
        source: str = "user",
    ) -> None:
        scope = current_exam_scope(learner_id)
        with self.engine.begin() as connection:
            owner = self._session_owner(connection, session_id, scope)
            if owner is None:
                self._insert_session(connection, session_id, learner_id, "新对话", scope, source)
            elif owner["learner_id"] != learner_id:
                raise ValueError("conversation session belongs to another learner")
            elif not self._scope_matches(owner.get("exam_track_id"), scope):
                # A session may have been created before the learner's exam
                # workspace was resolved (legacy rows have exam_track_id NULL).
                # Once the workflow resolves the real exam scope, adopt it for
                # this same-learner session instead of rejecting the write, so
                # planning runs can persist their outcome.
                if owner.get("exam_track_id") is None and scope != LEGACY_EXAM_SCOPE:
                    connection.execute(
                        text(
                            "UPDATE conversation_sessions SET exam_track_id=:scope "
                            "WHERE session_id=:session_id AND learner_id=:learner_id"
                        ),
                        {
                            "scope": scope,
                            "session_id": session_id,
                            "learner_id": learner_id,
                        },
                    )
                else:
                    raise ValueError("conversation session belongs to another learner")
            next_sequence = int(connection.execute(
                text(
                    "SELECT COUNT(*) FROM conversation_messages "
                    "WHERE session_id=:session_id"
                ),
                {"session_id": session_id},
            ).scalar_one())
            for index, message in enumerate(sanitize_conversation_messages(messages)):
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
                metadata = {
                    key: value
                    for key, value in message.items()
                    if key not in {"message_id", "role", "content", "created_at"}
                }
                metadata["_conversation_sequence"] = next_sequence
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
                            metadata, ensure_ascii=False, default=str
                        ),
                    },
                )
                next_sequence += 1

    def get_latest_context_summary(
        self, session_id: str, learner_id: str
    ) -> dict[str, Any] | None:
        scope = current_exam_scope(learner_id)
        with self.engine.connect() as connection:
            owner = self._session_owner(connection, session_id, scope)
            if (
                owner is None
                or owner["learner_id"] != learner_id
                or not self._scope_matches(owner.get("exam_track_id"), scope)
            ):
                return None
            row = connection.execute(
                text(
                    "SELECT summary_id, execution_id, payload_json, created_at "
                    "FROM context_summaries "
                    "WHERE session_id=:session_id "
                    "ORDER BY created_at DESC, summary_id DESC LIMIT 1"
                ),
                {"session_id": session_id},
            ).mappings().first()
        if row is None:
            return None
        payload = row.get("payload_json")
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            try:
                summary = json.loads(payload)
            except json.JSONDecodeError:
                return None
        elif isinstance(payload, dict):
            summary = dict(payload)
        else:
            return None
        return {
            "summary_id": row["summary_id"],
            "execution_id": row["execution_id"],
            "created_at": row["created_at"],
            **summary,
        }

    def save_context_summary(
        self,
        session_id: str,
        learner_id: str,
        execution_id: str,
        summary: dict[str, Any],
    ) -> None:
        scope = current_exam_scope(learner_id)
        with self.engine.begin() as connection:
            owner = self._session_owner(connection, session_id, scope)
            if (
                owner is None
                or owner["learner_id"] != learner_id
                or not self._scope_matches(owner.get("exam_track_id"), scope)
            ):
                return
            summary_id = f"SUM_{uuid4().hex}"
            connection.execute(
                text(
                    "INSERT INTO context_summaries "
                    "(summary_id, session_id, execution_id, payload_json, created_at) "
                    "VALUES (:summary_id, :session_id, :execution_id, :payload_json, :created_at)"
                ),
                {
                    "summary_id": summary_id,
                    "session_id": session_id,
                    "execution_id": execution_id,
                    "payload_json": json.dumps(summary, ensure_ascii=False, default=str),
                    # Explicit microsecond timestamp: the column default is
                    # second-precision CURRENT_TIMESTAMP, so two summaries
                    # saved within the same second would tie and "latest" would
                    # fall back to the random summary_id ordering.
                    "created_at": datetime.utcnow().isoformat(timespec="microseconds"),
                },
            )

    @staticmethod
    def _scope_matches(stored_scope: str | None, requested_scope: str) -> bool:
        if requested_scope == LEGACY_EXAM_SCOPE:
            return True
        return str(stored_scope or "") == requested_scope

    @staticmethod
    def _session_owner(connection, session_id: str, scope: str):
        if scope == LEGACY_EXAM_SCOPE:
            owner = connection.execute(
                text(
                    "SELECT learner_id FROM conversation_sessions "
                    "WHERE session_id=:session_id"
                ),
                {"session_id": session_id},
            ).mappings().first()
            return ({**owner, "exam_track_id": None} if owner is not None else None)
        return connection.execute(
            text(
                "SELECT learner_id, exam_track_id FROM conversation_sessions "
                "WHERE session_id=:session_id"
            ),
            {"session_id": session_id},
        ).mappings().first()

    @staticmethod
    def _insert_session(
        connection, session_id: str, learner_id: str, title: str, scope: str, source: str = "user"
    ) -> None:
        if scope == LEGACY_EXAM_SCOPE:
            connection.execute(
                text(
                    "INSERT INTO conversation_sessions (session_id, learner_id, title, source) "
                    "VALUES (:session_id, :learner_id, :title, :source)"
                ),
                {"session_id": session_id, "learner_id": learner_id, "title": title, "source": source},
            )
            return
        connection.execute(
            text(
                "INSERT INTO conversation_sessions "
                "(session_id, learner_id, title, exam_track_id, source) "
                "VALUES (:session_id, :learner_id, :title, :exam_track_id, :source)"
            ),
            {
                "session_id": session_id,
                "learner_id": learner_id,
                "title": title,
                "exam_track_id": scope,
                "source": source,
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

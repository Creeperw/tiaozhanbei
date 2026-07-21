from __future__ import annotations

import json
from threading import RLock
from typing import Protocol
from uuid import uuid4

from sqlalchemy import Engine, text

from competition_app.contracts.learning_plan import LearningPlanResult


class LearningPlanRepository(Protocol):
    """Persistence boundary for the learner's current plan and immutable versions."""

    def get_current(self, learner_id: str) -> LearningPlanResult | None: ...

    def save_current(
        self,
        learner_id: str,
        value: LearningPlanResult,
        *,
        invalidated_layers: list[str] | None = None,
    ) -> None: ...


class InMemoryLearningPlanRepository:
    def __init__(self) -> None:
        self._current: dict[str, LearningPlanResult] = {}
        self._invalidation_events: list[dict[str, str]] = []
        self._lock = RLock()

    def get_current(self, learner_id: str) -> LearningPlanResult | None:
        with self._lock:
            value = self._current.get(learner_id)
            return value.model_copy(deep=True) if value is not None else None

    def save_current(
        self,
        learner_id: str,
        value: LearningPlanResult,
        *,
        invalidated_layers: list[str] | None = None,
    ) -> None:
        if not learner_id:
            raise ValueError("learner_id is required")
        self._validate_owner(learner_id, value)
        with self._lock:
            self._current[learner_id] = value.model_copy(deep=True)
            for layer in invalidated_layers or []:
                self._invalidation_events.append(
                    {
                        "event_id": f"PIE_{uuid4().hex}",
                        "learner_id": learner_id,
                        "layer": layer,
                    }
                )

    @staticmethod
    def _validate_owner(learner_id: str, value: LearningPlanResult) -> None:
        for item in (value.long_term_plan, value.short_term_plan, value.learning_task):
            if item is not None and item.learner_id != learner_id:
                raise ValueError("learning plan identity does not match repository learner")


class SqlLearningPlanRepository:
    """SQLAlchemy repository backed by the Phase 1 version and head tables."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get_current(self, learner_id: str) -> LearningPlanResult | None:
        with self.engine.connect() as connection:
            payload = connection.execute(
                text(
                    "SELECT payload_json FROM learner_plan_states "
                    "WHERE learner_id=:learner_id"
                ),
                {"learner_id": learner_id},
            ).scalar_one_or_none()
        if payload is None:
            return None
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            payload = json.loads(payload)
        return LearningPlanResult.model_validate(payload)

    def save_current(
        self,
        learner_id: str,
        value: LearningPlanResult,
        *,
        invalidated_layers: list[str] | None = None,
    ) -> None:
        if not learner_id:
            raise ValueError("learner_id is required")
        InMemoryLearningPlanRepository._validate_owner(learner_id, value)
        serialized = value.model_dump_json()
        with self.engine.begin() as connection:
            self._save_versions(connection, value)
            exists = connection.execute(
                text(
                    "SELECT learner_id FROM learner_plan_states "
                    "WHERE learner_id=:learner_id"
                ),
                {"learner_id": learner_id},
            ).first()
            if exists:
                connection.execute(
                    text(
                        "UPDATE learner_plan_states SET payload_json=:payload_json, "
                        "updated_at=CURRENT_TIMESTAMP WHERE learner_id=:learner_id"
                    ),
                    {"learner_id": learner_id, "payload_json": serialized},
                )
            else:
                connection.execute(
                    text(
                        "INSERT INTO learner_plan_states (learner_id, payload_json) "
                        "VALUES (:learner_id, :payload_json)"
                    ),
                    {"learner_id": learner_id, "payload_json": serialized},
                )
            for layer in invalidated_layers or []:
                connection.execute(
                    text(
                        "INSERT INTO plan_invalidation_events "
                        "(event_id, learner_id, invalidated_layer, reason) "
                        "VALUES (:event_id, :learner_id, :layer, :reason)"
                    ),
                    {
                        "event_id": f"PIE_{uuid4().hex}",
                        "learner_id": learner_id,
                        "layer": layer,
                        "reason": "parent_plan_updated",
                    },
                )

    @staticmethod
    def _save_versions(connection, value: LearningPlanResult) -> None:
        rows = (
            ("long_term_plan_versions", "plan_id", value.long_term_plan),
            ("short_term_plan_versions", "plan_id", value.short_term_plan),
            ("learning_task_versions", "task_id", value.learning_task),
        )
        for table, id_column, item in rows:
            if item is None:
                continue
            item_id = getattr(item, id_column)
            exists = connection.execute(
                text(
                    f"SELECT {id_column} FROM {table} "
                    f"WHERE {id_column}=:item_id AND version=:version"
                ),
                {"item_id": item_id, "version": item.version},
            ).first()
            if exists:
                continue
            connection.execute(
                text(
                    f"INSERT INTO {table} "
                    f"({id_column}, learner_id, version, status, payload_json) "
                    f"VALUES (:item_id, :learner_id, :version, :status, :payload_json)"
                ),
                {
                    "item_id": item_id,
                    "learner_id": item.learner_id,
                    "version": item.version,
                    "status": item.status,
                    "payload_json": item.model_dump_json(),
                },
            )

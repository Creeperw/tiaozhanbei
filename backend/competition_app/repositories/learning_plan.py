from __future__ import annotations

import json
import hashlib
from contextlib import contextmanager
from threading import RLock
from typing import Protocol
from uuid import uuid4

from sqlalchemy import Engine, text

from competition_app.contracts.learning_plan import LearningPlanResult
from competition_app.contracts.exam_scope import LEGACY_EXAM_SCOPE
from competition_app.exam_scope import current_exam_scope


class PlanWriteConflictError(RuntimeError):
    """Raised when a plan write was prepared from a stale learner-plan head."""


def plan_head_versions(value: LearningPlanResult | None) -> dict[str, dict[str, object] | None]:
    """Return the immutable identity/version tuple for every mutable plan layer."""

    heads: dict[str, dict[str, object] | None] = {}
    for layer, id_field in (
        ("long_term_plan", "plan_id"),
        ("short_term_plan", "plan_id"),
        ("learning_task", "task_id"),
    ):
        item = getattr(value, layer, None) if value is not None else None
        heads[layer] = (
            {
                "id": getattr(item, id_field),
                "version": int(item.version),
            }
            if item is not None
            else None
        )
    return heads


def _heads_match(
    current: LearningPlanResult | None,
    expected: dict[str, dict[str, object] | None],
) -> bool:
    current_heads = plan_head_versions(current)
    return all(current_heads.get(layer) == head for layer, head in expected.items())


class LearningPlanRepository(Protocol):
    """Persistence boundary for the learner's current plan and immutable versions."""

    def get_current(self, learner_id: str) -> LearningPlanResult | None: ...

    def mutation_lock(self, learner_id: str): ...

    def list_scopes(self) -> list[tuple[str, str]]: ...

    def save_current(
        self,
        learner_id: str,
        value: LearningPlanResult,
        *,
        invalidated_layers: list[str] | None = None,
        sync_event_type: str = "publish",
        expected_task_id: str | None = None,
        expected_task_version: int | None = None,
        expected_heads: dict[str, dict[str, object] | None] | None = None,
    ) -> bool: ...


class InMemoryLearningPlanRepository:
    def __init__(self) -> None:
        self._current: dict[tuple[str, str], LearningPlanResult] = {}
        self._invalidation_events: list[dict[str, str]] = []
        self._lock = RLock()

    def get_current(self, learner_id: str) -> LearningPlanResult | None:
        with self._lock:
            value = self._current.get((learner_id, current_exam_scope(learner_id)))
            return value.model_copy(deep=True) if value is not None else None

    def list_scopes(self) -> list[tuple[str, str]]:
        with self._lock:
            return sorted(self._current)

    @contextmanager
    def mutation_lock(self, learner_id: str):
        """Exclusive counterpart to ordinary snapshot reads."""

        with self._lock:
            yield

    def save_current(
        self,
        learner_id: str,
        value: LearningPlanResult,
        *,
        invalidated_layers: list[str] | None = None,
        sync_event_type: str = "publish",
        expected_task_id: str | None = None,
        expected_task_version: int | None = None,
        expected_heads: dict[str, dict[str, object] | None] | None = None,
    ) -> bool:
        if not learner_id:
            raise ValueError("learner_id is required")
        self._validate_owner(learner_id, value)
        scope = current_exam_scope(learner_id)
        key = (learner_id, scope)
        with self._lock:
            if expected_heads is not None and not _heads_match(
                self._current.get(key), expected_heads
            ):
                return False
            if expected_task_id is not None or expected_task_version is not None:
                current_task = self._current.get(key)
                current_task = current_task.learning_task if current_task else None
                if (
                    current_task is None
                    or current_task.task_id != expected_task_id
                    or current_task.version != expected_task_version
                ):
                    return False
            self._current[key] = value.model_copy(deep=True)
            for layer in invalidated_layers or []:
                self._invalidation_events.append(
                    {
                        "event_id": f"PIE_{uuid4().hex}",
                        "learner_id": learner_id,
                        "exam_track_id": scope,
                        "layer": layer,
                    }
                )
            return True

    @staticmethod
    def _validate_owner(learner_id: str, value: LearningPlanResult) -> None:
        for item in (value.long_term_plan, value.short_term_plan, value.learning_task):
            if item is not None and item.learner_id != learner_id:
                raise ValueError("learning plan identity does not match repository learner")


class SqlLearningPlanRepository:
    """SQLAlchemy repository backed by the Phase 1 version and head tables."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def infer_active_scope(self, learner_id: str) -> str | None:
        """Return the learner's most recently used exam scope, if any.

        Used by the request boundary to bind the exam workspace for pure read
        APIs (learning path, plan context) that never run a planning workflow.
        Falls back to None when the learner has no scoped plan rows yet.
        """

        with self.engine.begin() as connection:
            exam_track_id = connection.execute(
                text(
                    "SELECT exam_track_id FROM learner_exam_plan_states "
                    "WHERE learner_id=:learner_id "
                    "ORDER BY updated_at DESC LIMIT 1"
                ),
                {"learner_id": learner_id},
            ).scalar_one_or_none()
        if exam_track_id is None:
            return None
        return str(exam_track_id)

    def list_scopes(self) -> list[tuple[str, str]]:
        """List persisted learner/exam heads for background maintenance."""

        with self.engine.begin() as connection:
            rows = connection.execute(
                text(
                    "SELECT learner_id, exam_track_id "
                    "FROM learner_exam_plan_states "
                    "UNION ALL "
                    "SELECT learner_id, :legacy_scope AS exam_track_id "
                    "FROM learner_plan_states "
                    "ORDER BY learner_id, exam_track_id"
                ),
                {"legacy_scope": LEGACY_EXAM_SCOPE},
            ).all()
        return [(str(learner_id), str(scope)) for learner_id, scope in rows]

    def get_current(self, learner_id: str) -> LearningPlanResult | None:
        scope = current_exam_scope(learner_id)
        with self.engine.begin() as connection:
            if scope == LEGACY_EXAM_SCOPE:
                payload = connection.execute(
                    text(
                        "SELECT payload_json FROM learner_plan_states "
                        "WHERE learner_id=:learner_id"
                    ),
                    {"learner_id": learner_id},
                ).scalar_one_or_none()
            else:
                payload = connection.execute(
                    text(
                        "SELECT payload_json FROM learner_exam_plan_states "
                        "WHERE learner_id=:learner_id "
                        "AND exam_track_id=:exam_track_id"
                    ),
                    {"learner_id": learner_id, "exam_track_id": scope},
                ).scalar_one_or_none()
                if payload is None:
                    payload = self._migrate_matching_legacy_head(
                        connection, learner_id=learner_id, requested_scope=scope
                    )
        if payload is None:
            return None
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            payload = json.loads(payload)
        return LearningPlanResult.model_validate(payload)

    @contextmanager
    def mutation_lock(self, learner_id: str):
        """Cross-process advisory X-lock for one learner's plan hierarchy."""

        if self.engine.dialect.name != "mysql":
            # SQLite test/runtime writes are already serialized by the engine;
            # expected_heads below still provides stale-snapshot protection.
            yield
            return
        lock_identity = f"{learner_id}:{current_exam_scope(learner_id)}"
        digest = hashlib.sha256(lock_identity.encode("utf-8")).hexdigest()[:40]
        lock_name = f"competition:plan:{digest}"
        with self.engine.connect() as connection:
            acquired = connection.execute(
                text("SELECT GET_LOCK(:lock_name, 0)"),
                {"lock_name": lock_name},
            ).scalar_one()
            if acquired != 1:
                raise PlanWriteConflictError(
                    "另一个会话正在修改当前学习计划；本次未执行并发覆盖。"
                )
            try:
                yield
            finally:
                connection.execute(
                    text("SELECT RELEASE_LOCK(:lock_name)"),
                    {"lock_name": lock_name},
                )

    def save_current(
        self,
        learner_id: str,
        value: LearningPlanResult,
        *,
        invalidated_layers: list[str] | None = None,
        sync_event_type: str = "publish",
        expected_task_id: str | None = None,
        expected_task_version: int | None = None,
        expected_heads: dict[str, dict[str, object] | None] | None = None,
    ) -> bool:
        if not learner_id:
            raise ValueError("learner_id is required")
        if sync_event_type not in {"publish", "replace"}:
            raise ValueError("sync_event_type must be publish or replace")
        InMemoryLearningPlanRepository._validate_owner(learner_id, value)
        serialized = value.model_dump_json()
        scope = current_exam_scope(learner_id)
        scoped = scope != LEGACY_EXAM_SCOPE
        with self.engine.begin() as connection:
            if scoped:
                head_query = (
                    "SELECT payload_json FROM learner_exam_plan_states "
                    "WHERE learner_id=:learner_id AND exam_track_id=:exam_track_id"
                )
                head_params = {"learner_id": learner_id, "exam_track_id": scope}
            else:
                head_query = (
                    "SELECT payload_json FROM learner_plan_states "
                    "WHERE learner_id=:learner_id"
                )
                head_params = {"learner_id": learner_id}
            if self.engine.dialect.name == "mysql":
                # InnoDB row-level X lock: readers can keep using their MVCC
                # snapshot, while competing publishers serialize here.
                head_query += " FOR UPDATE"
            current_payload = connection.execute(
                text(head_query), head_params
            ).scalar_one_or_none()
            if isinstance(current_payload, (bytes, bytearray)):
                current_payload = current_payload.decode("utf-8")
            if isinstance(current_payload, str):
                current_payload = json.loads(current_payload)
            current = (
                LearningPlanResult.model_validate(current_payload)
                if current_payload
                else None
            )
            if expected_heads is not None and not _heads_match(current, expected_heads):
                return False
            if expected_task_id is not None or expected_task_version is not None:
                if not expected_task_id or expected_task_version is None:
                    raise ValueError("refresh CAS requires task ID and version")
                if not self._claim_refresh(
                    connection,
                    learner_id,
                    expected_task_id,
                    expected_task_version,
                    value,
                ):
                    return False
            task_version_created = self._save_versions(connection, value)
            if scoped and current_payload is not None:
                connection.execute(
                    text(
                        "UPDATE learner_exam_plan_states "
                        "SET payload_json=:payload_json, migrated_from_legacy=FALSE, "
                        "updated_at=CURRENT_TIMESTAMP WHERE learner_id=:learner_id "
                        "AND exam_track_id=:exam_track_id"
                    ),
                    {
                        "learner_id": learner_id,
                        "exam_track_id": scope,
                        "payload_json": serialized,
                    },
                )
            elif scoped:
                connection.execute(
                    text(
                        "INSERT INTO learner_exam_plan_states "
                        "(learner_id, exam_track_id, payload_json, migrated_from_legacy) "
                        "VALUES (:learner_id, :exam_track_id, :payload_json, FALSE)"
                    ),
                    {
                        "learner_id": learner_id,
                        "exam_track_id": scope,
                        "payload_json": serialized,
                    },
                )
            elif current_payload is not None:
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
            task = value.learning_task
            if task_version_created and task is not None:
                connection.execute(
                    text(
                        "INSERT INTO learning_task_sync_outbox "
                        "(event_id, learner_id, task_id, task_version, event_type, payload_json) "
                        "VALUES (:event_id, :learner_id, :task_id, :task_version, "
                        ":event_type, :payload_json)"
                    ),
                    {
                        "event_id": f"LTSO_{uuid4().hex}",
                        "learner_id": learner_id,
                        "task_id": task.task_id,
                        "task_version": task.version,
                        "event_type": sync_event_type,
                        "payload_json": task.model_dump_json(),
                    },
                )
        return True

    def _migrate_matching_legacy_head(
        self,
        connection,
        *,
        learner_id: str,
        requested_scope: str,
    ):
        """Quarantine legacy singleton state unless its exam is unambiguous.

        A target switch may already have happened before this migration.  The
        active target therefore cannot be used to label the old plan.  Infer
        only from the persisted plan itself; ambiguous state stays in the old
        table and is deliberately invisible to every scoped workspace.
        """

        legacy_payload = connection.execute(
            text(
                "SELECT payload_json FROM learner_plan_states "
                "WHERE learner_id=:learner_id"
            ),
            {"learner_id": learner_id},
        ).scalar_one_or_none()
        if legacy_payload is None:
            return None
        parsed = self._decode_payload(legacy_payload)
        inferred_scope = self._infer_exam_track_id(parsed)
        if inferred_scope is None:
            return None
        serialized = json.dumps(parsed, ensure_ascii=False)
        prefix = (
            "INSERT OR IGNORE"
            if self.engine.dialect.name == "sqlite"
            else "INSERT IGNORE"
        )
        connection.execute(
            text(
                f"{prefix} INTO learner_exam_plan_states "
                "(learner_id, exam_track_id, payload_json, migrated_from_legacy) "
                "VALUES (:learner_id, :exam_track_id, :payload_json, TRUE)"
            ),
            {
                "learner_id": learner_id,
                "exam_track_id": inferred_scope,
                "payload_json": serialized,
            },
        )
        return legacy_payload if inferred_scope == requested_scope else None

    @staticmethod
    def _decode_payload(payload):
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        return json.loads(payload) if isinstance(payload, str) else payload

    @staticmethod
    def _infer_exam_track_id(payload: object) -> str | None:
        try:
            text_value = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            return None
        mappings = (
            ("中西医结合执业助理", "EXAM_2025_INTEGRATED_ASSISTANT"),
            ("中西医结合执业医师", "EXAM_2025_INTEGRATED_PHYSICIAN"),
            ("中医执业助理", "EXAM_2025_TCM_ASSISTANT"),
            ("中医执业医师", "EXAM_2025_TCM_PHYSICIAN"),
            ("执业药师", "EXAM_TCM_LICENSED_PHARMACIST"),
        )
        matches = {track for marker, track in mappings if marker in text_value}
        return next(iter(matches)) if len(matches) == 1 else None

    def _claim_refresh(
        self,
        connection,
        learner_id: str,
        expected_task_id: str,
        expected_task_version: int,
        value: LearningPlanResult,
    ) -> bool:
        replacement = value.learning_task
        if replacement is None:
            raise ValueError("refresh CAS requires a replacement learning task")
        values = {
            "learner_id": learner_id,
            "prior_task_id": expected_task_id,
            "prior_task_version": expected_task_version,
            "replacement_task_id": replacement.task_id,
            "replacement_task_version": replacement.version,
        }
        prefix = "INSERT OR IGNORE" if self.engine.dialect.name == "sqlite" else "INSERT IGNORE"
        result = connection.execute(
            text(
                f"{prefix} INTO learning_task_refresh_claims "
                "(learner_id, prior_task_id, prior_task_version, replacement_task_id, "
                "replacement_task_version) VALUES (:learner_id, :prior_task_id, "
                ":prior_task_version, :replacement_task_id, :replacement_task_version)"
            ),
            values,
        )
        return result.rowcount == 1

    @staticmethod
    def _save_versions(connection, value: LearningPlanResult) -> bool:
        task_version_created = False
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
            if table == "learning_task_versions":
                task_version_created = True
        return task_version_created

from __future__ import annotations

import json
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from sqlalchemy import Engine, text

from competition_app.contracts.resource import ResourceVersion
from competition_app.contracts.review import (
    ReviewAttempt,
    ReviewMemoryUnit,
    ReviewResourceBinding,
    ReviewSchedule,
    ReviewTask,
)


@dataclass(frozen=True)
class ReviewDelivery:
    task: ReviewTask
    resource: dict | None = None


class ReviewRepository(Protocol):
    def get_memory_unit(self, learner_id: str, kp_id: str) -> ReviewMemoryUnit | None: ...

    def list_memory_units(self, learner_id: str) -> list[ReviewMemoryUnit]: ...

    def save_memory_unit(self, unit: ReviewMemoryUnit) -> None: ...

    def get_attempt(self, attempt_id: str) -> ReviewAttempt | None: ...

    def list_recent_attempts(
        self, learner_id: str, kp_id: str, limit: int = 5
    ) -> list[ReviewAttempt]: ...

    def get_task(self, review_task_id: str) -> ReviewTask | None: ...

    def list_active_deliveries(self, learner_id: str) -> list[ReviewDelivery]: ...

    def record_delivery(
        self,
        schedule: ReviewSchedule,
        task: ReviewTask,
        resource: ResourceVersion,
        binding: ReviewResourceBinding,
    ) -> None: ...

    def apply_attempt(
        self,
        *,
        previous_version: int,
        unit: ReviewMemoryUnit,
        attempt: ReviewAttempt,
        task: ReviewTask,
    ) -> None: ...


class InMemoryReviewRepository:
    def __init__(self) -> None:
        self._units: dict[tuple[str, str], ReviewMemoryUnit] = {}
        self._attempts: dict[str, ReviewAttempt] = {}
        self._tasks: dict[str, ReviewTask] = {}
        self._resources: dict[str, dict] = {}
        self._schedules: dict[str, ReviewSchedule] = {}
        self._lock = RLock()

    def get_memory_unit(self, learner_id: str, kp_id: str) -> ReviewMemoryUnit | None:
        with self._lock:
            return self._units.get((learner_id, kp_id))

    def list_memory_units(self, learner_id: str) -> list[ReviewMemoryUnit]:
        with self._lock:
            return [
                item for (owner, _), item in self._units.items() if owner == learner_id
            ]

    def save_memory_unit(self, unit: ReviewMemoryUnit) -> None:
        with self._lock:
            self._units[(unit.learner_id, unit.kp_id)] = unit

    def get_attempt(self, attempt_id: str) -> ReviewAttempt | None:
        with self._lock:
            return self._attempts.get(attempt_id)

    def list_recent_attempts(
        self, learner_id: str, kp_id: str, limit: int = 5
    ) -> list[ReviewAttempt]:
        with self._lock:
            values = [
                item
                for item in self._attempts.values()
                if item.learner_id == learner_id and item.kp_id == kp_id
            ]
        return sorted(values, key=lambda item: item.answered_at, reverse=True)[:limit]

    def get_task(self, review_task_id: str) -> ReviewTask | None:
        with self._lock:
            return self._tasks.get(review_task_id)

    def list_active_deliveries(self, learner_id: str) -> list[ReviewDelivery]:
        with self._lock:
            tasks = [
                item
                for item in self._tasks.values()
                if item.learner_id == learner_id
                and item.status in {"pending", "bound", "overdue"}
            ]
            return [
                ReviewDelivery(task=task, resource=self._resources.get(task.review_task_id))
                for task in tasks
            ]

    def record_delivery(
        self,
        schedule: ReviewSchedule,
        task: ReviewTask,
        resource: ResourceVersion,
        binding: ReviewResourceBinding,
    ) -> None:
        with self._lock:
            self._schedules[schedule.schedule_id] = schedule
            self._tasks[task.review_task_id] = task
            self._resources[task.review_task_id] = resource.model_dump(mode="json")

    def apply_attempt(
        self,
        *,
        previous_version: int,
        unit: ReviewMemoryUnit,
        attempt: ReviewAttempt,
        task: ReviewTask,
    ) -> None:
        with self._lock:
            if attempt.attempt_id in self._attempts:
                return
            current = self._units.get((unit.learner_id, unit.kp_id))
            if current is None or current.version != previous_version:
                raise RuntimeError("review memory unit changed while applying feedback")
            self._units[(unit.learner_id, unit.kp_id)] = unit
            self._attempts[attempt.attempt_id] = attempt
            self._tasks[task.review_task_id] = task


class SqlReviewRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @staticmethod
    def _json(value):
        return json.loads(value) if isinstance(value, str) else value

    def get_memory_unit(self, learner_id: str, kp_id: str) -> ReviewMemoryUnit | None:
        with self.engine.connect() as connection:
            payload = connection.execute(
                text(
                    "SELECT payload_json FROM review_memory_units "
                    "WHERE learner_id=:learner_id AND kp_id=:kp_id"
                ),
                {"learner_id": learner_id, "kp_id": kp_id},
            ).scalar_one_or_none()
        return ReviewMemoryUnit.model_validate(self._json(payload)) if payload else None

    def list_memory_units(self, learner_id: str) -> list[ReviewMemoryUnit]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT payload_json FROM review_memory_units "
                    "WHERE learner_id=:learner_id"
                ),
                {"learner_id": learner_id},
            ).scalars()
            return [ReviewMemoryUnit.model_validate(self._json(row)) for row in rows]

    def save_memory_unit(self, unit: ReviewMemoryUnit) -> None:
        payload = json.dumps(unit.model_dump(mode="json"), ensure_ascii=False)
        with self.engine.begin() as connection:
            existing = connection.execute(
                text(
                    "SELECT memory_unit_id FROM review_memory_units "
                    "WHERE learner_id=:learner_id AND kp_id=:kp_id"
                ),
                {"learner_id": unit.learner_id, "kp_id": unit.kp_id},
            ).first()
            values = {
                "memory_unit_id": unit.memory_unit_id,
                "learner_id": unit.learner_id,
                "kp_id": unit.kp_id,
                "next_review_at": unit.next_review_at,
                "version": unit.version,
                "payload_json": payload,
            }
            if existing:
                connection.execute(
                    text(
                        "UPDATE review_memory_units SET next_review_at=:next_review_at, "
                        "version=:version, payload_json=:payload_json, updated_at=CURRENT_TIMESTAMP "
                        "WHERE learner_id=:learner_id AND kp_id=:kp_id"
                    ),
                    values,
                )
            else:
                connection.execute(
                    text(
                        "INSERT INTO review_memory_units "
                        "(memory_unit_id, learner_id, kp_id, next_review_at, version, payload_json) "
                        "VALUES (:memory_unit_id, :learner_id, :kp_id, :next_review_at, "
                        ":version, :payload_json)"
                    ),
                    values,
                )

    def get_attempt(self, attempt_id: str) -> ReviewAttempt | None:
        with self.engine.connect() as connection:
            payload = connection.execute(
                text(
                    "SELECT payload_json FROM review_attempts WHERE attempt_id=:attempt_id"
                ),
                {"attempt_id": attempt_id},
            ).scalar_one_or_none()
        return ReviewAttempt.model_validate(self._json(payload)) if payload else None

    def list_recent_attempts(
        self, learner_id: str, kp_id: str, limit: int = 5
    ) -> list[ReviewAttempt]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT payload_json FROM review_attempts "
                    "WHERE learner_id=:learner_id AND kp_id=:kp_id "
                    "ORDER BY answered_at DESC LIMIT :limit"
                ),
                {"learner_id": learner_id, "kp_id": kp_id, "limit": limit},
            ).scalars()
            return [ReviewAttempt.model_validate(self._json(row)) for row in rows]

    def get_task(self, review_task_id: str) -> ReviewTask | None:
        with self.engine.connect() as connection:
            payload = connection.execute(
                text(
                    "SELECT payload_json FROM review_tasks "
                    "WHERE review_task_id=:review_task_id"
                ),
                {"review_task_id": review_task_id},
            ).scalar_one_or_none()
        return ReviewTask.model_validate(self._json(payload)) if payload else None

    def list_active_deliveries(self, learner_id: str) -> list[ReviewDelivery]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT t.payload_json, r.payload_json AS resource_json "
                    "FROM review_tasks t "
                    "LEFT JOIN review_resource_bindings b "
                    "ON b.review_task_id=t.review_task_id "
                    "LEFT JOIN resource_versions r ON r.resource_id=b.resource_id "
                    "AND r.version=b.resource_version "
                    "WHERE t.learner_id=:learner_id "
                    "AND t.status IN ('pending', 'bound', 'overdue')"
                ),
                {"learner_id": learner_id},
            ).mappings()
            return [
                ReviewDelivery(
                    task=ReviewTask.model_validate(self._json(row["payload_json"])),
                    resource=(
                        self._json(row["resource_json"])
                        if row["resource_json"] is not None
                        else None
                    ),
                )
                for row in rows
            ]

    def record_delivery(
        self,
        schedule: ReviewSchedule,
        task: ReviewTask,
        resource: ResourceVersion,
        binding: ReviewResourceBinding,
    ) -> None:
        with self.engine.begin() as connection:
            existing = connection.execute(
                text(
                    "SELECT schedule_id FROM review_schedules WHERE schedule_id=:schedule_id"
                ),
                {"schedule_id": schedule.schedule_id},
            ).first()
            if not existing:
                connection.execute(
                    text(
                        "INSERT INTO review_schedules "
                        "(schedule_id, learner_id, calculated_at, payload_json) "
                        "VALUES (:schedule_id, :learner_id, :calculated_at, :payload_json)"
                    ),
                    {
                        "schedule_id": schedule.schedule_id,
                        "learner_id": schedule.learner_id,
                        "calculated_at": schedule.calculated_at,
                        "payload_json": json.dumps(
                            schedule.model_dump(mode="json"), ensure_ascii=False
                        ),
                    },
                )

    def apply_attempt(
        self,
        *,
        previous_version: int,
        unit: ReviewMemoryUnit,
        attempt: ReviewAttempt,
        task: ReviewTask,
    ) -> None:
        with self.engine.begin() as connection:
            if connection.execute(
                text("SELECT attempt_id FROM review_attempts WHERE attempt_id=:attempt_id"),
                {"attempt_id": attempt.attempt_id},
            ).first():
                return
            update = connection.execute(
                text(
                    "UPDATE review_memory_units SET next_review_at=:next_review_at, "
                    "version=:new_version, payload_json=:payload_json, "
                    "updated_at=CURRENT_TIMESTAMP WHERE learner_id=:learner_id "
                    "AND kp_id=:kp_id AND version=:previous_version"
                ),
                {
                    "next_review_at": unit.next_review_at,
                    "new_version": unit.version,
                    "payload_json": json.dumps(
                        unit.model_dump(mode="json"), ensure_ascii=False
                    ),
                    "learner_id": unit.learner_id,
                    "kp_id": unit.kp_id,
                    "previous_version": previous_version,
                },
            )
            if update.rowcount != 1:
                raise RuntimeError("review memory unit changed while applying feedback")
            attempt_payload = json.dumps(
                attempt.model_dump(mode="json"), ensure_ascii=False
            )
            connection.execute(
                text(
                    "INSERT INTO review_attempts "
                    "(attempt_id, review_task_id, learner_id, kp_id, outcome, "
                    "answered_at, payload_json) VALUES (:attempt_id, :review_task_id, "
                    ":learner_id, :kp_id, :outcome, :answered_at, :payload_json)"
                ),
                {
                    "attempt_id": attempt.attempt_id,
                    "review_task_id": attempt.review_task_id,
                    "learner_id": attempt.learner_id,
                    "kp_id": attempt.kp_id,
                    "outcome": attempt.outcome,
                    "answered_at": attempt.answered_at,
                    "payload_json": attempt_payload,
                },
            )
            task_payload = json.dumps(task.model_dump(mode="json"), ensure_ascii=False)
            connection.execute(
                text(
                    "UPDATE review_tasks SET status=:status, payload_json=:payload_json "
                    "WHERE review_task_id=:review_task_id"
                ),
                {
                    "status": task.status,
                    "payload_json": task_payload,
                    "review_task_id": task.review_task_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO review_state_events "
                    "(event_id, learner_id, kp_id, event_type, payload_json) "
                    "VALUES (:event_id, :learner_id, :kp_id, 'attempt_applied', :payload_json)"
                ),
                {
                    "event_id": attempt.attempt_id,
                    "learner_id": attempt.learner_id,
                    "kp_id": attempt.kp_id,
                    "payload_json": attempt_payload,
                },
            )

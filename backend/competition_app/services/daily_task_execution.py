from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, text

from competition_app.repositories.learning_plan import LearningPlanRepository


@dataclass
class DailyTaskExecutionCoordinator:
    engine: Engine | None
    plan_repository: LearningPlanRepository
    backend_handoff_runtime: Any | None = None

    def dispatch_pending(self, learner_id: str, limit: int = 20) -> int:
        if self.engine is None or self.backend_handoff_runtime is None:
            return 0
        if limit <= 0:
            return 0
        dispatched = 0
        with self.engine.begin() as connection:
            rows = connection.execute(
                text(
                    "SELECT event_id, task_id, task_version, event_type, payload_json, status, attempt_count, last_error "
                    "FROM learning_task_sync_outbox "
                    "WHERE learner_id=:learner_id AND status IN ('pending', 'failed') "
                    "ORDER BY created_at ASC LIMIT :limit"
                ),
                {"learner_id": learner_id, "limit": limit},
            ).mappings().all()
            for row in rows:
                try:
                    payload = row["payload_json"]
                    if isinstance(payload, (bytes, bytearray)):
                        payload = payload.decode("utf-8")
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    if not isinstance(payload, dict):
                        raise ValueError("outbox payload must be an object")
                    self.backend_handoff_runtime.upsert_daily_task_execution(learner_id, payload)
                    connection.execute(
                        text(
                            "UPDATE learning_task_sync_outbox SET status='delivered', attempt_count=attempt_count+1, "
                            "last_error=NULL, delivered_at=CURRENT_TIMESTAMP WHERE event_id=:event_id"
                        ),
                        {"event_id": row["event_id"]},
                    )
                    dispatched += 1
                except Exception as exc:
                    message = self._sanitize_error(exc)
                    connection.execute(
                        text(
                            "UPDATE learning_task_sync_outbox SET status='pending', attempt_count=attempt_count+1, "
                            "last_error=:last_error WHERE event_id=:event_id"
                        ),
                        {
                            "event_id": row["event_id"],
                            "last_error": message,
                        },
                    )
        return dispatched

    def load_current_progress(self, learner_id: str) -> dict[str, Any]:
        plans = self.plan_repository.get_current(learner_id)
        if plans is None or plans.learning_task is None:
            return {"status": "missing", "completed_items": 0, "total_items": 0, "items": []}
        task = plans.learning_task
        if self.backend_handoff_runtime is None:
            return {
                "host_task_id": task.task_id,
                "host_task_version": task.version,
                "status": "in_progress",
                "completed_items": 0,
                "total_items": len(task.items),
                "items": [],
            }
        progress = self.backend_handoff_runtime.load_daily_task_progress(
            learner_id,
            self._progress_request(task),
        )
        return progress if isinstance(progress, dict) else {}

    def ensure_current_snapshot(self, learner_id: str) -> bool:
        """Idempotently repair a missing delivered snapshot for the current version."""

        if self.backend_handoff_runtime is None:
            return False
        plans = self.plan_repository.get_current(learner_id)
        if plans is None or plans.learning_task is None:
            return False
        task = plans.learning_task
        payload = task.model_dump(mode="json")
        self.backend_handoff_runtime.upsert_daily_task_execution(learner_id, payload)
        return True

    def reconcile_parent_status(self, learner_id: str) -> bool:
        if self.backend_handoff_runtime is None:
            return False
        plans = self.plan_repository.get_current(learner_id)
        if plans is None or plans.learning_task is None:
            return False
        task = plans.learning_task
        if task.status == "completed":
            return True

        progress = self.load_current_progress(learner_id)
        if not isinstance(progress, dict):
            return False
        expected_item_ids = {item.task_item_id for item in task.items}
        completed_item_ids = {
            str(item.get("task_item_id") or "")
            for item in progress.get("items") or []
            if isinstance(item, dict)
            and str(item.get("status") or "").lower() == "completed"
        }
        if (
            not expected_item_ids
            or str(progress.get("status") or "").lower() != "completed"
            or completed_item_ids != expected_item_ids
        ):
            return False
        now = datetime.now(timezone.utc)
        completed_task = task.model_copy(
            update={
                "status": "completed",
                "version": task.version + 1,
                "updated_at": now,
            }
        )
        saved = self.plan_repository.save_current(
            learner_id,
            plans.model_copy(update={"learning_task": completed_task}),
            expected_task_id=task.task_id,
            expected_task_version=task.version,
        )
        if saved:
            return True
        current = self.plan_repository.get_current(learner_id)
        return bool(
            current is not None
            and current.learning_task is not None
            and current.learning_task.status == "completed"
        )

    @staticmethod
    def _progress_request(task: Any) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "host_task_id": task.task_id,
            "host_task_version": task.version,
            "items": [
                {
                    "task_item_id": item.task_item_id,
                    "item_type": item.item_type,
                    "kp_id": item.kp_id,
                    "required_question_count": item.required_question_count,
                }
                for item in task.items
            ],
        }

    @staticmethod
    def _sanitize_error(exc: Exception) -> str:
        message = str(exc).strip()
        if not message:
            return "handoff_unavailable"
        message = re.sub(r"\s+", " ", message)
        message = re.sub(
            r"(?i)(password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+",
            r"\1=[REDACTED]",
            message,
        )
        message = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", message)
        return message[:180]

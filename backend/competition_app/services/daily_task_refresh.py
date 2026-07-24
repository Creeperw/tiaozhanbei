from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

from competition_app.contracts.learning_plan import LearningTask
from competition_app.repositories.learning_plan import LearningPlanRepository


DAILY_TASK_REFRESH_INTERVAL = timedelta(hours=24)


class DailyTaskRefreshService:
    """Keep the current learning task on a server-owned rolling 24-hour window."""

    def __init__(self, repository: LearningPlanRepository) -> None:
        self.repository = repository
        self._lock = RLock()

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def ensure_current(
        self,
        learner_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current_time = self._utc(now or datetime.now(timezone.utc))
        with self._lock:
            plans = self.repository.get_current(learner_id)
            if plans is None or plans.learning_task is None:
                return self._result(None, current_time, available=False)

            task = plans.learning_task
            if task.refresh_started_at is None or task.refresh_due_at is None:
                # Legacy tasks start their first full window when the upgraded
                # service sees them, so deployment never discards user work.
                task = task.model_copy(
                    update={
                        "refresh_started_at": current_time,
                        "refresh_due_at": current_time + DAILY_TASK_REFRESH_INTERVAL,
                    }
                )
                plans = plans.model_copy(update={"learning_task": task})
                self.repository.save_current(learner_id, plans)

            due_at = self._utc(task.refresh_due_at)
            if current_time < due_at:
                return self._result(task, current_time)

            if plans.short_term_plan is None:
                return self._result(
                    task,
                    current_time,
                    available=False,
                    reason="short_term_plan_required",
                )

            next_task = self._next_task(task, plans.short_term_plan, current_time)
            self.repository.save_current(
                learner_id,
                plans.model_copy(update={"learning_task": next_task}),
            )
            return self._result(
                next_task,
                current_time,
                refreshed=True,
                previous_task_id=task.task_id,
                reason="refresh_due",
            )

    @staticmethod
    def _block_values(block: Any) -> tuple[str, int | None]:
        if isinstance(block, str):
            return block.strip(), None
        if isinstance(block, dict):
            return str(block.get("content") or "").strip(), block.get("estimated_minutes")
        return str(getattr(block, "content", "") or "").strip(), getattr(
            block, "estimated_minutes", None
        )

    def _next_task(self, task: LearningTask, short_plan: Any, now: datetime) -> LearningTask:
        package = short_plan.short_term_learning_package
        blocks = list(package.task_blocks) if package is not None else []
        usable = [self._block_values(block) for block in blocks]
        usable = [(content, minutes) for content, minutes in usable if content]

        if usable:
            current_index = next(
                (index for index, (content, _) in enumerate(usable) if content == task.task_content),
                -1,
            )
            content, minutes = usable[(current_index + 1) % len(usable)]
            if len(usable) == 1 and content == task.task_content:
                content = f"复盘并巩固：{content}"
            expected_output = package.expected_output
            completion_criteria = package.completion_criteria
        else:
            content = f"复盘并巩固：{task.task_content}"
            minutes = None
            expected_output = task.expected_output
            completion_criteria = task.completion_criteria

        return LearningTask(
            task_id=f"TASK_{uuid4().hex}",
            learner_id=task.learner_id,
            short_term_plan_id=task.short_term_plan_id,
            task_type=task.task_type,
            task_content=content,
            learning_chapter=task.learning_chapter,
            focus_knowledge_points=list(task.focus_knowledge_points),
            estimated_minutes=int(minutes or task.estimated_minutes),
            expected_output=expected_output,
            completion_criteria=completion_criteria,
            version=task.version + 1,
            status="pending",
            created_at=now,
            updated_at=now,
            refresh_started_at=now,
            refresh_due_at=now + DAILY_TASK_REFRESH_INTERVAL,
        )

    def _result(
        self,
        task: LearningTask | None,
        now: datetime,
        *,
        available: bool = True,
        refreshed: bool = False,
        previous_task_id: str | None = None,
        reason: str = "active",
    ) -> dict[str, Any]:
        due_at = self._utc(task.refresh_due_at) if task and task.refresh_due_at else None
        remaining = max(0, int((due_at - now).total_seconds())) if due_at else 0
        return {
            "schema_version": "1.0",
            "policy": "rolling_24h",
            "interval_hours": 24,
            "auto_refresh_enabled": True,
            "available": available and task is not None and due_at is not None,
            "state": "active" if due_at and now < due_at else "due" if due_at else "unavailable",
            "server_time": now.isoformat(),
            "refresh_started_at": (
                self._utc(task.refresh_started_at).isoformat()
                if task and task.refresh_started_at
                else None
            ),
            "refresh_due_at": due_at.isoformat() if due_at else None,
            "remaining_seconds": remaining,
            "refreshed": refreshed,
            "previous_task_id": previous_task_id,
            "current_task_id": task.task_id if task else None,
            "reason": reason,
        }

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from competition_app.contracts.learning_plan import LearningTask
from competition_app.repositories.learning_plan import LearningPlanRepository
from competition_app.services.learning_plan import materialize_daily_task_items
from competition_app.services.learning_plan import (
    KnowledgePointResolver,
    VideoResourceResolver,
)


DAILY_TASK_REFRESH_INTERVAL = timedelta(hours=24)


class DailyTaskRefreshService:
    """Keep the current learning task on a server-owned rolling 24-hour window."""

    def __init__(
        self,
        repository: LearningPlanRepository,
        knowledge_point_resolver: KnowledgePointResolver | None = None,
        video_resource_resolver: VideoResourceResolver | None = None,
        task_load_policy_loader: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.repository = repository
        self.knowledge_point_resolver = knowledge_point_resolver
        self.video_resource_resolver = video_resource_resolver
        self.task_load_policy_loader = task_load_policy_loader
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

            task_load_policy: dict[str, Any] = {}
            if self.task_load_policy_loader is not None:
                try:
                    loaded_policy = self.task_load_policy_loader(
                        learner_id,
                        plan_context={
                            "long_term_plan": (
                                plans.long_term_plan.model_dump(mode="json")
                                if plans.long_term_plan is not None
                                else None
                            ),
                            "short_term_plan": plans.short_term_plan.model_dump(
                                mode="json"
                            ),
                            "learning_task": task.model_dump(mode="json"),
                        },
                    )
                    if isinstance(loaded_policy, dict):
                        task_load_policy = loaded_policy
                except Exception:
                    # A telemetry outage must not block the 24-hour refresh.
                    task_load_policy = {}
            next_task = self._next_task(
                task,
                plans.short_term_plan,
                current_time,
                recommended_minutes=task_load_policy.get("recommended_minutes"),
            )
            saved = self.repository.save_current(
                learner_id,
                plans.model_copy(update={"learning_task": next_task}),
                sync_event_type="replace",
                expected_task_id=task.task_id,
                expected_task_version=task.version,
            )
            if not saved:
                winner = self.repository.get_current(learner_id)
                winning_task = winner.learning_task if winner is not None else None
                return self._result(
                    winning_task,
                    current_time,
                    reason="concurrent_refresh",
                )
            return self._result(
                next_task,
                current_time,
                refreshed=True,
                previous_task_id=task.task_id,
                reason="refresh_due",
                task_load_policy=task_load_policy,
            )

    @staticmethod
    def _block_values(block: Any) -> tuple[str, int | None, Any]:
        if isinstance(block, str):
            return block.strip(), None, block
        if isinstance(block, dict):
            return (
                str(block.get("content") or "").strip(),
                block.get("estimated_minutes"),
                block,
            )
        return (
            str(getattr(block, "content", "") or "").strip(),
            getattr(block, "estimated_minutes", None),
            block,
        )

    def _next_task(
        self,
        task: LearningTask,
        short_plan: Any,
        now: datetime,
        *,
        recommended_minutes: Any = None,
    ) -> LearningTask:
        package = short_plan.short_term_learning_package
        blocks = list(package.task_blocks) if package is not None else []
        usable = [self._block_values(block) for block in blocks]
        usable = [
            (content, minutes, block)
            for content, minutes, block in usable
            if content
        ]

        if usable:
            current_index = next(
                (
                    index
                    for index, (content, _, _) in enumerate(usable)
                    if content == task.task_content
                ),
                -1,
            )
            content, minutes, selected_block = usable[(current_index + 1) % len(usable)]
            if len(usable) == 1 and content == task.task_content:
                content = f"复盘并巩固：{content}"
            expected_output = package.expected_output
            completion_criteria = package.completion_criteria
        else:
            content = f"复盘并巩固：{task.task_content}"
            minutes = None
            selected_block = None
            expected_output = task.expected_output
            completion_criteria = task.completion_criteria

        target_minutes = (
            int(recommended_minutes)
            if isinstance(recommended_minutes, (int, float))
            and not isinstance(recommended_minutes, bool)
            and int(recommended_minutes) > 0
            else int(minutes or task.estimated_minutes)
        )
        target_minutes = max(10, min(24 * 60, target_minutes))
        try:
            items = materialize_daily_task_items(
                task_content=content,
                learning_chapter=task.learning_chapter,
                estimated_minutes=target_minutes,
                focus_knowledge_points=list(task.focus_knowledge_points),
                task_blocks=[selected_block] if selected_block is not None else [],
                knowledge_point_resolver=self.knowledge_point_resolver,
                video_resource_resolver=self.video_resource_resolver,
            )
        except ValueError:
            # Preserve the last executable budget if an unusually dense legacy
            # task cannot fit into the reduced recommendation.
            target_minutes = max(target_minutes, int(minutes or task.estimated_minutes))
            items = materialize_daily_task_items(
                task_content=content,
                learning_chapter=task.learning_chapter,
                estimated_minutes=target_minutes,
                focus_knowledge_points=list(task.focus_knowledge_points),
                task_blocks=[selected_block] if selected_block is not None else [],
                knowledge_point_resolver=self.knowledge_point_resolver,
                video_resource_resolver=self.video_resource_resolver,
            )
        return LearningTask(
            task_id=f"TASK_{uuid4().hex}",
            learner_id=task.learner_id,
            short_term_plan_id=task.short_term_plan_id,
            task_type=task.task_type,
            task_content=content,
            learning_chapter=task.learning_chapter,
            focus_knowledge_points=list(task.focus_knowledge_points),
            estimated_minutes=target_minutes,
            expected_output=expected_output,
            completion_criteria=completion_criteria,
            version=task.version + 1,
            status="pending",
            created_at=now,
            updated_at=now,
            refresh_started_at=now,
            refresh_due_at=now + DAILY_TASK_REFRESH_INTERVAL,
            items=items,
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
        task_load_policy: dict[str, Any] | None = None,
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
            "task_load_policy": task_load_policy or None,
        }

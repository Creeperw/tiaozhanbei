from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from competition_app.contracts.learning_plan import (
    DailyTaskItemSpec,
    LearningTask,
    is_executable_item,
    is_externally_owned_item,
)
from competition_app.contracts.exam_scope import ExamWorkspaceContext
from competition_app.exam_scope import (
    bind_exam_workspace_context,
    current_exam_scope,
    reset_exam_workspace,
)
from competition_app.repositories.learning_plan import LearningPlanRepository
from competition_app.services.learning_plan import (
    KnowledgePointResolver,
    VideoResourceResolver,
    materialize_daily_task_items,
)
from competition_app.services.daily_task_scheduler import (
    build_daily_task_schedule,
    reconcile_daily_task_schedule,
)


DAILY_TASK_REFRESH_INTERVAL = timedelta(hours=24)

logger = logging.getLogger(__name__)


class DailyTaskRefreshService:
    """Keep the current learning task on a server-owned rolling 24-hour window."""

    def __init__(
        self,
        repository: LearningPlanRepository,
        knowledge_point_resolver: KnowledgePointResolver | None = None,
        video_resource_resolver: VideoResourceResolver | None = None,
        task_load_policy_loader: Callable[..., dict[str, Any]] | None = None,
        path_candidate_loader: Callable[..., dict[str, Any]] | None = None,
        review_knowledge_point_loader: Callable[[str], list[str]] | None = None,
        progress_loader: Callable[[str, LearningTask], dict[str, Any]] | None = None,
    ) -> None:
        self.repository = repository
        self.knowledge_point_resolver = knowledge_point_resolver
        self.video_resource_resolver = video_resource_resolver
        self.task_load_policy_loader = task_load_policy_loader
        self.path_candidate_loader = path_candidate_loader
        # 到期复习知识点名称列表；用于把复习知识点纳入 24h 滚动任务的每日测验。
        self.review_knowledge_point_loader = review_knowledge_point_loader
        # 服务端原子项完成进度；用于判断跨窗口哪些外部项仍需保留。
        self.progress_loader = progress_loader
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
            path_candidates: dict[str, Any] = {}
            if self.path_candidate_loader is not None:
                try:
                    loaded_candidates = self.path_candidate_loader(
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
                            "available_minutes": task_load_policy.get(
                                "recommended_minutes"
                            ),
                        },
                        scope="daily_task",
                        limit=30,
                        include_blocked=True,
                    )
                    if isinstance(loaded_candidates, dict):
                        raw_items = loaded_candidates.get("items")
                        if isinstance(raw_items, list):
                            path_candidates = {
                                "state_digest": loaded_candidates.get("state_digest"),
                                "eligible": [
                                    item
                                    for item in raw_items
                                    if isinstance(item, dict)
                                    and item.get("eligible") is True
                                ],
                                "blocked": [
                                    item
                                    for item in raw_items
                                    if isinstance(item, dict)
                                    and item.get("eligible") is not True
                                ],
                            }
                        else:
                            path_candidates = loaded_candidates
                except Exception:
                    # Candidate telemetry is advisory; the scheduler can still
                    # use the compiled task intent and canonical review queue.
                    path_candidates = {}
            next_task = self._next_task(
                task,
                plans.short_term_plan,
                current_time,
                recommended_minutes=task_load_policy.get("recommended_minutes"),
                task_load_policy=task_load_policy,
                path_candidates=path_candidates,
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

    def refresh_due_tasks(
        self,
        *,
        now: datetime | None = None,
        on_refreshed: Callable[[str, str], None] | None = None,
    ) -> dict[str, int]:
        """Refresh every persisted exam workspace without a browser request.

        Scope binding keeps certificates fully independent. Per-task CAS in
        ``save_current`` remains the final guard when multiple workers scan the
        same due head concurrently.
        """

        list_scopes = getattr(self.repository, "list_scopes", None)
        if not callable(list_scopes):
            return {"scanned": 0, "refreshed": 0, "failed": 0}

        scopes = sorted(set(list_scopes()))
        refreshed = 0
        failed = 0
        for learner_id, exam_track_id in scopes:
            token = bind_exam_workspace_context(
                ExamWorkspaceContext(
                    learner_id=str(learner_id),
                    exam_track_id=str(exam_track_id),
                )
            )
            try:
                result = self.ensure_current(str(learner_id), now=now)
                if result.get("refreshed") is True:
                    refreshed += 1
                    if on_refreshed is not None:
                        on_refreshed(str(learner_id), str(exam_track_id))
            except Exception:
                failed += 1
            finally:
                reset_exam_workspace(token)
        return {"scanned": len(scopes), "refreshed": refreshed, "failed": failed}

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

    def _carried_external_items(
        self, task: LearningTask
    ) -> list[DailyTaskItemSpec]:
        """跨窗口保留未完成的外部来源原子项（如已接受的干预安排）。

        这些项无法从短期计划正文推导出来，重建今日任务时必须显式带回；
        已完成的项不再带过来。进度不可用时保守保留，因为静默丢掉用户
        刚刚确认的安排比多保留一次更不可接受。

        没有完成路径的项不带入新窗口：执行层没有它们的完成入口，带过去
        只会成为点不开的项，并让整版发布被拒收。
        """

        external_items = [
            item
            for item in task.items
            if is_externally_owned_item(item) and is_executable_item(item)
        ]
        dropped = [
            item
            for item in task.items
            if is_externally_owned_item(item) and not is_executable_item(item)
        ]
        if dropped:
            logger.warning(
                "dropped externally owned items without a completion path: "
                "learner=%s task=%s dropped=%s",
                task.learner_id,
                task.task_id,
                ", ".join(
                    f"{item.task_item_id}({item.item_type})" for item in dropped
                ),
            )
        if not external_items or self.progress_loader is None:
            return external_items
        try:
            progress = self.progress_loader(task.learner_id, task)
        except Exception:
            # 进度不可用不能阻塞 24 小时滚动刷新。
            return external_items
        recorded = progress.get("items") if isinstance(progress, dict) else None
        if not isinstance(recorded, list) or not recorded:
            return external_items
        completed_ids = {
            str(entry.get("task_item_id") or "")
            for entry in recorded
            if isinstance(entry, dict)
            and str(entry.get("status") or "").lower() == "completed"
        }
        if not completed_ids:
            return external_items
        return [
            item
            for item in external_items
            if item.task_item_id not in completed_ids
        ]

    def _next_task(
        self,
        task: LearningTask,
        short_plan: Any,
        now: datetime,
        *,
        recommended_minutes: Any = None,
        task_load_policy: dict[str, Any] | None = None,
        path_candidates: dict[str, Any] | None = None,
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

        carried_items = self._carried_external_items(task)
        carried_minutes = sum(
            item.estimated_minutes for item in carried_items
        )
        # 外部项已计入当前任务预算，回退取预算时不能把它们重复计入新内容。
        base_minutes = max(
            1.0, float(task.estimated_minutes) - carried_minutes
        )
        target_minutes = (
            float(recommended_minutes)
            if isinstance(recommended_minutes, (int, float))
            and not isinstance(recommended_minutes, bool)
            and float(recommended_minutes) > 0
            else float(minutes or base_minutes)
        )
        target_minutes = max(10, min(24 * 60, target_minutes))
        review_points = self._review_knowledge_points(task.learner_id)
        schedule = (
            build_daily_task_schedule(
                exam_scope_id=current_exam_scope(task.learner_id),
                target_minutes=target_minutes,
                learning_chapter=task.learning_chapter,
                intent_knowledge_points=list(task.focus_knowledge_points),
                review_knowledge_points=review_points,
                knowledge_point_resolver=self.knowledge_point_resolver,
                path_candidates=path_candidates,
                task_load_policy=task_load_policy,
                previous_schedule=task.daily_task_schedule,
            )
            if self.knowledge_point_resolver is not None
            else None
        )
        scheduled_names = (
            [item.knowledge_point_name for item in schedule.selected]
            if schedule is not None
            else list(task.focus_knowledge_points)
        )
        scheduled_ids = (
            {item.kp_id for item in schedule.selected}
            if schedule is not None
            else set()
        )
        if selected_block is not None:
            _content, _minutes, raw_block = self._block_values(selected_block)
            block_name = ""
            if isinstance(raw_block, dict):
                block_name = str(
                    raw_block.get("knowledge_point_name")
                    or raw_block.get("kp_id")
                    or ""
                ).strip()
            else:
                block_name = str(
                    getattr(raw_block, "knowledge_point_name", None)
                    or getattr(raw_block, "kp_id", None)
                    or ""
                ).strip()
            if block_name:
                try:
                    block_kp_id = self.knowledge_point_resolver(
                        block_name, task.learning_chapter
                    ) if self.knowledge_point_resolver is not None else None
                except TypeError:
                    block_kp_id = self.knowledge_point_resolver(block_name) if self.knowledge_point_resolver is not None else None
                if block_name not in scheduled_names and block_kp_id not in scheduled_ids:
                    selected_block = None
        try:
            items = materialize_daily_task_items(
                task_content=content,
                learning_chapter=task.learning_chapter,
                estimated_minutes=target_minutes,
                focus_knowledge_points=scheduled_names,
                task_blocks=[selected_block] if selected_block is not None else [],
                knowledge_point_resolver=self.knowledge_point_resolver,
                video_resource_resolver=self.video_resource_resolver,
                quiz_target_count=None,
                review_knowledge_points=review_points,
            )
        except ValueError:
            # Preserve the last executable budget if an unusually dense legacy
            # task cannot fit into the reduced recommendation.
            target_minutes = max(
                target_minutes, float(minutes or base_minutes)
            )
            items = materialize_daily_task_items(
                task_content=content,
                learning_chapter=task.learning_chapter,
                estimated_minutes=target_minutes,
                focus_knowledge_points=scheduled_names,
                task_blocks=[selected_block] if selected_block is not None else [],
                knowledge_point_resolver=self.knowledge_point_resolver,
                video_resource_resolver=self.video_resource_resolver,
                quiz_target_count=None,
                review_knowledge_points=review_points,
            )
        if carried_items:
            # 外部项不是本窗口的排程产物，追加在末尾并重排序号。
            items = [
                item.model_copy(update={"ordinal": ordinal})
                for ordinal, item in enumerate(
                    items + carried_items, start=1
                )
            ]
        actual_kp_ids = {
            str(item.kp_id)
            for item in items
            if item.item_type == "knowledge_practice"
            and not item.completion_policy.get("quiz")
            and item.kp_id
        }
        actual_names = list(
            dict.fromkeys(
                str(item.knowledge_point_name or "").strip()
                for item in items
                if item.item_type == "knowledge_practice"
                and not item.completion_policy.get("quiz")
                and str(item.knowledge_point_name or "").strip()
            )
        )
        if schedule is not None:
            schedule = reconcile_daily_task_schedule(
                schedule,
                materialized_kp_ids=actual_kp_ids,
            )
        return LearningTask(
            task_id=f"TASK_{uuid4().hex}",
            learner_id=task.learner_id,
            short_term_plan_id=task.short_term_plan_id,
            task_type=task.task_type,
            task_content=content,
            learning_chapter=task.learning_chapter,
            focus_knowledge_points=(
                actual_names if schedule is not None else list(task.focus_knowledge_points)
            ),
            estimated_minutes=max(
                1.0, sum(item.estimated_minutes for item in items)
            ),
            expected_output=expected_output,
            completion_criteria=completion_criteria,
            version=task.version + 1,
            status="pending",
            created_at=now,
            updated_at=now,
            refresh_started_at=now,
            refresh_due_at=now + DAILY_TASK_REFRESH_INTERVAL,
            items=items,
            daily_task_schedule=schedule,
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

    def _review_knowledge_points(self, learner_id: str) -> list[str]:
        """Load the learner's due-for-review knowledge point names.

        A loader outage must never block the 24-hour refresh, so any failure
        degrades to an empty list.
        """
        if self.review_knowledge_point_loader is None:
            return []
        try:
            loaded = self.review_knowledge_point_loader(learner_id)
        except Exception:
            return []
        if not isinstance(loaded, list):
            return []
        names: list[str] = []
        for name in loaded:
            value = str(name or "").strip()
            if not value or value == "知识点名称待补充":
                continue
            if value not in names:
                names.append(value)
        return names[:5]

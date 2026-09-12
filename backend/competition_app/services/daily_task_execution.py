from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text

from competition_app.repositories.learning_plan import LearningPlanRepository
from competition_app.contracts.learning_plan import StageEvidenceRecord


def daily_task_progress_request(task: Any) -> dict[str, Any]:
    """构造服务端进度查询载荷。

    刷新与执行两条链路共用同一份载荷定义，避免其中一条漏带字段后把
    进度查询退化为「全部未完成」。
    """

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
            self._reconcile_plan_progression(learner_id)
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
            self._reconcile_plan_progression(learner_id)
            return True
        current = self.plan_repository.get_current(learner_id)
        completed = bool(
            current is not None
            and current.learning_task is not None
            and current.learning_task.status == "completed"
        )
        if completed:
            self._reconcile_plan_progression(learner_id)
        return completed

    def _reconcile_plan_progression(self, learner_id: str) -> dict[str, Any]:
        """Write completed-task evidence and advance only deterministic gates."""

        plans = self.plan_repository.get_current(learner_id)
        if (
            plans is None
            or plans.learning_task is None
            or plans.learning_task.status != "completed"
        ):
            return {"changed": False}
        task = plans.learning_task
        now = datetime.now(timezone.utc)
        long_plan = plans.long_term_plan
        short_plan = plans.short_term_plan
        current_stage = self._current_stage_number(long_plan)
        stage_requirements = self._stage_requirements(long_plan, current_stage)
        declared = self._normalize_text(
            "\n".join(
                (
                    task.expected_output,
                    task.completion_criteria,
                    task.task_content,
                )
            )
        )
        evidence = list(long_plan.stage_evidence) if long_plan is not None else []
        added_requirements: list[str] = []
        for requirement in stage_requirements:
            normalized = self._normalize_text(requirement)
            if not normalized or normalized not in declared:
                continue
            if any(
                item.stage == current_stage
                and item.requirement == requirement
                and item.source_id == task.task_id
                for item in evidence
            ):
                continue
            evidence.append(
                StageEvidenceRecord(
                    evidence_id=f"STAGE_EVIDENCE_{uuid4().hex}",
                    stage=current_stage,
                    requirement=requirement,
                    source_type="completed_daily_task",
                    source_id=task.task_id,
                    verified_by="daily_task_execution",
                    verified_at=now,
                )
            )
            added_requirements.append(requirement)

        verified = {
            item.requirement
            for item in evidence
            if item.stage == current_stage
        }
        long_passed = bool(stage_requirements) and all(
            requirement in verified for requirement in stage_requirements
        )
        short_passed = self._short_term_gate_passed(
            learner_id, short_plan, task
        )
        updated_short = short_plan
        if (
            short_passed
            and short_plan is not None
            and short_plan.status != "completed"
        ):
            updated_short = short_plan.model_copy(
                update={
                    "status": "completed",
                    "version": short_plan.version + 1,
                    "updated_at": now,
                }
            )

        next_stage: int | None = None
        long_term_completed = False
        updated_long = long_plan
        if long_plan is not None and (added_requirements or long_passed):
            updates: dict[str, Any] = {
                "stage_evidence": evidence,
                "version": long_plan.version + 1,
                "updated_at": now,
            }
            if long_passed:
                next_selection = self._next_textbook_selection(
                    long_plan, current_stage
                )
                if next_selection is None:
                    updates["status"] = "completed"
                    long_term_completed = True
                else:
                    updates["textbook_selection"] = next_selection
                    updates["status"] = "active"
                    next_stage = current_stage + 1
            updated_long = long_plan.model_copy(update=updates)

        changed = (
            bool(added_requirements)
            or updated_short is not short_plan
            or updated_long is not long_plan
        )
        if not changed:
            return {"changed": False}

        stage_advanced = next_stage is not None
        result = plans.model_copy(
            update={
                "long_term_plan": updated_long,
                # A newly selected long-term stage invalidates downstream
                # plans; the next agent turn will construct them from the new
                # authoritative stage instead of silently reusing stale work.
                "short_term_plan": None if stage_advanced else updated_short,
                "learning_task": None if stage_advanced else task,
            }
        )
        self.plan_repository.save_current(
            learner_id,
            result,
            invalidated_layers=(
                ["short_term", "daily_task"] if stage_advanced else []
            ),
        )
        progression = {
            "event_id": (
                f"PROGRESSION_{task.task_id}_{current_stage}_"
                f"{int(short_passed)}_{int(long_passed)}"
            ),
            "completed_layer": "daily_task",
            "task_id": task.task_id,
            "stage": current_stage,
            "next_stage": next_stage,
            "short_term_completed": short_passed,
            "long_term_stage_passed": long_passed,
            "long_term_completed": long_term_completed,
            "verified_requirements": added_requirements,
        }
        writer = getattr(
            self.backend_handoff_runtime, "record_plan_progression_event", None
        )
        if callable(writer):
            try:
                writer(learner_id, progression)
            except Exception:
                # Plan state is authoritative; notification delivery is
                # recoverable and must not roll back a passed gate.
                pass
        return {"changed": True, **progression}

    def _short_term_gate_passed(
        self,
        learner_id: str,
        short_plan: Any,
        current_task: Any,
    ) -> bool:
        if short_plan is None or short_plan.short_term_learning_package is None:
            return False
        required = {
            self._normalize_task_content(self._block_content(block))
            for block in short_plan.short_term_learning_package.task_blocks
            if self._block_content(block)
        }
        if not required:
            return False
        completed = {
            self._normalize_task_content(current_task.task_content)
        }
        if self.engine is not None:
            with self.engine.connect() as connection:
                payloads = connection.execute(
                    text(
                        "SELECT payload_json FROM learning_task_versions "
                        "WHERE learner_id=:learner_id AND status='completed'"
                    ),
                    {"learner_id": learner_id},
                ).scalars().all()
            for payload in payloads:
                if isinstance(payload, (bytes, bytearray)):
                    payload = payload.decode("utf-8")
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except (TypeError, ValueError):
                        continue
                if (
                    isinstance(payload, dict)
                    and payload.get("short_term_plan_id") == short_plan.plan_id
                    and payload.get("status") == "completed"
                ):
                    completed.add(
                        self._normalize_task_content(
                            str(payload.get("task_content") or "")
                        )
                    )
        return required.issubset(completed)

    @staticmethod
    def _block_content(block: Any) -> str:
        if isinstance(block, str):
            return block.strip()
        if isinstance(block, dict):
            return str(block.get("content") or "").strip()
        return str(getattr(block, "content", "") or "").strip()

    @classmethod
    def _normalize_task_content(cls, value: str) -> str:
        normalized = re.sub(r"^复盘并巩固[:：]\s*", "", str(value or "").strip())
        return cls._normalize_text(normalized)

    @staticmethod
    def _normalize_text(value: str) -> str:
        return re.sub(r"[\s，。；：、,.!！?？（）()《》【】\"'“”‘’]", "", value).casefold()

    @staticmethod
    def _current_stage_number(plan: Any) -> int:
        if plan is None or plan.textbook_selection is None or plan.planning_route is None:
            return 1
        stage_id = plan.textbook_selection.stage_id
        textbook_route = plan.planning_route.textbook_route
        if textbook_route is not None and textbook_route.route is not None:
            for item in textbook_route.route.stages:
                if item.stage_id == stage_id:
                    return int(item.order)
        for index, phase in enumerate(plan.planning_route.phases, start=1):
            if phase.phase_id == stage_id:
                return index
        return 1

    @staticmethod
    def _stage_requirements(plan: Any, stage: int) -> list[str]:
        """阶段晋级条款 = 路线验收证据（基线）+ 用户长期计划该阶段验收条款 + 里程碑证据。

        三者合并去重（保序），让用户长期规划的个性化验收参与晋级门禁。
        存量计划中里程碑条款与路线条款逐字一致，合并后不改变既有判定。
        """
        if plan is None or plan.planning_route is None:
            return []
        requirements: list[str] = []
        textbook_route = plan.planning_route.textbook_route
        if textbook_route is not None and textbook_route.route is not None:
            for item in textbook_route.route.stages:
                if int(item.order) == stage:
                    requirements.extend(str(value) for value in item.exit_evidence)
                    break
        else:
            phases = list(plan.planning_route.phases)
            if 1 <= stage <= len(phases):
                requirements.extend(
                    str(value) for value in phases[stage - 1].exit_evidence
                )
        user_stages = list(plan.stages or [])
        if 1 <= stage <= len(user_stages):
            acceptance = getattr(user_stages[stage - 1], "acceptance", None) or []
            requirements.extend(str(value) for value in acceptance)
        milestones = list(plan.milestones or [])
        if 1 <= stage <= len(milestones):
            requirements.extend(
                str(value) for value in milestones[stage - 1].evidence_required
            )
        seen: set[str] = set()
        deduped: list[str] = []
        for item in requirements:
            if item and item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped

    @staticmethod
    def _next_textbook_selection(plan: Any, stage: int) -> Any | None:
        if plan.textbook_selection is None or plan.planning_route is None:
            return None
        textbook_route = plan.planning_route.textbook_route
        if textbook_route is not None and textbook_route.route is not None:
            stages = list(textbook_route.route.stages)
            next_item = next(
                (item for item in stages if int(item.order) == stage + 1),
                None,
            )
            if next_item is None:
                return None
            return plan.textbook_selection.model_copy(
                update={
                    "stage_id": next_item.stage_id,
                    "stage_name": next_item.name,
                    "books": list(next_item.books)[:2],
                    "reason": "上一阶段全部验收指标已取得服务端核验证据，自动进入下一阶段。",
                }
            )
        phases = list(plan.planning_route.phases)
        if stage >= len(phases):
            return None
        next_phase = phases[stage]
        return plan.textbook_selection.model_copy(
            update={
                "stage_id": next_phase.phase_id,
                "stage_name": next_phase.name,
                "books": list(next_phase.books)[:2],
                "reason": "上一阶段全部验收指标已取得服务端核验证据，自动进入下一阶段。",
            }
        )

    @staticmethod
    def _progress_request(task: Any) -> dict[str, Any]:
        return daily_task_progress_request(task)

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

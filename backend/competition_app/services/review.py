from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from competition_app.contracts.resource import ResourceVersion
from competition_app.contracts.review import (
    ReviewAttempt,
    ReviewAttemptSubmission,
    ReviewMemoryUnit,
    ReviewQueue,
    ReviewQueueEntry,
    ReviewResourceBinding,
    ReviewSchedule,
    ReviewTask,
    UserKnowledgeState,
)
from competition_app.repositories.review import ReviewRepository
from competition_app.review.math import retention_estimate


REVIEW_INTERVAL_SECONDS = (0, 1_200, 3_600, 32_400, 86_400, 172_800, 518_400, 2_678_400)
OUTCOME_SCORES = {
    "independent_correct": 1.0,
    "hinted_correct": 0.6,
    "skipped": 0.2,
    "wrong": 0.0,
}


class ReviewService:
    def __init__(self, repository: ReviewRepository) -> None:
        self.repository = repository

    def ingest_knowledge_states(
        self,
        *,
        learner_id: str,
        states: list[dict],
        prompt_abstract: str,
    ) -> None:
        for raw in states:
            required = {
                "user_id",
                "kp_id",
                "answer_accuracy",
                "kp_review_status",
                "calculated_at",
            }
            has_mastery = any(
                key in raw for key in ("knowledge_mastery", "knowledge_mastery（依据）")
            )
            has_forgetting = any(
                key in raw
                for key in (
                    "forgetting_coefficient",
                    "forgetting_coefficient（依据）",
                )
            )
            if not required.issubset(raw) or not has_mastery or not has_forgetting:
                # Partial state is still useful to Diagnosis, but cannot safely drive
                # deterministic scheduling or persistence.
                continue
            state = UserKnowledgeState.model_validate(raw)
            if state.user_id != learner_id:
                raise ValueError("user knowledge state identity does not match requested learner")
            current = self.repository.get_memory_unit(learner_id, state.kp_id)
            if current is not None and current.source_calculated_at is not None:
                if state.calculated_at <= current.source_calculated_at:
                    continue
            now = datetime.now(timezone.utc)
            calculated_at = self._as_utc(state.calculated_at)
            interval_seconds = self._initial_interval_seconds(
                state.knowledge_mastery, state.forgetting_coefficient
            )
            created_at = current.created_at if current is not None else now
            unit = ReviewMemoryUnit(
                memory_unit_id=(
                    current.memory_unit_id
                    if current is not None
                    else f"RMU_{uuid4().hex}"
                ),
                learner_id=learner_id,
                kp_id=state.kp_id,
                prompt_abstract=(current.prompt_abstract if current else prompt_abstract),
                mastery_score=state.knowledge_mastery * 100,
                lambda_per_day=max(0.03, min(0.20, state.forgetting_coefficient)),
                review_stage=current.review_stage if current else 0,
                stability_seconds=(
                    current.stability_seconds if current else max(1_200, interval_seconds)
                ),
                consecutive_correct=current.consecutive_correct if current else 0,
                consecutive_wrong=current.consecutive_wrong if current else 0,
                last_review_at=calculated_at,
                next_review_at=calculated_at + timedelta(seconds=interval_seconds),
                requires_remediation=(current.requires_remediation if current else False),
                source_calculated_at=calculated_at,
                source_attempt_id=(current.source_attempt_id if current else None),
                activation_source=(current.activation_source if current else None),
                activated_at=(current.activated_at if current else None),
                version=(current.version + 1 if current else 1),
                created_at=created_at,
                updated_at=now,
            )
            self.repository.save_memory_unit(unit)

    def ingest_question_attempts(
        self,
        *,
        learner_id: str,
        attempts: list[dict],
    ) -> int:
        """Admit knowledge points to review only after a completed question attempt."""

        activated = 0
        for raw in attempts:
            if raw.get("completion_status") not in (None, "completed", "submitted"):
                continue
            if raw.get("grading_status") not in (None, "reviewed", "accepted"):
                continue
            if raw.get("audit_decision") not in (None, "pass"):
                continue
            attempt_id = str(raw.get("attempt_id") or "").strip()
            kp_ids = [
                str(item).strip()
                for item in (raw.get("kp_ids") or raw.get("knowledge_points") or [])
                if str(item).strip()
            ]
            answered_at = self._parse_datetime(raw.get("answered_at"))
            if not attempt_id or not kp_ids or answered_at is None:
                continue
            is_correct = bool(raw.get("is_correct"))
            score = self._normalized_attempt_score(raw, is_correct=is_correct)
            for kp_id in dict.fromkeys(kp_ids):
                current = self.repository.get_memory_unit(learner_id, kp_id)
                if current is not None:
                    if current.source_attempt_id == attempt_id:
                        continue
                    if (
                        current.source_attempt_id is not None
                        and current.source_calculated_at is not None
                        and answered_at <= self._as_utc(current.source_calculated_at)
                    ):
                        continue

                previous_mastery = current.mastery_score / 100 if current else score
                previous_time = (
                    current.last_review_at or current.created_at if current else answered_at
                )
                forgetting = current.lambda_per_day if current else 0.08
                elapsed_days = max(
                    0.0,
                    (answered_at - self._as_utc(previous_time)).total_seconds() / 86_400,
                )
                mastery = max(
                    0.0,
                    min(
                        1.0,
                        0.65
                        * previous_mastery
                        * math.exp(-forgetting * elapsed_days)
                        + 0.35 * score,
                    ),
                )
                outcome = "independent_correct" if is_correct else "wrong"
                stage, interval_seconds = self._next_stage_and_interval(
                    current.review_stage if current else 0,
                    outcome,
                )
                consecutive_correct = (
                    (current.consecutive_correct if current else 0) + 1
                    if is_correct
                    else 0
                )
                consecutive_wrong = (
                    (current.consecutive_wrong if current else 0) + 1
                    if not is_correct
                    else 0
                )
                unit = ReviewMemoryUnit(
                    memory_unit_id=(
                        current.memory_unit_id if current else f"RMU_{uuid4().hex}"
                    ),
                    learner_id=learner_id,
                    kp_id=kp_id,
                    prompt_abstract=(
                        current.prompt_abstract
                        if current
                        else str(raw.get("knowledge_point_name") or kp_id)
                    ),
                    mastery_score=mastery * 100,
                    lambda_per_day=forgetting,
                    review_stage=stage,
                    stability_seconds=self._next_stability(
                        current.stability_seconds if current else 1_200,
                        outcome,
                    ),
                    consecutive_correct=consecutive_correct,
                    consecutive_wrong=consecutive_wrong,
                    last_review_at=answered_at,
                    next_review_at=answered_at + timedelta(seconds=interval_seconds),
                    requires_remediation=not is_correct,
                    source_calculated_at=answered_at,
                    source_attempt_id=attempt_id,
                    activation_source="graded_question_attempt",
                    activated_at=(
                        current.activated_at
                        if current is not None and current.activated_at is not None
                        else answered_at
                    ),
                    version=(current.version + 1 if current else 1),
                    created_at=current.created_at if current else answered_at,
                    updated_at=answered_at,
                )
                self.repository.save_memory_unit(unit)
                activated += 1
        return activated

    def has_completed_attempt(self, learner_id: str, kp_id: str) -> bool:
        unit = self.repository.get_memory_unit(learner_id, kp_id)
        return bool(unit and unit.source_attempt_id)

    def record_delivery(
        self,
        *,
        schedule: ReviewSchedule,
        task: ReviewTask,
        resource: ResourceVersion,
        binding: ReviewResourceBinding,
        prompt_abstract: str,
    ) -> None:
        current = self.repository.get_memory_unit(task.learner_id, task.primary_kp_id)
        if current is None:
            candidate = next(
                (
                    item
                    for item in schedule.candidates
                    if item.kp_id == task.primary_kp_id
                ),
                None,
            )
            now = self._as_utc(schedule.calculated_at)
            mastery = (
                candidate.input_mastery * 100
                if candidate is not None and candidate.input_mastery is not None
                else 0.0
            )
            unit = ReviewMemoryUnit(
                memory_unit_id=f"RMU_{uuid4().hex}",
                learner_id=task.learner_id,
                kp_id=task.primary_kp_id,
                prompt_abstract=resource.title or prompt_abstract,
                mastery_score=mastery,
                lambda_per_day=(
                    candidate.input_forgetting_coefficient
                    if candidate is not None
                    and candidate.input_forgetting_coefficient is not None
                    else 0.08
                ),
                stability_seconds=1_200,
                last_review_at=(
                    self._as_utc(candidate.state_calculated_at)
                    if candidate is not None and candidate.state_calculated_at
                    else now
                ),
                next_review_at=(
                    self._as_utc(candidate.next_review_at)
                    if candidate is not None
                    else now
                ),
                created_at=now,
                updated_at=now,
            )
            # Preserve the generated resource title, but do not admit this knowledge
            # point into the review queue until a question attempt is completed.
            self.repository.save_memory_unit(unit)
        elif resource.title and current.prompt_abstract != resource.title:
            self.repository.save_memory_unit(
                current.model_copy(
                    update={
                        "prompt_abstract": resource.title,
                        "updated_at": self._as_utc(schedule.calculated_at),
                    }
                )
            )
        self.repository.record_delivery(schedule, task, resource, binding)

    def get_queue(
        self,
        learner_id: str,
        *,
        now: datetime | None = None,
        limit: int = 50,
    ) -> ReviewQueue:
        calculated_at = self._as_utc(now or datetime.now(timezone.utc))
        deliveries = self.repository.list_active_deliveries(learner_id)
        delivery_by_kp = {}
        for delivery in deliveries:
            previous = delivery_by_kp.get(delivery.task.primary_kp_id)
            if previous is None or delivery.task.priority_score > previous.task.priority_score:
                delivery_by_kp[delivery.task.primary_kp_id] = delivery

        entries: list[ReviewQueueEntry] = []
        for unit in self.repository.list_memory_units(learner_id):
            if not unit.source_attempt_id:
                continue
            retention = self._retention(unit, calculated_at)
            due = calculated_at >= self._as_utc(unit.next_review_at)
            reasons = []
            if due:
                reasons.append("next_review_at_reached")
            if retention < 0.85:
                reasons.append("retention_below_warning_threshold")
            if unit.requires_remediation:
                reasons.append("requires_remediation")
            delivery = delivery_by_kp.get(unit.kp_id)
            if delivery is not None:
                reasons.append("resource_ready" if delivery.resource else "resource_pending")
            entries.append(
                ReviewQueueEntry(
                    memory_unit=unit,
                    retention_estimate=retention,
                    is_due=due,
                    reason_codes=reasons,
                    task=delivery.task if delivery else None,
                    resource=delivery.resource if delivery else None,
                )
            )
        entries.sort(
            key=lambda item: (
                item.task is None,
                not item.is_due,
                item.retention_estimate,
                item.memory_unit.next_review_at,
                item.memory_unit.kp_id,
            )
        )
        entries = entries[: max(1, min(limit, 200))]
        return ReviewQueue(
            learner_id=learner_id,
            calculated_at=calculated_at,
            entries=entries,
            due_count=sum(item.is_due for item in entries),
            active_task_count=sum(item.task is not None for item in entries),
            awaiting_resource_count=sum(
                item.is_due and (item.task is None or item.resource is None)
                for item in entries
            ),
        )

    def next_dispatch_entry(self, learner_id: str) -> ReviewQueueEntry | None:
        queue = self.get_queue(learner_id)
        return next(
            (
                item
                for item in queue.entries
                if item.is_due and (item.task is None or item.resource is None)
            ),
            None,
        )

    def submit_attempt(
        self,
        review_task_id: str,
        submission: ReviewAttemptSubmission,
    ) -> ReviewAttempt:
        attempt_id = submission.attempt_id or f"RATT_{uuid4().hex}"
        existing = self.repository.get_attempt(attempt_id)
        if existing is not None:
            if (
                existing.review_task_id != review_task_id
                or existing.learner_id != submission.learner_id
            ):
                raise ValueError("attempt_id already belongs to another review task")
            return existing
        task = self.repository.get_task(review_task_id)
        if task is None:
            raise KeyError("review task not found")
        if task.learner_id != submission.learner_id:
            raise PermissionError("review task does not belong to learner")
        if task.status not in {"pending", "bound", "overdue"}:
            raise ValueError("review task is no longer accepting feedback")
        unit = self.repository.get_memory_unit(task.learner_id, task.primary_kp_id)
        if unit is None:
            raise RuntimeError("review task has no memory unit")

        answered_at = self._as_utc(submission.answered_at or datetime.now(timezone.utc))
        recent = self.repository.list_recent_attempts(
            task.learner_id, task.primary_kp_id, 4
        )
        score = OUTCOME_SCORES[submission.outcome]
        recent_wrong = sum(item.outcome == "wrong" for item in recent) + (
            submission.outcome == "wrong"
        )
        consecutive_correct = (
            unit.consecutive_correct + 1
            if submission.outcome == "independent_correct"
            else 0
        )
        consecutive_wrong = (
            unit.consecutive_wrong + 1 if submission.outcome == "wrong" else 0
        )
        lambda_per_day = max(
            0.03,
            min(
                0.20,
                0.08
                + 0.04 * min(5, recent_wrong)
                - 0.015 * min(5, consecutive_correct),
            ),
        )
        previous_time = unit.last_review_at or unit.created_at
        elapsed_days = max(
            0.0,
            (answered_at - self._as_utc(previous_time)).total_seconds() / 86_400,
        )
        previous_mastery = unit.mastery_score / 100
        mastery = max(
            0.0,
            min(
                1.0,
                0.65
                * previous_mastery
                * math.exp(-lambda_per_day * elapsed_days)
                + 0.35 * score,
            ),
        )
        stage, interval_seconds = self._next_stage_and_interval(
            unit.review_stage, submission.outcome
        )
        stability = self._next_stability(
            unit.stability_seconds, submission.outcome
        )
        updated = unit.model_copy(
            update={
                "mastery_score": mastery * 100,
                "lambda_per_day": lambda_per_day,
                "review_stage": stage,
                "stability_seconds": stability,
                "consecutive_correct": consecutive_correct,
                "consecutive_wrong": consecutive_wrong,
                "last_review_at": answered_at,
                "next_review_at": answered_at + timedelta(seconds=interval_seconds),
                "requires_remediation": consecutive_wrong >= 2,
                "version": unit.version + 1,
                "updated_at": answered_at,
            }
        )
        attempt = ReviewAttempt(
            attempt_id=attempt_id,
            review_task_id=review_task_id,
            learner_id=task.learner_id,
            kp_id=task.primary_kp_id,
            outcome=submission.outcome,
            score=score,
            hint_used=submission.hint_used,
            answered_at=answered_at,
            memory_version_before=unit.version,
            memory_version_after=updated.version,
            mastery_before=unit.mastery_score,
            mastery_after=updated.mastery_score,
            stability_before=unit.stability_seconds,
            stability_after=updated.stability_seconds,
            next_review_at=updated.next_review_at,
        )
        updated_task = task.model_copy(
            update={
                "status": "skipped" if submission.outcome == "skipped" else "completed"
            }
        )
        self.repository.apply_attempt(
            previous_version=unit.version,
            unit=updated,
            attempt=attempt,
            task=updated_task,
        )
        return attempt

    @staticmethod
    def _initial_interval_seconds(mastery: float, forgetting: float) -> int:
        if mastery <= 0.70:
            return 60
        days = -math.log(0.70 / mastery) / forgetting
        return max(60, min(2_678_400, int(days * 86_400)))

    @staticmethod
    def _next_stage_and_interval(stage: int, outcome: str) -> tuple[int, int]:
        if outcome == "independent_correct":
            next_stage = min(7, stage + 1)
            return next_stage, REVIEW_INTERVAL_SECONDS[next_stage]
        if outcome == "hinted_correct":
            return stage, max(60, int(REVIEW_INTERVAL_SECONDS[stage] * 0.75))
        if outcome == "skipped":
            next_stage = max(1, stage - 1)
            return next_stage, max(60, int(REVIEW_INTERVAL_SECONDS[next_stage] * 0.5))
        return stage, 300

    @staticmethod
    def _next_stability(stability: float, outcome: str) -> float:
        if outcome == "independent_correct":
            return max(300.0, stability * 1.2)
        if outcome == "hinted_correct":
            return max(300.0, stability * 1.05)
        return max(300.0, stability * 0.6)

    @staticmethod
    def _retention(unit: ReviewMemoryUnit, now: datetime) -> float:
        if unit.last_review_at is None:
            return 0.0
        return retention_estimate(
            now,
            ReviewService._as_utc(unit.last_review_at),
            unit.stability_seconds,
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _parse_datetime(value) -> datetime | None:
        if isinstance(value, datetime):
            return ReviewService._as_utc(value)
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return ReviewService._as_utc(parsed)

    @staticmethod
    def _normalized_attempt_score(raw: dict, *, is_correct: bool) -> float:
        value = raw.get("score")
        try:
            score = float(value)
        except (TypeError, ValueError):
            return 1.0 if is_correct else 0.0
        max_score = raw.get("max_score")
        try:
            maximum = float(max_score)
        except (TypeError, ValueError):
            maximum = 0.0
        if maximum > 0:
            score /= maximum
        elif score > 1:
            score /= 100
        return max(0.0, min(1.0, score))

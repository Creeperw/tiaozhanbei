from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from competition_app.contracts.review import (
    DailyReviewPolicy,
    LearnerKPReviewState,
    ReviewCandidate,
    ReviewFormulaPolicy,
    ReviewSchedule,
    ReviewTask,
    UserKnowledgeState,
)
from competition_app.review.math import (
    compute_next_review_interval_minutes,
    compute_priority,
    compute_urgency,
    is_due,
    predict_mastery_retention,
    retention_estimate,
)


class ReviewScheduler:
    def rank_and_select(
        self,
        *,
        learner_id: str,
        kp_ids: list[str],
        states: list[UserKnowledgeState],
        daily_policy: DailyReviewPolicy,
        formula_policy: ReviewFormulaPolicy,
        now: datetime,
        diagnosed_weak_kp_ids: list[str] | None = None,
        user_requested: bool = True,
    ) -> ReviewSchedule:
        if not kp_ids:
            raise ValueError("review scheduling requires at least one resolved knowledge point")
        if daily_policy.capacity < 1:
            raise ValueError("daily review policy has no capacity")
        state_by_kp: dict[str, UserKnowledgeState] = {}
        for state in states:
            if state.user_id != learner_id:
                raise ValueError("review state identity does not match requested learner")
            if state.kp_id in state_by_kp:
                raise ValueError(f"duplicate review state for knowledge point: {state.kp_id}")
            state_by_kp[state.kp_id] = state

        weak_ids = set(diagnosed_weak_kp_ids or [])
        candidates = [
            self._candidate(
                kp_id=kp_id,
                state=state_by_kp.get(kp_id),
                now=now,
                policy=formula_policy,
                diagnosed_weak=kp_id in weak_ids,
            )
            for kp_id in dict.fromkeys(kp_ids)
        ]
        candidates.sort(
            key=lambda item: (
                not item.state_found,
                not item.is_due,
                -item.priority_score,
                item.retention_estimate,
                item.input_mastery if item.input_mastery is not None else -1.0,
                item.kp_id,
            )
        )
        selected = next(
            (
                item
                for item in candidates
                if user_requested or item.is_due or item.urgency >= 0.5
            ),
            None,
        )
        selected_task = None
        if selected is not None:
            source_type = (
                "initial_recall"
                if not selected.state_found
                else "system_recommended" if selected.is_due else "user_requested"
            )
            selected_task = ReviewTask(
                review_task_id=f"RT_{uuid4().hex}",
                learner_id=learner_id,
                primary_kp_id=selected.kp_id,
                source_type=source_type,
                priority_score=selected.priority_score,
            )
        return ReviewSchedule(
            schedule_id=f"RS_{uuid4().hex}",
            learner_id=learner_id,
            calculated_at=now,
            formula_policy=formula_policy,
            candidates=candidates,
            selected_task=selected_task,
            selection_summary=(
                f"从 {len(candidates)} 个候选知识点中选择 {selected.kp_id}。"
                if selected is not None
                else f"{len(candidates)} 个候选知识点均未达到系统推荐条件。"
            ),
        )

    @staticmethod
    def _candidate(
        *,
        kp_id: str,
        state: UserKnowledgeState | None,
        now: datetime,
        policy: ReviewFormulaPolicy,
        diagnosed_weak: bool,
    ) -> ReviewCandidate:
        if state is None:
            return ReviewCandidate(
                kp_id=kp_id,
                state_found=False,
                retention_estimate=0.0,
                next_interval_minutes=policy.min_review_interval_minutes,
                next_review_at=now,
                is_due=True,
                urgency=1.0,
                priority_score=1.0,
                can_skip=False,
                reason_codes=["initial_recall"],
            )
        if (now.tzinfo is None) != (state.calculated_at.tzinfo is None):
            raise ValueError("now and calculated_at must use compatible timezone awareness")
        elapsed_days = max(0.0, (now - state.calculated_at).total_seconds() / 86_400)
        retention = predict_mastery_retention(
            state.knowledge_mastery,
            state.forgetting_coefficient,
            elapsed_days,
        )
        interval_minutes = compute_next_review_interval_minutes(
            state.knowledge_mastery,
            state.forgetting_coefficient,
            policy,
        )
        next_review_at = state.calculated_at + timedelta(minutes=interval_minutes)
        due = now >= next_review_at or retention <= policy.min_retention_threshold
        urgency = compute_urgency(retention, policy.min_retention_threshold)
        priority = compute_priority(due=due, urgency=urgency, policy=policy)
        reasons: list[str] = []
        if due:
            reasons.append("due")
        if retention <= policy.min_retention_threshold:
            reasons.append("retention_below_threshold")
        if state.knowledge_mastery < policy.min_retention_threshold:
            reasons.append("low_mastery")
        if diagnosed_weak:
            reasons.append("diagnosed_weak")
        if not reasons:
            reasons.append("not_due")
        can_skip = state.knowledge_mastery > 0.95
        if can_skip:
            reasons.append("skippable_mastery")
        return ReviewCandidate(
            kp_id=kp_id,
            state_found=True,
            input_mastery=state.knowledge_mastery,
            input_forgetting_coefficient=state.forgetting_coefficient,
            state_calculated_at=state.calculated_at,
            elapsed_days=elapsed_days,
            retention_estimate=retention,
            next_interval_minutes=interval_minutes,
            next_review_at=next_review_at,
            is_due=due,
            urgency=urgency,
            priority_score=priority,
            can_skip=can_skip,
            reason_codes=reasons,
        )

    def schedule(
        self,
        learner_id: str,
        kp_id: str,
        state: LearnerKPReviewState | None,
        policy: DailyReviewPolicy,
        now: datetime,
        user_requested: bool = False,
    ) -> ReviewTask:
        if policy.capacity < 1:
            raise ValueError("daily review policy has no capacity")
        if state is None:
            source_type = "initial_recall"
            priority_score = 1.0
        else:
            if state.learner_id != learner_id or state.kp_id != kp_id:
                raise ValueError("review state identity does not match requested learner and knowledge point")
            due = is_due(now, state.next_review_at)
            if not due and not user_requested:
                raise ValueError("review state is not due and no user request was provided")
            source_type = "system_recommended" if due else "user_requested"
            if state.last_review_at is None:
                raise ValueError("last_review_at is required for an existing review state")
            priority_score = 1.0 - retention_estimate(
                now=now,
                last_review_at=state.last_review_at,
                stability_seconds=state.stability_seconds,
            )

        return ReviewTask(
            review_task_id=f"RT_{uuid4().hex}",
            learner_id=learner_id,
            primary_kp_id=kp_id,
            source_type=source_type,
            priority_score=max(0.0, min(1.0, priority_score)),
        )

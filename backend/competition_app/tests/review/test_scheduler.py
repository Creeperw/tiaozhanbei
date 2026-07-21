from datetime import datetime, timedelta, timezone

import pytest

from competition_app.contracts.review import (
    DailyReviewPolicy,
    LearnerKPReviewState,
    ReviewFormulaPolicy,
    UserKnowledgeState,
)
from competition_app.review.scheduler import ReviewScheduler


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
POLICY = DailyReviewPolicy(capacity=1, target_difficulty=2)


def test_missing_state_creates_initial_recall_task() -> None:
    task = ReviewScheduler().schedule(
        learner_id="learner_001",
        kp_id="KP_FJ_001",
        state=None,
        policy=POLICY,
        now=NOW,
    )

    assert task.source_type == "initial_recall"
    assert task.primary_kp_id == "KP_FJ_001"


def test_due_state_creates_system_recommended_task() -> None:
    state = LearnerKPReviewState(
        learner_id="learner_001",
        kp_id="KP_FJ_001",
        review_stage=4,
        stability_seconds=86_400,
        last_review_at=NOW - timedelta(days=2),
        next_review_at=NOW - timedelta(days=1),
    )

    task = ReviewScheduler().schedule("learner_001", "KP_FJ_001", state, POLICY, NOW)

    assert task.source_type == "system_recommended"
    assert task.priority_score > 0


def test_not_due_state_requires_explicit_user_request() -> None:
    state = LearnerKPReviewState(
        learner_id="learner_001",
        kp_id="KP_FJ_001",
        review_stage=4,
        stability_seconds=86_400,
        last_review_at=NOW,
        next_review_at=NOW + timedelta(days=1),
    )

    task = ReviewScheduler().schedule(
        "learner_001",
        "KP_FJ_001",
        state,
        POLICY,
        NOW,
        user_requested=True,
    )

    assert task.source_type == "user_requested"


def test_rank_and_select_uses_authoritative_user_knowledge_state() -> None:
    state = UserKnowledgeState.model_validate({
        "user_id": "learner_001",
        "kp_id": "KP_FJ_001",
        "knowledge_mastery（依据）": 0.58,
        "answer_accuracy": 0.6,
        "forgetting_coefficient（依据）": 0.08,
        "kp_review_status": "需要继续复习",
        "calculated_at": (NOW - timedelta(days=1)).isoformat(),
    })

    schedule = ReviewScheduler().rank_and_select(
        learner_id="learner_001",
        kp_ids=["KP_FJ_001", "KP_FJ_002"],
        states=[state],
        daily_policy=POLICY,
        formula_policy=ReviewFormulaPolicy(),
        now=NOW,
        diagnosed_weak_kp_ids=["KP_FJ_001"],
    )

    assert schedule.selected_task.primary_kp_id == "KP_FJ_001"
    assert schedule.selected_task.source_type == "system_recommended"
    assert schedule.candidates[0].state_found is True
    weak = next(item for item in schedule.candidates if item.kp_id == "KP_FJ_001")
    assert weak.input_mastery == 0.58
    assert "diagnosed_weak" in weak.reason_codes


def test_rank_and_select_rejects_cross_user_state() -> None:
    state = UserKnowledgeState(
        user_id="other",
        kp_id="KP_FJ_001",
        knowledge_mastery=0.8,
        answer_accuracy=0.8,
        forgetting_coefficient=0.08,
        kp_review_status="按计划复习",
        calculated_at=NOW,
    )
    with pytest.raises(ValueError, match="identity"):
        ReviewScheduler().rank_and_select(
            learner_id="learner_001",
            kp_ids=["KP_FJ_001"],
            states=[state],
            daily_policy=POLICY,
            formula_policy=ReviewFormulaPolicy(),
            now=NOW,
        )

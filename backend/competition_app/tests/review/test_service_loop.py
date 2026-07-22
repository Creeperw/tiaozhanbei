from datetime import datetime, timedelta, timezone

from competition_app.contracts.resource import ResourceVersion
from competition_app.contracts.review import (
    DailyReviewPolicy,
    ReviewAttemptSubmission,
    ReviewFormulaPolicy,
    ReviewResourceBinding,
)
from competition_app.repositories.review import InMemoryReviewRepository
from competition_app.review.scheduler import ReviewScheduler
from competition_app.services.review import ReviewService


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def build_delivered_service(*, admitted: bool = False) -> tuple[ReviewService, str]:
    service = ReviewService(InMemoryReviewRepository())
    service.ingest_knowledge_states(
        learner_id="L1",
        prompt_abstract="四君子汤",
        states=[
            {
                "user_id": "L1",
                "kp_id": "KP_FJ_001",
                "knowledge_mastery": 0.6,
                "answer_accuracy": 0.6,
                "forgetting_coefficient": 0.08,
                "kp_review_status": "到期",
                "calculated_at": (NOW - timedelta(days=1)).isoformat(),
            }
        ],
    )
    unit = service.repository.get_memory_unit("L1", "KP_FJ_001")
    if admitted:
        service.ingest_question_attempts(
            learner_id="L1",
            attempts=[{
                "attempt_id": "QUESTION_ATTEMPT_1",
                "kp_ids": ["KP_FJ_001"],
                "is_correct": False,
                "score": 0,
                "answered_at": (NOW - timedelta(days=1)).isoformat(),
            }],
        )
    schedule = ReviewScheduler().rank_and_select(
        learner_id="L1",
        kp_ids=["KP_FJ_001"],
        states=[],
        daily_policy=DailyReviewPolicy(),
        formula_policy=ReviewFormulaPolicy(),
        now=NOW,
    )
    task = schedule.selected_task.model_copy(
        update={"status": "bound" if admitted else "awaiting_attempt"}
    )
    resource = ResourceVersion(
        resource_id="RES_1",
        source_draft_id="DRAFT_1",
        title="四君子汤复习卡",
        content={"summary": "复习内容"},
        audit_result_id="AUDIT_1",
        published_at=NOW,
    )
    binding = ReviewResourceBinding(
        binding_id="BIND_1",
        review_task_id=task.review_task_id,
        resource_id=resource.resource_id,
        audit_result_id="AUDIT_1",
    )
    service.record_delivery(
        schedule=schedule,
        task=task,
        resource=resource,
        binding=binding,
        prompt_abstract=unit.prompt_abstract,
    )
    return service, task.review_task_id


def test_generated_card_without_question_attempt_is_not_queued() -> None:
    service, task_id = build_delivered_service()

    queue = service.get_queue("L1", now=NOW)

    assert queue.entries == []
    assert service.repository.get_task(task_id).status == "awaiting_attempt"


def test_completed_question_attempt_admits_knowledge_point_to_queue() -> None:
    service, _ = build_delivered_service()
    service.ingest_question_attempts(
        learner_id="L1",
        attempts=[{
            "attempt_id": "QUESTION_ATTEMPT_2",
            "kp_ids": ["KP_FJ_001"],
            "is_correct": False,
            "score": 0,
            "answered_at": (NOW - timedelta(minutes=10)).isoformat(),
        }],
    )

    queue = service.get_queue("L1", now=NOW)

    assert queue.due_count == 1
    assert queue.active_task_count == 0
    assert queue.awaiting_resource_count == 1
    assert queue.entries[0].memory_unit.source_attempt_id == "QUESTION_ATTEMPT_2"


def test_question_completion_without_prior_card_is_idempotently_admitted() -> None:
    service = ReviewService(InMemoryReviewRepository())
    attempt = {
        "attempt_id": "QUESTION_ATTEMPT_DIRECT_1",
        "kp_ids": ["KP_FJ_001"],
        "knowledge_point_name": "四君子汤",
        "is_correct": True,
        "score": 5,
        "max_score": 5,
        "answered_at": NOW.isoformat(),
    }

    assert service.ingest_question_attempts(learner_id="L1", attempts=[attempt]) == 1
    first = service.repository.get_memory_unit("L1", "KP_FJ_001")
    assert service.ingest_question_attempts(learner_id="L1", attempts=[attempt]) == 0
    replay = service.repository.get_memory_unit("L1", "KP_FJ_001")

    assert replay == first
    assert first.prompt_abstract == "四君子汤"
    assert first.source_attempt_id == "QUESTION_ATTEMPT_DIRECT_1"
    assert first.activation_source == "graded_question_attempt"
    assert first.activated_at == NOW
    assert first.next_review_at == NOW + timedelta(minutes=20)


def test_unfinished_or_rejected_grading_never_enters_review_queue() -> None:
    service = ReviewService(InMemoryReviewRepository())
    base = {
        "kp_ids": ["KP_FJ_001"],
        "is_correct": False,
        "answered_at": NOW.isoformat(),
    }

    admitted = service.ingest_question_attempts(
        learner_id="L1",
        attempts=[
            {**base, "attempt_id": "A1", "completion_status": "draft"},
            {**base, "attempt_id": "A2", "grading_status": "pending"},
            {**base, "attempt_id": "A3", "audit_decision": "reject"},
        ],
    )

    assert admitted == 0
    assert service.get_queue("L1", now=NOW).entries == []


def test_feedback_updates_memory_stage_and_is_idempotent() -> None:
    service, task_id = build_delivered_service(admitted=True)
    submission = ReviewAttemptSubmission(
        learner_id="L1",
        outcome="independent_correct",
        answered_at=NOW,
        attempt_id="ATTEMPT_1",
    )

    first = service.submit_attempt(task_id, submission)
    replay = service.submit_attempt(task_id, submission)
    unit = service.repository.get_memory_unit("L1", "KP_FJ_001")

    assert replay == first
    assert unit.version == first.memory_version_after
    assert unit.review_stage == 1
    assert unit.next_review_at == NOW + timedelta(minutes=20)
    assert unit.mastery_score > first.mastery_before
    assert service.repository.get_task(task_id).status == "completed"
    assert service.get_queue("L1", now=NOW).active_task_count == 0

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text

from competition_app.contracts.resource import ResourceVersion
from competition_app.contracts.review import (
    DailyReviewPolicy,
    ReviewFormulaPolicy,
    ReviewResourceBinding,
)
from competition_app.repositories.review import SqlReviewRepository
from competition_app.review.scheduler import ReviewScheduler
from competition_app.services.review import ReviewService


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def build_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE review_memory_units (memory_unit_id TEXT UNIQUE, learner_id TEXT, "
            "kp_id TEXT, next_review_at TIMESTAMP, version INTEGER, payload_json TEXT, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (learner_id, kp_id))"
        ))
        connection.execute(text(
            "CREATE TABLE review_schedules (schedule_id TEXT PRIMARY KEY, learner_id TEXT, "
            "calculated_at TIMESTAMP, payload_json TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "CREATE TABLE review_attempts (attempt_id TEXT PRIMARY KEY, review_task_id TEXT, "
            "learner_id TEXT, kp_id TEXT, outcome TEXT, answered_at TIMESTAMP, payload_json TEXT)"
        ))
        connection.execute(text(
            "CREATE TABLE review_state_events (event_id TEXT PRIMARY KEY, learner_id TEXT, "
            "kp_id TEXT, event_type TEXT, payload_json TEXT)"
        ))
        connection.execute(text(
            "CREATE TABLE review_tasks (review_task_id TEXT PRIMARY KEY, learner_id TEXT, "
            "primary_kp_id TEXT, status TEXT, payload_json TEXT)"
        ))
        connection.execute(text(
            "CREATE TABLE review_resource_bindings (binding_id TEXT PRIMARY KEY, "
            "review_task_id TEXT, resource_id TEXT, resource_version INTEGER, audit_result_id TEXT)"
        ))
        connection.execute(text(
            "CREATE TABLE resource_versions (resource_id TEXT, version INTEGER, status TEXT, "
            "payload_json TEXT, PRIMARY KEY(resource_id, version))"
        ))
    return engine


def test_review_queue_survives_repository_recreation() -> None:
    engine = build_engine()
    service = ReviewService(SqlReviewRepository(engine))
    service.ingest_knowledge_states(
        learner_id="L1",
        prompt_abstract="四君子汤",
        states=[{
            "user_id": "L1", "kp_id": "KP1", "knowledge_mastery": 0.5,
            "answer_accuracy": 0.5, "forgetting_coefficient": 0.08,
            "kp_review_status": "到期", "calculated_at": (NOW - timedelta(days=1)).isoformat(),
        }],
    )
    service.ingest_question_attempts(
        learner_id="L1",
        attempts=[{
            "attempt_id": "PERSISTED_QUESTION_ATTEMPT_1",
            "kp_ids": ["KP1"],
            "is_correct": False,
            "score": 0,
            "answered_at": (NOW - timedelta(days=1)).isoformat(),
        }],
    )
    schedule = ReviewScheduler().rank_and_select(
        learner_id="L1", kp_ids=["KP1"], states=[],
        daily_policy=DailyReviewPolicy(), formula_policy=ReviewFormulaPolicy(), now=NOW,
    )
    task = schedule.selected_task.model_copy(update={"status": "bound"})
    resource = ResourceVersion(
        resource_id="RES1", source_draft_id="D1", title="复习卡",
        content={"summary": "内容"}, audit_result_id="A1", published_at=NOW,
    )
    binding = ReviewResourceBinding(
        binding_id="B1", review_task_id=task.review_task_id,
        resource_id="RES1", audit_result_id="A1",
    )
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO review_tasks VALUES (:id, 'L1', 'KP1', 'bound', :payload)"
        ), {"id": task.review_task_id, "payload": json.dumps(task.model_dump(mode="json"))})
        connection.execute(text(
            "INSERT INTO resource_versions VALUES ('RES1', 1, 'published', :payload)"
        ), {"payload": json.dumps(resource.model_dump(mode="json"))})
        connection.execute(text(
            "INSERT INTO review_resource_bindings VALUES ('B1', :task, 'RES1', 1, 'A1')"
        ), {"task": task.review_task_id})
    service.record_delivery(
        schedule=schedule, task=task, resource=resource, binding=binding,
        prompt_abstract="四君子汤",
    )

    restored = ReviewService(SqlReviewRepository(engine)).get_queue("L1", now=NOW)

    assert restored.entries[0].task.review_task_id == task.review_task_id
    assert restored.entries[0].resource["resource_id"] == "RES1"
    assert restored.due_count == 1

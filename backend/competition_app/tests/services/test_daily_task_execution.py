from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, text

from competition_app.contracts.learning_plan import (
    DailyTaskItemSpec,
    LearningPlanResult,
    LearningTask,
    LongTermPlan,
    ShortTermPlan,
)
from competition_app.repositories.learning_plan import SqlLearningPlanRepository
from competition_app.services.daily_task_execution import DailyTaskExecutionCoordinator


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def build_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE learner_plan_states (learner_id TEXT PRIMARY KEY, payload_json TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE long_term_plan_versions (plan_id TEXT, learner_id TEXT, version INTEGER, status TEXT, payload_json TEXT, PRIMARY KEY(plan_id, version))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE short_term_plan_versions (plan_id TEXT, learner_id TEXT, version INTEGER, status TEXT, payload_json TEXT, PRIMARY KEY(plan_id, version))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE learning_task_versions (task_id TEXT, learner_id TEXT, version INTEGER, status TEXT, payload_json TEXT, PRIMARY KEY(task_id, version))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE learning_task_sync_outbox (event_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, task_id TEXT NOT NULL, task_version INTEGER NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempt_count INTEGER NOT NULL DEFAULT 0, last_error TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, delivered_at TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE learning_task_refresh_claims (learner_id TEXT NOT NULL, prior_task_id TEXT NOT NULL, prior_task_version INTEGER NOT NULL, replacement_task_id TEXT NOT NULL, replacement_task_version INTEGER NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(learner_id, prior_task_id, prior_task_version))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE plan_invalidation_events (event_id TEXT PRIMARY KEY, learner_id TEXT, invalidated_layer TEXT, reason TEXT)"
            )
        )
    return engine


def build_plan(learner_id: str) -> LearningPlanResult:
    long_plan = LongTermPlan(
        plan_id="LONG_1",
        learner_id=learner_id,
        content="长期计划",
        version=1,
        status="active",
        created_at=NOW,
        updated_at=NOW,
    )
    short_plan = ShortTermPlan(
        plan_id="SHORT_1",
        learner_id=learner_id,
        long_term_plan_id=long_plan.plan_id,
        content="短期计划",
        version=1,
        status="active",
        created_at=NOW,
        updated_at=NOW,
    )
    task = LearningTask(
        task_id="TASK_1",
        learner_id=learner_id,
        short_term_plan_id=short_plan.plan_id,
        task_type="daily_learning",
        task_content="学习四君子汤",
        learning_chapter="《方剂学》补益剂·补气",
        focus_knowledge_points=["四君子汤"],
        estimated_minutes=25,
        expected_output="复述",
        completion_criteria="完成任务",
        version=1,
        status="pending",
        created_at=NOW,
        updated_at=NOW,
        items=[
            DailyTaskItemSpec(
                task_item_id="DTI_1",
                ordinal=1,
                item_type="knowledge_practice",
                title="四君子汤知识点实践",
                estimated_minutes=10,
                knowledge_point_name="四君子汤",
                kp_id="KP_1",
                required_question_count=2,
                resource_ref={},
                completion_policy={"policy": "frozen_question_set"},
            )
        ],
    )
    return LearningPlanResult(
        long_term_plan=long_plan,
        short_term_plan=short_plan,
        learning_task=task,
    )


class FakeBackendHandoffRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.failures = 1
        self.progress_payload = {
            "host_task_id": "TASK_1",
            "host_task_version": 1,
            "status": "completed",
            "items": [
                {
                    "task_item_id": "DTI_1",
                    "status": "completed",
                    "progress": {
                        "reviewed_questions": 2,
                        "required_questions": 2,
                    },
                }
            ],
        }

    def upsert_daily_task_execution(self, external_user_id: str, payload: dict) -> dict:
        self.calls.append((external_user_id, payload))
        if self.failures:
            self.failures -= 1
            raise RuntimeError("handoff transient failure")
        return {"status": "delivered", "payload": payload}

    def load_daily_task_progress(self, external_user_id: str, payload: dict) -> dict:
        return self.progress_payload


def test_dispatch_pending_retries_failed_outbox_event_and_marks_delivered() -> None:
    engine = build_engine()
    repository = SqlLearningPlanRepository(engine)
    learner_id = "LEARNER_1"
    plan = build_plan(learner_id)
    repository.save_current(learner_id, plan)

    fake_runtime = FakeBackendHandoffRuntime()
    coordinator = DailyTaskExecutionCoordinator(
        engine=engine,
        plan_repository=repository,
        backend_handoff_runtime=fake_runtime,
    )

    first_dispatch = coordinator.dispatch_pending(learner_id, limit=20)

    assert first_dispatch == 0
    assert fake_runtime.calls[0][0] == learner_id
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT status, attempt_count, last_error FROM learning_task_sync_outbox WHERE learner_id=:learner_id"
            ),
            {"learner_id": learner_id},
        ).one()
    assert row[0] == "pending"
    assert row[1] == 1
    assert row[2] == "handoff transient failure"

    second_dispatch = coordinator.dispatch_pending(learner_id, limit=20)

    assert second_dispatch == 1
    assert len(fake_runtime.calls) == 2
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT status, attempt_count, last_error, delivered_at FROM learning_task_sync_outbox WHERE learner_id=:learner_id"
            ),
            {"learner_id": learner_id},
        ).one()
    assert row[0] == "delivered"
    assert row[1] == 2
    assert row[2] is None
    assert row[3] is not None


def test_reconcile_parent_status_marks_completed_only_when_all_items_report_complete() -> None:
    engine = build_engine()
    repository = SqlLearningPlanRepository(engine)
    learner_id = "LEARNER_2"
    plan = build_plan(learner_id)
    repository.save_current(learner_id, plan)

    fake_runtime = FakeBackendHandoffRuntime()
    coordinator = DailyTaskExecutionCoordinator(
        engine=engine,
        plan_repository=repository,
        backend_handoff_runtime=fake_runtime,
    )

    updated = coordinator.reconcile_parent_status(learner_id)

    assert updated is True
    stored = repository.get_current(learner_id)
    assert stored.learning_task.version == 2
    assert stored.learning_task.status == "completed"


def test_reconcile_parent_status_rejects_incomplete_item_evidence() -> None:
    engine = build_engine()
    repository = SqlLearningPlanRepository(engine)
    learner_id = "LEARNER_3"
    repository.save_current(learner_id, build_plan(learner_id))
    fake_runtime = FakeBackendHandoffRuntime()
    fake_runtime.progress_payload["status"] = "completed"
    fake_runtime.progress_payload["items"][0]["status"] = "pending"
    coordinator = DailyTaskExecutionCoordinator(engine, repository, fake_runtime)

    assert coordinator.reconcile_parent_status(learner_id) is False
    stored = repository.get_current(learner_id)
    assert stored.learning_task.version == 1
    assert stored.learning_task.status == "pending"


def test_outbox_error_is_redacted_before_persistence() -> None:
    engine = build_engine()
    repository = SqlLearningPlanRepository(engine)
    learner_id = "LEARNER_4"
    repository.save_current(learner_id, build_plan(learner_id))
    runtime = FakeBackendHandoffRuntime()
    runtime.upsert_daily_task_execution = lambda *_: (_ for _ in ()).throw(
        RuntimeError("token=top-secret password:hunter2")
    )
    coordinator = DailyTaskExecutionCoordinator(engine, repository, runtime)

    coordinator.dispatch_pending(learner_id)

    with engine.connect() as connection:
        error = connection.execute(
            text("SELECT last_error FROM learning_task_sync_outbox WHERE learner_id=:learner_id"),
            {"learner_id": learner_id},
        ).scalar_one()
    assert "top-secret" not in error
    assert "hunter2" not in error
    assert error == "token=[REDACTED] password=[REDACTED]"

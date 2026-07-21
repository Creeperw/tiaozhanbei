from datetime import datetime, timezone

from sqlalchemy import create_engine, text

from competition_app.contracts.learning_plan import (
    LearningPlanResult,
    LearningTask,
    LongTermPlan,
    ShortTermPlan,
)
from competition_app.repositories.learning_plan import SqlLearningPlanRepository
from competition_app.repositories.runtime import (
    SqlConversationRepository,
    SqlRunStateRepository,
)


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def build_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE long_term_plan_versions (plan_id TEXT, learner_id TEXT, "
            "version INTEGER, status TEXT, payload_json TEXT, PRIMARY KEY(plan_id, version))"
        ))
        connection.execute(text(
            "CREATE TABLE short_term_plan_versions (plan_id TEXT, learner_id TEXT, "
            "version INTEGER, status TEXT, payload_json TEXT, PRIMARY KEY(plan_id, version))"
        ))
        connection.execute(text(
            "CREATE TABLE learning_task_versions (task_id TEXT, learner_id TEXT, "
            "version INTEGER, status TEXT, payload_json TEXT, PRIMARY KEY(task_id, version))"
        ))
        connection.execute(text(
            "CREATE TABLE learner_plan_states (learner_id TEXT PRIMARY KEY, "
            "payload_json TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "CREATE TABLE plan_invalidation_events (event_id TEXT PRIMARY KEY, "
            "learner_id TEXT, invalidated_layer TEXT, reason TEXT)"
        ))
        connection.execute(text(
            "CREATE TABLE workflow_run_states (thread_id TEXT PRIMARY KEY, execution_id TEXT, "
            "case_id TEXT, learner_id TEXT, status TEXT, payload_json TEXT, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "CREATE TABLE execution_runs (execution_id TEXT PRIMARY KEY, case_id TEXT, "
            "status TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "CREATE TABLE conversation_sessions (session_id TEXT PRIMARY KEY, learner_id TEXT, "
            "title TEXT DEFAULT '新对话', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "CREATE TABLE conversation_messages (message_id TEXT PRIMARY KEY, session_id TEXT, "
            "role TEXT, content TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
    return engine


def plan_result() -> LearningPlanResult:
    long_plan = LongTermPlan(
        plan_id="LONG_1",
        learner_id="L1",
        content="长期计划",
        version=1,
        status="active",
        created_at=NOW,
        updated_at=NOW,
    )
    short_plan = ShortTermPlan(
        plan_id="SHORT_1",
        learner_id="L1",
        long_term_plan_id=long_plan.plan_id,
        content="短期计划",
        version=1,
        status="active",
        created_at=NOW,
        updated_at=NOW,
    )
    task = LearningTask(
        task_id="TASK_1",
        learner_id="L1",
        short_term_plan_id=short_plan.plan_id,
        task_type="knowledge_card",
        task_content="完成一张知识卡",
        estimated_minutes=20,
        expected_output="知识卡",
        completion_criteria="能够复述",
        version=1,
        status="pending",
        created_at=NOW,
        updated_at=NOW,
    )
    return LearningPlanResult(
        long_term_plan=long_plan,
        short_term_plan=short_plan,
        learning_task=task,
    )


def test_sql_learning_plan_repository_survives_repository_recreation() -> None:
    engine = build_engine()
    first = SqlLearningPlanRepository(engine)
    first.save_current("L1", plan_result())

    restored = SqlLearningPlanRepository(engine).get_current("L1")

    assert restored == plan_result()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM long_term_plan_versions")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM short_term_plan_versions")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM learning_task_versions")).scalar_one() == 1


def test_plan_repository_retains_history_and_records_lower_layer_invalidation() -> None:
    engine = build_engine()
    repository = SqlLearningPlanRepository(engine)
    original = plan_result()
    repository.save_current("L1", original)
    updated_long = original.long_term_plan.model_copy(
        update={"content": "新版长期计划", "version": 2}
    )
    repository.save_current(
        "L1",
        LearningPlanResult(long_term_plan=updated_long),
        invalidated_layers=["short_term", "daily_task"],
    )

    current = repository.get_current("L1")
    assert current.long_term_plan.version == 2
    assert current.short_term_plan is None
    assert current.learning_task is None
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM long_term_plan_versions")).scalar_one() == 2
        layers = {
            row[0]
            for row in connection.execute(text(
                "SELECT invalidated_layer FROM plan_invalidation_events"
            ))
        }
    assert layers == {"short_term", "daily_task"}


def test_sql_run_state_repository_merges_updates_and_survives_recreation() -> None:
    engine = build_engine()
    repository = SqlRunStateRepository(engine)
    repository.save("THREAD_1", {
        "status": "running",
        "thread_id": "THREAD_1",
        "execution_id": "EXE_1",
        "case_id": "CASE_1",
        "learner_id": "L1",
    })
    repository.save("THREAD_1", {"status": "completed", "result": {"ok": True}})

    restored = SqlRunStateRepository(engine).get("THREAD_1")
    assert restored["status"] == "completed"
    assert restored["execution_id"] == "EXE_1"
    assert restored["result"] == {"ok": True}
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT status FROM execution_runs WHERE execution_id='EXE_1'"
        )).scalar_one() == "completed"


def test_sql_conversation_repository_is_idempotent_and_checks_owner() -> None:
    engine = build_engine()
    repository = SqlConversationRepository(engine)
    messages = [{"message_id": "M1", "role": "user", "content": "制定长期规划"}]
    repository.save_messages("THREAD_1", "L1", messages)
    repository.save_messages("THREAD_1", "L1", messages)

    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM conversation_sessions")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM conversation_messages")).scalar_one() == 1

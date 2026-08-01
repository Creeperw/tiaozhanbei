import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

from competition_app.config import Settings
from competition_app.contracts.learning_plan import (
    LearningPlanResult,
    LearningTask,
    LongTermPlan,
    ShortTermPlan,
)
from competition_app.db.bootstrap import DatabaseBootstrap
from competition_app.repositories.learning_plan import SqlLearningPlanRepository
from competition_app.repositories.runtime import (
    InMemoryConversationRepository,
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
            "CREATE TABLE learning_task_sync_outbox (event_id TEXT PRIMARY KEY, "
            "learner_id TEXT NOT NULL, task_id TEXT NOT NULL, task_version INTEGER NOT NULL, "
            "event_type TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL "
            "DEFAULT 'pending', attempt_count INTEGER NOT NULL DEFAULT 0, last_error TEXT, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, delivered_at TIMESTAMP, "
            "UNIQUE(task_id, task_version, event_type))"
        ))
        connection.execute(text(
            "CREATE TABLE learning_task_refresh_claims (learner_id TEXT NOT NULL, "
            "prior_task_id TEXT NOT NULL, prior_task_version INTEGER NOT NULL, "
            "replacement_task_id TEXT NOT NULL, replacement_task_version INTEGER NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY(learner_id, prior_task_id, prior_task_version))"
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
            "role TEXT, content TEXT, metadata_json TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
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


def test_task_version_and_outbox_are_saved_atomically_and_idempotently() -> None:
    engine = build_engine()
    repository = SqlLearningPlanRepository(engine)
    value = plan_result()

    repository.save_current("L1", value)
    repository.save_current("L1", value)
    replacement = value.model_copy(
        update={
            "learning_task": value.learning_task.model_copy(
                update={"task_id": "TASK_2", "version": 2}
            )
        }
    )
    repository.save_current("L1", replacement, sync_event_type="replace")

    with engine.connect() as connection:
        rows = list(connection.execute(text(
            "SELECT task_id, task_version, event_type, status "
            "FROM learning_task_sync_outbox ORDER BY task_version"
        )))
    assert rows == [
        ("TASK_1", 1, "publish", "pending"),
        ("TASK_2", 2, "replace", "pending"),
    ]


def test_sql_refresh_cas_publishes_only_one_replacement_for_same_prior_task() -> None:
    engine = build_engine()
    first_repository = SqlLearningPlanRepository(engine)
    second_repository = SqlLearningPlanRepository(engine)
    original = plan_result()
    first_repository.save_current("L1", original)
    replacement_a = original.model_copy(
        update={
            "learning_task": original.learning_task.model_copy(
                update={"task_id": "TASK_A", "version": 2}
            )
        }
    )
    replacement_b = original.model_copy(
        update={
            "learning_task": original.learning_task.model_copy(
                update={"task_id": "TASK_B", "version": 2}
            )
        }
    )

    first_saved = first_repository.save_current(
        "L1",
        replacement_a,
        sync_event_type="replace",
        expected_task_id="TASK_1",
        expected_task_version=1,
    )
    second_saved = second_repository.save_current(
        "L1",
        replacement_b,
        sync_event_type="replace",
        expected_task_id="TASK_1",
        expected_task_version=1,
    )

    assert first_saved is True
    assert second_saved is False
    assert first_repository.get_current("L1").learning_task.task_id == "TASK_A"
    with engine.connect() as connection:
        replacements = list(connection.execute(text(
            "SELECT task_id FROM learning_task_sync_outbox WHERE event_type='replace'"
        )))
    assert replacements == [("TASK_A",)]


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
    messages = [{
        "message_id": "M1",
        "role": "assistant",
        "content": "试卷已经生成。",
        "actions": [{
            "label": "开始答题",
            "destination": "workshop.paper",
            "params": {"paper_id": "PAPER_1"},
        }],
    }]
    repository.save_messages("THREAD_1", "L1", messages)
    repository.save_messages("THREAD_1", "L1", messages)

    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM conversation_sessions")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM conversation_messages")).scalar_one() == 1
    assert repository.get_messages("THREAD_1", "L1")[0]["actions"][0]["label"] == "开始答题"


def test_conversation_repositories_sanitize_persisted_history_at_both_boundaries() -> None:
    messages = [
        {"message_id": "M_USER", "role": "user", "content": "介绍感冒"},
        {
            "message_id": "M_TRACE",
            "role": "tool",
            "content": "外部检索结果，不应进入正式历史",
        },
        {
            "message_id": "M_ASSISTANT",
            "role": "assistant",
            "content": '<think>内部推理</think>感冒可分风寒、风热。<<EV:{"secret":1}>>',
            "actions": [{"label": "查看知识卡"}],
            "raw_model_output": "不应持久化",
        },
    ]

    in_memory = InMemoryConversationRepository()
    in_memory.save_messages("THREAD_MEM", "L1", messages)
    memory_rows = in_memory.get_messages("THREAD_MEM", "L1")
    assert [row["role"] for row in memory_rows] == ["user", "assistant"]
    assert memory_rows[1]["content"] == "感冒可分风寒、风热。"
    assert memory_rows[1]["actions"][0]["label"] == "查看知识卡"
    assert "raw_model_output" not in memory_rows[1]

    engine = build_engine()
    sql = SqlConversationRepository(engine)
    sql.save_messages("THREAD_SQL", "L1", messages)
    sql_rows = sql.get_messages("THREAD_SQL", "L1")
    assert [row["role"] for row in sql_rows] == ["user", "assistant"]
    assistant_row = next(row for row in sql_rows if row["role"] == "assistant")
    assert assistant_row["content"] == "感冒可分风寒、风热。"
    assert "raw_model_output" not in assistant_row


def test_sql_conversation_repository_sanitizes_legacy_polluted_rows_on_read() -> None:
    engine = build_engine()
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO conversation_sessions (session_id, learner_id, title) "
            "VALUES ('LEGACY', 'L1', '旧会话')"
        ))
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(message_id, session_id, role, content, metadata_json) VALUES "
                "(:message_id, 'LEGACY', :role, :content, :metadata_json)"
            ),
            [
                {
                    "message_id": "OLD_USER",
                    "role": "user",
                    "content": "给我讲讲感冒",
                    "metadata_json": '{}',
                },
                {
                    "message_id": "OLD_TOOL",
                    "role": "tool",
                    "content": "教材外部检索结果",
                    "metadata_json": '{}',
                },
                {
                    "message_id": "OLD_ASSISTANT",
                    "role": "assistant",
                    "content": '<think>推理</think>正式回答<<REFS:[{"id":"E1"}]>>',
                    # Old metadata must not be able to replace the canonical
                    # database role/content or leak trace data into history.
                    "metadata_json": json.dumps({
                        "role": "tool",
                        "content": "伪造外部信息",
                        "raw_model_input": "隐藏",
                        "actions": [{"label": "查看详情"}],
                    }, ensure_ascii=False),
                },
            ],
        )

    rows = SqlConversationRepository(engine).get_messages("LEGACY", "L1")
    assert [(row["role"], row["content"]) for row in rows] == [
        ("assistant", "正式回答"),
        ("user", "给我讲讲感冒"),
    ]
    assistant_row = next(row for row in rows if row["role"] == "assistant")
    assert assistant_row["actions"] == [{"label": "查看详情"}]
    assert "raw_model_input" not in assistant_row


def test_formal_sqlite_database_preserves_runtime_repositories(tmp_path: Path) -> None:
    settings = Settings(
        mode="stub",
        use_sqlite=True,
        sqlite_path=tmp_path / "competition_app.sqlite3",
    )
    first_engine = DatabaseBootstrap(settings).ensure_database()
    SqlLearningPlanRepository(first_engine).save_current("L1", plan_result())
    SqlRunStateRepository(first_engine).save("THREAD_1", {
        "status": "completed",
        "thread_id": "THREAD_1",
        "execution_id": "EXE_1",
        "case_id": "CASE_1",
        "learner_id": "L1",
    })
    messages = [{"message_id": "M1", "role": "user", "content": "制定长期规划"}]
    SqlConversationRepository(first_engine).save_messages("THREAD_1", "L1", messages)
    first_engine.dispose()

    second_engine = DatabaseBootstrap(settings).ensure_database()

    assert SqlLearningPlanRepository(second_engine).get_current("L1") == plan_result()
    assert SqlRunStateRepository(second_engine).get("THREAD_1")["status"] == "completed"
    restored_messages = SqlConversationRepository(second_engine).get_messages(
        "THREAD_1", "L1"
    )
    assert [
        {key: message[key] for key in ("message_id", "role", "content")}
        for message in restored_messages
    ] == messages
    assert restored_messages[0]["created_at"]

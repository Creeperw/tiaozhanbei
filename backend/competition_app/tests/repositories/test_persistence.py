import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from competition_app.config import Settings
from competition_app.contracts.learning_plan import (
    LearningPlanResult,
    LearningTask,
    LongTermPlan,
    ShortTermPlan,
)
from competition_app.db.bootstrap import DatabaseBootstrap
from competition_app.repositories.learning_plan import (
    SqlLearningPlanRepository,
    plan_head_versions,
)
from competition_app.repositories.runtime import (
    InMemoryConversationRepository,
    SqlConversationRepository,
    SqlRunStateRepository,
    InMemoryRunStateRepository,
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
            "CREATE TABLE workflow_active_run_claims (learner_id TEXT NOT NULL, "
            "product_surface TEXT NOT NULL, thread_id TEXT NOT NULL UNIQUE, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY(learner_id, product_surface))"
        ))
        connection.execute(text(
            "CREATE TABLE execution_runs (execution_id TEXT PRIMARY KEY, case_id TEXT, "
            "status TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "CREATE TABLE conversation_sessions (session_id TEXT PRIMARY KEY, learner_id TEXT, "
            "title TEXT DEFAULT '新对话', source TEXT DEFAULT 'user', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "CREATE TABLE conversation_messages (message_id TEXT PRIMARY KEY, session_id TEXT, "
            "role TEXT, content TEXT, metadata_json TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "CREATE TABLE context_summaries (summary_id TEXT PRIMARY KEY, session_id TEXT, "
            "execution_id TEXT, payload_json TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
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


def test_sql_run_state_repository_claims_one_active_product_run() -> None:
    repository = SqlRunStateRepository(build_engine())

    first = repository.claim_active_run("L1", "smart_paper", "THREAD_1")
    second = repository.claim_active_run("L1", "smart_paper", "THREAD_2")

    assert first == "THREAD_1"
    assert second == "THREAD_1"


def test_sql_run_state_repository_releases_claim_on_terminal_save() -> None:
    repository = SqlRunStateRepository(build_engine())
    repository.claim_active_run("L1", "smart_paper", "THREAD_1")
    repository.save(
        "THREAD_1",
        {
            "thread_id": "THREAD_1",
            "learner_id": "L1",
            "product_surface": "smart_paper",
            "status": "running",
        },
    )
    repository.save("THREAD_1", {"status": "completed"})

    assert repository.claim_active_run("L1", "smart_paper", "THREAD_2") == "THREAD_2"


def test_sql_run_state_repository_cancelled_is_terminal_and_not_overwritten() -> None:
    repository = SqlRunStateRepository(build_engine())
    repository.claim_active_run("L1", "smart_paper", "THREAD_CANCEL")
    repository.save(
        "THREAD_CANCEL",
        {
            "thread_id": "THREAD_CANCEL",
            "learner_id": "L1",
            "product_surface": "smart_paper",
            "status": "running",
        },
    )

    requested = repository.request_cancellation("THREAD_CANCEL")
    assert requested is not None
    assert requested["status"] == "cancellation_requested"
    assert repository.claim_active_run("L1", "smart_paper", "THREAD_OTHER") == "THREAD_CANCEL"

    cancelled = repository.mark_cancelled("THREAD_CANCEL")
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    repository.save("THREAD_CANCEL", {"status": "completed", "result": {"late": True}})

    final = repository.get("THREAD_CANCEL")
    assert final is not None
    assert final["status"] == "cancelled"
    assert "result" not in final
    assert repository.claim_active_run("L1", "smart_paper", "THREAD_OTHER") == "THREAD_OTHER"


@pytest.mark.parametrize("repository_factory", [
    InMemoryRunStateRepository,
    lambda: SqlRunStateRepository(build_engine()),
])
def test_run_state_cancellation_preserves_requested_state_until_terminal_cancel(
    repository_factory,
) -> None:
    repository = repository_factory()
    repository.claim_active_run("L1", "smart_paper", "THREAD_CANCEL_FLOW")
    repository.save(
        "THREAD_CANCEL_FLOW",
        {
            "thread_id": "THREAD_CANCEL_FLOW",
            "learner_id": "L1",
            "product_surface": "smart_paper",
            "status": "running",
        },
    )

    requested = repository.request_cancellation("THREAD_CANCEL_FLOW")
    assert requested is not None
    assert requested["status"] == "cancellation_requested"
    repository.save("THREAD_CANCEL_FLOW", {"status": "completed", "result": {"late": True}})
    repository.save("THREAD_CANCEL_FLOW", {"status": "failed", "error_code": "late"})
    still_requested = repository.get("THREAD_CANCEL_FLOW")
    assert still_requested is not None
    assert still_requested["status"] == "cancellation_requested"
    assert "result" not in still_requested
    assert repository.claim_active_run("L1", "smart_paper", "THREAD_OTHER_FLOW") == "THREAD_CANCEL_FLOW"

    cancelled = repository.mark_cancelled("THREAD_CANCEL_FLOW")
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert repository.mark_cancelled("THREAD_CANCEL_FLOW")["status"] == "cancelled"
    repository.save("THREAD_CANCEL_FLOW", {"status": "completed", "result": {"too_late": True}})
    final = repository.get("THREAD_CANCEL_FLOW")
    assert final is not None
    assert final["status"] == "cancelled"
    assert "result" not in final
    assert repository.claim_active_run("L1", "smart_paper", "THREAD_OTHER_FLOW") == "THREAD_OTHER_FLOW"


@pytest.mark.parametrize("repository_factory", [
    InMemoryRunStateRepository,
    lambda: SqlRunStateRepository(build_engine()),
])
def test_run_state_completed_before_cancel_remains_completed(repository_factory) -> None:
    repository = repository_factory()
    repository.save("THREAD_COMPLETED_FIRST", {"status": "running"})
    repository.save("THREAD_COMPLETED_FIRST", {"status": "completed", "result": {"ok": True}})

    assert repository.request_cancellation("THREAD_COMPLETED_FIRST")["status"] == "completed"
    assert repository.mark_cancelled("THREAD_COMPLETED_FIRST")["status"] == "completed"
    final = repository.get("THREAD_COMPLETED_FIRST")
    assert final is not None
    assert final["status"] == "completed"
    assert final["result"] == {"ok": True}


def test_sql_run_state_repository_recovers_claim_left_by_previous_process() -> None:
    repository = SqlRunStateRepository(build_engine())
    repository.claim_active_run("L1", "smart_paper", "THREAD_1")
    repository.save(
        "THREAD_1",
        {
            "thread_id": "THREAD_1",
            "learner_id": "L1",
            "product_surface": "smart_paper",
            "status": "running",
        },
    )

    assert repository.recover_abandoned_active_runs() == ["THREAD_1"]
    recovered = repository.get("THREAD_1")
    assert recovered["status"] == "failed"
    assert recovered["error_code"] == "workflow_restart"
    assert recovered["retryable"] is True
    assert repository.claim_active_run("L1", "smart_paper", "THREAD_2") == "THREAD_2"


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


def test_plan_head_x_lock_rejects_a_second_write_from_the_same_stale_snapshot() -> None:
    engine = build_engine()
    first_repository = SqlLearningPlanRepository(engine)
    second_repository = SqlLearningPlanRepository(engine)
    original = plan_result()
    first_repository.save_current("L1", original)
    shared_snapshot = plan_head_versions(original)
    replacement_a = original.model_copy(
        update={
            "short_term_plan": original.short_term_plan.model_copy(
                update={"content": "会话 A 的短期计划", "version": 2}
            )
        }
    )
    replacement_b = original.model_copy(
        update={
            "short_term_plan": original.short_term_plan.model_copy(
                update={"content": "会话 B 的短期计划", "version": 2}
            )
        }
    )

    assert first_repository.save_current(
        "L1", replacement_a, expected_heads=shared_snapshot
    ) is True
    assert second_repository.save_current(
        "L1", replacement_b, expected_heads=shared_snapshot
    ) is False
    assert (
        first_repository.get_current("L1").short_term_plan.content
        == "会话 A 的短期计划"
    )


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


def test_conversation_repositories_hide_system_sessions_by_default() -> None:
    """系统任务会话（source=system）不出现在侧边栏列表，但消息仍可读、可显式列出。"""
    engine = build_engine()
    sql = SqlConversationRepository(engine)
    sql.create_session("CONV_USER", "L1", "用户对话")
    sql.save_messages("THREAD_SYS", "L1", [{"role": "user", "content": "请生成复习卡：湿性黏滞"}], source="system")

    assert [item["id"] for item in sql.list_sessions("L1")] == ["CONV_USER"]
    assert {item["id"] for item in sql.list_sessions("L1", include_system=True)} == {
        "CONV_USER",
        "THREAD_SYS",
    }
    assert sql.get_messages("THREAD_SYS", "L1")[0]["content"] == "请生成复习卡：湿性黏滞"

    # 首写者优先：同一会话先以 user 创建，后续 system 写入不得改变来源
    sql.create_session("CONV_USER", "L1", "用户对话", source="system")
    assert [item["id"] for item in sql.list_sessions("L1")] == ["CONV_USER"]
    # 同一会话先以 system 创建（save_messages 首次建行），后续 user 写入不得覆盖
    sql.save_messages("THREAD_SYS2", "L1", [{"role": "user", "content": "x"}], source="system")
    sql.save_messages("THREAD_SYS2", "L1", [{"role": "user", "content": "y"}], source="user")
    assert "THREAD_SYS2" not in [item["id"] for item in sql.list_sessions("L1")]

    in_memory = InMemoryConversationRepository()
    in_memory.create_session("CONV_MEM", "L1", "内存对话")
    in_memory.save_messages("THREAD_MEM_SYS", "L1", [{"role": "user", "content": "后台任务"}], source="system")
    assert [item["id"] for item in in_memory.list_sessions("L1")] == ["CONV_MEM"]
    assert {item["id"] for item in in_memory.list_sessions("L1", include_system=True)} == {
        "CONV_MEM",
        "THREAD_MEM_SYS",
    }


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


def test_context_summary_persists_and_loads_latest_across_repositories() -> None:
    summary = {
        "summary": "用户偏好晚间学习，每天最多60分钟。",
        "source_refs": [
            {"ref_type": "conversation_message", "ref_id": "MSG_1"},
            {"ref_type": "conversation_message", "ref_id": "MSG_2"},
        ],
        "preserved_facts": ["晚间学习"],
        "unresolved_questions": [],
        "temporary_constraints": ["每天最多60分钟"],
        "compression_version": "1.0.0",
    }

    in_memory = InMemoryConversationRepository()
    in_memory.create_session("CONV_SUM", "L1", "会话")
    in_memory.save_messages("CONV_SUM", "L1", [
        {"message_id": "MSG_1", "role": "user", "content": "我偏好晚间学习"},
        {"message_id": "MSG_2", "role": "assistant", "content": "已记录"},
    ])
    assert in_memory.get_latest_context_summary("CONV_SUM", "L1") is None
    in_memory.save_context_summary("CONV_SUM", "L1", "EXE_1", summary)
    loaded = in_memory.get_latest_context_summary("CONV_SUM", "L1")
    assert loaded["summary"] == summary["summary"]
    assert [ref["ref_id"] for ref in loaded["source_refs"]] == ["MSG_1", "MSG_2"]
    # A newer summary replaces the older one.
    in_memory.save_context_summary("CONV_SUM", "L1", "EXE_2", {
        **summary,
        "summary": "新增方剂背诵侧重。",
    })
    assert in_memory.get_latest_context_summary("CONV_SUM", "L1")["summary"] == (
        "新增方剂背诵侧重。"
    )
    # Cross-learner access is rejected.
    assert in_memory.get_latest_context_summary("CONV_SUM", "OTHER") is None

    engine = build_engine()
    sql = SqlConversationRepository(engine)
    sql.create_session("CONV_SUM_SQL", "L1", "会话")
    sql.save_messages("CONV_SUM_SQL", "L1", [
        {"message_id": "MSG_1", "role": "user", "content": "我偏好晚间学习"},
        {"message_id": "MSG_2", "role": "assistant", "content": "已记录"},
    ])
    assert sql.get_latest_context_summary("CONV_SUM_SQL", "L1") is None
    sql.save_context_summary("CONV_SUM_SQL", "L1", "EXE_1", summary)
    loaded_sql = sql.get_latest_context_summary("CONV_SUM_SQL", "L1")
    assert loaded_sql["summary"] == summary["summary"]
    assert loaded_sql["execution_id"] == "EXE_1"
    assert [ref["ref_id"] for ref in loaded_sql["source_refs"]] == ["MSG_1", "MSG_2"]
    sql.save_context_summary("CONV_SUM_SQL", "L1", "EXE_2", {
        **summary,
        "summary": "第二版摘要",
    })
    assert sql.get_latest_context_summary("CONV_SUM_SQL", "L1")["summary"] == "第二版摘要"
    # A summary without a session owner row is not written.
    sql.save_context_summary("CONV_UNKNOWN", "OTHER", "EXE_3", summary)
    assert sql.get_latest_context_summary("CONV_UNKNOWN", "OTHER") is None


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

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

from competition_app.contracts.learning_plan import (
    DailyTaskItemSpec,
    LearningPlanResult,
    LearningTask,
    LongTermPlan,
    ShortTermLearningPackage,
    ShortTermPlan,
    TextbookSelectionContext,
)
from competition_app.contracts.default_route import (
    DefaultRoutePhase,
    ResolvedPlanningRoute,
)
from competition_app.repositories.learning_plan import SqlLearningPlanRepository
from competition_app.services.daily_task_execution import (
    DailyTaskExecutionCoordinator,
    daily_task_progress_request,
    executable_publish_payload,
    split_executable_items,
)


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


def test_dispatch_pending_parks_event_after_max_attempts_and_logs(caplog) -> None:
    """发布持续失败必须有上限且有日志，不能静默无限重试。

    回归护栏：此前失败只写 last_error 并重置 pending，既无上限也无日志，
    线上 attempt_count 累到 7 仍无人可见，学习者整张今日任务无法完成。
    """

    engine = build_engine()
    repository = SqlLearningPlanRepository(engine)
    learner_id = "LEARNER_1"
    repository.save_current(learner_id, build_plan(learner_id))

    fake_runtime = FakeBackendHandoffRuntime()
    fake_runtime.failures = 10_000
    coordinator = DailyTaskExecutionCoordinator(
        engine=engine,
        plan_repository=repository,
        backend_handoff_runtime=fake_runtime,
    )

    max_attempts = DailyTaskExecutionCoordinator._MAX_PUBLISH_ATTEMPTS
    with caplog.at_level(logging.WARNING):
        for _ in range(max_attempts):
            assert coordinator.dispatch_pending(learner_id, limit=20) == 0

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT status, attempt_count FROM learning_task_sync_outbox WHERE learner_id=:learner_id"
            ),
            {"learner_id": learner_id},
        ).one()
    assert row[0] == DailyTaskExecutionCoordinator._PARKED_STATUS
    assert row[1] == max_attempts

    # 停靠后不再重试，且每次失败都留痕（最后一次必须升级为 ERROR）
    assert coordinator.dispatch_pending(learner_id, limit=20) == 0
    assert len(fake_runtime.calls) == max_attempts
    assert any(
        "daily task publication failed" in record.getMessage()
        for record in caplog.records
    )
    assert any(record.levelno >= logging.ERROR for record in caplog.records)


def test_ensure_current_snapshot_degrades_instead_of_raising(caplog) -> None:
    """快照自愈失败必须降级返回并留日志，不能抛出。

    该自愈与其余刷新步骤共用在 /learning-plans/current 的同一个 try 里，
    抛出会把学习者今日任务进度整体清空；
    /learning-tasks/current/refresh 无异常处理，会直接 500。
    """

    engine = build_engine()
    repository = SqlLearningPlanRepository(engine)
    learner_id = "LEARNER_1"
    repository.save_current(learner_id, build_plan(learner_id))

    fake_runtime = FakeBackendHandoffRuntime()
    fake_runtime.failures = 10_000
    coordinator = DailyTaskExecutionCoordinator(
        engine=engine,
        plan_repository=repository,
        backend_handoff_runtime=fake_runtime,
    )

    with caplog.at_level(logging.WARNING):
        assert coordinator.ensure_current_snapshot(learner_id) is False

    assert any(
        "daily task snapshot repair failed" in record.getMessage()
        for record in caplog.records
    )


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


def test_completed_daily_task_automatically_passes_single_block_short_plan() -> None:
    engine = build_engine()
    repository = SqlLearningPlanRepository(engine)
    learner_id = "LEARNER_SHORT_GATE"
    plan = build_plan(learner_id)
    short = plan.short_term_plan.model_copy(
        update={
            "short_term_learning_package": ShortTermLearningPackage(
                current_goal="完成四君子汤学习",
                task_blocks=["学习四君子汤"],
                expected_output="复述",
                completion_criteria="完成任务",
            )
        }
    )
    repository.save_current(
        learner_id, plan.model_copy(update={"short_term_plan": short})
    )
    coordinator = DailyTaskExecutionCoordinator(
        engine, repository, FakeBackendHandoffRuntime()
    )

    assert coordinator.reconcile_parent_status(learner_id) is True

    stored = repository.get_current(learner_id)
    assert stored.short_term_plan.status == "completed"
    assert stored.short_term_plan.version == 2
    assert stored.learning_task.status == "completed"


def test_verified_exit_evidence_advances_long_stage_and_invalidates_children() -> None:
    engine = build_engine()
    repository = SqlLearningPlanRepository(engine)
    learner_id = "LEARNER_LONG_GATE"
    plan = build_plan(learner_id)
    phases = [
        DefaultRoutePhase(
            phase_id="stage-1",
            name="基础阶段",
            objective="完成基础",
            books=["《中医学基础》"],
            exit_evidence=["完成任务"],
        ),
        DefaultRoutePhase(
            phase_id="stage-2",
            name="方剂阶段",
            objective="学习方剂",
            books=["《方剂学》"],
            exit_evidence=["完成方剂测评"],
        ),
    ]
    long_plan = plan.long_term_plan.model_copy(
        update={
            "planning_route": ResolvedPlanningRoute(
                goal_type="credential",
                goal_name="中医执业医师资格考试",
                planning_status="approved_route",
                match_reason="测试路线",
                route_id="route-1",
                route_version=1,
                route_status="approved",
                phases=phases,
            ),
            "textbook_selection": TextbookSelectionContext(
                route_id="route-1",
                route_version=1,
                stage_id="stage-1",
                stage_name="基础阶段",
                books=["《中医学基础》"],
                reason="当前阶段",
            ),
        }
    )
    repository.save_current(
        learner_id, plan.model_copy(update={"long_term_plan": long_plan})
    )
    runtime = FakeBackendHandoffRuntime()
    runtime.progression_events = []
    runtime.record_plan_progression_event = (
        lambda owner, event: runtime.progression_events.append((owner, event))
    )
    coordinator = DailyTaskExecutionCoordinator(engine, repository, runtime)

    assert coordinator.reconcile_parent_status(learner_id) is True

    stored = repository.get_current(learner_id)
    assert stored.long_term_plan.textbook_selection.stage_id == "stage-2"
    assert stored.long_term_plan.stage_evidence[0].requirement == "完成任务"
    assert stored.short_term_plan is None
    assert stored.learning_task is None
    assert runtime.progression_events[0][1]["next_stage"] == 2


def test_ensure_current_snapshot_redelivers_current_version_idempotently() -> None:
    engine = build_engine()
    repository = SqlLearningPlanRepository(engine)
    learner_id = "LEARNER_REPAIR"
    repository.save_current(learner_id, build_plan(learner_id))
    runtime = FakeBackendHandoffRuntime()
    runtime.failures = 0
    coordinator = DailyTaskExecutionCoordinator(engine, repository, runtime)

    assert coordinator.ensure_current_snapshot(learner_id) is True
    assert runtime.calls[-1][1]["task_id"] == "TASK_1"
    assert runtime.calls[-1][1]["version"] == 1


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


def _legacy_recall_item(ordinal: int, *, task_item_id: str = "DTI_LEGACY") -> DailyTaskItemSpec:
    return DailyTaskItemSpec(
        task_item_id=task_item_id,
        ordinal=ordinal,
        item_type="recall",
        title="回顾：君臣佐使",
        estimated_minutes=1.0,
    )


def build_plan_with_legacy_item(learner_id: str) -> LearningPlanResult:
    plan = build_plan(learner_id)
    task = plan.learning_task
    items = list(task.items) + [_legacy_recall_item(len(task.items) + 1)]
    return plan.model_copy(
        update={
            "learning_task": task.model_copy(
                update={
                    "items": items,
                    "estimated_minutes": sum(item.estimated_minutes for item in items),
                }
            )
        }
    )


def test_split_executable_items_separates_labels_without_a_completion_path() -> None:
    kept, dropped = split_executable_items(
        [
            {"task_item_id": "A", "item_type": "knowledge_practice"},
            {"task_item_id": "B", "item_type": "recall"},
            {"task_item_id": "C", "item_type": "reading"},
            {"task_item_id": "D", "item_type": "video_section"},
        ]
    )

    assert [item["task_item_id"] for item in kept] == ["A", "D"]
    assert [item["task_item_id"] for item in dropped] == ["B", "C"]


def test_executable_publish_payload_drops_items_without_a_completion_path(caplog) -> None:
    payload = {
        "task_id": "TASK_1",
        "version": 3,
        "items": [
            {"task_item_id": "DTI_1", "item_type": "knowledge_practice"},
            {"task_item_id": "DTI_LEGACY", "item_type": "recall"},
        ],
    }

    with caplog.at_level(logging.WARNING):
        filtered = executable_publish_payload(payload, learner_id="L1", source="test")

    assert [item["task_item_id"] for item in filtered["items"]] == ["DTI_1"]
    assert filtered["version"] == 3
    assert payload["items"][1]["task_item_id"] == "DTI_LEGACY"
    assert any(
        "dropped items without a completion path" in record.getMessage()
        for record in caplog.records
    )


def test_executable_publish_payload_passes_through_when_all_items_are_executable() -> None:
    payload = {
        "items": [{"task_item_id": "DTI_1", "item_type": "knowledge_practice"}]
    }

    assert executable_publish_payload(payload, learner_id="L1", source="test") is payload
    assert executable_publish_payload("not-a-dict", learner_id="L1", source="t") == "not-a-dict"
    assert executable_publish_payload(
        {"items": []}, learner_id="L1", source="t"
    ) == {"items": []}


def test_daily_task_progress_request_omits_items_without_a_completion_path(caplog) -> None:
    task = build_plan_with_legacy_item("LEARNER_PROGRESS").learning_task

    with caplog.at_level(logging.WARNING):
        payload = daily_task_progress_request(task)

    assert [item["task_item_id"] for item in payload["items"]] == ["DTI_1"]
    assert payload["host_task_id"] == task.task_id
    assert any(
        "ignored items without a completion path" in record.getMessage()
        for record in caplog.records
    )


def test_dispatch_pending_filters_legacy_items_out_of_a_stored_payload() -> None:
    """修复前写入 outbox 的载荷也必须被过滤，否则重试依旧整版失败。"""

    engine = build_engine()
    repository = SqlLearningPlanRepository(engine)
    learner_id = "LEARNER_LEGACY_OUTBOX"
    repository.save_current(learner_id, build_plan_with_legacy_item(learner_id))

    runtime = FakeBackendHandoffRuntime()
    runtime.failures = 0
    coordinator = DailyTaskExecutionCoordinator(engine, repository, runtime)

    assert coordinator.dispatch_pending(learner_id, limit=20) == 1

    published = runtime.calls[-1][1]
    assert [item["task_item_id"] for item in published["items"]] == ["DTI_1"]


def test_ensure_current_snapshot_converges_stored_task_onto_executable_items(caplog) -> None:
    """已落库的历史任务也必须自愈：前端按计划层渲染，不收敛就还是死链。"""

    engine = build_engine()
    repository = SqlLearningPlanRepository(engine)
    learner_id = "LEARNER_CONVERGE"
    repository.save_current(learner_id, build_plan_with_legacy_item(learner_id))

    runtime = FakeBackendHandoffRuntime()
    runtime.failures = 0
    coordinator = DailyTaskExecutionCoordinator(engine, repository, runtime)

    with caplog.at_level(logging.WARNING):
        assert coordinator.ensure_current_snapshot(learner_id) is True

    stored = repository.get_current(learner_id).learning_task
    assert [item.task_item_id for item in stored.items] == ["DTI_1"]
    assert [item.ordinal for item in stored.items] == [1]
    assert stored.version == 2
    assert stored.estimated_minutes == 10
    assert "回顾：君臣佐使" in stored.task_content
    assert any(
        "converging stored daily task onto executable items" in record.getMessage()
        for record in caplog.records
    )

    published = runtime.calls[-1][1]
    assert [item["task_item_id"] for item in published["items"]] == ["DTI_1"]

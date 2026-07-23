from datetime import datetime, timedelta, timezone

from competition_app.contracts.learning_plan import (
    LearningPlanResult,
    LearningTask,
    ShortTermLearningPackage,
    ShortTermPlan,
    ShortTermTaskBlock,
)
from competition_app.repositories.learning_plan import InMemoryLearningPlanRepository
from competition_app.services.daily_task_refresh import DailyTaskRefreshService


def _state(now: datetime, *, due_at: datetime | None) -> LearningPlanResult:
    learner_id = "learner-daily-refresh"
    package = ShortTermLearningPackage(
        current_goal="本周掌握补气剂",
        task_blocks=[
            ShortTermTaskBlock(content="学习四君子汤", estimated_minutes=25),
            ShortTermTaskBlock(content="辨析四君子汤与参苓白术散", estimated_minutes=35),
        ],
        expected_output="完成一份辨析记录",
        completion_criteria="核心辨析点正确率达到 80%",
    )
    short_plan = ShortTermPlan(
        plan_id="SHORT_REFRESH",
        learner_id=learner_id,
        long_term_plan_id="LONG_REFRESH",
        content="本周学习补气剂",
        version=1,
        status="active",
        created_at=now,
        updated_at=now,
        short_term_learning_package=package,
    )
    task = LearningTask(
        task_id="TASK_REFRESH_1",
        learner_id=learner_id,
        short_term_plan_id=short_plan.plan_id,
        task_type="daily_learning",
        task_content="学习四君子汤",
        learning_chapter="《方剂学》补益剂",
        focus_knowledge_points=["四君子汤"],
        estimated_minutes=25,
        expected_output="完成知识卡",
        completion_criteria="能够说明组成与配伍",
        version=1,
        status="pending",
        created_at=now,
        updated_at=now,
        refresh_started_at=now if due_at else None,
        refresh_due_at=due_at,
    )
    return LearningPlanResult(short_term_plan=short_plan, learning_task=task)


def test_legacy_task_receives_a_full_24_hour_window_without_being_replaced() -> None:
    now = datetime(2026, 7, 23, 8, tzinfo=timezone.utc)
    repository = InMemoryLearningPlanRepository()
    repository.save_current("learner-daily-refresh", _state(now, due_at=None))

    timer = DailyTaskRefreshService(repository).ensure_current(
        "learner-daily-refresh", now=now
    )

    stored = repository.get_current("learner-daily-refresh").learning_task
    assert timer["refreshed"] is False
    assert timer["remaining_seconds"] == 24 * 60 * 60
    assert stored.task_id == "TASK_REFRESH_1"
    assert stored.refresh_due_at == now + timedelta(hours=24)


def test_overdue_task_rolls_to_next_short_term_block_once() -> None:
    now = datetime(2026, 7, 23, 8, tzinfo=timezone.utc)
    repository = InMemoryLearningPlanRepository()
    repository.save_current(
        "learner-daily-refresh",
        _state(now - timedelta(hours=25), due_at=now - timedelta(hours=1)),
    )
    service = DailyTaskRefreshService(repository)

    first = service.ensure_current("learner-daily-refresh", now=now)
    second = service.ensure_current("learner-daily-refresh", now=now)

    stored = repository.get_current("learner-daily-refresh").learning_task
    assert first["refreshed"] is True
    assert first["previous_task_id"] == "TASK_REFRESH_1"
    assert second["refreshed"] is False
    assert stored.task_content == "辨析四君子汤与参苓白术散"
    assert stored.estimated_minutes == 35
    assert stored.status == "pending"
    assert stored.refresh_due_at == now + timedelta(hours=24)
    assert second["current_task_id"] == stored.task_id

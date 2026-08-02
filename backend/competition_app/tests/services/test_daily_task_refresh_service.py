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
            ShortTermTaskBlock(
                content="辨析四君子汤与参苓白术散",
                estimated_minutes=35,
                item_type="video_section",
                resource_ref={
                    "trusted_resource_id": "VIDEO_REFRESH_2",
                    "provider": "bilibili",
                    "bvid": "BV_REFRESH_2",
                    "start_seconds": 30,
                    "end_seconds": 150,
                },
            ),
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
        items=[
            {
                "task_item_id": "DTI_REFRESH_1",
                "ordinal": 1,
                "item_type": "knowledge_practice",
                "title": "完成四君子汤练习",
                "estimated_minutes": 25,
                "kp_id": "KP_FJ_001",
                "required_question_count": 3,
                "completion_policy": {"policy": "frozen_question_set"},
            }
        ],
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
    service = DailyTaskRefreshService(
        repository,
        video_resource_resolver=lambda resource_ref: (
            resource_ref if resource_ref.get("trusted_resource_id") else None
        ),
    )

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
    assert stored.items[0].task_item_id != "DTI_REFRESH_1"
    assert stored.items[0].task_item_id.startswith("DTI_")
    assert stored.items[0].item_type == "video_section"
    assert stored.items[0].resource_ref["trusted_resource_id"] == "VIDEO_REFRESH_2"


def test_overdue_task_applies_system_task_load_policy_without_filling_budget() -> None:
    now = datetime(2026, 7, 23, 8, tzinfo=timezone.utc)
    repository = InMemoryLearningPlanRepository()
    repository.save_current(
        "learner-daily-refresh",
        _state(now - timedelta(hours=25), due_at=now - timedelta(hours=1)),
    )
    policy_calls = []
    service = DailyTaskRefreshService(
        repository,
        video_resource_resolver=lambda resource_ref: (
            dict(resource_ref, duration_seconds=120)
            if resource_ref.get("trusted_resource_id")
            else None
        ),
        task_load_policy_loader=lambda learner_id, **kwargs: (
            policy_calls.append((learner_id, kwargs))
            or {
                "policy_id": "next-day-load-v1",
                "recommended_minutes": 20,
                "direction": "decrease",
            }
        ),
    )

    result = service.ensure_current("learner-daily-refresh", now=now)
    stored = repository.get_current("learner-daily-refresh").learning_task

    assert stored.estimated_minutes == 20
    # 视频原子项按真实时长占位（30s→150s，120 秒 = 2 分钟），
    # 其余预算分配给配套复习项，总和不超过任务预算。
    assert stored.items[0].estimated_minutes == 2
    assert sum(item.estimated_minutes for item in stored.items) == 20
    assert result["task_load_policy"]["direction"] == "decrease"
    assert policy_calls[0][1]["plan_context"]["learning_task"]["task_id"] == (
        "TASK_REFRESH_1"
    )

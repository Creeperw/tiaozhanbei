from datetime import datetime, timezone

from competition_app.contracts.learning_plan import (
    DailyTaskItemSpec,
    LearningPlanResult,
    LearningTask,
)
from competition_app.services.plan_review_apply import apply_accepted_plan_review
from competition_app.services.plan_review_replan import PlanReviewReplanCoordinator

NOW = datetime(2026, 8, 16, 8, 0, 0, tzinfo=timezone.utc)


def _practice_item(ordinal, count, quiz=False):
    return DailyTaskItemSpec(
        task_item_id=f"ITM_{ordinal}",
        ordinal=ordinal,
        item_type="knowledge_practice",
        title=f"知识点练习{ordinal}",
        estimated_minutes=count * 1.5,
        kp_id=f"KP_{ordinal}",
        knowledge_point_name=f"知识点{ordinal}",
        required_question_count=count,
        completion_policy={"policy": "frozen_question_set", "quiz": quiz},
    )


def _video_item(ordinal=1):
    return DailyTaskItemSpec(
        task_item_id="ITM_V1",
        ordinal=ordinal,
        item_type="video_section",
        title="观看章节视频",
        estimated_minutes=4.0,
        resource_ref={
            "source": "knowledge_atlas",
            "video_id": "V1",
            "start_seconds": 0,
            "end_seconds": 240,
        },
        completion_policy={"policy": "html5_coverage"},
    )


def _task():
    return LearningTask(
        task_id="TASK_1",
        learner_id="USER_1",
        short_term_plan_id="STP_1",
        task_type="daily",
        task_content="今日围绕《中医学基础》绪论学习：观看章节视频；完成知识点练习。",
        learning_chapter="《中医学基础》绪论",
        focus_knowledge_points=["知识点1"],
        estimated_minutes=19.0,
        expected_output="1条章节视频观看记录与3道配套题提交记录",
        completion_criteria="完成全部2个原子任务",
        version=3,
        status="active",
        created_at=NOW,
        updated_at=NOW,
        items=[_video_item(1), _practice_item(2, 10)],
    )


class FakeRepository:
    def __init__(self, saved_result=True):
        self.saved_result = saved_result
        self.saved = []

    def save_current(self, learner_id, plans, **kwargs):
        self.saved.append((learner_id, plans, kwargs))
        return self.saved_result


class FakePlanService:
    def __init__(self, current=None, saved_result=True):
        self._current = current
        self.plan_repository = FakeRepository(saved_result=saved_result)

    def get_current(self, learner_id):
        return self._current


def _review(operation="reduce_load", target_layer="daily_task", summary="", evidence=None):
    return {
        "review_id": "PLAN_REVIEW_1",
        "outcome": "daily_adjustment_suggested",
        "summary": summary or "近期任务完成率偏低，建议减少今日任务数量。",
        "evidence": evidence or ["任务完成率 12%", "到期复习 2 个知识点"],
        "proposal": {
            "target_layer": target_layer,
            "operation": operation,
            "requires_confirmation": operation not in {"reduce_load"},
            "workflow_request": {
                "task_type": "learning_plan",
                "plan_scope": target_layer,
                "user_request": "请结合近期低完成率的监控证据，强制调整我的短期计划。",
            },
        },
        "input_snapshot": {
            "dimensions": {"execution": 0.12, "mastery": 0.77},
            "low_completion_streak_days": 2,
            "agent_decision": None,
        },
    }


def test_reduce_load_trims_practice_questions():
    service = FakePlanService(current=LearningPlanResult(learning_task=_task()))
    result = apply_accepted_plan_review(service, "USER_1", _review())

    assert result["applied"] is True
    assert "10道减至" in result["summary"]

    task = service.plan_repository.saved[0][1].learning_task
    assert task.version == 4
    practice = [i for i in task.items if i.item_type == "knowledge_practice"][0]
    assert practice.required_question_count < 10
    assert practice.ordinal == 2
    video = task.items[0]
    assert video.item_type == "video_section"
    assert "今日减负" in task.task_content
    assert "完成全部2个原子任务" in task.completion_criteria


def test_reduce_load_keeps_video_and_quiz():
    task = _task()
    task.items = [
        _video_item(1),
        _practice_item(2, 6, quiz=True),
        _practice_item(3, 8),
    ]
    task.estimated_minutes = 4.0 + 6 * 1.5 + 8 * 1.5
    service = FakePlanService(current=LearningPlanResult(learning_task=task))
    result = apply_accepted_plan_review(service, "USER_1", _review())

    assert result["applied"] is True
    saved = service.plan_repository.saved[0][1].learning_task
    quiz = [i for i in saved.items if i.completion_policy.get("quiz")]
    video = [i for i in saved.items if i.item_type == "video_section"]
    assert len(quiz) == 1 and quiz[0].required_question_count == 6
    assert len(video) == 1


def test_reduce_load_noop_when_already_lean():
    task = _task()
    task.items = [_video_item(1), _practice_item(2, 1)]
    service = FakePlanService(current=LearningPlanResult(learning_task=task))
    result = apply_accepted_plan_review(service, "USER_1", _review())

    assert result["applied"] is False
    assert "已很精简" in result["reason"]
    assert service.plan_repository.saved == []


def test_add_review_window_appends_recall_item():
    service = FakePlanService(current=LearningPlanResult(learning_task=_task()))
    started = []
    result = apply_accepted_plan_review(
        service,
        "USER_1",
        _review(operation="add_review_window", target_layer="short_term"),
        replan_starter=lambda learner_id, review: started.append((learner_id, review["review_id"])),
    )

    assert result["applied"] is True
    assert result["replan_started"] is True
    assert started == [("USER_1", "PLAN_REVIEW_1")]
    assert service.plan_repository.saved == []


def test_add_review_window_skips_duplicate():
    task = _task()
    task.items = list(task.items) + [
        DailyTaskItemSpec(
            task_item_id="ITM_R1",
            ordinal=3,
            item_type="recall",
            title="到期复习：知识点",
            estimated_minutes=15.0,
            resource_ref={"review_id": "PLAN_REVIEW_1", "source": "plan_review"},
        )
    ]
    task.estimated_minutes = 19.0 + 15.0
    service = FakePlanService(current=LearningPlanResult(learning_task=task))
    started = []
    result = apply_accepted_plan_review(
        service,
        "USER_1",
        _review(operation="add_review_window", target_layer="short_term"),
        replan_starter=lambda learner_id, review: started.append((learner_id, review["review_id"])),
    )

    assert result["applied"] is True
    assert result["replan_started"] is True
    assert started == [("USER_1", "PLAN_REVIEW_1")]
    assert service.plan_repository.saved == []


def test_replan_starts_async_and_reports_started():
    started = []

    def starter(learner_id, review):
        started.append((learner_id, review["review_id"]))

    service = FakePlanService(current=LearningPlanResult(learning_task=_task()))
    result = apply_accepted_plan_review(
        service,
        "USER_1",
        _review(operation="replan_for_low_completion", target_layer="short_term"),
        replan_starter=starter,
    )

    assert result["applied"] is True
    assert "重规划" in result["summary"]
    assert started == [("USER_1", "PLAN_REVIEW_1")]
    assert service.plan_repository.saved == []


def test_replan_without_starter_reports_unavailable():
    service = FakePlanService(current=LearningPlanResult(learning_task=_task()))
    result = apply_accepted_plan_review(
        service,
        "USER_1",
        _review(operation="replan_for_low_completion", target_layer="short_term"),
        replan_starter=None,
    )

    assert result["applied"] is False
    assert "不可用" in result["reason"]


def test_slow_progress_combines_reduce_and_replan():
    started = []

    def starter(learner_id, review):
        started.append(learner_id)

    service = FakePlanService(current=LearningPlanResult(learning_task=_task()))
    result = apply_accepted_plan_review(
        service,
        "USER_1",
        _review(operation="slow_progress", target_layer="short_term"),
        replan_starter=starter,
    )

    assert result["applied"] is True
    assert result.get("replan_started") is True
    assert started == ["USER_1"]
    assert service.plan_repository.saved == []  # 减负由级联协调器在短期更新后统一重建


def test_unknown_operation_is_noop():
    service = FakePlanService(current=LearningPlanResult(learning_task=_task()))
    result = apply_accepted_plan_review(
        service, "USER_1", _review(operation="keep_current", target_layer="daily_task")
    )

    assert result["applied"] is False
    assert service.plan_repository.saved == []


def test_short_term_replan_coordinator_cascades_to_daily_task():
    class Plan:
        def __init__(self, plan_id, version):
            self.plan_id = plan_id
            self.version = version

    class Task:
        task_id = "TASK_NEW"
        version = 2
        short_term_plan_id = "STP_NEW"

    class Result:
        def __init__(self, short_term_plan=None, learning_task=None, invalidated_layers=None):
            self.short_term_plan = short_term_plan
            self.learning_task = learning_task
            self.invalidated_layers = invalidated_layers or []

    class Service:
        def __init__(self):
            self.short = Plan("STP_OLD", 3)
            self.task = Task()
            self.task.task_id = "TASK_OLD"

        def get_current(self, learner_id):
            return type(
                "Current",
                (),
                {"short_term_plan": self.short, "learning_task": self.task},
            )()

    calls = []

    service = Service()

    class UseCase:
        async def execute(self, request):
            calls.append((request.plan_scope, request.operation_id, request.system_operation))
            if request.plan_scope == "short_term":
                service.short = Plan("STP_NEW", 4)
                service.task = None
                return Result(short_term_plan=Plan("STP_NEW", 4), invalidated_layers=["daily_task"])
            service.task = Task()
            return Result(learning_task=service.task)

    import asyncio

    result = asyncio.run(
        PlanReviewReplanCoordinator(
            review_card_use_case=UseCase(),
            plan_service=service,
        ).run(
            "USER_1",
            {
                "review_id": "PLAN_REVIEW_1",
                "summary": "低完成率",
                "proposal": {
                    "workflow_request": {
                        "user_request": "请调整短期计划",
                    }
                },
            },
        )
    )

    assert result["generated_scopes"] == ["short_term", "daily_task"]
    assert result["short_term_version"] == 4
    assert calls == [
        ("short_term", "plan-review:PLAN_REVIEW_1:short-term-v1", "plan_review_replan"),
        ("daily_task", "plan-review:PLAN_REVIEW_1:daily-task-v1", "plan_review_replan"),
    ]


def test_short_term_replan_coordinator_unwraps_review_card_result_envelope():
    class Plan:
        def __init__(self, plan_id, version):
            self.plan_id = plan_id
            self.version = version

    class Task:
        task_id = "TASK_NEW"
        version = 2
        short_term_plan_id = "STP_NEW"

    class LearningPlan:
        def __init__(self, short_term_plan=None, learning_task=None, invalidated_layers=None):
            self.short_term_plan = short_term_plan
            self.learning_task = learning_task
            self.invalidated_layers = invalidated_layers or []

    class ReviewCardResult:
        def __init__(self, learning_plan):
            self.learning_plan = learning_plan

    class Service:
        def __init__(self):
            self.short = Plan("STP_OLD", 3)
            self.task = Task()
            self.task.task_id = "TASK_OLD"

        def get_current(self, learner_id):
            return type(
                "Current",
                (),
                {"short_term_plan": self.short, "learning_task": self.task},
            )()

    service = Service()

    class UseCase:
        async def execute(self, request):
            if request.plan_scope == "short_term":
                service.short = Plan("STP_NEW", 4)
                service.task = None
                return ReviewCardResult(
                    LearningPlan(
                        short_term_plan=service.short,
                        invalidated_layers=["daily_task"],
                    )
                )
            service.task = Task()
            return ReviewCardResult(LearningPlan(learning_task=service.task))

    import asyncio

    result = asyncio.run(
        PlanReviewReplanCoordinator(
            review_card_use_case=UseCase(),
            plan_service=service,
        ).run(
            "USER_1",
            {
                "review_id": "PLAN_REVIEW_ENVELOPE",
                "summary": "低完成率",
                "proposal": {"workflow_request": {"user_request": "请调整短期计划"}},
            },
        )
    )

    assert result["short_term_plan_id"] == "STP_NEW"
    assert result["daily_task_id"] == "TASK_NEW"

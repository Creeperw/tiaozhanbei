from datetime import datetime, timezone

from competition_app.contracts.learning_plan import (
    DailyTaskItemSpec,
    LearningPlanResult,
    LearningTask,
)
from competition_app.services.intervention_apply import (
    INTERVENTION_ADDED_MINUTES,
    apply_accepted_intervention,
)

NOW = datetime(2026, 8, 16, 8, 0, 0, tzinfo=timezone.utc)


def _task(*, items=(), task_content="今日学习：基础知识。"):
    return LearningTask(
        task_id="TASK_1",
        learner_id="USER_1",
        short_term_plan_id="STP_1",
        task_type="daily",
        task_content=task_content,
        estimated_minutes=60.0,
        expected_output="提交练习",
        completion_criteria="完成全部任务",
        version=3,
        status="active",
        created_at=NOW,
        updated_at=NOW,
        items=list(items),
    )


def _practice_item(ordinal=1, count=10):
    return DailyTaskItemSpec(
        task_item_id=f"ITM_{ordinal}",
        ordinal=ordinal,
        item_type="knowledge_practice",
        title="完成核心练习",
        estimated_minutes=float(count),
        per_question_estimated_minutes=1.0,
        knowledge_point_name="舌诊",
        kp_id="KP_1",
        required_question_count=count,
        resource_ref={},
        completion_policy={"policy": "frozen_question_set"},
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


_INTERVENTION = {
    "intervention_id": 13,
    "action": "安排错题复盘",
    "reason": "系统根据近期学习监控判断当前处于“错题积压”。存在9个到期复习"
    "和重复出现的薄弱知识点（中医诊断学·舌诊、四君子汤）。",
    "trigger_snapshot": {
        "overview": {"stage_id": "T5", "stage_name": "错题积压"},
        "agent_decision": {
            "decide": "adjust",
            "adjustment": {
                "target_layer": "daily_task",
                "operation": "add_review_window",
                "summary": "建议今日安排错题复盘，重点复习舌诊与四君子汤。",
            },
        },
    },
}


def test_applies_review_intervention_as_daily_task_item():
    service = FakePlanService(current=LearningPlanResult(learning_task=_task()))
    result = apply_accepted_intervention(service, "USER_1", _INTERVENTION)

    assert result["applied"] is True
    assert result["title"] == "错题复盘：中医诊断学·舌诊、四君子汤"
    assert result["summary"].startswith("建议今日安排错题复盘")

    task = service.plan_repository.saved[0][1].learning_task
    assert task.version == 4
    assert task.estimated_minutes == 60.0 + INTERVENTION_ADDED_MINUTES
    assert len(task.items) == 1
    item = task.items[0]
    assert item.item_type == "recall"
    assert item.ordinal == 1
    assert item.estimated_minutes == INTERVENTION_ADDED_MINUTES
    assert item.resource_ref["intervention_id"] == "13"
    assert item.resource_ref["source"] == "learning_intervention"
    assert "错题复盘：中医诊断学·舌诊、四君子汤" in task.task_content


def test_skips_duplicate_application_of_same_intervention():
    existing = DailyTaskItemSpec(
        task_item_id="ITM_INTERV_aaaa",
        ordinal=1,
        item_type="recall",
        title="错题复盘：中医诊断学·舌诊、四君子汤",
        estimated_minutes=15.0,
        resource_ref={"intervention_id": "13", "source": "learning_intervention"},
    )
    service = FakePlanService(
        current=LearningPlanResult(learning_task=_task(items=[existing]))
    )
    result = apply_accepted_intervention(service, "USER_1", _INTERVENTION)

    assert result["applied"] is False
    assert result["already_applied"] is True
    assert "已经安排" in result["reason"]
    assert service.plan_repository.saved == []


def test_does_not_apply_non_actionable_intervention():
    intervention = {
        **_INTERVENTION,
        "action": "保持当前计划",
        "trigger_snapshot": {},
    }
    service = FakePlanService(current=LearningPlanResult(learning_task=_task()))
    result = apply_accepted_intervention(service, "USER_1", intervention)

    assert result["applied"] is False
    assert result["retryable"] is False
    assert service.plan_repository.saved == []


def test_does_not_apply_without_daily_task():
    service = FakePlanService(current=LearningPlanResult(learning_task=None))
    result = apply_accepted_intervention(service, "USER_1", _INTERVENTION)

    assert result["applied"] is False
    assert result["retryable"] is True
    assert "没有进行中的每日任务" in result["reason"]
    assert service.plan_repository.saved == []


def test_reports_when_save_conflicts():
    service = FakePlanService(
        current=LearningPlanResult(learning_task=_task()), saved_result=False
    )
    result = apply_accepted_intervention(service, "USER_1", _INTERVENTION)

    assert result["applied"] is False
    assert result["retryable"] is True
    assert "请重试" in result["reason"]


def test_reads_flat_normalized_agent_summary_and_operation():
    intervention = {
        **_INTERVENTION,
        "action": "任意展示文案",
        "trigger_snapshot": {
            "agent_decision": {
                "decide": "adjust",
                "operation": "add_mistake_review",
                "summary": "建议复盘薄弱知识点（舌诊）。",
            },
        },
    }
    service = FakePlanService(current=LearningPlanResult(learning_task=_task()))

    result = apply_accepted_intervention(service, "USER_1", intervention)

    assert result["applied"] is True
    assert result["summary"] == "建议复盘薄弱知识点（舌诊）。"
    assert result["title"] == "错题复盘：舌诊"


def test_applies_structured_reduce_load_operation():
    intervention = {
        "intervention_id": 22,
        "action": "恢复学习节奏",
        "reason": "近期任务完成率偏低。",
        "trigger_snapshot": {
            "final_recommendation": {
                "execution_operation": "reduce_load",
                "actionable": True,
            },
        },
    }
    service = FakePlanService(
        current=LearningPlanResult(learning_task=_task(items=[_practice_item()]))
    )

    result = apply_accepted_intervention(service, "USER_1", intervention)

    assert result["applied"] is True
    assert result["title"] == "今日任务减负"
    saved_task = service.plan_repository.saved[0][1].learning_task
    assert saved_task.items[0].required_question_count == 7


def test_reduce_load_replay_is_idempotent_for_same_intervention():
    intervention = {
        "intervention_id": 22,
        "action": "恢复学习节奏",
        "reason": "近期任务完成率偏低。",
        "trigger_snapshot": {
            "final_recommendation": {
                "execution_operation": "reduce_load",
                "actionable": True,
            },
        },
    }
    service = FakePlanService(
        current=LearningPlanResult(learning_task=_task(items=[_practice_item()]))
    )
    first = apply_accepted_intervention(service, "USER_1", intervention)
    service._current = service.plan_repository.saved[0][1]

    replay = apply_accepted_intervention(service, "USER_1", intervention)

    assert first["applied"] is True
    assert replay["applied"] is False
    assert replay["already_applied"] is True
    assert len(service.plan_repository.saved) == 1
    assert service._current.learning_task.items[0].required_question_count == 7

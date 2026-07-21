from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from competition_app.contracts.learning_plan import (
    GoalContract,
    LearningPlanProposal,
    LearningPlanResult,
    LearningTask,
    LearningTaskProposal,
    LongTermPlan,
    PlanMilestone,
    RecommendationTrace,
    RecoveryPolicy,
    ShortTermFocusContext,
    ShortTermLearningPackage,
    ShortTermPlan,
)


def task_proposal_data() -> dict[str, object]:
    return {
        "task_type": "review",
        "task_content": "复习阴阳五行基础概念",
        "estimated_minutes": 30,
        "expected_output": "完成一份概念对照表",
        "completion_criteria": "概念匹配正确率达到 80%",
    }


def long_term_content() -> str:
    return (
        "【最终目标】掌握中医基础理论。"
        "【能力路径与阶段】基础概念→辨证应用。"
        "【阶段里程碑】完成闭卷说明；截止时间待确认。"
        "【资源预算】最低投入和缓冲时间待确认。"
        "【重规划条件】连续两次未达标时调整。"
        "【保温底线】每周一次知识卡回忆。"
    )


def short_term_content() -> str:
    return (
        "【当前主目标】复习阴阳五行。"
        "【长期目标保温】保留一次知识卡回忆。"
        "【时间分配】30分钟用于当前任务。"
        "【具体任务块】完成概念对照表，产出对照结果，完成标准为正确率80%。"
        "【复习任务】完成后安排错题回顾。"
        "【反馈指标】记录完成率、正确率和错因。"
    )


def formal_plan_data(*, plan_id: str, long_term_plan_id: str | None = None) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    data: dict[str, object] = {
        "plan_id": plan_id,
        "learner_id": "LEARNER_001",
        "content": "掌握中医基础理论",
        "version": 1,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    if long_term_plan_id is not None:
        data["long_term_plan_id"] = long_term_plan_id
    return data


def formal_task_data() -> dict[str, object]:
    now = datetime.now(timezone.utc)
    return {
        "task_id": "TASK_001",
        "learner_id": "LEARNER_001",
        "short_term_plan_id": "LP_SHORT_001",
        **task_proposal_data(),
        "version": 1,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }


@pytest.mark.parametrize("field", ["long_term_plan_content", "short_term_plan_content"])
def test_learning_plan_proposal_rejects_empty_plan_text(field: str) -> None:
    data = {
        "long_term_plan_content": long_term_content(),
        "short_term_plan_content": short_term_content(),
        "adjustment_reason": "根据当前薄弱知识点安排",
        "task_proposal": task_proposal_data(),
    }
    data[field] = ""

    with pytest.raises(ValidationError):
        LearningPlanProposal.model_validate(data)


@pytest.mark.parametrize("estimated_minutes", [0, -1])
def test_learning_task_proposal_requires_positive_estimated_minutes(
    estimated_minutes: int,
) -> None:
    data = task_proposal_data()
    data["estimated_minutes"] = estimated_minutes

    with pytest.raises(ValidationError):
        LearningTaskProposal.model_validate(data)


def test_formal_learning_task_requires_positive_estimated_minutes() -> None:
    data = formal_task_data()
    data["estimated_minutes"] = 0

    with pytest.raises(ValidationError):
        LearningTask.model_validate(data)


def test_formal_learning_plan_result_contains_system_fields() -> None:
    now = datetime.now(timezone.utc)
    long_term_plan = LongTermPlan(
        plan_id="LP_LONG_001",
        learner_id="LEARNER_001",
        content="三个月内掌握中医基础理论",
        version=1,
        status="active",
        created_at=now,
        updated_at=now,
    )
    short_term_plan = ShortTermPlan(
        plan_id="LP_SHORT_001",
        learner_id="LEARNER_001",
        long_term_plan_id="LP_LONG_001",
        content="本周复习阴阳五行",
        version=1,
        status="active",
        created_at=now,
        updated_at=now,
    )
    task = LearningTask(
        task_id="TASK_001",
        learner_id="LEARNER_001",
        short_term_plan_id="LP_SHORT_001",
        task_type="review",
        task_content="复习阴阳五行基础概念",
        estimated_minutes=30,
        expected_output="完成一份概念对照表",
        completion_criteria="概念匹配正确率达到 80%",
        version=1,
        status="pending",
        created_at=now,
        updated_at=now,
    )

    result = LearningPlanResult(
        long_term_plan=long_term_plan,
        short_term_plan=short_term_plan,
        learning_task=task,
    )

    assert result.long_term_plan.plan_id == "LP_LONG_001"
    assert result.short_term_plan.version == 1
    assert result.learning_task.status == "pending"
    assert result.learning_task.created_at == now


def test_short_term_focus_context_keeps_only_system_owned_selection_facts() -> None:
    focus = ShortTermFocusContext(
        focus_type="due_review",
        focus_label="四君子汤",
        knowledge_point_ids=["KP_FJ_001"],
    )

    assert focus.model_dump() == {
        "focus_type": "due_review",
        "focus_label": "四君子汤",
        "knowledge_point_ids": ["KP_FJ_001"],
    }


@pytest.mark.parametrize(
    ("model", "data", "required_field"),
    [
        (LongTermPlan, formal_plan_data(plan_id="LP_LONG_001"), "plan_id"),
        (LongTermPlan, formal_plan_data(plan_id="LP_LONG_001"), "learner_id"),
        (LongTermPlan, formal_plan_data(plan_id="LP_LONG_001"), "version"),
        (LongTermPlan, formal_plan_data(plan_id="LP_LONG_001"), "status"),
        (LongTermPlan, formal_plan_data(plan_id="LP_LONG_001"), "created_at"),
        (LongTermPlan, formal_plan_data(plan_id="LP_LONG_001"), "updated_at"),
        (
            ShortTermPlan,
            formal_plan_data(plan_id="LP_SHORT_001", long_term_plan_id="LP_LONG_001"),
            "plan_id",
        ),
        (LearningTask, formal_task_data(), "task_id"),
        (LearningTask, formal_task_data(), "learner_id"),
        (LearningTask, formal_task_data(), "version"),
        (LearningTask, formal_task_data(), "status"),
        (LearningTask, formal_task_data(), "created_at"),
        (LearningTask, formal_task_data(), "updated_at"),
    ],
)
def test_formal_entities_require_system_fields(
    model: type[LongTermPlan] | type[ShortTermPlan] | type[LearningTask],
    data: dict[str, object],
    required_field: str,
) -> None:
    data.pop(required_field)

    with pytest.raises(ValidationError):
        model.model_validate(data)


def proposal_data() -> dict[str, object]:
    return {
        "long_term_plan_content": long_term_content(),
        "short_term_plan_content": short_term_content(),
        "adjustment_reason": "根据当前薄弱知识点安排",
        "task_proposal": task_proposal_data(),
    }


def test_learning_plan_proposal_keeps_legacy_fixture_valid_without_structured_fields() -> None:
    proposal = LearningPlanProposal.model_validate(proposal_data())

    assert proposal.goal_contract is None
    assert proposal.short_term_learning_package is None


def test_structured_learning_plan_fields_are_available_on_proposal_and_persisted_plans() -> None:
    goal_contract = GoalContract(
        goal_type="literacy",
        goal_name="提升中医经典阅读能力",
        observable_ability="能够独立断句并释义经典原文。",
        acceptance_evidence=["提交一篇原文的断句与释义。"],
    )
    milestone = PlanMilestone(
        milestone_id="M1",
        name="完成基础阅读",
        success_criteria="完成原文断句与释义。",
        evidence_required=["断句与释义作业。"],
    )
    learning_package = ShortTermLearningPackage(
        current_goal="完成一篇原文断句。",
        task_blocks=["断句", "释义"],
        expected_output="断句与释义作业。",
        completion_criteria="断句和释义均完成。",
    )
    recovery_policy = RecoveryPolicy(
        trigger_conditions=["连续两次任务未达标。"],
        recovery_actions=["降低任务难度并安排复习。"],
    )
    proposal = LearningPlanProposal(
        **proposal_data(),
        goal_contract=goal_contract,
        milestones=[milestone],
        short_term_learning_package=learning_package,
        recovery_policy=recovery_policy,
    )
    long_term = LongTermPlan(
        **formal_plan_data(plan_id="LP_LONG_002"),
        goal_contract=goal_contract,
        milestones=[milestone],
        recovery_policy=recovery_policy,
    )
    short_term = ShortTermPlan(
        **formal_plan_data(plan_id="LP_SHORT_002", long_term_plan_id="LP_LONG_002"),
        short_term_learning_package=learning_package,
    )

    assert proposal.goal_contract == goal_contract
    assert long_term.milestones == [milestone]
    assert short_term.short_term_learning_package == learning_package


def test_formal_plans_store_recommendation_trace() -> None:
    trace = RecommendationTrace(
        default_route="遵循已批准路线。",
        user_state="当前基础阶段。",
        time_constraint="本次可用20分钟。",
        current_task="完成主动回忆。",
    )
    long_term = LongTermPlan(
        **formal_plan_data(plan_id="LP_LONG_TRACE"),
        recommendation_trace=trace,
    )
    short_term = ShortTermPlan(
        **formal_plan_data(plan_id="LP_SHORT_TRACE", long_term_plan_id="LP_LONG_TRACE"),
        recommendation_trace=trace,
    )

    assert long_term.recommendation_trace == trace
    assert short_term.recommendation_trace == trace


def test_short_term_package_expresses_structured_budget_and_maintenance() -> None:
    learning_package = ShortTermLearningPackage.model_validate(
        {
            "current_goal": "完成一次主动回忆与纠错。",
            "task_blocks": [
                {"content": "主动回忆", "estimated_minutes": 8},
                {"content": "对照纠错", "estimated_minutes": 5},
            ],
            "review_minutes": 3,
            "maintenance_minutes": 2,
            "buffer_minutes": 2,
            "maintenance_plan": "复习一张长期主线知识卡。",
            "expected_output": "回忆与纠错记录。",
            "completion_criteria": "完成回忆、纠错和长期主线复习。",
        }
    )

    assert learning_package.task_blocks[0].estimated_minutes == 8
    assert learning_package.review_minutes == 3
    assert learning_package.maintenance_minutes == 2
    assert learning_package.buffer_minutes == 2
    assert learning_package.maintenance_plan == "复习一张长期主线知识卡。"


def test_formal_long_term_plan_stores_plan_assumptions_and_unknowns() -> None:
    plan = LongTermPlan(
        **formal_plan_data(plan_id="LP_LONG_STRUCTURED"),
        assumptions=["按每周三次学习暂定。"],
        unknowns_to_confirm=["目标日期待确认。"],
    )

    assert plan.assumptions == ["按每周三次学习暂定。"]
    assert plan.unknowns_to_confirm == ["目标日期待确认。"]


def test_formal_learning_plan_proposal_round_trips_all_task_5_fields() -> None:
    proposal = LearningPlanProposal(
        **proposal_data(),
        planning_route={
            "goal_type": "literacy",
            "goal_name": "提升中医经典阅读能力",
            "planning_status": "approved_route",
            "match_reason": "canonical_name",
            "route_id": "ROUTE_TCM_LITERACY_V1",
            "route_version": 1,
            "route_status": "approved",
            "planning_label": "literacy_default_route",
            "phases": [
                {
                    "phase_id": "FOUNDATION",
                    "name": "基础阅读",
                    "objective": "建立术语和句读基础。",
                    "exit_evidence": ["完成一篇原文的断句与释义。"],
                    "source_refs": ["TEXTBOOK_001"],
                }
            ],
            "sources": [
                {
                    "source_id": "TEXTBOOK_001",
                    "source_type": "textbook",
                    "title": "中医经典选读",
                }
            ],
            "runtime_checks": ["核验教材版本。"],
        },
        short_term_learning_package={
            "time_window_weeks": 2,
            "current_goal": "完成一篇原文断句。",
            "task_blocks": ["断句", "释义"],
            "expected_output": "断句与释义作业。",
            "completion_criteria": "断句和释义均完成。",
        },
        recommendation_trace=RecommendationTrace(
            default_route="依据已批准经典阅读路线。",
            user_state="当前阅读基础待巩固。",
            time_constraint="当前可用30分钟。",
            current_task="先完成原文断句。",
        ),
        assumptions=["按基础阅读阶段开始。"],
        unknowns_to_confirm=["目标典籍范围待确认。"],
    )

    restored = LearningPlanProposal.model_validate(proposal.model_dump())

    assert restored == proposal
    assert restored.planning_route.planning_label == "literacy_default_route"
    assert restored.short_term_learning_package.time_window_weeks == 2
    assert restored.recommendation_trace.current_task == "先完成原文断句。"


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (LearningTaskProposal, task_proposal_data()),
        (LearningPlanProposal, proposal_data()),
        (LongTermPlan, formal_plan_data(plan_id="LP_LONG_001")),
        (
            ShortTermPlan,
            formal_plan_data(plan_id="LP_SHORT_001", long_term_plan_id="LP_LONG_001"),
        ),
        (LearningTask, formal_task_data()),
        (
            LearningPlanResult,
            {
                "long_term_plan": formal_plan_data(plan_id="LP_LONG_001"),
                "short_term_plan": formal_plan_data(
                    plan_id="LP_SHORT_001", long_term_plan_id="LP_LONG_001"
                ),
                "learning_task": formal_task_data(),
            },
        ),
    ],
)
def test_learning_plan_contracts_reject_extra_fields(
    model: type[
        LearningTaskProposal
        | LearningPlanProposal
        | LongTermPlan
        | ShortTermPlan
        | LearningTask
        | LearningPlanResult
    ],
    data: dict[str, object],
) -> None:
    data["unexpected_system_field"] = "must-be-rejected"

    with pytest.raises(ValidationError):
        model.model_validate(data)

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from competition_app.agents.learning_plan_service import LearningPlanServiceAdapter
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.default_route import ResolvedPlanningRoute
from competition_app.contracts.learning_plan import (
    GoalContract,
    LearningPlanProposal,
    LearningTaskProposal,
    PlanMilestone,
    RecoveryPolicy,
    RecommendationTrace,
    ShortTermFocusContext,
    ShortTermLearningPackage,
)
from competition_app.services.default_route import DefaultRouteRepository
from competition_app.services.learning_plan import LearningPlanService


DATA_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "default_routes"


@pytest.fixture
def repository() -> DefaultRouteRepository:
    return DefaultRouteRepository.from_directory(DATA_DIRECTORY)


def proposal(*, short_term: str = "本周完成一次主动回忆。") -> LearningPlanProposal:
    return LearningPlanProposal(
        long_term_plan_content="逐步建立中医基础理论知识结构。",
        short_term_plan_content=short_term,
        priority_mode="normal",
        adjustment_reason="当前适合从短时复习开始。",
        task_proposal=LearningTaskProposal(
            task_type="active_recall",
            task_content="完成一次主动回忆。",
            estimated_minutes=10,
            expected_output="一份回忆结果。",
            completion_criteria="完成回忆并标记遗漏。",
        ),
    )


def structured_proposal(
    repository: DefaultRouteRepository,
    *,
    route: ResolvedPlanningRoute | None = None,
    priority_mode: str = "normal",
    task_content: str = "完成一次主动回忆。",
    task_minutes: int = 10,
    task_blocks: list[object] | None = None,
    maintenance_plan: str | None = "每周复习一张长期主线知识卡。",
    maintenance_unavailable_reason: str | None = None,
    review_minutes: int | None = None,
    maintenance_minutes: int | None = None,
    buffer_minutes: int | None = None,
) -> LearningPlanProposal:
    planning_route = route or repository.resolve(
        goal_type="credential", goal_name="中医执业医师"
    )
    package_data: dict[str, object] = {
        "time_window_weeks": 1,
        "current_goal": "完成当前主题的主动回忆与纠错。",
        "task_blocks": task_blocks or [task_content, "对照纠错"],
        "expected_output": "主动回忆与纠错记录。",
        "completion_criteria": "完成回忆并对照教材标记遗漏。",
        "maintenance_plan": maintenance_plan,
        "maintenance_unavailable_reason": maintenance_unavailable_reason,
        "review_minutes": review_minutes,
        "maintenance_minutes": maintenance_minutes,
        "buffer_minutes": buffer_minutes,
    }
    return LearningPlanProposal(
        long_term_plan_content="逐步建立中医基础理论知识结构。",
        short_term_plan_content="本周完成主动回忆、纠错和长期主线维护。",
        priority_mode=priority_mode,
        adjustment_reason="根据已知路线和当前学习状态安排。",
        task_proposal=LearningTaskProposal(
            task_type="active_recall",
            task_content=task_content,
            estimated_minutes=task_minutes,
            expected_output="一份主动回忆结果。",
            completion_criteria="完成回忆并标记遗漏。",
        ),
        planning_route=planning_route,
        goal_contract=GoalContract(
            goal_type=planning_route.goal_type,
            goal_name=planning_route.goal_name,
            observable_ability="能够闭卷说明核心概念并完成纠错。",
            acceptance_evidence=["闭卷回忆与教材对照纠错记录。"],
        ),
        milestones=[
            PlanMilestone(
                milestone_id="M1",
                name="完成基础回忆",
                success_criteria="能够闭卷说明核心概念。",
                evidence_required=["闭卷回忆与纠错记录。"],
            )
        ],
        short_term_learning_package=ShortTermLearningPackage.model_validate(package_data),
        recovery_policy=RecoveryPolicy(
            trigger_conditions=["连续两次未完成任务。"],
            recovery_actions=["降低负荷，复习缺口后恢复长期主线。"],
        ),
        assumptions=list(planning_route.assumptions),
        unknowns_to_confirm=list(planning_route.unknowns_to_confirm),
    )


def test_service_materializes_system_owned_plan_and_task_records() -> None:
    service = LearningPlanService()
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)

    result = service.materialize("LEARNER_1", proposal(), now=now)

    assert result.long_term_plan.plan_id.startswith("LP_LONG_")
    assert result.short_term_plan.long_term_plan_id == result.long_term_plan.plan_id
    assert result.learning_task.short_term_plan_id == result.short_term_plan.plan_id
    assert result.learning_task.status == "pending"
    assert result.long_term_plan.version == 1
    assert service.get_current("LEARNER_1") == result


def test_service_persists_system_owned_short_term_focus_header() -> None:
    service = LearningPlanService()
    value = proposal()
    value.short_term_focus = ShortTermFocusContext(
        focus_type="knowledge_cluster",
        focus_label="补气类方剂",
        knowledge_point_ids=["KP_FJ_001", "KP_FJ_002"],
    )

    result = service.materialize("LEARNER_FOCUS", value)

    assert result.short_term_plan.short_term_focus == value.short_term_focus


def test_service_updates_existing_records_with_a_new_version() -> None:
    service = LearningPlanService()
    first_time = datetime(2026, 7, 15, tzinfo=timezone.utc)
    first = service.materialize("LEARNER_1", proposal(), now=first_time)

    second = service.materialize(
        "LEARNER_1",
        proposal(short_term="今天改为完成错题复盘。"),
        now=first_time + timedelta(days=1),
    )

    assert second.long_term_plan.plan_id == first.long_term_plan.plan_id
    assert second.short_term_plan.plan_id == first.short_term_plan.plan_id
    assert second.learning_task.task_id == first.learning_task.task_id
    assert second.long_term_plan.version == 2
    assert second.short_term_plan.content == "今天改为完成错题复盘。"
    assert second.long_term_plan.created_at == first.long_term_plan.created_at


def test_service_reuses_supplied_existing_plan_content_when_requested() -> None:
    service = LearningPlanService()
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    reusable = proposal()
    reusable.long_term_plan_action = "reuse"
    reusable.short_term_plan_action = "reuse"

    result = service.materialize(
        "LEARNER_REUSE",
        reusable,
        now=now,
        current_long_term_plan={
            "plan_id": "LONG_EXISTING", "content": "已有长期规划正文。", "version": 4, "status": "active"
        },
        current_short_term_plan={
            "plan_id": "SHORT_EXISTING", "content": "已有短期规划正文。", "version": 7, "status": "active"
        },
    )

    assert result.long_term_plan.plan_id == "LONG_EXISTING"
    assert result.long_term_plan.content == "已有长期规划正文。"
    assert result.long_term_plan.version == 4
    assert result.short_term_plan.plan_id == "SHORT_EXISTING"
    assert result.short_term_plan.content == "已有短期规划正文。"
    assert result.short_term_plan.version == 7


def test_service_persists_three_model_bodies_verbatim() -> None:
    service = LearningPlanService()
    value = proposal()
    value.long_term_plan_content = "长期正文\n保留原始换行。"
    value.short_term_plan_content = "短期正文\n保留原始换行。"
    value.daily_task_content = "当日正文\n保留原始换行。"
    value.task_proposal.task_content = value.daily_task_content

    result = service.materialize("LEARNER_THREE_BODIES", value)

    assert result.long_term_plan.content == value.long_term_plan_content
    assert result.short_term_plan.content == value.short_term_plan_content
    assert result.learning_task.task_content == value.daily_task_content


def test_service_persists_daily_chapter_and_focus_knowledge_points() -> None:
    service = LearningPlanService()
    value = proposal()
    value.task_proposal.learning_chapter = "《方剂学》补益剂·补气"
    value.task_proposal.focus_knowledge_points = ["四君子汤", "君臣佐使配伍"]

    result = service.materialize("LEARNER_DAILY_SCOPE", value)

    assert result.learning_task.learning_chapter == "《方剂学》补益剂·补气"
    assert result.learning_task.focus_knowledge_points == ["四君子汤", "君臣佐使配伍"]


def test_service_updates_only_target_layer_versions() -> None:
    service = LearningPlanService()
    first = service.materialize("LEARNER_LAYER_VERSION", proposal())
    value = proposal(short_term="只更新短期正文。")
    value.long_term_plan_action = "reuse"
    value.daily_task_action = "reuse"

    second = service.materialize("LEARNER_LAYER_VERSION", value)

    assert second.long_term_plan.version == first.long_term_plan.version
    assert second.long_term_plan.content == first.long_term_plan.content
    assert second.short_term_plan.version == first.short_term_plan.version + 1
    assert second.learning_task.version == first.learning_task.version
    assert second.learning_task.task_content == first.learning_task.task_content


def test_service_materializes_approved_route_and_all_formal_structured_fields(
    repository: DefaultRouteRepository,
) -> None:
    service = LearningPlanService(repository)
    value = structured_proposal(repository)
    value.assumptions = ["从当前阶段开始。"]
    value.unknowns_to_confirm = ["考试日期待确认。"]

    result = service.materialize("LEARNER_APPROVED", value, available_minutes=20)

    assert result.long_term_plan.planning_route == value.planning_route
    assert result.long_term_plan.goal_contract == value.goal_contract
    assert result.long_term_plan.milestones == value.milestones
    assert result.long_term_plan.assumptions == ["从当前阶段开始。"]
    assert result.long_term_plan.unknowns_to_confirm == ["考试日期待确认。"]
    assert result.short_term_plan.short_term_learning_package == value.short_term_learning_package
    assert result.short_term_plan.recovery_policy == value.recovery_policy


def test_materialize_long_term_rejects_placeholder_textbook_stage(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(repository)
    value.long_term_plan_stages = [
        {
            "stage": 1,
            "book": ["待确认教材"],
            "goal": "完成当前长期学习阶段目标",
        }
    ]

    with pytest.raises(ValueError, match="placeholder textbooks cannot be published"):
        LearningPlanService(repository).materialize_long_term(
            "LEARNER_PLACEHOLDER",
            value,
        )


def test_service_materializes_provisional_plan_with_assumptions(
    repository: DefaultRouteRepository,
) -> None:
    route = ResolvedPlanningRoute(
        goal_type="literacy",
        goal_name="经典阅读",
        planning_status="provisional",
        match_reason="no_safe_match",
        assumptions=["先按一周阅读任务暂定。"],
    )
    value = structured_proposal(repository, route=route)

    result = LearningPlanService(repository).materialize("LEARNER_PROVISIONAL", value)

    assert result.long_term_plan.planning_route.planning_status == "provisional"
    assert result.long_term_plan.assumptions == ["先按一周阅读任务暂定。"]


def test_service_rejects_candidate_route_status(repository: DefaultRouteRepository) -> None:
    route = ResolvedPlanningRoute.model_construct(
        goal_type="credential",
        goal_name="候选路线",
        planning_status="candidate",
        match_reason="model_candidate",
        route_id="candidate_route",
        route_version=1,
        route_status=None,
        phases=[],
        sources=[],
        runtime_checks=[],
        assumptions=[],
        unknowns_to_confirm=[],
    )
    value = structured_proposal(repository)
    value.planning_route = route

    with pytest.raises(
        ValueError,
        match="planning route status must be 'approved_route' or 'provisional'",
    ):
        LearningPlanService(repository).materialize("LEARNER_CANDIDATE", value)


def test_service_rejects_unknown_approved_route(repository: DefaultRouteRepository) -> None:
    route = ResolvedPlanningRoute(
        goal_type="credential",
        goal_name="未知路线",
        planning_status="approved_route",
        match_reason="explicit_route_id",
        route_id="missing_route",
        route_version=1,
        route_status="approved",
    )

    with pytest.raises(ValueError, match="approved route ID/version could not be resolved"):
        LearningPlanService(repository).materialize(
            "LEARNER_UNKNOWN", structured_proposal(repository, route=route)
        )


def test_service_rejects_approved_route_version_mismatch(
    repository: DefaultRouteRepository,
) -> None:
    route = repository.resolve(goal_type="credential", goal_name="中医执业医师")
    mismatched = route.model_copy(update={"route_version": route.route_version + 1})

    with pytest.raises(ValueError, match="approved route ID/version could not be resolved"):
        LearningPlanService(repository).materialize(
            "LEARNER_ROUTE_VERSION", structured_proposal(repository, route=mismatched)
        )


def test_service_rejects_approved_route_with_nonapproved_status(
    repository: DefaultRouteRepository,
) -> None:
    route = repository.resolve(goal_type="credential", goal_name="中医执业医师")
    mismatched = route.model_construct(
        **{
            **route.model_dump(),
            "route_status": "candidate",
        }
    )
    value = structured_proposal(repository)
    value.planning_route = mismatched

    with pytest.raises(ValueError, match="approved plan route_status must be approved"):
        LearningPlanService(repository).materialize(
            "LEARNER_ROUTE_STATUS", value
        )


def test_service_rejects_approved_plan_without_route_identity(
    repository: DefaultRouteRepository,
) -> None:
    route = ResolvedPlanningRoute.model_construct(
        goal_type="credential",
        goal_name="中医执业医师",
        planning_status="approved_route",
        match_reason="canonical_name",
        route_id=None,
        route_version=None,
        route_status="approved",
        phases=[],
        sources=[],
        runtime_checks=[],
        assumptions=[],
        unknowns_to_confirm=[],
    )
    value = structured_proposal(repository)
    value.planning_route = route

    with pytest.raises(ValueError, match="approved plan requires an approved route ID and version"):
        LearningPlanService(repository).materialize("LEARNER_NO_ROUTE", value)


def test_service_rejects_structured_plan_without_explicit_route_status(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(repository)
    value.planning_route = None

    with pytest.raises(
        ValueError,
        match="structured plan requires an approved_route or provisional planning route",
    ):
        LearningPlanService(repository).materialize("LEARNER_STRUCTURED_NO_ROUTE", value)


def test_service_rejects_provisional_plan_that_references_any_route(
    repository: DefaultRouteRepository,
) -> None:
    route = ResolvedPlanningRoute(
        goal_type="literacy",
        goal_name="临时路线",
        planning_status="provisional",
        match_reason="no_safe_match",
        route_id="untrusted_route",
        assumptions=["暂定。"],
    )

    with pytest.raises(
        ValueError,
        match="provisional plan must not reference an approved, candidate, or unknown route",
    ):
        LearningPlanService(repository).materialize(
            "LEARNER_PROVISIONAL_ROUTE", structured_proposal(repository, route=route)
        )


def test_service_rejects_provisional_plan_without_assumptions_or_unknowns(
    repository: DefaultRouteRepository,
) -> None:
    route = ResolvedPlanningRoute.model_construct(
        goal_type="literacy",
        goal_name="临时路线",
        planning_status="provisional",
        match_reason="no_safe_match",
        route_id=None,
        route_version=None,
        route_status=None,
        phases=[],
        sources=[],
        runtime_checks=[],
        assumptions=[],
        unknowns_to_confirm=[],
    )
    value = structured_proposal(repository)
    value.planning_route = route
    value.assumptions = []
    value.unknowns_to_confirm = []

    with pytest.raises(
        ValueError,
        match="provisional plan requires assumptions or unknowns_to_confirm",
    ):
        LearningPlanService(repository).materialize("LEARNER_NO_CONTEXT", value)


def test_service_rejects_current_task_over_available_minutes(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(repository, task_minutes=16)

    with pytest.raises(ValueError, match="current learning task exceeds available_minutes"):
        LearningPlanService(repository).materialize(
            "LEARNER_TASK_BUDGET", value, available_minutes=15
        )


def test_service_rejects_structured_total_budget_over_available_minutes(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(
        repository,
        task_minutes=10,
        task_blocks=[
            {"content": "主动回忆", "estimated_minutes": 8},
            {"content": "对照纠错", "estimated_minutes": 7},
        ],
        review_minutes=3,
        maintenance_minutes=2,
        buffer_minutes=2,
    )

    with pytest.raises(ValueError, match="short-term structured total exceeds available_minutes"):
        LearningPlanService(repository).materialize(
            "LEARNER_TOTAL_BUDGET", value, available_minutes=20
        )


def test_service_rejects_current_task_inconsistent_with_short_term_task_blocks(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(
        repository,
        task_content="复习方剂基础。",
        task_blocks=["背诵针灸穴位", "整理经络图"],
    )

    with pytest.raises(
        ValueError,
        match="current learning task must match a short-term task block",
    ):
        LearningPlanService(repository).materialize("LEARNER_TASK_MISMATCH", value)


def test_service_rejects_mixed_structured_and_legacy_task_blocks_with_known_budget(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(
        repository,
        task_blocks=[
            {"content": "主动回忆", "estimated_minutes": 8},
            "对照纠错",
        ],
    )

    with pytest.raises(
        ValueError,
        match="short-term task_blocks must be all structured or all legacy strings",
    ):
        LearningPlanService(repository).materialize(
            "LEARNER_MIXED_BUDGET", value, available_minutes=20
        )


def test_service_rejects_milestone_without_observable_evidence(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(repository)
    value.milestones = [
        PlanMilestone(
            milestone_id="M1",
            name="完成阶段",
            success_criteria="完成阶段。",
            evidence_required=["   "],
        )
    ]

    with pytest.raises(ValueError, match="milestone M1 requires observable exit or acceptance evidence"):
        LearningPlanService(repository).materialize("LEARNER_EVIDENCE", value)


def test_service_rejects_advanced_clinical_milestone_without_formal_evaluation_boundary(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(repository)
    value.milestones = [
        PlanMilestone(
            milestone_id="CLINICAL_ADVANCED",
            name="高级临床实践技能",
            success_criteria="能够独立完成高级临床实践技能。",
            evidence_required=["提交一次实践记录。"],
        )
    ]

    with pytest.raises(
        ValueError,
        match="advanced clinical capability requires mentor or formal evaluation boundary",
    ):
        LearningPlanService(repository).materialize("LEARNER_CLINICAL", value)


def test_service_accepts_advanced_clinical_milestone_with_mentor_evidence(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(repository)
    value.milestones = [
        PlanMilestone(
            milestone_id="CLINICAL_ADVANCED",
            name="高级临床实践技能",
            success_criteria="在导师监督下完成高级临床实践技能。",
            evidence_required=["导师签字评价记录。"],
        )
    ]

    result = LearningPlanService(repository).materialize("LEARNER_CLINICAL_SAFE", value)

    assert result.long_term_plan.milestones == value.milestones


def test_service_rejects_unrelated_mentor_text_for_another_advanced_milestone(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(repository)
    value.milestones = [
        PlanMilestone(
            milestone_id="READING",
            name="导师推荐阅读",
            success_criteria="阅读导师推荐教材。",
            evidence_required=["提交阅读笔记。"],
        ),
        PlanMilestone(
            milestone_id="CLINICAL_ADVANCED",
            name="高级临床实践技能",
            success_criteria="能够独立完成高级临床实践技能。",
            evidence_required=["提交一次实践记录。"],
        ),
    ]

    with pytest.raises(
        ValueError,
        match="advanced clinical capability requires mentor or formal evaluation boundary",
    ):
        LearningPlanService(repository).materialize("LEARNER_CLINICAL_UNRELATED", value)


def test_service_rejects_unrelated_mentor_reading_in_advanced_clinical_milestone(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(repository)
    value.milestones = [
        PlanMilestone(
            milestone_id="CLINICAL_ADVANCED",
            name="高级临床实践技能",
            success_criteria="能够独立完成高级临床实践技能。",
            evidence_required=["阅读导师推荐教材并提交笔记。"],
        )
    ]

    with pytest.raises(
        ValueError,
        match="advanced clinical capability requires mentor or formal evaluation boundary",
    ):
        LearningPlanService(repository).materialize("LEARNER_CLINICAL_READING", value)


def test_service_rejects_mentor_guided_reading_as_advanced_clinical_evaluation(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(repository)
    value.milestones = [
        PlanMilestone(
            milestone_id="CLINICAL_ADVANCED",
            name="高级临床实践技能",
            success_criteria="能够独立完成高级临床实践技能。",
            evidence_required=["导师指导阅读教材并提交笔记。"],
        )
    ]

    with pytest.raises(
        ValueError,
        match="advanced clinical capability requires mentor or formal evaluation boundary",
    ):
        LearningPlanService(repository).materialize("LEARNER_CLINICAL_GUIDED_READING", value)


def test_service_rejects_temporary_focus_without_maintenance_or_explicit_unavailability(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(
        repository,
        priority_mode="temporary_focus",
        maintenance_plan=None,
        maintenance_unavailable_reason=None,
    )

    with pytest.raises(
        ValueError,
        match="temporary focus requires maintenance_plan or maintenance_unavailable_reason",
    ):
        LearningPlanService(repository).materialize("LEARNER_MAINTENANCE", value)


def test_service_accepts_temporary_focus_with_explicit_maintenance_unavailability(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(
        repository,
        priority_mode="temporary_focus",
        maintenance_plan=None,
        maintenance_unavailable_reason="本周预算全部被临时考试占用，下周恢复长期主线。",
    )

    result = LearningPlanService(repository).materialize("LEARNER_MAINTENANCE_BOUNDARY", value)

    assert (
        result.short_term_plan.short_term_learning_package.maintenance_unavailable_reason
        == "本周预算全部被临时考试占用，下周恢复长期主线。"
    )


def test_service_rejects_blank_recovery_policy_actions(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(repository, priority_mode="recovery")
    value.recovery_policy = RecoveryPolicy(
        trigger_conditions=["连续两次未完成任务。"],
        recovery_actions=["   "],
    )

    with pytest.raises(
        ValueError,
        match="recovery_policy requires non-empty trigger conditions and recovery actions",
    ):
        LearningPlanService(repository).materialize("LEARNER_RECOVERY_EMPTY", value)


@pytest.mark.parametrize(
    "unsafe_instruction",
    [
        "针对当前真实患者给出个体化诊断结论。",
        "为该患者开具个体化处方。",
        "指导这位患者每日服用黄芪30克。",
        "为该患者开具黄芪30克。",
        "建议这位患者每日口服阿司匹林100mg。",
        "根据当前患者情况将黄芪调整至30克。",
        "患者每日服用黄芪30克。",
        "患儿口服布洛芬5ml。",
        "给予患者阿司匹林100mg。",
        "教材反例：不得自行用药。针对当前真实患者，建议每日服用阿司匹林100mg。",
        "教材反例：不得自行用药，但针对当前患者建议每日服用阿司匹林100mg。",
        "病人每天吃阿司匹林100mg。",
        "我家孩子每天喝布洛芬5ml。",
        "向现实患者承诺该方案保证治愈。",
    ],
)
def test_service_rejects_real_patient_diagnosis_prescription_dose_or_efficacy_instructions(
    repository: DefaultRouteRepository,
    unsafe_instruction: str,
) -> None:
    value = structured_proposal(repository, task_content=unsafe_instruction)

    with pytest.raises(ValueError, match="medical education safety boundary"):
        LearningPlanService(repository).materialize("LEARNER_UNSAFE", value)


@pytest.mark.parametrize(
    "safe_instruction",
    [
        "学习方剂教材中的药物剂量并制作对照表。",
        "分析教材患者病例的辨证诊断思路，不提出真实诊疗建议。",
        "背诵处方审核法规与用药安全知识。",
        "教材反例：真实患者不得自行诊断或照方服药。",
    ],
)
def test_service_does_not_block_medical_education_counterexamples(
    repository: DefaultRouteRepository,
    safe_instruction: str,
) -> None:
    value = structured_proposal(repository, task_content=safe_instruction)

    result = LearningPlanService(repository).materialize("LEARNER_SAFE_EDUCATION", value)

    assert result.learning_task.task_content == safe_instruction


def test_service_does_not_combine_real_patient_and_textbook_dose_across_fields(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(
        repository,
        task_content="摘录教材中‘每日服用黄芪30克’的历史剂量记载并制作对照表。",
    )
    value.long_term_plan_content = "真实患者信息不得用于诊疗，本计划仅安排教材学习。"

    result = LearningPlanService(repository).materialize("LEARNER_SAFE_FIELDS", value)

    assert result.learning_task.task_content == "摘录教材中‘每日服用黄芪30克’的历史剂量记载并制作对照表。"


def test_service_does_not_require_mentor_boundary_for_basic_clinical_skills(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(repository)
    value.milestones = [
        PlanMilestone(
            milestone_id="CLINICAL_BASIC",
            name="基础临床实践技能",
            success_criteria="完成基础临床实践技能知识复习。",
            evidence_required=["提交一次基础技能知识自测记录。"],
        )
    ]

    result = LearningPlanService(repository).materialize("LEARNER_CLINICAL_BASIC", value)

    assert result.long_term_plan.milestones == value.milestones


def test_service_atomically_reuses_structured_long_and_short_term_metadata(
    repository: DefaultRouteRepository,
) -> None:
    service = LearningPlanService(repository)
    first_value = structured_proposal(repository)
    first_value.assumptions = ["原长期假设。"]
    first_value.unknowns_to_confirm = ["原待确认项。"]
    first_value.recommendation_trace = RecommendationTrace(
        default_route="原路线。",
        user_state="原状态。",
        time_constraint="原预算。",
        current_task="原任务。",
    )
    first = service.materialize("LEARNER_ATOMIC_REUSE", first_value)

    replacement_route = ResolvedPlanningRoute(
        goal_type="literacy",
        goal_name="临时阅读目标",
        planning_status="provisional",
        match_reason="no_safe_match",
        assumptions=["新临时假设。"],
    )
    second_value = structured_proposal(repository, route=replacement_route)
    second_value.long_term_plan_action = "reuse"
    second_value.short_term_plan_action = "reuse"
    second_value.long_term_plan_content = "不得替换原长期正文。"
    second_value.short_term_plan_content = "不得替换原短期正文。"
    second_value.assumptions = ["不得替换原长期假设。"]
    second_value.unknowns_to_confirm = ["不得替换原待确认项。"]

    second = service.materialize("LEARNER_ATOMIC_REUSE", second_value)

    assert second.long_term_plan.content == first.long_term_plan.content
    assert second.long_term_plan.goal_contract == first.long_term_plan.goal_contract
    assert second.long_term_plan.planning_route == first.long_term_plan.planning_route
    assert second.long_term_plan.milestones == first.long_term_plan.milestones
    assert second.long_term_plan.assumptions == first.long_term_plan.assumptions
    assert second.long_term_plan.unknowns_to_confirm == first.long_term_plan.unknowns_to_confirm
    assert second.long_term_plan.recommendation_trace == first.long_term_plan.recommendation_trace
    assert second.short_term_plan.content == first.short_term_plan.content
    assert (
        second.short_term_plan.short_term_learning_package
        == first.short_term_plan.short_term_learning_package
    )
    assert second.short_term_plan.recovery_policy == first.short_term_plan.recovery_policy
    assert second.short_term_plan.recommendation_trace == first.short_term_plan.recommendation_trace


def test_service_rejects_invalid_short_route_when_long_plan_is_reused(
    repository: DefaultRouteRepository,
) -> None:
    service = LearningPlanService(repository)
    service.materialize("LEARNER_MIXED_REUSE", structured_proposal(repository))
    invalid_route = ResolvedPlanningRoute.model_construct(
        goal_type="credential",
        goal_name="候选路线",
        planning_status="candidate",
        match_reason="model_candidate",
        route_id="candidate_route",
        route_version=1,
        route_status=None,
        phases=[],
        sources=[],
        runtime_checks=[],
        assumptions=[],
        unknowns_to_confirm=[],
    )
    value = structured_proposal(repository)
    value.long_term_plan_action = "reuse"
    value.short_term_plan_action = "update"
    value.planning_route = invalid_route

    with pytest.raises(
        ValueError,
        match="planning route status must be 'approved_route' or 'provisional'",
    ):
        service.materialize("LEARNER_MIXED_REUSE", value)


def test_service_rejects_different_approved_short_route_when_long_plan_is_reused(
    repository: DefaultRouteRepository,
) -> None:
    service = LearningPlanService(repository)
    service.materialize("LEARNER_ROUTE_MISMATCH", structured_proposal(repository))
    other_route = repository.resolve(
        goal_type="credential", goal_name="执业药师职业资格考试（中药学类）"
    )
    assert other_route.planning_status == "approved_route"
    value = structured_proposal(repository, route=other_route)
    value.long_term_plan_action = "reuse"
    value.short_term_plan_action = "update"

    with pytest.raises(
        ValueError,
        match="long-term and short-term plans must use the same approved route ID/version",
    ):
        service.materialize("LEARNER_ROUTE_MISMATCH", value)


def test_service_rejects_missing_short_route_when_approved_long_plan_is_reused(
    repository: DefaultRouteRepository,
) -> None:
    service = LearningPlanService(repository)
    service.materialize("LEARNER_ROUTE_MISSING", structured_proposal(repository))
    value = proposal()
    value.long_term_plan_action = "reuse"
    value.short_term_plan_action = "update"

    with pytest.raises(
        ValueError,
        match="long-term and short-term plans must use the same approved route ID/version",
    ):
        service.materialize("LEARNER_ROUTE_MISSING", value)


def test_service_rejects_blank_long_recovery_when_short_plan_is_reused(
    repository: DefaultRouteRepository,
) -> None:
    service = LearningPlanService(repository)
    service.materialize("LEARNER_LONG_RECOVERY", structured_proposal(repository))
    value = structured_proposal(repository)
    value.long_term_plan_action = "update"
    value.short_term_plan_action = "reuse"
    value.recovery_policy = RecoveryPolicy(
        trigger_conditions=["连续两次未完成任务。"],
        recovery_actions=["   "],
    )

    with pytest.raises(
        ValueError,
        match="recovery_policy requires non-empty trigger conditions and recovery actions",
    ):
        service.materialize("LEARNER_LONG_RECOVERY", value)


@pytest.mark.asyncio
async def test_adapter_injects_repository_and_passes_available_minutes(
    repository: DefaultRouteRepository,
) -> None:
    adapter = LearningPlanServiceAdapter(route_repository=repository)
    value = structured_proposal(repository, task_minutes=11)
    diagnosis = type("Diagnosis", (), {"learning_plan_proposal": value})()
    context = {
        "case_id": "CASE_ADAPTER",
        "trace_id": "TRACE_ADAPTER",
        "request_id": "REQUEST_ADAPTER",
        "execution_id": "EXECUTION_ADAPTER",
        "step_id": "learning_plan",
        "learner_id": "LEARNER_ADAPTER",
        "available_minutes": 10,
        "dependency_outputs": {
            "diagnosis": AgentEnvelope(
                artifact_id="ARTIFACT_DIAGNOSIS",
                artifact_type="diagnosis_result",
                producer="diagnosis_agent",
                payload=diagnosis,
                case_id="CASE_ADAPTER",
                trace_id="TRACE_ADAPTER",
                request_id="REQUEST_ADAPTER",
                execution_id="EXECUTION_ADAPTER",
                step_id="diagnosis",
                task_type="learning_plan",
                learner_id="LEARNER_ADAPTER",
            )
        },
    }

    assert adapter.service.route_repository is repository
    with pytest.raises(ValueError, match="current learning task exceeds available_minutes"):
        await adapter.run(context)


def test_container_shares_route_repository_between_resolver_and_service(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    registry = container.review_card_use_case.orchestrator.agent_registry
    resolver = registry.get("default_route_resolver")
    adapter = registry.get("learning_plan_service")

    assert resolver._repository is adapter.service.route_repository

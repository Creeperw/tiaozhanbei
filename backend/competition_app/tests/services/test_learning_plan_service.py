from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from competition_app.agents.learning_plan_service import LearningPlanServiceAdapter
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings
from competition_app.contracts.base import AgentEnvelope
from competition_app.agents.diagnosis import DiagnosisResult
from competition_app.contracts.resource import AuditResult
from competition_app.contracts.plan_compilation import (
    CompiledLongTermContract,
    CompiledLongTermStage,
    CompiledPlanContractResult,
    PlanCompilationEnvelope,
    PlanSourceAnchor,
)
from competition_app.contracts.default_route import ResolvedPlanningRoute
from competition_app.contracts.learning_plan import (
    GoalContract,
    LearningPlanProposal,
    LearningTaskProposal,
    LongTermPlanStage,
    PlanMilestone,
    RecoveryPolicy,
    RecommendationTrace,
    ShortTermFocusContext,
    ShortTermFocusEvidenceAnchor,
    ShortTermLearningPackage,
    StageEvidenceRecord,
    TextbookSelectionContext,
)
from competition_app.services.default_route import DefaultRouteRepository
from competition_app.services.intervention_apply import apply_accepted_intervention
from competition_app.services.learning_plan import (
    LearningPlanService,
    materialize_daily_task_items,
)
from competition_app.services.plan_progress import build_plan_progress
from competition_app.services.textbook_route import TextbookRouteRepository


DATA_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "default_routes"
TEXTBOOK_ROUTE_FILE = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "textbook_routes"
    / "tcm_textbook_routes.v1.json"
)


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


def test_short_term_formula_preview_preserves_long_term_stage_and_evidence(
    repository: DefaultRouteRepository,
) -> None:
    service = LearningPlanService(repository)
    planning_route = repository.resolve(
        goal_type="credential", goal_name="中医执业医师"
    )
    textbook_route = TextbookRouteRepository.from_file(TEXTBOOK_ROUTE_FILE).resolve(
        exam_route_id="tcm_physician_standard_degree",
        goal_text="中医执业医师资格考试",
    )
    planning_route = planning_route.model_copy(
        update={"textbook_route": textbook_route}
    )
    route_stages = textbook_route.route.stages
    long_value = structured_proposal(repository, route=planning_route)
    long_value.long_term_plan_stages = [
        LongTermPlanStage(
            stage=stage.order,
            stage_name=stage.name,
            book=stage.books,
            goal=stage.objective,
            duration_days=90,
            schedule_summary=f"按{stage.name}的正式教材推进并保留验收证据。",
        )
        for stage in route_stages
    ]
    long_value.textbook_selection = TextbookSelectionContext(
        route_id=textbook_route.route.route_id,
        route_version=textbook_route.route.route_version,
        stage_id="stage-1",
        stage_name=route_stages[0].name,
        books=["《中医学基础》"],
        reason="长期 active stage 由服务端阶段证据控制。",
    )
    initial = service.materialize("LEARNER_FORMULA_PREVIEW", long_value)
    anchor_time = datetime(2026, 9, 3, tzinfo=timezone.utc)
    stage_evidence = StageEvidenceRecord(
        evidence_id="STAGE_EVIDENCE_EXISTING",
        stage=1,
        requirement="长期阶段已有局部核验证据",
        source_type="audited_assessment",
        source_id="ASSESSMENT_EXISTING",
        verified_by="test_fixture",
        verified_at=anchor_time,
    )
    preserved_long = initial.long_term_plan.model_copy(
        update={"stage_evidence": [stage_evidence]}
    )
    service.plan_repository.save_current(
        "LEARNER_FORMULA_PREVIEW",
        initial.model_copy(update={"long_term_plan": preserved_long}),
    )

    short_value = structured_proposal(
        repository,
        route=planning_route,
        priority_mode="temporary_focus",
    )
    short_value.long_term_plan_action = "reuse"
    short_value.short_term_plan_content = (
        "未来7天使用《方剂学》入门预习四君子汤、参苓白术散和理中丸；"
        "本专题不代表阶段完成或晋级，长期阶段保持 stage-1。"
    )
    short_value.short_term_learning_package.duration_days = 7
    short_value.short_term_learning_package.progression_nodes = [
        "完成三方教材核对。",
        "提交三方闭卷比较表。",
    ]
    short_value.short_term_focus = ShortTermFocusContext(
        focus_type="special_topic",
        focus_label="四君子汤、参苓白术散、理中丸",
        mode="temporary_cross_stage",
        progression_stage_id="stage-1",
        focus_stage_id="stage-2",
        focus_books=["《方剂学》"],
        focus_names=["四君子汤", "参苓白术散", "理中丸"],
        focus_evidence=[
            ShortTermFocusEvidenceAnchor(
                name=name,
                evidence_id=f"E_{index}",
                source_id=f"方剂学_clean:{index:05d}",
                source_label="《方剂学》· 教材证据",
            )
            for index, name in enumerate(
                ["四君子汤", "参苓白术散", "理中丸"], start=1
            )
        ],
        prerequisite_mode="introductory_preview",
    )
    short_value.textbook_selection = TextbookSelectionContext(
        route_id=textbook_route.route.route_id,
        route_version=textbook_route.route.route_version,
        stage_id="stage-2",
        stage_name=route_stages[1].name,
        books=["《方剂学》"],
        reason="同路线证据锚定的临时入门预习，不代表长期阶段完成。",
    )

    generated = service.materialize_short_term(
        "LEARNER_FORMULA_PREVIEW",
        short_value,
        current_long_term_plan=preserved_long.model_dump(mode="json"),
    )
    stored = service.get_current("LEARNER_FORMULA_PREVIEW")

    assert generated.generated_scope == "short_term"
    assert generated.long_term_plan is None
    assert generated.short_term_plan.textbook_selection.stage_id == "stage-2"
    assert generated.short_term_plan.textbook_selection.books == ["《方剂学》"]
    assert stored.long_term_plan.plan_id == preserved_long.plan_id
    assert stored.long_term_plan.version == preserved_long.version
    assert stored.long_term_plan.textbook_selection.stage_id == "stage-1"
    assert stored.long_term_plan.textbook_selection.books == ["《中医学基础》"]
    assert stored.long_term_plan.stage_evidence == [stage_evidence]
    assert stored.long_term_plan.milestones == preserved_long.milestones
    assert LearningPlanService._current_stage_number(stored.long_term_plan) == 1


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


def test_service_materializes_stable_system_owned_video_and_practice_items(
    repository: DefaultRouteRepository,
) -> None:
    formal_ids = {
        "四君子汤": "KP_1",
        "君臣佐使配伍": "KP_2",
        "补气剂适应证": "KP_3",
    }
    service = LearningPlanService(
        repository,
        knowledge_point_resolver=formal_ids.get,
        video_resource_resolver=lambda resource_ref: (
            resource_ref if resource_ref.get("trusted_resource_id") else None
        ),
    )
    value = structured_proposal(repository)
    value.task_proposal.estimated_minutes = 20
    value.task_proposal.task_content = "观看补气剂视频片段"
    value.task_proposal.focus_knowledge_points = [
        "四君子汤",
        "君臣佐使配伍",
        "补气剂适应证",
    ]
    value.short_term_learning_package = ShortTermLearningPackage(
        current_goal="观看视频并完成三个知识点练习",
        task_blocks=[
            {
                "content": "观看补气剂视频片段",
                "estimated_minutes": 5,
                "item_type": "video_section",
                "resource_ref": {
                    "trusted_resource_id": "VIDEO_SECTION_1",
                    "provider": "bilibili",
                    "bvid": "BV1TEST",
                    "page": 1,
                    "start_seconds": 10,
                    "end_seconds": 70,
                },
            }
        ],
        expected_output="完成视频和练习",
        completion_criteria="四项全部完成",
    )

    first = service.materialize("LEARNER_ATOMIC", value)
    reusable = value.model_copy(deep=True)
    reusable.daily_task_action = "reuse"
    reused = service.materialize("LEARNER_ATOMIC", reusable)
    updated = service.materialize("LEARNER_ATOMIC", value)

    assert len(first.learning_task.items) == 5
    assert [item.item_type for item in first.learning_task.items] == [
        "video_section",
        "knowledge_practice",
        "knowledge_practice",
        "knowledge_practice",
        "knowledge_practice",
    ]
    assert all(item.task_item_id.startswith("DTI_") for item in first.learning_task.items)
    # 前四项为三个知识点配套练习，最后一项为按剩余时间动态算题数的每日测验
    assert [item.kp_id for item in first.learning_task.items[1:-1]] == [
        "KP_1",
        "KP_2",
        "KP_3",
    ]
    quiz = first.learning_task.items[-1]
    assert quiz.completion_policy.get("quiz") is True
    assert quiz.required_question_count == 3
    assert [item.task_item_id for item in reused.learning_task.items] == [
        item.task_item_id for item in first.learning_task.items
    ]
    assert [item.task_item_id for item in updated.learning_task.items] != [
        item.task_item_id for item in first.learning_task.items
    ]


def test_service_does_not_persist_model_knowledge_point_names_as_ids() -> None:
    value = proposal()
    value.task_proposal.focus_knowledge_points = ["四君子汤", "君臣佐使配伍"]

    task = LearningPlanService().materialize("LEARNER_MODEL_SHAPE", value).learning_task

    # 模型给的知识点名称没有解析器就得不到正式 ID，执行层没有题组可发。
    # 名称只留在计划正文的焦点里，不能变成一个点不开的原子项。
    assert task.items == []
    assert task.focus_knowledge_points == ["四君子汤", "君臣佐使配伍"]


def test_service_does_not_create_atom_for_unverified_video_resource(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(repository)
    value.task_proposal.task_content = "观看模型推荐的视频"
    value.short_term_learning_package = ShortTermLearningPackage(
        current_goal="学习补气剂",
        task_blocks=[
            {
                "content": "观看模型推荐的视频",
                "estimated_minutes": 10,
                "item_type": "video_section",
                "resource_ref": {"url": "https://example.invalid/model-output"},
            }
        ],
        expected_output="学习记录",
        completion_criteria="完成学习",
    )

    task = LearningPlanService(repository).materialize(
        "LEARNER_UNVERIFIED_VIDEO", value
    ).learning_task

    # 未通过可信资源校验的视频不能成为原子项；也不能降级成 reading——
    # reading 同样没有完成入口，只会成为一个点不开的项。
    assert task.items == []


def test_service_selects_published_video_for_legacy_model_block_from_formal_kp(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(repository)
    value.task_proposal.task_content = "观看四君子汤视频并完成练习"
    value.task_proposal.estimated_minutes = 10
    value.task_proposal.focus_knowledge_points = ["四君子汤"]
    value.short_term_learning_package = ShortTermLearningPackage(
        current_goal="学习四君子汤",
        task_blocks=["观看四君子汤视频并完成练习"],
        expected_output="完成视频和练习",
        completion_criteria="两项全部完成",
    )
    observed_refs = []

    def resolve_video(resource_ref):
        observed_refs.append(resource_ref)
        if resource_ref == {"kp_id": "KP_FORMAL_1"}:
            return {
                "source": "knowledge_atlas",
                "provider": "bilibili",
                "bvid": "BV_REAL",
                "page": 1,
                "start_seconds": 12,
                "end_seconds": 42,
            }
        return None

    task = LearningPlanService(
        repository,
        knowledge_point_resolver=lambda name: (
            "KP_FORMAL_1" if name == "四君子汤" else None
        ),
        video_resource_resolver=resolve_video,
    ).materialize("LEARNER_PUBLISHED_VIDEO", value).learning_task

    assert observed_refs == [{"kp_id": "KP_FORMAL_1"}]
    assert [item.item_type for item in task.items] == [
        "video_section",
        "knowledge_practice",
        "knowledge_practice",
    ]
    assert task.items[0].resource_ref["bvid"] == "BV_REAL"
    assert task.items[0].completion_policy["policy"] == "iframe_focus_and_confirmation"
    # 剩余预算 4.5 分钟 → 动态测验 3 题
    assert task.items[2].completion_policy.get("quiz") is True
    assert task.items[2].required_question_count == 3


def test_service_always_attempts_chapter_video_before_focus_practice(
    repository: DefaultRouteRepository,
) -> None:
    value = structured_proposal(repository)
    value.task_proposal.task_content = "学习四君子汤并完成配套练习"
    value.task_proposal.learning_chapter = "《方剂学》补益剂·补气"
    value.task_proposal.estimated_minutes = 20
    value.task_proposal.focus_knowledge_points = ["四君子汤"]
    value.short_term_learning_package.task_blocks = [
        "学习四君子汤并完成配套练习"
    ]
    observed_refs = []

    def resolve_video(resource_ref):
        observed_refs.append(resource_ref)
        return {
            "source": "knowledge_atlas",
            "provider": "bilibili",
            "bvid": "BV_CHAPTER",
            "page": 1,
            "start_seconds": 0,
            "end_seconds": 600,
        }

    service = LearningPlanService(
        repository,
        knowledge_point_resolver=lambda name: (
            "KP_FORMAL_1" if name == "四君子汤" else None
        ),
        video_resource_resolver=resolve_video,
    )
    task = service.materialize("LEARNER_CHAPTER_VIDEO", value).learning_task

    assert observed_refs == [
        {
            "learning_chapter": "《方剂学》补益剂·补气",
            "kp_ids": ["KP_FORMAL_1"],
        }
    ]
    assert [item.item_type for item in task.items] == [
        "video_section",
        "knowledge_practice",
        "knowledge_practice",
    ]
    assert task.items[0].title == "观看《方剂学》补益剂·补气章节视频"
    assert task.items[1].kp_id == "KP_FORMAL_1"
    # 20 分钟：视频 10 + 练习 4.5 → 剩余 5.5 → 动态测验 3 题
    assert task.items[2].completion_policy.get("quiz") is True
    assert task.items[2].required_question_count == 3

    normalized = service.ensure_executable_daily_resources(
        "LEARNER_CHAPTER_VIDEO"
    )
    assert normalized.focus_knowledge_points == ["四君子汤"]
    assert normalized.task_content == (
        "今日围绕《方剂学》补益剂·补气学习：观看《方剂学》补益剂·补气章节视频；"
        "完成知识点 四君子汤 练习；完成今日测验。"
    )
    # 测验是独立原子：配套题统计只含知识点练习（3 道）
    assert normalized.expected_output == "1条章节视频观看记录与3道配套题提交记录"
    assert normalized.completion_criteria == (
        "完成全部3个原子任务（1个章节视频、1个知识点共3道题）；"
        "以服务端记录全部完成为通过标准。"
    )


def test_completed_task_evidence_drives_the_stage_pass_gate(
    repository: DefaultRouteRepository,
) -> None:
    service = LearningPlanService(repository)
    plans = service.materialize("LEARNER_STAGE_GATE", structured_proposal(repository))
    requirements = service._stage_requirements(
        plans.long_term_plan, stage=1
    )
    task = plans.learning_task.model_copy(
        update={
            "status": "completed",
            "expected_output": "；".join(requirements),
        }
    )
    service.plan_repository.save_current(
        "LEARNER_STAGE_GATE",
        plans.model_copy(update={"learning_task": task}),
    )
    updated = plans
    for requirement in requirements:
        updated = service.record_completed_task_stage_evidence(
            "LEARNER_STAGE_GATE",
            stage=1,
            requirement=requirement,
            task_id=task.task_id,
        )
    progress = build_plan_progress(updated)

    first_stage = progress["long_term"]["stage_progress"][0]
    assert first_stage["passed"] is True
    assert first_stage["can_advance"] is True
    assert first_stage["indicators"][0]["status"] == "satisfied"
    assert first_stage["indicators"][0]["evidence_refs"][0]["source_id"] == task.task_id


def test_stage_progress_projects_the_approved_textbook_route_as_authoritative(
    repository: DefaultRouteRepository,
) -> None:
    planning_route = repository.resolve(
        goal_type="credential", goal_name="中医执业医师"
    )
    textbook_route = TextbookRouteRepository.from_file(TEXTBOOK_ROUTE_FILE).resolve(
        exam_route_id="tcm_physician_standard_degree",
        goal_text="中医执业医师资格考试",
    )
    planning_route = planning_route.model_copy(
        update={"textbook_route": textbook_route}
    )
    plans = LearningPlanService(repository).materialize(
        "LEARNER_ROUTE_PROJECTION",
        structured_proposal(repository, route=planning_route),
    )
    plans = plans.model_copy(
        update={
            "long_term_plan": plans.long_term_plan.model_copy(
                update={
                    "stages": [
                        LongTermPlanStage(
                            stage=1,
                            book=["模型误写教材"],
                            goal="模型误写目标",
                        )
                    ]
                }
            )
        }
    )

    first_stage = build_plan_progress(plans)["long_term"]["stage_progress"][0]
    trusted_stage = (
        plans.long_term_plan.planning_route.textbook_route.route.stages[0]
    )
    assert first_stage["name"] == trusted_stage.name
    assert first_stage["books"] == trusted_stage.books
    assert first_stage["goal"] == trusted_stage.objective
    expected_indicators = [
        *trusted_stage.exit_evidence,
        *[
            str(item)
            for item in plans.long_term_plan.milestones[0].evidence_required
            if str(item).strip() not in trusted_stage.exit_evidence
        ],
    ]
    assert [item["description"] for item in first_stage["indicators"]] == (
        expected_indicators
    )


def test_stage_gate_rejects_unapproved_requirement(
    repository: DefaultRouteRepository,
) -> None:
    service = LearningPlanService(repository)
    plans = service.materialize("LEARNER_STAGE_GATE_BAD", structured_proposal(repository))
    task = plans.learning_task.model_copy(update={"status": "completed"})
    service.plan_repository.save_current(
        "LEARNER_STAGE_GATE_BAD",
        plans.model_copy(update={"learning_task": task}),
    )

    with pytest.raises(ValueError, match="approved stage gate"):
        service.record_completed_task_stage_evidence(
            "LEARNER_STAGE_GATE_BAD",
            stage=1,
            requirement="自行编造的阶段通过条件",
            task_id=task.task_id,
        )


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "digest", "message"),
    [
        ("revise", "a" * 64, "requires a passing audit"),
        ("pass", "b" * 64, "approval does not match current proposal"),
    ],
)
async def test_adapter_rejects_unapproved_long_term_plan_before_materialization(
    repository: DefaultRouteRepository,
    decision: str,
    digest: str,
    message: str,
) -> None:
    adapter = LearningPlanServiceAdapter(route_repository=repository)
    value = structured_proposal(repository)
    contract = CompiledLongTermContract(
        scope="long_term",
        long_term_plan_content=value.long_term_plan_content,
        total_duration_days=30,
        stages=[
            CompiledLongTermStage(
                stage=1,
                stage_name="基础阶段",
                books=["《中医基础理论》"],
                goal="建立基础框架。",
                duration_days=30,
                schedule_summary="使用《中医基础理论》建立基础框架。",
            )
        ],
        field_anchors={
            "/long_term_plan_content": [
                PlanSourceAnchor(
                    source_field="long_term_plan_content",
                    source_quote=value.long_term_plan_content,
                )
            ]
        },
    )
    diagnosis = DiagnosisResult(
        learning_plan_proposal=value,
        plan_scope="long_term",
        compiled_plan_contract=PlanCompilationEnvelope(
            result=CompiledPlanContractResult(status="compiled", contract=contract),
            source_digest="c" * 64,
        ),
    )
    audit = AuditResult(
        audit_result_id="AUDIT_GATE",
        decision=decision,
        subject_digest=digest,
        plan_scope="long_term",
    )
    context = {
        "case_id": "CASE_GATE",
        "trace_id": "TRACE_GATE",
        "request_id": "REQUEST_GATE",
        "execution_id": "EXECUTION_GATE",
        "step_id": "learning_plan",
        "learner_id": "LEARNER_GATE",
        "dependency_outputs": {
            "diagnosis": AgentEnvelope(
                artifact_id="ART_DIAGNOSIS_GATE",
                artifact_type="diagnosis_result",
                producer="diagnosis_agent",
                payload=diagnosis,
                case_id="CASE_GATE",
                trace_id="TRACE_GATE",
                request_id="REQUEST_GATE",
                execution_id="EXECUTION_GATE",
                step_id="diagnosis",
                task_type="learning_plan",
                learner_id="LEARNER_GATE",
            ),
            "audit": AgentEnvelope(
                artifact_id="ART_AUDIT_GATE",
                artifact_type="audit_result",
                producer="audit_agent",
                payload=audit,
                case_id="CASE_GATE",
                trace_id="TRACE_GATE",
                request_id="REQUEST_GATE",
                execution_id="EXECUTION_GATE",
                step_id="audit",
                task_type="learning_plan",
                learner_id="LEARNER_GATE",
            ),
        },
    }

    with pytest.raises(RuntimeError, match=message):
        await adapter.run(context)

    assert adapter.service.get_current("LEARNER_GATE") is None


def test_container_shares_route_repository_between_resolver_and_service(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    registry = container.review_card_use_case.orchestrator.agent_registry
    resolver = registry.get("default_route_resolver")
    adapter = registry.get("learning_plan_service")

    assert resolver._repository is adapter.service.route_repository


def test_container_injects_same_production_resolvers_into_plan_and_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    formal_resolver = lambda name: "KP_FORMAL" if name == "四君子汤" else None
    class Runtime:
        resolve_executable_knowledge_point = staticmethod(formal_resolver)

        def __getattr__(self, name: str):
            return lambda *args, **kwargs: None

    runtime = Runtime()
    monkeypatch.setattr(
        "competition_app.application.container.load_backend_handoff",
        lambda settings, **_kwargs: runtime,
    )

    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    adapter = container.review_card_use_case.orchestrator.agent_registry.get(
        "learning_plan_service"
    )

    assert (
        adapter.service.knowledge_point_resolver
        is container.daily_task_refresh_service.knowledge_point_resolver
    )
    assert adapter.service.knowledge_point_resolver("四君子汤") == "KP_FORMAL"
    assert adapter.service.video_resource_resolver is None
    assert container.daily_task_refresh_service.video_resource_resolver is None


def test_materialize_daily_task_items_appends_quiz_atom_when_target_count() -> None:
    items = materialize_daily_task_items(
        task_content="完成今日学习。",
        learning_chapter="《方剂学》补益剂·补气",
        estimated_minutes=20,
        focus_knowledge_points=["四君子汤"],
        knowledge_point_resolver=lambda name: (
            "KP_FORMAL_1" if name == "四君子汤" else None
        ),
        video_resource_resolver=None,
        quiz_target_count=12,
    )

    quiz_items = [
        item for item in items
        if item.item_type == "knowledge_practice" and item.completion_policy.get("quiz")
    ]
    assert len(quiz_items) == 1
    quiz = quiz_items[0]
    # 20 分钟：知识点配套练习 3×1.5=4.5，剩余 15.5 → floor(15.5/1.5)=10 题
    assert quiz.title == "完成今日测验"
    assert quiz.kp_id == "KP_FORMAL_1"
    assert quiz.required_question_count == 10
    assert quiz.estimated_minutes == pytest.approx(15.0)
    assert quiz.per_question_estimated_minutes == pytest.approx(1.5)
    assert quiz.completion_policy["quiz"] is True
    assert quiz.completion_policy["quiz_target_count"] == 10
    assert quiz.completion_policy["policy"] == "frozen_question_set"
    # 任务展示时长 = 资源项合计 T（4.5 + 15 = 19.5，不超过预算 20）
    assert sum(item.estimated_minutes for item in items) == pytest.approx(19.5)


def test_materialize_daily_task_items_without_quiz_target_dynamic_quiz() -> None:
    """不传 quiz_target_count 时按剩余时间动态计算测验题数（去静态上限）。"""
    items = materialize_daily_task_items(
        task_content="完成今日学习。",
        estimated_minutes=10,
        focus_knowledge_points=["四君子汤"],
        knowledge_point_resolver=lambda name: (
            "KP_FORMAL_1" if name == "四君子汤" else None
        ),
    )

    quiz_items = [
        item for item in items
        if item.completion_policy.get("quiz")
    ]
    # 10 分钟：练习 4.5，剩余 5.5 → floor(5.5/1.5)=3 题
    assert len(quiz_items) == 1
    assert quiz_items[0].required_question_count == 3
    assert quiz_items[0].completion_policy["quiz_target_count"] == 3
    assert sum(item.estimated_minutes for item in items) == pytest.approx(9.0)


def test_materialize_daily_task_items_no_quiz_without_resolved_kp() -> None:
    items = materialize_daily_task_items(
        task_content="完成今日学习。",
        estimated_minutes=10,
        focus_knowledge_points=["四君子汤"],
        knowledge_point_resolver=lambda name: None,
        quiz_target_count=12,
    )

    assert all(
        item.item_type != "knowledge_practice" or not item.completion_policy.get("quiz")
        for item in items
    )


def test_materialize_daily_task_items_quiz_falls_back_to_review_kp() -> None:
    """当日知识点全部解析失败时，复习知识点仍能保住每日测验。"""
    items = materialize_daily_task_items(
        task_content="完成今日学习。",
        estimated_minutes=10,
        focus_knowledge_points=["今日知识点"],
        knowledge_point_resolver=lambda name: (
            "KP_REVIEW_1" if name == "复习知识点一" else None
        ),
        quiz_target_count=12,
        review_knowledge_points=["复习知识点一", "复习知识点二"],
    )

    quiz_items = [
        item
        for item in items
        if item.completion_policy.get("quiz")
    ]
    assert len(quiz_items) == 1
    quiz = quiz_items[0]
    # 10 分钟：当日知识点解析失败 → 无配套练习，recall 1 分钟，
    # 剩余 9 分钟 → floor(9/1.5)=6 题
    assert quiz.title == "完成今日测验"
    assert quiz.kp_id == "KP_REVIEW_1"
    assert quiz.required_question_count == 6
    assert quiz.completion_policy["quiz_target_count"] == 6
    # 复习知识点不单独生成练习原子，避免任务膨胀
    assert all(
        item.kp_id != "KP_REVIEW_1" or item.completion_policy.get("quiz")
        for item in items
    )


def test_materialize_daily_task_items_quiz_prefers_focus_over_review_kp() -> None:
    """当日与复习知识点都解析成功时，测验锚定当日知识点。"""
    items = materialize_daily_task_items(
        task_content="完成今日学习。",
        estimated_minutes=10,
        focus_knowledge_points=["四君子汤"],
        knowledge_point_resolver=lambda name: (
            "KP_FOCUS_1" if name == "四君子汤" else "KP_REVIEW_1"
        ),
        quiz_target_count=12,
        review_knowledge_points=["复习知识点一"],
    )

    quiz_items = [
        item
        for item in items
        if item.completion_policy.get("quiz")
    ]
    assert len(quiz_items) == 1
    assert quiz_items[0].kp_id == "KP_FOCUS_1"


def test_materialize_daily_task_appends_daily_quiz_atom(
    repository: DefaultRouteRepository,
) -> None:
    """初次生成今日任务必须带上“今日测验（10-15题）”原子项。"""
    service = LearningPlanService(
        repository,
        knowledge_point_resolver=lambda name: (
            "KP_FORMAL_1" if name == "四君子汤" else None
        ),
        video_resource_resolver=lambda resource_ref: None,
    )
    value = structured_proposal(repository)
    value.task_proposal.learning_chapter = "《方剂学》补益剂·补气"
    value.task_proposal.focus_knowledge_points = ["四君子汤"]

    result = service.materialize_daily_task(
        "LEARNER_QUIZ_ATOM",
        value,
        current_short_term_plan={
            "plan_id": "LP_SHORT_EXISTING",
            "short_term_learning_package": None,
        },
    )

    quiz_items = [
        item
        for item in result.learning_task.items
        if item.completion_policy.get("quiz")
    ]
    assert len(quiz_items) == 1
    quiz = quiz_items[0]
    # 任务预算 10 分钟：练习 4.5 + 测验 floor(5.5/1.5)=3 题（动态）
    assert quiz.title == "完成今日测验"
    assert quiz.item_type == "knowledge_practice"
    assert quiz.kp_id == "KP_FORMAL_1"
    assert quiz.required_question_count == 3
    assert quiz.completion_policy["quiz"] is True
    assert quiz.completion_policy["quiz_target_count"] == 3
    assert quiz.completion_policy["policy"] == "frozen_question_set"


def _d3_service(repository: DefaultRouteRepository) -> LearningPlanService:
    return LearningPlanService(
        repository,
        knowledge_point_resolver=lambda name: (
            "KP_FORMAL_1" if name == "四君子汤" else None
        ),
        video_resource_resolver=lambda resource_ref: None,
    )


def _d3_proposal(
    repository: DefaultRouteRepository, *, task_minutes: int = 39
) -> LearningPlanProposal:
    value = structured_proposal(repository, task_minutes=task_minutes)
    value.task_proposal.learning_chapter = "《方剂学》补益剂·补气"
    value.task_proposal.focus_knowledge_points = ["四君子汤"]
    return value


def test_materialize_daily_task_truncates_budget_by_available_minutes(
    repository: DefaultRouteRepository,
) -> None:
    """D3：R = min(S, B)。策略建议 39 分钟、用户仅 15 分钟 → 按 15 分钟物化。"""
    value = _d3_proposal(repository, task_minutes=39)

    result = _d3_service(repository).materialize_daily_task(
        "LEARNER_D3_TRUNCATE",
        value,
        current_short_term_plan={
            "plan_id": "LP_SHORT_EXISTING",
            "short_term_learning_package": None,
        },
        recommended_minutes=39,
        available_minutes=15,
    )

    task = result.learning_task
    total = sum(item.estimated_minutes for item in task.items)
    # 验收：T <= B，且任务展示时长 = 实际资源合计 T
    assert total == pytest.approx(15.0)
    assert total <= 15
    assert task.estimated_minutes == pytest.approx(total)
    # 15 分钟：配套练习 4.5 + 测验 floor(10.5/1.5)=7 题 × 1.5
    quiz = next(item for item in task.items if item.completion_policy.get("quiz"))
    assert quiz.required_question_count == 7
    assert quiz.estimated_minutes == pytest.approx(10.5)
    assert quiz.per_question_estimated_minutes == pytest.approx(1.5)


def test_materialize_daily_task_display_minutes_is_item_total(
    repository: DefaultRouteRepository,
) -> None:
    """D3：任务展示时长 = 资源项合计 T，|T - R| <= 1.5（测验按整题截断）。"""
    value = _d3_proposal(repository, task_minutes=25)

    result = _d3_service(repository).materialize_daily_task(
        "LEARNER_D3_DISPLAY",
        value,
        current_short_term_plan={
            "plan_id": "LP_SHORT_EXISTING",
            "short_term_learning_package": None,
        },
        recommended_minutes=25,
    )

    task = result.learning_task
    total = sum(item.estimated_minutes for item in task.items)
    assert task.estimated_minutes == pytest.approx(total)
    assert abs(total - 25) <= 1.5
    # 25 分钟：练习 4.5 + 测验 floor(20.5/1.5)=13 题 → T=24.0
    assert total == pytest.approx(24.0)
    quiz = next(item for item in task.items if item.completion_policy.get("quiz"))
    assert quiz.required_question_count == 13


def test_materialize_daily_task_exercises_use_per_question_minutes(
    repository: DefaultRouteRepository,
) -> None:
    """D3：知识点配套练习 = 每知识点 3 题 × 1.5 分钟/题。"""
    value = _d3_proposal(repository, task_minutes=20)

    result = _d3_service(repository).materialize_daily_task(
        "LEARNER_D3_EXERCISE",
        value,
        current_short_term_plan={
            "plan_id": "LP_SHORT_EXISTING",
            "short_term_learning_package": None,
        },
        recommended_minutes=20,
    )

    exercises = [
        item
        for item in result.learning_task.items
        if item.item_type == "knowledge_practice"
        and not item.completion_policy.get("quiz")
    ]
    assert len(exercises) == 1
    exercise = exercises[0]
    assert exercise.required_question_count == 3
    assert exercise.estimated_minutes == pytest.approx(4.5)
    assert exercise.per_question_estimated_minutes == pytest.approx(1.5)
    assert result.learning_task.estimated_minutes == pytest.approx(
        sum(item.estimated_minutes for item in result.learning_task.items)
    )


def test_materialize_daily_task_uses_review_kp_loader_for_quiz(
    repository: DefaultRouteRepository,
) -> None:
    """review_knowledge_point_loader 的复习知识点进入每日测验题源池。"""
    service = LearningPlanService(
        repository,
        knowledge_point_resolver=lambda name: (
            "KP_FORMAL_1" if name == "四君子汤" else "KP_REVIEW_1"
        ),
        video_resource_resolver=lambda resource_ref: None,
        review_knowledge_point_loader=lambda learner_id: [
            "复习知识点一",
            "知识点名称待补充",  # 应被过滤
        ],
    )
    value = structured_proposal(repository)
    value.task_proposal.learning_chapter = "《方剂学》补益剂·补气"
    value.task_proposal.focus_knowledge_points = ["四君子汤"]

    result = service.materialize_daily_task(
        "LEARNER_REVIEW_QUIZ",
        value,
        current_short_term_plan={
            "plan_id": "LP_SHORT_EXISTING",
            "short_term_learning_package": None,
        },
    )

    quiz_items = [
        item
        for item in result.learning_task.items
        if item.completion_policy.get("quiz")
    ]
    assert len(quiz_items) == 1
    # 当日知识点解析成功，测验仍锚定当日知识点
    assert quiz_items[0].kp_id == "KP_FORMAL_1"


def test_materialize_daily_task_keeps_quiz_with_only_review_kp(
    repository: DefaultRouteRepository,
) -> None:
    """当日知识点在知识库中缺失（解析失败）时，复习知识点保住测验。"""
    service = LearningPlanService(
        repository,
        knowledge_point_resolver=lambda name: (
            "KP_REVIEW_1" if name == "复习知识点一" else None
        ),
        video_resource_resolver=lambda resource_ref: None,
        review_knowledge_point_loader=lambda learner_id: ["复习知识点一"],
    )
    value = structured_proposal(repository)
    value.task_proposal.learning_chapter = "《方剂学》补益剂·补气"
    value.task_proposal.focus_knowledge_points = ["知识库缺失的知识点"]

    result = service.materialize_daily_task(
        "LEARNER_REVIEW_QUIZ_ONLY",
        value,
        current_short_term_plan={
            "plan_id": "LP_SHORT_EXISTING",
            "short_term_learning_package": None,
        },
    )

    quiz_items = [
        item
        for item in result.learning_task.items
        if item.completion_policy.get("quiz")
    ]
    assert len(quiz_items) == 1
    assert quiz_items[0].kp_id == "KP_REVIEW_1"
    assert quiz_items[0].completion_policy["quiz"] is True


def test_ensure_executable_daily_resources_keeps_quiz_policy(
    repository: DefaultRouteRepository,
) -> None:
    service = LearningPlanService(
        repository,
        knowledge_point_resolver=lambda name: (
            "KP_FORMAL_1" if name == "四君子汤" else None
        ),
        video_resource_resolver=lambda resource_ref: None,
    )
    # 含 recall 原子项的任务在 ensure 时会被重新物化并追加每日测验。
    value = structured_proposal(
        repository,
        task_content="对照纠错",
        task_blocks=[
            {
                "content": "对照纠错",
                "estimated_minutes": 5,
                "item_type": "recall",
            }
        ],
    )
    value.task_proposal.learning_chapter = "《方剂学》补益剂·补气"
    value.task_proposal.estimated_minutes = 20
    value.task_proposal.focus_knowledge_points = ["四君子汤"]
    service.materialize("LEARNER_QUIZ_RESOURCES", value)

    normalized = service.ensure_executable_daily_resources("LEARNER_QUIZ_RESOURCES")

    assert normalized is not None
    quiz_items = [
        item for item in normalized.items
        if item.item_type == "knowledge_practice" and item.completion_policy.get("quiz")
    ]
    assert len(quiz_items) == 1
    # 20 分钟预算：回忆 1 + 练习 4.5 → 测验 floor(14.5/1.5)=9 题（动态）
    assert quiz_items[0].required_question_count == 9
    assert quiz_items[0].completion_policy["quiz_target_count"] == 9


_INTERVENTION_WITH_MISTAKE_REVIEW = {
    "intervention_id": "6",
    "action": "安排错题复盘",
    "reason": "系统根据近期学习监控判断当前处于“错题积压”，存在重复出现的"
    "薄弱知识点（中医诊断学·舌诊）。",
}


def test_ensure_executable_daily_resources_preserves_intervention_item(
    repository: DefaultRouteRepository,
) -> None:
    """已接受的干预项由干预模块维护，正文重建不得删除它。

    回归：干预项 item_type=recall 曾使 ensure 判定任务“不可执行”，于是按
    正文重新物化整个 items 列表，把用户刚确认的安排静默删除。
    """
    service = LearningPlanService(
        repository,
        knowledge_point_resolver=lambda name: (
            "KP_FORMAL_1"
            if name in {"四君子汤", "中医诊断学·舌诊"}
            else None
        ),
        video_resource_resolver=lambda resource_ref: None,
    )
    value = structured_proposal(repository)
    value.task_proposal.task_content = "学习四君子汤并完成配套练习"
    value.task_proposal.learning_chapter = "《方剂学》补益剂·补气"
    value.task_proposal.estimated_minutes = 20
    value.task_proposal.focus_knowledge_points = ["四君子汤"]
    value.short_term_learning_package.task_blocks = [
        "学习四君子汤并完成配套练习"
    ]
    learner_id = "LEARNER_INTERVENTION_KEEP"
    service.materialize(learner_id, value)

    applied = apply_accepted_intervention(
        service, learner_id, _INTERVENTION_WITH_MISTAKE_REVIEW
    )
    assert applied["applied"] is True
    accepted = service.get_current(learner_id).learning_task
    assert accepted.items[-1].item_type == "knowledge_practice"

    normalized = service.ensure_executable_daily_resources(learner_id)

    assert normalized is not None
    assert [item.item_type for item in normalized.items] == [
        item.item_type for item in accepted.items
    ]
    preserved = normalized.items[-1]
    assert preserved.resource_ref["source"] == "learning_intervention"
    assert preserved.resource_ref["intervention_id"] == "6"
    assert preserved.title.startswith("错题复盘：")
    assert [item.ordinal for item in normalized.items] == list(
        range(1, len(normalized.items) + 1)
    )


def test_ensure_executable_daily_resources_rebuilds_around_intervention_item(
    repository: DefaultRouteRepository,
) -> None:
    """legacy 任务重建时，外部干预项被追加保留而不是丢弃。"""
    service = LearningPlanService(
        repository,
        knowledge_point_resolver=lambda name: (
            "KP_FORMAL_1"
            if name in {"四君子汤", "中医诊断学·舌诊"}
            else None
        ),
        video_resource_resolver=lambda resource_ref: None,
    )
    value = structured_proposal(
        repository,
        task_content="对照纠错",
        task_blocks=[
            {
                "content": "对照纠错",
                "estimated_minutes": 5,
                "item_type": "recall",
            }
        ],
    )
    value.task_proposal.learning_chapter = "《方剂学》补益剂·补气"
    value.task_proposal.estimated_minutes = 20
    value.task_proposal.focus_knowledge_points = ["四君子汤"]
    learner_id = "LEARNER_INTERVENTION_REBUILD"
    service.materialize(learner_id, value)

    applied = apply_accepted_intervention(
        service, learner_id, _INTERVENTION_WITH_MISTAKE_REVIEW
    )
    assert applied["applied"] is True

    normalized = service.ensure_executable_daily_resources(learner_id)

    assert normalized is not None
    preserved = [
        item
        for item in normalized.items
        if item.resource_ref.get("source") == "learning_intervention"
    ]
    assert len(preserved) == 1
    # 重建后仍追加在末尾，且序号连续。
    assert preserved[0].ordinal == len(normalized.items)
    assert [item.ordinal for item in normalized.items] == list(
        range(1, len(normalized.items) + 1)
    )
    # 旧的无来源 recall 项被重建为可执行原子，外部干预项不受影响。
    assert [
        item
        for item in normalized.items
        if item.item_type == "recall"
        and item.resource_ref.get("source") != "learning_intervention"
    ] == []
    assert normalized.estimated_minutes >= sum(
        item.estimated_minutes for item in normalized.items
    )


def _video_ref(*, duration_seconds: int) -> dict[str, object]:
    """构造满足 DailyTaskItemSpec 校验的视频资源引用。"""
    return {
        "source": "test",
        "duration_seconds": duration_seconds,
        "start_seconds": 0,
        "end_seconds": duration_seconds,
    }


def test_materialize_daily_task_items_prunes_fixed_atoms_to_hard_budget() -> None:
    """D3：固定成本 C > R 时从列表末尾整项删除，直到 C <= R 并重算测验。

    4 分钟视频 + 3 个 4.5 分钟练习，R=15：删除一个练习后 C=13，
    测验 floor(2/1.5)=1 题，T=14.5 <= 15。
    """
    resolver = lambda name, learning_chapter="": f"KP_{name}"  # noqa: E731
    blocks = [
        {
            "item_type": "knowledge_practice",
            "content": f"练习{index}",
            "knowledge_point_name": f"KP{index}",
            "required_question_count": 3,
        }
        for index in range(1, 4)
    ]
    items = materialize_daily_task_items(
        task_content="今日任务",
        learning_chapter="《方剂学》补益剂·补气",
        estimated_minutes=15.0,
        focus_knowledge_points=[],
        task_blocks=blocks,
        knowledge_point_resolver=resolver,
        video_resource_resolver=lambda resource_ref: _video_ref(duration_seconds=181),
        review_knowledge_points=["KP1"],
    )

    total = sum(item.estimated_minutes for item in items)
    assert total == pytest.approx(14.5)
    assert total <= 15.0
    videos = [item for item in items if item.item_type == "video_section"]
    assert len(videos) == 1
    assert videos[0].estimated_minutes == pytest.approx(4.0)
    exercises = [
        item
        for item in items
        if item.item_type == "knowledge_practice"
        and not item.completion_policy.get("quiz")
    ]
    # 4 分钟视频 + 2 个完整练习 + 1 道每日测验
    assert len(exercises) == 2
    assert all(item.estimated_minutes == pytest.approx(4.5) for item in exercises)
    quizzes = [
        item
        for item in items
        if item.completion_policy.get("quiz")
    ]
    assert len(quizzes) == 1
    assert quizzes[0].required_question_count == 1
    assert quizzes[0].estimated_minutes == pytest.approx(1.5)


def test_materialize_daily_task_items_removes_oversized_video_without_clipping() -> None:
    """D3：单个原子时长 > R 时整项删除，不伪造成更短的视频。

    20 分钟视频 + R=15：视频被整项移除，练习与测验按剩余预算安排。
    """
    resolver = lambda name, learning_chapter="": f"KP_{name}"  # noqa: E731
    items = materialize_daily_task_items(
        task_content="今日任务",
        learning_chapter="《方剂学》补益剂·补气",
        estimated_minutes=15.0,
        focus_knowledge_points=["KP1"],
        task_blocks=[],
        knowledge_point_resolver=resolver,
        video_resource_resolver=lambda resource_ref: _video_ref(
            duration_seconds=20 * 60
        ),
    )

    total = sum(item.estimated_minutes for item in items)
    assert total == pytest.approx(15.0)
    assert total <= 15.0
    videos = [item for item in items if item.item_type == "video_section"]
    # 视频被整项删除，而不是被伪造成 15 分钟视频
    assert videos == []
    exercises = [
        item
        for item in items
        if item.item_type == "knowledge_practice"
        and not item.completion_policy.get("quiz")
    ]
    assert len(exercises) == 1
    assert exercises[0].estimated_minutes == pytest.approx(4.5)
    quizzes = [
        item
        for item in items
        if item.completion_policy.get("quiz")
    ]
    assert len(quizzes) == 1
    # 剩余预算 10.5 分钟 → floor(10.5/1.5)=7 题
    assert quizzes[0].required_question_count == 7
    assert quizzes[0].estimated_minutes == pytest.approx(10.5)


def test_materialize_daily_task_persists_selected_and_deferred_kps(
    repository: DefaultRouteRepository,
) -> None:
    resolver_map = {
        "四君子汤": "KP_FJ_001",
        "参苓白术散": "KP_FJ_002",
        "补中益气汤": "KP_FJ_003",
    }
    service = LearningPlanService(
        repository,
        knowledge_point_resolver=lambda name, chapter="": resolver_map.get(name),
        video_resource_resolver=lambda resource_ref: None,
    )
    value = structured_proposal(repository, task_minutes=30)
    value.task_proposal.learning_chapter = "《方剂学》补益剂·补气"
    value.task_proposal.focus_knowledge_points = list(resolver_map)

    result = service.materialize_daily_task(
        "LEARNER_SCHEDULED_DAILY",
        value,
        current_short_term_plan={
            "plan_id": "LP_SHORT_EXISTING",
            "short_term_learning_package": None,
        },
        available_minutes=30,
    )

    task = result.learning_task
    assert task.daily_task_schedule is not None
    assert len(task.daily_task_schedule.selected) == 2
    assert len(task.daily_task_schedule.deferred) == 1
    assert set(task.focus_knowledge_points) == {
        item.knowledge_point_name for item in task.daily_task_schedule.selected
    }
    assert "已顺延" in task.daily_task_schedule.explanation
    assert sum(item.estimated_minutes for item in task.items) <= 30

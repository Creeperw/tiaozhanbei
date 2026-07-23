from pathlib import Path
from types import SimpleNamespace

import pytest
import json

from competition_app.agents.default_route_resolver import DefaultRouteResolverAgent
from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.application.container import ApplicationContainer
from competition_app.application.personalized_review_card import (
    ReviewCardRequest,
    WorkflowResumeRequest,
)
from competition_app.config import Settings
from competition_app.contracts.learning_plan import LearningPlanProposal
from competition_app.llm.stub import StubChatModel
from competition_app.services.default_route import DefaultRouteRepository


class CapturingStubChatModel(StubChatModel):
    def __init__(self) -> None:
        self.request = None

    async def complete_json(self, role, payload, on_delta=None):
        self.request = payload
        return await super().complete_json(role, payload, on_delta)


class FalseReusePlanModel(StubChatModel):
    def __init__(self) -> None:
        self.schema_properties: dict = {}

    async def complete_json(self, role, payload, on_delta=None):
        result = await super().complete_json(role, payload, on_delta)
        if role == "diagnosis_agent":
            self.schema_properties = payload["payload"]["output_schema"]["properties"]
        return result


class CountingPlanModel(StubChatModel):
    def __init__(self) -> None:
        self.diagnosis_calls = 0

    async def complete_json(self, role, payload, on_delta=None):
        if role == "diagnosis_agent":
            self.diagnosis_calls += 1
        return await super().complete_json(role, payload, on_delta)


def plan_input(record) -> dict:
    return record.model_dump(mode="json")


async def build_layered_plan(
    container: ApplicationContainer,
    *,
    learner_id: str,
    available_minutes: int = 20,
    user_profile: dict | None = None,
    topic: str = "四君子汤",
) -> SimpleNamespace:
    """Build the three independently persisted layers used by integration tests."""

    profile = user_profile or {
        "goals": {"type": "course", "name": "系统掌握方剂学"}
    }
    goal_type = str(profile.get("goals", {}).get("type", ""))
    course_confirmation = "，仅作为课程学习，不参加考试" if goal_type == "course" else ""
    long_result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request=f"请为{topic}制定长期学习规划{course_confirmation}",
            available_minutes=available_minutes,
            user_profile=profile,
            plan_scope="long_term",
        )
    )
    long_term_plan = long_result.learning_plan.long_term_plan
    short_result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request=f"请根据长期规划制定{topic}本周学习计划",
            available_minutes=available_minutes,
            user_profile=profile,
            long_term_plan=plan_input(long_term_plan),
            plan_scope="short_term",
        )
    )
    short_term_plan = short_result.learning_plan.short_term_plan
    daily_result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="请根据本周计划安排今天的任务",
            available_minutes=available_minutes,
            user_profile=profile,
            long_term_plan=plan_input(long_term_plan),
            short_term_plan=plan_input(short_term_plan),
            plan_scope="daily_task",
        )
    )
    return SimpleNamespace(
        long_result=long_result,
        short_result=short_result,
        daily_result=daily_result,
        long_term_plan=long_term_plan,
        short_term_plan=short_term_plan,
        learning_task=daily_result.learning_plan.learning_task,
    )


@pytest.mark.asyncio
async def test_stub_workflow_materializes_diagnosis_plan_and_learning_task(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    plan = await build_layered_plan(
        container, learner_id="LEARNER_FRAMEWORK_1", available_minutes=15
    )

    plan_output = next(
        item
        for item in plan.long_result.agent_outputs
        if item.producer == "learning_plan_service"
    )
    assert plan_output.payload.generated_scope == "long_term"
    producers = [item.producer for item in plan.long_result.agent_outputs]
    assert set(producers) == {
        "planner_agent", "default_route_resolver", "diagnosis_agent",
        "learning_plan_service",
    }
    assert producers.index("planner_agent") < producers.index("diagnosis_agent")
    assert producers.index("default_route_resolver") < producers.index("diagnosis_agent")
    assert producers.index("diagnosis_agent") < producers.index("learning_plan_service")
    assert plan.long_term_plan.content
    assert plan.short_term_plan.long_term_plan_id == plan.long_term_plan.plan_id
    assert plan.learning_task.short_term_plan_id == plan.short_term_plan.plan_id
    assert plan.learning_task.status == "pending"
    assert plan.long_term_plan.content != plan.short_term_plan.content
    assert plan.short_term_plan.content != plan.learning_task.task_content


@pytest.mark.asyncio
async def test_existing_valid_long_and_short_plans_are_reused_verbatim(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    learner_id = "LEARNER_REUSE_INTEGRATION"
    initial_plan = await build_layered_plan(
        container,
        learner_id=learner_id,
        user_profile={"goals": {"type": "course", "name": "系统掌握方剂学"}},
    )

    reused = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="请查看我的学习计划并安排今日任务",
            available_minutes=15,
            user_profile={"goals": {"long_term_goal": "系统掌握方剂学"}},
            long_term_plan=plan_input(initial_plan.long_term_plan),
            short_term_plan=plan_input(initial_plan.short_term_plan),
            learning_task=initial_plan.learning_task.model_dump(mode="json"),
        )
    )

    assert reused.learning_plan.generated_scope == "daily_task"
    assert reused.learning_plan.long_term_plan is None
    assert reused.learning_plan.short_term_plan is None
    assert reused.learning_plan.learning_task.version == initial_plan.learning_task.version + 1
    persisted = container.review_card_use_case.plan_repository.get_current(learner_id)
    assert persisted.long_term_plan.content == initial_plan.long_term_plan.content
    assert persisted.short_term_plan.content == initial_plan.short_term_plan.content
    assert persisted.long_term_plan.version == initial_plan.long_term_plan.version
    assert persisted.short_term_plan.version == initial_plan.short_term_plan.version


@pytest.mark.asyncio
async def test_today_learning_question_is_materialized_as_daily_task(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    learner_id = "LEARNER_TODAY_SCOPE"
    initial = await build_layered_plan(
        container, learner_id=learner_id, available_minutes=30, topic="方剂学"
    )

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="我今天要学习些什么东西？",
            available_minutes=25,
            long_term_plan=plan_input(initial.long_term_plan),
            short_term_plan=plan_input(initial.short_term_plan),
            learning_task=initial.learning_task.model_dump(mode="json"),
        )
    )

    assert result.task_type == "learning_plan"
    assert result.learning_plan.generated_scope == "daily_task"
    assert result.learning_plan.long_term_plan is None
    assert result.learning_plan.short_term_plan is None
    assert result.learning_plan.learning_task.short_term_plan_id == (
        initial.short_term_plan.plan_id
    )


@pytest.mark.asyncio
async def test_explicit_time_change_updates_only_short_term_and_daily_layers(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    learner_id = "LEARNER_SHORT_CHANGE_INTEGRATION"
    initial_plan = await build_layered_plan(
        container,
        learner_id=learner_id,
        available_minutes=25,
        user_profile={"goals": {"type": "course", "name": "系统掌握方剂学"}},
    )

    changed = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="未来两周每天只有15分钟，请调整短期计划",
            available_minutes=15,
            user_profile={"goals": {"type": "course", "name": "系统掌握方剂学"}},
            long_term_plan=plan_input(initial_plan.long_term_plan),
            short_term_plan=plan_input(initial_plan.short_term_plan),
            learning_task=initial_plan.learning_task.model_dump(mode="json"),
        )
    )

    assert changed.learning_plan.generated_scope == "short_term"
    assert changed.learning_plan.long_term_plan is None
    assert changed.learning_plan.short_term_plan.version == initial_plan.short_term_plan.version + 1
    assert changed.learning_plan.short_term_plan.content != initial_plan.short_term_plan.content
    assert "未来两周" in changed.learning_plan.short_term_plan.content
    assert changed.learning_plan.learning_task is None
    assert changed.learning_plan.invalidated_layers == ["daily_task"]
    persisted = container.review_card_use_case.plan_repository.get_current(learner_id)
    assert persisted.long_term_plan.content == initial_plan.long_term_plan.content
    assert persisted.learning_task is None


@pytest.mark.asyncio
async def test_due_review_keeps_a_verifiable_mainline_maintenance_action(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    learner_id = "LEARNER_DUE_REVIEW_MAINTENANCE"
    initial = await build_layered_plan(
        container, learner_id=learner_id, available_minutes=10
    )

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="请结合到期复习和本周计划，安排今天的任务",
            available_minutes=10,
            user_profile={"goals": {"type": "course", "name": "系统掌握方剂学"}},
            long_term_plan=plan_input(initial.long_term_plan),
            short_term_plan=plan_input(initial.short_term_plan),
            plan_scope="daily_task",
            user_knowledge_state=[{
                "kp_id": "020490",
                "knowledge_mastery": 0.52,
                "kp_review_status": "需要继续复习",
            }],
        )
    )

    daily = result.learning_plan.learning_task.task_content
    assert "短期计划" in daily
    assert "主线" in daily
    assert "三分钟" in daily
    assert "恢复检查点" in daily


@pytest.mark.asyncio
async def test_stub_learning_plan_contains_actionable_long_and_short_horizon_content(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    plan = await build_layered_plan(
        container,
        learner_id="LEARNER_ACTIONABLE_PLAN",
        available_minutes=30,
        user_profile={
            "goals": {
                "type": "course",
                "name": "系统掌握方剂学",
                "short_term_goal": "两周内掌握四君子汤组成、功效和配伍",
            }
        },
    )
    long_term = plan.long_term_plan.content
    short_term = plan.short_term_plan.content
    assert "四君子汤" in long_term
    assert "阶段" in long_term
    assert "验收" in long_term or "标准" in long_term
    assert "一周" in short_term or "第1周" in short_term
    assert "产出" in short_term
    assert "完成标准" in short_term
    assert plan.learning_task.task_content
    assert plan.learning_task.task_content != short_term
    assert plan.short_term_plan.recovery_policy is not None


@pytest.mark.asyncio
async def test_stub_learning_plan_uses_approved_route_for_physician_goal(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    plan = await build_layered_plan(
        container,
        learner_id="LEARNER_APPROVED_ROUTE",
        available_minutes=30,
        user_profile={
            "goals": {"type": "credential", "name": "中医执业医师"}
        },
        topic="中医执业医师考试",
    )

    route = plan.long_term_plan.planning_route
    assert route.planning_status == "approved_route"
    assert route.route_id == "tcm_physician_standard_degree"
    assert route.textbook_route is not None
    assert route.textbook_route.route is not None
    assert route.textbook_route.route.route_id == "textbook_tcm_physician"
    assert plan.long_term_plan.textbook_selection is not None
    assert plan.long_term_plan.textbook_selection.books
    assert len(plan.long_term_plan.textbook_selection.books) <= 2
    assert plan.short_term_plan.textbook_selection is not None
    assert plan.short_term_plan.textbook_selection.books
    assert route.route_version >= 1
    assert plan.long_term_plan.milestones
    snapshot = json.loads(plan.long_result.snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["learning_plan"]["long_term_plan"]["planning_route"]["planning_status"] == "approved_route"
    long_term = plan.long_term_plan.content
    textbook_stages = route.textbook_route.route.stages
    for stage in textbook_stages:
        assert stage.books
        assert stage.name in long_term
        assert stage.objective in long_term
        for book in stage.books:
            assert book in long_term
        for evidence in stage.exit_evidence:
            assert evidence in long_term
    assert route.route_id not in long_term
    assert "默认路线版本" not in long_term
    assert "来源编号" not in long_term


@pytest.mark.asyncio
async def test_vague_nursing_exam_requests_route_clarification(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="LEARNER_NURSING_ROUTE_CLARIFICATION",
            user_request="我想学护理准备考试，请制定学习计划",
            available_minutes=30,
            plan_scope="long_term",
        )
    )

    assert result.learning_plan.requires_clarification
    assert result.learning_plan.clarification_questions
    assert "具体考试" in "".join(result.learning_plan.clarification_questions)


@pytest.mark.asyncio
async def test_long_plan_requires_one_route_when_user_selects_two(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="LEARNER_MULTIPLE_PHYSICIAN_ROUTES",
            user_request="规定学历、中医（专长）医师考核",
            available_minutes=60,
            plan_scope="long_term",
        )
    )

    assert result.learning_plan.requires_clarification
    assert result.learning_plan.requested_scope == "long_term"
    assert result.learning_plan.clarification_questions == [
        "你同时选择了多个不同的报考路径，它们不能合并为同一条长期规划。"
        "请只确认一项：规定学历路径、中医（专长）医师资格考核，"
        "或传统医学师承/确有专长人员考核。"
    ]


@pytest.mark.asyncio
async def test_long_plan_resumes_with_one_route_and_updates_structured_stages(
    tmp_path,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    thread_id = "THREAD_MULTIPLE_PHYSICIAN_ROUTES"
    interrupted = await container.review_card_use_case.execute(
        ReviewCardRequest(
            thread_id=thread_id,
            learner_id="LEARNER_MULTIPLE_PHYSICIAN_ROUTES_RESUME",
            user_request="规定学历、中医（专长）医师考核",
            available_minutes=60,
            plan_scope="long_term",
        )
    )

    resumed = await container.review_card_use_case.resume(
        thread_id,
        WorkflowResumeRequest(answer="规定学历路径。", plan_scope="long_term"),
    )

    assert interrupted.status == "interrupted"
    assert resumed.status == "success"
    long_term = resumed.learning_plan.long_term_plan
    assert long_term.planning_route.route_id == "tcm_physician_standard_degree"
    assert (
        long_term.planning_route.textbook_route.route.route_id
        == "textbook_tcm_physician"
    )
    trusted_stages = long_term.planning_route.textbook_route.route.stages
    assert len(long_term.stages) == len(trusted_stages)
    assert [stage.stage for stage in long_term.stages] == list(
        range(1, len(trusted_stages) + 1)
    )
    assert [stage.book for stage in long_term.stages] == [
        stage.books for stage in trusted_stages
    ]


@pytest.mark.asyncio
async def test_long_plan_continues_after_user_supplies_exact_exam_goal(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    thread_id = "THREAD_LONG_PLAN_EXAM_FOLLOWUP"

    interrupted = await container.review_card_use_case.execute(
        ReviewCardRequest(
            thread_id=thread_id,
            learner_id="LEARNER_LONG_PLAN_EXAM_FOLLOWUP",
            user_request="请结合我的学习状态，给我制定一份长期学习计划。",
            plan_scope="long_term",
        )
    )
    resumed = await container.review_card_use_case.resume(
        thread_id,
        WorkflowResumeRequest(
            answer="我想考中医执业医师资格考试",
            plan_scope="long_term",
        ),
    )

    assert interrupted.status == "interrupted"
    assert resumed.status == "success"
    assert resumed.learning_plan.generated_scope == "long_term"
    assert resumed.learning_plan.short_term_plan is None
    assert resumed.learning_plan.learning_task is None
    assert (
        resumed.learning_plan.long_term_plan.planning_route.route_id
        == "tcm_physician_standard_degree"
    )


@pytest.mark.asyncio
async def test_integrated_long_plan_collects_and_writes_required_profile_fields(tmp_path) -> None:
    container = ApplicationContainer.build(
        Settings(mode="stub"), snapshot_root=tmp_path, include_backend_handoff=False
    )
    writes: list[dict] = []
    container.review_card_use_case.behavior_context_loader = lambda _: {
        "source": "frontend_backend",
        "user_profile": {"learning_goal": "中医执业医师资格考试"},
        "learning_target": {"exam_name": "中医执业医师资格考试"},
    }

    def write_profile(_learner_id: str, updates: dict, _execution_id: str | None) -> dict:
        writes.append(dict(updates))
        return updates

    container.review_card_use_case.profile_update_writer = write_profile
    thread_id = "THREAD_PROFILE_GATE"
    first = await container.review_card_use_case.execute(
        ReviewCardRequest(
            thread_id=thread_id,
            learner_id="LEARNER_PROFILE_GATE",
            user_request="请结合我的学习状态，给我制定一份长期学习计划。",
            plan_scope="long_term",
        )
    )
    second = await container.review_card_use_case.resume(
        thread_id,
        WorkflowResumeRequest(answer="零基础", plan_scope="long_term"),
    )
    completed = await container.review_card_use_case.resume(
        thread_id,
        WorkflowResumeRequest(answer="每周学习5天，每天2小时", plan_scope="long_term"),
    )

    assert first.status == "interrupted"
    assert first.interrupt["profile_fields"] == ["learning_background"]
    assert second.status == "interrupted"
    assert second.interrupt["profile_fields"] == ["time_constraints"]
    assert completed.status == "success"
    assert writes == [
        {"learning_background": "零基础"},
        {"time_constraints": "每周学习5天，每天2小时"},
    ]


@pytest.mark.asyncio
async def test_partial_inline_profile_still_collects_and_resumes_missing_goal(
    tmp_path,
) -> None:
    container = ApplicationContainer.build(
        Settings(mode="stub"), snapshot_root=tmp_path, include_backend_handoff=False
    )
    stored_profile = {
        "learning_background": "零基础",
        "time_constraints": "每周学习4天，每天2小时",
    }
    writes: list[dict] = []
    container.review_card_use_case.behavior_context_loader = lambda _: {
        "source": "frontend_backend",
        "user_profile": dict(stored_profile),
    }

    def write_profile(_learner_id: str, updates: dict, _execution_id: str | None) -> dict:
        writes.append(dict(updates))
        stored_profile.update(updates)
        return dict(stored_profile)

    container.review_card_use_case.profile_update_writer = write_profile
    thread_id = "THREAD_PARTIAL_INLINE_PROFILE"
    interrupted = await container.review_card_use_case.execute(
        ReviewCardRequest(
            thread_id=thread_id,
            learner_id="LEARNER_PARTIAL_INLINE_PROFILE",
            user_request="请结合我的学习状态，给我制定一份长期学习计划。",
            plan_scope="long_term",
            user_profile={"learning_background": "零基础"},
        )
    )
    resumed = await container.review_card_use_case.resume(
        thread_id,
        WorkflowResumeRequest(
            answer="中医执业医师考试",
            plan_scope="long_term",
        ),
    )

    assert interrupted.status == "interrupted"
    assert interrupted.interrupt["interrupt_type"] == "profile_completion"
    assert interrupted.interrupt["profile_fields"] == ["learning_goal"]
    assert resumed.status == "success"
    assert writes == [{"learning_goal": "中医执业医师考试"}]
    assert resumed.learning_plan.long_term_plan.planning_route.route_id == (
        "tcm_physician_standard_degree"
    )


@pytest.mark.asyncio
async def test_stub_formula_course_plan_uses_book_level_approved_route(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    plan = await build_layered_plan(
        container,
        learner_id="LEARNER_FORMULA_ROUTE",
        available_minutes=25,
        user_profile={
            "goals": {
                "type": "course",
                "name": "系统掌握方剂学",
                "short_term_goal": "本周掌握四君子汤",
            }
        },
    )

    route = plan.long_term_plan.planning_route
    assert route.planning_status == "approved_route"
    assert route.route_id == "tcm_formula_course"
    assert [phase.books for phase in route.phases] == [
        ["《中医基础理论》", "《中医诊断学》", "《中药学》"],
        ["《方剂学》"],
        ["《方剂学》", "《中医内科学》"],
        ["《方剂学》", "《中医内科学》"],
    ]
    assert [stage.model_dump() for stage in plan.long_term_plan.stages] == [
        {
            "stage": index,
            "book": phase.books,
            "goal": phase.objective,
        }
        for index, phase in enumerate(route.phases, start=1)
    ]
    long_term = plan.long_term_plan.content
    assert "《中医基础理论》、《中医诊断学》、《中药学》" in long_term
    assert "《方剂学》" in long_term


@pytest.mark.asyncio
async def test_empty_existing_plan_cannot_suppress_approved_book_route(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    model = FalseReusePlanModel()
    container.review_card_use_case.orchestrator.agent_registry.get(
        "diagnosis_agent"
    ).chat_model = model

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="LEARNER_FALSE_REUSE",
            user_request="请为四君子汤制定长期学习规划，仅作为课程学习，不参加考试",
            available_minutes=25,
            user_profile={"goals": {"type": "course", "name": "系统掌握方剂学"}},
            long_term_plan={},
            plan_scope="long_term",
        )
    )

    long_term = result.learning_plan.long_term_plan.content
    assert "long_term_plan_action" not in model.schema_properties
    assert "tcm_formula_course" not in long_term
    assert "《中医基础理论》" in long_term
    assert "《方剂学》" in long_term


@pytest.mark.asyncio
async def test_stub_learning_plan_falls_back_to_provisional_for_literacy_goal(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="LEARNER_PROVISIONAL_ROUTE",
            user_request="我想提升中医经典阅读与学术表达能力，请制定学习计划",
            available_minutes=25,
            user_profile={
                "goals": {
                    "type": "literacy",
                    "name": "中医经典阅读与学术表达",
                }
            },
            plan_scope="long_term",
        )
    )

    assert result.learning_plan.requires_clarification
    assert "具体考试" in "".join(result.learning_plan.clarification_questions)


@pytest.mark.asyncio
async def test_real_repository_resolver_route_context_reaches_diagnosis_and_formal_proposal() -> None:
    route_directory = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "default_routes"
    )
    repository = DefaultRouteRepository.from_directory(route_directory)
    resolver_context = {
        "case_id": "CASE_REAL_ROUTE",
        "trace_id": "TRACE_REAL_ROUTE",
        "request_id": "REQUEST_REAL_ROUTE",
        "execution_id": "EXECUTION_REAL_ROUTE",
        "step_id": "route_resolution",
        "task_type": "learning_plan",
        "learner_id": "LEARNER_REAL_ROUTE",
        "user_request": "请制定中医执业医师学习计划",
        "user_profile": {
            "goals": {"type": "credential", "name": "中医执业医师"}
        },
    }
    route_output = await DefaultRouteResolverAgent(repository).run(resolver_context)
    model = CapturingStubChatModel()
    diagnosis_context = {
        **resolver_context,
        "step_id": "diagnosis",
        "available_minutes": 15,
        "dependency_outputs": {"route_resolution": route_output},
    }

    proposal = (
        await DiagnosisAgent(model).run(diagnosis_context)
    ).payload.learning_plan_proposal

    route_context = model.request["payload"]["default_route"]
    assert route_context["planning_status"] == "approved_route"
    assert route_context["phases"] == [
        {
            key: phase.model_dump().get(key)
            for key in (
                "name", "objective", "books", "learning_focus",
                "sequence_basis", "exit_evidence",
            )
        }
        for phase in route_output.payload.phases
    ]
    assert "route_id" not in route_context
    assert "sources" not in route_context
    assert "runtime_checks" not in route_context
    assert proposal.planning_route == route_output.payload
    assert LearningPlanProposal.model_validate(proposal.model_dump()) == proposal


@pytest.mark.asyncio
async def test_vague_replan_returns_clarification_without_calling_planning_model(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    model = CountingPlanModel()
    container.review_card_use_case.orchestrator.agent_registry.get(
        "diagnosis_agent"
    ).chat_model = model

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="LEARNER_CLARIFY",
            user_request="这个长期规划我不满意，重新计划一下",
            long_term_plan={"plan_id": "L1", "content": "原长期正文", "version": 2, "status": "active"},
            short_term_plan={"plan_id": "S1", "content": "原短期正文", "version": 4, "status": "active"},
        )
    )

    assert model.diagnosis_calls == 0
    assert result.learning_plan.requires_clarification
    assert result.learning_plan.clarification_questions
    assert result.learning_plan.reason
    assert not hasattr(result.learning_plan, "long_term_plan")
    assert not hasattr(result.learning_plan, "short_term_plan")
    assert not hasattr(result.learning_plan, "learning_task")
    assert not any(
        item.agent == "diagnosis_agent" for item in result.model_trace
    )


@pytest.mark.asyncio
async def test_confirmed_clarification_regenerates_long_and_short_plans(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="LEARNER_CONFIRMED_REPLAN",
            user_request="补充说明：希望按基础、代表方、类方辨析和综合应用分阶段学习。",
            long_term_plan={
                "plan_id": "L1",
                "content": "原长期正文",
                "version": 2,
                "status": "active",
            },
            short_term_plan={
                "plan_id": "S1",
                "content": "原短期正文",
                "version": 4,
                "status": "active",
            },
            user_profile={
                "goals": {
                    "type": "course",
                    "name": "系统掌握方剂学",
                    "short_term_goal": "本周掌握补气类方剂",
                }
            },
            plan_change_context={
                "original_request": "这个长期规划我不满意，重新计划一下",
                "target_layers": ["long_term"],
                "change_details": "希望按基础、代表方、类方辨析和综合应用分阶段学习；确认仅作为方剂学课程学习，不参加考试。",
                "expected_outcome": "形成可验收的阶段路线。",
            },
            plan_scope="long_term",
        )
    )

    assert not getattr(result.learning_plan, "requires_clarification", False)
    assert result.learning_plan.long_term_plan.content != "原长期正文"
    assert result.learning_plan.short_term_plan is None
    assert result.learning_plan.invalidated_layers == ["short_term", "daily_task"]

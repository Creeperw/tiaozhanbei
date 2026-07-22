from pathlib import Path

import pytest

from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.application.container import ApplicationContainer
from competition_app.application.personalized_review_card import ReviewCardRequest
from competition_app.config import Settings
from competition_app.llm.stub import StubChatModel


class CapturingStubChatModel(StubChatModel):
    def __init__(self) -> None:
        self.last_payload = None

    async def complete_json(self, role, payload, on_delta=None):
        if role == "diagnosis_agent":
            self.last_payload = payload
        return await super().complete_json(role, payload, on_delta)


def plan_input(record) -> dict:
    return record.model_dump(mode="json")


@pytest.mark.asyncio
async def test_explicit_plan_scopes_generate_one_layer_at_a_time(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    learner_id = "LAYERED_PLAN_1"
    profile = {"goals": {"type": "credential", "name": "中医执业医师"}}

    long_result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="请制定中医执业医师考试长期规划",
            plan_scope="long_term",
            user_profile=profile,
        )
    )
    long_plan = long_result.learning_plan
    assert long_plan.generated_scope == "long_term"
    assert long_plan.long_term_plan is not None
    assert long_plan.short_term_plan is None
    assert long_plan.learning_task is None
    assert long_plan.invalidated_layers == ["short_term", "daily_task"]
    planning_service = container.review_card_use_case.orchestrator.agent_registry.get(
        "learning_plan_service"
    ).service
    stored_after_long = planning_service.get_current(learner_id)
    assert stored_after_long.long_term_plan is not None
    assert stored_after_long.short_term_plan is None
    assert stored_after_long.learning_task is None

    short_result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="根据长期规划制定本周短期计划",
            plan_scope="short_term",
            user_profile=profile,
            long_term_plan=plan_input(long_plan.long_term_plan),
        )
    )
    short_plan = short_result.learning_plan
    assert short_plan.generated_scope == "short_term"
    assert short_plan.long_term_plan is None
    assert short_plan.short_term_plan is not None
    assert short_plan.learning_task is None
    assert short_plan.invalidated_layers == ["daily_task"]
    stored_after_short = planning_service.get_current(learner_id)
    assert stored_after_short.long_term_plan is not None
    assert stored_after_short.short_term_plan is not None
    assert stored_after_short.learning_task is None

    daily_result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="根据本周计划安排今天的任务",
            plan_scope="daily_task",
            user_profile=profile,
            long_term_plan=plan_input(long_plan.long_term_plan),
            short_term_plan=plan_input(short_plan.short_term_plan),
        )
    )
    daily_plan = daily_result.learning_plan
    assert daily_plan.generated_scope == "daily_task"
    assert daily_plan.long_term_plan is None
    assert daily_plan.short_term_plan is None
    assert daily_plan.learning_task is not None
    assert daily_plan.invalidated_layers == []


@pytest.mark.asyncio
async def test_follow_up_today_request_is_model_routed_to_daily_task(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    learner_id = "FOLLOW_UP_DAILY_TASK"
    profile = {"goals": {"type": "credential", "name": "中医执业医师"}}

    long_result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="请制定中医执业医师考试长期规划",
            plan_scope="long_term",
            user_profile=profile,
        )
    )
    short_result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="请结合我的学习状态，为四君子汤制定本周学习计划。",
            user_profile=profile,
            long_term_plan=plan_input(long_result.learning_plan.long_term_plan),
        )
    )
    assert short_result.learning_plan.generated_scope == "short_term"

    daily_result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="再给我今天的任务",
            user_profile=profile,
            messages=[
                {
                    "role": "user",
                    "content": "请结合我的学习状态，为四君子汤制定本周学习计划。",
                },
                {"role": "assistant", "content": "短期计划已经整理好。"},
                {"role": "user", "content": "再给我今天的任务"},
            ],
            long_term_plan=plan_input(long_result.learning_plan.long_term_plan),
            short_term_plan=plan_input(short_result.learning_plan.short_term_plan),
        )
    )

    planner_output = next(
        item for item in daily_result.agent_outputs if item.producer == "planner_agent"
    )
    assert planner_output.payload.plan_scope == "daily_task"
    assert daily_result.learning_plan.generated_scope == "daily_task"
    assert daily_result.learning_plan.learning_task is not None


@pytest.mark.asyncio
async def test_goal_correction_keeps_the_active_long_term_planning_scope(
    tmp_path: Path,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    user_request = "不对，我要考执业医师资格证"

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="FOLLOW_UP_LONG_TERM_GOAL",
            conversation_id="FOLLOW_UP_LONG_TERM_GOAL_CONVERSATION",
            user_request=user_request,
            messages=[
                {
                    "role": "user",
                    "content": "请结合我的学习状态，为我制定一份学习计划。",
                },
                {"role": "assistant", "content": "请确认需要哪一层计划。"},
                {"role": "user", "content": "长期计划吧"},
                {
                    "role": "user",
                    "content": "目前是零基础，我是计算机专业的",
                },
                {"role": "user", "content": "每周大概4天，一天4小时"},
                {"role": "user", "content": user_request},
            ],
            user_profile={
                "goals": {"type": "credential", "name": "中医执业医师"},
                "background": "零基础，计算机专业",
                "time_constraints": "每周4天，每天4小时",
            },
        )
    )

    planner_output = next(
        item for item in result.agent_outputs if item.producer == "planner_agent"
    )
    assert planner_output.payload.task_type == "learning_plan"
    assert planner_output.payload.plan_scope == "long_term"
    assert "knowledge_base_agent" not in planner_output.payload.selected_agents
    assert result.learning_plan.generated_scope == "long_term"


@pytest.mark.asyncio
async def test_short_plan_inherits_physician_route_despite_stale_profile_goal(
    tmp_path: Path,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    learner_id = "SHORT_INHERITS_LONG_ROUTE"
    long_result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="请制定中医执业医师考试长期规划",
            plan_scope="long_term",
            user_profile={
                "goals": {"type": "credential", "name": "中医执业医师"}
            },
        )
    )
    long_plan = long_result.learning_plan.long_term_plan

    short_result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request=(
                "请结合我的真实掌握状态和长期规划，给我一份短期规划。\n"
                + long_plan.content
                + "\n补充：我想考执业医师资格证，可以说是零基础。"
            ),
            plan_scope="short_term",
            user_profile={
                "goals": {"long_term_goal": "建立方剂学知识体系"}
            },
            long_term_plan=plan_input(long_plan),
        )
    )

    plan = short_result.learning_plan
    assert not getattr(plan, "requires_clarification", False)
    assert plan.generated_scope == "short_term"
    assert plan.short_term_plan is not None
    assert plan.short_term_plan.planning_route.route_id == "tcm_physician_standard_degree"
    assert (
        plan.short_term_plan.planning_route.textbook_route.route.route_id
        == "textbook_tcm_physician"
    )


@pytest.mark.asyncio
async def test_short_plan_imports_a_complete_inline_long_term_parent(
    tmp_path: Path,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    learner_id = "SHORT_IMPORTS_INLINE_LONG"
    inline_long = (
        "【最终目标】达到中医执业医师考试要求。"
        "【能力路径与阶段】依次完成五个教材阶段。"
        "【阶段里程碑】逐阶段提交路线规定的验收证据。"
        "【资源预算】按实际可用时间安排。"
        "【重规划条件】目标或时间显著变化时调整。"
        "【保温底线】中断时保留一次基础回顾。"
    )

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request=(
                "请结合我的真实掌握状态和长期规划，给我一份短期规划。\n"
                + inline_long
                + "\n补充：我想考执业医师资格证，可以说是零基础。"
            ),
            plan_scope="short_term",
            user_profile={
                "goals": {"long_term_goal": "建立方剂学知识体系"}
            },
            long_term_plan={"content": inline_long, "status": "active"},
        )
    )

    plan = result.learning_plan
    assert not getattr(plan, "requires_clarification", False)
    assert plan.generated_scope == "short_term"
    assert plan.short_term_plan.planning_route.route_id == "tcm_physician_standard_degree"
    service = container.review_card_use_case.orchestrator.agent_registry.get(
        "learning_plan_service"
    ).service
    stored = service.get_current(learner_id)
    assert stored.long_term_plan.content == inline_long
    assert stored.long_term_plan.plan_id
    assert stored.long_term_plan.version == 1
    assert stored.short_term_plan.long_term_plan_id == stored.long_term_plan.plan_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "request_text", "expected_question"),
    [
        ("short_term", "制定本周计划", "长期规划"),
        ("daily_task", "安排今天的任务", "短期计划"),
    ],
)
async def test_lower_layer_requires_an_existing_parent_plan(
    tmp_path: Path,
    scope: str,
    request_text: str,
    expected_question: str,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=f"MISSING_PARENT_{scope}",
            user_request=request_text,
            plan_scope=scope,
        )
    )

    clarification = result.learning_plan
    assert clarification.requires_clarification is True
    assert clarification.requested_scope == scope
    assert any(expected_question in question for question in clarification.clarification_questions)


@pytest.mark.asyncio
async def test_unspecified_scope_asks_which_layer_to_plan(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="UNSPECIFIED_LAYER",
            user_request="请制定学习计划",
            plan_scope="unspecified",
        )
    )

    clarification = result.learning_plan
    assert clarification.requires_clarification is True
    assert clarification.requested_scope == "unspecified"
    assert any("长期规划、短期计划或当日任务" in item for item in clarification.clarification_questions)


@pytest.mark.asyncio
async def test_ambiguous_formula_goal_asks_for_route_before_long_term_plan(
    tmp_path: Path,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="AMBIGUOUS_FORMULA_ROUTE",
            user_request="请结合我的真实掌握状态，给我一个长期学习规划。",
            plan_scope="long_term",
            user_profile={
                "goals": {"long_term_goal": "建立方剂学知识体系"}
            },
        )
    )

    clarification = result.learning_plan
    assert clarification.requires_clarification is True
    assert clarification.requested_scope == "long_term"
    assert any(
        "课程" in question and "考试" in question
        for question in clarification.clarification_questions
    )


@pytest.mark.asyncio
async def test_short_plan_does_not_retain_the_invalidated_daily_task(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    learner_id = "NO_STALE_DAILY_TASK"
    long_result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="制定中医执业医师考试长期规划",
            plan_scope="long_term",
            user_profile={"goals": {"type": "credential", "name": "中医执业医师"}},
        )
    )

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="根据长期规划制定本周短期计划",
            plan_scope="short_term",
            long_term_plan=plan_input(long_result.learning_plan.long_term_plan),
            learning_task={"task_content": "已经失效的旧当日任务"},
        )
    )

    task_blocks = result.learning_plan.short_term_plan.short_term_learning_package.task_blocks
    assert all("已经失效的旧当日任务" not in str(block) for block in task_blocks)
    assert all("当日任务需另行安排" not in str(block) for block in task_blocks)
    assert all(len(str(block).strip()) >= 20 for block in task_blocks)
    assert all(
        str(block).strip() in result.learning_plan.short_term_plan.content
        for block in task_blocks
    )


@pytest.mark.asyncio
async def test_stale_short_plan_cannot_create_a_daily_task_after_long_plan_changes(
    tmp_path: Path,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    learner_id = "STALE_PARENT_PLAN"
    profile = {"goals": {"type": "credential", "name": "中医执业医师"}}
    first_long = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="制定中医执业医师考试长期规划",
            plan_scope="long_term",
            user_profile=profile,
        )
    )
    short = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="根据长期规划制定本周短期计划",
            plan_scope="short_term",
            user_profile=profile,
            long_term_plan=plan_input(first_long.learning_plan.long_term_plan),
        )
    )
    await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="重新制定长期规划",
            plan_scope="long_term",
            user_profile=profile,
        )
    )

    stale_attempt = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id=learner_id,
            user_request="根据短期计划安排今天的任务",
            plan_scope="daily_task",
            user_profile=profile,
            short_term_plan=plan_input(short.learning_plan.short_term_plan),
        )
    )

    assert stale_attempt.learning_plan.requires_clarification is True
    assert stale_attempt.learning_plan.requested_scope == "daily_task"
    assert "短期计划" in stale_attempt.learning_plan.reason


@pytest.mark.asyncio
async def test_inactive_parent_plan_is_not_accepted_for_a_lower_layer(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    long_result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="INACTIVE_PARENT",
            user_request="制定中医执业医师考试长期规划",
            plan_scope="long_term",
            user_profile={"goals": {"type": "credential", "name": "中医执业医师"}},
        )
    )
    inactive_long = plan_input(long_result.learning_plan.long_term_plan)
    inactive_long["status"] = "superseded"

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="INACTIVE_PARENT",
            user_request="根据长期规划制定本周短期计划",
            plan_scope="short_term",
            long_term_plan=inactive_long,
        )
    )

    assert result.learning_plan.requires_clarification is True
    assert result.learning_plan.requested_scope == "short_term"


@pytest.mark.asyncio
async def test_parent_plan_from_another_learner_is_rejected(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    foreign = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="LEARNER_A",
            user_request="制定中医执业医师考试长期规划",
            plan_scope="long_term",
            user_profile={"goals": {"type": "credential", "name": "中医执业医师"}},
        )
    )

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="LEARNER_B",
            user_request="根据长期规划制定本周短期计划",
            plan_scope="short_term",
            long_term_plan=plan_input(foreign.learning_plan.long_term_plan),
        )
    )

    assert result.learning_plan.requires_clarification is True
    assert result.learning_plan.requested_scope == "short_term"


@pytest.mark.asyncio
async def test_short_term_plan_loads_persisted_parent_when_request_omits_it(
    tmp_path: Path,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="PERSISTED_PARENT",
            user_request="制定中医执业医师考试长期规划",
            plan_scope="long_term",
            user_profile={"goals": {"type": "credential", "name": "中医执业医师"}},
        )
    )

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="PERSISTED_PARENT",
            user_request="根据已有长期规划制定本周短期计划",
            plan_scope="short_term",
        )
    )

    assert result.learning_plan.generated_scope == "short_term"
    assert result.learning_plan.short_term_plan.long_term_plan_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "expected", "forbidden"),
    [
        (
            "long_term",
            {"long_term_plan_content", "long_term_plan_stages"},
            {"short_term_plan_content", "daily_task_content"},
        ),
        (
            "short_term",
            {"short_term_plan_content", "expected_output", "completion_criteria"},
            {"long_term_plan_content", "daily_task_content"},
        ),
        (
            "daily_task",
            {"daily_task_content", "estimated_minutes", "expected_output", "completion_criteria"},
            {"long_term_plan_content", "short_term_plan_content"},
        ),
    ],
)
async def test_diagnosis_sends_only_the_target_layer_schema_to_the_model(
    scope: str,
    expected: set[str],
    forbidden: set[str],
) -> None:
    model = CapturingStubChatModel()
    context = {
        "case_id": "CASE_SCOPE_SCHEMA",
        "trace_id": "TRACE_SCOPE_SCHEMA",
        "request_id": "REQ_SCOPE_SCHEMA",
        "execution_id": "EXE_SCOPE_SCHEMA",
        "step_id": "diagnosis",
        "task_type": "learning_plan",
        "plan_scope": scope,
        "learner_id": "SCOPED_SCHEMA",
        "user_request": "制定对应层级的学习安排",
        "available_minutes": 30,
        "current_long_term_plan": {"content": "已有长期规划", "status": "active"},
        "current_short_term_plan": {"content": "已有短期计划", "status": "active"},
        "current_learning_task": {"task_content": "已有当日任务"},
        "dependency_outputs": {},
    }

    await DiagnosisAgent(model).run(context)

    properties = set(model.last_payload["payload"]["output_schema"]["properties"])
    assert expected <= properties
    assert properties.isdisjoint(forbidden)

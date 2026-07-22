import pytest

from competition_app.agents.planner import PlannerAgent, PlannerDecision
from competition_app.llm.schemas import PlannerModelOutput


def test_dynamic_plan_contains_only_planner_selected_agents() -> None:
    decision = PlannerDecision(
        task_type="learning_plan",
        selected_agents=[
            "default_route_resolver",
            "knowledge_base_agent",
            "diagnosis_agent",
            "learning_plan_service",
        ],
        routing_reason="仅制定学习计划",
    )

    plan = PlannerAgent.build_plan(decision)

    route = next(step for step in plan.steps if step.step_id == "route_resolution")
    diagnosis = next(step for step in plan.steps if step.step_id == "diagnosis")
    assert [step.agent for step in plan.steps] == [
        "knowledge_base_agent",
        "default_route_resolver",
        "diagnosis_agent",
        "learning_plan_service",
    ]
    assert plan.steps.index(route) < plan.steps.index(diagnosis)
    assert route.agent == "default_route_resolver"
    assert set(diagnosis.depends_on) == {"knowledge", "route_resolution"}
    assert "expert_agent" not in {step.agent for step in plan.steps}
    assert "audit_agent" not in {step.agent for step in plan.steps}


def test_dynamic_router_rejects_expert_without_required_upstream_agents() -> None:
    output = PlannerModelOutput(
        task_type="personalized_review_card",
        selected_agents=["expert_agent", "audit_agent"],
        routing_reason="非法缺少依赖",
        risk_level="low",
        requires_audit=True,
    )

    with pytest.raises(ValueError, match="invalid agent dependencies"):
        PlannerAgent.validate_selection(output)


class CapturingPlannerModel:
    def __init__(self) -> None:
        self.payload = None

    async def complete_json(self, role, payload, on_delta=None):
        self.payload = payload
        return {
            "task_type": "learning_plan",
            "selected_agents": [
                "knowledge_base_agent",
                "diagnosis_agent",
                "learning_plan_service",
            ],
            "routing_reason": "短对话只制定计划，不需要压缩或生成资源。",
            "risk_level": "low",
            "requires_audit": False,
            "fallback_policy": "fail_closed",
        }


class LongTermPlanWithoutKnowledgeModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "task_type": "learning_plan",
            "selected_agents": ["diagnosis_agent", "learning_plan_service"],
            "routing_reason": "用户画像和学情足以生成长期规划，无需教材检索。",
            "risk_level": "low",
            "requires_audit": False,
        }


class ScopeIgnoringPlannerModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "task_type": "knowledge_explanation",
            "selected_agents": ["knowledge_base_agent", "expert_agent", "audit_agent"],
            "routing_reason": "错误地忽略了系统给出的规划层级。",
            "risk_level": "low",
            "requires_audit": True,
        }


class DailyTaskMislabelingPlannerModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "task_type": "learning_plan",
            "plan_scope": "short_term",
            "selected_agents": ["diagnosis_agent", "learning_plan_service"],
            "routing_reason": "用户询问今天学什么，属于制定短期学习计划。",
            "risk_level": "low",
            "requires_audit": False,
        }


class DailyTaskSemanticPlannerModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "task_type": "learning_plan",
            "plan_scope": "daily_task",
            "selected_agents": ["diagnosis_agent", "learning_plan_service"],
            "routing_reason": "用户承接上文要求生成今天的任务。",
            "risk_level": "low",
            "requires_audit": False,
        }


@pytest.mark.asyncio
async def test_plan_scope_deterministically_forces_learning_plan_route() -> None:
    result = await PlannerAgent(ScopeIgnoringPlannerModel()).run(
        {
            "case_id": "C_SCOPE",
            "trace_id": "T_SCOPE",
            "request_id": "R_SCOPE",
            "execution_id": "E_SCOPE",
            "step_id": "planner",
            "learner_id": "L_SCOPE",
            "user_request": "请制定长期规划",
            "plan_scope": "long_term",
            "messages": [],
            "conversation_requires_compression": False,
        }
    )

    assert result.payload.task_type == "learning_plan"
    assert result.payload.selected_agents == [
        "default_route_resolver",
        "diagnosis_agent",
        "learning_plan_service",
    ]


@pytest.mark.asyncio
async def test_daily_question_cannot_be_mislabeled_as_short_term_plan() -> None:
    result = await PlannerAgent(DailyTaskMislabelingPlannerModel()).run(
        {
            "case_id": "C_DAILY",
            "trace_id": "T_DAILY",
            "request_id": "R_DAILY",
            "execution_id": "E_DAILY",
            "step_id": "planner",
            "learner_id": "L_DAILY",
            "user_request": "我今天要学习些什么东西？",
            "plan_scope": "daily_task",
            "available_minutes": 60,
            "current_long_term_plan": {"content": "长期规划"},
            "current_short_term_plan": {"content": "本周学习四君子汤"},
            "messages": [],
            "conversation_requires_compression": False,
        }
    )

    assert result.payload.task_type == "learning_plan"
    assert result.payload.plan_scope == "daily_task"
    assert "当日任务" in result.payload.routing_reason
    assert "短期计划" not in result.payload.routing_reason


@pytest.mark.asyncio
async def test_planner_semantics_override_classifier_hint() -> None:
    result = await PlannerAgent(DailyTaskSemanticPlannerModel()).run(
        {
            "case_id": "C_HINT",
            "trace_id": "T_HINT",
            "request_id": "R_HINT",
            "execution_id": "E_HINT",
            "step_id": "planner",
            "learner_id": "L_HINT",
            "user_request": "再给我今天的任务",
            "plan_scope": None,
            "plan_scope_hint": "short_term",
            "messages": [
                {"role": "user", "content": "请制定本周学习计划"},
                {"role": "assistant", "content": "短期计划已生成"},
                {"role": "user", "content": "再给我今天的任务"},
            ],
            "conversation_requires_compression": False,
        }
    )

    assert result.payload.plan_scope == "daily_task"
    assert "当日任务" in result.payload.routing_reason


@pytest.mark.asyncio
async def test_planner_uses_hint_only_when_model_omits_learning_plan_scope() -> None:
    result = await PlannerAgent(LongTermPlanWithoutKnowledgeModel()).run(
        {
            "case_id": "C_FALLBACK",
            "trace_id": "T_FALLBACK",
            "request_id": "R_FALLBACK",
            "execution_id": "E_FALLBACK",
            "step_id": "planner",
            "learner_id": "L_FALLBACK",
            "user_request": "再给我今天的任务",
            "plan_scope": None,
            "plan_scope_hint": "daily_task",
            "messages": [],
            "conversation_requires_compression": False,
        }
    )

    assert result.payload.plan_scope == "daily_task"


@pytest.mark.asyncio
async def test_planner_uses_unspecified_instead_of_null_for_ambiguous_plan() -> None:
    result = await PlannerAgent(LongTermPlanWithoutKnowledgeModel()).run(
        {
            "case_id": "C_UNSPECIFIED",
            "trace_id": "T_UNSPECIFIED",
            "request_id": "R_UNSPECIFIED",
            "execution_id": "E_UNSPECIFIED",
            "step_id": "planner",
            "learner_id": "L_UNSPECIFIED",
            "user_request": "给我安排一下学习",
            "plan_scope": None,
            "plan_scope_hint": None,
            "messages": [],
            "conversation_requires_compression": False,
        }
    )

    assert result.payload.plan_scope == "unspecified"


@pytest.mark.asyncio
async def test_planner_receives_routing_skills_and_does_not_output_knowledge_query() -> None:
    model = CapturingPlannerModel()
    context = {
        "case_id": "C1", "trace_id": "T1", "request_id": "R1", "execution_id": "E1",
        "step_id": "planner", "learner_id": "L1", "user_request": "制定四君子汤计划",
        "messages": [{"message_id": "M1", "role": "user", "content": "制定计划"}],
        "conversation_requires_compression": False,
    }

    result = await PlannerAgent(model).run(context)

    assert not hasattr(result.payload, "knowledge_query")
    payload = model.payload["payload"]
    assert "prompt_skill" not in payload
    assert model.payload["prompt_skill_id"] == "planner.route_request"
    assert "任务目标" in model.payload["task_instructions"]
    assert {item["task_type"] for item in payload["routing_skills"]} == {
        "knowledge_explanation", "learning_plan", "personalized_review_card",
        "paper_generation"
    }
    assert payload["conversation_context"]["requires_compression"] is False
    assert payload["conversation_context"]["recent_turns"] == [
        {"role": "user", "content": "制定计划"}
    ]


@pytest.mark.asyncio
async def test_planner_preserves_no_knowledge_selection_for_long_term_plan() -> None:
    result = await PlannerAgent(LongTermPlanWithoutKnowledgeModel()).run(
        {
            "case_id": "C1",
            "trace_id": "T1",
            "request_id": "R1",
            "execution_id": "E1",
            "step_id": "planner",
            "learner_id": "L1",
            "user_request": "请给我一份长期学习规划。",
            "messages": [],
            "conversation_requires_compression": False,
        }
    )
    plan = PlannerAgent.build_plan(result.payload)

    assert result.payload.selected_agents == [
        "default_route_resolver",
        "diagnosis_agent",
        "learning_plan_service",
    ]
    assert [step.agent for step in plan.steps] == result.payload.selected_agents
    assert "knowledge_base_agent" not in result.payload.selected_agents


def test_planner_completes_learning_plan_service_without_forcing_knowledge() -> None:
    output = PlannerModelOutput(
        task_type="learning_plan",
        selected_agents=["diagnosis_agent"],
        routing_reason="用户询问最近学习状态。",
        risk_level="low",
        requires_audit=False,
    )

    completed = PlannerAgent.complete_required_selection(output)
    plan = PlannerAgent.build_plan(
        PlannerDecision(
            task_type=completed.task_type,
            selected_agents=completed.selected_agents,
            routing_reason=completed.routing_reason,
            risk_level=completed.risk_level,
            requires_audit=completed.requires_audit,
        )
    )

    assert completed.selected_agents == [
        "default_route_resolver", "diagnosis_agent", "learning_plan_service"
    ]
    assert [step.agent for step in plan.steps] == completed.selected_agents
    diagnosis = next(step for step in plan.steps if step.step_id == "diagnosis")
    assert diagnosis.depends_on == ["route_resolution"]


def test_personalized_review_card_places_route_resolution_before_diagnosis() -> None:
    output = PlannerModelOutput(
        task_type="personalized_review_card",
        selected_agents=[
            "knowledge_base_agent",
            "diagnosis_agent",
            "learning_plan_service",
            "review_scheduler",
            "expert_agent",
            "audit_agent",
        ],
        routing_reason="生成学习卡片。",
        risk_level="low",
        requires_audit=True,
    )

    completed = PlannerAgent.complete_required_selection(output)
    plan = PlannerAgent.build_plan(
        PlannerDecision(
            task_type=completed.task_type,
            selected_agents=completed.selected_agents,
            routing_reason=completed.routing_reason,
            risk_level=completed.risk_level,
            requires_audit=completed.requires_audit,
        )
    )

    route = next(step for step in plan.steps if step.step_id == "route_resolution")
    diagnosis = next(step for step in plan.steps if step.step_id == "diagnosis")
    assert route.agent == "default_route_resolver"
    assert plan.steps.index(route) < plan.steps.index(diagnosis)
    assert set(diagnosis.depends_on) == {"knowledge", "route_resolution"}


class IncompleteReviewCardModel:
    def __init__(self, selected_agents: list[str]) -> None:
        self.selected_agents = selected_agents

    async def complete_json(self, role, payload, on_delta=None):
        return {
            "task_type": "personalized_review_card",
            "selected_agents": self.selected_agents,
            "routing_reason": "生成学习卡片。",
            "risk_level": "low",
            "requires_audit": True,
            "fallback_policy": "fail_closed",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selected_agents", "requires_compression", "expected_memory"),
    [
        (["memory_agent"], True, True),
        (["knowledge_base_agent"], True, True),
        (["knowledge_base_agent"], False, False),
    ],
)
async def test_planner_completes_incomplete_review_card_delivery_chain_from_model_output(
    selected_agents: list[str], requires_compression: bool, expected_memory: bool
) -> None:
    result = await PlannerAgent(IncompleteReviewCardModel(selected_agents)).run(
        {
            "case_id": "C1",
            "trace_id": "T1",
            "request_id": "R1",
            "execution_id": "E1",
            "step_id": "planner",
            "learner_id": "L1",
            "user_request": "请生成一张四君子汤复习卡",
            "messages": [],
            "conversation_requires_compression": requires_compression,
        }
    )

    assert result.payload.selected_agents == [
        *(["memory_agent"] if expected_memory else []),
        "knowledge_base_agent",
        "default_route_resolver",
        "diagnosis_agent",
        "review_scheduler",
        "expert_agent",
        "audit_agent",
    ]
    PlannerAgent.validate_selection(result.payload)


def test_paper_generation_uses_minimal_evidence_expert_audit_chain() -> None:
    output = PlannerModelOutput(
        task_type="paper_generation",
        selected_agents=["expert_agent", "audit_agent"],
        routing_reason="用户要求生成试卷蓝图。",
        risk_level="medium",
        requires_audit=True,
    )

    completed = PlannerAgent.complete_required_selection(output)
    plan = PlannerAgent.build_plan(
        PlannerDecision(
            task_type=completed.task_type,
            selected_agents=completed.selected_agents,
            routing_reason=completed.routing_reason,
            risk_level=completed.risk_level,
            requires_audit=completed.requires_audit,
        )
    )

    assert completed.selected_agents == [
        "knowledge_base_agent", "expert_agent", "audit_agent"
    ]
    assert [(step.agent, step.depends_on) for step in plan.steps] == [
        ("paper_blueprint_agent", []),
        ("knowledge_base_agent", ["paper_blueprint"]),
        ("paper_assembly_agent", ["paper_blueprint", "question_pool"]),
        ("audit_agent", ["paper_blueprint", "question_pool", "paper_assembly"]),
    ]
    question_pool_step = next(step for step in plan.steps if step.step_id == "question_pool")
    assert question_pool_step.timeout_seconds == 300.0
    paper_assembly_step = next(step for step in plan.steps if step.step_id == "paper_assembly")
    assert paper_assembly_step.timeout_seconds == 180.0
    audit_step = next(step for step in plan.steps if step.step_id == "audit")
    assert audit_step.timeout_seconds == 120.0


def test_knowledge_explanation_does_not_include_planning_or_review_services() -> None:
    output = PlannerModelOutput(
        task_type="knowledge_explanation",
        selected_agents=["knowledge_base_agent", "expert_agent", "audit_agent"],
        routing_reason="用户要求讲解感冒。",
        risk_level="low",
        requires_audit=True,
    )

    completed = PlannerAgent.complete_required_selection(output)
    plan = PlannerAgent.build_plan(
        PlannerDecision(
            task_type=completed.task_type,
            selected_agents=completed.selected_agents,
            routing_reason=completed.routing_reason,
            risk_level=completed.risk_level,
            requires_audit=completed.requires_audit,
        )
    )

    assert [step.agent for step in plan.steps] == [
        "knowledge_base_agent", "knowledge_explanation_agent", "audit_agent"
    ]


def test_personalized_review_card_uses_mastery_flow_without_plan_service() -> None:
    decision = PlannerDecision(
        task_type="personalized_review_card",
        selected_agents=[
            "knowledge_base_agent",
            "default_route_resolver",
            "diagnosis_agent",
            "review_scheduler",
            "expert_agent",
            "audit_agent",
        ],
        routing_reason="复习资源链路",
    )

    plan = PlannerAgent.build_plan(decision)

    assert [step.agent for step in plan.steps] == [
        "knowledge_base_agent",
        "default_route_resolver",
        "diagnosis_agent",
        "review_scheduler",
        "expert_agent",
        "audit_agent",
    ]
    assert "learning_plan_service" not in [step.agent for step in plan.steps]
    assert "diagnosis_agent" in {step.agent for step in plan.steps}
    assert "review_scheduler" in {step.agent for step in plan.steps}
    assert "default_route_resolver" in {step.agent for step in plan.steps}

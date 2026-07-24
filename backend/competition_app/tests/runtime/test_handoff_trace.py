import pytest

from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.execution import ExecutionPlan, ExecutionStep
from competition_app.contracts.learning_plan import LearningPlanClarificationResult
from competition_app.agents.common import envelope
from competition_app.runtime.agent_registry import AgentRegistry
from competition_app.runtime.langgraph_orchestrator import LangGraphOrchestrator
from competition_app.runtime.orchestrator import Orchestrator


def root_context(**overrides: object) -> dict[str, object]:
    context: dict[str, object] = {
        "case_id": "CASE_HANDOFF",
        "trace_id": "TRACE_HANDOFF",
        "request_id": "REQ_HANDOFF",
        "execution_id": "EXE_HANDOFF",
        "learner_id": "LEARNER_HANDOFF",
        "task_type": "learning_plan",
        "user_request": "制定短期学习计划",
    }
    context.update(overrides)
    return context


def dependency_output(step_id: str, payload: dict[str, object]) -> AgentEnvelope[dict[str, object]]:
    return AgentEnvelope(
        artifact_id=f"ART_{step_id}",
        artifact_type="handoff_dependency",
        case_id="CASE_HANDOFF",
        trace_id="TRACE_HANDOFF",
        request_id="REQ_HANDOFF",
        execution_id="EXE_HANDOFF",
        step_id=step_id,
        producer=f"{step_id}_agent",
        task_type="learning_plan",
        learner_id="LEARNER_HANDOFF",
        payload=payload,
    )


class ReturningAgent:
    def __init__(self, output: AgentEnvelope[dict[str, object]]) -> None:
        self.output = output

    async def run(self, context):
        return self.output


class CapturingAgent:
    def __init__(self) -> None:
        self.context: dict[str, object] | None = None

    async def run(self, context):
        self.context = context
        return {"diagnosis": "ready"}


class CountingAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, context):
        self.calls += 1
        return {"unexpected": "agent should have been blocked"}


class ClarifyingAgent:
    async def run(self, context):
        return envelope(
            context,
            "expert_agent",
            "learning_plan_clarification",
            LearningPlanClarificationResult(
                clarification_questions=["请补充证据来源"],
                reason="证据不足，需要用户确认。",
                requested_scope="short_term",
            ),
        )


def registry_with_diagnosis(agent: CapturingAgent) -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(
        "memory_agent",
        ReturningAgent(
            dependency_output(
                "memory",
                {
                    "learning_goal": "完成本周测验",
                    "time_budget": 25,
                    "multi_scale_learning_state": {"macro": {}, "meso": {}, "micro": {}},
                },
            )
        ),
    )
    registry.register(
        "route_agent",
        ReturningAgent(dependency_output("route_resolution", {"route": "short_term"})),
    )
    registry.register("diagnosis_agent", agent)
    return registry


def plan_for_diagnosis() -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="PLAN_HANDOFF_DIAGNOSIS",
        task_type="learning_plan",
        steps=[
            ExecutionStep(step_id="memory", agent="memory_agent"),
            ExecutionStep(step_id="route_resolution", agent="route_agent"),
            ExecutionStep(
                step_id="diagnosis",
                agent="diagnosis_agent",
                depends_on=["memory", "route_resolution"],
            ),
        ],
    )


@pytest.mark.asyncio
async def test_orchestrator_passes_handoff_and_direct_dependencies() -> None:
    agent = CapturingAgent()
    result = await Orchestrator(registry_with_diagnosis(agent)).execute(
        plan_for_diagnosis(), root_context()
    )

    assert result.status == "success"
    assert agent.context is not None
    assert agent.context["agent_handoff"]["target_agent"] == "diagnosis_agent"
    assert set(agent.context["dependency_outputs"]) == {"memory", "route_resolution"}
    assert result.communication_trace[-1].target_agent == "diagnosis_agent"
    assert result.communication_trace[-1].status == "consumed"
    assert result.communication_trace[-1].schema_version == "1.0"


@pytest.mark.asyncio
async def test_orchestrator_does_not_call_agent_on_blocking_gap() -> None:
    registry = AgentRegistry()
    agent = CountingAgent()
    registry.register("expert_agent", agent)
    plan = ExecutionPlan(
        plan_id="PLAN_HANDOFF_BLOCKED",
        task_type="knowledge_explanation",
        steps=[ExecutionStep(step_id="expert", agent="expert_agent")],
    )

    result = await Orchestrator(registry).execute(
        plan, root_context(task_type="knowledge_explanation")
    )

    assert result.status == "failed"
    assert result.error_type == "AgentHandoffBlocked"
    assert agent.calls == 0
    assert result.communication_trace[-1].status == "blocked"


@pytest.mark.asyncio
async def test_interruptible_success_with_blocking_gap_is_not_published() -> None:
    registry = AgentRegistry()
    agent = CountingAgent()
    registry.register("expert_agent", agent)
    plan = ExecutionPlan(
        plan_id="PLAN_INTERRUPTIBLE_BLOCKED",
        task_type="knowledge_explanation",
        steps=[ExecutionStep(step_id="expert", agent="expert_agent")],
    )

    result = await Orchestrator(registry).execute(
        plan,
        root_context(task_type="knowledge_explanation", interruptible=True),
    )

    assert result.status == "failed"
    assert result.error_type == "AgentHandoffBlocked"
    assert result.outputs == {}
    assert agent.calls == 1
    assert result.communication_trace[-1].status == "blocked"


@pytest.mark.asyncio
async def test_langgraph_interruptible_clarification_is_allowed_despite_blocking_gap() -> None:
    registry = AgentRegistry()
    registry.register("expert_agent", ClarifyingAgent())
    plan = ExecutionPlan(
        plan_id="PLAN_INTERRUPTIBLE_CLARIFICATION",
        task_type="knowledge_explanation",
        steps=[ExecutionStep(step_id="expert", agent="expert_agent")],
    )

    result = await LangGraphOrchestrator(registry).execute(
        plan,
        root_context(task_type="knowledge_explanation", interruptible=True),
        thread_id="THREAD_HANDOFF_CLARIFICATION",
    )

    assert result.status == "interrupted"
    assert result.communication_trace[-1].status == "consumed"


@pytest.mark.asyncio
async def test_foreign_dependency_is_excluded_from_agent_context_and_handoff() -> None:
    foreign_output = dependency_output("upstream", {"artifact": "foreign-data"}).model_copy(
        update={"learner_id": "LEARNER_OTHER"}
    )
    registry = AgentRegistry()
    registry.register("upstream_agent", ReturningAgent(foreign_output))
    agent = CapturingAgent()
    registry.register("target_agent", agent)
    plan = ExecutionPlan(
        plan_id="PLAN_FOREIGN_DEPENDENCY",
        task_type="learning_plan",
        steps=[
            ExecutionStep(step_id="upstream", agent="upstream_agent"),
            ExecutionStep(step_id="target", agent="target_agent", depends_on=["upstream"]),
        ],
    )

    result = await Orchestrator(registry).execute(plan, root_context())

    assert result.status == "success"
    assert agent.context is not None
    assert agent.context["dependency_outputs"] == {}
    assert "foreign-data" not in str(agent.context["agent_handoff"])
    assert "upstream:cross_user_output" in agent.context["cognitive_gap"]["omitted_categories"]


@pytest.mark.asyncio
async def test_safe_plain_dependency_remains_visible_in_compatibility_mode() -> None:
    registry = AgentRegistry()
    registry.register("upstream_agent", ReturningAgent({"artifact": "safe-direct-output"}))
    agent = CapturingAgent()
    registry.register("target_agent", agent)
    plan = ExecutionPlan(
        plan_id="PLAN_PLAIN_DEPENDENCY",
        task_type="learning_plan",
        steps=[
            ExecutionStep(step_id="upstream", agent="upstream_agent"),
            ExecutionStep(step_id="target", agent="target_agent", depends_on=["upstream"]),
        ],
    )

    result = await Orchestrator(registry).execute(plan, root_context())

    assert result.status == "success"
    assert agent.context is not None
    assert agent.context["dependency_outputs"] == {"upstream": {"artifact": "safe-direct-output"}}
    assert any(
        fact["category"] == "artifact"
        for fact in agent.context["agent_handoff"]["confirmed_facts"]
    )


@pytest.mark.asyncio
async def test_unsafe_plain_dependency_field_is_omitted_and_not_exposed() -> None:
    registry = AgentRegistry()
    registry.register(
        "upstream_agent",
        ReturningAgent({"artifact": "safe-direct-output", "api_token": "must-not-leak"}),
    )
    agent = CapturingAgent()
    registry.register("target_agent", agent)
    plan = ExecutionPlan(
        plan_id="PLAN_UNSAFE_PLAIN_DEPENDENCY",
        task_type="learning_plan",
        steps=[
            ExecutionStep(step_id="upstream", agent="upstream_agent"),
            ExecutionStep(step_id="target", agent="target_agent", depends_on=["upstream"]),
        ],
    )

    result = await Orchestrator(registry).execute(plan, root_context())

    assert result.status == "success"
    assert agent.context is not None
    assert agent.context["dependency_outputs"] == {"upstream": {"artifact": "safe-direct-output"}}
    assert "must-not-leak" not in str(agent.context["agent_handoff"])
    assert "upstream:api_token:unsafe_field" in agent.context["cognitive_gap"]["omitted_categories"]


@pytest.mark.asyncio
async def test_langgraph_uses_same_handoff_contract_as_legacy() -> None:
    agent = CapturingAgent()

    result = await LangGraphOrchestrator(registry_with_diagnosis(agent)).execute(
        plan_for_diagnosis(), root_context()
    )

    assert result.status == "success"
    assert result.communication_trace
    assert agent.context is not None
    assert agent.context["agent_handoff"]["schema_version"] == "1.0"

import asyncio

import pytest

from competition_app.contracts.resource import AuditResult

from competition_app.contracts.execution import ExecutionPlan, ExecutionStep
from competition_app.runtime.agent_registry import AgentRegistry
from competition_app.runtime.orchestrator import Orchestrator
from competition_app.runtime.tool_registry import ToolRegistry
from competition_app.runtime.trace import TraceRecorder


class AuditSequenceAgent:
    def __init__(self, decisions: list[str]) -> None:
        self.decisions = decisions
        self.calls = 0

    async def run(self, context):
        decision = self.decisions[min(self.calls, len(self.decisions) - 1)]
        self.calls += 1
        return type("Output", (), {"payload": AuditResult(
            audit_result_id=f"AUDIT_{self.calls}", decision=decision
        )})()


class CountingAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, context):
        self.calls += 1
        return {"draft": self.calls}


class FeedbackCapturingAgent(CountingAgent):
    def __init__(self) -> None:
        super().__init__()
        self.audit_feedback = None

    async def run(self, context):
        self.audit_feedback = context.get("audit_feedback")
        return await super().run(context)


class RevisionFailingAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, context):
        self.calls += 1
        if self.calls > 1:
            raise ValueError("revised paper violates schema")
        return {"draft": 1}


class FailingAgent:
    async def run(self, context):
        raise LookupError("knowledge point could not be resolved")


@pytest.mark.asyncio
async def test_orchestrator_preserves_failure_reason() -> None:
    registry = AgentRegistry()
    registry.register("knowledge_base_agent", FailingAgent())
    plan = ExecutionPlan(
        plan_id="P_FAIL", task_type="personalized_review_card",
        steps=[ExecutionStep(step_id="knowledge", agent="knowledge_base_agent")],
    )
    result = await Orchestrator(registry).execute(plan, {})
    assert result.status == "failed"
    assert result.error_type == "LookupError"
    assert "knowledge point could not be resolved" in result.error_message


@pytest.mark.asyncio
async def test_orchestrator_revises_expert_once_then_passes() -> None:
    registry = AgentRegistry()
    expert = CountingAgent()
    audit = AuditSequenceAgent(["revise", "pass"])
    registry.register("expert_agent", expert)
    registry.register("audit_agent", audit)
    plan = ExecutionPlan(
        plan_id="P1", task_type="personalized_review_card", steps=[
            ExecutionStep(step_id="expert", agent="expert_agent"),
            ExecutionStep(step_id="audit", agent="audit_agent", depends_on=["expert"]),
        ]
    )

    result = await Orchestrator(registry).execute(plan, {})

    assert result.status == "success"
    assert expert.calls == 2
    assert audit.calls == 2


@pytest.mark.asyncio
async def test_orchestrator_emits_revision_events() -> None:
    from competition_app.runtime.event_stream import bind_event_sink, reset_event_sink

    events = []
    token = bind_event_sink(events.append)
    try:
        registry = AgentRegistry()
        registry.register("expert_agent", CountingAgent())
        registry.register("audit_agent", AuditSequenceAgent(["revise", "pass"]))
        plan = ExecutionPlan(
            plan_id="P_EVENTS", task_type="personalized_review_card", steps=[
                ExecutionStep(step_id="expert", agent="expert_agent"),
                ExecutionStep(step_id="audit", agent="audit_agent", depends_on=["expert"]),
            ]
        )
        result = await Orchestrator(registry).execute(plan, {})
    finally:
        reset_event_sink(token)

    assert result.status == "success"
    assert [event["event"] for event in events].count("audit_revision_started") == 1
    assert [event["event"] for event in events].count("audit_revision_completed") == 1


@pytest.mark.asyncio
async def test_orchestrator_waits_for_human_when_revision_is_still_not_approved() -> None:
    registry = AgentRegistry()
    expert = CountingAgent()
    audit = AuditSequenceAgent(["revise", "revise"])
    registry.register("expert_agent", expert)
    registry.register("audit_agent", audit)
    plan = ExecutionPlan(
        plan_id="P_STILL_REVISE", task_type="personalized_review_card", steps=[
            ExecutionStep(step_id="expert", agent="expert_agent"),
            ExecutionStep(step_id="audit", agent="audit_agent", depends_on=["expert"]),
        ]
    )

    result = await Orchestrator(registry).execute(plan, {})

    assert result.status == "waiting_human_review"
    assert expert.calls == 2
    assert audit.calls == 2


@pytest.mark.asyncio
async def test_orchestrator_passes_audit_feedback_to_revision_agent() -> None:
    registry = AgentRegistry()
    expert = FeedbackCapturingAgent()
    audit = AuditSequenceAgent(["revise", "pass"])
    registry.register("expert_agent", expert)
    registry.register("audit_agent", audit)
    plan = ExecutionPlan(
        plan_id="P_FEEDBACK", task_type="personalized_review_card", steps=[
            ExecutionStep(step_id="expert", agent="expert_agent"),
            ExecutionStep(step_id="audit", agent="audit_agent", depends_on=["expert"]),
        ]
    )

    result = await Orchestrator(registry).execute(plan, {})

    assert result.status == "success"
    assert expert.audit_feedback is not None


@pytest.mark.asyncio
async def test_orchestrator_fails_when_revision_is_rejected() -> None:
    registry = AgentRegistry()
    registry.register("expert_agent", CountingAgent())
    registry.register("audit_agent", AuditSequenceAgent(["revise", "reject"]))
    plan = ExecutionPlan(
        plan_id="P_REJECT_AFTER_REVISION", task_type="personalized_review_card", steps=[
            ExecutionStep(step_id="expert", agent="expert_agent"),
            ExecutionStep(step_id="audit", agent="audit_agent", depends_on=["expert"]),
        ]
    )

    result = await Orchestrator(registry).execute(plan, {})

    assert result.status == "failed"


@pytest.mark.asyncio
async def test_orchestrator_preserves_revision_failure_reason() -> None:
    registry = AgentRegistry()
    expert = RevisionFailingAgent()
    audit = AuditSequenceAgent(["revise"])
    registry.register("expert_agent", expert)
    registry.register("audit_agent", audit)
    plan = ExecutionPlan(
        plan_id="P_REVISION_FAIL",
        task_type="personalized_review_card",
        steps=[
            ExecutionStep(step_id="expert", agent="expert_agent"),
            ExecutionStep(step_id="audit", agent="audit_agent", depends_on=["expert"]),
        ],
    )

    result = await Orchestrator(registry).execute(plan, {})

    assert result.status == "failed"
    assert result.error_type == "ValueError"
    assert "revised paper violates schema" in result.error_message


@pytest.mark.asyncio
async def test_knowledge_explanation_revision_reuses_explanation_agent() -> None:
    registry = AgentRegistry()
    expert = CountingAgent()
    audit = AuditSequenceAgent(["revise", "pass"])
    registry.register("knowledge_explanation_agent", expert)
    registry.register("audit_agent", audit)
    plan = ExecutionPlan(
        plan_id="P_EXPLAIN_REVISION",
        task_type="knowledge_explanation",
        steps=[
            ExecutionStep(step_id="expert", agent="knowledge_explanation_agent"),
            ExecutionStep(step_id="audit", agent="audit_agent", depends_on=["expert"]),
        ],
    )

    result = await Orchestrator(registry).execute(
        plan, {"task_type": "knowledge_explanation"}
    )

    assert result.status == "success"
    assert expert.calls == 2
    assert audit.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "expected_status"),
    [("reject", "failed"), ("needs_human_review", "waiting_human_review")],
)
async def test_orchestrator_maps_terminal_audit_decisions(decision, expected_status) -> None:
    registry = AgentRegistry()
    registry.register("expert_agent", CountingAgent())
    registry.register("audit_agent", AuditSequenceAgent([decision]))
    plan = ExecutionPlan(
        plan_id="P1", task_type="personalized_review_card", steps=[
            ExecutionStep(step_id="expert", agent="expert_agent"),
            ExecutionStep(step_id="audit", agent="audit_agent", depends_on=["expert"]),
        ]
    )

    result = await Orchestrator(registry).execute(plan, {})

    assert result.status == expected_status


class RecordingAgent:
    def __init__(self, name: str, events: list[str], fail_once: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail_once = fail_once
        self.calls = 0

    async def run(self, context: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        self.events.append(f"start:{self.name}")
        await asyncio.sleep(0.01)
        if self.fail_once and self.calls == 1:
            self.events.append(f"fail:{self.name}")
            raise RuntimeError("temporary")
        self.events.append(f"end:{self.name}")
        return {"agent": self.name, "inputs": sorted(context.get("dependency_outputs", {}))}


@pytest.mark.asyncio
async def test_orchestrator_runs_levels_in_dependency_order_and_retries_once() -> None:
    events: list[str] = []
    registry = AgentRegistry()
    memory = RecordingAgent("memory", events)
    knowledge = RecordingAgent("knowledge", events, fail_once=True)
    diagnosis = RecordingAgent("diagnosis", events)
    registry.register("memory_agent", memory)
    registry.register("knowledge_agent", knowledge)
    registry.register("diagnosis_agent", diagnosis)
    plan = ExecutionPlan(
        plan_id="P1",
        task_type="personalized_review_card",
        steps=[
            ExecutionStep(step_id="memory", agent="memory_agent"),
            ExecutionStep(step_id="knowledge", agent="knowledge_agent"),
            ExecutionStep(step_id="diagnosis", agent="diagnosis_agent", depends_on=["memory", "knowledge"]),
        ],
    )

    result = await Orchestrator(registry).execute(plan, {"request": "review"})

    assert result.status == "success"
    assert knowledge.calls == 2
    assert events.index("start:diagnosis") > events.index("end:memory")
    assert events.index("start:diagnosis") > events.index("end:knowledge")
    assert result.outputs["diagnosis"]["inputs"] == ["knowledge", "memory"]
    assert [item.status for item in result.trace if item.step_id == "knowledge"] == ["retrying", "success"]


class ContextRecordingAgent:
    def __init__(self) -> None:
        self.received_context = None

    async def run(self, context):
        self.received_context = context
        return {"ok": True}


@pytest.mark.asyncio
async def test_orchestrator_injects_tool_runtime_into_agent_context() -> None:
    registry = AgentRegistry()
    agent = ContextRecordingAgent()
    tools = ToolRegistry()
    registry.register("knowledge_base_agent", agent)
    plan = ExecutionPlan(
        plan_id="P_TOOL_CONTEXT",
        task_type="personalized_review_card",
        steps=[ExecutionStep(step_id="knowledge", agent="knowledge_base_agent")],
    )

    result = await Orchestrator(registry, tools).execute(plan, {})

    assert result.status == "success"
    assert agent.received_context["tool_registry"] is tools
    assert isinstance(agent.received_context["trace_recorder"], TraceRecorder)


class ToolInvokingAgent:
    async def run(self, context):
        return await context["tool_registry"].invoke(
            "search_textbook_evidence",
            "knowledge_base_agent",
            trace_recorder=context["trace_recorder"],
            safe_input_summary={"query_length": 4},
            safe_output_summary_factory=lambda result: {"evidence_count": result["count"]},
            query="四君子汤",
        )


@pytest.mark.asyncio
async def test_orchestrator_exposes_tool_trace_in_execution_result() -> None:
    registry = AgentRegistry()
    tools = ToolRegistry()
    registry.register("knowledge_base_agent", ToolInvokingAgent())
    tools.register(
        "search_textbook_evidence",
        lambda query: {"count": 1},
        allowed_agents={"knowledge_base_agent"},
    )
    plan = ExecutionPlan(
        plan_id="P_TOOL_TRACE",
        task_type="personalized_review_card",
        steps=[ExecutionStep(step_id="knowledge", agent="knowledge_base_agent")],
    )

    result = await Orchestrator(registry, tools).execute(plan, {})

    assert result.status == "success"
    assert [(item.tool_name, item.agent, item.status) for item in result.tool_trace] == [
        ("search_textbook_evidence", "knowledge_base_agent", "success")
    ]

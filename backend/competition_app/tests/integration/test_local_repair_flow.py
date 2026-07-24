import pytest

from competition_app.contracts.execution import ExecutionPlan, ExecutionStep
from competition_app.contracts.resource import AuditResult
from competition_app.runtime.agent_registry import AgentRegistry
from competition_app.runtime.event_stream import bind_event_sink, reset_event_sink
from competition_app.runtime.orchestrator import Orchestrator


class RecordingAgent:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    async def run(self, context):
        self.calls.append(self.name)
        return {"producer": self.name}


class EmptyFindingsAuditAgent:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.count = 0

    async def run(self, context):
        self.calls.append("audit")
        self.count += 1
        return type(
            "AuditOutput",
            (),
            {
                "payload": AuditResult(
                    audit_result_id=f"EMPTY_{self.count}",
                    decision="revise",
                )
            },
        )()


class FailsDuringRepairAgent(RecordingAgent):
    async def run(self, context):
        self.calls.append(self.name)
        if context.get("audit_feedback") is not None:
            raise RuntimeError("repair action failed")
        return {"producer": self.name}


class RevisingAuditAgent:
    def __init__(self, decisions: list[str], calls: list[str]) -> None:
        self.decisions = decisions
        self.calls = calls
        self.count = 0

    async def run(self, context):
        self.calls.append("audit")
        decision = self.decisions[min(self.count, len(self.decisions) - 1)]
        self.count += 1
        return type(
            "AuditOutput",
            (),
            {
                "payload": AuditResult(
                    audit_result_id=f"AUDIT_{self.count}",
                    decision=decision,
                    findings=["证据缺失"] if decision == "revise" else [],
                )
            },
        )()


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="LOCAL_REPAIR",
        task_type="personalized_review_card",
        steps=[
            ExecutionStep(step_id="knowledge", agent="knowledge_agent"),
            ExecutionStep(
                step_id="expert", agent="expert_agent", depends_on=["knowledge"]
            ),
            ExecutionStep(
                step_id="audit", agent="audit_agent", depends_on=["expert"]
            ),
        ],
    )


def _registry(decisions: list[str], calls: list[str]) -> tuple[AgentRegistry, RevisingAuditAgent]:
    registry = AgentRegistry()
    registry.register("knowledge_agent", RecordingAgent("knowledge", calls))
    registry.register("expert_agent", RecordingAgent("expert", calls))
    audit = RevisingAuditAgent(decisions, calls)
    registry.register("audit_agent", audit)
    return registry, audit


@pytest.mark.asyncio
async def test_missing_evidence_reruns_only_knowledge_expert_and_audit() -> None:
    calls: list[str] = []
    registry, _ = _registry(["revise", "pass"], calls)

    result = await Orchestrator(registry).execute(_plan(), {})

    assert result.status == "success"
    assert calls == ["knowledge", "expert", "audit", "knowledge", "expert", "audit"]
    assert result.repair_trace[0].rerun_step_ids == ["knowledge", "expert", "audit"]
    assert result.repair_trace[0].status == "completed"


@pytest.mark.asyncio
async def test_second_failed_audit_stops_without_third_round() -> None:
    calls: list[str] = []
    registry, audit = _registry(["revise", "revise"], calls)

    result = await Orchestrator(registry).execute(_plan(), {})

    assert result.status == "waiting_human_review"
    assert len(result.repair_trace) == 1
    assert result.repair_trace[0].status == "stopped"
    assert audit.count == 2


@pytest.mark.asyncio
async def test_repair_events_are_safe_summaries_without_audit_findings() -> None:
    calls: list[str] = []
    registry, _ = _registry(["revise", "pass"], calls)
    events: list[dict[str, object]] = []
    token = bind_event_sink(events.append)
    try:
        result = await Orchestrator(registry).execute(_plan(), {})
    finally:
        reset_event_sink(token)

    assert result.status == "success"
    repair_events = [
        event
        for event in events
        if str(event["event"]).startswith("repair_")
    ]
    assert {event["event"] for event in repair_events} >= {
        "repair_planned",
        "repair_step_started",
        "repair_step_completed",
        "repair_reaudit_started",
        "repair_completed",
    }
    assert all("findings" not in event and "output" not in event for event in repair_events)


@pytest.mark.asyncio
async def test_empty_modern_audit_findings_fail_closed_without_legacy_revision() -> None:
    calls: list[str] = []
    registry = AgentRegistry()
    expert = RecordingAgent("expert", calls)
    audit = EmptyFindingsAuditAgent(calls)
    registry.register("knowledge_agent", RecordingAgent("knowledge", calls))
    registry.register("expert_agent", expert)
    registry.register("audit_agent", audit)

    result = await Orchestrator(registry).execute(_plan(), {})

    assert result.status == "waiting_human_review"
    assert calls == ["knowledge", "expert", "audit"]
    assert result.repair_trace[0].status == "stopped"
    assert result.repair_trace[0].final_audit_decision == "needs_human_review"


@pytest.mark.asyncio
async def test_legacy_repair_exception_stops_trace_and_emits_safe_event() -> None:
    calls: list[str] = []
    registry = AgentRegistry()
    registry.register("knowledge_agent", RecordingAgent("knowledge", calls))
    registry.register("expert_agent", FailsDuringRepairAgent("expert", calls))
    registry.register("audit_agent", RevisingAuditAgent(["revise"], calls))
    events: list[dict[str, object]] = []
    token = bind_event_sink(events.append)
    try:
        result = await Orchestrator(registry).execute(_plan(), {})
    finally:
        reset_event_sink(token)

    assert result.status == "failed"
    assert result.repair_trace[0].status == "stopped"
    stopped = [event for event in events if event["event"] == "repair_stopped"]
    assert len(stopped) == 1
    assert stopped[0]["status"] == "failed"
    assert "error_message" not in stopped[0]

import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel
from sqlalchemy import create_engine

from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.execution import ExecutionPlan, ExecutionStep
from competition_app.contracts.resource import AuditResult
from competition_app.contracts.local_repair import RepairIssue
from competition_app.contracts.learning_plan import LearningPlanClarificationResult
from competition_app.contracts.default_route import ResolvedPlanningRoute
from competition_app.contracts.textbook_route import ResolvedTextbookRoute
from competition_app.agents.common import envelope
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings
from competition_app.runtime.agent_registry import AgentRegistry
from competition_app.runtime.agent_communication import CognitiveGapAnalyzer
from competition_app.runtime.langgraph_orchestrator import LangGraphOrchestrator
from competition_app.runtime.orchestrator import Orchestrator
from competition_app.runtime.event_stream import bind_event_sink, reset_event_sink
from competition_app.runtime.sqlalchemy_checkpointer import SqlAlchemyCheckpointSaver
from competition_app.db.migrations import MigrationRunner


class RecordingAgent:
    def __init__(self, name: str, events: list[str], fail_once: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail_once = fail_once
        self.calls = 0

    async def run(self, context):
        self.calls += 1
        self.events.append(f"start:{self.name}")
        await asyncio.sleep(0.01)
        if self.fail_once and self.calls == 1:
            raise RuntimeError("temporary")
        self.events.append(f"end:{self.name}")
        return {
            "agent": self.name,
            "inputs": sorted(context.get("dependency_outputs", {})),
        }


class FailingAgent:
    async def run(self, context):
        raise LookupError("knowledge point could not be resolved")


class CountingAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, context):
        self.calls += 1
        return {"draft": self.calls}


class AuditSequenceAgent:
    def __init__(self, decisions: list[str]) -> None:
        self.decisions = decisions
        self.calls = 0

    async def run(self, context):
        decision = self.decisions[min(self.calls, len(self.decisions) - 1)]
        self.calls += 1
        return AuditOutput(
            payload=AuditResult(
                audit_result_id=f"AUDIT_{self.calls}",
                decision=decision,
                structured_findings=(
                    [
                        RepairIssue(
                            issue_id=f"ISSUE_{self.calls}",
                            issue_type="content_quality",
                            message="内容质量需修订",
                            owner_step_id="expert",
                            affected_step_ids=["expert"],
                        )
                    ]
                    if decision == "revise"
                    else []
                ),
            )
        )


class AuditOutput(BaseModel):
    payload: AuditResult


class EvidenceAuditSequenceAgent(AuditSequenceAgent):
    async def run(self, context):
        decision = self.decisions[min(self.calls, len(self.decisions) - 1)]
        self.calls += 1
        return AuditOutput(
            payload=AuditResult(
                audit_result_id=f"AUDIT_{self.calls}",
                decision=decision,
                findings=["证据缺失"] if decision == "revise" else [],
            )
        )


class InterruptDuringRepairExpertAgent:
    """Only asks for input on the Expert action in the repair pass."""

    def __init__(self) -> None:
        self.calls = 0
        self.interrupted = False
        self.received_findings: list[list[str]] = []

    async def run(self, context):
        self.calls += 1
        audit_feedback = context.get("audit_feedback")
        if audit_feedback is not None:
            self.received_findings.append(
                list(getattr(getattr(audit_feedback, "payload", None), "findings", []))
            )
        if audit_feedback is not None and not self.interrupted:
            self.interrupted = True
            return type(
                "RepairClarification",
                (),
                {"payload": LearningPlanClarificationResult(
                    clarification_questions=["请确认修复后的讲解范围。"],
                    reason="修复需要补充范围。",
                    requested_scope="long_term",
                )},
            )()
        return {"draft": self.calls}


class EnvelopeEvidenceAuditSequenceAgent(AuditSequenceAgent):
    async def run(self, context):
        decision = self.decisions[min(self.calls, len(self.decisions) - 1)]
        self.calls += 1
        return AgentEnvelope[AuditResult](
            artifact_id=f"ART_AUDIT_{self.calls}",
            artifact_type="audit_result",
            case_id="CASE_REPAIR_RESTART",
            trace_id="TRACE_REPAIR_RESTART",
            request_id="REQ_REPAIR_RESTART",
            execution_id="EXE_REPAIR_RESTART",
            step_id=str(context["step_id"]),
            producer="audit_agent",
            task_type="personalized_review_card",
            learner_id="LEARNER_REPAIR_RESTART",
            payload=AuditResult(
                audit_result_id=f"AUDIT_{self.calls}",
                decision=decision,
                findings=["证据缺失"] if decision == "revise" else [],
            ),
        )


class FailsDuringRepairAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, context):
        self.calls += 1
        if context.get("audit_feedback") is not None:
            raise RuntimeError("repair action failed")
        return {"draft": self.calls}


def test_restore_checkpoint_output_retypes_dict_payload_inside_envelope() -> None:
    flattened = AgentEnvelope[dict[str, object]](
        artifact_id="ART_AUDIT_FLAT",
        artifact_type="audit_result",
        case_id="CASE_FLAT",
        trace_id="TRACE_FLAT",
        request_id="REQ_FLAT",
        execution_id="EXE_FLAT",
        step_id="audit",
        producer="audit_agent",
        task_type="personalized_review_card",
        learner_id="LEARNER_FLAT",
        payload=AuditResult(
            audit_result_id="AUDIT_FLAT",
            decision="revise",
            findings=["证据缺失"],
        ).model_dump(mode="json"),
    )

    restored = LangGraphOrchestrator._restore_checkpoint_output(flattened)

    assert isinstance(restored, AgentEnvelope)
    assert isinstance(restored.payload, AuditResult)
    assert restored.payload.findings == ["证据缺失"]


class ClarifyingAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, context):
        self.calls += 1
        if "用户补充的具体变化" not in context.get("user_request", ""):
            return envelope(
                context,
                "diagnosis_agent",
                "learning_plan_clarification",
                LearningPlanClarificationResult(
                    clarification_questions=["新的目标和期限是什么？"],
                    reason="重规划信息不足。",
                    requested_scope="long_term",
                ),
            )
        return {
            "resolved_request": context["user_request"],
            "plan_scope": context.get("plan_scope"),
        }


class PrerequisiteClarifyingAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, context):
        self.calls += 1
        if "用户补充的具体变化" not in context.get("user_request", ""):
            return envelope(
                context,
                "diagnosis_agent",
                "learning_plan_clarification",
                LearningPlanClarificationResult(
                    clarification_questions=["当前还没有有效短期计划，是否先制定短期计划？"],
                    reason="当日任务必须基于有效短期计划制定。",
                    requested_scope="short_term",
                ),
            )
        return {
            "resolved_request": context["user_request"],
            "plan_scope": context.get("plan_scope"),
        }


class DependencyAwareClarifyingAgent(ClarifyingAgent):
    async def run(self, context):
        result = await super().run(context)
        if isinstance(result, dict):
            dependencies = context.get("dependency_outputs", {})
            result["dependency_keys"] = sorted(dependencies)
            route = dependencies.get("route")
            result["route_payload_type"] = type(
                getattr(route, "payload", None)
            ).__name__
        return result


class RouteEnvelopeAgent:
    async def run(self, context):
        return envelope(
            context,
            "default_route_resolver",
            "resolved_planning_route",
            ResolvedPlanningRoute(
                goal_type="credential",
                goal_name="中医执业医师",
                planning_status="provisional",
                match_reason="test",
                assumptions=["test"],
            ),
        )


class RefreshingRouteAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, context):
        self.calls += 1
        is_resumed = "用户补充的具体变化" in context.get("user_request", "")
        return envelope(
            context,
            "default_route_resolver",
            "resolved_planning_route",
            ResolvedPlanningRoute(
                goal_type="credential",
                goal_name=("中医执业医师" if is_resumed else "未提供学习目标"),
                planning_status=("approved_route" if is_resumed else "provisional"),
                route_id=("tcm_physician_standard_degree" if is_resumed else None),
                route_version=(1 if is_resumed else None),
                route_status=("approved" if is_resumed else None),
                match_reason=(
                    "agent_selected" if is_resumed else "agent_requires_clarification"
                ),
                unknowns_to_confirm=([] if is_resumed else ["准备参加什么考试？"]),
            ),
        )


class RouteDependentDiagnosisAgent:
    async def run(self, context):
        route = context["dependency_outputs"]["route_resolution"].payload
        if route.planning_status != "approved_route":
            return envelope(
                context,
                "diagnosis_agent",
                "learning_plan_clarification",
                LearningPlanClarificationResult(
                    clarification_questions=list(route.unknowns_to_confirm),
                    reason="学习目标尚未匹配到正式路线。",
                    requested_scope="long_term",
                ),
            )
        return {"route_id": route.route_id, "goal_name": route.goal_name}


class NestedTextbookRefreshingRouteAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, context):
        self.calls += 1
        is_resumed = "用户补充的具体变化" in context.get("user_request", "")
        question = "请说明要参加的具体考试或要学习的专业方向。"
        return envelope(
            context,
            "default_route_resolver",
            "resolved_planning_route",
            ResolvedPlanningRoute(
                goal_type="credential",
                goal_name=("中医执业医师考试" if is_resumed else "学习计划"),
                planning_status=("approved_route" if is_resumed else "provisional"),
                route_id=("tcm_physician_standard_degree" if is_resumed else None),
                route_version=(1 if is_resumed else None),
                route_status=("approved" if is_resumed else None),
                match_reason=("agent_selected" if is_resumed else "no_safe_match"),
                assumptions=([] if is_resumed else ["目标待确认"]),
                textbook_route=(
                    None
                    if is_resumed
                    else ResolvedTextbookRoute(
                        planning_status="unmatched",
                        match_reason="no_textbook_route_match",
                        clarification_questions=[question],
                    )
                ),
            ),
        )


class NestedTextbookDependentDiagnosisAgent:
    async def run(self, context):
        route = context["dependency_outputs"]["route_resolution"].payload
        if route.planning_status != "approved_route":
            return envelope(
                context,
                "diagnosis_agent",
                "learning_plan_clarification",
                LearningPlanClarificationResult(
                    clarification_questions=list(
                        route.textbook_route.clarification_questions
                    ),
                    reason="教材路线尚未确定。",
                    requested_scope="long_term",
                ),
            )
        return {"route_id": route.route_id, "goal_name": route.goal_name}


@pytest.mark.asyncio
async def test_langgraph_executes_parallel_dependencies_and_retries() -> None:
    events: list[str] = []
    registry = AgentRegistry()
    memory = RecordingAgent("memory", events)
    knowledge = RecordingAgent("knowledge", events, fail_once=True)
    diagnosis = RecordingAgent("diagnosis", events)
    registry.register("memory_agent", memory)
    registry.register("knowledge_agent", knowledge)
    registry.register("diagnosis_agent", diagnosis)
    plan = ExecutionPlan(
        plan_id="P_GRAPH",
        task_type="personalized_review_card",
        steps=[
            ExecutionStep(step_id="memory", agent="memory_agent"),
            ExecutionStep(step_id="knowledge", agent="knowledge_agent"),
            ExecutionStep(
                step_id="diagnosis",
                agent="diagnosis_agent",
                depends_on=["memory", "knowledge"],
            ),
        ],
    )

    result = await LangGraphOrchestrator(registry).execute(plan, {})

    assert result.status == "success"
    assert knowledge.calls == 2
    assert result.outputs["diagnosis"]["inputs"] == ["knowledge", "memory"]
    assert events.index("start:diagnosis") > events.index("end:memory")
    assert events.index("start:diagnosis") > events.index("end:knowledge")


@pytest.mark.asyncio
async def test_langgraph_preserves_step_failure_and_blocks_dependents() -> None:
    registry = AgentRegistry()
    dependent = CountingAgent()
    registry.register("knowledge_agent", FailingAgent())
    registry.register("expert_agent", dependent)
    plan = ExecutionPlan(
        plan_id="P_FAIL",
        task_type="personalized_review_card",
        steps=[
            ExecutionStep(step_id="knowledge", agent="knowledge_agent"),
            ExecutionStep(
                step_id="expert",
                agent="expert_agent",
                depends_on=["knowledge"],
            ),
        ],
    )

    result = await LangGraphOrchestrator(registry).execute(plan, {})

    assert result.status == "failed"
    assert result.error_type == "LookupError"
    assert "knowledge point could not be resolved" in result.error_message
    assert dependent.calls == 0


@pytest.mark.asyncio
async def test_langgraph_preserves_single_audit_revision() -> None:
    registry = AgentRegistry()
    expert = CountingAgent()
    audit = AuditSequenceAgent(["revise", "pass"])
    registry.register("expert_agent", expert)
    registry.register("audit_agent", audit)
    plan = ExecutionPlan(
        plan_id="P_REVISE",
        task_type="personalized_review_card",
        steps=[
            ExecutionStep(step_id="expert", agent="expert_agent"),
            ExecutionStep(
                step_id="audit", agent="audit_agent", depends_on=["expert"]
            ),
        ],
    )

    result = await LangGraphOrchestrator(registry).execute(plan, {})

    assert result.status == "success"
    assert expert.calls == 2
    assert audit.calls == 2


@pytest.mark.asyncio
async def test_langgraph_empty_modern_audit_findings_fail_closed() -> None:
    registry = AgentRegistry()
    expert = CountingAgent()
    audit = AuditSequenceAgent(["revise"])
    registry.register("expert_agent", expert)
    registry.register("audit_agent", audit)
    original_run = audit.run

    async def empty_findings_run(context):
        result = await original_run(context)
        result.payload.findings = []
        result.payload.structured_findings = []
        return result

    audit.run = empty_findings_run
    plan = ExecutionPlan(
        plan_id="P_EMPTY_FINDINGS",
        task_type="personalized_review_card",
        steps=[
            ExecutionStep(step_id="expert", agent="expert_agent"),
            ExecutionStep(step_id="audit", agent="audit_agent", depends_on=["expert"]),
        ],
    )

    result = await LangGraphOrchestrator(registry).execute(plan, {})

    assert result.status == "waiting_human_review"
    assert expert.calls == 1
    assert audit.calls == 1
    assert result.repair_trace[0].status == "stopped"


@pytest.mark.asyncio
async def test_langgraph_repair_exception_stops_trace_and_emits_safe_event() -> None:
    registry = AgentRegistry()
    knowledge = FailsDuringRepairAgent()
    registry.register("knowledge_agent", knowledge)
    registry.register("expert_agent", CountingAgent())
    registry.register("audit_agent", EvidenceAuditSequenceAgent(["revise"]))
    plan = ExecutionPlan(
        plan_id="P_REPAIR_FAILS",
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
    events: list[dict[str, object]] = []
    token = bind_event_sink(events.append)
    try:
        result = await LangGraphOrchestrator(registry).execute(plan, {})
    finally:
        reset_event_sink(token)

    assert result.status == "failed"
    assert result.repair_trace[0].status == "stopped"
    stopped = [event for event in events if event["event"] == "repair_stopped"]
    assert len(stopped) == 1
    assert stopped[0]["status"] == "failed"
    assert "error_message" not in stopped[0]


def test_langgraph_compiles_existing_execution_plan_for_visualization() -> None:
    registry = AgentRegistry()
    registry.register("memory_agent", CountingAgent())
    registry.register("expert_agent", CountingAgent())
    plan = ExecutionPlan(
        plan_id="P_VISUAL",
        task_type="personalized_review_card",
        steps=[
            ExecutionStep(step_id="memory", agent="memory_agent"),
            ExecutionStep(
                step_id="expert", agent="expert_agent", depends_on=["memory"]
            ),
        ],
    )

    graph = LangGraphOrchestrator(registry).compile_plan(plan, {})
    mermaid = graph.get_graph().draw_mermaid()

    assert "memory" in mermaid
    assert "expert" in mermaid


def test_container_uses_langgraph_by_default_and_keeps_legacy_fallback() -> None:
    default_container = ApplicationContainer.build(Settings(mode="stub"))
    legacy_container = ApplicationContainer.build(
        Settings(mode="stub", execution_engine="legacy")
    )

    assert isinstance(
        default_container.review_card_use_case.orchestrator,
        LangGraphOrchestrator,
    )
    assert type(legacy_container.review_card_use_case.orchestrator) is Orchestrator


@pytest.mark.asyncio
async def test_langgraph_interrupts_and_resumes_same_thread_from_checkpoint() -> None:
    registry = AgentRegistry()
    agent = ClarifyingAgent()
    registry.register("diagnosis_agent", agent)
    plan = ExecutionPlan(
        plan_id="P_INTERRUPT",
        task_type="learning_plan",
        steps=[ExecutionStep(step_id="diagnosis", agent="diagnosis_agent")],
    )
    context = {
        "case_id": "CASE_INTERRUPT",
        "trace_id": "TRACE_INTERRUPT",
        "request_id": "REQ_INTERRUPT",
        "execution_id": "EXE_INTERRUPT",
        "learner_id": "LEARNER_INTERRUPT",
        "task_type": "learning_plan",
        "user_request": "这个长期规划我不满意，重新计划一下",
        "original_user_request": "这个长期规划我不满意，重新计划一下",
        "messages": [],
        "available_minutes": 15,
        "multi_scale_learning_state": {"macro": {}, "meso": {}, "micro": {}},
        "interruptible": True,
        "plan_scope": "long_term",
    }
    orchestrator = LangGraphOrchestrator(registry)

    interrupted = await orchestrator.execute(
        plan, context, thread_id="THREAD_INTERRUPT_001"
    )

    assert interrupted.status == "interrupted", (
        interrupted.error_type,
        interrupted.error_message,
        interrupted.outputs,
    )
    assert interrupted.thread_id == "THREAD_INTERRUPT_001"
    assert interrupted.interrupt["step_id"] == "diagnosis"
    assert orchestrator.pending_interrupt("THREAD_INTERRUPT_001") is not None

    resumed = await orchestrator.resume(
        "THREAD_INTERRUPT_001",
        {
            "answer": "改为半年内完成方剂学，按教材章节推进。",
            "plan_scope": "long_term",
        },
    )

    assert resumed.status == "success"
    assert "半年内完成方剂学" in resumed.outputs["diagnosis"]["resolved_request"]
    assert resumed.outputs["diagnosis"]["plan_scope"] == "long_term"
    assert agent.calls == 3
    assert orchestrator.pending_interrupt("THREAD_INTERRUPT_001") is None


@pytest.mark.asyncio
async def test_langgraph_resume_uses_agent_requested_prerequisite_scope() -> None:
    registry = AgentRegistry()
    agent = PrerequisiteClarifyingAgent()
    registry.register("diagnosis_agent", agent)
    plan = ExecutionPlan(
        plan_id="P_PREREQUISITE",
        task_type="learning_plan",
        steps=[ExecutionStep(step_id="diagnosis", agent="diagnosis_agent")],
    )
    context = {
        "case_id": "CASE_PREREQUISITE",
        "trace_id": "TRACE_PREREQUISITE",
        "request_id": "REQ_PREREQUISITE",
        "execution_id": "EXE_PREREQUISITE",
        "learner_id": "LEARNER_PREREQUISITE",
        "task_type": "learning_plan",
        "user_request": "我今天要学习些什么东西？",
        "original_user_request": "我今天要学习些什么东西？",
        "messages": [],
        "available_minutes": 15,
        "multi_scale_learning_state": {"macro": {}, "meso": {}, "micro": {}},
        "interruptible": True,
        "plan_scope": "daily_task",
    }
    orchestrator = LangGraphOrchestrator(registry)

    interrupted = await orchestrator.execute(
        plan, context, thread_id="THREAD_PREREQUISITE_001"
    )
    resumed = await orchestrator.resume(
        "THREAD_PREREQUISITE_001",
        {"answer": "可以"},
    )

    assert interrupted.interrupt["requested_scope"] == "short_term"
    assert resumed.status == "success"
    assert resumed.outputs["diagnosis"]["plan_scope"] == "short_term"


@pytest.mark.asyncio
async def test_langgraph_restores_interrupted_thread_after_process_recreation(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'langgraph-restart.sqlite'}"
    migration_dir = Path(__file__).parents[2] / "migrations"
    plan = ExecutionPlan(
        plan_id="P_PERSISTENT_INTERRUPT",
        task_type="learning_plan",
        steps=[ExecutionStep(step_id="diagnosis", agent="diagnosis_agent")],
    )
    context = {
        "case_id": "CASE_PERSISTENT",
        "trace_id": "TRACE_PERSISTENT",
        "request_id": "REQ_PERSISTENT",
        "execution_id": "EXE_PERSISTENT",
        "learner_id": "LEARNER_PERSISTENT",
        "task_type": "learning_plan",
        "user_request": "重新制定长期计划",
        "original_user_request": "重新制定长期计划",
        "messages": [],
        "available_minutes": 15,
        "multi_scale_learning_state": {"macro": {}, "meso": {}, "micro": {}},
        "interruptible": True,
        "plan_scope": "long_term",
    }
    first_engine = create_engine(database_url)
    MigrationRunner(first_engine, migration_dir).run()
    first_registry = AgentRegistry()
    first_registry.register("diagnosis_agent", ClarifyingAgent())
    first = LangGraphOrchestrator(
        first_registry,
        checkpointer=SqlAlchemyCheckpointSaver(first_engine),
    )

    interrupted = await first.execute(
        plan, context, thread_id="THREAD_PERSISTENT_INTERRUPT"
    )
    assert interrupted.status == "interrupted"
    first_engine.dispose()

    second_engine = create_engine(database_url)
    second_registry = AgentRegistry()
    second_registry.register("diagnosis_agent", ClarifyingAgent())
    restored = LangGraphOrchestrator(
        second_registry,
        checkpointer=SqlAlchemyCheckpointSaver(second_engine),
    )
    resumed = await restored.resume(
        "THREAD_PERSISTENT_INTERRUPT",
        {"answer": "半年内完成方剂学", "plan_scope": "long_term"},
        plan=plan,
        context=context,
    )

    assert resumed.status == "success"
    assert "半年内完成方剂学" in resumed.outputs["diagnosis"]["resolved_request"]
    second_engine.dispose()


@pytest.mark.asyncio
async def test_process_restart_during_repair_preserves_completed_repair_nodes(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'repair-restart.sqlite'}"
    migration_dir = Path(__file__).parents[2] / "migrations"
    plan = ExecutionPlan(
        plan_id="P_REPAIR_RESTART",
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
    context = {
        "task_type": "personalized_review_card",
        "user_request": "请生成有证据的个性化讲解",
        "original_user_request": "请生成有证据的个性化讲解",
        "messages": [],
        "interruptible": True,
    }
    knowledge = CountingAgent()
    expert = InterruptDuringRepairExpertAgent()
    first_engine = create_engine(database_url)
    MigrationRunner(first_engine, migration_dir).run()
    first_registry = AgentRegistry()
    first_registry.register("knowledge_agent", knowledge)
    first_registry.register("expert_agent", expert)
    first_registry.register("audit_agent", EnvelopeEvidenceAuditSequenceAgent(["revise"]))
    first = LangGraphOrchestrator(
        first_registry,
        checkpointer=SqlAlchemyCheckpointSaver(first_engine),
    )

    interrupted = await first.execute(plan, context, thread_id="THREAD_REPAIR_1")

    assert interrupted.status == "interrupted", (
        interrupted.error_type,
        interrupted.error_message,
        interrupted.outputs,
    )
    assert "knowledge" in interrupted.outputs
    completed_knowledge_calls = knowledge.calls
    first_engine.dispose()

    second_engine = create_engine(database_url)
    second_registry = AgentRegistry()
    second_registry.register("knowledge_agent", knowledge)
    second_registry.register("expert_agent", expert)
    second_registry.register("audit_agent", EnvelopeEvidenceAuditSequenceAgent(["pass"]))
    second = LangGraphOrchestrator(
        second_registry,
        checkpointer=SqlAlchemyCheckpointSaver(second_engine),
    )
    resumed = await second.resume(
        "THREAD_REPAIR_1",
        {"answer": "继续", "plan_scope": "long_term"},
        plan=plan,
        context=context,
    )

    assert resumed.status == "success"
    assert knowledge.calls == completed_knowledge_calls
    assert resumed.repair_trace[0].status == "completed"
    assert expert.received_findings
    assert expert.received_findings[-1] == ["证据缺失"]
    second_engine.dispose()


@pytest.mark.asyncio
async def test_langgraph_resume_preserves_completed_upstream_outputs() -> None:
    registry = AgentRegistry()
    registry.register("route_agent", RouteEnvelopeAgent())
    registry.register("diagnosis_agent", DependencyAwareClarifyingAgent())
    plan = ExecutionPlan(
        plan_id="P_INTERRUPT_WITH_PARENT",
        task_type="learning_plan",
        steps=[
            ExecutionStep(step_id="route", agent="route_agent"),
            ExecutionStep(
                step_id="diagnosis",
                agent="diagnosis_agent",
                depends_on=["route"],
            ),
        ],
    )
    context = {
        "case_id": "CASE_INTERRUPT_PARENT",
        "trace_id": "TRACE_INTERRUPT_PARENT",
        "request_id": "REQ_INTERRUPT_PARENT",
        "execution_id": "EXE_INTERRUPT_PARENT",
        "learner_id": "LEARNER_INTERRUPT_PARENT",
        "task_type": "learning_plan",
        "user_request": "重新制定计划",
        "original_user_request": "重新制定计划",
        "messages": [],
        "available_minutes": 15,
        "multi_scale_learning_state": {"macro": {}, "meso": {}, "micro": {}},
        "interruptible": True,
        "plan_scope": "long_term",
    }
    orchestrator = LangGraphOrchestrator(registry)

    interrupted = await orchestrator.execute(
        plan, context, thread_id="THREAD_INTERRUPT_PARENT"
    )
    resumed = await orchestrator.resume(
        "THREAD_INTERRUPT_PARENT",
        {"answer": "一年内完成", "plan_scope": "long_term"},
    )

    assert interrupted.status == "interrupted"
    assert "route" in interrupted.outputs
    assert resumed.status == "success"
    assert set(resumed.outputs) == {"route", "diagnosis"}
    assert resumed.outputs["diagnosis"]["dependency_keys"] == ["route"]
    assert resumed.outputs["diagnosis"]["route_payload_type"] == "ResolvedPlanningRoute"


@pytest.mark.asyncio
async def test_langgraph_resume_refreshes_route_resolution_before_diagnosis() -> None:
    registry = AgentRegistry()
    route_agent = RefreshingRouteAgent()
    registry.register("default_route_resolver", route_agent)
    registry.register("diagnosis_agent", RouteDependentDiagnosisAgent())
    plan = ExecutionPlan(
        plan_id="P_INTERRUPT_REFRESH_ROUTE",
        task_type="learning_plan",
        steps=[
            ExecutionStep(
                step_id="route_resolution",
                agent="default_route_resolver",
            ),
            ExecutionStep(
                step_id="diagnosis",
                agent="diagnosis_agent",
                depends_on=["route_resolution"],
            ),
        ],
    )
    context = {
        "case_id": "CASE_INTERRUPT_REFRESH_ROUTE",
        "trace_id": "TRACE_INTERRUPT_REFRESH_ROUTE",
        "request_id": "REQ_INTERRUPT_REFRESH_ROUTE",
        "execution_id": "EXE_INTERRUPT_REFRESH_ROUTE",
        "learner_id": "LEARNER_INTERRUPT_REFRESH_ROUTE",
        "task_type": "learning_plan",
        "user_request": "请结合我的学习状态，给我制定一份长期学习计划。",
        "original_user_request": "请结合我的学习状态，给我制定一份长期学习计划。",
        "messages": [],
        "available_minutes": 15,
        "multi_scale_learning_state": {"macro": {}, "meso": {}, "micro": {}},
        "interruptible": True,
        "plan_scope": "long_term",
    }
    orchestrator = LangGraphOrchestrator(registry)

    interrupted = await orchestrator.execute(
        plan,
        context,
        thread_id="THREAD_INTERRUPT_REFRESH_ROUTE",
    )
    resumed = await orchestrator.resume(
        "THREAD_INTERRUPT_REFRESH_ROUTE",
        {
            "answer": "我想考中医执业医师资格考试",
            "plan_scope": "long_term",
        },
    )

    assert interrupted.status == "interrupted"
    assert resumed.status == "success"
    assert route_agent.calls == 2
    assert resumed.outputs["route_resolution"].payload.planning_status == "approved_route"
    assert resumed.outputs["diagnosis"] == {
        "route_id": "tcm_physician_standard_degree",
        "goal_name": "中医执业医师",
    }


@pytest.mark.asyncio
async def test_langgraph_resume_refreshes_route_for_nested_textbook_question() -> None:
    registry = AgentRegistry()
    route_agent = NestedTextbookRefreshingRouteAgent()
    registry.register("default_route_resolver", route_agent)
    registry.register(
        "diagnosis_agent", NestedTextbookDependentDiagnosisAgent()
    )
    plan = ExecutionPlan(
        plan_id="P_INTERRUPT_REFRESH_NESTED_TEXTBOOK",
        task_type="learning_plan",
        steps=[
            ExecutionStep(
                step_id="route_resolution",
                agent="default_route_resolver",
            ),
            ExecutionStep(
                step_id="diagnosis",
                agent="diagnosis_agent",
                depends_on=["route_resolution"],
            ),
        ],
    )
    context = {
        "case_id": "CASE_INTERRUPT_REFRESH_NESTED_TEXTBOOK",
        "trace_id": "TRACE_INTERRUPT_REFRESH_NESTED_TEXTBOOK",
        "request_id": "REQ_INTERRUPT_REFRESH_NESTED_TEXTBOOK",
        "execution_id": "EXE_INTERRUPT_REFRESH_NESTED_TEXTBOOK",
        "learner_id": "LEARNER_INTERRUPT_REFRESH_NESTED_TEXTBOOK",
        "task_type": "learning_plan",
        "user_request": "请制定长期学习计划。",
        "original_user_request": "请制定长期学习计划。",
        "messages": [],
        "available_minutes": 15,
        "multi_scale_learning_state": {"macro": {}, "meso": {}, "micro": {}},
        "interruptible": True,
        "plan_scope": "long_term",
    }
    orchestrator = LangGraphOrchestrator(registry)

    interrupted = await orchestrator.execute(
        plan,
        context,
        thread_id="THREAD_INTERRUPT_REFRESH_NESTED_TEXTBOOK",
    )
    resumed = await orchestrator.resume(
        "THREAD_INTERRUPT_REFRESH_NESTED_TEXTBOOK",
        {"answer": "中医执业医师考试", "plan_scope": "long_term"},
    )

    assert interrupted.status == "interrupted"
    assert interrupted.interrupt["questions"] == [
        "请说明要参加的具体考试或要学习的专业方向。"
    ]
    assert resumed.status == "success"
    assert route_agent.calls == 2
    assert resumed.outputs["diagnosis"]["route_id"] == (
        "tcm_physician_standard_degree"
    )


@pytest.mark.asyncio
async def test_langgraph_uses_same_handoff_contract_as_legacy() -> None:
    class HandoffCapturingAgent:
        def __init__(self) -> None:
            self.context = None

        async def run(self, context):
            self.context = context
            return {"status": "ok"}

    agent = HandoffCapturingAgent()
    registry = AgentRegistry()
    registry.register("handoff_agent", agent)
    plan = ExecutionPlan(
        plan_id="P_LANGGRAPH_HANDOFF",
        task_type="learning_plan",
        steps=[ExecutionStep(step_id="handoff", agent="handoff_agent")],
    )
    context = {
        "case_id": "CASE_LANGGRAPH_HANDOFF",
        "trace_id": "TRACE_LANGGRAPH_HANDOFF",
        "request_id": "REQ_LANGGRAPH_HANDOFF",
        "execution_id": "EXE_LANGGRAPH_HANDOFF",
        "learner_id": "LEARNER_LANGGRAPH_HANDOFF",
        "task_type": "learning_plan",
        "user_request": "请处理学习计划",
    }

    result = await LangGraphOrchestrator(registry).execute(plan, context)

    assert result.status == "success"
    assert result.communication_trace
    assert agent.context["agent_handoff"]["schema_version"] == "1.0"


@pytest.mark.asyncio
async def test_langgraph_rejects_interruptible_ordinary_success_with_blocking_handoff() -> None:
    registry = AgentRegistry()
    agent = CountingAgent()
    registry.register("expert_agent", agent)
    plan = ExecutionPlan(
        plan_id="P_LANGGRAPH_INTERRUPTIBLE_BLOCKED",
        task_type="knowledge_explanation",
        steps=[ExecutionStep(step_id="expert", agent="expert_agent")],
    )
    context = {
        "case_id": "CASE_LANGGRAPH_INTERRUPTIBLE_BLOCKED",
        "trace_id": "TRACE_LANGGRAPH_INTERRUPTIBLE_BLOCKED",
        "request_id": "REQ_LANGGRAPH_INTERRUPTIBLE_BLOCKED",
        "execution_id": "EXE_LANGGRAPH_INTERRUPTIBLE_BLOCKED",
        "learner_id": "LEARNER_LANGGRAPH_INTERRUPTIBLE_BLOCKED",
        "task_type": "knowledge_explanation",
        "user_request": "请生成知识讲解",
        "interruptible": True,
    }

    result = await LangGraphOrchestrator(registry).execute(plan, context)

    assert result.status == "failed"
    assert result.error_type == "AgentHandoffBlocked"
    assert result.outputs == {}
    assert agent.calls == 1
    assert result.communication_trace[-1].status == "blocked"


def test_langgraph_resume_does_not_fabricate_unrelated_handoff_facts() -> None:
    context = {
        "trace_id": "TRACE_RESUME_FACTS",
        "execution_id": "EXE_RESUME_FACTS",
        "learner_id": "LEARNER_RESUME_FACTS",
        "user_request": "重新制定学习计划",
    }

    LangGraphOrchestrator._apply_resume_value(
        context,
        {"answer": "半年内完成方剂学"},
    )
    analysis = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(step_id="diagnosis", agent="diagnosis_agent"),
        root_context=context,
        dependency_outputs={},
    )

    assert context["learning_goal"] == "半年内完成方剂学"
    assert "available_minutes" not in context
    assert "multi_scale_learning_state" not in context
    assert set(analysis.gap.blocking_fields) == {
        "time_budget",
        "multi_scale_learning_state",
    }

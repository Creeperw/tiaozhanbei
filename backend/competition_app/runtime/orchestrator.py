from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.execution import ExecutionPlan, ExecutionStep
from competition_app.runtime.agent_registry import AgentRegistry
from competition_app.runtime.agent_communication import CognitiveGapAnalyzer
from competition_app.runtime.local_repair import LocalRepairController
from competition_app.runtime.trace import (
    CommunicationTrace,
    RepairTrace,
    StepTrace,
    ToolTrace,
    TraceRecorder,
)
from competition_app.runtime.tool_registry import ToolRegistry
from competition_app.runtime.event_stream import emit_runtime_event


class ExecutionResult(BaseModel):
    status: Literal["success", "failed", "waiting_human_review", "interrupted"]
    outputs: dict[str, Any] = Field(default_factory=dict)
    trace: list[StepTrace] = Field(default_factory=list)
    tool_trace: list[ToolTrace] = Field(default_factory=list)
    communication_trace: list[CommunicationTrace] = Field(default_factory=list)
    repair_trace: list[RepairTrace] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    thread_id: str | None = None
    interrupt: dict[str, Any] | None = None


class AgentHandoffBlocked(RuntimeError):
    """The next agent cannot safely proceed without its required handoff fields."""


@dataclass
class RepairExecutionOutcome:
    outputs: dict[str, Any] | None
    final_decision: str | None


class Orchestrator:
    engine_name = "legacy"

    def __init__(
        self,
        agent_registry: AgentRegistry,
        tool_registry: ToolRegistry | None = None,
        communication_analyzer: CognitiveGapAnalyzer | None = None,
        repair_controller: LocalRepairController | None = None,
    ) -> None:
        self.agent_registry = agent_registry
        self.tool_registry = tool_registry or ToolRegistry()
        self.communication_analyzer = communication_analyzer or CognitiveGapAnalyzer()
        self.repair_controller = repair_controller or LocalRepairController()

    async def execute(
        self,
        plan: ExecutionPlan,
        context: dict[str, Any],
        *,
        thread_id: str | None = None,
    ) -> ExecutionResult:
        plan.validate_dag()
        steps = {step.step_id: step for step in plan.steps}
        outputs: dict[str, Any] = {}
        trace = TraceRecorder()
        repair_trace: list[RepairTrace] = []

        for level in plan.topological_levels():
            tasks = [self._run_step(steps[step_id], context, outputs, trace) for step_id in level]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for step_id, result in zip(level, results):
                if isinstance(result, BaseException):
                    return ExecutionResult(
                        status="failed",
                        outputs=outputs,
                        trace=trace.items,
                        tool_trace=trace.tool_items,
                        communication_trace=trace.communication_items,
                        repair_trace=repair_trace,
                        error_type=type(result).__name__,
                        error_message=(
                            f"步骤 {step_id}（{steps[step_id].agent}）失败："
                            f"{type(result).__name__}: {result or '未提供错误详情'}"
                        ),
                    )
                outputs[step_id] = result
                decision = getattr(getattr(result, "payload", None), "decision", None)
                if decision == "reject":
                    return ExecutionResult(
                        status="failed", outputs=outputs, trace=trace.items, tool_trace=trace.tool_items,
                        communication_trace=trace.communication_items,
                        repair_trace=repair_trace,
                    )
                if decision == "needs_human_review":
                    return ExecutionResult(
                        status="waiting_human_review",
                        outputs=outputs,
                        trace=trace.items,
                        tool_trace=trace.tool_items,
                        communication_trace=trace.communication_items,
                        repair_trace=repair_trace,
                    )
                if decision == "revise":
                    emit_runtime_event(
                        "audit_revision_started",
                        audit_step_id=step_id,
                        status="running",
                    )
                    try:
                        repair = await self._execute_local_repair(
                            plan=plan,
                            audit_step_id=step_id,
                            audit_step=steps[step_id],
                            root_context=context,
                            outputs=outputs,
                            trace=trace,
                            repair_trace=repair_trace,
                        )
                    except Exception as exc:
                        emit_runtime_event(
                            "audit_revision_failed",
                            audit_step_id=step_id,
                            error_type=type(exc).__name__,
                            error_message=str(exc),
                        )
                        return ExecutionResult(
                            status="failed",
                            outputs=outputs,
                            trace=trace.items,
                            tool_trace=trace.tool_items,
                            communication_trace=trace.communication_items,
                            repair_trace=repair_trace,
                            error_type=type(exc).__name__,
                            error_message=f"audit revision failed: {exc}",
                        )
                    if repair.outputs is None:
                        emit_runtime_event(
                            "audit_revision_completed",
                            audit_step_id=step_id,
                            status="needs_human_review",
                        )
                        return ExecutionResult(
                            status=(
                                "failed"
                                if repair.final_decision == "reject"
                                else "waiting_human_review"
                            ),
                            outputs=outputs,
                            trace=trace.items,
                            tool_trace=trace.tool_items,
                            communication_trace=trace.communication_items,
                            repair_trace=repair_trace,
                            error_type="AuditRevisionNeedsHumanReview",
                            error_message="audit requested revision but revised output still requires review",
                        )
                    emit_runtime_event(
                        "audit_revision_completed", audit_step_id=step_id, status="pass"
                    )
                    outputs.update(repair.outputs)

        return ExecutionResult(
            status="success",
            outputs=outputs,
            trace=trace.items,
            tool_trace=trace.tool_items,
            communication_trace=trace.communication_items,
            repair_trace=repair_trace,
        )

    async def _execute_local_repair(
        self,
        *,
        plan: ExecutionPlan,
        audit_step_id: str,
        audit_step: ExecutionStep,
        root_context: dict[str, Any],
        outputs: dict[str, Any],
        trace: TraceRecorder,
        repair_trace: list[RepairTrace],
    ) -> RepairExecutionOutcome:
        """Run the Task 3 repair plan once, retaining every unaffected output."""
        original_audit = outputs[audit_step_id]
        payload = getattr(original_audit, "payload", None)
        findings = list(getattr(payload, "findings", []) or [])
        structured_findings = list(getattr(payload, "structured_findings", []) or [])
        repair_plan = self.repair_controller.plan_repair(
            plan=plan,
            audit_step_id=audit_step_id,
            audit_findings=findings,
            structured_findings=structured_findings,
            outputs=outputs,
        )
        rerun_step_ids = [action.step_id for action in repair_plan.actions]
        record = RepairTrace(
            repair_id=repair_plan.repair_id,
            trigger_step_id=audit_step_id,
            issue_types=[issue.issue_type for issue in repair_plan.issues],
            rerun_step_ids=rerun_step_ids,
            preserved_step_ids=sorted(set(outputs) - set(rerun_step_ids)),
            status="planned",
        )
        repair_trace.append(record)
        emit_runtime_event(
            "repair_planned",
            repair_id=record.repair_id,
            trigger_step_id=audit_step_id,
            issue_types=record.issue_types,
            rerun_step_ids=record.rerun_step_ids,
            preserved_step_ids=record.preserved_step_ids,
            status=repair_plan.status,
        )

        if repair_plan.status == "needs_human_review":
            record.status = "stopped"
            record.final_audit_decision = "needs_human_review"
            emit_runtime_event(
                "repair_stopped",
                repair_id=record.repair_id,
                trigger_step_id=audit_step_id,
                status="needs_human_review",
            )
            return RepairExecutionOutcome(None, "needs_human_review")

        record.status = "running"
        repaired_outputs = dict(outputs)
        repair_context = {**root_context, "audit_feedback": original_audit}
        steps_by_id = {step.step_id: step for step in plan.steps}
        try:
            for action in repair_plan.actions:
                rerun_step = steps_by_id[action.step_id]
                if action.step_id == audit_step_id:
                    emit_runtime_event(
                        "repair_reaudit_started",
                        repair_id=record.repair_id,
                        trigger_step_id=audit_step_id,
                        status="running",
                    )
                else:
                    emit_runtime_event(
                        "repair_step_started",
                        repair_id=record.repair_id,
                        step_id=action.step_id,
                        status="running",
                    )
                repaired_outputs[action.step_id] = await self._run_step(
                    rerun_step, repair_context, repaired_outputs, trace
                )
                if action.step_id != audit_step_id:
                    emit_runtime_event(
                        "repair_step_completed",
                        repair_id=record.repair_id,
                        step_id=action.step_id,
                        status="success",
                    )
        except Exception:
            outputs.update(repaired_outputs)
            record.status = "stopped"
            record.final_audit_decision = "failed"
            emit_runtime_event(
                "repair_stopped",
                repair_id=record.repair_id,
                trigger_step_id=audit_step_id,
                status="failed",
            )
            raise

        final_decision = getattr(
            getattr(repaired_outputs[audit_step_id], "payload", None), "decision", None
        )
        record.final_audit_decision = final_decision
        if final_decision == "pass":
            record.status = "completed"
            emit_runtime_event(
                "repair_completed",
                repair_id=record.repair_id,
                trigger_step_id=audit_step_id,
                status="pass",
            )
            return RepairExecutionOutcome(repaired_outputs, final_decision)

        record.status = "stopped"
        emit_runtime_event(
            "repair_stopped",
            repair_id=record.repair_id,
            trigger_step_id=audit_step_id,
            status=final_decision or "needs_human_review",
        )
        outputs.update(repaired_outputs)
        return RepairExecutionOutcome(None, final_decision)

    async def _run_step(
        self,
        step: ExecutionStep,
        root_context: dict[str, Any],
        outputs: dict[str, Any],
        trace: TraceRecorder,
    ) -> Any:
        agent = self.agent_registry.get(step.agent)
        emit_runtime_event(
            "step_started",
            step_id=step.step_id,
            agent=step.agent,
            depends_on=step.depends_on,
        )
        step_context = dict(root_context)
        step_context["step_id"] = step.step_id
        declared_dependency_outputs = {
            dependency: outputs[dependency] for dependency in step.depends_on
        }
        dependency_outputs = self._agent_visible_dependencies(
            declared_dependency_outputs,
            learner_id=str(root_context.get("learner_id") or ""),
        )
        step_context["dependency_outputs"] = dependency_outputs
        step_context["tool_registry"] = self.tool_registry
        step_context["trace_recorder"] = trace

        analysis = None
        if self._can_prepare_handoff(root_context):
            analysis = self.communication_analyzer.analyze(
                step=step,
                root_context=root_context,
                dependency_outputs=declared_dependency_outputs,
            )
            step_context["agent_handoff"] = analysis.bundle.model_dump(mode="json")
            step_context["cognitive_gap"] = analysis.gap.model_dump(mode="json")
            if analysis.gap.blocking_fields and not root_context.get("interruptible"):
                communication = self._communication_trace(analysis, step, "blocked")
                trace.record_communication(communication)
                emit_runtime_event(
                    "handoff_blocked",
                    step_id=step.step_id,
                    agent=step.agent,
                    handoff_id=analysis.bundle.handoff_id,
                    blocking_fields=analysis.gap.blocking_fields,
                )
                raise AgentHandoffBlocked(
                    "missing required handoff fields: "
                    + ", ".join(analysis.gap.blocking_fields)
                )
            trace.record_communication(self._communication_trace(analysis, step, "prepared"))
            emit_runtime_event(
                "handoff_prepared",
                step_id=step.step_id,
                agent=step.agent,
                handoff_id=analysis.bundle.handoff_id,
                fact_count=len(analysis.bundle.confirmed_facts),
                evidence_count=len(analysis.bundle.evidence),
            )

        for attempt in range(1, step.max_retries + 2):
            try:
                result = await asyncio.wait_for(agent.run(step_context), timeout=step.timeout_seconds)
                if (
                    analysis is not None
                    and analysis.gap.blocking_fields
                    and not self._is_clarification_result(result)
                ):
                    trace.record_communication(self._communication_trace(analysis, step, "blocked"))
                    emit_runtime_event(
                        "handoff_blocked",
                        step_id=step.step_id,
                        agent=step.agent,
                        handoff_id=analysis.bundle.handoff_id,
                        blocking_fields=analysis.gap.blocking_fields,
                    )
                    raise AgentHandoffBlocked(
                        "missing required handoff fields: "
                        + ", ".join(analysis.gap.blocking_fields)
                    )
                trace.record(step.step_id, step.agent, "success", attempt)
                if analysis is not None:
                    trace.record_communication(self._communication_trace(analysis, step, "consumed"))
                    emit_runtime_event(
                        "handoff_consumed",
                        step_id=step.step_id,
                        agent=step.agent,
                        handoff_id=analysis.bundle.handoff_id,
                    )
                emit_runtime_event(
                    "system_output",
                    step_id=step.step_id,
                    agent=step.agent,
                    output=result,
                )
                emit_runtime_event(
                    "step_completed", step_id=step.step_id, agent=step.agent, status="success"
                )
                return result
            except AgentHandoffBlocked:
                trace.record(step.step_id, step.agent, "failed", attempt, "AgentHandoffBlocked")
                raise
            except Exception as exc:
                if attempt <= step.max_retries:
                    emit_runtime_event(
                        "step_retrying",
                        step_id=step.step_id,
                        agent=step.agent,
                        attempt=attempt,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                    trace.record(step.step_id, step.agent, "retrying", attempt, type(exc).__name__)
                    continue
                trace.record(step.step_id, step.agent, "failed", attempt, type(exc).__name__)
                raise
        raise RuntimeError("unreachable")

    @staticmethod
    def _can_prepare_handoff(root_context: dict[str, Any]) -> bool:
        return all(
            root_context.get(field) not in (None, "")
            for field in ("trace_id", "execution_id", "learner_id")
        )

    def _agent_visible_dependencies(
        self,
        dependency_outputs: dict[str, Any], *, learner_id: str
    ) -> dict[str, Any]:
        visible: dict[str, Any] = {}
        for step_id, output in dependency_outputs.items():
            if isinstance(output, AgentEnvelope):
                if output.learner_id != learner_id:
                    continue
                visible[step_id] = output
            elif isinstance(output, Mapping):
                visible[step_id] = self.communication_analyzer.sanitize_compatibility_output(
                    output
                )
        return visible

    @staticmethod
    def _is_clarification_result(result: Any) -> bool:
        return bool(getattr(getattr(result, "payload", None), "requires_clarification", False))

    @staticmethod
    def _communication_trace(analysis: Any, step: ExecutionStep, status: str) -> CommunicationTrace:
        return CommunicationTrace(
            handoff_id=analysis.bundle.handoff_id,
            step_id=step.step_id,
            target_agent=step.agent,
            fact_count=len(analysis.bundle.confirmed_facts),
            evidence_count=len(analysis.bundle.evidence),
            blocking_field_count=len(analysis.gap.blocking_fields),
            omitted_categories=analysis.gap.omitted_categories,
            status=status,
        )

    async def _revise_once(
        self,
        audit_step_id: str,
        audit_step: ExecutionStep,
        root_context: dict[str, Any],
        outputs: dict[str, Any],
        trace: TraceRecorder,
    ) -> dict[str, Any] | None:
        raise RuntimeError(
            "direct legacy revision is disabled; use the bounded local repair controller"
        )

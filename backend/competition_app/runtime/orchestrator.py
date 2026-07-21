from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, Field

from competition_app.contracts.execution import ExecutionPlan, ExecutionStep
from competition_app.runtime.agent_registry import AgentRegistry
from competition_app.runtime.trace import StepTrace, ToolTrace, TraceRecorder
from competition_app.runtime.tool_registry import ToolRegistry
from competition_app.runtime.event_stream import emit_runtime_event


class ExecutionResult(BaseModel):
    status: Literal["success", "failed", "waiting_human_review", "interrupted"]
    outputs: dict[str, Any] = Field(default_factory=dict)
    trace: list[StepTrace] = Field(default_factory=list)
    tool_trace: list[ToolTrace] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    thread_id: str | None = None
    interrupt: dict[str, Any] | None = None


class Orchestrator:
    engine_name = "legacy"

    def __init__(self, agent_registry: AgentRegistry, tool_registry: ToolRegistry | None = None) -> None:
        self.agent_registry = agent_registry
        self.tool_registry = tool_registry or ToolRegistry()

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
                        status="failed", outputs=outputs, trace=trace.items, tool_trace=trace.tool_items
                    )
                if decision == "needs_human_review":
                    return ExecutionResult(
                        status="waiting_human_review",
                        outputs=outputs,
                        trace=trace.items,
                        tool_trace=trace.tool_items,
                    )
                if decision == "revise":
                    emit_runtime_event(
                        "audit_revision_started",
                        audit_step_id=step_id,
                        findings=getattr(getattr(result, "payload", None), "findings", []),
                    )
                    try:
                        revised = await self._revise_once(
                            step_id, steps[step_id], context, outputs, trace
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
                            error_type=type(exc).__name__,
                            error_message=f"audit revision failed: {exc}",
                        )
                    if revised is None:
                        emit_runtime_event(
                            "audit_revision_completed",
                            audit_step_id=step_id,
                            status="needs_human_review",
                        )
                        revised_decision = getattr(
                            getattr(outputs.get(step_id), "payload", None),
                            "decision",
                            None,
                        )
                        return ExecutionResult(
                            status=(
                                "failed"
                                if revised_decision == "reject"
                                else "waiting_human_review"
                            ),
                            outputs=outputs,
                            trace=trace.items,
                            tool_trace=trace.tool_items,
                            error_type="AuditRevisionNeedsHumanReview",
                            error_message="audit requested revision but revised output still requires review",
                        )
                    emit_runtime_event(
                        "audit_revision_completed", audit_step_id=step_id, status="pass"
                    )
                    outputs.update(revised)

        return ExecutionResult(
            status="success", outputs=outputs, trace=trace.items, tool_trace=trace.tool_items
        )

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
        step_context["dependency_outputs"] = {
            dependency: outputs[dependency] for dependency in step.depends_on
        }
        step_context["tool_registry"] = self.tool_registry
        step_context["trace_recorder"] = trace

        for attempt in range(1, step.max_retries + 2):
            try:
                result = await asyncio.wait_for(agent.run(step_context), timeout=step.timeout_seconds)
                trace.record(step.step_id, step.agent, "success", attempt)
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

    async def _revise_once(
        self,
        audit_step_id: str,
        audit_step: ExecutionStep,
        root_context: dict[str, Any],
        outputs: dict[str, Any],
        trace: TraceRecorder,
    ) -> dict[str, Any] | None:
        expert_step_id = next(
            (
                dependency
                for dependency in audit_step.depends_on
                if dependency in {"paper_assembly", "expert"}
            ),
            None,
        )
        if expert_step_id is None:
            return None
        task_type = str(root_context.get("task_type", ""))
        if expert_step_id == "paper_assembly":
            expert_agent_name = "paper_assembly_agent"
        elif task_type == "knowledge_explanation":
            expert_agent_name = "knowledge_explanation_agent"
        else:
            expert_agent_name = "expert_agent"
        expert_step = ExecutionStep(
            step_id=expert_step_id,
            agent=expert_agent_name,
            timeout_seconds=180.0 if expert_step_id == "paper_assembly" else 60.0,
        )
        revision_context = dict(root_context)
        revision_context["step_id"] = expert_step_id
        revision_context["dependency_outputs"] = {
            key: value for key, value in outputs.items() if key != expert_step_id
        }
        revision_context["audit_feedback"] = outputs[audit_step_id]
        expert = self.agent_registry.get(expert_agent_name)
        revised_expert = await asyncio.wait_for(
            expert.run(revision_context), timeout=expert_step.timeout_seconds
        )
        trace.record(expert_step_id, "expert_agent", "success", 2)

        audit_context = dict(root_context)
        audit_context["step_id"] = audit_step_id
        audit_context["audit_feedback"] = outputs[audit_step_id]
        audit_context["dependency_outputs"] = {
            dependency: revised_expert if dependency == expert_step_id else outputs[dependency]
            for dependency in audit_step.depends_on
        }
        audit = self.agent_registry.get(audit_step.agent)
        revised_audit = await asyncio.wait_for(
            audit.run(audit_context), timeout=audit_step.timeout_seconds
        )
        outputs[audit_step_id] = revised_audit
        trace.record(audit_step_id, audit_step.agent, "success", 2)
        final_decision = getattr(getattr(revised_audit, "payload", None), "decision", None)
        if final_decision != "pass":
            return None
        return {expert_step_id: revised_expert, audit_step_id: revised_audit}

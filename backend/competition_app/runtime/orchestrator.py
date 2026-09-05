from __future__ import annotations

import asyncio
import json
import hashlib
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
from competition_app.runtime.event_stream import (
    build_public_agent_output,
    emit_runtime_event,
)
from competition_app.runtime.snapshot import _sanitize
from competition_app.llm.openai_compatible import ModelResponseError
from competition_app.runtime.evolution_rules import EvolutionRuleRegistry


class ExecutionResult(BaseModel):
    status: Literal["success", "failed", "waiting_human_review", "interrupted"]
    outputs: dict[str, Any] = Field(default_factory=dict)
    trace: list[StepTrace] = Field(default_factory=list)
    tool_trace: list[ToolTrace] = Field(default_factory=list)
    communication_trace: list[CommunicationTrace] = Field(default_factory=list)
    repair_trace: list[RepairTrace] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    error_diagnostics: dict[str, Any] = Field(default_factory=dict)
    thread_id: str | None = None
    interrupt: dict[str, Any] | None = None


class AgentHandoffBlocked(RuntimeError):
    """The next agent cannot safely proceed without its required handoff fields."""


class StepDeadlineExceeded(TimeoutError):
    """Raised when the orchestrator cancels an agent at its step deadline."""

    def __init__(self, step: ExecutionStep) -> None:
        super().__init__(
            f"orchestrator step deadline exceeded for {step.step_id} "
            f"({step.timeout_seconds:.0f}s)"
        )
        self.step_id = step.step_id
        self.agent = step.agent
        self.step_timeout_seconds = float(step.timeout_seconds)
        self.cancel_source = "orchestrator_step_deadline"
        self.last_timing_details: dict[str, Any] = {}


def failure_diagnostics(error: BaseException) -> dict[str, Any]:
    """Return bounded, machine-owned metadata for an execution failure."""

    diagnostics: dict[str, Any] = {}
    timeout = getattr(error, "step_timeout_seconds", None)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = None
    if timeout is not None and 0 < timeout <= 86_400:
        diagnostics["step_timeout_seconds"] = timeout
    cancel_source = str(getattr(error, "cancel_source", "") or "")
    if cancel_source == "orchestrator_step_deadline":
        diagnostics["cancel_source"] = cancel_source
    timing = getattr(error, "last_timing_details", None)
    if isinstance(timing, dict):
        for key, maximum in (
            ("provider_duration_ms", 86_400_000),
            ("request_attempt_count", 100),
            ("reasoning_delta_count", 10_000_000),
            ("response_chars", 100_000_000),
        ):
            try:
                value = int(timing.get(key))
            except (TypeError, ValueError):
                continue
            if 0 <= value <= maximum:
                diagnostics[key] = value
        last_reasoning_at = timing.get("last_reasoning_at_monotonic")
        if isinstance(last_reasoning_at, (int, float)) and (
            0 <= float(last_reasoning_at) <= 10_000_000_000
        ):
            diagnostics["last_reasoning_at_monotonic"] = float(
                last_reasoning_at
            )
    return diagnostics


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
        evolution_rule_registry: EvolutionRuleRegistry | None = None,
        provider_timeout_seconds: float | None = None,
    ) -> None:
        self.agent_registry = agent_registry
        self.tool_registry = tool_registry or ToolRegistry()
        self.communication_analyzer = communication_analyzer or CognitiveGapAnalyzer()
        self.repair_controller = repair_controller or LocalRepairController()
        self.evolution_rule_registry = evolution_rule_registry
        self.provider_timeout_seconds = provider_timeout_seconds

    async def execute(
        self,
        plan: ExecutionPlan,
        context: dict[str, Any],
        *,
        thread_id: str | None = None,
    ) -> ExecutionResult:
        plan.validate_dag()
        if self.provider_timeout_seconds is not None:
            plan.validate_deadlines(
                provider_timeout_seconds=self.provider_timeout_seconds
            )
        steps = {step.step_id: step for step in plan.steps}
        outputs: dict[str, Any] = {}
        trace = TraceRecorder()
        repair_trace: list[RepairTrace] = []

        for level in plan.topological_levels():
            self._raise_if_cancelled(context)
            tasks = [self._run_step(steps[step_id], context, outputs, trace) for step_id in level]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for step_id, result in zip(level, results):
                if isinstance(result, asyncio.CancelledError):
                    raise result
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
                        error_diagnostics=failure_diagnostics(result),
                    )
                self._raise_if_cancelled(context)
                outputs[step_id] = result
                decision = getattr(getattr(result, "payload", None), "decision", None)
                if decision == "reject":
                    return ExecutionResult(
                        status="failed", outputs=outputs, trace=trace.items, tool_trace=trace.tool_items,
                        communication_trace=trace.communication_items,
                        repair_trace=repair_trace,
                    )
                # 人工复核不是直接对用户暴露审核报告的终点：只要审核
                # 产出了可定位的问题，先复用同一套受控返修流程，再进行
                # 一次审核。返修审核通过后才能进入发布；无法安全生成
                # 返修计划时，_execute_local_repair 会返回人工复核状态。
                if decision in {"revise", "needs_human_review"}:
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
                    except asyncio.CancelledError:
                        raise
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

        self._raise_if_cancelled(context)
        return ExecutionResult(
            status="success",
            outputs=outputs,
            trace=trace.items,
            tool_trace=trace.tool_items,
            communication_trace=trace.communication_items,
            repair_trace=repair_trace,
        )

    @staticmethod
    def _raise_if_cancelled(context: dict[str, Any]) -> None:
        check = context.get("cancellation_check")
        if callable(check):
            check()

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
        preserved_step_ids = sorted(set(outputs) - set(rerun_step_ids))
        record = RepairTrace(
            repair_id=repair_plan.repair_id,
            trigger_step_id=audit_step_id,
            issue_types=[issue.issue_type for issue in repair_plan.issues],
            issue_ids=[issue.issue_id for issue in repair_plan.issues],
            initial_audit_decision=getattr(payload, "decision", None),
            origin_step_ids=list(dict.fromkeys(
                issue.origin_step_id
                for issue in repair_plan.issues
                if issue.origin_step_id
            )),
            owner_step_ids=list(dict.fromkeys(
                issue.owner_step_id
                for issue in repair_plan.issues
                if issue.owner_step_id
            )),
            location_labels=list(dict.fromkeys(
                location.display_label
                for issue in repair_plan.issues
                for location in issue.locations
            ))[:8],
            rerun_step_ids=rerun_step_ids,
            preserved_step_ids=preserved_step_ids,
            status="planned",
            before_digest=self.repair_controller._output_digest({
                action.step_id: outputs.get(action.step_id)
                for action in repair_plan.actions
                if action.step_id != audit_step_id
            }),
            preserved_before_digest=self.repair_controller._output_digest({
                step_id: outputs.get(step_id) for step_id in preserved_step_ids
            }),
        )
        repair_trace.append(record)
        emit_runtime_event(
            "repair_planned",
            repair_id=record.repair_id,
            trigger_step_id=audit_step_id,
            issue_types=record.issue_types,
            issue_ids=record.issue_ids,
            location_labels=record.location_labels,
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
        steps_by_id = {step.step_id: step for step in plan.steps}
        try:
            for action in repair_plan.actions:
                self._raise_if_cancelled(root_context)
                rerun_step = steps_by_id[action.step_id]
                action_payload = action.model_dump(mode="json")
                if action.step_id == "paper_assembly":
                    previous_output = repaired_outputs.get(action.step_id)
                    previous_payload = getattr(
                        previous_output, "payload", previous_output
                    )
                    previous_question_ids = [
                        str(item.question.question_id)
                        for item in list(
                            getattr(previous_payload, "items", []) or []
                        )
                    ]
                    scoped_ids = set(action.scope_question_ids)
                    action_payload["preserve_question_ids"] = [
                        question_id
                        for question_id in previous_question_ids
                        if question_id not in scoped_ids
                    ]
                repair_context = {
                    **root_context,
                    "audit_feedback": original_audit,
                    "repair_instruction": action_payload,
                    "previous_step_output": repaired_outputs.get(action.step_id),
                }
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
                self._raise_if_cancelled(root_context)
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
        record.after_digest = self.repair_controller._output_digest({
            action.step_id: repaired_outputs.get(action.step_id)
            for action in repair_plan.actions
            if action.step_id != audit_step_id
        })
        record.preserved_after_digest = self.repair_controller._output_digest({
            step_id: repaired_outputs.get(step_id)
            for step_id in record.preserved_step_ids
        })
        record.preserved_outputs_unchanged = (
            record.preserved_before_digest == record.preserved_after_digest
        )
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
        dependency_names = list(step.depends_on)
        emit_runtime_event(
            "step_started",
            step_id=step.step_id,
            agent=step.agent,
            depends_on=step.depends_on,
            input_summary=self._runtime_input_summary(
                root_context,
                step,
                dependency_names,
            ),
        )
        step_context = dict(root_context)
        step_context["step_id"] = step.step_id
        if step.plan_scope is not None:
            step_context["plan_scope"] = step.plan_scope
            if step.agent == "diagnosis_agent":
                step_context["task_type"] = "learning_plan"
        if step.action == "query_learner_data":
            # Diagnosis 以 learner_data_query 语义执行时（如组卷链路中读取
            # 学习进度供蓝图使用），必须以查询模式运行而不是规划模式。
            step_context["task_type"] = "learner_data_query"
        if step.audit_subject is not None:
            step_context["audit_subject"] = step.audit_subject
        declared_dependency_outputs = {
            dependency: outputs[dependency] for dependency in step.depends_on
        }
        dependency_outputs = self._agent_visible_dependencies(
            declared_dependency_outputs,
            learner_id=str(root_context.get("learner_id") or ""),
        )
        diagnosis_key = (
            "diagnosis_long"
            if step.plan_scope == "long_term"
            else "diagnosis_short"
            if step.plan_scope == "short_term"
            else "diagnosis_short"
            if "diagnosis_short" in dependency_outputs
            else "diagnosis_long"
            if "diagnosis_long" in dependency_outputs
            else None
        )
        if diagnosis_key is not None and diagnosis_key in dependency_outputs:
            dependency_outputs.setdefault("diagnosis", dependency_outputs[diagnosis_key])
        if step.plan_scope == "short_term" and "diagnosis_long" in dependency_outputs:
            long_diagnosis = dependency_outputs["diagnosis_long"].payload
            long_proposal = getattr(long_diagnosis, "learning_plan_proposal", None)
            if long_proposal is not None:
                long_payload = long_proposal.model_dump(mode="json")
                step_context["current_long_term_plan"] = {
                    **long_payload,
                    "plan_id": "PENDING_AUDITED_LONG_PLAN",
                    "status": "active",
                }
        if step.agent == "learning_plan_service":
            dependency_outputs.setdefault(
                "audit", dependency_outputs.get("audit_short")
            )
        step_context["dependency_outputs"] = dependency_outputs
        step_context["tool_registry"] = self.tool_registry
        step_context["trace_recorder"] = trace
        if self.evolution_rule_registry is not None:
            strategies = self.evolution_rule_registry.resolve(
                target_agent=step.agent,
                target_step_id=step.step_id,
                task_type=str(step_context.get("task_type") or ""),
            )
            if strategies:
                step_context["evolution_strategies"] = [
                    strategy.as_context() for strategy in strategies
                ]
                self.evolution_rule_registry.record_exposure(
                    strategies,
                    execution_id=str(root_context.get("execution_id") or "") or None,
                    target_agent=step.agent,
                    input_digest=hashlib.sha256(
                        str(root_context.get("user_request") or "").encode("utf-8")
                    ).hexdigest(),
                )

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
                self._raise_if_cancelled(root_context)
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
                    output_kind=(
                        "compiler"
                        if "compiler" in str(step.agent).lower()
                        else "business"
                    ),
                    output_summary=self._runtime_output_summary(result),
                    # Full learner-relevant prose is projected from the real
                    # AgentEnvelope before the generic observability summary is
                    # truncated. Prompts, compiler JSON and internal IDs never
                    # enter this field.
                    public_output=build_public_agent_output(step.agent, result),
                )
                emit_runtime_event(
                    "step_completed", step_id=step.step_id, agent=step.agent,
                    status="success",
                    output_kind=(
                        "compiler"
                        if "compiler" in str(step.agent).lower()
                        else "business"
                    ),
                )
                return result
            except asyncio.CancelledError:
                trace.record(step.step_id, step.agent, "failed", attempt, "CancelledError")
                raise
            except AgentHandoffBlocked:
                trace.record(step.step_id, step.agent, "failed", attempt, "AgentHandoffBlocked")
                raise
            except asyncio.TimeoutError:
                # 步骤预算耗尽（wait_for 超时）直接失败，不再重试：模型传输层
                # 已有 bounded 重试，整个 Agent 重跑只会重复已完成的工作，
                # 并把单步挂起放大为多倍预算（max_retries × timeout）。
                trace.record(step.step_id, step.agent, "failed", attempt, "TimeoutError")
                emit_runtime_event(
                    "step_timeout",
                    step_id=step.step_id,
                    agent=step.agent,
                    attempt=attempt,
                    timeout_seconds=step.timeout_seconds,
                    cancel_source="orchestrator_step_deadline",
                )
                timeout_error = StepDeadlineExceeded(step)
                model_trace_recorder = root_context.get("model_trace_recorder")
                if model_trace_recorder is not None:
                    items = getattr(model_trace_recorder, "items", [])
                    if items:
                        latest = items[-1]
                        timeout_error.last_timing_details = {
                            key: value
                            for key, value in {
                                "provider_duration_ms": getattr(
                                    latest, "provider_duration_ms", None
                                ),
                                "request_attempt_count": getattr(
                                    latest, "request_attempt_count", None
                                ),
                                "last_reasoning_at_monotonic": getattr(
                                    latest, "last_reasoning_at_monotonic", None
                                ),
                                "reasoning_delta_count": getattr(
                                    latest, "reasoning_delta_count", None
                                ),
                                "response_chars": getattr(
                                    latest, "response_chars", None
                                ),
                            }.items()
                            if value is not None
                        }
                setattr(
                    timeout_error,
                    "last_error_details",
                    {
                        "reason": "step_timeout",
                        "cancel_source": timeout_error.cancel_source,
                    },
                )
                raise timeout_error from None
            except Exception as exc:
                # Model adapters already own transport retry, structured-output
                # repair and provider failover.  Re-running the whole Agent here
                # multiplies those attempts and repeats completed tool work.
                if isinstance(exc, ModelResponseError):
                    trace.record(step.step_id, step.agent, "failed", attempt, type(exc).__name__)
                    raise
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
    def _runtime_input_summary(
        root_context: dict[str, Any],
        step: ExecutionStep,
        dependency_names: list[str],
    ) -> dict[str, Any]:
        """Expose a bounded, secret-safe handoff summary to the UI."""
        return _sanitize(
            {
                "user_request": str(root_context.get("user_request") or "")[:1200],
                "original_user_request": str(
                    root_context.get("original_user_request")
                    or root_context.get("user_request")
                    or ""
                )[:1200],
                "task_type": root_context.get("task_type"),
                "plan_scope": root_context.get("plan_scope"),
                "available_minutes": root_context.get("available_minutes"),
                "dependencies": dependency_names,
                "has_user_profile": bool(
                    root_context.get("user_profile") or root_context.get("profile")
                ),
                "has_compressed_history": bool(
                    root_context.get("compressed_conversation_summary")
                ),
                "recent_message_count": min(
                    len(root_context.get("messages") or []),
                    99,
                ),
                "has_external_information": bool(
                    root_context.get("current_page_context")
                    or root_context.get("external_information")
                    or root_context.get("external_information_request")
                ),
                "has_learning_monitoring": bool(root_context.get("learning_monitoring")),
                "has_existing_long_term_plan": bool(
                    (root_context.get("current_long_term_plan") or {}).get("content")
                ),
                "has_existing_short_term_plan": bool(
                    (root_context.get("current_short_term_plan") or {}).get("content")
                ),
            }
        )

    @staticmethod
    def _runtime_output_summary(result: Any) -> dict[str, Any]:
        """Keep the full agent result inspectable without flooding SSE."""
        safe = _sanitize(result)
        # Test doubles and a few internal agents return lightweight objects
        # instead of dicts/envelopes.  Convert those through their public
        # payload/model_dump surface before serializing the trace; observability
        # must never change the workflow result.
        if not isinstance(safe, (dict, list, str, int, float, bool, type(None))):
            if hasattr(safe, "model_dump"):
                safe = _sanitize(safe.model_dump(mode="json"))
            elif hasattr(safe, "payload"):
                payload = getattr(safe, "payload")
                safe = {
                    "payload": _sanitize(
                        payload.model_dump(mode="json")
                        if hasattr(payload, "model_dump")
                        else payload
                    )
                }
            else:
                safe = {"repr": _sanitize(repr(safe))}
        if isinstance(safe, dict):
            payload = safe.get("payload", safe)
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if len(encoded) > 6000:
                payload = {"truncated": encoded[:6000], "truncated_chars": len(encoded)}
            return {
                "artifact_type": safe.get("artifact_type"),
                "producer": safe.get("producer"),
                "payload": payload,
            }
        encoded = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
        return {"payload": encoded[:6000], "truncated_chars": len(encoded)}

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

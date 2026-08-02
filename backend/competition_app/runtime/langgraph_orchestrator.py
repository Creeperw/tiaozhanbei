from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph._internal._runnable import set_config_context
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.default_route import ResolvedPlanningRoute
from competition_app.contracts.execution import ExecutionPlan, ExecutionStep
from competition_app.contracts.knowledge import EvidencePack
from competition_app.contracts.local_repair import LocalRepairPlan
from competition_app.contracts.resource import AuditResult
from competition_app.contracts.paper import (
    ExamPaperDraft,
    PaperBlueprint,
    QuestionCandidatePool,
)
from competition_app.runtime.event_stream import emit_runtime_event
from competition_app.runtime.orchestrator import ExecutionResult, Orchestrator
from competition_app.runtime.trace import CommunicationTrace, RepairTrace, TraceRecorder


def _merge_mappings(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Merge independent node updates; a controlled revision may replace one output."""
    return {**left, **right}


def _merge_communication_traces(
    left: list[CommunicationTrace], right: list[CommunicationTrace]
) -> list[CommunicationTrace]:
    """Keep independently completed handoffs while avoiding resume duplicates."""
    merged: dict[tuple[str, str], CommunicationTrace] = {}
    for item in [*left, *right]:
        trace = (
            item
            if isinstance(item, CommunicationTrace)
            else CommunicationTrace.model_validate(item)
        )
        merged[(trace.handoff_id, trace.status)] = trace
    return list(merged.values())


def _append_unique_repair_trace(
    left: list[RepairTrace], right: list[RepairTrace]
) -> list[RepairTrace]:
    """Replace the current state of a repair without duplicating it after resume."""
    merged: dict[str, RepairTrace] = {}
    for item in [*left, *right]:
        trace = item if isinstance(item, RepairTrace) else RepairTrace.model_validate(item)
        merged[trace.repair_id] = trace
    return list(merged.values())


class LangGraphExecutionState(TypedDict):
    outputs: Annotated[dict[str, Any], _merge_mappings]
    failures: Annotated[dict[str, dict[str, str]], _merge_mappings]
    blocked_steps: Annotated[dict[str, str], _merge_mappings]
    terminal_states: Annotated[dict[str, dict[str, str | None]], _merge_mappings]
    communication_trace: Annotated[list[CommunicationTrace], _merge_communication_traces]
    repair_plans: Annotated[dict[str, dict[str, Any]], _merge_mappings]
    repair_progress: Annotated[dict[str, dict[str, Any]], _merge_mappings]
    repair_trace: Annotated[list[RepairTrace], _append_unique_repair_trace]


@dataclass
class _InterruptedSession:
    graph: Any
    config: dict[str, Any]
    plan: ExecutionPlan
    trace: TraceRecorder
    context: dict[str, Any]


class LangGraphOrchestrator(Orchestrator):
    """Execute the existing dynamic ExecutionPlan through a LangGraph StateGraph.

    The checkpointer is injected by the application container. Production database
    deployments use durable SQL checkpoints; dependency-free tests keep the in-memory
    saver.
    """

    engine_name = "langgraph"
    _RESUME_SENSITIVE_AGENTS = frozenset({"default_route_resolver"})

    def __init__(self, agent_registry, tool_registry=None, *, checkpointer=None) -> None:
        super().__init__(agent_registry, tool_registry)
        serde = JsonPlusSerializer(
            pickle_fallback=True,
            allowed_msgpack_modules=True,
        )
        self._checkpointer = checkpointer or InMemorySaver(serde=serde)
        self.persistent_checkpoints = bool(
            getattr(self._checkpointer, "persistent", False)
        )
        self._interrupted_sessions: dict[str, _InterruptedSession] = {}

    def restore_thread(
        self,
        thread_id: str,
        plan: ExecutionPlan,
        context: dict[str, Any],
    ) -> None:
        """Recompile closures and attach them to an existing durable checkpoint."""

        if thread_id in self._interrupted_sessions:
            return
        plan.validate_dag()
        trace = TraceRecorder()
        graph = self.compile_plan(plan, context, trace)
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = graph.get_state(config)
        if not snapshot or not snapshot.tasks:
            raise KeyError(f"LangGraph thread {thread_id} has no durable checkpoint")
        self._interrupted_sessions[thread_id] = _InterruptedSession(
            graph=graph,
            config=config,
            plan=plan,
            trace=trace,
            context=context,
        )

    async def execute(
        self,
        plan: ExecutionPlan,
        context: dict[str, Any],
        *,
        thread_id: str | None = None,
    ) -> ExecutionResult:
        plan.validate_dag()
        resolved_thread_id = thread_id or f"THREAD_{uuid4().hex}"
        trace = TraceRecorder()
        graph = self.compile_plan(plan, context, trace)
        config = {"configurable": {"thread_id": resolved_thread_id}}
        self._interrupted_sessions[resolved_thread_id] = _InterruptedSession(
            graph=graph,
            config=config,
            plan=plan,
            trace=trace,
            context=context,
        )
        initial_state: LangGraphExecutionState = {
            "outputs": {},
            "failures": {},
            "blocked_steps": {},
            "terminal_states": {},
            "communication_trace": [],
            "repair_plans": {},
            "repair_progress": {},
            "repair_trace": [],
        }
        try:
            state = await graph.ainvoke(initial_state, config=config)
        except Exception as exc:
            self._interrupted_sessions.pop(resolved_thread_id, None)
            await self._checkpointer.adelete_thread(resolved_thread_id)
            return ExecutionResult(
                status="failed",
                trace=trace.items,
                tool_trace=trace.tool_items,
                communication_trace=trace.communication_items,
                error_type=type(exc).__name__,
                error_message=f"LangGraph execution failed: {exc}",
                thread_id=resolved_thread_id,
            )

        result = self._result_from_state(
            state, plan=plan, trace=trace, thread_id=resolved_thread_id
        )
        if result.status != "interrupted":
            self._interrupted_sessions.pop(resolved_thread_id, None)
            await self._checkpointer.adelete_thread(resolved_thread_id)
        return result

    async def resume(
        self,
        thread_id: str,
        resume_value: Any,
        *,
        plan: ExecutionPlan | None = None,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        session = self._interrupted_sessions.get(thread_id)
        if session is None and plan is not None and context is not None:
            self.restore_thread(thread_id, plan, context)
            session = self._interrupted_sessions.get(thread_id)
        if session is None:
            raise KeyError(f"LangGraph thread {thread_id} is not waiting for input")
        if context:
            # Graph node closures retain the original root_context object.  A
            # resumed run may have materialized a parent plan or appended
            # profile/memory facts meanwhile, so update that same object
            # instead of merely passing an unused context argument around.
            session.context.update(context)
        emit_runtime_event("graph_resume_requested", thread_id=thread_id)
        try:
            state = await session.graph.ainvoke(
                Command(resume=resume_value),
                config=session.config,
            )
        except Exception as exc:
            self._interrupted_sessions.pop(thread_id, None)
            await self._checkpointer.adelete_thread(thread_id)
            return ExecutionResult(
                status="failed",
                trace=session.trace.items,
                tool_trace=session.trace.tool_items,
                communication_trace=session.trace.communication_items,
                error_type=type(exc).__name__,
                error_message=f"LangGraph resume failed: {exc}",
                thread_id=thread_id,
            )
        result = self._result_from_state(
            state,
            plan=session.plan,
            trace=session.trace,
            thread_id=thread_id,
        )
        if result.status != "interrupted":
            self._interrupted_sessions.pop(thread_id, None)
            await self._checkpointer.adelete_thread(thread_id)
        return result

    def pending_interrupt(self, thread_id: str) -> dict[str, Any] | None:
        session = self._interrupted_sessions.get(thread_id)
        if session is None:
            return None
        snapshot = session.graph.get_state(session.config)
        for task in snapshot.tasks:
            if task.interrupts:
                value = task.interrupts[0].value
                return value if isinstance(value, dict) else {"prompt": str(value)}
        return None

    def _result_from_state(
        self,
        state: dict[str, Any],
        *,
        plan: ExecutionPlan,
        trace: TraceRecorder,
        thread_id: str,
    ) -> ExecutionResult:
        interruptions = state.get("__interrupt__") or []
        if interruptions:
            value = interruptions[0].value
            interrupt_payload = (
                value if isinstance(value, dict) else {"prompt": str(value)}
            )
            emit_runtime_event("graph_interrupted", **interrupt_payload)
            return ExecutionResult(
                status="interrupted",
                outputs=dict(state.get("outputs", {})),
                trace=trace.items,
                tool_trace=trace.tool_items,
                communication_trace=self._communication_trace_from_state(state, trace),
                repair_trace=self._repair_trace_from_state(state),
                thread_id=thread_id,
                interrupt=interrupt_payload,
            )

        outputs = dict(state.get("outputs", {}))
        failures = state.get("failures", {})
        if failures:
            failed_step_id = next(
                step.step_id for step in plan.steps if step.step_id in failures
            )
            failure = failures[failed_step_id]
            return ExecutionResult(
                status="failed",
                outputs=outputs,
                trace=trace.items,
                tool_trace=trace.tool_items,
                communication_trace=self._communication_trace_from_state(state, trace),
                repair_trace=self._repair_trace_from_state(state),
                error_type=failure["error_type"],
                error_message=failure["error_message"],
                thread_id=thread_id,
            )

        terminal_states = state.get("terminal_states", {})
        for step in plan.steps:
            terminal = terminal_states.get(step.step_id)
            if terminal is None:
                continue
            status = str(terminal["status"])
            return ExecutionResult(
                status=status,
                outputs=outputs,
                trace=trace.items,
                tool_trace=trace.tool_items,
                communication_trace=self._communication_trace_from_state(state, trace),
                repair_trace=self._repair_trace_from_state(state),
                error_type=terminal.get("error_type"),
                error_message=terminal.get("error_message"),
                thread_id=thread_id,
            )

        return ExecutionResult(
            status="success",
            outputs=outputs,
            trace=trace.items,
            tool_trace=trace.tool_items,
            communication_trace=self._communication_trace_from_state(state, trace),
            repair_trace=self._repair_trace_from_state(state),
            thread_id=thread_id,
        )

    def compile_plan(
        self,
        plan: ExecutionPlan,
        context: dict[str, Any],
        trace: TraceRecorder | None = None,
    ):
        """Compile an existing typed execution plan into an executable graph."""
        plan.validate_dag()
        recorder = trace or TraceRecorder()
        builder = StateGraph(LangGraphExecutionState)
        dependents: set[str] = set()
        steps_by_id = {step.step_id: step for step in plan.steps}
        audit_step_ids = {
            step.step_id for step in plan.steps if self._is_audit_step(step)
        }
        audit_gate_ids = {
            audit_step_id: f"{audit_step_id}__approved"
            for audit_step_id in audit_step_ids
            if any(
                audit_step_id in candidate.depends_on
                for candidate in plan.steps
            )
        }

        def is_ancestor(candidate_id: str, step_id: str) -> bool:
            pending = list(steps_by_id[step_id].depends_on)
            visited: set[str] = set()
            while pending:
                dependency_id = pending.pop()
                if dependency_id == candidate_id:
                    return True
                if dependency_id in visited:
                    continue
                visited.add(dependency_id)
                pending.extend(steps_by_id[dependency_id].depends_on)
            return False

        for step in plan.steps:
            builder.add_node(
                step.step_id,
                self._node_for_step(step, context, recorder, steps_by_id, plan),
            )
            dependents.update(step.depends_on)
        for gate_id in audit_gate_ids.values():
            builder.add_node(
                gate_id,
                lambda state: {"outputs": dict(state.get("outputs", {}))},
            )

        for step in plan.steps:
            gated_audits = [
                dependency
                for dependency in step.depends_on
                if dependency in audit_gate_ids
            ]
            effective_dependencies = [
                audit_gate_ids.get(dependency, dependency)
                for dependency in step.depends_on
                if not any(
                    dependency != audit_step_id
                    and is_ancestor(dependency, audit_step_id)
                    for audit_step_id in gated_audits
                )
            ]
            if not effective_dependencies:
                builder.add_edge(START, step.step_id)
            elif len(effective_dependencies) == 1:
                builder.add_edge(effective_dependencies[0], step.step_id)
            else:
                builder.add_edge(effective_dependencies, step.step_id)

        builder.add_node(
            "repair_execute",
            self._repair_node(plan, context, recorder, steps_by_id),
        )
        for step in plan.steps:
            if self._is_audit_step(step):
                gate_id = audit_gate_ids.get(step.step_id)
                path_map = {
                    "repair_execute": "repair_execute",
                    "end": END,
                }
                if gate_id is not None:
                    path_map[f"approved:{step.step_id}"] = gate_id
                builder.add_conditional_edges(
                    step.step_id,
                    self._route_after_audit_step(step.step_id, gate_id),
                    path_map,
                )
            elif step.step_id not in dependents:
                builder.add_edge(step.step_id, END)
        repair_path_map = {
            "repair_execute": "repair_execute",
            "end": END,
            **{
                f"approved:{audit_step_id}": gate_id
                for audit_step_id, gate_id in audit_gate_ids.items()
            },
        }
        builder.add_conditional_edges(
            "repair_execute",
            self._route_after_repair(audit_gate_ids),
            repair_path_map,
        )

        return builder.compile(
            checkpointer=self._checkpointer,
            name=f"competition_{plan.task_type}",
        )

    @staticmethod
    def _communication_trace_from_state(
        state: dict[str, Any], trace: TraceRecorder
    ) -> list[CommunicationTrace]:
        items = state.get("communication_trace") or []
        return _merge_communication_traces(
            [],
            [*items, *trace.communication_items],
        )

    @staticmethod
    def _repair_trace_from_state(state: dict[str, Any]) -> list[RepairTrace]:
        return _append_unique_repair_trace([], state.get("repair_trace") or [])

    @staticmethod
    def _is_audit_step(step: ExecutionStep) -> bool:
        return step.agent == "audit_agent" or (step.action or "").lower() in {
            "audit",
            "review_exam_paper",
        }

    @staticmethod
    def _route_after_audit_step(audit_step_id: str, gate_id: str | None):
        def route(state: LangGraphExecutionState) -> str:
            if state.get("terminal_states"):
                return "end"
            progress = state.get("repair_progress", {}).get(audit_step_id, {})
            if str(progress.get("status")) in {"planned", "running"}:
                return "repair_execute"
            if gate_id is not None:
                return f"approved:{audit_step_id}"
            return "end"

        return route

    @staticmethod
    def _route_after_repair(audit_gate_ids: dict[str, str]):
        def route(state: LangGraphExecutionState) -> str | list[str]:
            if state.get("terminal_states"):
                return "end"
            progress_items = state.get("repair_progress", {})
            if any(
                str(progress.get("status")) in {"planned", "running"}
                for progress in progress_items.values()
            ):
                return "repair_execute"
            approved_paths = [
                f"approved:{audit_step_id}"
                for audit_step_id, progress in progress_items.items()
                if (
                    audit_step_id in audit_gate_ids
                    and str(progress.get("status")) == "completed"
                )
            ]
            return approved_paths or "end"

        return route

    @staticmethod
    def _communication_update(step_id: str, trace: TraceRecorder) -> dict[str, Any]:
        return {
            "communication_trace": [
                item for item in trace.communication_items if item.step_id == step_id
            ]
        }

    @staticmethod
    def _restore_checkpoint_output(value: Any) -> Any:
        """Restore typed envelopes flattened during checkpoint replay."""
        payload_types = {
            "resolved_planning_route": ResolvedPlanningRoute,
            "evidence_pack": EvidencePack,
            "audit_result": AuditResult,
            "paper_blueprint": PaperBlueprint,
            "question_candidate_pool": QuestionCandidatePool,
            "exam_paper_draft": ExamPaperDraft,
        }
        if isinstance(value, AgentEnvelope):
            payload_type = payload_types.get(str(value.artifact_type))
            if payload_type is None or not isinstance(value.payload, dict):
                return value
            normalized = value.model_dump(mode="python")
            normalized["payload"] = payload_type.model_validate(value.payload)
            return AgentEnvelope[Any].model_validate(normalized)
        if not isinstance(value, dict):
            return value
        required = {
            "artifact_id",
            "artifact_type",
            "case_id",
            "trace_id",
            "request_id",
            "execution_id",
            "step_id",
            "producer",
            "task_type",
            "learner_id",
            "payload",
        }
        if not required.issubset(value):
            return value
        normalized = dict(value)
        payload_type = payload_types.get(str(value.get("artifact_type")))
        if payload_type is not None and isinstance(value.get("payload"), dict):
            normalized["payload"] = payload_type.model_validate(value["payload"])
        return AgentEnvelope[Any].model_validate(normalized)

    def _repair_node(
        self,
        plan: ExecutionPlan,
        root_context: dict[str, Any],
        trace: TraceRecorder,
        steps_by_id: dict[str, ExecutionStep],
    ):
        async def run_repair(
            state: LangGraphExecutionState,
            config: RunnableConfig,
        ) -> dict[str, Any]:
            progress_items = state.get("repair_progress", {})
            audit_step_id = next(
                (
                    step_id
                    for step_id, progress in progress_items.items()
                    if str(progress.get("status")) in {"planned", "running"}
                ),
                None,
            )
            if audit_step_id is None:
                return {}
            plan_data = state.get("repair_plans", {}).get(audit_step_id)
            if not isinstance(plan_data, dict):
                return {
                    "terminal_states": {
                        audit_step_id: {
                            "status": "waiting_human_review",
                            "error_type": "RepairPlanUnavailable",
                            "error_message": "repair plan was unavailable after checkpoint recovery",
                        }
                    }
                }
            repair_plan = LocalRepairPlan.model_validate(plan_data)
            progress = dict(progress_items[audit_step_id])
            completed = list(progress.get("completed_step_ids") or [])
            action = next(
                (item for item in repair_plan.actions if item.step_id not in completed),
                None,
            )
            if action is None:
                progress["status"] = "completed"
                return {"repair_progress": {audit_step_id: progress}}

            outputs = {
                key: self._restore_checkpoint_output(value)
                for key, value in dict(state.get("outputs", {})).items()
            }
            original_audit = outputs.get(audit_step_id)
            rerun_step = steps_by_id[action.step_id]
            repair_context = {
                **root_context,
                "audit_feedback": original_audit,
                "repair_instruction": action.model_dump(mode="json"),
                "previous_step_output": outputs.get(action.step_id),
            }
            record_item = next(
                (
                    item
                    for item in state.get("repair_trace", [])
                    if (
                        item.repair_id
                        if isinstance(item, RepairTrace)
                        else item.get("repair_id")
                    )
                    == repair_plan.repair_id
                ),
                None,
            )
            if record_item is None:
                progress["status"] = "stopped"
                emit_runtime_event(
                    "repair_stopped",
                    repair_id=repair_plan.repair_id,
                    trigger_step_id=audit_step_id,
                    status="needs_human_review",
                )
                return {
                    "repair_progress": {audit_step_id: progress},
                    "terminal_states": {
                        audit_step_id: {
                            "status": "waiting_human_review",
                            "error_type": "RepairTraceUnavailable",
                            "error_message": "repair trace was unavailable after checkpoint recovery",
                        }
                    },
                }
            record = RepairTrace.model_validate(record_item)
            if action.step_id == audit_step_id:
                emit_runtime_event(
                    "repair_reaudit_started",
                    repair_id=repair_plan.repair_id,
                    trigger_step_id=audit_step_id,
                    status="running",
                )
            else:
                emit_runtime_event(
                    "repair_step_started",
                    repair_id=repair_plan.repair_id,
                    step_id=action.step_id,
                    status="running",
                )
            try:
                result = await self._run_step(rerun_step, repair_context, outputs, trace)
                clarification = (
                    self._clarification_payload(result, rerun_step)
                    if root_context.get("interruptible")
                    else None
                )
                while clarification is not None:
                    root_context.setdefault("_interrupted_dependency_outputs", {})[
                        action.step_id
                    ] = dict(outputs)
                    with set_config_context(config) as interrupt_context:
                        resume_value = interrupt_context.run(interrupt, clarification)
                    self._apply_resume_value(
                        root_context,
                        resume_value,
                        requested_scope=clarification.get("requested_scope"),
                    )
                    repair_context = {
                        **root_context,
                        "audit_feedback": original_audit,
                        "repair_instruction": action.model_dump(mode="json"),
                        "previous_step_output": outputs.get(action.step_id),
                    }
                    emit_runtime_event(
                        "graph_resumed",
                        thread_id=config.get("configurable", {}).get("thread_id"),
                        step_id=action.step_id,
                    )
                    result = await self._run_step(
                        rerun_step, repair_context, outputs, trace
                    )
                    clarification = self._clarification_payload(result, rerun_step)
            except GraphInterrupt:
                raise
            except Exception as exc:
                progress["status"] = "stopped"
                record.status = "stopped"
                record.final_audit_decision = "failed"
                emit_runtime_event(
                    "repair_stopped",
                    repair_id=repair_plan.repair_id,
                    trigger_step_id=audit_step_id,
                    status="failed",
                )
                return {
                    "repair_progress": {audit_step_id: progress},
                    "repair_trace": [record],
                    "failures": {
                        action.step_id: {
                            "error_type": type(exc).__name__,
                            "error_message": f"repair step {action.step_id} failed: {exc}",
                        }
                    },
                    **self._communication_update(action.step_id, trace),
                }

            completed.append(action.step_id)
            progress["completed_step_ids"] = completed
            progress["status"] = "running"
            record.status = "running"
            if action.step_id != audit_step_id:
                emit_runtime_event(
                    "repair_step_completed",
                    repair_id=repair_plan.repair_id,
                    step_id=action.step_id,
                    status="success",
                )
                return {
                    "outputs": {action.step_id: result},
                    "repair_progress": {audit_step_id: progress},
                    "repair_trace": [record],
                    **self._communication_update(action.step_id, trace),
                }

            decision = getattr(getattr(result, "payload", None), "decision", None)
            record.final_audit_decision = decision
            record.after_digest = self.repair_controller._output_digest({
                item.step_id: outputs.get(item.step_id)
                for item in repair_plan.actions
                if item.step_id != audit_step_id
            })
            if decision == "pass":
                record.status = "completed"
                progress["status"] = "completed"
                root_context.setdefault(
                    "_approved_dependency_outputs", {}
                ).update({**outputs, audit_step_id: result})
                emit_runtime_event(
                    "repair_completed",
                    repair_id=repair_plan.repair_id,
                    trigger_step_id=audit_step_id,
                    status="pass",
                )
                return {
                    "outputs": {audit_step_id: result},
                    "repair_progress": {audit_step_id: progress},
                    "repair_trace": [record],
                    **self._communication_update(audit_step_id, trace),
                }

            record.status = "stopped"
            progress["status"] = "stopped"
            status = "failed" if decision == "reject" else "waiting_human_review"
            emit_runtime_event(
                "repair_stopped",
                repair_id=repair_plan.repair_id,
                trigger_step_id=audit_step_id,
                status=decision or "needs_human_review",
            )
            return {
                "outputs": {audit_step_id: result},
                "repair_progress": {audit_step_id: progress},
                "repair_trace": [record],
                "terminal_states": {
                    audit_step_id: {
                        "status": status,
                        "error_type": "AuditRevisionNeedsHumanReview",
                        "error_message": "audit still requires review after the bounded repair",
                    }
                },
                **self._communication_update(audit_step_id, trace),
            }

        return run_repair

    def _node_for_step(
        self,
        step: ExecutionStep,
        root_context: dict[str, Any],
        trace: TraceRecorder,
        steps_by_id: dict[str, ExecutionStep],
        plan: ExecutionPlan,
    ):
        async def run_node(
            state: LangGraphExecutionState,
            config: RunnableConfig,
        ) -> dict[str, Any]:
            def with_communication(update: dict[str, Any]) -> dict[str, Any]:
                return {
                    **update,
                    **self._communication_update(step.step_id, trace),
                }

            failures = state.get("failures", {})
            blocked = state.get("blocked_steps", {})
            failed_dependencies = [
                dependency
                for dependency in step.depends_on
                if dependency in failures or dependency in blocked
            ]
            if failed_dependencies:
                return {
                    "blocked_steps": {
                        step.step_id: "blocked by failed dependencies: "
                        + ", ".join(failed_dependencies)
                    }
                }
            if state.get("terminal_states"):
                return {
                    "blocked_steps": {
                        step.step_id: "blocked by terminal audit decision"
                    }
                }

            interrupted_dependencies = root_context.get(
                "_interrupted_dependency_outputs", {}
            ).get(step.step_id, {})
            approved_dependencies = root_context.get(
                "_approved_dependency_outputs", {}
            )
            checkpoint_outputs = {
                **dict(interrupted_dependencies),
                **dict(approved_dependencies),
                **dict(state.get("outputs", {})),
            }
            outputs = {
                key: self._restore_checkpoint_output(value)
                for key, value in checkpoint_outputs.items()
            }
            try:
                result = await self._run_step(
                    step, root_context, outputs, trace
                )
            except Exception as exc:
                return with_communication({
                    "failures": {
                        step.step_id: {
                            "error_type": type(exc).__name__,
                            "error_message": (
                                f"步骤 {step.step_id}（{step.agent}）失败："
                                f"{type(exc).__name__}: {exc or '未提供错误详情'}"
                            ),
                        }
                    }
                })

            clarification = (
                self._clarification_payload(result, step)
                if root_context.get("interruptible")
                else None
            )
            while clarification is not None:
                root_context.setdefault("_interrupted_dependency_outputs", {})[
                    step.step_id
                ] = dict(outputs)
                with set_config_context(config) as interrupt_context:
                    resume_value = interrupt_context.run(interrupt, clarification)
                self._apply_resume_value(
                    root_context,
                    resume_value,
                    requested_scope=clarification.get("requested_scope"),
                    interrupt_type=clarification.get("interrupt_type"),
                )
                emit_runtime_event(
                    "graph_resumed",
                    thread_id=config.get("configurable", {}).get("thread_id"),
                    step_id=step.step_id,
                )
                try:
                    outputs = await self._refresh_resume_dependencies(
                        step,
                        root_context,
                        outputs,
                        trace,
                        steps_by_id,
                        clarification,
                    )
                    root_context.setdefault(
                        "_interrupted_dependency_outputs", {}
                    )[step.step_id] = dict(outputs)
                    result = await self._run_step(
                        step, root_context, outputs, trace
                    )
                except Exception as exc:
                    return with_communication({
                        "failures": {
                            step.step_id: {
                                "error_type": type(exc).__name__,
                                "error_message": (
                                    f"步骤 {step.step_id}（{step.agent}）恢复后失败："
                                    f"{type(exc).__name__}: {exc or '未提供错误详情'}"
                                ),
                            }
                        }
                    })
                clarification = self._clarification_payload(result, step)

            preserved_dependencies = root_context.get(
                "_interrupted_dependency_outputs", {}
            ).pop(step.step_id, {})
            outputs[step.step_id] = result
            node_outputs = {**preserved_dependencies, step.step_id: result}
            decision = getattr(getattr(result, "payload", None), "decision", None)
            if decision == "reject":
                return with_communication({
                    "outputs": node_outputs,
                    "terminal_states": {
                        step.step_id: {
                            "status": "failed",
                            "error_type": None,
                            "error_message": None,
                        }
                    },
                })
            if decision == "needs_human_review":
                return with_communication({
                    "outputs": node_outputs,
                    "terminal_states": {
                        step.step_id: {
                            "status": "waiting_human_review",
                            "error_type": None,
                            "error_message": None,
                        }
                    },
                })
            if decision != "revise":
                if self._is_audit_step(step) and decision == "pass":
                    root_context.setdefault(
                        "_approved_dependency_outputs", {}
                    ).update(node_outputs)
                return with_communication({"outputs": node_outputs})

            emit_runtime_event(
                "audit_revision_started",
                audit_step_id=step.step_id,
                status="running",
            )
            payload = getattr(result, "payload", None)
            findings = list(getattr(payload, "findings", []) or [])
            structured_findings = list(
                getattr(payload, "structured_findings", []) or []
            )
            repair_plan = self.repair_controller.plan_repair(
                plan=plan,
                audit_step_id=step.step_id,
                audit_findings=findings,
                structured_findings=structured_findings,
                outputs={**outputs, step.step_id: result},
            )
            rerun_step_ids = [action.step_id for action in repair_plan.actions]
            record = RepairTrace(
                repair_id=repair_plan.repair_id,
                trigger_step_id=step.step_id,
                issue_types=[issue.issue_type for issue in repair_plan.issues],
                issue_ids=[issue.issue_id for issue in repair_plan.issues],
                location_labels=list(dict.fromkeys(
                    location.display_label
                    for issue in repair_plan.issues
                    for location in issue.locations
                ))[:8],
                rerun_step_ids=rerun_step_ids,
                preserved_step_ids=sorted(
                    set(outputs).union(node_outputs) - set(rerun_step_ids)
                ),
                status=(
                    "planned"
                    if repair_plan.status == "planned"
                    else "stopped"
                ),
                final_audit_decision=(
                    None
                    if repair_plan.status == "planned"
                    else "needs_human_review"
                ),
                before_digest=self.repair_controller._output_digest({
                    action.step_id: outputs.get(action.step_id)
                    for action in repair_plan.actions
                    if action.step_id != step.step_id
                }),
            )
            emit_runtime_event(
                "repair_planned",
                repair_id=record.repair_id,
                trigger_step_id=step.step_id,
                issue_types=record.issue_types,
                issue_ids=record.issue_ids,
                location_labels=record.location_labels,
                rerun_step_ids=record.rerun_step_ids,
                preserved_step_ids=record.preserved_step_ids,
                status=repair_plan.status,
            )
            if repair_plan.status == "needs_human_review":
                emit_runtime_event(
                    "repair_stopped",
                    repair_id=record.repair_id,
                    trigger_step_id=step.step_id,
                    status="needs_human_review",
                )
                emit_runtime_event(
                    "audit_revision_completed",
                    audit_step_id=step.step_id,
                    status="needs_human_review",
                )
                return with_communication({
                    "outputs": node_outputs,
                    "repair_trace": [record],
                    "terminal_states": {
                        step.step_id: {
                            "status": "waiting_human_review",
                            "error_type": "RepairPlanNeedsHumanReview",
                            "error_message": (
                                "audit findings could not be safely repaired: "
                                + "; ".join(str(item) for item in findings)
                            ),
                        }
                    },
                })
            return with_communication({
                "outputs": node_outputs,
                "repair_plans": {
                    step.step_id: repair_plan.model_dump(mode="json")
                },
                "repair_progress": {
                    step.step_id: {
                        "repair_id": repair_plan.repair_id,
                        "status": "planned",
                        "completed_step_ids": [],
                    }
                },
                "repair_trace": [record],
            })

        return run_node

    async def _refresh_resume_dependencies(
        self,
        step: ExecutionStep,
        root_context: dict[str, Any],
        outputs: dict[str, Any],
        trace: TraceRecorder,
        steps_by_id: dict[str, ExecutionStep],
        clarification: dict[str, Any],
    ) -> dict[str, Any]:
        """Refresh upstream decisions that depend on newly supplied user intent."""
        refreshed = dict(outputs)
        for dependency_id in step.depends_on:
            dependency_step = steps_by_id.get(dependency_id)
            if (
                dependency_step is None
                or dependency_step.agent not in self._RESUME_SENSITIVE_AGENTS
                or not (
                    self._clarification_comes_from_dependency(
                        clarification,
                        refreshed.get(dependency_id),
                    )
                    or self._profile_clarification_affects_route(clarification)
                )
            ):
                continue
            refreshed[dependency_id] = await self._run_step(
                dependency_step,
                root_context,
                refreshed,
                trace,
            )
        return refreshed

    @staticmethod
    def _clarification_comes_from_dependency(
        clarification: dict[str, Any],
        dependency_output: Any,
    ) -> bool:
        payload = getattr(dependency_output, "payload", None)
        if payload is None:
            return False
        dependency_questions = {
            str(item).strip()
            for item in (getattr(payload, "unknowns_to_confirm", None) or [])
            if str(item).strip()
        }
        textbook_resolution = getattr(payload, "textbook_route", None)
        if isinstance(textbook_resolution, dict):
            textbook_questions = textbook_resolution.get(
                "clarification_questions"
            ) or []
        else:
            textbook_questions = (
                getattr(textbook_resolution, "clarification_questions", None)
                or []
            )
        dependency_questions.update(
            str(item).strip()
            for item in textbook_questions
            if str(item).strip()
        )
        current_questions = {
            str(item).strip()
            for item in (clarification.get("questions") or [])
            if str(item).strip()
        }
        return bool(dependency_questions & current_questions)

    @staticmethod
    def _profile_clarification_affects_route(
        clarification: dict[str, Any],
    ) -> bool:
        profile_fields = {
            str(item).strip()
            for item in (clarification.get("profile_fields") or [])
            if str(item).strip()
        }
        return bool(
            profile_fields.intersection(
                {"learning_goal", "learning_background"}
            )
        )

    @staticmethod
    def _clarification_payload(result: Any, step: ExecutionStep) -> dict[str, Any] | None:
        payload = getattr(result, "payload", None)
        clarification_source = getattr(payload, "governance", None) or payload
        if clarification_source is None or not getattr(
            clarification_source, "requires_clarification", False
        ):
            return None
        questions = list(
            getattr(clarification_source, "clarification_questions", []) or []
        )
        # Keep the business scope selected by the agent separate from a
        # prerequisite scope.  These values used to be referenced as local
        # variables without being derived from the clarification result,
        # which made the first prerequisite interrupt fail with NameError and
        # also caused the resume path to lose the original daily/short-term
        # request.  The agent is authoritative here; no keyword inference is
        # performed by the orchestrator.
        original_scope = getattr(clarification_source, "plan_scope", None) or getattr(
            step, "plan_scope", None
        )
        requested_scope = getattr(clarification_source, "requested_scope", None)
        prerequisite_scope = getattr(
            clarification_source, "prerequisite_scope", None
        )
        if not prerequisite_scope and getattr(
            clarification_source, "interrupt_type", None
        ) == "planning_prerequisite":
            prerequisite_scope = requested_scope
        if (
            not prerequisite_scope
            and original_scope in {"long_term", "short_term", "daily_task"}
            and requested_scope in {"long_term", "short_term"}
            and requested_scope != original_scope
        ):
            prerequisite_scope = requested_scope
        return {
            "step_id": step.step_id,
            "agent": step.agent,
            "reason": getattr(clarification_source, "reason", None)
            or getattr(clarification_source, "clarification_reason", None)
            or "需要用户补充信息后继续。",
            "questions": questions,
            # For a planning prerequisite, requested_scope is the missing
            # parent that must be created.  The original child layer remains
            # available separately so resume can return to it.
            "requested_scope": prerequisite_scope or requested_scope or original_scope,
            "original_scope": original_scope,
            "prerequisite_scope": prerequisite_scope,
            "profile_fields": list(
                getattr(clarification_source, "clarification_fields", []) or []
            ),
            "interrupt_type": getattr(
                clarification_source, "interrupt_type", None
            ),
        }

    @staticmethod
    def _apply_resume_value(
        root_context: dict[str, Any],
        resume_value: Any,
        *,
        requested_scope: str | None = None,
        interrupt_type: str | None = None,
    ) -> None:
        value = resume_value if isinstance(resume_value, dict) else {"answer": resume_value}
        if interrupt_type == "memory_conflict":
            answer = str(value.get("answer") or "").strip()
            root_context["memory_conflict_answer"] = answer
            root_context["latest_resume_answer"] = answer
            if answer:
                messages = root_context.setdefault("messages", [])
                if not any(
                    item.get("role") == "user" and item.get("content") == answer
                    for item in messages
                    if isinstance(item, dict)
                ):
                    messages.append({"role": "user", "content": answer})
            return
        profile_updates = value.get("profile_updates")
        if isinstance(profile_updates, dict):
            profile = root_context.setdefault("user_profile", {})
            profile.update(
                {
                    str(key): item
                    for key, item in profile_updates.items()
                    if str(key).strip() and item not in (None, "")
                }
            )

        # A profile-completion answer is evidence for the learner profile, not
        # a request to mutate an existing plan.  Keep it on the conversation
        # and return to the interrupted planning scope.  In particular, do not
        # fall through to the generic free-form branch below: that branch
        # creates ``plan_change_context`` and makes Diagnosis treat answers
        # such as “每周学习5天，每天2小时” as a replanning request.  The
        # semantic planner must only assess an actual change after the profile
        # gate has completed.
        if interrupt_type == "profile_completion":
            answer = str(value.get("answer") or "").strip()
            if answer:
                root_context["latest_resume_answer"] = answer
                messages = root_context.setdefault("messages", [])
                if not any(
                    item.get("role") == "user" and item.get("content") == answer
                    for item in messages
                    if isinstance(item, dict)
                ):
                    messages.append({"role": "user", "content": answer})
            if requested_scope in {"long_term", "short_term", "daily_task"}:
                root_context["plan_scope"] = requested_scope
                root_context["plan_scope_hint"] = requested_scope
                root_context["continued_plan_scope"] = requested_scope
            return

        if interrupt_type == "planning_prerequisite":
            # The user is answering the prerequisite question raised by the
            # already-selected child task (for example “可以” to “是否先建立
            # 短期计划？”).  It is not a new plan-change request and must not
            # be interpreted as a new scope by the generic resume branch.
            answer = str(value.get("answer") or "").strip()
            if answer:
                root_context["latest_resume_answer"] = answer
                messages = root_context.setdefault("messages", [])
                if not any(
                    item.get("role") == "user" and item.get("content") == answer
                    for item in messages
                    if isinstance(item, dict)
                ):
                    messages.append({"role": "user", "content": answer})
            original_scope = value.get("plan_scope") or root_context.get(
                "requested_plan_scope"
            )
            if original_scope in {"long_term", "short_term", "daily_task"}:
                root_context["plan_scope"] = original_scope
                root_context["continued_plan_scope"] = original_scope
                root_context["plan_scope_hint"] = original_scope
            return

        selected_scope = value.get("plan_scope")
        if (
            selected_scope not in {"long_term", "short_term", "daily_task"}
            and requested_scope in {"long_term", "short_term", "daily_task"}
        ):
            # This scope was requested by the upstream agent in the interrupt
            # payload. It is not inferred from the user's words.
            selected_scope = requested_scope
        if selected_scope in {"long_term", "short_term", "daily_task"} and (
            value.get("clarification_kind") == "plan_scope"
            or interrupt_type in {"plan_scope_resolution", "planning_prerequisite"}
            or selected_scope != root_context.get("plan_scope")
        ):
            root_context["plan_scope"] = selected_scope
            root_context["plan_scope_hint"] = selected_scope
            root_context["continued_plan_scope"] = selected_scope
        is_scope_clarification_answer = (
            value.get("clarification_kind") == "plan_scope"
            or selected_scope in {
                "long_term",
                "short_term",
                "daily_task",
            }
        ) and (
            requested_scope == "unspecified"
            or root_context.get("plan_scope") == "unspecified"
        )
        is_scope_selection = selected_scope in {
            "long_term",
            "short_term",
            "daily_task",
        } and is_scope_clarification_answer
        if is_scope_clarification_answer:
            answer = str(value.get("answer") or "").strip()
            if is_scope_selection:
                root_context["plan_scope"] = selected_scope
                root_context["plan_scope_hint"] = selected_scope
                root_context["continued_plan_scope"] = selected_scope
                # Selecting a planning layer is not a request to mutate that
                # layer. Diagnosis decides create/reuse from the actual user
                # facts and existing plan state.
            if answer:
                root_context["latest_resume_answer"] = answer
                messages = root_context.setdefault("messages", [])
                if not any(
                    item.get("role") == "user" and item.get("content") == answer
                    for item in messages
                    if isinstance(item, dict)
                ):
                    messages.append({"role": "user", "content": answer})
            return
        if interrupt_type == "route_resolution":
            # Route/profile answers are facts for the resolver, not plan
            # mutations. Keep the established goal and feed the answer back
            # through conversation context without creating a replan contract.
            answer = str(value.get("answer") or "").strip()
            if answer:
                root_context["latest_resume_answer"] = answer
                messages = root_context.setdefault("messages", [])
                if not any(
                    item.get("role") == "user" and item.get("content") == answer
                    for item in messages
                    if isinstance(item, dict)
                ):
                    messages.append({"role": "user", "content": answer})
                original = str(
                    root_context.get("original_user_request")
                    or root_context.get("user_request")
                    or ""
                ).strip()
                root_context["user_request"] = "\n".join(
                    item for item in (original, f"用户补充信息：{answer}") if item
                )
            return
        change = value.get("plan_change_context")
        if not isinstance(change, dict):
            answer = str(value.get("answer") or "").strip()
            change = {
                "original_request": root_context.get("user_request", ""),
                # Leave target_layers empty when the answer is natural
                # language. Diagnosis owns the semantic interpretation; an
                # empty list is materially different from claiming the user
                # selected "unspecified" as a layer.
                "target_layers": [],
                "change_details": answer,
            }
        details = str(change.get("change_details") or value.get("answer") or "").strip()
        if details:
            root_context["latest_resume_answer"] = details
            # Backward-compatible direct orchestration callers may provide a
            # goal answer without any existing plan context. In a real
            # replanning continuation the answer remains a change fact for
            # Diagnosis and never replaces the established goal.
            if (
                not root_context.get("current_long_term_plan")
                and not root_context.get("current_short_term_plan")
                and not root_context.get("plan_change_context")
                and not change.get("target_layers")
            ):
                root_context["learning_goal"] = details
        original_request = str(
            change.get("original_request")
            or root_context.get("original_user_request")
            or root_context.get("user_request")
            or ""
        ).strip()
        parts = [original_request]
        if details:
            parts.append(f"用户补充的具体变化：{details}")
        for label, key in (
            ("可用时间", "available_time"),
            ("希望保留", "keep_items"),
            ("希望放弃", "drop_items"),
            ("期望结果", "expected_outcome"),
        ):
            item = change.get(key)
            if item:
                parts.append(f"{label}：{item}")
        root_context["user_request"] = "\n".join(dict.fromkeys(parts))
        root_context["plan_change_context"] = change
        # Only an explicit, validated scope changes the current planning
        # layer. Free-form answers stay in plan_change_context for Diagnosis.
        scope = value.get("plan_scope")
        if scope in {"long_term", "short_term", "daily_task"}:
            root_context["plan_scope"] = scope
        root_context["explicit_long_term_change"] = bool(
            "long_term" in (change.get("target_layers") or [])
        )
        root_context["explicit_short_term_change"] = bool(
            "short_term" in (change.get("target_layers") or [])
        )
        if details:
            messages = root_context.setdefault("messages", [])
            if not any(
                item.get("role") == "user" and item.get("content") == details
                for item in messages
                if isinstance(item, dict)
            ):
                messages.append({"role": "user", "content": details})

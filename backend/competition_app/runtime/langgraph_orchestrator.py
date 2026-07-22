from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph._internal._runnable import set_config_context
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.default_route import ResolvedPlanningRoute
from competition_app.contracts.execution import ExecutionPlan, ExecutionStep
from competition_app.contracts.knowledge import EvidencePack
from competition_app.runtime.event_stream import emit_runtime_event
from competition_app.runtime.orchestrator import ExecutionResult, Orchestrator
from competition_app.runtime.trace import TraceRecorder


def _merge_mappings(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Merge independent node updates; a controlled revision may replace one output."""
    return {**left, **right}


class LangGraphExecutionState(TypedDict):
    outputs: Annotated[dict[str, Any], _merge_mappings]
    failures: Annotated[dict[str, dict[str, str]], _merge_mappings]
    blocked_steps: Annotated[dict[str, str], _merge_mappings]
    terminal_states: Annotated[dict[str, dict[str, str | None]], _merge_mappings]


@dataclass
class _InterruptedSession:
    graph: Any
    config: dict[str, Any]
    plan: ExecutionPlan
    trace: TraceRecorder


class LangGraphOrchestrator(Orchestrator):
    """Execute the existing dynamic ExecutionPlan through a LangGraph StateGraph.

    Checkpoints are kept in memory. They survive browser refreshes and short network
    disconnects while this service process remains alive, but intentionally do not
    survive a server restart.
    """

    engine_name = "langgraph"
    _RESUME_SENSITIVE_AGENTS = frozenset({"default_route_resolver"})

    def __init__(self, agent_registry, tool_registry=None) -> None:
        super().__init__(agent_registry, tool_registry)
        self._checkpointer = InMemorySaver(
            serde=JsonPlusSerializer(
                pickle_fallback=True,
                allowed_msgpack_modules=True,
            )
        )
        self._interrupted_sessions: dict[str, _InterruptedSession] = {}

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
        )
        initial_state: LangGraphExecutionState = {
            "outputs": {},
            "failures": {},
            "blocked_steps": {},
            "terminal_states": {},
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

    async def resume(self, thread_id: str, resume_value: Any) -> ExecutionResult:
        session = self._interrupted_sessions.get(thread_id)
        if session is None:
            raise KeyError(f"LangGraph thread {thread_id} is not waiting for input")
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
                error_type=terminal.get("error_type"),
                error_message=terminal.get("error_message"),
                thread_id=thread_id,
            )

        return ExecutionResult(
            status="success",
            outputs=outputs,
            trace=trace.items,
            tool_trace=trace.tool_items,
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

        for step in plan.steps:
            builder.add_node(
                step.step_id,
                self._node_for_step(step, context, recorder, steps_by_id),
            )
            dependents.update(step.depends_on)

        for step in plan.steps:
            if not step.depends_on:
                builder.add_edge(START, step.step_id)
            elif len(step.depends_on) == 1:
                builder.add_edge(step.depends_on[0], step.step_id)
            else:
                builder.add_edge(step.depends_on, step.step_id)

        leaf_steps = [
            step.step_id for step in plan.steps if step.step_id not in dependents
        ]
        for step_id in leaf_steps:
            builder.add_edge(step_id, END)

        return builder.compile(
            checkpointer=self._checkpointer,
            name=f"competition_{plan.task_type}",
        )

    @staticmethod
    def _restore_checkpoint_output(value: Any) -> Any:
        """Restore typed envelopes flattened during checkpoint replay."""
        if isinstance(value, AgentEnvelope) or not isinstance(value, dict):
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
        payload_types = {
            "resolved_planning_route": ResolvedPlanningRoute,
            "evidence_pack": EvidencePack,
        }
        normalized = dict(value)
        payload_type = payload_types.get(str(value.get("artifact_type")))
        if payload_type is not None and isinstance(value.get("payload"), dict):
            normalized["payload"] = payload_type.model_validate(value["payload"])
        return AgentEnvelope[Any].model_validate(normalized)

    def _node_for_step(
        self,
        step: ExecutionStep,
        root_context: dict[str, Any],
        trace: TraceRecorder,
        steps_by_id: dict[str, ExecutionStep],
    ):
        async def run_node(
            state: LangGraphExecutionState,
            config: RunnableConfig,
        ) -> dict[str, Any]:
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
            checkpoint_outputs = {
                **dict(interrupted_dependencies),
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
                return {
                    "failures": {
                        step.step_id: {
                            "error_type": type(exc).__name__,
                            "error_message": (
                                f"步骤 {step.step_id}（{step.agent}）失败："
                                f"{type(exc).__name__}: {exc or '未提供错误详情'}"
                            ),
                        }
                    }
                }

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
                self._apply_resume_value(root_context, resume_value)
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
                    return {
                        "failures": {
                            step.step_id: {
                                "error_type": type(exc).__name__,
                                "error_message": (
                                    f"步骤 {step.step_id}（{step.agent}）恢复后失败："
                                    f"{type(exc).__name__}: {exc or '未提供错误详情'}"
                                ),
                            }
                        }
                    }
                clarification = self._clarification_payload(result, step)

            preserved_dependencies = root_context.get(
                "_interrupted_dependency_outputs", {}
            ).pop(step.step_id, {})
            outputs[step.step_id] = result
            node_outputs = {**preserved_dependencies, step.step_id: result}
            decision = getattr(getattr(result, "payload", None), "decision", None)
            if decision == "reject":
                return {
                    "outputs": node_outputs,
                    "terminal_states": {
                        step.step_id: {
                            "status": "failed",
                            "error_type": None,
                            "error_message": None,
                        }
                    },
                }
            if decision == "needs_human_review":
                return {
                    "outputs": node_outputs,
                    "terminal_states": {
                        step.step_id: {
                            "status": "waiting_human_review",
                            "error_type": None,
                            "error_message": None,
                        }
                    },
                }
            if decision != "revise":
                return {"outputs": node_outputs}

            emit_runtime_event(
                "audit_revision_started",
                audit_step_id=step.step_id,
                findings=getattr(getattr(result, "payload", None), "findings", []),
            )
            try:
                revised = await self._revise_once(
                    step.step_id, step, root_context, outputs, trace
                )
            except Exception as exc:
                emit_runtime_event(
                    "audit_revision_failed",
                    audit_step_id=step.step_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                return {
                    "outputs": {step.step_id: result},
                    "failures": {
                        step.step_id: {
                            "error_type": type(exc).__name__,
                            "error_message": f"audit revision failed: {exc}",
                        }
                    },
                }
            if revised is not None:
                emit_runtime_event(
                    "audit_revision_completed",
                    audit_step_id=step.step_id,
                    status="pass",
                )
                return {"outputs": revised}

            emit_runtime_event(
                "audit_revision_completed",
                audit_step_id=step.step_id,
                status="needs_human_review",
            )
            revised_audit = outputs[step.step_id]
            revised_decision = getattr(
                getattr(revised_audit, "payload", None), "decision", None
            )
            return {
                "outputs": {step.step_id: revised_audit},
                "terminal_states": {
                    step.step_id: {
                        "status": (
                            "failed"
                            if revised_decision == "reject"
                            else "waiting_human_review"
                        ),
                        "error_type": "AuditRevisionNeedsHumanReview",
                        "error_message": (
                            "audit requested revision but revised output still requires review"
                        ),
                    }
                },
            }

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
                or not self._clarification_comes_from_dependency(
                    clarification,
                    refreshed.get(dependency_id),
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
        current_questions = {
            str(item).strip()
            for item in (clarification.get("questions") or [])
            if str(item).strip()
        }
        return bool(dependency_questions & current_questions)

    @staticmethod
    def _clarification_payload(result: Any, step: ExecutionStep) -> dict[str, Any] | None:
        payload = getattr(result, "payload", None)
        if payload is None or not getattr(payload, "requires_clarification", False):
            return None
        questions = list(getattr(payload, "clarification_questions", []) or [])
        return {
            "step_id": step.step_id,
            "agent": step.agent,
            "reason": getattr(payload, "reason", None)
            or getattr(payload, "clarification_reason", None)
            or "需要用户补充信息后继续。",
            "questions": questions,
            "requested_scope": getattr(payload, "requested_scope", None)
            or getattr(payload, "plan_scope", None),
            "profile_fields": list(getattr(payload, "clarification_fields", []) or []),
            "interrupt_type": getattr(payload, "interrupt_type", None),
        }

    @staticmethod
    def _apply_resume_value(root_context: dict[str, Any], resume_value: Any) -> None:
        value = resume_value if isinstance(resume_value, dict) else {"answer": resume_value}
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
        change = value.get("plan_change_context")
        if not isinstance(change, dict):
            answer = str(value.get("answer") or "").strip()
            change = {
                "original_request": root_context.get("user_request", ""),
                "target_layers": [value.get("plan_scope") or "long_term"],
                "change_details": answer,
            }
        details = str(change.get("change_details") or value.get("answer") or "").strip()
        if details:
            root_context["latest_resume_answer"] = details
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
        scope = value.get("plan_scope") or next(
            iter(change.get("target_layers") or []), root_context.get("plan_scope")
        )
        if scope:
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

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from competition_app.agents.planner import PlannerAgent, PlannerDecision
from competition_app.contracts.base import AgentEnvelope, WritebackIntent
from competition_app.contracts.execution import ExecutionPlan
from competition_app.contracts.resource import (
    AuditResult,
    QuestionConsumptionDecision,
    ResourceDraft,
    ResourceVersion,
)
from competition_app.contracts.review import ReviewResourceBinding, ReviewSchedule, ReviewTask
from competition_app.contracts.workshop import UiAction
from competition_app.runtime.orchestrator import Orchestrator
from competition_app.runtime.snapshot import SnapshotExporter
from competition_app.runtime.model_trace import ModelCallTrace, ModelTraceRecorder
from competition_app.runtime.event_stream import emit_runtime_event
from competition_app.runtime.data_permissions import AgentDataPermissionGateway
from competition_app.repositories.learning_plan import (
    InMemoryLearningPlanRepository,
    LearningPlanRepository,
)
from competition_app.repositories.runtime import (
    ConversationRepository,
    InMemoryConversationRepository,
    InMemoryRunStateRepository,
    RunStateRepository,
)
from competition_app.services.writeback import WritebackExecutor
from competition_app.services.review import ReviewService
from competition_app.services.plan_scope import (
    infer_continued_plan_scope,
    infer_plan_scope,
)
from competition_app.services.learning_monitoring import LearningMonitoringService
from competition_app.application.workflow_presentation import workflow_result_to_markdown


class PlanChangeContext(BaseModel):
    original_request: str = Field(min_length=1)
    target_layers: list[Literal["long_term", "short_term", "daily_task"]] = Field(min_length=1)
    change_details: str = Field(min_length=1)
    available_time: str | None = None
    keep_items: str | None = None
    drop_items: str | None = None
    expected_outcome: str | None = None


class ReviewCardRequest(BaseModel):
    thread_id: str | None = Field(default=None, min_length=8, max_length=128)
    conversation_id: str | None = Field(default=None, min_length=8, max_length=128)
    learner_id: str
    user_request: str = Field(min_length=1)
    available_minutes: int = Field(default=15, gt=0, le=24 * 60)
    messages: list[dict[str, str]] = Field(default_factory=list)
    user_profile: dict[str, Any] = Field(default_factory=dict)
    learning_profile: dict[str, Any] = Field(default_factory=dict)
    system_data: dict[str, Any] = Field(default_factory=dict)
    user_knowledge_state: list[dict[str, Any]] = Field(default_factory=list)
    question_attempt: list[dict[str, Any]] = Field(default_factory=list)
    question_learning_stats: list[dict[str, Any]] = Field(default_factory=list)
    long_term_plan: dict[str, Any] = Field(default_factory=dict)
    short_term_plan: dict[str, Any] = Field(default_factory=dict)
    learning_task: dict[str, Any] = Field(default_factory=dict)
    exam_constraints: dict[str, Any] = Field(default_factory=dict)
    plan_change_context: PlanChangeContext | None = None
    plan_scope: Literal["long_term", "short_term", "daily_task", "unspecified"] | None = None
    plan_scope_hint: Literal["long_term", "short_term", "daily_task", "unspecified"] | None = None


class ReviewCardResult(BaseModel):
    status: Literal["success", "failed"]
    execution_id: str
    task_type: str
    agent_outputs: list[AgentEnvelope[Any]]
    learning_plan: Any | None = None
    review_schedule: ReviewSchedule | None = None
    review_task: ReviewTask | None = None
    resource: ResourceDraft | None = None
    resource_version: ResourceVersion | None = None
    resource_binding: ReviewResourceBinding | None = None
    audit: AuditResult | None = None
    snapshot_path: Path
    writeback_intents: list[WritebackIntent]
    model_trace: list[ModelCallTrace] = Field(default_factory=list)
    ui_actions: list[UiAction] = Field(default_factory=list)


class WorkflowResumeRequest(BaseModel):
    answer: str = Field(min_length=1)
    plan_scope: Literal["long_term", "short_term", "daily_task", "unspecified"] | None = None
    plan_change_context: PlanChangeContext | None = None
    profile_updates: dict[str, str] = Field(default_factory=dict)


class WorkflowInterruptedResult(BaseModel):
    status: Literal["interrupted"] = "interrupted"
    thread_id: str
    execution_id: str
    task_type: str
    interrupt: dict[str, Any]
    completed_steps: list[str] = Field(default_factory=list)
    agent_outputs: list[AgentEnvelope[Any]] = Field(default_factory=list)
    model_trace: list[ModelCallTrace] = Field(default_factory=list)


@dataclass
class _WorkflowContinuation:
    request: ReviewCardRequest
    case_id: str
    execution_id: str
    execution_plan: ExecutionPlan
    planner_output: AgentEnvelope[Any]
    context: dict[str, Any]


class PersonalizedReviewCardUseCase:
    conversation_compression_threshold_chars = 4_000

    def __init__(
        self,
        orchestrator: Orchestrator,
        snapshot_exporter: SnapshotExporter,
        writeback_executor: WritebackExecutor | None = None,
        terminal_trace=None,
        model_trace_recorder: ModelTraceRecorder | None = None,
        plan_repository: LearningPlanRepository | None = None,
        run_state_repository: RunStateRepository | None = None,
        conversation_repository: ConversationRepository | None = None,
        review_service: ReviewService | None = None,
        behavior_context_loader: Callable[[str], dict[str, Any]] | None = None,
        profile_update_writer: Callable[[str, dict[str, Any], str | None], dict[str, Any]] | None = None,
        profile_memory_extractor: Callable[[str, str, str | None], dict[str, Any]] | None = None,
        data_permission_gateway: AgentDataPermissionGateway | None = None,
        workshop_runtime: Any | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.snapshot_exporter = snapshot_exporter
        self.writeback_executor = writeback_executor
        self.terminal_trace = terminal_trace
        self.model_trace_recorder = model_trace_recorder
        self.plan_repository = plan_repository or InMemoryLearningPlanRepository()
        self.run_state_repository = run_state_repository or InMemoryRunStateRepository()
        self.conversation_repository = (
            conversation_repository or InMemoryConversationRepository()
        )
        self.review_service = review_service
        self.behavior_context_loader = behavior_context_loader
        self.profile_update_writer = profile_update_writer
        self.profile_memory_extractor = profile_memory_extractor
        self.data_permission_gateway = data_permission_gateway or AgentDataPermissionGateway()
        self.workshop_runtime = workshop_runtime
        self._continuations: dict[str, _WorkflowContinuation] = {}

    async def execute(
        self, request: ReviewCardRequest
    ) -> ReviewCardResult | WorkflowInterruptedResult:
        if self.model_trace_recorder:
            self.model_trace_recorder.reset()
        thread_id = request.thread_id or f"THREAD_{uuid4().hex}"
        conversation_id = request.conversation_id or thread_id
        execution_id = f"EXE_{uuid4().hex}"
        case_id = f"CASE_{uuid4().hex}"
        self._remember_run(
            thread_id,
            {
                "status": "running",
                "thread_id": thread_id,
                "execution_id": execution_id,
                "case_id": case_id,
                "learner_id": request.learner_id,
            },
        )
        existing_messages = self.conversation_repository.get_messages(
            conversation_id, request.learner_id
        )
        # The repository is the durable source of conversation context.  A
        # client may send only the visible/current turn after a refresh, so it
        # must never replace a longer server-side history.
        persisted_messages = list(existing_messages)
        if request.messages:
            if not persisted_messages:
                persisted_messages = list(request.messages)
            elif (
                len(request.messages) >= len(persisted_messages)
                and request.messages[: len(persisted_messages)] == persisted_messages
            ):
                persisted_messages = list(request.messages)
            else:
                for message in request.messages:
                    if not persisted_messages or message != persisted_messages[-1]:
                        persisted_messages.append(message)
        if (
            not persisted_messages
            or persisted_messages[-1].get("content") != request.user_request
        ):
            persisted_messages.append({"role": "user", "content": request.user_request})
        self.conversation_repository.save_messages(
            conversation_id, request.learner_id, persisted_messages
        )
        if not existing_messages:
            self.conversation_repository.rename_session(
                conversation_id,
                request.learner_id,
                request.user_request.strip().replace("\n", " ")[:40] or "新对话",
            )
        if self.profile_memory_extractor is not None:
            await asyncio.to_thread(
                self.profile_memory_extractor,
                request.learner_id,
                request.user_request,
                execution_id,
            )
        behavior_context = await self._load_behavior_context(request.learner_id)
        effective_user_profile = self._merge_context_dict(
            request.user_profile, behavior_context.get("user_profile", {})
        )
        effective_learning_profile = self._merge_context_dict(
            request.learning_profile, behavior_context.get("learning_profile", {})
        )
        effective_system_data = self._merge_context_dict(
            request.system_data, behavior_context.get("system_data", {})
        )
        effective_knowledge_states = (
            behavior_context.get("user_knowledge_state")
            or request.user_knowledge_state
        )
        effective_question_attempts = (
            behavior_context.get("question_attempt", [])
            if self.behavior_context_loader is not None
            else request.question_attempt
        )
        effective_question_learning_stats = (
            behavior_context.get("question_learning_stats")
            or request.question_learning_stats
        )
        learning_monitoring = LearningMonitoringService().build_snapshot(
            request.learner_id,
            {
                **behavior_context,
                "learning_profile": effective_learning_profile,
                "system_data": effective_system_data,
                "question_attempt": effective_question_attempts,
                "mastery": effective_knowledge_states,
            },
            window_days=7,
        )
        if self.review_service is not None and effective_question_attempts:
            self.review_service.ingest_question_attempts(
                learner_id=request.learner_id,
                attempts=effective_question_attempts,
            )
        if self.review_service is not None and effective_knowledge_states:
            self.review_service.ingest_knowledge_states(
                learner_id=request.learner_id,
                states=effective_knowledge_states,
                prompt_abstract=request.user_request,
            )
        persisted_plans = self.plan_repository.get_current(request.learner_id)
        current_long_term_plan = (
            request.long_term_plan
            or (
                persisted_plans.long_term_plan.model_dump(mode="json")
                if persisted_plans is not None
                and persisted_plans.long_term_plan is not None
                else {}
            )
        )
        current_short_term_plan = (
            request.short_term_plan
            or (
                persisted_plans.short_term_plan.model_dump(mode="json")
                if persisted_plans is not None
                and persisted_plans.short_term_plan is not None
                else {}
            )
        )
        current_learning_task = (
            request.learning_task
            or (
                persisted_plans.learning_task.model_dump(mode="json")
                if persisted_plans is not None
                and persisted_plans.learning_task is not None
                else {}
            )
        )
        plan_change = request.plan_change_context
        effective_user_request = request.user_request
        if plan_change is not None:
            clarification_parts = [
                plan_change.original_request,
                f"用户补充的具体变化：{plan_change.change_details}",
            ]
            for label, value in (
                ("可用时间", plan_change.available_time),
                ("希望保留", plan_change.keep_items),
                ("希望放弃", plan_change.drop_items),
                ("期望结果", plan_change.expected_outcome),
            ):
                if value and value.strip():
                    clarification_parts.append(f"{label}：{value.strip()}")
            effective_user_request = "\n".join(clarification_parts)
        total_message_chars = sum(
            len(str(item.get("content", ""))) for item in persisted_messages
        )
        # Explicit scope is user/system authority. Text classifiers only provide
        # a hint; Planner owns the semantic decision and may override that hint.
        explicit_plan_scope = request.plan_scope
        plan_scope_hint = request.plan_scope_hint or infer_plan_scope(
            effective_user_request
        )
        continued_plan_scope = infer_continued_plan_scope(
            effective_user_request,
            persisted_messages,
        )
        context_messages = [
            {
                **item,
                "message_id": item.get("message_id") or f"{conversation_id}:message:{index + 1}",
                "learner_id": item.get("learner_id") or request.learner_id,
            }
            for index, item in enumerate(persisted_messages)
            if isinstance(item, dict)
        ]
        context = {
            "case_id": case_id,
            "trace_id": f"TRACE_{uuid4().hex}",
            "request_id": f"REQ_{uuid4().hex}",
            "execution_id": execution_id,
            "thread_id": thread_id,
            "interruptible": request.thread_id is not None,
            "original_user_request": request.user_request,
            "learner_id": request.learner_id,
            "user_request": effective_user_request,
            "available_minutes": request.available_minutes,
            "messages": context_messages,
            "user_profile": effective_user_profile,
            "learning_profile": effective_learning_profile,
            "system_data": effective_system_data,
            "user_knowledge_states": effective_knowledge_states,
            "question_attempts": effective_question_attempts,
            "question_learning_stats": effective_question_learning_stats,
            "behavior_context_source": behavior_context.get("source"),
            # Every product entry point follows the same backend-owned planning
            # prerequisite policy. Tests that call agents directly remain able to
            # opt in explicitly without manufacturing persistence dependencies.
            "enforce_profile_readiness": (
                self.behavior_context_loader is not None
                and not self._has_meaningful_profile(request.user_profile)
            ),
            "enforce_planning_readiness": True,
            "behavior_context_calculated_at": behavior_context.get("calculated_at"),
            "learning_monitoring": learning_monitoring.model_dump(mode="json"),
            "learning_target": behavior_context.get("learning_target"),
            "current_long_term_plan": current_long_term_plan,
            "current_short_term_plan": current_short_term_plan,
            "current_learning_task": current_learning_task,
            "exam_constraints": request.exam_constraints,
            "plan_change_context": (
                plan_change.model_dump(exclude_none=True) if plan_change else None
            ),
            "plan_scope": explicit_plan_scope,
            "plan_scope_hint": plan_scope_hint,
            "continued_plan_scope": continued_plan_scope,
            "explicit_long_term_change": bool(
                plan_change and "long_term" in plan_change.target_layers
            ),
            "explicit_short_term_change": bool(
                plan_change and "short_term" in plan_change.target_layers
            ),
            "requires_learning_plan_output": (
                any(word in effective_user_request for word in ("学习计划", "复习计划", "制定计划", "规划"))
                and any(word in effective_user_request for word in ("学习卡", "学习卡片", "复习卡", "学习资源"))
            ),
            "conversation_requires_compression": (
                total_message_chars > self.conversation_compression_threshold_chars
            ),
            "profile": {
                "confirmed_preferences": effective_user_profile.get(
                    "user_preference", {}
                ),
            },
            "terminal_trace": self.terminal_trace,
        }
        planner = self.orchestrator.agent_registry.get("planner_agent")
        planner_context = dict(context)
        planner_context["step_id"] = "planner"
        emit_runtime_event("step_started", step_id="planner", agent="planner_agent", depends_on=[])
        planner_output = await planner.run(planner_context)
        emit_runtime_event(
            "system_output",
            step_id="planner",
            agent="planner_agent",
            output=planner_output,
        )
        emit_runtime_event(
            "step_completed", step_id="planner", agent="planner_agent", status="success"
        )
        execution_plan = PlannerAgent.build_plan(planner_output.payload)
        context["task_type"] = planner_output.payload.task_type
        context["plan_scope"] = planner_output.payload.plan_scope
        self._emit_compiled_graph(execution_plan)
        execution = await self.orchestrator.execute(
            execution_plan,
            context,
            thread_id=thread_id,
        )
        if execution.status == "interrupted":
            interrupted = WorkflowInterruptedResult(
                thread_id=thread_id,
                execution_id=execution_id,
                task_type=planner_output.payload.task_type,
                interrupt=execution.interrupt or {},
                completed_steps=list(execution.outputs),
                agent_outputs=[
                    planner_output,
                    *[
                        output
                        for output in execution.outputs.values()
                        if isinstance(output, AgentEnvelope)
                    ],
                ],
                model_trace=self._model_trace(),
            )
            self._continuations[thread_id] = _WorkflowContinuation(
                request=request,
                case_id=case_id,
                execution_id=execution_id,
                execution_plan=execution_plan,
                planner_output=planner_output,
                context=context,
            )
            self._remember_run(
                thread_id,
                {
                    "status": "interrupted",
                    "thread_id": thread_id,
                    "interrupt": interrupted.interrupt,
                    "completed_steps": interrupted.completed_steps,
                    "execution_id": execution_id,
                    "task_type": interrupted.task_type,
                    "continuation": self._continuation_payload(
                        self._continuations[thread_id]
                    ),
                },
            )
            self._save_assistant_message(
                conversation_id, request.learner_id, persisted_messages, interrupted
            )
            return interrupted
        if execution.status != "success":
            self.mark_run_failed(thread_id, execution.error_message or execution.status)
            detail = execution.error_message or self._execution_failure_detail(execution)
            raise RuntimeError(f"personalized review card execution failed: {detail}")
        result = self._finalize_execution(
            request=request,
            case_id=case_id,
            execution_id=execution_id,
            execution_plan=execution_plan,
            execution=execution,
            planner_output=planner_output,
        )
        self._remember_run(
            thread_id,
            {
                "status": "completed",
                "thread_id": thread_id,
                "result": result,
                "continuation": None,
            },
        )
        self._save_assistant_message(
            conversation_id, request.learner_id, persisted_messages, result
        )
        return result

    async def resume(
        self,
        thread_id: str,
        request: WorkflowResumeRequest,
    ) -> ReviewCardResult | WorkflowInterruptedResult:
        continuation = self._continuations.get(thread_id)
        if continuation is None:
            continuation = self._restore_continuation(thread_id)
            if continuation is not None:
                self._continuations[thread_id] = continuation
        if continuation is None:
            raise KeyError(f"没有可恢复的 LangGraph 会话：{thread_id}")
        self._remember_run(thread_id, {"status": "running", "thread_id": thread_id})
        conversation_id = continuation.request.conversation_id or thread_id
        persisted_messages = self.conversation_repository.get_messages(
            conversation_id, continuation.request.learner_id
        )
        persisted_messages.append({"role": "user", "content": request.answer})
        self.conversation_repository.save_messages(
            conversation_id, continuation.request.learner_id, persisted_messages
        )
        resume_payload = request.model_dump(mode="json", exclude_none=True)
        run_state = self.get_run_state(thread_id) or {}
        interrupt_payload = run_state.get("interrupt") or {}
        if interrupt_payload.get("interrupt_type") == "profile_completion":
            pending_fields = {
                str(field)
                for field in interrupt_payload.get("profile_fields") or []
                if str(field).strip()
            }
            profile_updates = dict(request.profile_updates)
            if not profile_updates and len(pending_fields) == 1:
                profile_updates[next(iter(pending_fields))] = request.answer.strip()
            self.data_permission_gateway.authorize(
                agent="memory_agent",
                domain="learner_profile",
                action="write",
                fields=set(profile_updates),
                confirmed_fields=pending_fields,
            )
            if self.profile_update_writer is None:
                raise RuntimeError("profile writeback is unavailable for this workflow")
            await asyncio.to_thread(
                self.profile_update_writer,
                continuation.request.learner_id,
                profile_updates,
                continuation.execution_id,
            )
            resume_payload["profile_updates"] = profile_updates
        execution = await self.orchestrator.resume(
            thread_id,
            resume_payload,
            plan=continuation.execution_plan,
            context=continuation.context,
        )
        if execution.status == "interrupted":
            interrupted = WorkflowInterruptedResult(
                thread_id=thread_id,
                execution_id=continuation.execution_id,
                task_type=continuation.planner_output.payload.task_type,
                interrupt=execution.interrupt or {},
                completed_steps=list(execution.outputs),
                agent_outputs=[
                    continuation.planner_output,
                    *[
                        output
                        for output in execution.outputs.values()
                        if isinstance(output, AgentEnvelope)
                    ],
                ],
                model_trace=self._model_trace(),
            )
            self._remember_run(
                thread_id,
                {
                    "status": "interrupted",
                    "thread_id": thread_id,
                    "interrupt": interrupted.interrupt,
                    "completed_steps": interrupted.completed_steps,
                    "execution_id": continuation.execution_id,
                    "task_type": interrupted.task_type,
                    "continuation": self._continuation_payload(continuation),
                },
            )
            self._save_assistant_message(
                conversation_id,
                continuation.request.learner_id,
                persisted_messages,
                interrupted,
            )
            return interrupted
        if execution.status != "success":
            detail = execution.error_message or self._execution_failure_detail(execution)
            self.mark_run_failed(thread_id, detail)
            raise RuntimeError(f"personalized review card execution failed: {detail}")
        result = self._finalize_execution(
            request=continuation.request,
            case_id=continuation.case_id,
            execution_id=continuation.execution_id,
            execution_plan=continuation.execution_plan,
            execution=execution,
            planner_output=continuation.planner_output,
        )
        self._continuations.pop(thread_id, None)
        self._remember_run(
            thread_id,
            {
                "status": "completed",
                "thread_id": thread_id,
                "result": result,
                "continuation": None,
            },
        )
        self._save_assistant_message(
            conversation_id,
            continuation.request.learner_id,
            persisted_messages,
            result,
        )
        return result

    def _save_assistant_message(
        self,
        conversation_id: str,
        learner_id: str,
        messages: list[dict[str, Any]],
        result: ReviewCardResult | WorkflowInterruptedResult,
    ) -> None:
        content = workflow_result_to_markdown(result)
        actions = [
            action.model_dump(mode="json")
            for action in getattr(result, "ui_actions", [])
        ]
        assistant_message: dict[str, Any] = {"role": "assistant", "content": content}
        if actions:
            assistant_message["actions"] = actions
        self.conversation_repository.save_messages(
            conversation_id,
            learner_id,
            [*messages, assistant_message],
        )

    def get_run_state(self, thread_id: str) -> dict[str, Any] | None:
        return self.run_state_repository.get(thread_id)

    def mark_run_failed(self, thread_id: str, message: str) -> None:
        self._continuations.pop(thread_id, None)
        self._remember_run(
            thread_id,
            {
                "status": "failed",
                "thread_id": thread_id,
                "message": message,
                "continuation": None,
            },
        )

    def _continuation_payload(
        self, continuation: _WorkflowContinuation
    ) -> dict[str, Any]:
        context = {
            key: value
            for key, value in continuation.context.items()
            if key != "terminal_trace"
        }
        return {
            "request": continuation.request.model_dump(mode="json"),
            "case_id": continuation.case_id,
            "execution_id": continuation.execution_id,
            "execution_plan": continuation.execution_plan.model_dump(mode="json"),
            "planner_output": continuation.planner_output.model_dump(mode="json"),
            "context": context,
        }

    def _restore_continuation(
        self, thread_id: str
    ) -> _WorkflowContinuation | None:
        state = self.run_state_repository.get(thread_id) or {}
        if state.get("status") not in {"interrupted", "running"}:
            return None
        payload = state.get("continuation")
        if not isinstance(payload, dict):
            return None
        context = dict(payload.get("context") or {})
        context["terminal_trace"] = self.terminal_trace
        return _WorkflowContinuation(
            request=ReviewCardRequest.model_validate(payload.get("request") or {}),
            case_id=str(payload.get("case_id") or state.get("case_id") or ""),
            execution_id=str(
                payload.get("execution_id") or state.get("execution_id") or ""
            ),
            execution_plan=ExecutionPlan.model_validate(
                payload.get("execution_plan") or {}
            ),
            planner_output=AgentEnvelope[PlannerDecision].model_validate(
                payload.get("planner_output") or {}
            ),
            context=context,
        )

    def _remember_run(self, thread_id: str, state: dict[str, Any]) -> None:
        self.run_state_repository.save(thread_id, state)

    def _finalize_execution(
        self,
        *,
        request: ReviewCardRequest,
        case_id: str,
        execution_id: str,
        execution_plan: ExecutionPlan,
        execution,
        planner_output: AgentEnvelope[Any],
    ) -> ReviewCardResult:
        agent_outputs = [planner_output, *[
            output for output in execution.outputs.values() if isinstance(output, AgentEnvelope)
        ]]
        learning_plan_output = execution.outputs.get("learning_plan")
        learning_plan = (
            getattr(learning_plan_output, "payload", None) if learning_plan_output else None
        )
        if planner_output.payload.task_type == "learning_plan":
            snapshot_path = self.snapshot_exporter.export(
                case_id,
                execution_id,
                {
                    "request": request,
                    "plan": execution_plan,
                    "agent_outputs": agent_outputs,
                    "learning_plan": learning_plan,
                    "trace": execution.trace,
                    "tool_trace": execution.tool_trace,
                    "model_trace": self._model_trace(),
                },
            )
            return ReviewCardResult(
                status="success",
                execution_id=execution_id,
                task_type=planner_output.payload.task_type,
                agent_outputs=agent_outputs,
                learning_plan=learning_plan,
                snapshot_path=snapshot_path,
                writeback_intents=[],
                model_trace=self._model_trace(),
            )
        if planner_output.payload.task_type == "paper_generation":
            return self._publish_paper_blueprint(
                request=request,
                case_id=case_id,
                execution_id=execution_id,
                execution_plan=execution_plan,
                execution=execution,
                planner_output=planner_output,
                agent_outputs=agent_outputs,
            )
        if planner_output.payload.task_type == "knowledge_explanation":
            return self._publish_standalone_resource(
                request=request,
                case_id=case_id,
                execution_id=execution_id,
                execution_plan=execution_plan,
                execution=execution,
                planner_output=planner_output,
                agent_outputs=agent_outputs,
            )
        audit = execution.outputs["audit"].payload
        if audit.decision != "pass":
            raise RuntimeError(f"resource was not approved: {audit.decision}")
        resource = execution.outputs["expert"].payload
        review_schedule = execution.outputs["schedule"].payload
        if review_schedule.selected_task is None:
            raise RuntimeError("review schedule did not select a task")
        is_admitted = bool(
            self.review_service
            and self.review_service.has_completed_attempt(
                request.learner_id,
                review_schedule.selected_task.primary_kp_id,
            )
        )
        review_task = review_schedule.selected_task.model_copy(
            update={"status": "bound" if is_admitted else "awaiting_attempt"}
        )
        resource_version = ResourceVersion(
            resource_id=f"RES_{uuid4().hex}",
            source_draft_id=resource.resource_draft_id,
            title=resource.title,
            content=resource.content,
            audit_result_id=audit.audit_result_id,
            published_at=datetime.now(timezone.utc),
        )
        resource_binding = ReviewResourceBinding(
            binding_id=f"BIND_{uuid4().hex}",
            review_task_id=review_task.review_task_id,
            resource_id=resource_version.resource_id,
            resource_version=resource_version.resource_version,
            audit_result_id=audit.audit_result_id,
        )
        writeback_intents = self._build_writeback_intents(
            execution_id, audit, resource_version, review_task, resource_binding
        )
        if self.writeback_executor:
            self.writeback_executor.execute_batch(writeback_intents)
        if self.review_service is not None:
            self.review_service.record_delivery(
                schedule=review_schedule,
                task=review_task,
                resource=resource_version,
                binding=resource_binding,
                prompt_abstract=request.user_request,
            )
        snapshot_path = self.snapshot_exporter.export(
            case_id,
            execution_id,
            {
                "request": request,
                "plan": execution_plan,
                "agent_outputs": agent_outputs,
                "review_schedule": review_schedule,
                "review_task": review_task,
                "resource": resource,
                "resource_version": resource_version,
                "resource_binding": resource_binding,
                "writeback_intents": writeback_intents,
                "audit": audit,
                "trace": execution.trace,
                "tool_trace": execution.tool_trace,
                "model_trace": self._model_trace(),
            },
        )
        return ReviewCardResult(
            status="success",
            execution_id=execution_id,
            task_type=planner_output.payload.task_type,
            agent_outputs=agent_outputs,
            learning_plan=learning_plan,
            review_schedule=review_schedule,
            review_task=review_task,
            resource=resource,
            resource_version=resource_version,
            resource_binding=resource_binding,
            audit=audit,
            snapshot_path=snapshot_path,
            writeback_intents=writeback_intents,
            model_trace=self._model_trace(),
        )

    def _publish_paper_blueprint(
        self,
        *,
        request: ReviewCardRequest,
        case_id: str,
        execution_id: str,
        execution_plan,
        execution,
        planner_output,
        agent_outputs: list[AgentEnvelope[Any]],
    ) -> ReviewCardResult:
        audit = execution.outputs["audit"].payload
        if audit.decision != "pass":
            raise RuntimeError(f"exam paper was not approved: {audit.decision}")
        paper = execution.outputs["paper_assembly"].payload
        blueprint = execution.outputs["paper_blueprint"].payload
        candidate_pool = execution.outputs["question_pool"].payload
        paper_publication: dict[str, Any] | None = None
        if self.workshop_runtime is not None:
            self.data_permission_gateway.authorize(
                agent="paper_assembly_agent",
                domain="paper_workspace",
                action="write",
                fields={"paper", "blueprint", "evidence_pack", "execution_id"},
            )
            knowledge_output = execution.outputs.get("knowledge")
            evidence_pack = getattr(knowledge_output, "payload", None)
            paper_publication = self.workshop_runtime.publish_agent_paper(
                request.learner_id,
                execution_id=execution_id,
                paper=paper.model_dump(mode="json"),
                blueprint=blueprint.model_dump(mode="json"),
                evidence_pack=(
                    evidence_pack.model_dump(mode="json")
                    if hasattr(evidence_pack, "model_dump")
                    else {}
                ),
            )
        publish_answers = self._paper_answers_requested(request)
        paper_content: dict[str, Any] = {
            "试卷说明": paper.instructions,
            "试卷正文": [item.model_dump(mode="json") for item in paper.learner_questions()],
        }
        if publish_answers:
            paper_content["参考答案"] = [
                {
                    "题号": item.sequence,
                    "答案": item.question.reference_answer,
                }
                for item in paper.items
            ]
            paper_content["答案解析"] = [
                {
                    "题号": item.sequence,
                    "解析": item.question.analysis or "暂无解析",
                }
                for item in paper.items
            ]
        paper_content.update(
            {
                "蓝图覆盖": paper.coverage_summary,
                "待确认项": paper.unresolved_constraints,
            }
        )
        resource = ResourceDraft(
            resource_draft_id=paper.paper_draft_id,
            title=paper.title,
            content=paper_content,
            target_difficulty=1,
            estimated_minutes=paper.duration_minutes or request.available_minutes,
            safety_notes=[
                (
                    "答案与解析按用户要求独立发布，不与题干混排；内部检索信息仍不公开。"
                    if publish_answers
                    else "考生视图不包含答案、解析和内部检索信息。"
                )
            ],
            question_consumption=QuestionConsumptionDecision(
                use_question_candidates=True,
                usage_reason="完整试卷仅从按蓝图检索的候选题池中选择。",
                selected_question_ids=[item.question.question_id for item in paper.items],
                resource_type="practice",
            ),
        )
        resource_version = ResourceVersion(
            resource_id=f"RES_{uuid4().hex}",
            source_draft_id=resource.resource_draft_id,
            title=resource.title,
            content=resource.content,
            audit_result_id=audit.audit_result_id,
            published_at=datetime.now(timezone.utc),
        )
        writeback_intents = [
            WritebackIntent(
                intent_id=f"WBI_{uuid4().hex}",
                source_artifact_id=audit.audit_result_id,
                effect_type="record_audit",
                target_service="audit_service",
                target_entity_type="audit_result",
                payload={
                    **audit.model_dump(mode="json"),
                    "resource_id": resource_version.resource_id,
                    "source_draft_id": resource.resource_draft_id,
                },
                idempotency_key=f"{execution_id}:audit:{audit.audit_result_id}",
            ),
            WritebackIntent(
                intent_id=f"WBI_{uuid4().hex}",
                source_artifact_id=resource.resource_draft_id,
                effect_type="publish_resource",
                target_service="resource_service",
                target_entity_type="resource_version",
                payload=resource_version.model_dump(mode="json"),
                preconditions=["audit_pass"],
                idempotency_key=(
                    f"{execution_id}:resource:{resource_version.resource_id}:"
                    f"v{resource_version.resource_version}"
                ),
            ),
        ]
        if self.writeback_executor:
            self.writeback_executor.execute_batch(writeback_intents)
        snapshot_path = self.snapshot_exporter.export(
            case_id,
            execution_id,
            {
                "request": request,
                "plan": execution_plan,
                "agent_outputs": agent_outputs,
                "paper_blueprint": blueprint,
                "question_candidate_pool": candidate_pool,
                "exam_paper_draft": paper,
                "resource": resource,
                "resource_version": resource_version,
                "writeback_intents": writeback_intents,
                "audit": audit,
                "trace": execution.trace,
                "tool_trace": execution.tool_trace,
                "model_trace": self._model_trace(),
            },
        )
        return ReviewCardResult(
            status="success",
            execution_id=execution_id,
            task_type=planner_output.payload.task_type,
            agent_outputs=agent_outputs,
            resource=resource,
            resource_version=resource_version,
            audit=audit,
            snapshot_path=snapshot_path,
            writeback_intents=writeback_intents,
            model_trace=self._model_trace(),
            ui_actions=(
                [
                    UiAction(
                        label="开始答题",
                        destination="workshop.paper",
                        params={"paper_id": str(paper_publication["paper_id"])},
                    )
                ]
                if paper_publication and paper_publication.get("paper_id")
                else []
            ),
        )

    @staticmethod
    def _paper_answers_requested(request: ReviewCardRequest) -> bool:
        user_request = request.user_request.replace(" ", "")
        if any(
            phrase in user_request
            for phrase in ("不需要答案", "不要答案", "隐藏答案", "不提供答案")
        ):
            return False
        if any(keyword in user_request for keyword in ("答案", "解析", "评分说明")):
            return True
        requirement = str(
            request.exam_constraints.get("answer_and_rubric_requirement") or ""
        ).strip()
        return bool(requirement) and not any(
            phrase in requirement for phrase in ("不需要", "不要", "隐藏", "不提供")
        )

    def _publish_standalone_resource(
        self,
        *,
        request: ReviewCardRequest,
        case_id: str,
        execution_id: str,
        execution_plan,
        execution,
        planner_output,
        agent_outputs: list[AgentEnvelope[Any]],
    ) -> ReviewCardResult:
        audit = execution.outputs["audit"].payload
        if audit.decision != "pass":
            raise RuntimeError(f"standalone resource was not approved: {audit.decision}")
        resource = execution.outputs["expert"].payload
        resource_version = ResourceVersion(
            resource_id=f"RES_{uuid4().hex}",
            source_draft_id=resource.resource_draft_id,
            title=resource.title,
            content=resource.content,
            audit_result_id=audit.audit_result_id,
            published_at=datetime.now(timezone.utc),
        )
        card_publication = self._publish_knowledge_card(
            request=request,
            execution_id=execution_id,
            execution=execution,
            resource=resource,
        )
        writeback_intents = [
            WritebackIntent(
                intent_id=f"WBI_{uuid4().hex}",
                source_artifact_id=audit.audit_result_id,
                effect_type="record_audit",
                target_service="audit_service",
                target_entity_type="audit_result",
                payload={
                    **audit.model_dump(mode="json"),
                    "resource_id": resource_version.resource_id,
                },
                idempotency_key=f"{execution_id}:audit:{audit.audit_result_id}",
            ),
            WritebackIntent(
                intent_id=f"WBI_{uuid4().hex}",
                source_artifact_id=resource.resource_draft_id,
                effect_type="publish_resource",
                target_service="resource_service",
                target_entity_type="resource_version",
                payload=resource_version.model_dump(mode="json"),
                preconditions=["audit_pass"],
                idempotency_key=(
                    f"{execution_id}:resource:{resource_version.resource_id}:"
                    f"v{resource_version.resource_version}"
                ),
            ),
        ]
        if self.writeback_executor:
            self.writeback_executor.execute_batch(writeback_intents)
        snapshot_path = self.snapshot_exporter.export(
            case_id,
            execution_id,
            {
                "request": request,
                "plan": execution_plan,
                "agent_outputs": agent_outputs,
                "resource": resource,
                "resource_version": resource_version,
                "audit": audit,
                "writeback_intents": writeback_intents,
                "trace": execution.trace,
                "tool_trace": execution.tool_trace,
                "model_trace": self._model_trace(),
            },
        )
        return ReviewCardResult(
            status="success",
            execution_id=execution_id,
            task_type=planner_output.payload.task_type,
            agent_outputs=agent_outputs,
            resource=resource,
            resource_version=resource_version,
            audit=audit,
            snapshot_path=snapshot_path,
            writeback_intents=writeback_intents,
            model_trace=self._model_trace(),
            ui_actions=(
                [
                    UiAction(
                        label="查看知识卡",
                        destination="workshop.knowledge_card",
                        params={"card_id": str(card_publication["card_id"])},
                    )
                ]
                if card_publication and card_publication.get("card_id")
                else []
            ),
        )

    def _publish_knowledge_card(
        self,
        *,
        request: ReviewCardRequest,
        execution_id: str,
        execution,
        resource: ResourceDraft,
    ) -> dict[str, Any] | None:
        if self.workshop_runtime is None:
            return None
        knowledge_output = execution.outputs.get("knowledge")
        evidence_pack = getattr(knowledge_output, "payload", None)
        kp_ids = list(getattr(evidence_pack, "resolved_kp_ids", []) or [])
        if not kp_ids:
            return None
        self.data_permission_gateway.authorize(
            agent="expert_agent",
            domain="knowledge_card",
            action="write",
            fields={"kp_id", "title", "resource_bundle", "source_execution_id"},
        )
        evidence_items = list(getattr(evidence_pack, "evidence_items", []) or [])
        textbook_slices: list[dict[str, Any]] = []
        videos: list[dict[str, Any]] = []
        questions: list[dict[str, Any]] = []
        provenance: list[dict[str, Any]] = []
        for item in evidence_items:
            value = item.model_dump(mode="json") if hasattr(item, "model_dump") else {}
            resource_type = str(value.get("resource_type") or "")
            normalized = {
                "source_id": value.get("source_id"),
                "summary": value.get("content_summary"),
                "url": value.get("source_url"),
                "origin": "web_search" if str(value.get("authority_level", "")).startswith("web_") else "knowledge_repository",
            }
            if resource_type == "video":
                videos.append(normalized)
            elif resource_type == "question":
                questions.append(normalized)
            elif resource_type == "textbook":
                textbook_slices.append(normalized)
            provenance.append({
                "kind": resource_type or "reference",
                "source_id": value.get("source_id"),
                "origin": normalized["origin"],
            })
        for question in list(getattr(evidence_pack, "_question_details", []) or []):
            value = (
                question.model_dump(mode="json")
                if hasattr(question, "model_dump")
                else {}
            )
            question_id = str(value.get("question_id") or "").strip()
            if question_id and any(
                str(item.get("question_id") or item.get("source_id") or "")
                == question_id
                for item in questions
            ):
                continue
            questions.append(
                {
                    "question_id": question_id,
                    "question_type": value.get("question_type"),
                    "stem": value.get("stem"),
                    "options": value.get("options") or [],
                    "reference_answer": value.get("reference_answer"),
                    "analysis": value.get("analysis"),
                    "tags": value.get("tags") or [],
                    "origin": (
                        "web_search"
                        if value.get("source_tier") == "web_reference"
                        else "knowledge_repository"
                    ),
                }
            )
            provenance.append(
                {
                    "kind": "question",
                    "source_id": question_id,
                    "origin": questions[-1]["origin"],
                }
            )
        bundle = {
            "schema_version": "1.0",
            "bundle_id": f"KRB_{execution_id}",
            "knowledge_point": {"kp_id": kp_ids[0], "title": resource.title},
            "explanation": {"title": resource.title, "content": resource.content, "source": "expert_agent"},
            "textbook_slices": textbook_slices,
            "videos": videos,
            "questions": questions,
            "coverage": {
                "knowledge_point": True,
                "explanation": True,
                "textbook_slices": bool(textbook_slices),
                "videos": bool(videos),
                "questions": bool(questions),
                "fallback_used": [],
            },
            "provenance": provenance,
        }
        return self.workshop_runtime.save_knowledge_card(
            request.learner_id,
            kp_id=kp_ids[0],
            title=resource.title,
            resource_bundle=bundle,
            source_execution_id=execution_id,
        )

    async def _load_behavior_context(self, learner_id: str) -> dict[str, Any]:
        if self.behavior_context_loader is None:
            return {}
        try:
            value = await asyncio.to_thread(self.behavior_context_loader, learner_id)
        except Exception as exc:
            emit_runtime_event(
                "behavior_context_unavailable",
                source="frontend_backend",
                error_type=type(exc).__name__,
            )
            return {}
        if not isinstance(value, dict):
            return {}
        emit_runtime_event(
            "behavior_context_loaded",
            source=value.get("source", "frontend_backend"),
            calculated_at=value.get("calculated_at"),
            attempt_count=len(value.get("question_attempt", [])),
            mastery_count=len(value.get("mastery", [])),
        )
        return value

    @classmethod
    def _merge_context_dict(
        cls, request_value: dict[str, Any], server_value: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge nested context while keeping persisted server facts authoritative."""

        merged = dict(request_value or {})
        for key, value in (server_value or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._merge_context_dict(merged[key], value)
            elif value not in (None, "", [], {}):
                merged[key] = value
        return merged

    @staticmethod
    def _has_meaningful_profile(profile: dict[str, Any]) -> bool:
        """Distinguish an actual learner profile from an empty database shell."""

        ignored = {"user_id", "learner_id", "created_at", "updated_at"}
        return any(
            key not in ignored and value not in (None, "", [], {})
            for key, value in (profile or {}).items()
        )

    def _emit_compiled_graph(self, plan: ExecutionPlan) -> None:
        """Expose the actual runtime DAG without leaking graph state or payloads."""
        levels = [["planner"], *plan.topological_levels()]
        nodes = [
            {
                "step_id": "planner",
                "agent": "planner_agent",
                "action": "route_request",
                "max_retries": 0,
            },
            *[
                {
                    "step_id": step.step_id,
                    "agent": step.agent,
                    "action": step.action,
                    "max_retries": step.max_retries,
                }
                for step in plan.steps
            ],
        ]
        root_steps = [step.step_id for step in plan.steps if not step.depends_on]
        edges = [
            {"source": "planner", "target": step_id, "kind": "dependency"}
            for step_id in root_steps
        ]
        edges.extend(
            {
                "source": dependency,
                "target": step.step_id,
                "kind": "dependency",
            }
            for step in plan.steps
            for dependency in step.depends_on
        )
        control_edges = []
        audit_step = next(
            (step for step in plan.steps if step.agent == "audit_agent"), None
        )
        if audit_step is not None:
            revision_target = next(
                (
                    dependency
                    for dependency in audit_step.depends_on
                    if dependency in {"expert", "paper_assembly"}
                ),
                None,
            )
            if revision_target:
                control_edges.append(
                    {
                        "source": audit_step.step_id,
                        "target": revision_target,
                        "kind": "revision",
                        "label": "审核返修",
                    }
                )
        parallel_groups = [level for level in levels if len(level) > 1]
        emit_runtime_event(
            "graph_compiled",
            engine=getattr(self.orchestrator, "engine_name", "legacy"),
            graph_name=f"competition_{plan.task_type}",
            task_type=plan.task_type,
            nodes=nodes,
            edges=edges,
            control_edges=control_edges,
            levels=levels,
            parallel_groups=parallel_groups,
            capabilities={
                "dynamic_routing": True,
                "parallel_execution": bool(parallel_groups),
                "retryable_nodes": sum(
                    1 for step in plan.steps if step.max_retries > 0
                ),
                "controlled_revision": bool(control_edges),
            },
        )

    def _model_trace(self) -> list[ModelCallTrace]:
        return self.model_trace_recorder.items if self.model_trace_recorder else []

    @staticmethod
    def _execution_failure_detail(execution) -> str:
        audit = execution.outputs.get("audit")
        audit_payload = getattr(audit, "payload", None)
        decision = getattr(audit_payload, "decision", None)
        findings = getattr(audit_payload, "findings", [])
        if decision:
            finding_summary = "; ".join(str(item) for item in findings[:2])
            return f"audit decision={decision}" + (f"; findings={finding_summary}" if finding_summary else "")
        return execution.status

    @staticmethod
    def _build_writeback_intents(
        execution_id: str,
        audit: AuditResult,
        resource: ResourceVersion,
        task: ReviewTask,
        binding: ReviewResourceBinding,
    ) -> list[WritebackIntent]:
        return [
            WritebackIntent(
                intent_id=f"WBI_{uuid4().hex}",
                source_artifact_id=audit.audit_result_id,
                effect_type="record_audit",
                target_service="audit_service",
                target_entity_type="audit_result",
                payload={
                    **audit.model_dump(mode="json"),
                    "resource_id": resource.resource_id,
                    "source_draft_id": resource.source_draft_id,
                },
                idempotency_key=f"{execution_id}:audit:{audit.audit_result_id}",
            ),
            WritebackIntent(
                intent_id=f"WBI_{uuid4().hex}",
                source_artifact_id=resource.source_draft_id,
                effect_type="publish_resource",
                target_service="resource_service",
                target_entity_type="resource_version",
                payload=resource.model_dump(mode="json"),
                preconditions=["audit_pass"],
                idempotency_key=f"{execution_id}:resource:{resource.resource_id}:v{resource.resource_version}",
            ),
            WritebackIntent(
                intent_id=f"WBI_{uuid4().hex}",
                source_artifact_id=task.review_task_id,
                effect_type="upsert_review_task",
                target_service="review_scheduler_service",
                target_entity_type="review_task",
                payload=task.model_dump(mode="json"),
                idempotency_key=f"{execution_id}:review-task:{task.review_task_id}",
            ),
            WritebackIntent(
                intent_id=f"WBI_{uuid4().hex}",
                source_artifact_id=binding.binding_id,
                effect_type="bind_review_resource",
                target_service="resource_service",
                target_entity_type="review_resource_binding",
                payload=binding.model_dump(mode="json"),
                preconditions=["audit_pass"],
                idempotency_key=f"{execution_id}:binding:{binding.binding_id}",
            ),
        ]

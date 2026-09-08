from __future__ import annotations

from types import SimpleNamespace

import pytest

from competition_app.agents.planner import PlannerDecision
from competition_app.application.personalized_review_card import (
    ExamWorkspaceChangedError,
    PersonalizedReviewCardUseCase,
    ReviewCardRequest,
    WorkflowResumeRequest,
    _WorkflowContinuation,
    _PrerequisiteInterrupted,
)
from competition_app.exam_scope import bind_exam_workspace, reset_exam_workspace
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.execution import ExecutionPlan, ExecutionStep
from competition_app.runtime.orchestrator import ExecutionResult
from competition_app.runtime.snapshot import SnapshotExporter


class _ResumeOnlyOrchestrator:
    def __init__(self) -> None:
        self.resume_context = None
        self.resume_value = None

    async def resume(self, thread_id, resume_value, *, plan=None, context=None):
        self.resume_value = resume_value
        self.resume_context = dict(context or {})
        return ExecutionResult(
            status="interrupted",
            thread_id=thread_id,
            interrupt={
                "interrupt_type": "route_resolution",
                "questions": ["测试结束前保持中断。"],
            },
        )

    async def abandon_thread(self, thread_id):
        self.abandoned_thread_id = thread_id


@pytest.mark.asyncio
@pytest.mark.parametrize("abandon_fails", [False, True])
@pytest.mark.parametrize("interrupt_type", ["planning_focus_resolution", "plan_scope_resolution"])
async def test_focus_resume_restarts_semantics_without_reusing_stale_evidence(tmp_path, monkeypatch, abandon_fails, interrupt_type):
    orchestrator = _ResumeOnlyOrchestrator()
    use_case = PersonalizedReviewCardUseCase(orchestrator, SnapshotExporter(tmp_path))
    thread_id = "THREAD_LEGACY_FOCUS"
    request = ReviewCardRequest(thread_id=thread_id, conversation_id="CONV_FOCUS",
                                learner_id="LEARNER_RESUME", user_request="按学情安排下周",
                                available_minutes=35, user_profile={"learning_goal": "中医执业医师资格考试"})
    continuation = _WorkflowContinuation(
        request=request, case_id="CASE_RESUME", execution_id="EXE_RESUME",
        execution_plan=ExecutionPlan(plan_id="PLAN_RESUME", task_type="learning_plan",
            steps=[ExecutionStep(step_id="diagnosis", agent="diagnosis_agent")]),
        planner_output=_planner_output(),
        context={"learner_id": "LEARNER_RESUME", "requested_plan_scope": "short_term",
                 "_interrupted_dependency_outputs": {"diagnosis": {"knowledge": "stale"}}},
    )
    use_case._continuations[thread_id] = continuation
    use_case._remember_run(thread_id, dict(status="interrupted", thread_id=thread_id,
        learner_id="LEARNER_RESUME", execution_id="EXE_RESUME",
        interrupt={"interrupt_type": interrupt_type}))
    calls = []

    async def restart(**kwargs):
        calls.append(kwargs)
        return "restarted"

    monkeypatch.setattr(use_case, "_execute_started_run", restart)
    if abandon_fails:
        async def fail_abandon(thread):
            raise RuntimeError("checkpoint unavailable")
        monkeypatch.setattr(orchestrator, "abandon_thread", fail_abandon)
        with pytest.raises(RuntimeError, match="checkpoint unavailable"):
            await use_case.resume(thread_id, WorkflowResumeRequest(answer="沿现有路线安排短期计划"))
        assert calls == []
        assert use_case.get_run_state(thread_id)["status"] == "failed"
        assert orchestrator.resume_context is None
        return
    result = await use_case.resume(thread_id, WorkflowResumeRequest(answer="沿现有路线安排短期计划"))
    assert result == "restarted"
    assert orchestrator.abandoned_thread_id == thread_id
    assert orchestrator.resume_context is None
    assert calls[0]["request"].user_request == "沿现有路线安排短期计划"
    assert calls[0]["request"].plan_scope_hint == (
        "short_term" if interrupt_type == "planning_focus_resolution" else None
    )
    assert calls[0]["thread_id"] == thread_id
    assert calls[0]["conversation_id"] == "CONV_FOCUS"
    assert calls[0]["execution_id"] == "EXE_RESUME"
    assert calls[0]["case_id"] == "CASE_RESUME"
    assert calls[0]["request"].learner_id == "LEARNER_RESUME"
    assert calls[0]["request"].available_minutes == 35
    assert calls[0]["request"].user_profile == request.user_profile


def _planner_output() -> AgentEnvelope[PlannerDecision]:
    return AgentEnvelope(
        artifact_id="ART_PLAN",
        artifact_type="planner_decision",
        case_id="CASE_RESUME",
        trace_id="TRACE_RESUME",
        request_id="REQ_RESUME",
        execution_id="EXE_RESUME",
        step_id="planner",
        producer="planner_agent",
        task_type="learning_plan",
        learner_id="LEARNER_RESUME",
        payload=PlannerDecision(
            task_type="learning_plan",
            plan_scope="daily_task",
            plan_action="create_or_update",
            selected_agents=["diagnosis_agent", "learning_plan_service"],
            routing_reason="用户申请今日任务。",
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,has_parents", [("daily_task", True), ("long_term", True), ("long_term", False), ("short_term", True), ("short_term", False)])
async def test_course_status_resume_skips_parent_materialization(tmp_path, monkeypatch, scope, has_parents) -> None:
    orchestrator = _ResumeOnlyOrchestrator()
    use_case = PersonalizedReviewCardUseCase(
        orchestrator,
        SnapshotExporter(tmp_path),
    )
    thread_id = "THREAD_COURSE_STATUS_RESUME"
    request = ReviewCardRequest(
        thread_id=thread_id,
        conversation_id="CONV_COURSE_STATUS_RESUME",
        learner_id="LEARNER_RESUME",
        user_request="请安排所请求层级的学习计划。",
        plan_scope=scope,
    )
    continuation = _WorkflowContinuation(
        request=request,
        case_id="CASE_RESUME",
        execution_id="EXE_RESUME",
        execution_plan=ExecutionPlan(
            plan_id="PLAN_RESUME",
            task_type="learning_plan",
            steps=[ExecutionStep(step_id="diagnosis", agent="diagnosis_agent")],
        ),
        planner_output=_planner_output(),
        context={
            "learner_id": "LEARNER_RESUME",
            "requested_plan_scope": scope,
            "plan_scope": scope,
            "current_long_term_plan": {"plan_id": "LONG_1", "status": "active"} if has_parents else {},
            "current_short_term_plan": {"plan_id": "SHORT_1", "status": "active"} if has_parents else {},
            "messages": [],
        },
    )
    use_case._continuations[thread_id] = continuation
    use_case._remember_run(
        thread_id,
        {
            "status": "interrupted",
            "thread_id": thread_id,
            "execution_id": "EXE_RESUME",
            "learner_id": "LEARNER_RESUME",
            "interrupt": {
                "interrupt_type": "planning_prerequisite",
                "prerequisite_kind": "course_status_confirmation",
                "requested_scope": scope,
                "original_scope": scope,
                "required_prerequisite_courses": ["中医诊断学"],
                "questions": ["你是否已完成《中医诊断学》？"],
            },
        },
    )

    async def forbidden_parent_materialization(**kwargs):
        raise AssertionError("课程状态确认不得创建父计划")

    monkeypatch.setattr(
        use_case,
        "_materialize_planning_prerequisite",
        forbidden_parent_materialization,
    )

    result = await use_case.resume(
        thread_id,
        WorkflowResumeRequest(answer="没有学过"),
    )

    assert result.status == "interrupted"
    assert orchestrator.resume_value["plan_scope"] == scope
    assert orchestrator.resume_context["plan_scope"] == scope
    assert orchestrator.resume_context["latest_resume_answer"] == "没有学过"
    if scope != "daily_task":
        assert not orchestrator.resume_context.get("prerequisite_resume_pending")
        assert not orchestrator.resume_context.get("force_prerequisite_daily_task")
        assert bool(orchestrator.resume_context["current_long_term_plan"]) == has_parents
        return
    assert orchestrator.resume_context["prerequisite_resume_pending"] is True
    assert orchestrator.resume_context["required_prerequisite_courses"] == [
        "中医诊断学"
    ]
    assert orchestrator.resume_context["current_long_term_plan"]["plan_id"] == "LONG_1"
    assert orchestrator.resume_context["current_short_term_plan"]["plan_id"] == "SHORT_1"


@pytest.mark.asyncio
async def test_resume_rejects_checkpoint_after_exam_workspace_switch(tmp_path) -> None:
    orchestrator = _ResumeOnlyOrchestrator()
    use_case = PersonalizedReviewCardUseCase(orchestrator, SnapshotExporter(tmp_path))
    thread_id = "THREAD_EXAM_SCOPE_SWITCH"
    request = ReviewCardRequest(
        thread_id=thread_id,
        learner_id="LEARNER_RESUME",
        user_request="请制定长期规划。",
        plan_scope="long_term",
    )
    use_case._continuations[thread_id] = _WorkflowContinuation(
        request=request,
        case_id="CASE_RESUME",
        execution_id="EXE_RESUME",
        execution_plan=ExecutionPlan(
            plan_id="PLAN_RESUME",
            task_type="learning_plan",
            steps=[ExecutionStep(step_id="diagnosis", agent="diagnosis_agent")],
        ),
        planner_output=_planner_output(),
        context={
            "learner_id": "LEARNER_RESUME",
            "exam_scope_id": "__legacy__",
            "messages": [],
        },
    )
    token = bind_exam_workspace(
        "LEARNER_RESUME",
        {"exam_track_id": "EXAM_2025_TCM_PHYSICIAN"},
    )
    try:
        with pytest.raises(ExamWorkspaceChangedError) as raised:
            await use_case.resume(
                thread_id,
                WorkflowResumeRequest(answer="中医执业医师资格考试"),
            )
    finally:
        reset_exam_workspace(token)

    assert raised.value.error_code == "exam_workspace_changed"
    assert raised.value.checkpoint_scope == "__legacy__"
    assert raised.value.current_scope == "EXAM_2025_TCM_PHYSICIAN"
    assert thread_id not in use_case._continuations
    assert orchestrator.abandoned_thread_id == thread_id
    state = use_case.get_run_state(thread_id)
    assert state["status"] == "failed"
    assert state["error_code"] == "exam_workspace_changed"
    assert state["retryable"] is False
    assert state["continuation"] is None
    assert use_case.conversation_repository.list_sessions("LEARNER_RESUME") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["success", "partial", "second_interrupt", "pending_parent", "concurrent_parent", "concurrent_task", "unavailable", "exam_changed"])
async def test_parent_publication_refreshes_child_baseline_before_generation(tmp_path, scenario):
    from competition_app.agents.learning_plan_service import LearningPlanServiceAdapter, LearningStateChangedError
    from competition_app.services.current_learning_state import CurrentLearningStateReader
    from competition_app.runtime.tool_registry import ToolRegistry

    plans = SimpleNamespace(long_term_plan=None, short_term_plan=None, learning_task=None)
    records = [986, *range(988, 1023)]
    state_reader = CurrentLearningStateReader(
        SimpleNamespace(get_current=lambda learner: plans),
        lambda *args, **kwargs: {"availability": "available", "books": [], "record_version": list(records)},
        SimpleNamespace(get_queue=lambda *args, **kwargs: SimpleNamespace(entries=[])),
    )
    token = bind_exam_workspace("LEARNER_RESUME", {"exam_track_id": "EXAM_TCM_LICENSED_PHARMACIST"})
    try:
        initial = state_reader.read()
        reads = []

        def read_latest():
            fresh = state_reader.read()
            if scenario == "unavailable":
                fresh["facts_availability"] = "unavailable"
            if scenario == "partial":
                fresh["facts_availability"] = "partial"
                fresh["availability"] = "partial"
            if scenario == "exam_changed":
                fresh["exam_track_id"] = "EXAM_OTHER"
            reads.append(fresh)
            return fresh

        tools = ToolRegistry()
        tools.register("get_current_learning_state", read_latest,
                       allowed_agents={"diagnosis_agent", "learning_plan_service"})
        resumed = []
        generated = []

        def publish(scope):
            layer = "long_term_plan" if scope == "long_term" else "short_term_plan"
            value = {"plan_id": f"PLAN_{scope}", "version": 1, "status": "active"}
            record = SimpleNamespace(**value, stages=[], textbook_selection=None,
                                     model_dump=lambda **kwargs: dict(value))
            setattr(plans, layer, record)
            if scenario == "concurrent_parent":
                setattr(plans, layer, SimpleNamespace(**{**value, "version": 2}, stages=[], textbook_selection=None))
            if scenario == "concurrent_task":
                plans.learning_task = SimpleNamespace(task_id="OTHER_TASK", version=1, model_dump=lambda **kwargs: {})
            return ExecutionResult(status="success", outputs={
                "learning_plan": SimpleNamespace(payload=SimpleNamespace(**{layer: record}))})

        class Orchestrator:
            tool_registry = tools

            async def execute(self, plan, context, *, thread_id):
                if context["plan_scope"] == "short_term":
                    return ExecutionResult(status="interrupted", interrupt={
                        "interrupt_type": "planning_prerequisite", "requested_scope": "long_term"})
                generated.append("long_term")
                return publish("long_term")

            async def resume(self, thread_id, resume_value, *, plan=None, context=None):
                resumed.append(thread_id)
                if scenario == "pending_parent":
                    return publish("short_term")
                assert context["current_long_term_plan"]["plan_id"] == "PLAN_long_term"
                assert context["current_learning_state"]["source_version"] != initial["source_version"]
                await LearningPlanServiceAdapter._validate_learning_state({**context, "tool_registry": tools})
                if scenario == "second_interrupt":
                    return ExecutionResult(status="interrupted", outputs={
                        "audit": SimpleNamespace(payload=SimpleNamespace(decision="pass", findings=[]))},
                        interrupt={"step_id": "learning_plan", "reason": "状态发生真实变化", "questions": ["读取最新状态后重新生成？"]})
                generated.append("short_term")
                return publish("short_term")

        use_case = PersonalizedReviewCardUseCase(Orchestrator(), SnapshotExporter(tmp_path))
        continuation = _WorkflowContinuation(
            request=ReviewCardRequest(learner_id="LEARNER_RESUME", user_request="建立必要父计划并安排今天任务"),
            case_id="CASE_RESUME", execution_id="EXE_RESUME",
            execution_plan=ExecutionPlan(plan_id="PLAN", task_type="learning_plan", steps=[ExecutionStep(step_id="diagnosis", agent="diagnosis_agent")]),
            planner_output=_planner_output(),
            context={"learner_id": "LEARNER_RESUME", "requested_plan_scope": "daily_task",
                     "task_type": "learning_plan", "current_learning_state": initial},
        )
        if scenario == "pending_parent":
            publish("long_term")
            continuation.context["current_long_term_plan"] = plans.long_term_plan.model_dump()
            continuation.context["current_learning_state"] = state_reader.read()
            continuation.context["pending_prerequisite"] = {
                "thread_id": "EXE_RESUME:prerequisite:short_term", "scope": "short_term",
                "confirmation": "已授权父计划", "interrupt": {"questions": ["补充情况？"]}}
        call = use_case._materialize_planning_prerequisite(
            thread_id="THREAD_TEST", continuation=continuation, answer="同意先建立父计划",
            persisted_messages=[], interrupt_payload={"requested_scope": "short_term", "original_scope": "daily_task"})
        if scenario in {"concurrent_parent", "concurrent_task", "unavailable", "exam_changed"}:
            with pytest.raises(RuntimeError, match="未生成"):
                await call
            assert resumed == []
            assert continuation.context["current_learning_state"] is initial
        elif scenario == "second_interrupt":
            with pytest.raises(_PrerequisiteInterrupted) as raised:
                await call
            assert raised.value.interrupt["reason"] == "状态发生真实变化"
            assert raised.value.thread_id == "EXE_RESUME:prerequisite:short_term"
            assert raised.value.scope == "short_term"
        else:
            await call
            fresh = continuation.context["current_learning_state"]
            assert fresh["plan_versions"]["long_term_plan"]["id"] == "PLAN_long_term"
            assert fresh["plan_versions"]["short_term_plan"]["id"] == "PLAN_short_term"
            assert len(reads) == (1 if scenario == "pending_parent" else 3)
            if scenario in {"success", "partial"}:
                assert generated == ["long_term", "short_term"]
            # Actual changes after the child baseline still fail publication.
            records.append(1023)
            with pytest.raises(LearningStateChangedError):
                await LearningPlanServiceAdapter._validate_learning_state({**continuation.context, "tool_registry": tools})
    finally:
        reset_exam_workspace(token)


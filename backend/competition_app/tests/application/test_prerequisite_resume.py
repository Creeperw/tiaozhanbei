from __future__ import annotations

import pytest

from competition_app.agents.planner import PlannerDecision
from competition_app.application.personalized_review_card import (
    ExamWorkspaceChangedError,
    PersonalizedReviewCardUseCase,
    ReviewCardRequest,
    WorkflowResumeRequest,
    _WorkflowContinuation,
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
async def test_course_status_resume_skips_parent_materialization(tmp_path, monkeypatch) -> None:
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
        user_request="请安排今日任务。",
        plan_scope="daily_task",
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
            "requested_plan_scope": "daily_task",
            "plan_scope": "daily_task",
            "current_long_term_plan": {"plan_id": "LONG_1", "status": "active"},
            "current_short_term_plan": {"plan_id": "SHORT_1", "status": "active"},
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
                "requested_scope": "daily_task",
                "original_scope": "daily_task",
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
    assert orchestrator.resume_value["plan_scope"] == "daily_task"
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


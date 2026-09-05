from pathlib import Path
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.agents.common import envelope
from competition_app.application.container import ApplicationContainer
from competition_app.application.personalized_review_card import (
    ExamWorkspaceChangedError,
    WorkflowHumanReviewResult,
)
from competition_app.config import Settings
from competition_app.contracts.learning_plan import LearningPlanResult, LongTermPlan, LongTermPlanStage
from competition_app.contracts.resource import AuditResult
from competition_app.contracts.local_repair import RepairIssue


def test_review_card_api_runs_shared_use_case(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    client = TestClient(create_app(container, auth_required=False))

    response = client.post(
        "/api/v1/review-cards",
        json={"learner_id": "L1", "user_request": "生成理中丸复习卡", "available_minutes": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["resource_version"]["status"] == "published"
    assert body["review_task"]["primary_kp_id"] == "KP_FJ_018"


def test_review_card_api_preserves_bound_daily_task_item_id(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    class RecordingWorkshopRuntime:
        def __init__(self) -> None:
            self.calls = []

        def publish_agent_paper(self, learner_id, **kwargs):
            self.calls.append((learner_id, kwargs))
            return {"paper_id": "PAPER_BOUND", "status": "published"}

    runtime = RecordingWorkshopRuntime()
    container.review_card_use_case.workshop_runtime = runtime
    client = TestClient(create_app(container, auth_required=False))

    response = client.post(
        "/api/v1/review-cards",
        json={
            "learner_id": "L_BOUND",
            "user_request": "请围绕四君子汤生成一份60分钟练习试卷蓝图",
            "available_minutes": 60,
            "daily_task_item_id": "ITEM_BOUND",
        },
    )

    assert response.status_code == 200
    assert runtime.calls[0][0] == "L_BOUND"
    assert runtime.calls[0][1]["daily_task_item_id"] == "ITEM_BOUND"


def test_exam_constraints_bypass_planner_and_route_to_paper_generation(
    tmp_path: Path,
) -> None:
    """组卷表单携带 exam_constraints 时跳过 Planner 模型，直接进入组卷链路。"""
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    class ExplodingPlanner:
        """旁路生效时不应被调用；一旦被调用即让测试失败。"""

        def __init__(self) -> None:
            self.calls = 0

        async def run(self, context):
            self.calls += 1
            raise AssertionError("exam_constraints 组卷请求不应调用 Planner")

    planner = ExplodingPlanner()
    registry = container.review_card_use_case.orchestrator.agent_registry
    registry._agents["planner_agent"] = planner

    class RecordingWorkshopRuntime:
        def __init__(self) -> None:
            self.calls = []

        def publish_agent_paper(self, learner_id, **kwargs):
            self.calls.append((learner_id, kwargs))
            return {"paper_id": "PAPER_BYPASS", "status": "published"}

    runtime = RecordingWorkshopRuntime()
    container.review_card_use_case.workshop_runtime = runtime
    client = TestClient(create_app(container, auth_required=False))

    response = client.post(
        "/api/v1/review-cards",
        json={
            "learner_id": "L_BYPASS",
            "user_request": "请生成一份练习试卷",
            "available_minutes": 60,
            "exam_constraints": {
                "question_count": 5,
                "question_types": ["单选题"],
                "question_type_distribution": {"single_choice": 5},
                "answer_mode": "practice",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["task_type"] == "paper_generation"
    assert planner.calls == 0
    assert runtime.calls[0][0] == "L_BYPASS"


def test_workshop_smart_paper_stream_skips_planner_but_keeps_memory_and_diagnosis(
    tmp_path: Path,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    class ExplodingAgent:
        async def run(self, _context):
            raise AssertionError("dedicated smart-paper graph must not run this agent")

    registry = container.review_card_use_case.orchestrator.agent_registry
    registry._agents["planner_agent"] = ExplodingAgent()

    class RecordingWorkshopRuntime:
        def publish_agent_paper(self, learner_id, **kwargs):
            return {"paper_id": f"PAPER_{learner_id}", "status": "published"}

    container.review_card_use_case.workshop_runtime = RecordingWorkshopRuntime()
    client = TestClient(create_app(container, auth_required=False))
    with client.stream(
        "POST",
        "/api/v1/workshop/smart-papers/stream",
        json={
            "thread_id": "THREAD_SMART_PAPER_FIXED_001",
            "conversation_id": "THREAD_SMART_PAPER_FIXED_001",
            "learner_id": "L_SMART_FIXED",
            "user_request": "生成专项练试卷",
            "available_minutes": 60,
            "exam_constraints": {
                "question_count": 1,
                "question_type_distribution": {"single_choice": 1},
                "answer_mode": "practice",
                "paper_kind": "special",
                "topic": "四君子汤",
            },
        },
    ) as response:
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    assert response.status_code == 200
    assert events[-1]["event"] == "run_completed"
    started_steps = {
        event.get("step_id")
        for event in events
        if event.get("event") == "step_started"
    }
    assert "planner" not in started_steps
    assert {"memory", "diagnosis"}.issubset(started_steps)
    graph = next(event for event in events if event.get("event") == "graph_compiled")
    assert set(graph["levels"][0]) == {"memory", "diagnosis"}
    assert {node["step_id"] for node in graph["nodes"]} == {
        "memory",
        "diagnosis",
        "paper_blueprint",
        "question_pool",
        "paper_assembly",
        "audit",
    }


def test_paper_request_without_exam_constraints_still_uses_planner(
    tmp_path: Path,
) -> None:
    """未携带有效 exam_constraints 的组卷请求仍走 Planner 语义路由。"""
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    delegate = container.review_card_use_case.orchestrator.agent_registry.get(
        "planner_agent"
    )

    class CountingPlanner:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, context):
            self.calls += 1
            return await delegate.run(context)

    registry = container.review_card_use_case.orchestrator.agent_registry
    registry._agents["planner_agent"] = CountingPlanner()
    client = TestClient(create_app(container, auth_required=False))

    response = client.post(
        "/api/v1/review-cards",
        json={
            "learner_id": "L_PLANNER",
            "user_request": "请围绕四君子汤生成一份60分钟练习试卷蓝图",
            "available_minutes": 60,
        },
    )

    assert response.status_code == 200
    assert registry._agents["planner_agent"].calls == 1


def test_learning_path_api_projects_only_the_signed_in_users_plan(tmp_path: Path) -> None:
    container = ApplicationContainer.build(
        Settings(mode="stub"), snapshot_root=tmp_path, include_backend_handoff=False
    )
    client = TestClient(create_app(container, auth_required=True))
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "username": "path-owner",
            "display_name": "路径同学",
            "password": "correct-horse-2026",
        },
    )
    learner_id = registered.json()["user"]["user_id"]
    now = datetime.now(timezone.utc)
    container.review_card_use_case.plan_repository.save_current(
        learner_id,
        LearningPlanResult(
            generated_scope="long_term",
            long_term_plan=LongTermPlan(
                plan_id="LP_API_PATH",
                learner_id=learner_id,
                content="长期规划",
                version=1,
                status="active",
                created_at=now,
                updated_at=now,
                stages=[
                    LongTermPlanStage(
                        stage=1,
                        stage_name="基础阶段",
                        book=["《中医学基础》"],
                        goal="建立基础。",
                        duration_days=30,
                        schedule_summary=(
                            "使用《中医学基础》建立理论框架，形成笔记并闭卷验收。"
                        ),
                    )
                ],
            ),
        ),
    )

    root = client.get("/api/v1/learning-path")
    books = client.get(
        "/api/v1/learning-path/nodes",
        params={"parent_id": root.json()["nodes"][0]["node_id"]},
    )

    assert root.status_code == 200
    assert root.json()["schema_version"] == "1.0"
    assert root.json()["nodes"][0]["node_type"] == "stage"
    assert books.status_code == 200
    assert books.json()["nodes"][0]["node_type"] == "book"
    assert books.json()["nodes"][0]["title"] == "《中医学基础》"


def test_learning_path_api_returns_an_actionable_empty_state_before_planning(tmp_path: Path) -> None:
    container = ApplicationContainer.build(
        Settings(mode="stub"), snapshot_root=tmp_path, include_backend_handoff=False
    )
    client = TestClient(create_app(container, auth_required=True))
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "path-newcomer",
            "display_name": "新同学",
            "password": "correct-horse-2026",
        },
    )

    response = client.get("/api/v1/learning-path")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1.0",
        "learner_id": response.json()["learner_id"],
        "plan_ref": None,
        "parent_id": None,
        "parent_type": None,
        "current_node_id": None,
        "nodes": [],
        "offset": 0,
        "limit": 100,
        "total": 0,
        "has_more": False,
        "availability": "requires_long_term_plan",
        "message": "请先完成长期学习规划，再生成阶段、教材和知识点路径。",
    }


def test_current_learning_plan_api_returns_prose_and_structured_stages(
    tmp_path: Path,
) -> None:
    container = ApplicationContainer.build(
        Settings(mode="stub"), snapshot_root=tmp_path, include_backend_handoff=False
    )
    client = TestClient(create_app(container, auth_required=True))
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "username": "plan-reader",
            "display_name": "规划同学",
            "password": "correct-horse-2026",
        },
    )
    learner_id = registered.json()["user"]["user_id"]
    now = datetime.now(timezone.utc)
    container.learning_plan_service.plan_repository.save_current(
        learner_id,
        LearningPlanResult(
            generated_scope="long_term",
            long_term_plan=LongTermPlan(
                plan_id="LP_CURRENT_API",
                learner_id=learner_id,
                content="【最终目标】完成教材阶段学习。",
                version=1,
                status="active",
                created_at=now,
                updated_at=now,
                stages=[
                    LongTermPlanStage(
                        stage=1,
                        stage_name="基础阶段",
                        book=["《中医学基础》"],
                        goal="建立基础理论框架。",
                        duration_days=30,
                        schedule_summary=(
                            "使用《中医学基础》建立理论框架，形成笔记并闭卷验收。"
                        ),
                    )
                ],
            ),
        ),
    )

    response = client.get("/api/v1/learning-plans/current")

    assert response.status_code == 200
    body = response.json()
    assert body["long_term"]["content"].startswith("【最终目标】")
    assert body["long_term"]["structured"]["stages"] == [
        {
            "stage": 1,
            "stage_name": "基础阶段",
            "book": ["《中医学基础》"],
            "goal": "建立基础理论框架。",
            "duration_days": 30,
            "schedule_summary": (
                "使用《中医学基础》建立理论框架，形成笔记并闭卷验收。"
            ),
            "acceptance": [],
        }
    ]
    assert body["long_term"]["stage_progress"][0]["pass_rule"] == (
        "all_exit_evidence_verified"
    )


def test_review_queue_starts_only_after_question_completion(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    client = TestClient(create_app(container, auth_required=False))
    learner_id = "REVIEW_QUEUE_API_1"
    created = client.post(
        "/api/v1/review-cards",
        json={
            "learner_id": learner_id,
            "user_request": "生成四君子汤复习卡",
            "available_minutes": 10,
            "user_knowledge_state": [{
                "user_id": learner_id,
                "kp_id": "KP_FJ_001",
                "knowledge_mastery": 0.55,
                "answer_accuracy": 0.5,
                "forgetting_coefficient": 0.08,
                "kp_review_status": "到期",
                "calculated_at": "2026-07-18T12:00:00Z",
            }],
        },
    )
    assert created.status_code == 200
    assert created.json()["review_task"]["status"] == "awaiting_attempt"

    queue = client.get(f"/api/v1/learners/{learner_id}/review-queue")
    assert queue.status_code == 200
    assert queue.json()["entries"] == []

    container.review_service.ingest_question_attempts(
        learner_id=learner_id,
        attempts=[{
            "attempt_id": "KNOWLEDGE_QUESTION_ATTEMPT_1",
            "kp_ids": ["KP_FJ_001"],
            "is_correct": False,
            "score": 0,
            "answered_at": "2026-07-18T12:00:00Z",
        }],
    )
    admitted = client.get(f"/api/v1/learners/{learner_id}/review-queue").json()
    assert admitted["entries"][0]["memory_unit"]["source_attempt_id"] == "KNOWLEDGE_QUESTION_ATTEMPT_1"
    assert admitted["entries"][0]["task"] is None

    dispatched = client.post(
        f"/api/v1/learners/{learner_id}/review-queue/dispatch",
        json={"available_minutes": 10},
    )
    assert dispatched.status_code == 200
    task_id = dispatched.json()["review_task"]["review_task_id"]
    assert dispatched.json()["review_task"]["status"] == "bound"
    version_before = container.review_service.repository.get_memory_unit(
        learner_id, "KP_FJ_001"
    ).version

    feedback = client.post(
        f"/api/v1/review-tasks/{task_id}/attempts",
        json={
            "learner_id": learner_id,
            "outcome": "independent_correct",
            "attempt_id": "REVIEW_API_ATTEMPT_1",
        },
    )
    assert feedback.status_code == 200
    assert feedback.json()["memory_version_after"] == version_before + 1
    refreshed = client.get(f"/api/v1/learners/{learner_id}/review-queue").json()
    assert refreshed["active_task_count"] == 0


def test_due_review_dispatch_generates_and_pushes_resource(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    learner_id = "REVIEW_DISPATCH_API_1"
    container.review_service.ingest_knowledge_states(
        learner_id=learner_id,
        prompt_abstract="四君子汤",
        states=[{
            "user_id": learner_id,
            "kp_id": "KP_FJ_001",
            "knowledge_mastery": 0.5,
            "answer_accuracy": 0.5,
            "forgetting_coefficient": 0.08,
            "kp_review_status": "到期",
            "calculated_at": "2026-07-18T12:00:00Z",
        }],
    )
    container.review_service.ingest_question_attempts(
        learner_id=learner_id,
        attempts=[{
            "attempt_id": "DISPATCH_SOURCE_ATTEMPT_1",
            "kp_ids": ["KP_FJ_001"],
            "is_correct": False,
            "score": 0,
            "answered_at": "2026-07-18T12:00:00Z",
        }],
    )
    client = TestClient(create_app(container, auth_required=False))

    dispatched = client.post(
        f"/api/v1/learners/{learner_id}/review-queue/dispatch",
        json={"available_minutes": 10},
    )

    assert dispatched.status_code == 200
    assert dispatched.json()["resource_version"]["status"] == "published"
    queue = client.get(f"/api/v1/learners/{learner_id}/review-queue").json()
    assert queue["active_task_count"] == 1
    assert queue["awaiting_resource_count"] == 0


def test_learning_automation_pushes_due_resource_once(tmp_path: Path) -> None:
    container = ApplicationContainer.build(
        Settings(mode="stub"),
        snapshot_root=tmp_path,
        include_backend_handoff=False,
    )
    client = TestClient(create_app(container))
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "username": "automation-review-user",
            "password": "correct-horse-2026",
        },
    )
    learner_id = registered.json()["user"]["user_id"]
    container.review_service.ingest_knowledge_states(
        learner_id=learner_id,
        prompt_abstract="四君子汤",
        states=[{
            "user_id": learner_id,
            "kp_id": "KP_FJ_001",
            "knowledge_mastery": 0.5,
            "answer_accuracy": 0.5,
            "forgetting_coefficient": 0.08,
            "kp_review_status": "到期",
            "calculated_at": "2026-07-18T12:00:00Z",
        }],
    )
    container.review_service.ingest_question_attempts(
        learner_id=learner_id,
        attempts=[{
            "attempt_id": "AUTOMATION_SOURCE_ATTEMPT_1",
            "kp_ids": ["KP_FJ_001"],
            "is_correct": False,
            "score": 0,
            "answered_at": "2026-07-18T12:00:00Z",
        }],
    )

    first = client.post(
        "/api/v1/learning-automation/run",
        json={"days": 30, "available_minutes": 10},
    )
    second = client.post(
        "/api/v1/learning-automation/run",
        json={"days": 30, "available_minutes": 10},
    )

    assert first.status_code == 200
    assert first.json()["automation"]["status"] == "unavailable"
    assert first.json()["review_resource_push"]["status"] == "pushed"
    assert first.json()["review_queue"]["awaiting_resource_count"] == 0
    assert second.status_code == 200
    assert second.json()["review_resource_push"]["status"] == "empty"


def test_health_endpoint() -> None:
    container = ApplicationContainer.build(Settings(mode="stub"))
    response = TestClient(create_app(container, auth_required=False)).get("/health")
    assert response.json() == {
        "status": "ok",
        "mode": "stub",
        "chat_model": "StubChatModel",
        "embedding_model": "StubEmbeddingModel",
        "knowledge_source": "demo",
        "execution_engine": "langgraph",
    }


def test_retired_chat_and_demo_static_interfaces_are_not_served() -> None:
    container = ApplicationContainer.build(Settings(mode="stub"))
    client = TestClient(create_app(container, auth_required=False))

    for path in (
        "/chat/",
        "/chat/chat.js",
        "/demo/",
        "/demo/app.js",
        "/demo-app",
    ):
        assert client.get(path).status_code == 404


def test_api_rejects_time_budget_above_twenty_four_hours() -> None:
    container = ApplicationContainer.build(Settings(mode="stub"))
    client = TestClient(create_app(container, auth_required=False))

    response = client.post(
        "/api/v1/review-cards",
        json={
            "learner_id": "TIME_LIMIT_1",
            "user_request": "制定学习计划",
            "available_minutes": 1441,
        },
    )

    assert response.status_code == 422


def test_stream_api_emits_model_and_system_events_before_final_result(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    client = TestClient(create_app(container, auth_required=False))

    with client.stream(
        "POST",
        "/api/v1/review-cards/stream",
        json={
            "learner_id": "STREAM_1",
            "user_request": "生成四君子汤复习卡",
            "available_minutes": 15,
            "messages": [
                {"message_id": "STREAM_MSG_1", "role": "user", "content": "安排复习"}
            ],
        },
    ) as response:
        assert response.status_code == 200
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    event_names = [item["event"] for item in events]
    assert "step_started" in event_names
    assert "model_input" in event_names
    assert "model_delta" not in event_names
    assert "model_transport" in event_names
    assert "model_output" in event_names
    assert "system_output" in event_names
    assert "agent_output_started" in event_names
    assert "agent_output_delta" in event_names
    assert "agent_output_committed" in event_names
    output_phases = {
        item.get("output_phase")
        for item in events
        if item.get("event") in {
            "agent_output_started",
            "agent_output_delta",
            "agent_output_committed",
            "agent_output_replaced",
        }
    }
    assert output_phases <= {"working", "formal"}
    assert "formal" in output_phases
    assert "graph_compiled" in event_names
    assert "answer_started" in event_names
    assert "answer_delta" in event_names
    assert "answer_committed" in event_names
    assert event_names[-1] == "run_completed"
    assert events[-1]["result"]["status"] == "success"
    streamed_answer = "".join(
        str(item.get("delta") or "")
        for item in events
        if item["event"] == "answer_delta"
    )
    assert "<<REFS:" not in streamed_answer
    assert events[-1]["assistant_message"].startswith(streamed_answer.rstrip())
    sequences = [item["seq"] for item in events]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    graph = next(item for item in events if item["event"] == "graph_compiled")
    assert graph["engine"] == "langgraph"
    assert graph["levels"][0] == ["planner"]
    assert {node["step_id"] for node in graph["nodes"]} >= {
        "planner", "knowledge", "diagnosis", "audit"
    }
    assert any(edge["kind"] == "revision" for edge in graph["control_edges"])
    transport = next(item for item in events if item["event"] == "model_transport")
    assert "request_payload" not in transport
    assert "response_text" not in transport
    diagnosis_event = next(
        item for item in events
        if item["event"] == "model_input" and item["agent"] == "diagnosis_agent"
    )
    assert "raw_input" not in diagnosis_event
    terminal = events[-1]
    assert "model_trace" not in terminal["result"]
    assert "agent_outputs" not in terminal["result"]
    planner_agent_text = "".join(
        str(item.get("delta") or "")
        for item in events
        if item["event"] == "agent_output_delta"
        and item.get("agent") == "planner_agent"
    )
    assert planner_agent_text
    assert '"routing_reason"' in planner_agent_text
    assert '"task_type"' in planner_agent_text
    assert '"selected_agents"' in planner_agent_text


def test_stream_api_emits_waiting_human_review_as_a_normal_terminal_event(
    tmp_path: Path,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    async def wait_for_review(_request):
        return WorkflowHumanReviewResult(
            execution_id="EXE_REVIEW",
            task_type="general_learning_support",
            review=AuditResult(
                audit_result_id="AUDIT_REVIEW",
                decision="needs_human_review",
                findings=["实时信息来源需要人工核验。"],
            ),
        )

    container.review_card_use_case.execute = wait_for_review
    client = TestClient(create_app(container, auth_required=False))

    with client.stream(
        "POST",
        "/api/v1/review-cards/stream",
        json={
            "learner_id": "REVIEW_VIEW_1",
            "user_request": "今天天气怎样？",
            "available_minutes": 15,
        },
    ) as response:
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    assert response.status_code == 200
    assert events[-1]["event"] == "run_waiting_human_review"
    assert events[-1]["result"]["status"] == "waiting_human_review"
    assert "人工复核" in events[-1]["assistant_message"]
    assert "审核未能完成" not in events[-1]["assistant_message"]


def test_human_review_public_result_exposes_safe_paper_preview_not_audit_details(
    tmp_path: Path,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    async def wait_for_review(_request):
        return WorkflowHumanReviewResult(
            review_id="HR_PREVIEW",
            execution_id="EXE_PREVIEW",
            task_type="paper_generation",
            review=AuditResult(
                audit_result_id="AUDIT_PREVIEW",
                decision="needs_human_review",
                findings=["内部审核意见不应公开。"],
            ),
            preview={
                "artifact_type": "paper_draft",
                "title": "四君子汤练习",
                "question_count": 1,
                "questions": [{"question_id": "Q1", "stem": "组成是？"}],
                "can_answer": False,
            },
        )

    container.review_card_use_case.execute = wait_for_review
    client = TestClient(create_app(container, auth_required=False))
    with client.stream(
        "POST",
        "/api/v1/review-cards/stream",
        json={"learner_id": "L1", "user_request": "组卷"},
    ) as response:
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    public_result = events[-1]["result"]
    assert public_result["preview"]["title"] == "四君子汤练习"
    assert "review" not in public_result
    assert "内部审核意见" not in json.dumps(public_result, ensure_ascii=False)


def test_human_review_rejects_paper_without_publishing(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    thread_id = "THREAD_HUMAN_REJECT_001"
    container.review_card_use_case._remember_run(
        thread_id,
        {
            "status": "waiting_human_review",
            "thread_id": thread_id,
            "learner_id": "L1",
            "execution_id": "EXE_HUMAN_REJECT",
            "result": WorkflowHumanReviewResult(
                review_id="HR_REJECT",
                execution_id="EXE_HUMAN_REJECT",
                task_type="paper_generation",
                review=AuditResult(
                    audit_result_id="AUDIT_REJECT",
                    decision="needs_human_review",
                ),
            ),
        },
    )

    result = container.review_card_use_case.resolve_smart_paper_human_review(
        thread_id,
        action="reject",
        reviewer_id="ADMIN_1",
        note="题目内容需要重新整理",
    )

    assert result["status"] == "human_review_rejected"
    assert container.review_card_use_case.get_run_state(thread_id)["status"] == "human_review_rejected"


def test_human_review_cannot_override_deterministic_paper_blocker(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    thread_id = "THREAD_HUMAN_BLOCKER_001"
    container.review_card_use_case._remember_run(
        thread_id,
        {
            "status": "waiting_human_review",
            "thread_id": thread_id,
            "learner_id": "L1",
            "execution_id": "EXE_HUMAN_BLOCKER",
            "result": WorkflowHumanReviewResult(
                review_id="HR_BLOCKER",
                execution_id="EXE_HUMAN_BLOCKER",
                task_type="paper_generation",
                review=AuditResult(
                    audit_result_id="AUDIT_BLOCKER",
                    decision="needs_human_review",
                    structured_findings=[
                        RepairIssue(
                            issue_id="ISSUE_HARD_COUNT",
                            issue_type="paper_blueprint_mismatch",
                            message="题量硬约束未满足",
                            origin="deterministic",
                            blocking=True,
                        )
                    ],
                ),
            ),
        },
    )

    with pytest.raises(ValueError, match="硬约束"):
        container.review_card_use_case.resolve_smart_paper_human_review(
            thread_id,
            action="approve_publish",
            reviewer_id="ADMIN_1",
            note="人工确认发布",
        )


def test_human_review_can_publish_complete_paper_after_admin_confirmation(
    tmp_path: Path,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    thread_id = "THREAD_HUMAN_APPROVE_001"

    class HumanReviewAuditAgent:
        async def run(self, context):
            return envelope(
                context,
                "audit_agent",
                "audit_result",
                AuditResult(
                    audit_result_id="AUDIT_NEEDS_AUTHORITY",
                    decision="needs_human_review",
                    findings=["当前题目的权威口径需要管理员核验。"],
                    audit_report="结构化试卷完整，但语义口径需人工确认。",
                ),
            )

    class RecordingWorkshopRuntime:
        def __init__(self) -> None:
            self.calls = []

        def publish_agent_paper(self, learner_id, **kwargs):
            self.calls.append((learner_id, kwargs))
            return {"paper_id": "PAPER_HUMAN_APPROVED", "status": "published"}

    registry = container.review_card_use_case.orchestrator.agent_registry
    registry._agents["audit_agent"] = HumanReviewAuditAgent()
    runtime = RecordingWorkshopRuntime()
    container.review_card_use_case.workshop_runtime = runtime
    client = TestClient(create_app(container, auth_required=False))

    with client.stream(
        "POST",
        "/api/v1/workshop/smart-papers/stream",
        json={
            "thread_id": thread_id,
            "conversation_id": thread_id,
            "learner_id": "L_HUMAN_APPROVE",
            "user_request": "生成四君子汤专项练习",
            "available_minutes": 30,
            "exam_constraints": {
                "question_count": 1,
                "question_type_distribution": {"single_choice": 1},
                "answer_mode": "practice",
                "paper_kind": "special",
                "topic": "四君子汤组成",
            },
        },
    ) as response:
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    assert events[-1]["event"] == "run_waiting_human_review"
    assert runtime.calls == []

    approved = container.review_card_use_case.resolve_smart_paper_human_review(
        thread_id,
        action="approve_publish",
        reviewer_id="ADMIN_1",
        note="已核对教材口径，确认题目、答案与解析可以发布。",
    )

    assert approved.status == "success"
    assert len(runtime.calls) == 1
    assert runtime.calls[0][0] == "L_HUMAN_APPROVE"
    assert container.review_card_use_case.get_run_state(thread_id)["status"] == "completed"


def test_stream_api_does_not_expose_internal_failure_detail(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    async def fail_execution(_request):
        raise RuntimeError(
            "personalized review card execution failed: "
            "ValueError: 教材路线选择与系统已解析路线不一致"
        )

    container.review_card_use_case.execute = fail_execution
    client = TestClient(create_app(container, auth_required=False))

    with client.stream(
        "POST",
        "/api/v1/review-cards/stream",
        json={
            "learner_id": "FAILURE_VIEW_1",
            "user_request": "制定长期学习计划",
            "available_minutes": 15,
        },
    ) as response:
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    assert response.status_code == 200
    assert events[-1]["event"] == "run_failed"
    assert "请稍后重试" in events[-1]["message"]
    assert "ValueError" not in events[-1]["message"]
    assert "教材路线" not in events[-1]["message"]


def test_stream_api_exposes_safe_failure_classification(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    async def fail_execution(_request):
        raise TimeoutError("knowledge expansion timed out")

    container.review_card_use_case.execute = fail_execution
    client = TestClient(create_app(container, auth_required=False))

    with client.stream(
        "POST",
        "/api/v1/review-cards/stream",
        json={
            "learner_id": "FAILURE_CLASSIFICATION_1",
            "user_request": "制定短期学习计划",
            "available_minutes": 15,
        },
    ) as response:
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    failure = events[-1]
    assert response.status_code == 200
    assert failure["event"] == "run_failed"
    assert failure["error_code"] == "knowledge_timeout"
    assert failure["retryable"] is True
    assert failure["user_message"] == failure["message"]
    assert "knowledge expansion" not in failure["message"]


def test_stream_api_exposes_exam_workspace_change_as_safe_terminal_error(
    tmp_path: Path,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    async def fail_execution(_request):
        raise ExamWorkspaceChangedError(
            checkpoint_scope="__legacy__",
            current_scope="EXAM_2025_TCM_PHYSICIAN",
        )

    container.review_card_use_case.execute = fail_execution
    client = TestClient(create_app(container, auth_required=False))

    with client.stream(
        "POST",
        "/api/v1/review-cards/stream",
        json={
            "learner_id": "EXAM_WORKSPACE_CHANGED_1",
            "user_request": "继续旧的学习规划",
            "available_minutes": 15,
        },
    ) as response:
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    failure = events[-1]
    assert response.status_code == 200
    assert failure["event"] == "run_failed"
    assert failure["error_code"] == "exam_workspace_changed"
    assert failure["retryable"] is False
    assert failure["message"] == (
        "考试目标已切换，旧任务不能继续；请在当前考试下重新发起该请求。"
    )
    assert "__legacy__" not in json.dumps(failure, ensure_ascii=False)
    assert "EXAM_2025_TCM_PHYSICIAN" not in json.dumps(failure, ensure_ascii=False)


def test_run_status_exposes_safe_failed_terminal_state(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    thread_id = "THREAD_SAFE_FAILURE_STATUS_001"
    container.review_card_use_case._remember_run(
        thread_id,
        {
            "status": "failed",
            "thread_id": thread_id,
            "execution_id": "EXE_SAFE_FAILURE_STATUS_001",
            "message": "secret internal failure detail",
            "error_code": "knowledge_timeout",
            "error_type": "TimeoutError",
            "retryable": True,
            "failed_step": "knowledge",
        },
    )
    client = TestClient(create_app(container, auth_required=False))

    response = client.get(f"/api/v1/review-cards/runs/{thread_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "status": "failed",
        "thread_id": thread_id,
        "execution_id": "EXE_SAFE_FAILURE_STATUS_001",
        "task_type": None,
        "result": None,
        "message": "知识检索超时，已保存当前会话，请稍后重试。",
        "error_code": "knowledge_timeout",
        "error_type": "TimeoutError",
        "retryable": True,
        "failed_step": "knowledge",
        "interrupt": None,
    }
    assert "secret internal failure detail" not in json.dumps(payload, ensure_ascii=False)


def test_stream_api_interrupts_and_resumes_same_langgraph_thread(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    client = TestClient(create_app(container, auth_required=False))
    thread_id = "THREAD_API_INTERRUPT_001"
    original_request = "这个长期规划我不满意，重新计划一下"

    with client.stream(
        "POST",
        "/api/v1/review-cards/stream",
        json={
            "thread_id": thread_id,
            "learner_id": "API_INTERRUPT_1",
            "user_request": original_request,
            "user_profile": {
                "goals": {"type": "credential", "name": "中医执业医师"}
            },
            "long_term_plan": {"content": "原长期计划", "status": "active"},
            "short_term_plan": {"content": "原短期计划", "status": "active"},
        },
    ) as response:
        first_events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    assert response.status_code == 200
    assert first_events[-1]["event"] == "run_interrupted"
    assert "graph_interrupted" in {item["event"] for item in first_events}
    interrupted = first_events[-1]["result"]
    assert interrupted["status"] == "interrupted"
    assert interrupted["thread_id"] == thread_id
    assert interrupted["interrupt"]["step_id"] == "diagnosis"

    status = client.get(f"/api/v1/review-cards/runs/{thread_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "interrupted"

    with client.stream(
        "POST",
        f"/api/v1/review-cards/runs/{thread_id}/resume/stream",
        json={
            "answer": "长期规划改为一年内按基础、代表方和综合应用三个阶段推进。",
            "plan_scope": "long_term",
            "plan_change_context": {
                "original_request": original_request,
                "target_layers": ["long_term"],
                "change_details": "一年内按基础、代表方和综合应用三个阶段推进。",
                "expected_outcome": "每阶段都有验收标准。",
            },
        },
    ) as response:
        resumed_events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    assert response.status_code == 200
    resumed_names = [item["event"] for item in resumed_events]
    assert resumed_names[0] == "run_resumed"
    assert "graph_resume_requested" in resumed_names
    assert "graph_resumed" in resumed_names
    assert resumed_names[-1] == "run_completed"
    assert resumed_events[-1]["result"]["status"] == "success"
    assert "graph_compiled" not in resumed_names

    completed = client.get(f"/api/v1/review-cards/runs/{thread_id}").json()
    assert completed["status"] == "completed"
    assert completed["result"]["status"] == "success"


def test_stream_api_projects_learning_packet_without_exposing_it_to_browser(tmp_path: Path) -> None:
    # Raw model boundaries are intentionally opt-in. This test validates the
    # internal full-capture projection while separately asserting that the
    # browser event remains redacted.
    container = ApplicationContainer.build(
        Settings(mode="stub"), snapshot_root=tmp_path, trace_level="full"
    )
    client = TestClient(create_app(container, auth_required=False))

    with client.stream(
        "POST",
        "/api/v1/review-cards/stream",
        json={
            "learner_id": "USER_PACKET_1",
            "user_request": "生成四君子汤复习卡",
            "available_minutes": 15,
            "messages": [
                {"message_id": "PACKET_MSG_1", "role": "user", "content": "安排复习"}
            ],
            "user_profile": {"goals": {"long_term_goal": "长期目标"}},
            "learning_profile": {"current_status": {"status_code": "T1"}},
            "system_data": {"task_completion_rate": {"value": 0.6}},
            "user_knowledge_state": [{"kp_id": "KP_FJ_001", "kp_review_status": "due"}],
            "question_attempt": [{"question_id": "Q_1", "is_correct": False}],
        },
    ) as response:
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    public_diagnosis_event = next(
        item for item in events
        if item["event"] == "model_input" and item["agent"] == "diagnosis_agent"
    )
    assert "raw_input" not in public_diagnosis_event

    diagnosis_trace = next(
        item
        for item in container.review_card_use_case.model_trace_recorder.items
        if item.agent == "diagnosis_agent"
    )
    diagnosis_input = diagnosis_trace.raw_input["payload"]
    assert diagnosis_input["goals"]["long_term_goal"] == "长期目标"
    assert diagnosis_input["learning_evidence"]["current_status"]["status_code"] == "T1"
    assert diagnosis_input["learning_evidence"]["behavior_summary"]["task_completion_rate"]["value"] == 0.6
    assert "user_profile" not in diagnosis_input
    assert "user_knowledge_state" not in diagnosis_input
    assert "question_attempt" not in diagnosis_input


def test_stream_api_emits_knowledge_retrieval_content(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    client = TestClient(create_app(container, auth_required=False))

    with client.stream(
        "POST",
        "/api/v1/review-cards/stream",
        json={
            "learner_id": "KNOWLEDGE_VIEW_1",
            "user_request": "为四君子汤生成学习卡片",
            "available_minutes": 15,
        },
    ) as response:
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    retrieval = next(item for item in events if item["event"] == "knowledge_retrieval")
    assert retrieval["kp_query"] == "四君子汤"
    assert retrieval["question_query"] == "四君子汤 相关题目"
    assert retrieval["evidence_items"]
    assert "人参" in retrieval["evidence_items"][0]["content"]

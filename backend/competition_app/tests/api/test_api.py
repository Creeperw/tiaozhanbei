from pathlib import Path
import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.application.personalized_review_card import WorkflowHumanReviewResult
from competition_app.config import Settings
from competition_app.contracts.learning_plan import LearningPlanResult, LongTermPlan, LongTermPlanStage
from competition_app.contracts.resource import AuditResult


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
    assert "model_delta" in event_names
    assert "model_transport" in event_names
    assert "model_output" in event_names
    assert "system_output" in event_names
    assert "graph_compiled" in event_names
    assert event_names[-1] == "run_completed"
    assert events[-1]["result"]["status"] == "success"
    graph = next(item for item in events if item["event"] == "graph_compiled")
    assert graph["engine"] == "langgraph"
    assert graph["levels"][0] == ["planner"]
    assert {node["step_id"] for node in graph["nodes"]} >= {
        "planner", "knowledge", "diagnosis", "audit"
    }
    assert any(edge["kind"] == "revision" for edge in graph["control_edges"])
    transport = next(item for item in events if item["event"] == "model_transport")
    assert transport["request_payload"]["mode"] == "stub_or_non_http_model"
    assert transport["response_text"].startswith("{")
    diagnosis_input = next(
        item for item in events
        if item["event"] == "model_input" and item["agent"] == "diagnosis_agent"
    )["raw_input"]
    assert diagnosis_input["target_agent"] == "diagnosis_agent"
    assert set(diagnosis_input["payload"]) >= {
        "goals",
        "time_constraints",
        "learning_evidence",
        "default_route",
        "existing_plans",
        "plan_actions",
    }


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


def test_stream_api_projects_user_learning_packet_to_diagnosis_context(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
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

    diagnosis_input = next(
        item for item in events
        if item["event"] == "model_input" and item["agent"] == "diagnosis_agent"
    )["raw_input"]["payload"]
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

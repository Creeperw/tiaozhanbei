from pathlib import Path
import json
import re

from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings


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


def test_demo_page_and_framework_output_are_available(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    client = TestClient(create_app(container, auth_required=False))

    page = client.get("/demo/")
    assert page.status_code == 200
    assert "时珍智训 · 运行观测台" in page.text
    assert "一键测试数据" in page.text
    assert "data-preset=\"review_due\"" in page.text
    assert "长期学习计划" in page.text
    assert "短期计划" in page.text
    assert "未来 1–2 周计划" not in page.text
    assert "今日学习任务" in page.text
    assert 'id="graph-monitor"' in page.text
    assert 'id="graph-canvas"' in page.text
    assert 'id="review-queue-panel"' in page.text
    assert 'id="review-dispatch-button"' in page.text
    assert 'id="review-queue-button"' in page.text
    assert "本次动态执行图" in page.text
    assert "/demo/app.js" in page.text
    script = client.get("/demo/app.js")
    assert "题库知识点关联" in script.text
    assert "正式题库的 Bridge" not in script.text
    assert "graph_compiled" in script.text
    assert "renderExecutionGraph" in script.text
    assert "audit_revision_started" in script.text
    assert "step_retrying" in script.text
    assert "/review-queue" in script.text
    assert "independent_correct" in script.text
    assert "review-tasks/" in script.text
    assert "reviewQueueButton" in script.text
    stylesheet = client.get("/demo/styles.css")
    assert ".graph-node.retrying" in stylesheet.text
    assert ".graph-edge.revision" in stylesheet.text

    response = client.post(
        "/api/v1/review-cards",
        json={
            "learner_id": "DEMO_1",
            "user_request": "生成四君子汤复习卡",
            "available_minutes": 15,
            "messages": [
                {"message_id": "DEMO_MSG_1", "role": "user", "content": "安排一次复习"}
            ],
        },
    )
    assert response.status_code == 200
    producers = {item["producer"] for item in response.json()["agent_outputs"]}
    assert "planner_agent" in producers
    assert "diagnosis_agent" in producers
    assert "expert_agent" in producers
    assert "audit_agent" in producers
    model_trace = response.json()["model_trace"]
    assert {item["agent"] for item in model_trace} >= {
        "planner_agent",
        "diagnosis_agent",
        "expert_agent",
        "audit_agent",
    }
    assert all(item["raw_output"] is not None for item in model_trace)


def test_demo_exposes_independent_plan_inputs_and_submits_them_as_current_plans() -> None:
    container = ApplicationContainer.build(Settings(mode="stub"))
    client = TestClient(create_app(container, auth_required=False))

    page = client.get("/demo/")
    script = client.get("/demo/app.js")

    assert 'id="long-term-plan"' in page.text
    assert 'id="short-term-plan"' in page.text
    assert "长期规划" in page.text
    assert "短期计划" in page.text
    assert "preset.longPlan" in script.text
    assert "preset.shortPlan" in script.text
    assert "longTermPlanText || inlineLongTermPlanText" in script.text
    assert "currentLongTermPlan" in script.text
    assert "safePacket.long_term_plan" in script.text
    assert "safePacket.short_term_plan" in script.text


def test_demo_submits_plan_scope_and_only_renders_the_generated_layer() -> None:
    container = ApplicationContainer.build(Settings(mode="stub"))
    client = TestClient(create_app(container, auth_required=False))

    page = client.get("/demo/").text
    script = client.get("/demo/app.js").text

    assert '<script src="/chat/plan_scope.js?v=20260720.1"></script>' in page
    assert "const planScopeHint = inferPlanScope(requestText)" in script
    assert "const planScope = pendingPlanScope" in script
    assert "plan_scope: planScope" in script
    assert "plan_scope_hint: planScopeHint" in script
    assert "displayPlanLayer('.long-plan', planOutput.long_term_plan)" in script
    assert "displayPlanLayer('.short-plan', planOutput.short_term_plan)" in script
    assert "displayPlanLayer('.task-card', planOutput.learning_task)" in script
    assert "if (planOutput.long_term_plan)" in script
    assert "if (planOutput.short_term_plan)" in script
    assert "if (planOutput.learning_task)" in script
    assert "payload.long_term_plan?.version" in script
    assert "payload.short_term_plan?.version" in script
    assert "payload.learning_task?.status" in script


def test_demo_accepts_a_full_day_budget_without_implying_it_must_be_filled() -> None:
    container = ApplicationContainer.build(Settings(mode="stub"))
    client = TestClient(create_app(container, auth_required=False))

    page = client.get("/demo/")

    assert 'id="minutes"' in page.text
    assert 'max="1440"' in page.text
    assert "只表示本次任务可用上限，不要求安排满" in page.text


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


def test_demo_exposes_plan_clarification_panel_and_resubmits_context() -> None:
    container = ApplicationContainer.build(Settings(mode="stub"))
    client = TestClient(create_app(container, auth_required=False))

    page = client.get("/demo/")
    script = client.get("/demo/app.js")

    assert 'id="plan-clarification"' in page.text
    assert 'id="clarification-questions"' in page.text
    assert 'id="clarification-change-details"' in page.text
    assert 'id="clarification-submit"' in page.text
    assert "planOutput?.requires_clarification" in script.text
    assert "if (payload.requires_clarification)" in script.text
    assert "等待用户补充重规划信息" in script.text
    assert "plan_change_context:" in script.text
    assert "target_layers" in script.text
    apply_preset = re.search(
        r"function applyPreset\(name, \{ preserveRun = false \} = \{\}\) \{(?P<content>.*?)\n\}",
        script.text,
        re.DOTALL,
    )
    assert apply_preset is not None
    assert "pendingPlanChangeContext = null" in apply_preset.group("content")
    assert "Command(resume)" in script.text
    assert "run_interrupted" in script.text
    assert "restorePendingRun" in script.text
    assert "/resume/stream" in script.text


def test_demo_formal_plan_result_has_only_three_independent_layers() -> None:
    container = ApplicationContainer.build(Settings(mode="stub"))
    client = TestClient(create_app(container, auth_required=False))

    page = client.get("/demo/")
    script = client.get("/demo/app.js")

    assert page.status_code == 200
    assert script.status_code == 200
    result_grid = re.search(
        r'<div class="result-grid">(?P<content>.*?)</div>\s*</section>',
        page.text,
        re.DOTALL,
    )
    assert result_grid is not None
    formal_result = result_grid.group("content")
    assert formal_result.count("data-result-layer=") == 4
    assert "长期学习计划" in formal_result
    assert "短期计划" in formal_result
    assert "未来 1–2 周计划" not in formal_result
    assert "今日学习任务" in formal_result
    assert "复习调度" not in formal_result
    assert "学习产物" in formal_result
    assert "调试详情 · 系统处理后的数据" in page.text

    render_results = re.search(
        r"function renderResults\(body\) \{(?P<content>.*?)\n\}",
        script.text,
        re.DOTALL,
    )
    assert render_results is not None
    formal_renderer = render_results.group("content")
    assert "long_term_plan.content" in formal_renderer
    assert "short_term_plan.content" in formal_renderer
    assert "short_term_plan.short_term_focus" in formal_renderer
    assert "short_term_plan.textbook_selection" in formal_renderer
    assert "focusTypeLabels" in formal_renderer
    assert "learning_task.task_content" in formal_renderer
    assert "renderResourceResult" in formal_renderer
    assert "planning_route" not in formal_renderer
    assert "route_id" not in formal_renderer
    assert "source_id" not in formal_renderer
    assert "formula_version" not in formal_renderer


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

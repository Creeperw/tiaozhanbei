import json
import subprocess

from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings


def build_client() -> TestClient:
    return TestClient(
        create_app(
            ApplicationContainer.build(Settings(mode="stub")),
            auth_required=False,
        )
    )


def test_chat_page_is_a_separate_product_surface_from_demo() -> None:
    client = build_client()

    chat = client.get("/chat/")
    demo = client.get("/demo/")
    root = client.get("/", follow_redirects=False)

    assert chat.status_code == 200
    assert "岐黄学伴" in chat.text
    assert "时珍智训 · 运行观测台" not in chat.text
    assert "/chat/chat.css" in chat.text
    assert "/chat/chat.js" in chat.text

    assert demo.status_code == 200
    assert "时珍智训 · 运行观测台" in demo.text
    assert root.status_code == 200
    assert "时珍智训" in root.text


def test_chat_page_has_accessible_conversation_and_composer_landmarks() -> None:
    page = build_client().get("/chat/").text

    assert 'href="#conversation"' in page
    assert '<main id="conversation"' in page
    assert 'aria-live="polite"' in page
    assert 'id="message-form"' in page
    assert 'id="message-input"' in page
    assert 'id="send-button"' in page
    assert 'id="connection-status"' in page
    assert 'id="new-conversation"' in page


def test_chat_page_keeps_realtime_execution_graph_beside_the_conversation() -> None:
    page = build_client().get("/chat/").text

    assert 'class="chat-workspace"' in page
    assert 'id="execution-toggle"' in page
    assert 'id="execution-panel"' in page
    assert 'id="execution-graph"' in page
    assert 'id="execution-node-count"' in page
    assert 'id="execution-retry-count"' in page
    assert 'id="execution-revision-count"' in page
    assert 'id="execution-event-list"' in page
    assert "实时执行链路" in page


def test_chat_page_exposes_learning_cockpit_without_removing_execution_graph() -> None:
    page = build_client().get("/chat/").text
    script = build_client().get("/chat/chat.js").text
    stylesheet = build_client().get("/chat/chat.css").text

    for element_id in (
        "learning-toggle",
        "learning-panel",
        "metric-completion",
        "focus-start",
        "focus-end",
        "current-task-complete",
        "learning-trend",
        "weak-point-list",
        "learning-review-list",
        "execution-graph",
    ):
        assert f'id="{element_id}"' in page
    for endpoint in (
        "/api/v1/learning-context",
        "/learning-activity/tasks",
        "/learning-activity/focus-sessions",
        "/api/v1/learning-tasks/current/complete",
        "/api/v1/review-tasks/",
    ):
        assert endpoint in script
    assert "behavior_context_loaded" in script
    assert ".learning-panel" in stylesheet
    assert "body.learning-panel-open" in stylesheet


def test_chat_client_reuses_stream_api_and_restores_browser_session() -> None:
    script = build_client().get("/chat/chat.js")

    assert script.status_code == 200
    assert "/api/v1/review-cards/stream" in script.text
    assert "competition.chat.v2" in script.text
    assert "storageOwnerId" in script.text
    assert "competition:auth-ready" in script.text
    assert "localStorage.getItem" in script.text
    assert "localStorage.setItem" in script.text
    assert "messages:" in script.text
    assert "long_term_plan:" in script.text
    assert "short_term_plan:" in script.text
    assert "learning_task:" in script.text
    assert "run_completed" in script.text
    assert "run_interrupted" in script.text
    assert "requires_clarification" in script.text
    assert "AbortController" in script.text
    assert "pendingThreadId" in script.text
    assert "/resume/stream" in script.text
    assert "restoreLangGraphRun" in script.text


def test_chat_client_visualizes_langgraph_events_and_persists_the_trace() -> None:
    script = build_client().get("/chat/chat.js").text
    stylesheet = build_client().get("/chat/chat.css").text

    assert "executionTrace" in script
    assert "handleExecutionEvent(event)" in script
    assert "renderExecutionGraph" in script
    for event_name in (
        "graph_compiled",
        "step_started",
        "step_completed",
        "step_retrying",
        "graph_interrupted",
        "graph_resumed",
        "audit_revision_started",
        "run_completed",
        "run_failed",
    ):
        assert event_name in script
    assert ".execution-node.running" in stylesheet
    assert ".execution-node.completed" in stylesheet
    assert ".execution-edge.revision" in stylesheet
    assert "body.execution-mobile-open .execution-panel" in stylesheet


def test_chat_client_resumes_plan_clarification_with_required_context() -> None:
    script = build_client().get("/chat/chat.js").text

    assert "pendingClarification" in script
    assert "plan_change_context:" in script
    assert "original_request:" in script
    assert "target_layers:" in script
    assert "change_details:" in script
    assert "normalizePendingClarification" in script
    for short_term_phrase in ("今日", "今天", "每日", "任务", "近期"):
        assert short_term_phrase in script


def test_chat_keeps_structured_long_term_plan_stages_visible() -> None:
    script = build_client().get("/chat/chat.js").text

    assert "long_term_plan.stages" in script
    assert "stage.book" in script
    assert "阶段路线" in script


def test_chat_requests_and_persists_one_plan_layer_at_a_time() -> None:
    script = build_client().get("/chat/chat.js").text

    assert "/chat/plan_scope.js" in build_client().get("/chat/").text
    assert "plan_scope: planScope" in script
    assert "plan_scope_hint: inferredPlanScope" in script
    assert "plan.generated_scope === 'long_term'" in script
    assert "plan.generated_scope === 'short_term'" in script
    assert "state.shortTermPlan = {}" in script
    assert "state.learningTask = {}" in script


def test_chat_plan_scope_classifier_distinguishes_target_from_parent_plan() -> None:
    classifier = build_client().get("/chat/plan_scope.js")
    assert classifier.status_code == 200
    cases = [
        "我今天要学习些什么东西？",
        "今天学什么？",
        "再给我今天的任务",
        "请制定学习计划",
        "请制定中医执业医师考试长期规划",
        "请根据长期规划制定本周短期计划",
        "请结合我的学习状态和长期学习计划，给我制定短期学习计划。",
        "请根据短期计划安排今天的任务",
        "请制定本周学习任务",
        "请把长期规划和短期计划一起重做",
        "请解释四君子汤的配伍意义",
        (
            "请结合我的真实掌握状态和长期规划，给我一份短期规划。\n"
            "【最终目标】系统掌握方剂学。\n"
            "【资源预算】每日学习5小时。\n"
            "补充：我想考执业医师资格证。"
        ),
    ]
    node_script = (
        classifier.text
        + "\nconsole.log(JSON.stringify({requests: "
        + json.dumps(cases, ensure_ascii=False)
        + ".map(globalThis.inferPlanScope), answers: "
        + json.dumps(["长期规划", "短期计划", "当日任务", "可以"], ensure_ascii=False)
        + ".map(globalThis.inferPlanLayerAnswer)}));"
    )

    completed = subprocess.run(
        ["node", "-e", node_script],
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)
    assert result["requests"] == [
        "daily_task",
        "daily_task",
        "daily_task",
        "unspecified",
        "long_term",
        "short_term",
        "short_term",
        "daily_task",
        "short_term",
        "unspecified",
        None,
        "short_term",
    ]
    assert result["answers"] == ["long_term", "short_term", "daily_task", None]


def test_shared_plan_parser_extracts_a_complete_inline_long_term_plan() -> None:
    classifier = build_client().get("/chat/plan_scope.js")
    request = (
        "请结合长期规划制定短期计划。\n"
        "【最终目标】目标。\n"
        "【能力路径与阶段】路径。\n"
        "【阶段里程碑】里程碑。\n"
        "【资源预算】预算。\n"
        "【重规划条件】条件。\n"
        "【保温底线】底线。\n"
        "补充：我想考执业医师。"
    )
    node_script = (
        classifier.text
        + "\nconsole.log(JSON.stringify(globalThis.extractInlineLongTermPlan("
        + json.dumps(request, ensure_ascii=False)
        + ")));"
    )

    completed = subprocess.run(
        ["node", "-e", node_script],
        check=True,
        capture_output=True,
        text=True,
    )

    extracted = json.loads(completed.stdout)
    assert extracted.startswith("【最终目标】")
    assert "【保温底线】底线。" in extracted
    assert "补充：" not in extracted


def test_chat_styles_cover_responsive_focus_loading_and_error_states() -> None:
    stylesheet = build_client().get("/chat/chat.css")

    assert stylesheet.status_code == 200
    assert "@media" in stylesheet.text
    assert ":focus-visible" in stylesheet.text
    assert ".message--loading" in stylesheet.text
    assert ".message--error" in stylesheet.text
    assert "prefers-reduced-motion" in stylesheet.text
    assert "grid-template-columns: minmax(0, 1fr);" in stylesheet.text


def test_chat_renders_plan_markdown_as_safe_readable_elements() -> None:
    script = build_client().get("/chat/chat.js").text
    stylesheet = build_client().get("/chat/chat.css").text

    assert "function appendRichText" in script
    assert "document.createElement('table')" in script
    assert "document.createElement('h3')" in script
    assert ".rich-table" in stylesheet
    assert ".rich-heading" in stylesheet

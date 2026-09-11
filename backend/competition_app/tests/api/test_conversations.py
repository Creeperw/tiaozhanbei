from pathlib import Path

from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings


def client_for(tmp_path: Path) -> TestClient:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    return TestClient(create_app(container))


def register(client: TestClient, username: str) -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "correct-horse-2026"},
    )
    assert response.status_code == 201
    return response.json()["user"]


def test_conversation_crud_is_owned_by_authenticated_user(tmp_path: Path) -> None:
    app = create_app(
        ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    )
    alice = TestClient(app)
    bob = TestClient(app)
    register(alice, "conversation-alice")
    register(bob, "conversation-bob")

    created = alice.post("/api/v1/conversations", json={"title": "新对话"})
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    assert alice.get("/api/v1/conversations").json()[0]["id"] == conversation_id
    assert bob.get("/api/v1/conversations").json() == []

    renamed = alice.patch(
        f"/api/v1/conversations/{conversation_id}", json={"title": "方剂学复习"}
    )
    assert renamed.status_code == 200
    assert bob.patch(
        f"/api/v1/conversations/{conversation_id}", json={"title": "越权"}
    ).status_code == 404
    assert bob.delete(f"/api/v1/conversations/{conversation_id}").status_code == 404
    assert alice.delete(f"/api/v1/conversations/{conversation_id}").status_code == 200


def test_conversation_list_hides_system_sessions(tmp_path: Path) -> None:
    """系统会话不出现在侧边栏，且判定只看系统自有枚举。

    这里刻意让标题就是内部提示词原文：如果过滤还依赖标题文本，这条断言会失败。
    用提示词开头几个字去猜「这是不是机器生成的会话」既不可靠，也会把用户真实
    输入误判成系统会话。
    """
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    client = TestClient(create_app(container))
    user = register(client, "conversation-internal-review")
    repository = container.review_card_use_case.conversation_repository
    repository.create_session(
        "THREAD_DUE_REVIEW_INTERNAL_1",
        user["user_id"],
        "请为以下已到期知识点生成一张可立即学习的复习卡：四君子汤",
        source="system",
    )
    visible = client.post("/api/v1/conversations", json={"title": "方剂问答"}).json()

    sessions = client.get("/api/v1/conversations").json()

    assert [session["id"] for session in sessions] == [visible["id"]]


def test_built_in_wizard_session_is_created_hidden(tmp_path: Path) -> None:
    """学习路径规划向导在创建时就声明自己是系统会话。

    否则在首个工作流落库之前，它会以用户会话身份短暂出现在 AI 助手历史里；
    用户中途关掉页面、工作流从未落库时，这条会话就会永久留在侧边栏。
    """
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    client = TestClient(create_app(container))
    register(client, "conversation-wizard")

    wizard = client.post(
        "/api/v1/conversations",
        json={"title": "中医执业医师资格考试个性化学习路径", "source": "system"},
    ).json()
    visible = client.post("/api/v1/conversations", json={"title": "方剂问答"}).json()

    sessions = client.get("/api/v1/conversations").json()

    assert [session["id"] for session in sessions] == [visible["id"]]
    assert wizard["id"] not in [session["id"] for session in sessions]


def test_workflow_messages_are_persisted_under_conversation_not_run(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    register(client, "conversation-flow")
    conversation_id = client.post(
        "/api/v1/conversations", json={"title": "新对话"}
    ).json()["id"]

    response = client.post(
        "/api/v1/review-cards",
        json={
            "thread_id": "THREAD_CONVERSATION_FLOW_1",
            "conversation_id": conversation_id,
            "learner_id": "browser-placeholder",
            "user_request": "请讲解四君子汤",
        },
    )
    assert response.status_code == 200
    messages = client.get(
        f"/api/v1/conversations/{conversation_id}/messages"
    ).json()
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "请讲解四君子汤"
    assert messages[1]["content"]
    assert client.get("/api/v1/conversations").json()[0]["title"] == "请讲解四君子汤"


def test_conversation_history_returns_persisted_workflow_actions(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    client = TestClient(create_app(container))
    user = register(client, "conversation-actions")
    conversation_id = client.post(
        "/api/v1/conversations", json={"title": "组卷"}
    ).json()["id"]
    repository = container.review_card_use_case.conversation_repository
    repository.save_messages(
        conversation_id,
        user["user_id"],
        [{
            "role": "assistant",
            "content": "试卷已经生成。",
            "actions": [{
                "label": "开始答题",
                "destination": "workshop.paper",
                "params": {"paper_id": "PAPER_1"},
            }],
        }],
    )

    messages = client.get(
        f"/api/v1/conversations/{conversation_id}/messages"
    ).json()

    assert messages[0]["actions"] == [{
        "label": "开始答题",
        "destination": "workshop.paper",
        "params": {"paper_id": "PAPER_1"},
    }]


def test_conversation_history_gives_legacy_paper_messages_a_safe_fallback(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    client = TestClient(create_app(container))
    user = register(client, "conversation-legacy-paper")
    conversation_id = client.post(
        "/api/v1/conversations", json={"title": "旧组卷"}
    ).json()["id"]
    container.review_card_use_case.conversation_repository.save_messages(
        conversation_id,
        user["user_id"],
        [{
            "role": "assistant",
            "content": (
                "试卷已经完成组卷并通过审核。试卷正文已保存到学习工坊，"
                "请点击下方“开始答题”进入计时答题界面。"
            ),
        }],
    )

    message = client.get(
        f"/api/v1/conversations/{conversation_id}/messages"
    ).json()[0]

    assert message["actions"][0] == {
        "action_type": "navigate",
        "label": "前往试卷列表",
        "destination": "workshop.paper",
        "params": {},
    }


def test_conversation_history_returns_persisted_trace_events(tmp_path: Path) -> None:
    """Stream 工作流结束后，消息接口应返回随消息持久化的运行时事件。"""
    client = client_for(tmp_path)
    register(client, "conversation-trace")
    conversation_id = client.post(
        "/api/v1/conversations", json={"title": "执行轨迹"}
    ).json()["id"]

    with client.stream(
        "POST",
        "/api/v1/review-cards/stream",
        json={
            "thread_id": "THREAD_TRACE_EVENTS_1",
            "conversation_id": conversation_id,
            "learner_id": "browser-placeholder",
            "user_request": "请讲解四君子汤",
        },
    ) as response:
        assert response.status_code == 200
        assert response.iter_lines()

    messages = client.get(
        f"/api/v1/conversations/{conversation_id}/messages"
    ).json()
    assistant = next(
        item for item in messages if item["role"] == "assistant"
    )
    assert isinstance(assistant.get("trace_events"), list)
    assert any(
        event.get("event") == "graph_compiled"
        for event in assistant["trace_events"]
    )
    assert not any(
        event.get("event") == "model_delta"
        for event in assistant["trace_events"]
    )
    assert any(
        event.get("event") == "agent_output_delta" and event.get("delta")
        for event in assistant["trace_events"]
    )
    assert all(
        event.get("output_phase") == "formal"
        for event in assistant["trace_events"]
        if event.get("event", "").startswith("agent_output_")
    )
    forbidden_trace_fields = {
        "output_summary", "raw_input", "raw_output", "request_payload", "response_text"
    }
    assert all(
        forbidden_trace_fields.isdisjoint(event)
        for event in assistant["trace_events"]
    )

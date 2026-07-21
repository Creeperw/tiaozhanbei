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

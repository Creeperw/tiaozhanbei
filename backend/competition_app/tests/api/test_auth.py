from pathlib import Path

from fastapi.testclient import TestClient

from competition_app.api.app import SESSION_COOKIE, create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings


def build_client(tmp_path: Path) -> TestClient:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    return TestClient(create_app(container))


def register(client: TestClient, username: str) -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "display_name": f"{username}同学",
            "password": "correct-horse-2026",
        },
    )
    assert response.status_code == 201
    assert client.cookies.get(SESSION_COOKIE)
    return response.json()["user"]


def test_protected_pages_and_api_require_login(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    page = client.get("/chat/", follow_redirects=False)
    api = client.post(
        "/api/v1/review-cards",
        json={"learner_id": "forged", "user_request": "生成复习卡"},
    )

    assert page.status_code == 303
    assert page.headers["location"].startswith("/auth/?next=/chat/")
    assert api.status_code == 401
    assert client.get("/auth/").status_code == 200
    assert client.get("/health").status_code == 200


def test_register_login_me_and_logout(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    user = register(client, "LinStudent")

    current = client.get("/api/v1/auth/me")
    assert current.status_code == 200
    assert current.json()["user"] == user
    assert "password" not in str(current.json())

    duplicate = client.post(
        "/api/v1/auth/register",
        json={"username": "linstudent", "password": "another-password"},
    )
    assert duplicate.status_code == 409

    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401

    invalid = client.post(
        "/api/v1/auth/login",
        json={"username": "LinStudent", "password": "wrong-password"},
    )
    assert invalid.status_code == 401
    logged_in = client.post(
        "/api/v1/auth/login",
        json={"username": "linstudent", "password": "correct-horse-2026"},
    )
    assert logged_in.status_code == 200
    assert logged_in.json()["user"]["user_id"] == user["user_id"]


def test_authenticated_identity_overrides_payload_and_isolates_users(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    app = create_app(container)
    alice_client = TestClient(app)
    bob_client = TestClient(app)
    alice = register(alice_client, "alice2026")
    bob = register(bob_client, "bob2026")

    created = alice_client.post(
        "/api/v1/review-cards",
        json={
            "learner_id": bob["user_id"],
            "user_request": "生成四君子汤复习卡",
            "available_minutes": 10,
            "user_profile": {"user_id": bob["user_id"], "user_name": "伪造身份"},
            "user_knowledge_state": [{
                "user_id": bob["user_id"],
                "kp_id": "KP_FJ_001",
                "knowledge_mastery": 0.5,
                "answer_accuracy": 0.5,
                "forgetting_coefficient": 0.08,
                "kp_review_status": "到期",
                "calculated_at": "2026-07-18T12:00:00Z",
            }],
        },
    )
    assert created.status_code == 200
    task = created.json()["review_task"]
    assert task["learner_id"] == alice["user_id"]

    own_queue = alice_client.get(
        f"/api/v1/learners/{alice['user_id']}/review-queue"
    )
    other_queue = bob_client.get(
        f"/api/v1/learners/{alice['user_id']}/review-queue"
    )
    forged_path = alice_client.get(
        f"/api/v1/learners/{bob['user_id']}/review-queue"
    )
    assert own_queue.status_code == 200
    assert own_queue.json()["learner_id"] == alice["user_id"]
    assert other_queue.status_code == 403
    assert forged_path.status_code == 403

    cross_user_feedback = bob_client.post(
        f"/api/v1/review-tasks/{task['review_task_id']}/attempts",
        json={"learner_id": alice["user_id"], "outcome": "independent_correct"},
    )
    assert cross_user_feedback.status_code == 403


def test_langgraph_run_state_is_owned_by_authenticated_user(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    app = create_app(container)
    alice_client = TestClient(app)
    bob_client = TestClient(app)
    alice = register(alice_client, "runowner")
    register(bob_client, "runintruder")
    thread_id = "THREAD_AUTH_OWNER_2026"

    with alice_client.stream(
        "POST",
        "/api/v1/review-cards/stream",
        json={
            "thread_id": thread_id,
            "learner_id": "untrusted-input",
            "user_request": "生成四君子汤复习卡",
        },
    ) as response:
        list(response.iter_lines())
    assert response.status_code == 200

    owner_state = alice_client.get(f"/api/v1/review-cards/runs/{thread_id}")
    intruder_state = bob_client.get(f"/api/v1/review-cards/runs/{thread_id}")
    collision = bob_client.post(
        "/api/v1/review-cards/stream",
        json={
            "thread_id": thread_id,
            "learner_id": alice["user_id"],
            "user_request": "覆盖会话",
        },
    )
    assert owner_state.status_code == 200
    assert owner_state.json()["learner_id"] == alice["user_id"]
    assert intruder_state.status_code == 404
    assert collision.status_code == 409

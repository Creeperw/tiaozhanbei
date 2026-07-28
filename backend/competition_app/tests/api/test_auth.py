from pathlib import Path
from io import BytesIO
import struct

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


def test_current_user_readiness_monitoring_and_review_contracts(tmp_path: Path) -> None:
    container = ApplicationContainer.build(
        Settings(mode="stub"),
        snapshot_root=tmp_path,
        include_backend_handoff=False,
    )
    client = TestClient(create_app(container))
    user = register(client, "ContractStudent")

    readiness = client.get("/api/v1/planning/readiness?scope=long_term")
    monitoring = client.get("/api/v1/learning-monitoring/snapshot?days=7")
    queue = client.get("/api/v1/review-queue")

    assert readiness.status_code == 200
    assert readiness.json()["status"] == "needs_profile"
    assert monitoring.status_code == 200
    assert monitoring.json()["evidence_status"] == "insufficient"
    assert queue.status_code == 200
    assert queue.json()["learner_id"] == user["user_id"]
    assert queue.json()["admission_policy"] == "completed_graded_kp_question_v1"


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


def test_formal_frontend_assets_are_public_but_business_api_stays_protected(
    tmp_path: Path,
) -> None:
    frontend_root = tmp_path / "frontend"
    assets_root = frontend_root / "assets"
    covers_root = frontend_root / "textbook-covers"
    status_icons_root = frontend_root / "textbook-status-icons"
    acupuncture_root = frontend_root / "acupuncture"
    assets_root.mkdir(parents=True)
    covers_root.mkdir(parents=True)
    status_icons_root.mkdir(parents=True)
    acupuncture_root.mkdir(parents=True)
    (frontend_root / "index.html").write_text(
        '<div id="root"></div><script src="/assets/app.js"></script>',
        encoding="utf-8",
    )
    (frontend_root / "favicon.ico").write_bytes(b"icon")
    (frontend_root / "favicon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"/>',
        encoding="utf-8",
    )
    (frontend_root / "hero_word.txt").write_text(
        "今天，从这里开始\n循序渐进",
        encoding="utf-8",
    )
    (assets_root / "app.js").write_text("window.loaded = true", encoding="utf-8")
    (assets_root / "app.css").write_text("body { color: green; }", encoding="utf-8")
    (covers_root / "方剂学.jpg").write_bytes(b"textbook-cover")
    (status_icons_root / "completed.svg").write_bytes(b"status-icon")
    (acupuncture_root / "body-front.jpg").write_bytes(b"acupuncture-image")
    container = ApplicationContainer.build(
        Settings(mode="stub", frontend_dist_root=frontend_root),
        snapshot_root=tmp_path / "snapshots",
    )

    with TestClient(create_app(container, auth_required=True)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/assets/app.css").status_code == 200
        cover = client.get("/textbook-covers/%E6%96%B9%E5%89%82%E5%AD%A6.jpg")
        assert cover.status_code == 200
        assert cover.content == b"textbook-cover"
        status_icon = client.get("/textbook-status-icons/completed.svg")
        assert status_icon.status_code == 200
        assert status_icon.content == b"status-icon"
        acupuncture = client.get("/acupuncture/body-front.jpg")
        assert acupuncture.status_code == 200
        assert acupuncture.content == b"acupuncture-image"
        assert client.get("/favicon.ico").status_code == 200
        assert client.get("/favicon.svg").status_code == 200
        assert client.get("/favicon.svg").headers["content-type"].startswith(
            "image/svg+xml"
        )
        hero_word = client.get("/hero_word.txt")
        assert hero_word.status_code == 200
        assert hero_word.text.splitlines() == ["今天，从这里开始", "循序渐进"]
        protected = client.post(
            "/api/v1/review-cards",
            json={"learner_id": "anonymous", "user_request": "生成复习卡"},
        )

    assert protected.status_code == 401


def test_platform_video_asset_is_public_and_uses_h264() -> None:
    app = create_app(ApplicationContainer.build(Settings(mode="stub")))
    video_path = (
        Path(__file__).resolve().parents[2]
        / "static"
        / "platform-assets"
        / "home"
        / "platform-agents.mp4"
    )

    with TestClient(app) as client:
        response = client.get("/platform-assets/home/platform-agents.mp4")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("video/mp4")
    assert response.content == video_path.read_bytes()
    assert b"avc1" in response.content
    assert b"hvc1" not in response.content


def test_register_login_me_and_logout(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    user = register(client, "LinStudent")
    assert user["role"] == "user"
    assert user["onboarding_required"] is True

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


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", 24, 24, 8, 2, 0, 0, 0) + b"\x00\x00\x00\x00"


def test_account_profile_and_avatar_are_private_to_the_current_user(tmp_path: Path) -> None:
    settings = Settings(
        mode="stub",
        use_sqlite=True,
        sqlite_path=tmp_path / "competition_app.sqlite3",
        avatar_dir=tmp_path / "avatars",
    )
    app = create_app(ApplicationContainer.build(settings, snapshot_root=tmp_path))
    alice_client = TestClient(app)
    bob_client = TestClient(app)
    alice = register(alice_client, "profile-alice")
    register(bob_client, "profile-bob")

    initial = alice_client.get("/api/v1/auth/me/profile")
    assert initial.status_code == 200
    assert initial.json()["profile"]["display_name"] == alice["display_name"]
    assert initial.json()["profile"]["avatar_url"] is None

    saved = alice_client.patch(
        "/api/v1/auth/me/profile",
        json={
            "display_name": "艾丽丝同学",
            "gender": "female",
            "birth_date": "2000-06-18",
            "region": "上海市",
            "contact_email": "alice@example.com",
            "signature": "循序精进。",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["user"]["display_name"] == "艾丽丝同学"
    assert saved.json()["profile"]["contact_email"] == "alice@example.com"

    uploaded = alice_client.put(
        "/api/v1/auth/me/avatar",
        files={"file": ("avatar.png", _png_bytes(), "image/png")},
    )
    assert uploaded.status_code == 200
    avatar_url = uploaded.json()["profile"]["avatar_url"]
    assert avatar_url and "v=1" in avatar_url
    avatar = alice_client.get(avatar_url)
    assert avatar.status_code == 200
    assert avatar.headers["content-type"].startswith("image/png")

    other_profile = bob_client.get("/api/v1/auth/me/profile")
    assert other_profile.status_code == 200
    assert other_profile.json()["profile"]["display_name"] != "艾丽丝同学"
    assert bob_client.get("/api/v1/auth/me/avatar").status_code == 404

    restarted = TestClient(create_app(ApplicationContainer.build(settings, snapshot_root=tmp_path / "restart")))
    restarted.cookies.set(SESSION_COOKIE, alice_client.cookies.get(SESSION_COOKIE))
    assert restarted.get("/api/v1/auth/me/profile").json()["profile"]["signature"] == "循序精进。"
    assert restarted.get("/api/v1/auth/me/avatar").status_code == 200


def test_account_profile_rejects_future_birth_date_and_invalid_avatar(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    register(client, "profile-validation")

    future = client.patch(
        "/api/v1/auth/me/profile",
        json={"display_name": "测试用户", "birth_date": "2999-01-01"},
    )
    assert future.status_code == 422
    assert "出生日期" in future.json()["detail"]

    invalid = client.put(
        "/api/v1/auth/me/avatar",
        files={"file": ("not-an-image.txt", b"not an image", "text/plain")},
    )
    assert invalid.status_code == 422


def test_workshop_favorites_and_notes_are_private_and_persistent(tmp_path: Path) -> None:
    settings = Settings(
        mode="stub",
        use_sqlite=True,
        sqlite_path=tmp_path / "competition_app.sqlite3",
    )
    app = create_app(ApplicationContainer.build(settings, snapshot_root=tmp_path))
    alice_client = TestClient(app)
    bob_client = TestClient(app)
    register(alice_client, "library-alice")
    register(bob_client, "library-bob")

    folder = alice_client.post(
        "/api/v1/workshop/favorite-folders", json={"name": "方剂重点"}
    )
    assert folder.status_code == 201
    folder_id = folder.json()["folder"]["folder_id"]
    notebook = alice_client.post(
        "/api/v1/workshop/note-folders", json={"name": "经方笔记"}
    )
    assert notebook.status_code == 201

    favorite = alice_client.post(
        "/api/v1/workshop/favorites",
        json={
            "folder_id": folder_id,
            "resource_type": "question",
            "resource_id": "Q_SIJUNZI",
            "title": "四君子汤的君药",
            "source": "智能组卷",
            "content": {
                "question_content": "四君子汤的君药是？",
                "standard_answer": ["A"],
                "explanation": "人参为君药。",
            },
        },
    )
    note = alice_client.post(
        "/api/v1/workshop/notes",
        json={
            "title": "四君子汤记忆",
            "content": "人参为君，白术为臣。",
            "note_type": "题目笔记",
            "source": "智能组卷",
            "resource_type": "question",
            "resource_id": "Q_SIJUNZI",
            "context": {
                "notebook": "经方笔记",
                "question_content": "四君子汤的君药是？",
            },
        },
    )
    assert favorite.status_code == 201
    assert note.status_code == 201
    favorite_id = favorite.json()["favorite"]["favorite_id"]
    note_id = note.json()["note"]["note_id"]

    assert bob_client.get("/api/v1/workshop/favorite-folders").json()["items"] == []
    assert bob_client.get("/api/v1/workshop/note-folders").json()["items"] == []
    assert bob_client.get("/api/v1/workshop/favorites").json()["items"] == []
    assert bob_client.get("/api/v1/workshop/notes").json()["items"] == []
    assert bob_client.delete(f"/api/v1/workshop/favorites/{favorite_id}").status_code == 404
    assert bob_client.put(
        f"/api/v1/workshop/notes/{note_id}",
        json={"title": "越权修改", "content": "不允许"},
    ).status_code == 404

    updated = alice_client.put(
        f"/api/v1/workshop/notes/{note_id}",
        json={"title": "四君子汤配伍", "content": "人参、白术、茯苓、炙甘草。"},
    )
    assert updated.status_code == 200
    assert updated.json()["note"]["title"] == "四君子汤配伍"

    restarted = TestClient(create_app(ApplicationContainer.build(
        settings, snapshot_root=tmp_path / "restart"
    )))
    restarted.cookies.set(SESSION_COOKIE, alice_client.cookies.get(SESSION_COOKIE))
    persisted_favorites = restarted.get("/api/v1/workshop/favorites").json()["items"]
    persisted_notes = restarted.get("/api/v1/workshop/notes").json()["items"]
    persisted_notebooks = restarted.get("/api/v1/workshop/note-folders").json()["items"]
    assert persisted_favorites[0]["content"]["standard_answer"] == ["A"]
    assert persisted_notes[0]["content"] == "人参、白术、茯苓、炙甘草。"
    assert len(persisted_notebooks) == 1
    assert persisted_notebooks[0]["name"] == "经方笔记"
    assert persisted_notebooks[0]["note_count"] == 1

    assert restarted.delete(f"/api/v1/workshop/favorites/{favorite_id}").status_code == 204
    assert restarted.delete(f"/api/v1/workshop/notes/{note_id}").status_code == 204
    assert restarted.delete(
        f"/api/v1/workshop/favorite-folders/{folder_id}"
    ).status_code == 204


def test_registration_onboarding_gate_is_persistent_until_completed(
    tmp_path: Path,
) -> None:
    client = build_client(tmp_path)
    user = register(client, "survey-required")
    assert user["onboarding_required"] is True

    completed = client.post("/api/v1/auth/onboarding/complete")

    assert completed.status_code == 200
    assert completed.json()["user"]["onboarding_required"] is False
    assert client.get("/api/v1/auth/me").json()["user"]["onboarding_required"] is False


def test_sqlite_auth_survives_container_rebuild(tmp_path: Path) -> None:
    settings = Settings(
        mode="stub",
        use_sqlite=True,
        sqlite_path=tmp_path / "competition_app.sqlite3",
    )
    first_client = TestClient(
        create_app(ApplicationContainer.build(settings, snapshot_root=tmp_path / "first"))
    )
    user = register(first_client, "persistent-student")
    session_cookie = first_client.cookies.get(SESSION_COOKIE)

    second_client = TestClient(
        create_app(ApplicationContainer.build(settings, snapshot_root=tmp_path / "second"))
    )
    second_client.cookies.set(SESSION_COOKIE, session_cookie)

    current = second_client.get("/api/v1/auth/me")
    assert current.status_code == 200
    assert current.json()["user"]["user_id"] == user["user_id"]


def test_configured_admin_is_bootstrapped_with_an_admin_role(tmp_path: Path) -> None:
    container = ApplicationContainer.build(
        Settings(
            mode="stub",
            admin_username="platform-admin",
            admin_default_password="admin-password-2026",
        ),
        snapshot_root=tmp_path,
    )
    client = TestClient(create_app(container))

    logged_in = client.post(
        "/api/v1/auth/login",
        json={"username": "platform-admin", "password": "admin-password-2026"},
    )

    assert logged_in.status_code == 200
    assert logged_in.json()["user"]["role"] == "admin"


def test_legacy_bearer_auth_endpoints_cannot_create_a_second_identity(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    unauthenticated = client.post("/token", data={"username": "x", "password": "y"})
    assert unauthenticated.status_code == 401

    user = register(client, "cookie-owner")
    retired = client.post("/register", json={})
    compatibility_me = client.get("/users/me")

    assert retired.status_code == 410
    assert compatibility_me.status_code == 200
    assert compatibility_me.json()["id"] == user["user_id"]


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

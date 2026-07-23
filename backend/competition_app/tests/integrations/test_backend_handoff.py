from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings
from competition_app.integrations.backend_handoff import (
    _explicit_profile_updates,
    _model_environment,
    _normalize_profile_memory_value,
)


def test_memory_agent_normalizes_legacy_instruction_shaped_learning_goal():
    assert _normalize_profile_memory_value(
        "learning_goal",
        "请结合我的学习状态，重新给我制定一份长期规划。我要考取中医执业医师资格证。",
    ) == "中医执业医师资格考试"


def test_memory_agent_drops_generic_planning_instruction_as_goal():
    assert _normalize_profile_memory_value(
        "learning_goal",
        "请结合我的学习状态，为我制定一份学习计划。",
    ) == ""


def test_explicit_profile_fallback_captures_current_turn_without_inference():
    assert _explicit_profile_updates(
        "请制定长期规划。我想考中医执业医师资格考试，目前零基础，"
        "我是计算机专业，每周可以学习4天、每天4小时。"
    ) == {
        "learning_goal": "中医执业医师资格考试",
        "learning_background": "零基础，计算机专业",
        "time_constraints": "每周可以学习4天，每天4小时",
    }


def test_explicit_profile_fallback_accepts_bare_exam_answer():
    assert _explicit_profile_updates("中医执业医师考试") == {
        "learning_goal": "中医执业医师资格考试"
    }


def test_explicit_profile_fallback_keeps_major_after_zero_basis_clause():
    updates = _explicit_profile_updates(
        "请制定长期计划。我是零基础，计算机专业，每周学习4天，每天2小时。"
    )

    assert updates["learning_background"] == "零基础，计算机专业"
    assert updates["time_constraints"] == "每周学习4天，每天2小时"


def test_explicit_profile_fallback_does_not_invent_missing_facts():
    assert _explicit_profile_updates("请结合我的学习状态制定长期规划") == {}


class FakeBackendHandoffRuntime:
    def __init__(self, question_attempts: list[dict] | None = None) -> None:
        self.app = FastAPI()
        self._started = False

        @self.app.get("/handoff-ping")
        async def ping():
            return {"source": "handoff"}

        @self.app.get("/handoff-identity")
        async def identity(request: Request):
            user = getattr(request.state, "current_user", None)
            return {
                "user_id": user.user_id if user is not None else None,
                "username": user.username if user is not None else None,
            }

        self.loaded_user_ids = []
        self.question_attempts = question_attempts or []
        self.profile_updates = []

    async def startup(self) -> None:
        self._started = True

    async def shutdown(self) -> None:
        self._started = False

    def status(self) -> dict[str, object]:
        return {
            "enabled": True,
            "mounted": True,
            "route_count": len(self.app.routes),
            "started": self._started,
        }

    def load_learning_context(self, external_user_id: str) -> dict:
        self.loaded_user_ids.append(external_user_id)
        return {
            "source": "frontend_backend",
            "calculated_at": "2026-07-21T08:00:00+08:00",
            "learning_profile": {"weak_kp_ids": ["KP_WEAK_1"]},
            "system_data": {"task_completion_rate": {"value": 0.5}},
            "learning_trends": {"days": 7, "series": []},
            "question_attempt": self.question_attempts,
        }

    def update_learning_profile(self, external_user_id: str, updates: dict, execution_id=None) -> dict:
        self.profile_updates.append((external_user_id, updates, execution_id))
        return dict(updates)

    def load_review_dashboard(self, external_user_id: str, *, history_limit: int = 100) -> dict:
        return {
            "schema_version": "1.0",
            "learner_id": external_user_id,
            "mastery": [{
                "kp_id": "KP_FJ_001", "kp_name": "四君子汤",
                "mastery_score": 82.0, "attempt_count": 2,
            }],
            "mastery_history": [{
                "history_id": "H_1", "kp_id": "KP_FJ_001",
                "kp_name": "四君子汤", "mastery_score": 82.0,
            }],
            "review_states": [],
            "review_tasks": [],
        }


def test_settings_parse_backend_handoff_configuration() -> None:
    settings = Settings.from_env(
        {
            "BACKEND_HANDOFF_ENABLED": "true",
            "BACKEND_HANDOFF_ROOT": "/tmp/backend-handoff",
            "BACKEND_HANDOFF_RUNTIME_ROOT": "/tmp/backend-runtime",
            "BACKEND_HANDOFF_MYSQL_DATABASE": "frontend_domain",
            "BACKEND_HANDOFF_SECRET_KEY": "test-secret",
        }
    )

    assert settings.backend_handoff_enabled is True
    assert settings.backend_handoff_root == Path("/tmp/backend-handoff")
    assert settings.backend_handoff_runtime_root == Path("/tmp/backend-runtime")
    assert settings.backend_handoff_mysql_database == "frontend_domain"
    assert settings.backend_handoff_secret_key == "test-secret"


def test_handoff_uses_only_the_main_model_stack_and_disables_voice() -> None:
    settings = Settings(
        chat_base_url="https://main-model.test/v1",
        chat_model="deepseek-v4-flash",
        embedding_model="Qwen/Qwen3-Embedding-4B",
        dashscope_api_key="secret-for-test",
    )

    environment = _model_environment(settings)

    assert environment["LLM_API_BASE_URL"] == settings.chat_base_url
    assert environment["LLM_API_MODEL"] == settings.chat_model
    assert environment["PLANNER_EXECUTOR_MODEL"] == settings.chat_model
    assert environment["MANAGER_REVIEWER_MODEL"] == settings.chat_model
    assert environment["EMBEDDING_MODEL_ID"] == settings.embedding_model
    assert environment["EMBEDDING_MODE"] == "disabled"
    assert environment["VOICE_MODE"] == "disabled"


def test_delivered_routes_require_the_main_cookie_boundary() -> None:
    container = ApplicationContainer.build(Settings())
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime
    app = create_app(container, auth_required=True)

    with TestClient(app) as client:
        handoff_response = client.get("/handoff-ping")
        protected_status = client.get("/api/v1/platform/status")

    assert handoff_response.status_code == 401
    assert protected_status.status_code == 401
    assert runtime._started is False


def test_platform_status_exposes_mounted_contract_when_auth_is_disabled() -> None:
    container = ApplicationContainer.build(Settings())
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime

    with TestClient(create_app(container, auth_required=False)) as client:
        response = client.get("/api/v1/platform/status")
        assert response.status_code == 200
        assert response.json()["mounted"] is True
        assert response.json()["started"] is True


def test_production_api_prefix_reaches_delivered_business_routes() -> None:
    container = ApplicationContainer.build(Settings())
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime

    with TestClient(create_app(container, auth_required=False)) as client:
        response = client.get("/api/handoff-ping")

    assert response.status_code == 200
    assert response.json() == {"source": "handoff"}


def test_formal_frontend_assistant_character_assets_are_mounted(tmp_path) -> None:
    asset_root = tmp_path / "assistant-character"
    asset_root.mkdir(parents=True)
    (asset_root / "avatar.png").write_bytes(b"frontend-avatar")
    container = ApplicationContainer.build(
        Settings(frontend_dist_root=tmp_path),
        snapshot_root=tmp_path / "snapshots",
    )

    with TestClient(create_app(container, auth_required=False)) as client:
        response = client.get("/assistant-character/avatar.png")

    assert response.status_code == 200
    assert response.content == b"frontend-avatar"


def test_formal_frontend_learning_stage_assets_are_mounted(tmp_path) -> None:
    asset_root = tmp_path / "learning-stage"
    asset_root.mkdir(parents=True)
    (asset_root / "foundation.png").write_bytes(b"learning-stage-artwork")
    container = ApplicationContainer.build(
        Settings(frontend_dist_root=tmp_path),
        snapshot_root=tmp_path / "snapshots",
    )

    with TestClient(create_app(container, auth_required=False)) as client:
        response = client.get("/learning-stage/foundation.png")

    assert response.status_code == 200
    assert response.content == b"learning-stage-artwork"


def test_main_cookie_identity_reaches_mounted_business_routes(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime

    with TestClient(create_app(container, auth_required=True)) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "username": "mounted-owner",
                "display_name": "集成同学",
                "password": "correct-horse-2026",
            },
        ).json()["user"]
        response = client.get("/handoff-identity")

    assert response.status_code == 200
    assert response.json() == {
        "user_id": registered["user_id"],
        "username": "mounted-owner",
    }


def test_learning_context_uses_authenticated_host_identity(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime

    with TestClient(create_app(container, auth_required=True)) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "username": "behavior-owner",
                "display_name": "行为同学",
                "password": "correct-horse-2026",
            },
        )
        user_id = registered.json()["user"]["user_id"]
        response = client.get("/api/v1/learning-context")

    assert response.status_code == 200
    assert response.json()["learner_id"] == user_id
    assert response.json()["learning_profile"]["weak_kp_ids"] == ["KP_WEAK_1"]
    assert runtime.loaded_user_ids == [user_id]


def test_learning_context_projects_completed_questions_into_review_queue_once(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    runtime = FakeBackendHandoffRuntime([{
        "attempt_id": "SERVER_QUESTION_ATTEMPT_1",
        "kp_ids": ["KP_FJ_001"],
        "knowledge_point_name": "四君子汤",
        "is_correct": True,
        "score": 100,
        "answered_at": "2026-07-21T08:00:00Z",
    }])
    container.backend_handoff_runtime = runtime

    with TestClient(create_app(container, auth_required=True)) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "username": "review-owner",
                "display_name": "复习同学",
                "password": "correct-horse-2026",
            },
        )
        user_id = registered.json()["user"]["user_id"]
        first = client.get("/api/v1/learning-context").json()
        version = first["review_queue"]["entries"][0]["memory_unit"]["version"]
        replay = client.get("/api/v1/learning-context").json()

    assert first["review_queue"]["entries"][0]["memory_unit"]["source_attempt_id"] == "SERVER_QUESTION_ATTEMPT_1"
    assert replay["review_queue"]["entries"][0]["memory_unit"]["version"] == version


def test_review_dashboard_combines_queue_mastery_and_history_for_current_user(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime

    with TestClient(create_app(container, auth_required=True)) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "username": "review-dashboard-owner",
                "display_name": "复习看板同学",
                "password": "correct-horse-2026",
            },
        )
        user_id = registered.json()["user"]["user_id"]
        response = client.get("/api/v1/review-dashboard")

    assert response.status_code == 200
    payload = response.json()
    assert payload["learner_id"] == user_id
    assert payload["mastery"][0]["kp_name"] == "四君子汤"
    assert payload["summary"]["average_mastery"] == 82.0
    assert payload["summary"]["history_count"] == 1

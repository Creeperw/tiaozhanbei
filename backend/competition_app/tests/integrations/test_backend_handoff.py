from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings


class FakeBackendHandoffRuntime:
    def __init__(self, question_attempts: list[dict] | None = None) -> None:
        self.app = FastAPI()
        self._started = False

        @self.app.get("/handoff-ping")
        async def ping():
            return {"source": "handoff"}

        self.loaded_user_ids = []
        self.question_attempts = question_attempts or []

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


def test_delivered_routes_are_mounted_without_parent_cookie_interception() -> None:
    container = ApplicationContainer.build(Settings())
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime
    app = create_app(container, auth_required=True)

    with TestClient(app) as client:
        handoff_response = client.get("/handoff-ping")
        protected_status = client.get("/api/v1/platform/status")

    assert handoff_response.status_code == 200
    assert handoff_response.json() == {"source": "handoff"}
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

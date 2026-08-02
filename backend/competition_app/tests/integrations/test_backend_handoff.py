from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings
from competition_app.integrations import backend_handoff
from competition_app.integrations.backend_handoff import (
    BackendHandoffRuntime,
    _explicit_profile_updates,
    _model_environment,
    _normalize_profile_memory_value,
)
from competition_app.services.profile_readiness import ProfileReadinessService


def test_memory_agent_normalizes_legacy_instruction_shaped_learning_goal():
    assert _normalize_profile_memory_value(
        "learning_goal",
        "请结合我的学习状态，重新给我制定一份长期规划。我要考取中医执业医师资格证。",
    ) == "中医执业医师资格考试"


def test_stable_paper_submission_injects_question_explanation_agent():
    calls = []

    class FakeDB:
        def close(self):
            calls.append("closed")

    db = FakeDB()
    explanation_runner = object()
    modules = {
        "APP.backend.database": SimpleNamespace(SessionLocal=lambda: db),
        "APP.backend.paper_submission_service": SimpleNamespace(
            submit_paper=lambda *args, **kwargs: (
                calls.append((args, kwargs)) or {"status": "completed"}
            )
        ),
        "APP.backend.expert_agent_service": SimpleNamespace(
            generate_question_explanation=explanation_runner
        ),
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: SimpleNamespace(id=7)

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        result = runtime.submit_paper("external-1", "PAPER_1", "request-1")

    assert result == {"status": "completed"}
    assert calls[0][0][1:] == (7, "PAPER_1", "request-1")
    assert calls[0][1]["explanation_runner"] is explanation_runner
    assert calls[-1] == "closed"


def test_daily_task_handoff_maps_user_and_rolls_back_failed_upsert():
    calls = []

    class FakeDB:
        def rollback(self):
            calls.append("rolled_back")

        def close(self):
            calls.append("closed")

    db = FakeDB()
    modules = {
        "APP.backend.database": SimpleNamespace(SessionLocal=lambda: db),
        "APP.backend.daily_task_progress_service": SimpleNamespace(
            upsert_daily_task_snapshot=lambda *_: (_ for _ in ()).throw(
                RuntimeError("storage unavailable")
            )
        ),
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: SimpleNamespace(id=7)

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        with pytest.raises(RuntimeError, match="storage unavailable"):
            runtime.upsert_daily_task_execution("external-1", {"task_id": "TASK_1"})

    assert calls == ["rolled_back", "closed"]


def test_memory_governance_persists_candidates_and_confirmed_replacement_atomically():
    calls = []

    class FakeDB:
        def add(self, item):
            calls.append(("add", item.event_type, item.user_id))

        def commit(self):
            calls.append("committed")

        def rollback(self):
            calls.append("rolled_back")

        def close(self):
            calls.append("closed")

    class FakeAgentEvent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    db = FakeDB()
    modules = {
        "APP.backend.database": SimpleNamespace(
            SessionLocal=lambda: db,
            AgentEvent=FakeAgentEvent,
        ),
        "APP.backend.health_memory": SimpleNamespace(
            save_extracted_memories=lambda *args, **kwargs: (
                calls.append(("candidates", args[1], args[2], kwargs))
                or {"non_important_candidates": args[2]["candidates"]}
            ),
            apply_confirmed_memory_replacements=lambda *args, **kwargs: (
                calls.append(("replace", args[1], args[2]))
                or {"replaced": [{"memory_id": 7, "successor_id": 8}]}
            ),
        ),
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: (
        calls.append(("user", current_db, external_id))
        or SimpleNamespace(id=23)
    )

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        result = runtime.persist_memory_governance(
            "external-23",
            execution_id="EXE_1",
            candidates=[{"summary": "用户长期偏好对比表式资源。"}],
            resolution="replace_existing",
            conflicts=[{"memory_id": 7, "proposed_memory": "每天学习一小时。"}],
        )

    assert calls[0] == ("user", db, "external-23")
    assert calls[1][0:2] == ("candidates", 23)
    assert calls[1][3]["commit"] is False
    assert calls[2] == (
        "replace",
        23,
        [{"memory_id": 7, "proposed_memory": "每天学习一小时。"}],
    )
    assert calls[-2:] == ["committed", "closed"]
    assert result["replaced"] == [{"memory_id": 7, "successor_id": 8}]


def test_memory_governance_rolls_back_if_replacement_fails():
    calls = []

    class FakeDB:
        def add(self, item):
            calls.append("added")

        def commit(self):
            calls.append("committed")

        def rollback(self):
            calls.append("rolled_back")

        def close(self):
            calls.append("closed")

    db = FakeDB()
    modules = {
        "APP.backend.database": SimpleNamespace(
            SessionLocal=lambda: db,
            AgentEvent=lambda **kwargs: kwargs,
        ),
        "APP.backend.health_memory": SimpleNamespace(
            save_extracted_memories=lambda *args, **kwargs: {},
            apply_confirmed_memory_replacements=lambda *args, **kwargs: (
                (_ for _ in ()).throw(ValueError("foreign memory"))
            ),
        ),
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: SimpleNamespace(id=23)

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        with pytest.raises(ValueError, match="foreign memory"):
            runtime.persist_memory_governance(
                "external-23",
                execution_id="EXE_1",
                candidates=[],
                resolution="replace_existing",
                conflicts=[{"memory_id": 99, "proposed_memory": "新值"}],
            )

    assert calls == ["rolled_back", "closed"]


def test_formal_knowledge_point_resolver_delegates_to_handoff_repository():
    calls = []

    class FakeDB:
        def close(self):
            calls.append("closed")

    db = FakeDB()
    modules = {
        "APP.backend.database": SimpleNamespace(SessionLocal=lambda: db),
        "APP.backend.daily_task_progress_service": SimpleNamespace(
            resolve_executable_knowledge_point=lambda current_db, name, **kwargs: (
                calls.append((current_db, name, kwargs)) or "KP_FORMAL_1"
            )
        ),
    }
    runtime = object.__new__(BackendHandoffRuntime)

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        result = runtime.resolve_executable_knowledge_point("四君子汤")

    assert result == "KP_FORMAL_1"
    assert calls[0] == (db, "四君子汤", {"required_question_count": 3})
    assert calls[-1] == "closed"


def test_publish_agent_paper_forwards_optional_daily_task_item_id():
    calls = []

    class FakeDB:
        def close(self):
            calls.append("closed")

    db = FakeDB()
    modules = {
        "APP.backend.database": SimpleNamespace(SessionLocal=lambda: db),
        "APP.backend.learning_workshop_service": SimpleNamespace(
            publish_agent_paper=lambda *args, **kwargs: (
                calls.append((args, kwargs)) or {"paper_id": "PAPER_1", "status": "published"}
            )
        ),
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: SimpleNamespace(id=7)

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        result = runtime.publish_agent_paper(
            "external-1",
            execution_id="EXEC_1",
            paper={},
            blueprint={},
            evidence_pack={},
            daily_task_item_id="ITEM_BOUND",
        )

    assert result == {"paper_id": "PAPER_1", "status": "published"}
    assert calls[0][1]["user_id"] == 7
    assert calls[0][1]["daily_task_item_id"] == "ITEM_BOUND"
    assert calls[-1] == "closed"


def test_knowledge_card_handoff_maps_external_user_for_list_get_and_save():
    calls = []

    class FakeDB:
        def close(self):
            calls.append("closed")

    db = FakeDB()
    service = SimpleNamespace(
        list_knowledge_cards=lambda *args, **kwargs: (
            calls.append(("list", args, kwargs)) or {"items": []}
        ),
        get_knowledge_card=lambda *args, **kwargs: (
            calls.append(("get", args, kwargs)) or {"card_id": "CARD_1"}
        ),
        upsert_knowledge_card=lambda *args, **kwargs: (
            calls.append(("save", args, kwargs)) or {"card_id": "CARD_1"}
        ),
    )
    modules = {
        "APP.backend.database": SimpleNamespace(SessionLocal=lambda: db),
        "APP.backend.learning_workshop_service": service,
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: SimpleNamespace(id=7)

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        assert runtime.list_knowledge_cards("external-1", offset=3, limit=5) == {
            "items": []
        }
        assert runtime.get_knowledge_card("external-1", "CARD_1") == {
            "card_id": "CARD_1"
        }
        assert runtime.save_knowledge_card(
            "external-1",
            kp_id="KP_1",
            title="阴阳学说",
            resource_bundle={"summary": "核心概念"},
            source_execution_id="EXEC_1",
        ) == {"card_id": "CARD_1"}

    assert calls[0] == ("list", (db,), {"user_id": 7, "offset": 3, "limit": 5})
    assert calls[2] == ("get", (db,), {"user_id": 7, "card_id": "CARD_1"})
    assert calls[4] == (
        "save",
        (db,),
        {
            "user_id": 7,
            "kp_id": "KP_1",
            "title": "阴阳学说",
            "resource_bundle": {"summary": "核心概念"},
            "source_execution_id": "EXEC_1",
        },
    )
    assert calls.count("closed") == 3


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


def test_registration_survey_becomes_agent_ready_profile_context():
    assert hasattr(backend_handoff, "_onboarding_profile_context")
    onboarding = {
        "status": "onboarding_completed",
        "survey_answers": {
            "learner_group": "academic",
            "learner_group_title": "学历教育群体",
            "major_or_role": "非医学专业",
            "tcm_foundation": "零基础",
            "learned_courses": [],
            "long_term_goal": "职业技能认证",
            "target_exam_or_course": "中医执业医师",
            "textbook_route_id": "textbook_tcm_physician",
            "textbook_route_version": 1,
            "daily_available_minutes": 45,
            "preferred_time_slot": "晚间",
            "resource_preference": ["知识卡片", "分阶测试题"],
        },
        "l0_baseline": {
            "stage_id": "L0",
            "major_or_role": "非医学专业",
            "target_exam_or_course": "中医执业医师",
            "textbook_route_id": "textbook_tcm_physician",
            "textbook_route_version": 1,
            "daily_available_minutes": 45,
        },
    }

    profile = backend_handoff._onboarding_profile_context(onboarding)

    assert profile["learning_goal"] == "中医执业医师"
    assert profile["learning_background"] == "零基础；非医学专业"
    assert profile["daily_available_minutes"] == 45
    assert profile["user_major_or_profession"] == "非医学专业"
    assert profile["goals"] == {
        "goal_type": "credential",
        "goal_name": "中医执业医师",
        "long_term_goal": "职业技能认证",
        "short_term_goal": "",
        "textbook_route_id": "textbook_tcm_physician",
        "textbook_route_version": 1,
    }
    readiness = ProfileReadinessService().evaluate(
        {"user_profile": profile},
        "long_term",
    )
    assert readiness.can_proceed is True
    assert readiness.questions == []


def test_multiscale_loaders_map_external_user_and_share_the_read_transaction():
    calls = []

    class FakeDB:
        def close(self):
            calls.append(("close", self))

    db = FakeDB()
    state = {
        "schema_version": "1.0",
        "learner_id": "7",
        "state_digest": "a" * 24,
    }
    candidates = {
        "schema_version": "1.0",
        "learner_id": "7",
        "scope": "daily_task",
        "items": [],
    }
    modules = {
        "APP.backend.database": SimpleNamespace(SessionLocal=lambda: db),
        "APP.backend.multiscale_learning_service": SimpleNamespace(
            verify_host_plan_context=lambda context, **kwargs: {
                **context,
                "verified_for": kwargs["external_user_id"],
            },
            build_multiscale_state=lambda current_db, user_id, **kwargs: (
                calls.append(("state", current_db, user_id, kwargs)) or state
            ),
            build_path_candidates=lambda current_db, user_id, **kwargs: (
                calls.append(("candidates", current_db, user_id, kwargs))
                or candidates
            ),
        ),
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: (
        calls.append(("user", current_db, external_id))
        or SimpleNamespace(id=7)
    )

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        loaded_state = runtime.load_multiscale_learning_state(
            "external-1",
            plan_context={"long_term_plan": {"plan_id": "LONG_1"}},
            window_days=30,
        )
        loaded_candidates = runtime.load_path_candidates(
            "external-1",
            plan_context={"short_term_plan": {"plan_id": "SHORT_1"}},
            scope="daily_task",
            limit=5,
            include_blocked=False,
        )

    assert loaded_state is state
    assert loaded_candidates is candidates
    assert calls[0] == ("user", db, "external-1")
    assert calls[1] == (
        "state",
        db,
        7,
        {
            "plan_context": {
                "long_term_plan": {"plan_id": "LONG_1"},
                "verified_for": "external-1",
            },
            "window_days": 30,
        },
    )
    assert calls[3] == ("user", db, "external-1")
    assert calls[4] == (
        "candidates",
        db,
        7,
        {
            "plan_context": {
                "short_term_plan": {"plan_id": "SHORT_1"},
                "verified_for": "external-1",
            },
            "scope": "daily_task",
            "limit": 5,
            "include_blocked": False,
        },
    )
    assert calls[2] == ("close", db)
    assert calls[5] == ("close", db)


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


def test_handoff_uses_main_model_stack_enables_embedding_and_disables_voice() -> None:
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
    assert environment["EMBEDDING_MODE"] == "enabled"
    assert environment["EMBEDDING_API_BASE_URL"] == settings.embedding_base_url
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


def test_formal_frontend_acupuncture_models_are_mounted(tmp_path) -> None:
    (tmp_path / "blender.yibiaozhu.glb").write_bytes(b"acupuncture-model")
    container = ApplicationContainer.build(
        Settings(frontend_dist_root=tmp_path),
        snapshot_root=tmp_path / "snapshots",
    )

    with TestClient(create_app(container, auth_required=True)) as client:
        response = client.get("/acupuncture-models/blender.yibiaozhu.glb")

    assert response.status_code == 200
    assert response.content == b"acupuncture-model"


def test_formal_frontend_textbook_cover_assets_are_mounted(tmp_path) -> None:
    asset_root = tmp_path / "textbook-covers"
    asset_root.mkdir(parents=True)
    (asset_root / "方剂学.jpg").write_bytes(b"textbook-cover")
    container = ApplicationContainer.build(
        Settings(frontend_dist_root=tmp_path),
        snapshot_root=tmp_path / "snapshots",
    )

    with TestClient(create_app(container, auth_required=False)) as client:
        response = client.get("/textbook-covers/%E6%96%B9%E5%89%82%E5%AD%A6.jpg")

    assert response.status_code == 200
    assert response.content == b"textbook-cover"


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

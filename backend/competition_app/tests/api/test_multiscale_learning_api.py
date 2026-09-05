from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings


class MultiscaleRuntime:
    def __init__(self) -> None:
        self.app = FastAPI()
        self.calls: list[tuple] = []

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def load_multiscale_learning_state(self, learner_id, **kwargs):
        self.calls.append(("state", learner_id, kwargs))
        return {
            "schema_version": "1.0",
            "state_id": "MSLS_1",
            "learner_id": learner_id,
            "generated_at": "2026-07-24T08:00:00+00:00",
            "macro": {},
            "meso": {},
            "micro": {
                "recent_attempts": [{"attempt_id": "ATTEMPT_PRIVATE"}],
                "question_accuracy": {
                    "available": False,
                    "value": None,
                    "unit": "ratio_0_1",
                    "source_refs": [],
                    "unavailable_reason": "no_question_attempts",
                },
            },
            "data_quality": {},
            "hard_constraints": [],
            "source_refs": [],
            "state_digest": "a" * 24,
        }

    def load_path_candidates(self, learner_id, **kwargs):
        self.calls.append(("candidates", learner_id, kwargs))
        return {
            "schema_version": "1.0",
            "learner_id": learner_id,
            "scope": kwargs["scope"],
            "generated_at": "2026-07-24T08:00:00+00:00",
            "state_digest": "a" * 24,
            "items": [],
            "counts": {
                "returned": 0,
                "eligible": 0,
                "blocked": 0,
                "due_reviews_considered": 0,
            },
            "scoring_policy": {},
        }


def _client(tmp_path: Path):
    container = ApplicationContainer.build(
        Settings(mode="stub"),
        snapshot_root=tmp_path,
        include_backend_handoff=False,
    )
    runtime = MultiscaleRuntime()
    container.backend_handoff_runtime = runtime
    client = TestClient(create_app(container))
    registered = client.post(
        "/api/v1/auth/register",
        json={"username": "multiscale-api", "password": "correct-horse-2026"},
    )
    return (
        client,
        container,
        runtime,
        registered.json()["user"]["user_id"],
    )


def test_multiscale_endpoint_returns_versioned_contract(tmp_path: Path) -> None:
    client, _, runtime, learner_id = _client(tmp_path)

    response = client.get("/api/v1/learning-state/multiscale?window_days=30")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "1.0"
    assert set(payload) >= {
        "state_id",
        "learner_id",
        "macro",
        "meso",
        "micro",
        "data_quality",
        "hard_constraints",
        "source_refs",
        "state_digest",
    }
    assert payload["learner_id"] == learner_id
    assert payload["micro"]["recent_attempts"] == []
    assert runtime.calls == [
        (
            "state",
            learner_id,
            {"plan_context": {}, "window_days": 30},
        )
    ]


def test_multiscale_endpoint_can_include_recent_events(tmp_path: Path) -> None:
    client, _, _, _ = _client(tmp_path)

    response = client.get(
        "/api/v1/learning-state/multiscale"
        "?window_days=7&include_recent_events=true"
    )

    assert response.status_code == 200
    assert response.json()["micro"]["recent_attempts"] == [
        {"attempt_id": "ATTEMPT_PRIVATE"}
    ]


def test_path_candidates_validate_scope_limit_and_window(tmp_path: Path) -> None:
    client, _, _, _ = _client(tmp_path)

    assert client.get(
        "/api/v1/learning-state/path-candidates?scope=unknown"
    ).status_code == 422
    assert client.get(
        "/api/v1/learning-state/path-candidates?scope=daily_task&limit=31"
    ).status_code == 422
    assert client.get(
        "/api/v1/learning-state/multiscale?window_days=14"
    ).status_code == 422


def test_path_candidates_use_authenticated_owner(tmp_path: Path) -> None:
    client, _, runtime, learner_id = _client(tmp_path)

    response = client.get(
        "/api/v1/learning-state/path-candidates"
        "?scope=short_term&limit=3&include_blocked=false"
    )

    assert response.status_code == 200
    assert response.json()["learner_id"] == learner_id
    assert runtime.calls == [
        (
            "candidates",
            learner_id,
            {
                "plan_context": {},
                "scope": "short_term",
                "limit": 3,
                "include_blocked": False,
            },
        )
    ]


def test_coordination_endpoint_filters_execution_and_learner_together(
    tmp_path: Path,
) -> None:
    client, container, _, learner_id = _client(tmp_path)
    repository = container.review_card_use_case.run_state_repository
    repository.save(
        "THREAD_OWN",
        {
            "execution_id": "EXE_OWN",
            "learner_id": learner_id,
            "coordination": {
                "schema_version": "1.0",
                "communication_trace": [
                    {
                        "schema_version": "1.0",
                        "handoff_id": "HANDOFF_1",
                        "step_id": "diagnosis",
                        "target_agent": "diagnosis_agent",
                        "fact_count": 2,
                        "evidence_count": 1,
                        "blocking_field_count": 0,
                        "omitted_categories": ["raw_conversation"],
                        "status": "consumed",
                        "created_at": "2026-07-24T08:00:00+00:00",
                    }
                ],
                "repair_trace": [],
            },
        },
    )
    repository.save(
        "THREAD_OTHER",
        {
            "execution_id": "EXE_OTHER",
            "learner_id": "OTHER_LEARNER",
            "coordination": {
                "schema_version": "1.0",
                "communication_trace": [],
                "repair_trace": [],
            },
        },
    )

    own = client.get("/api/v1/executions/EXE_OWN/coordination")
    other = client.get("/api/v1/executions/EXE_OTHER/coordination")
    missing = client.get("/api/v1/executions/EXE_MISSING/coordination")

    assert own.status_code == 200
    assert own.json()["schema_version"] == "1.0"
    assert own.json()["execution_id"] == "EXE_OWN"
    assert own.json()["communication_summary"]["total"] == 1
    assert "confirmed_facts" not in str(own.json())
    assert other.status_code == 404
    assert missing.status_code == 404


def test_new_endpoints_authenticate_before_runtime_or_repository(
    tmp_path: Path,
) -> None:
    _, container, runtime, _ = _client(tmp_path)
    anonymous = TestClient(create_app(container))

    responses = [
        anonymous.get("/api/v1/learning-state/multiscale"),
        anonymous.get(
            "/api/v1/learning-state/path-candidates?scope=daily_task"
        ),
        anonymous.get("/api/v1/executions/EXE_UNKNOWN/coordination"),
    ]

    assert [response.status_code for response in responses] == [401, 401, 401]
    assert runtime.calls == []

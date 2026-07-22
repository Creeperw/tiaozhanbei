from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings


class ActivityRuntime:
    def __init__(self) -> None:
        self.calls = []
        self.app = FastAPI()

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def load_learning_activity_summary(
        self,
        learner_id: str,
        *,
        days: int,
        recent_limit: int,
    ) -> dict:
        self.calls.append((learner_id, days, recent_limit))
        return {
            "schema_version": "1.0",
            "window_days": days,
            "calculated_at": "2026-07-21T20:00:00+08:00",
            "system_data": {"task_completion_rate": {"value": 0.5}},
            "trends": {
                "days": days,
                "series": [{"date": "2026-07-21", "focus_minutes": 10}],
                "calculated_at": "2026-07-21T20:00:00+08:00",
            },
            "counters": {},
            "recent_activities": [],
            "collection": {},
        }


def _client(tmp_path: Path) -> tuple[TestClient, ActivityRuntime]:
    container = ApplicationContainer.build(
        Settings(mode="stub"),
        snapshot_root=tmp_path,
        include_backend_handoff=False,
    )
    runtime = ActivityRuntime()
    container.backend_handoff_runtime = runtime
    client = TestClient(create_app(container))
    response = client.post(
        "/api/v1/auth/register",
        json={"username": "activity-reader", "password": "correct-horse-2026"},
    )
    assert response.status_code == 201
    return client, runtime


def test_activity_summary_and_trends_use_authenticated_user(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)

    summary = client.get("/api/v1/learning-activity/summary?days=7&recent_limit=5")
    trends = client.get("/api/v1/learning-activity/trends?days=90")

    assert summary.status_code == 200
    assert summary.json()["system_data"]["task_completion_rate"]["value"] == 0.5
    assert trends.status_code == 200
    assert trends.json()["days"] == 90
    assert runtime.calls[0][1:] == (7, 5)
    assert runtime.calls[1][1:] == (90, 1)
    assert runtime.calls[0][0] == runtime.calls[1][0]


def test_activity_summary_rejects_unsupported_window(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)

    response = client.get("/api/v1/learning-activity/summary?days=14")

    assert response.status_code == 422
    assert runtime.calls == []

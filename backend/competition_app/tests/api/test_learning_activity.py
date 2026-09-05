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
            "system_data": {
                "task_completion_rate": {"available": True, "value": 0.5},
                "daily_atomic_task_completion_rate": {
                    "available": True,
                    "value": 0.5,
                },
            },
            "trends": {
                "days": days,
                "series": [{"date": "2026-07-21", "focus_minutes": 10}],
                "calculated_at": "2026-07-21T20:00:00+08:00",
            },
            "counters": {
                "login": {
                    "events": 5,
                    "distinct_login_days": 3,
                    "checkin_days": 2,
                    "active_days": 4,
                },
                "focus_sessions": {"total": 2, "active_seconds": 750},
                "daily_task_items": {
                    "total": 4,
                    "completed": 2,
                    "pending": 2,
                },
            },
            "recent_activities": [],
            "collection": {},
        }

    def load_learning_statistics(
        self,
        learner_id: str,
        *,
        days: int,
    ) -> dict:
        self.calls.append(("statistics", learner_id, days))
        return {
            "schema_version": "1.0",
            "learner_id": learner_id,
            "window": {"days": days},
            "lifetime": {
                "questions_completed": 12,
                "unique_questions_completed": 9,
            },
            "current_window": {
                "questions_completed": 4,
                "unique_questions_completed": 3,
                "correct_answers": 3,
                "incorrect_answers": 1,
                "score_rate": 0.75,
                "paper_attempts_completed": 1,
                "active_mistakes": 1,
            },
            "metric_definitions": {},
            "counting_policy": {},
        }

    def get_checkin_status(self, learner_id: str, *, days: int) -> dict:
        self.calls.append(("checkin", learner_id, days))
        return {
            "checked_in_today": True,
            "streak": 2,
            "total_checkins": 6,
            "calendar_days": [],
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


def test_learning_statistics_uses_authenticated_user_and_validated_window(
    tmp_path: Path,
) -> None:
    client, runtime = _client(tmp_path)

    response = client.get("/api/v1/learning-statistics/overview?days=90")

    assert response.status_code == 200
    assert response.json()["lifetime"]["questions_completed"] == 12
    assert runtime.calls == [
        ("statistics", response.json()["learner_id"], 90),
    ]


def test_learning_statistics_rejects_unsupported_window(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)

    response = client.get("/api/v1/learning-statistics/overview?days=14")

    assert response.status_code == 422
    assert runtime.calls == []


def test_learning_metrics_overview_exposes_auditable_sources_and_formulas(
    tmp_path: Path,
) -> None:
    client, _runtime = _client(tmp_path)

    response = client.get("/api/v1/learning-metrics/overview?days=7")

    assert response.status_code == 200
    metrics = response.json()["metrics"]
    assert metrics["login_events"]["value"] == 5
    assert metrics["distinct_login_days"]["value"] == 3
    assert metrics["focus_seconds"]["value"] == 750
    assert metrics["daily_task_completion_rate"]["value"] == 0.5
    assert metrics["questions_completed"]["value"] == 4
    assert metrics["questions_completed_lifetime"]["value"] == 12
    assert metrics["checkin_streak"]["value"] == 2
    assert metrics["reviews_due"]["sources"] == ["canonical_review_memory"]
    assert metrics["daily_task_completion_rate"]["formula"]

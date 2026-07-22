from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings


class GovernanceRuntime:
    def __init__(self) -> None:
        self.app = FastAPI()
        self.calls = []

    async def startup(self):
        return None

    async def shutdown(self):
        return None

    def load_learning_insights(self, learner_id, **kwargs):
        self.calls.append(("insights", learner_id, kwargs))
        return {
            "schema_version": "1.0",
            "overview": {"stage_id": "T0", "stage_name": "稳定学习"},
            "dimensions": [],
            "activity_trends": {"series": []},
            "mastery_heatmap": [],
            "weak_points": [],
            "mistake_distribution": [],
            "data_quality": {},
        }

    def load_resource_match_report(self, learner_id, **kwargs):
        self.calls.append(("resources", learner_id, kwargs))
        return {"schema_version": "1.0", "target": {}, "summary": {}, "matches": []}

    def list_notifications(self, learner_id, **kwargs):
        self.calls.append(("notifications", learner_id, kwargs))
        return {"schema_version": "1.0", "unread_count": 1, "items": []}

    def update_notification_status(self, learner_id, notification_id, status):
        return {"notification_id": notification_id, "status": status, "learner_id": learner_id}

    def get_notification_preferences(self, learner_id):
        return {"in_app_enabled": True, "categories": {}, "learner_id": learner_id}

    def update_notification_preferences(self, learner_id, updates):
        return {**updates, "learner_id": learner_id}

    def list_interventions(self, learner_id, **kwargs):
        return {"schema_version": "1.0", "items": [], "learner_id": learner_id}

    def submit_intervention_feedback(self, learner_id, intervention_id, action, reason):
        return {"intervention_id": intervention_id, "action": action, "learner_id": learner_id}

    def list_plan_reviews(self, learner_id, **kwargs):
        return {"schema_version": "1.0", "items": [], "learner_id": learner_id}

    def run_plan_review(self, learner_id, **kwargs):
        return {"review_id": "REVIEW_1", "status": "proposal_pending", "learner_id": learner_id}

    def decide_plan_review(self, learner_id, review_id, decision):
        return {"review_id": review_id, "status": f"{decision}ed", "learner_id": learner_id}


def _client(tmp_path: Path):
    container = ApplicationContainer.build(
        Settings(mode="stub"), snapshot_root=tmp_path, include_backend_handoff=False
    )
    runtime = GovernanceRuntime()
    container.backend_handoff_runtime = runtime
    client = TestClient(create_app(container))
    registered = client.post(
        "/api/v1/auth/register",
        json={"username": "governance-api", "password": "correct-horse-2026"},
    )
    return client, runtime, registered.json()["user"]["user_id"]


def test_learning_governance_endpoints_use_authenticated_owner(tmp_path: Path) -> None:
    client, runtime, learner_id = _client(tmp_path)

    insights = client.get("/api/v1/learning-insights?days=7")
    resources = client.get("/api/v1/resource-match-report?limit=5")
    notifications = client.get("/api/v1/notifications?status=unread")
    updated = client.patch("/api/v1/notifications/NOTIF_1", json={"status": "read"})
    preferences = client.put(
        "/api/v1/notification-preferences",
        json={"digest_frequency": "daily", "categories": {"review_due": False}},
    )
    intervention = client.post(
        "/api/v1/interventions/3/feedback", json={"action": "postpone"}
    )
    review = client.post("/api/v1/plan-reviews/run")
    decision = client.post(
        "/api/v1/plan-reviews/REVIEW_1/decision", json={"decision": "accept"}
    )

    assert all(response.status_code == 200 for response in (
        insights, resources, notifications, updated, preferences,
        intervention, review, decision,
    ))
    assert runtime.calls[0][1] == learner_id
    assert runtime.calls[1][1] == learner_id
    assert updated.json()["learner_id"] == learner_id
    assert review.json()["learner_id"] == learner_id


def test_learning_insights_rejects_unsupported_window(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    assert client.get("/api/v1/learning-insights?days=14").status_code == 422

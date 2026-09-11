from pathlib import Path
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings
from competition_app.services.plan_review_replan import PlanReviewReplanCoordinator


class GovernanceRuntime:
    def __init__(self) -> None:
        self.app = FastAPI()
        self.calls = []
        self.feedback_commits = []

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

    def load_task_load_policy(self, learner_id, **kwargs):
        self.calls.append(("task_load", learner_id, kwargs))
        return {
            "schema_version": "1.0",
            "policy_id": "next-day-load-v1",
            "recommended_minutes": 25,
        }

    def record_resource_recommendation_event(self, learner_id, **kwargs):
        self.calls.append(("resource_event", learner_id, kwargs))
        return {**kwargs, "recorded": True}

    def load_resource_effectiveness_report(self, learner_id, **kwargs):
        self.calls.append(("resource_effectiveness", learner_id, kwargs))
        return {"schema_version": "1.0", "funnel": {}, "learning_outcomes": {}}

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

    def submit_intervention_feedback(
        self,
        learner_id,
        intervention_id,
        action,
        reason,
        *,
        commit=True,
        application_result=None,
    ):
        self.feedback_commits.append((commit, application_result))
        return {
            "intervention_id": intervention_id,
            "action": "安排错题复盘",
            "learner_id": learner_id,
            "lifecycle_status": "accepted" if commit else "delivered",
            "trigger_snapshot": {
                "final_recommendation": {
                    "execution_operation": "add_mistake_review",
                    "actionable": True,
                },
            },
        }

    def create_intervention_applied_notification(self, *args, **kwargs):
        return {"notification_id": "NOTIF_INTERVENTION"}

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
    return client, runtime, registered.json()["user"]["user_id"], container


def test_learning_governance_endpoints_use_authenticated_owner(tmp_path: Path) -> None:
    client, runtime, learner_id, _ = _client(tmp_path)

    insights = client.get("/api/v1/learning-insights?days=7")
    resources = client.get("/api/v1/resource-match-report?limit=5")
    task_load = client.get("/api/v1/task-load-policy")
    resource_event = client.post(
        "/api/v1/resource-recommendations/events",
        json={
            "event_type": "click",
            "recommendation_credential": "signed-recommendation-credential",
            "resource_id": "CARD_1",
            "resource_type": "knowledge_card",
            "kp_ids": ["KP_1"],
        },
    )
    effectiveness = client.get("/api/v1/resource-effectiveness?days=7")
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
        insights, resources, task_load, resource_event, effectiveness,
        notifications, updated, preferences,
        intervention, review, decision,
    ))
    assert runtime.calls[0][1] == learner_id
    assert runtime.calls[0][2]["review_projection"]["source"] == (
        "canonical_review_memory"
    )
    assert runtime.calls[0][2]["review_projection"]["due_count"] == 0
    assert runtime.calls[1][1] == learner_id
    assert runtime.calls[2][0] == "task_load"
    assert runtime.calls[2][2]["review_projection"]["source"] == (
        "canonical_review_memory"
    )
    assert runtime.calls[3][0] == "resource_event"
    assert runtime.calls[3][1] == learner_id
    assert runtime.calls[3][2]["recommendation_credential"] == "signed-recommendation-credential"
    assert runtime.calls[4][0] == "resource_effectiveness"
    assert updated.json()["learner_id"] == learner_id
    assert review.json()["learner_id"] == learner_id


def test_intervention_feedback_accept_reports_applied_payload(tmp_path: Path) -> None:
    client, runtime, _, _ = _client(tmp_path)

    intervention = client.post(
        "/api/v1/interventions/3/feedback", json={"action": "accept"}
    )
    assert intervention.status_code == 200
    payload = intervention.json()
    # stub 容器没有真实每日任务，落地应明确返回失败原因而不抛异常。
    assert payload["intervention_id"] == 3
    assert payload["applied"]["applied"] is False
    assert isinstance(payload["applied"]["reason"], str)
    assert payload["feedback_committed"] is False
    assert [commit for commit, _ in runtime.feedback_commits] == [False]


def test_intervention_accept_commits_after_apply_and_ignores_notification_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, runtime, _, _ = _client(tmp_path)
    monkeypatch.setattr(
        "competition_app.api.app.apply_accepted_intervention",
        lambda *args, **kwargs: {
            "applied": True,
            "already_applied": False,
            "retryable": False,
            "reason": "",
            "summary": "已安排错题复盘。",
            "title": "错题复盘：舌诊",
        },
    )
    runtime.create_intervention_applied_notification = lambda *args, **kwargs: (
        (_ for _ in ()).throw(RuntimeError("notification unavailable"))
    )

    response = client.post(
        "/api/v1/interventions/3/feedback",
        json={"action": "accept"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["feedback_committed"] is True
    assert payload["lifecycle_status"] == "accepted"
    assert payload["applied"]["applied"] is True
    assert payload["notification"]["reason"] == "notification_failed"
    assert [commit for commit, _ in runtime.feedback_commits] == [False, True]
    assert runtime.feedback_commits[1][1]["applied"] is True


def test_intervention_accept_commits_idempotent_already_applied_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, runtime, _, _ = _client(tmp_path)
    monkeypatch.setattr(
        "competition_app.api.app.apply_accepted_intervention",
        lambda *args, **kwargs: {
            "applied": False,
            "already_applied": True,
            "retryable": False,
            "reason": "该干预已经安排进今日任务。",
            "summary": "建议今天先完成舌诊错题复盘。",
            "title": "错题复盘：舌诊",
        },
    )

    response = client.post(
        "/api/v1/interventions/3/feedback",
        json={"action": "accept"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["feedback_committed"] is True
    assert payload["applied"]["already_applied"] is True
    assert payload["lifecycle_status"] == "accepted"
    assert [commit for commit, _ in runtime.feedback_commits] == [False, True]


def test_plan_review_decision_accept_reports_applied_payload(tmp_path: Path) -> None:
    client, _, _, _ = _client(tmp_path)

    decision = client.post(
        "/api/v1/plan-reviews/REVIEW_1/decision", json={"decision": "accept"}
    )
    assert decision.status_code == 200
    payload = decision.json()
    assert payload["review_id"] == "REVIEW_1"
    # Fake 复盘无 proposal，落地应明确返回无需落地而不抛异常。
    assert payload["applied"]["applied"] is False
    assert isinstance(payload["applied"]["reason"], str)


def test_short_replan_preclaim_transitions_and_ignores_notification_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, runtime, _, _ = _client(tmp_path)
    transitions = []
    notifications = []
    execution = {}
    execution_id_holder = [""]
    terminal = threading.Event()

    runtime.decide_plan_review = lambda learner_id, review_id, decision: {
        "review_id": review_id,
        "status": "accepted",
        "execution_status": "not_started",
        "execution": {},
        "proposal": {
            "target_layer": "short_term",
            "operation": "replan_for_low_completion",
            "requires_confirmation": True,
            "workflow_request": {
                "task_type": "learning_plan",
                "plan_scope": "short_term",
                "user_request": "请缩小短期计划范围。",
            },
        },
    }

    def claim(_learner_id, _review_id, *, execution_id):
        execution_id_holder[0] = execution_id
        execution.update({"execution_id": execution_id, "attempt": 1})
        transitions.append("queued")
        return {
            "review_id": _review_id,
            "status": "accepted",
            "execution_status": "queued",
            "execution": dict(execution),
            "proposal": {
                "target_layer": "short_term",
                "operation": "replan_for_low_completion",
                "workflow_request": {
                    "task_type": "learning_plan",
                    "plan_scope": "short_term",
                    "user_request": "请缩小短期计划范围。",
                },
            },
        }

    def update(_learner_id, _review_id, *, status, execution=None, execution_id=None):
        assert execution_id == execution_id_holder[0]
        transitions.append(status)
        return {"execution_status": status, "execution": execution or {}}

    def notify(_learner_id, *, review_id, status, summary=""):
        notifications.append(status)
        if status == "succeeded":
            terminal.set()
            raise RuntimeError("notification store unavailable")
        return {"notification_id": "NOTIF_1", "review_id": review_id}

    runtime.claim_plan_review_execution = claim
    runtime.update_plan_review_execution = update
    runtime.update_plan_review_lifecycle_notification = notify

    async def successful_run(self, learner_id, review):
        return {
            "short_term_plan_id": "SHORT_2",
            "short_term_version": 2,
            "daily_task_id": "TASK_2",
            "daily_task_version": 1,
            "invalidated_layers": ["daily_task"],
        }

    monkeypatch.setattr(PlanReviewReplanCoordinator, "run", successful_run)

    with client:
        response = client.post(
            "/api/v1/plan-reviews/REVIEW_SHORT/decision",
            json={"decision": "accept"},
        )
        assert terminal.wait(timeout=3)

    assert response.status_code == 200
    assert response.json()["execution_status"] == "queued"
    assert response.json()["proposal"]["target_layer"] == "short_term"
    assert transitions == ["queued", "running", "succeeded"]
    assert notifications == ["queued", "running", "succeeded"]


def test_short_replan_failure_notifies_once_and_retry_gets_new_owner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, runtime, _, _ = _client(tmp_path)
    attempts = 0
    current = {"status": "not_started", "execution": {}}
    transitions = []
    notifications = []
    terminal = threading.Event()

    def review_payload():
        return {
            "review_id": "REVIEW_FAIL_RETRY",
            "status": "accepted",
            "execution_status": current["status"],
            "execution": dict(current["execution"]),
            "proposal": {
                "target_layer": "short_term",
                "operation": "replan_for_low_completion",
                "workflow_request": {
                    "task_type": "learning_plan",
                    "plan_scope": "short_term",
                    "user_request": "请缩小短期计划范围。",
                },
            },
        }

    runtime.decide_plan_review = lambda *_args: review_payload()

    def claim(_learner_id, _review_id, *, execution_id):
        nonlocal attempts
        if current["status"] not in {"queued", "running", "succeeded"}:
            attempts += 1
            current.update({
                "status": "queued",
                "execution": {"execution_id": execution_id, "attempt": attempts},
            })
            transitions.append(("queued", execution_id, attempts))
        return review_payload()

    def update(_learner_id, _review_id, *, status, execution=None, execution_id=None):
        assert execution_id == current["execution"]["execution_id"]
        current["status"] = status
        current["execution"].update(execution or {})
        transitions.append((status, execution_id, attempts))
        if status == "failed":
            terminal.set()
        return review_payload()

    def notify(_learner_id, *, review_id, status, summary=""):
        notifications.append((status, review_id, summary))
        return {"notification_id": "NOTIF_RETRY", "review_id": review_id}

    runtime.claim_plan_review_execution = claim
    runtime.update_plan_review_execution = update
    runtime.update_plan_review_lifecycle_notification = notify

    async def failed_run(self, learner_id, review):
        raise RuntimeError("isolated coordinator failure")

    monkeypatch.setattr(PlanReviewReplanCoordinator, "run", failed_run)

    with client:
        first = client.post(
            "/api/v1/plan-reviews/REVIEW_FAIL_RETRY/decision",
            json={"decision": "accept"},
        )
        assert terminal.wait(timeout=3)
        first_execution_id = current["execution"]["execution_id"]
        terminal.clear()
        retry = client.post(
            "/api/v1/plan-reviews/REVIEW_FAIL_RETRY/decision",
            json={"decision": "accept"},
        )
        assert terminal.wait(timeout=3)

    assert first.status_code == 200
    assert retry.status_code == 200
    assert first.json()["execution_status"] == "queued"
    assert retry.json()["execution_status"] == "queued"
    assert attempts == 2
    assert current["execution"]["execution_id"] != first_execution_id
    assert [status for status, _, _ in notifications] == [
        "queued", "running", "failed", "queued", "running", "failed"
    ]
    assert not any(status == "succeeded" for status, _, _ in notifications)
    assert transitions[-1][0] == "failed"


def test_daily_reduce_load_ignores_stale_async_failure_state(tmp_path: Path) -> None:
    client, runtime, _, _ = _client(tmp_path)
    claims = []
    runtime.decide_plan_review = lambda learner_id, review_id, decision: {
        "review_id": review_id,
        "status": "accepted",
        "execution_status": "failed",
        "execution": {"execution_id": "STALE_ASYNC_EXECUTION", "error": "stale"},
        "proposal": {
            "target_layer": "daily_task",
            "operation": "reduce_load",
            "requires_confirmation": False,
        },
    }
    runtime.claim_plan_review_execution = lambda *args, **kwargs: claims.append(
        (args, kwargs)
    )

    with client:
        response = client.post(
            "/api/v1/plan-reviews/REVIEW_DAILY/decision",
            json={"decision": "accept"},
        )

    assert response.status_code == 200
    assert response.json()["applied"]["applied"] is False
    assert claims == []


def test_daily_reduce_load_accept_replay_is_applied_only_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, runtime, _, _ = _client(tmp_path)
    decisions = 0
    applications = []

    def decide(_learner_id, review_id, _decision):
        nonlocal decisions
        decisions += 1
        return {
            "review_id": review_id,
            "status": "accepted",
            "decision_replayed": decisions > 1,
            "execution_status": "not_started",
            "execution": {},
            "proposal": {
                "target_layer": "daily_task",
                "operation": "reduce_load",
            },
        }

    def apply_once(*args, **kwargs):
        applications.append((args, kwargs))
        return {
            "applied": True,
            "reason": "",
            "summary": "今日配套题已减负。",
        }

    runtime.decide_plan_review = decide
    monkeypatch.setattr(
        "competition_app.api.app.apply_accepted_plan_review",
        apply_once,
    )

    with client:
        first = client.post(
            "/api/v1/plan-reviews/REVIEW_DAILY_REPLAY/decision",
            json={"decision": "accept"},
        )
        replay = client.post(
            "/api/v1/plan-reviews/REVIEW_DAILY_REPLAY/decision",
            json={"decision": "accept"},
        )

    assert first.status_code == 200
    assert first.json()["applied"]["applied"] is True
    assert replay.status_code == 200
    assert replay.json()["decision_replayed"] is True
    assert replay.json()["applied"] == {
        "applied": False,
        "already_applied": True,
        "reason": "该调整已接受并处理，无需重复执行。",
        "summary": "",
    }
    assert len(applications) == 1




def test_learning_insights_rejects_unsupported_window(tmp_path: Path) -> None:
    client, _, _, _ = _client(tmp_path)
    assert client.get("/api/v1/learning-insights?days=14").status_code == 422


def test_learning_insights_does_not_create_assistant_review_card_task(tmp_path: Path) -> None:
    client, _, learner_id, container = _client(tmp_path)
    container.review_service.ingest_knowledge_states(
        learner_id=learner_id,
        prompt_abstract="四君子汤",
        states=[{
            "user_id": learner_id,
            "kp_id": "KP_FJ_001",
            "knowledge_mastery": 0.5,
            "answer_accuracy": 0.5,
            "forgetting_coefficient": 0.08,
            "kp_review_status": "到期",
            "calculated_at": "2026-07-18T12:00:00Z",
        }],
    )
    container.review_service.ingest_question_attempts(
        learner_id=learner_id,
        attempts=[{
            "attempt_id": "INSIGHTS_AUTO_PUSH_ATTEMPT_1",
            "kp_ids": ["KP_FJ_001"],
            "is_correct": False,
            "score": 0,
            "answered_at": "2026-07-18T12:00:00Z",
        }],
    )

    initial_queue = container.review_service.get_queue(learner_id).model_dump(
        mode="json"
    )
    assert initial_queue["awaiting_resource_count"] == 1
    with client:
        response = client.get("/api/v1/learning-insights?days=30")
        assert response.status_code == 200
        queue = container.review_service.get_queue(learner_id).model_dump(
            mode="json"
        )
        push_status = client.get("/api/v1/learning-automation/status").json()

    assert queue["active_task_count"] == 0
    assert queue["awaiting_resource_count"] == 1
    assert push_status["review_resource_push"]["status"] == "idle"

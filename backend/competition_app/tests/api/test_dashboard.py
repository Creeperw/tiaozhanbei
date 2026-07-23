from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings
from competition_app.contracts.learning_plan import LearningPlanResult, LearningTask, ShortTermPlan


def test_dashboard_home_is_derived_from_current_users_main_state(tmp_path: Path) -> None:
    app = create_app(
        ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    )
    alice = TestClient(app)
    bob = TestClient(app)
    alice.post(
        "/api/v1/auth/register",
        json={"username": "dashboard-alice", "password": "correct-horse-2026"},
    )
    bob.post(
        "/api/v1/auth/register",
        json={"username": "dashboard-bob", "password": "correct-horse-2026"},
    )
    conversation = alice.post(
        "/api/v1/conversations", json={"title": "四君子汤学习"}
    ).json()

    alice_home = alice.get("/api/v1/dashboard/home")
    bob_home = bob.get("/api/v1/dashboard/home")

    assert alice_home.status_code == 200
    assert alice_home.json()["continue_learning"][0]["id"] == conversation["id"]
    assert bob_home.json()["continue_learning"] == []
    assert {card["key"] for card in alice_home.json()["status_cards"]} == {
        "accuracy",
        "completion",
    }


def test_dashboard_projects_current_task_chapter_and_knowledge_card_actions(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    container.knowledge_backend = SimpleNamespace(
        map=SimpleNamespace(
            resolve_topic=lambda query, limit=1: [{
                "kp_id": "KP_SIJUNZI",
                "name": "四君子汤",
                "kp": {"kp_lv1": "方剂学", "kp_lv2": "补益剂·补气"},
            }]
        )
    )
    client = TestClient(create_app(container))
    registered = client.post(
        "/api/v1/auth/register",
        json={"username": "dashboard-task", "password": "correct-horse-2026"},
    ).json()
    learner_id = registered["user"]["user_id"]
    now = datetime.now(timezone.utc)
    short_plan = ShortTermPlan(
        plan_id="SHORT_DASHBOARD",
        learner_id=learner_id,
        long_term_plan_id="LONG_DASHBOARD",
        content="本周学习补气剂。",
        version=1,
        status="active",
        created_at=now,
        updated_at=now,
    )
    task = LearningTask(
        task_id="TASK_DASHBOARD",
        learner_id=learner_id,
        short_term_plan_id=short_plan.plan_id,
        task_type="daily_learning",
        task_content="学习四君子汤的组成、功用和配伍意义。",
        learning_chapter="《方剂学》补益剂·补气",
        focus_knowledge_points=["四君子汤"],
        estimated_minutes=25,
        expected_output="一份闭卷回忆记录。",
        completion_criteria="能够说出组成并解释配伍。",
        version=1,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    container.review_card_use_case.plan_repository.save_current(
        learner_id,
        LearningPlanResult(short_term_plan=short_plan, learning_task=task),
    )

    payload = client.get("/api/v1/dashboard/home").json()

    current = payload["current_learning_task"]
    assert current["learning_chapter"] == {
        "book": "方剂学",
        "title": "补益剂·补气",
        "source": "learning_task",
    }
    assert current["knowledge_cards"] == [{
        "kp_id": "KP_SIJUNZI",
        "title": "四君子汤",
        "book": "方剂学",
        "chapter": "补益剂·补气",
        "action": {
            "action_type": "navigate",
            "label": "学习知识卡",
            "destination": "workshop.knowledge_card",
            "params": {"kp_id": "KP_SIJUNZI"},
        },
    }]
    assert payload["daily_task_timer"]["policy"] == "rolling_24h"
    assert payload["daily_task_timer"]["available"] is True
    assert current["refresh_due_at"] is not None

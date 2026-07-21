from pathlib import Path

from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings


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

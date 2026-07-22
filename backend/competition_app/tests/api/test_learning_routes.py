from pathlib import Path

from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        ApplicationContainer.build(
            Settings(mode="stub"),
            snapshot_root=tmp_path,
            include_backend_handoff=False,
        )
    )
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/register",
        json={"username": "route-reader", "password": "correct-horse-2026"},
    )
    assert response.status_code == 201
    return client


def test_lists_non_personalized_approved_learning_routes(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/v1/learning-routes")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "1.0"
    assert payload["route_kind"] == "classic_reference"
    assert payload["personalized"] is False
    assert payload["total"] == 7
    assert all(item["status"] == "approved" for item in payload["items"])
    assert all(item["stage_count"] > 0 for item in payload["items"])
    assert all(item["detail_endpoint"].startswith("/api/v1/learning-routes/") for item in payload["items"])


def test_learning_route_detail_exposes_ordered_stages_books_and_sources(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    response = client.get("/api/v1/learning-routes/textbook_tcm_physician")

    assert response.status_code == 200
    payload = response.json()
    route = payload["route"]
    assert route["route_id"] == "textbook_tcm_physician"
    assert [stage["order"] for stage in route["stages"]] == list(
        range(1, len(route["stages"]) + 1)
    )
    assert all(stage["books"] for stage in route["stages"])
    assert payload["sources"]
    assert payload["navigation"]["atlas_route_id"] == "textbook_14_5"


def test_learning_routes_are_authenticated_and_unknown_route_is_404(
    tmp_path: Path,
) -> None:
    container = ApplicationContainer.build(
        Settings(mode="stub"),
        snapshot_root=tmp_path,
        include_backend_handoff=False,
    )
    anonymous = TestClient(create_app(container))
    assert anonymous.get("/api/v1/learning-routes").status_code == 401

    client = _client(tmp_path / "registered")
    assert client.get("/api/v1/learning-routes/missing").status_code == 404

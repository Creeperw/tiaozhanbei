from types import SimpleNamespace

from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings


def test_recognition_report_routes_use_authenticated_owner(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    calls = []
    container.knowledge_backend = SimpleNamespace(
        list_recognition_reports=lambda owner, offset=0, limit=20: calls.append(("list", owner, offset, limit)) or {
            "items": [], "total": 0, "offset": offset, "limit": limit, "has_more": False,
        },
        get_recognition_report=lambda owner, report_id: calls.append(("detail", owner, report_id)) or {
            "report_id": report_id, "read_only": True,
        },
    )
    client = TestClient(create_app(container, auth_required=True))
    register = client.post("/api/v1/auth/register", json={
        "username": "reviewer",
        "display_name": "审查用户",
        "password": "correct-horse-2026",
    })
    assert register.status_code == 201

    owner = str(register.json()["user"]["user_id"])
    page = client.get("/api/v1/knowledge/content/recognition-reports?offset=2&limit=500")
    detail = client.get("/api/v1/knowledge/content/recognition-reports/run-a")

    assert page.status_code == 200
    assert page.json()["limit"] == 100
    assert detail.status_code == 200
    assert calls == [("list", owner, 2, 100), ("detail", owner, "run-a")]

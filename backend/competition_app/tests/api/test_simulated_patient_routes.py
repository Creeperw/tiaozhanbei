from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from competition_app.api import simulated_patient_routes


class _Engine:
    def execute(self, request):
        return SimpleNamespace(
            session_id=request.session_id,
            action=request.action,
            success=True,
            data={"user_id": request.user_id},
            error=None,
            is_complete=False,
            turn_count=0,
            help_available=False,
        )


def test_simulated_patient_uses_authenticated_identity_instead_of_payload_user_id() -> None:
    app = FastAPI()
    app.include_router(simulated_patient_routes.router)
    simulated_patient_routes._engine = _Engine()

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.current_user = SimpleNamespace(user_id="owner")
        return await call_next(request)

    client = TestClient(app)
    response = client.post(
        "/api/v1/simulated-patient",
        json={"user_id": "another-user", "session_id": "s1", "action": "start"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["user_id"] == "owner"


def test_simulated_patient_shortcut_routes_ignore_the_path_user_id() -> None:
    app = FastAPI()
    app.include_router(simulated_patient_routes.router)
    simulated_patient_routes._engine = _Engine()

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.current_user = SimpleNamespace(user_id="owner")
        return await call_next(request)

    response = TestClient(app).get("/api/v1/simulated-patient/stats/another-user")

    assert response.status_code == 200
    assert response.json()["data"]["user_id"] == "owner"

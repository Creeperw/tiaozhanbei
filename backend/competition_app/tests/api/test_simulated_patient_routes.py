from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from competition_app.api import simulated_patient_routes
from competition_app.simulated_patient.schemas import SimulatedPatientResponse


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


class _CompletedSubmitEngine:
    def execute(self, request):
        return SimulatedPatientResponse(
            session_id=request.session_id,
            action=request.action,
            success=True,
            data={
                "history_id": "H1",
                "grading_report": {"score": 88, "history_id": "H1"},
            },
            is_complete=True,
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


def test_completed_submit_projects_activity_once() -> None:
    calls = []
    app = FastAPI()
    app.include_router(simulated_patient_routes.router)
    simulated_patient_routes._engine = _CompletedSubmitEngine()
    simulated_patient_routes._activity_projection = (
        lambda user_id, data: calls.append((user_id, data))
    )

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.current_user = SimpleNamespace(user_id="owner")
        return await call_next(request)

    response = TestClient(app).post(
        "/api/v1/simulated-patient",
        json={"user_id": "another-user", "session_id": "s1", "action": "submit"},
    )

    assert response.status_code == 200
    assert calls == [("owner", {
        "session_id": "s1",
        "history_id": "H1",
        "practice_scope": "full",
        "data": {"history_id": "H1", "grading_report": {"score": 88, "history_id": "H1"}},
    })]


def test_non_submit_and_incomplete_submit_do_not_project_activity() -> None:
    calls = []
    app = FastAPI()
    app.include_router(simulated_patient_routes.router)
    simulated_patient_routes._engine = _Engine()
    simulated_patient_routes._activity_projection = (
        lambda user_id, data: calls.append((user_id, data))
    )

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.current_user = SimpleNamespace(user_id="owner")
        return await call_next(request)

    client = TestClient(app)
    assert client.post(
        "/api/v1/simulated-patient",
        json={"user_id": "owner", "session_id": "s1", "action": "start"},
    ).status_code == 200
    assert client.post(
        "/api/v1/simulated-patient",
        json={"user_id": "owner", "session_id": "s1", "action": "submit"},
    ).status_code == 200
    assert calls == []


def test_projection_failure_is_fail_open_for_completed_submit() -> None:
    def fail_projection(user_id, data):
        raise RuntimeError("activity database unavailable")

    app = FastAPI()
    app.include_router(simulated_patient_routes.router)
    simulated_patient_routes._engine = _CompletedSubmitEngine()
    simulated_patient_routes._activity_projection = fail_projection

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.current_user = SimpleNamespace(user_id="owner")
        return await call_next(request)

    response = TestClient(app).post(
        "/api/v1/simulated-patient",
        json={"user_id": "owner", "session_id": "s1", "action": "submit"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["history_id"] == "H1"


def test_acupuncture_cases_load_from_versioned_data_file() -> None:
    assert simulated_patient_routes.ACUPUNCTURE_CASE_DATA_PATH.parent.name == "data"
    assert simulated_patient_routes.ACUPUNCTURE_CASE_DATA_PATH.is_file()
    app = FastAPI()
    app.include_router(simulated_patient_routes.router)

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.current_user = SimpleNamespace(user_id="owner")
        return await call_next(request)

    response = TestClient(app).get("/api/v1/simulated-patient/acupuncture-cases")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert len(payload["data"]["cases"]) == 10
    assert payload["data"]["cases"][0]["caseId"] == "acup_00001"


def test_curated_acupuncture_cases_have_archived_model_positions() -> None:
    cases = simulated_patient_routes._load_acupuncture_cases()

    assert len(cases) == 10
    points = [point for case in cases for point in case["standardAcupoints"]]
    assert len(points) == 12
    assert all(len(point.get("modelPosition", [])) == 3 for point in points)
    assert next(point for point in points if point["name"] == "承山")["modelNodeName"] == "chenshan"


def test_acupuncture_scoring_uses_archived_surface_position_and_partial_credit() -> None:
    case_data = next(
        case for case in simulated_patient_routes._load_acupuncture_cases()
        if case["caseId"] == "acup_00001"
    )
    standard = case_data["standardAcupoints"][0]["modelPosition"]
    needle = {
        "point": [standard[0] + 0.021, standard[1], standard[2]],
        "insertionType": "direct",
        "depthValue": 0.6,
        "retentionMinutes": 20,
    }

    result = simulated_patient_routes._score_acupuncture_case(
        case_data,
        [needle],
        {"taixi": [99, 99, 99]},
    )

    assert result == {
        "available": True,
        "total": 95,
        "position": 80,
        "insertion": 100,
        "depth": 100,
        "retention": 100,
        "feedback": case_data["feedbackScripts"]["excellent"]["message"],
    }


def test_pricking_cupping_remains_in_answer_but_is_not_scored_as_needling() -> None:
    case_data = next(
        case for case in simulated_patient_routes._load_acupuncture_cases()
        if case["caseId"] == "acup_00008"
    )
    chenshan = next(point for point in case_data["standardAcupoints"] if point["name"] == "承山")
    weizhong = next(point for point in case_data["standardAcupoints"] if point["name"] == "委中")

    result = simulated_patient_routes._score_acupuncture_case(case_data, [{
        "point": chenshan["modelPosition"],
        "insertionType": "direct",
        "depthValue": 1.5,
        "retentionMinutes": 30,
    }])

    assert weizhong["needleAngle"] == "仅刺络拔罐，不作针刺"
    assert result["total"] == 100
    assert result["position"] == 100


def test_all_ten_curated_cases_accept_their_archived_correct_answers() -> None:
    cases = simulated_patient_routes._load_acupuncture_cases()

    for case_data in cases:
        needles = []
        for point in case_data["standardAcupoints"]:
            if point.get("procedureType") in {"pricking", "pricking_cupping"}:
                continue
            needles.append({
                "point": point["modelPosition"],
                "insertionType": point.get("insertionType"),
                "depthValue": (point["needleDepth"]["min"] + point["needleDepth"]["max"]) / 2,
                "retentionMinutes": (point["retentionTime"]["min"] + point["retentionTime"]["max"]) / 2,
            })

        result = simulated_patient_routes._score_acupuncture_case(case_data, needles)

        assert result["total"] == 100, case_data["caseId"]
        assert result["position"] == 100, case_data["caseId"]

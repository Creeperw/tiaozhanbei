import unittest
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.auth import get_current_user
from APP.backend.case_repository import CaseRepository
from APP.backend.case_training_models import CaseDefinitionRecord, CaseVersionRecord
from APP.backend.case_training_service import CaseTrainingService
from APP.backend.database import get_db


class CaseTrainingRouteTests(unittest.TestCase):
    def setUp(self):
        from APP.backend.main import app
        from APP.backend.routers import case_training_routes

        self.app = app
        self.routes = case_training_routes
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add_all([
                database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x"),
                database.UserModel(id=2, username="other", email="other@example.com", hashed_password="x"),
            ])
            db.commit()
        CaseRepository(self.Session).create_case(
            case_definition_id="CASE_ROUTE_001",
            case_version_id="CASEV_ROUTE_001",
            title="虚劳案例",
            visible_context={"chief_complaint": "乏力纳差", "case_type": "internal", "kp_ids": ["KP_CASE_001"]},
            patient_context={"reported_symptoms": ["乏力", "食欲不振"]},
            golden_standard={"syndrome": "脾胃气虚", "prescription": "四君子汤"},
            rubric={
                "full": {"syndrome": 50, "formula_name": 15, "formula_composition": 25, "inquiry": 10},
                "diagnosis_only": {"syndrome": 70, "inquiry": 30},
            },
        )

        def override_db():
            session = self.Session()
            try:
                yield session
            finally:
                session.close()

        self.user_id = 1

        def override_user():
            return database.UserModel(id=self.user_id, username="learner", email="learner@example.com", hashed_password="x")

        self.app.dependency_overrides[get_db] = override_db
        self.app.dependency_overrides[get_current_user] = override_user
        self.routes.case_training_limiter = self.routes.CaseTrainingLimiter()
        self.default_service_factory = self.routes.case_training_service_factory
        self.routes.case_training_service_factory = lambda _: CaseTrainingService(
            self.Session,
            patient_runner=lambda **kwargs: {"reply": "我主要觉得乏力，吃饭也没胃口。"},
            patient_auditor=lambda **kwargs: {"decision": "pass"},
        )
        self.routes.case_training_limiter.reset()
        self.client = TestClient(self.app)

    def tearDown(self):
        self.routes.case_training_limiter.reset()
        self.app.dependency_overrides.clear()
        self.routes.case_training_service_factory = self.default_service_factory
        self.engine.dispose()

    def test_openapi_registers_all_case_training_routes(self):
        paths = self.app.openapi()["paths"]
        expected = {
            "/training/cases/types",
            "/training/case-sessions",
            "/training/case-sessions/{session_id}",
            "/training/case-sessions/{session_id}/messages",
            "/training/case-sessions/{session_id}/help",
            "/training/case-sessions/{session_id}/submit",
        }
        self.assertTrue(expected <= set(paths))

    def test_all_case_routes_require_authentication(self):
        self.app.dependency_overrides.pop(get_current_user)

        response = self.client.get("/training/cases/types")

        self.assertEqual(response.status_code, 401)

    def test_case_types_and_invalid_help_state_are_resource_safe(self):
        types = self.client.get("/training/cases/types")
        self.assertEqual(types.status_code, 200)
        self.assertEqual(types.json(), {"types": ["internal"], "modes": ["full", "diagnosis_only"]})

        started = self.client.post("/training/case-sessions", json={"selection": "random"})
        self.assertEqual(started.status_code, 200)
        help_response = self.client.post(
            f"/training/case-sessions/{started.json()['session_id']}/help",
            json={"help_type": "hint"},
        )
        self.assertEqual(help_response.status_code, 409)
        self.assertNotIn("Traceback", help_response.text)

    def test_start_get_and_message_do_not_expose_hidden_case_data(self):
        started = self.client.post(
            "/training/case-sessions",
            json={"selection": "by_type", "case_type": "internal", "mode": "diagnosis_only"},
        )
        self.assertEqual(started.status_code, 200)
        body = started.json()
        self.assertEqual(body["mode"], "diagnosis_only")
        self.assertNotIn("脾胃气虚", str(body))
        self.assertNotIn("四君子汤", str(body))

        restored = self.client.get(f"/training/case-sessions/{body['session_id']}")
        self.assertEqual(restored.status_code, 200)
        self.assertNotIn("kp_ids", restored.json()["visible_context"])

        message = self.client.post(
            f"/training/case-sessions/{body['session_id']}/messages",
            json={"message": "您哪里不舒服？"},
        )
        self.assertEqual(message.status_code, 200)
        self.assertIn("disclaimer", message.json())

    def test_routes_enforce_ownership_state_and_message_size(self):
        started = self.client.post("/training/case-sessions", json={"selection": "random"}).json()
        session_id = started["session_id"]
        self.user_id = 2

        self.assertEqual(self.client.get(f"/training/case-sessions/{session_id}").status_code, 404)
        self.assertEqual(
            self.client.post(f"/training/case-sessions/{session_id}/messages", json={"message": "问题"}).status_code,
            404,
        )
        self.user_id = 1
        oversized = self.client.post(
            f"/training/case-sessions/{session_id}/messages",
            json={"message": "问" * 2731},
        )
        self.assertEqual(oversized.status_code, 422)

    def test_default_route_service_uses_real_case_grading_runner(self):
        service = self.default_service_factory(self.Session)

        self.assertIs(service._grading_runner, self.routes.grade_practice_submission)

    def test_active_session_limit_is_safe_under_concurrent_starts(self):
        for _ in range(2):
            self.assertEqual(self.client.post("/training/case-sessions", json={"selection": "random"}).status_code, 200)

        def start_session():
            return self.client.post("/training/case-sessions", json={"selection": "random"}).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _: start_session(), range(2)))

        self.assertEqual(sorted(statuses), [200, 409])
        with self.Session() as db:
            self.assertEqual(self.routes._active_session_count(db, 1), 3)

    def test_case_types_exclude_definitions_without_versions(self):
        with self.Session() as db:
            db.add(CaseDefinitionRecord(
                case_definition_id="CASE_UNPUBLISHED",
                title="未发布案例",
                visible_context_json='{"case_type":"unavailable"}',
                patient_context_json="{}",
            ))
            db.commit()

        response = self.client.get("/training/cases/types")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["types"], ["internal"])

    def test_empty_case_library_returns_actionable_unavailable_status(self):
        with self.Session() as db:
            db.query(CaseDefinitionRecord).delete()
            db.query(CaseVersionRecord).delete()
            db.commit()

        response = self.client.post("/training/case-sessions", json={"selection": "random"})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "No training cases are currently available")

    def test_active_session_limit_and_operation_rate_limit(self):
        started = self.client.post("/training/case-sessions", json={"selection": "random"})
        self.assertEqual(started.status_code, 200)
        session_id = started.json()["session_id"]
        for _ in range(2):
            self.assertEqual(self.client.post("/training/case-sessions", json={"selection": "random"}).status_code, 200)
        self.assertEqual(self.client.post("/training/case-sessions", json={"selection": "random"}).status_code, 409)

        self.routes.case_training_limiter = self.routes.CaseTrainingLimiter(max_requests=1, window_seconds=60)
        limited = self.client.post(f"/training/case-sessions/{session_id}/messages", json={"message": "请描述症状"})
        self.assertEqual(limited.status_code, 200)
        limited = self.client.post(f"/training/case-sessions/{session_id}/messages", json={"message": "还有其他症状吗"})
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited.headers)


if __name__ == "__main__":
    unittest.main()

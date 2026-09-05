import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.auth import get_current_user
from APP.backend.database import get_db, QuestionIngestionTaskRecord


class FakeQuestionIngestionService:
    def __init__(self, result):
        self.result = result
        self.payload = None

    def ingest(self, db, payload):
        self.payload = payload
        return self.result


class FakeQuestionIngestionTaskService:
    def __init__(self):
        self.tasks = {}

    def submit(self, db, *, submitted_by_user_id, payload):
        task = type("Task", (), {"task_id": "QING_ROUTE_001", "status": "queued"})()
        self.tasks[task.task_id] = task
        return task

    def run(self, db, task_id):
        task = self.tasks[task_id]
        task.status = "completed"
        task.outcome_status = "active"
        task.published_question_id = "Q_ROUTE_001"
        task.error_code = None
        return task


class QuestionIngestionRouteTests(unittest.TestCase):
    def setUp(self):
        from APP.backend.main import app
        from APP.backend.routers import knowledge_routes

        self.app = app
        self.routes = knowledge_routes
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.user_role = "admin"

        def override_db():
            session = self.Session()
            try:
                yield session
            finally:
                session.close()

        def override_user():
            return database.UserModel(id=1, username="admin", email="admin@example.com", hashed_password="x", role=self.user_role)

        self.app.dependency_overrides[get_db] = override_db
        self.app.dependency_overrides[get_current_user] = override_user
        self.default_factory = self.routes.question_ingestion_service_factory
        self.default_task_factory = getattr(self.routes, "question_ingestion_task_service_factory", None)
        self.default_pdf_factory = getattr(self.routes, "question_pdf_ingestion_service_factory", None)
        self.service = FakeQuestionIngestionService({"status": "active", "question_id": "Q_ROUTE_001", "stored": True})
        self.routes.question_ingestion_service_factory = lambda: self.service
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.routes.question_ingestion_service_factory = self.default_factory
        if self.default_task_factory is not None:
            self.routes.question_ingestion_task_service_factory = self.default_task_factory
        if self.default_pdf_factory is not None:
            self.routes.question_pdf_ingestion_service_factory = self.default_pdf_factory
        self.engine.dispose()

    def test_admin_ingestion_uses_queued_task_contract(self):
        self.routes.question_ingestion_task_service_factory = FakeQuestionIngestionTaskService
        response = self.client.post(
            "/knowledge/questions/ingest",
            json={
                "stem": "四君子汤主治哪类证候？",
                "answer": "脾胃气虚证",
                "question_type": "single_choice",
                "requested_kp_ids": ["KP_FJ_001"],
                "source_type": "forged",
                "owner_id": "999",
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["task_id"], "QING_ROUTE_001")

    def test_non_admin_cannot_ingest_questions(self):
        self.user_role = "user"

        response = self.client.post("/knowledge/questions/ingest", json={"stem": "题干"})

        self.assertEqual(response.status_code, 403)

    def test_blank_stem_is_rejected(self):
        response = self.client.post("/knowledge/questions/ingest", json={"stem": "   "})

        self.assertEqual(response.status_code, 422)

    def test_task_detail_returns_actual_outcome_status(self):
        with self.Session() as db:
            db.add(QuestionIngestionTaskRecord(
                task_id="QING_DETAIL_001",
                submitted_by_user_id=1,
                status="completed",
                result_json='{"status":"needs_human_review"}',
            ))
            db.commit()

        response = self.client.get("/knowledge/admin/question-ingestion-tasks/QING_DETAIL_001")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["outcome_status"], "needs_human_review")

    def test_admin_can_submit_queued_question_ingestion_task(self):
        self.routes.question_ingestion_task_service_factory = FakeQuestionIngestionTaskService
        response = self.client.post("/knowledge/admin/question-ingestion-tasks", json={"stem": "四君子汤主治哪类证候？"})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {
            "task_id": "QING_ROUTE_001",
            "status": "queued",
            "outcome_status": None,
            "published_question_id": None,
            "error_code": None,
        })

    def test_admin_can_retry_failed_task(self):
        with self.Session() as db:
            db.add(QuestionIngestionTaskRecord(
                task_id="QING_RETRY_001",
                submitted_by_user_id=1,
                status="failed",
                error_code="RuntimeError",
            ))
            db.commit()

        class RetryService:
            def retry(self, db, task_id):
                task = db.query(QuestionIngestionTaskRecord).filter_by(task_id=task_id).one()
                task.status = "queued"
                task.retry_count += 1
                task.error_code = None
                db.commit()
                return task

        self.routes.question_ingestion_task_service_factory = RetryService
        response = self.client.post("/knowledge/admin/question-ingestion-tasks/QING_RETRY_001/retry")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "queued")
        self.assertEqual(response.json()["retry_count"], 1)
    def test_admin_can_list_recent_question_ingestion_tasks(self):
        with self.Session() as db:
            db.add_all([
                QuestionIngestionTaskRecord(task_id="QING_LIST_001", submitted_by_user_id=1, status="completed"),
                QuestionIngestionTaskRecord(task_id="QING_LIST_002", submitted_by_user_id=1, status="failed", error_code="RuntimeError"),
            ])
            db.commit()

        response = self.client.get("/knowledge/admin/question-ingestion-tasks")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["task_id"] for item in response.json()["tasks"]], ["QING_LIST_002", "QING_LIST_001"])

        response = self.client.post(
            "/knowledge/admin/question-ingestion-pdf-upload",
            files={"file": ("题目.pdf", b"not-a-pdf", "application/pdf")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Invalid PDF file")

        self.routes.question_pdf_ingestion_service_factory = lambda: FakePdfQuestionIngestionService({
            "file_id": "FILE_001",
            "file_path": "D:/uploads/题目.pdf",
            "original_filename": "题目.pdf",
            "source_ref": "upload:FILE_001",
            "owner_id": "1",
        })
        self.routes.question_ingestion_task_service_factory = FakeQuestionIngestionTaskService

        response = self.client.post("/knowledge/admin/question-ingestion-pdf-tasks", json={"file_id": "FILE_001"})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["task_id"], "QING_ROUTE_001")

    def test_admin_pdf_question_ingestion_rejects_unknown_upload(self):
        self.routes.question_pdf_ingestion_service_factory = lambda: FakePdfQuestionIngestionService(error=ValueError("Uploaded PDF was not found"))

        response = self.client.post("/knowledge/admin/question-ingestion-pdf-tasks", json={"file_id": "MISSING"})

        self.assertEqual(response.status_code, 404)


class FakePdfQuestionIngestionService:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    def build_payload(self, *, file_id, submitted_by_user_id):
        if self.error:
            raise self.error
        return self.payload


if __name__ == "__main__":
    unittest.main()

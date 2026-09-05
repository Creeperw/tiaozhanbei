import json
import unittest
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from APP.backend.database import Base, QuestionBankItem, QuestionIngestionTaskRecord
from APP.backend.time_utils import utc_now
from APP.backend.question_ingestion_task_service import QuestionIngestionTaskService


class FakeQuestionIngestionService:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def ingest(self, db, payload):
        if self.error:
            raise self.error
        return self.result


class QuestionIngestionTaskServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.payload = {"stem": "四君子汤主治哪类证候？", "question_type": "single_choice"}

    def tearDown(self):
        self.engine.dispose()

    def test_submit_creates_queued_task_without_running_pipeline(self):
        with self.Session() as db:
            service = QuestionIngestionTaskService(lambda: FakeQuestionIngestionService({
                "status": "active",
                "stored": True,
                "question_id": "Q_TASK_001",
            }))
            task = service.submit(db, submitted_by_user_id=1, payload=self.payload)

            persisted = db.query(QuestionIngestionTaskRecord).filter_by(task_id=task.task_id).one()
            self.assertEqual(persisted.status, "queued")
            self.assertEqual(persisted.retry_count, 0)
            self.assertIsNone(persisted.started_at)

    def test_worker_claims_queued_task_and_persists_published_item_result(self):
        with self.Session() as db:
            service = QuestionIngestionTaskService(lambda: FakeQuestionIngestionService({
                "status": "active",
                "stored": True,
                "question_id": "Q_TASK_001",
                "audit": {"quality_score": 0.9},
            }))
            task = service.submit(db, submitted_by_user_id=1, payload=self.payload)
            result = service.run_next(db)

            persisted = db.query(QuestionIngestionTaskRecord).filter_by(task_id=task.task_id).one()
            self.assertEqual(result.task_id, task.task_id)
            self.assertEqual(result.status, "completed")
            self.assertEqual(persisted.status, "completed")
            self.assertEqual(persisted.published_question_id, "Q_TASK_001")
            self.assertIsNotNone(persisted.started_at)
            self.assertIsNotNone(persisted.finished_at)

    def test_failed_task_can_be_requeued_and_retried(self):
        with self.Session() as db:
            service = QuestionIngestionTaskService(lambda: FakeQuestionIngestionService(error=RuntimeError("provider unavailable")))
            task = service.submit(db, submitted_by_user_id=1, payload=self.payload)
            service.run_next(db)

            retried = service.retry(db, task.task_id)
            persisted = db.query(QuestionIngestionTaskRecord).filter_by(task_id=task.task_id).one()
            self.assertEqual(retried.status, "queued")
            self.assertEqual(persisted.status, "queued")
            self.assertEqual(persisted.retry_count, 1)
            self.assertIsNone(persisted.error_code)

    def test_serialization_failure_rolls_back_published_question_with_task_completion(self):
        class NonSerializableResultService:
            def ingest(self, db, payload):
                db.add(QuestionBankItem(question_id="Q_ATOMIC_001", stem=payload["stem"]))
                return {"status": "active", "stored": True, "question_id": "Q_ATOMIC_001", "bad": object()}

        with self.Session() as db:
            service = QuestionIngestionTaskService(NonSerializableResultService)
            task = service.submit(db, submitted_by_user_id=1, payload=self.payload)
            result = service.run_next(db)

            self.assertEqual(result.status, "failed")
            self.assertEqual(db.query(QuestionBankItem).filter_by(question_id="Q_ATOMIC_001").count(), 0)

    def test_expired_running_task_is_reclaimed_before_queued_work(self):
        with self.Session() as db:
            task = QuestionIngestionTaskService().submit(db, submitted_by_user_id=1, payload=self.payload)
            task.status = "running"
            task.claim_expires_at = utc_now() - timedelta(seconds=1)
            db.commit()

            result = QuestionIngestionTaskService(lambda: FakeQuestionIngestionService({
                "status": "duplicate", "stored": False, "question_id": "Q_EXISTING",
            })).run_next(db)

            self.assertEqual(result.task_id, task.task_id)
            self.assertEqual(result.status, "completed")

    def test_retry_only_transitions_failed_task_once(self):
        with self.Session() as db:
            task = QuestionIngestionTaskService().submit(db, submitted_by_user_id=1, payload=self.payload)
            task.status = "failed"
            db.commit()

            service = QuestionIngestionTaskService()
            service.retry(db, task.task_id)
            with self.assertRaises(ValueError):
                service.retry(db, task.task_id)

    def test_worker_runs_pdf_task_with_pdf_ingestion_service(self):
        class FakePdfIngestionService:
            def ingest(self, db, payload):
                return {"status": "completed", "question_count": 2, "results": []}

        with self.Session() as db:
            service = QuestionIngestionTaskService(
                lambda: FakeQuestionIngestionService(),
                lambda: FakePdfIngestionService(),
            )
            task = service.submit(db, submitted_by_user_id=1, payload={**self.payload, "task_kind": "pdf"})
            result = service.run_next(db)

            self.assertEqual(result.task_id, task.task_id)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.outcome_status, "completed")

    def test_pdf_task_claim_uses_extended_lease(self):
        observed = {}

        class FakePdfIngestionService:
            def ingest(self, db, payload):
                task = db.query(QuestionIngestionTaskRecord).filter_by(task_id=payload["task_id"]).one()
                observed["seconds"] = (task.claim_expires_at - task.started_at).total_seconds()
                return {"status": "completed", "results": []}

        with self.Session() as db:
            task = QuestionIngestionTaskService().submit(
                db,
                submitted_by_user_id=1,
                payload={**self.payload, "task_kind": "pdf"},
            )
            task.payload_json = json.dumps({**self.payload, "task_kind": "pdf", "task_id": task.task_id})
            db.commit()
            result = QuestionIngestionTaskService(
                lambda: FakeQuestionIngestionService(),
                lambda: FakePdfIngestionService(),
            ).run_next(db)

            self.assertEqual(result.status, "completed")
            self.assertGreaterEqual(observed["seconds"], 7200)

    def test_run_next_returns_none_when_no_task_is_queued(self):
        with self.Session() as db:
            self.assertIsNone(QuestionIngestionTaskService().run_next(db))

    def test_marks_task_failed_when_ingestion_raises_without_publishing(self):
        with self.Session() as db:
            service = QuestionIngestionTaskService(lambda: FakeQuestionIngestionService(error=RuntimeError("provider unavailable")))
            task = service.submit(db, submitted_by_user_id=1, payload=self.payload)
            result = service.run(db, task.task_id)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.error_code, "RuntimeError")
            self.assertIsNone(result.published_question_id)


if __name__ == "__main__":
    unittest.main()

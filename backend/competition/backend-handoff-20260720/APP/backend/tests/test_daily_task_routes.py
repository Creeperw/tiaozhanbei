import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.auth import get_current_user
from APP.backend.database import get_db
from APP.backend.routers import training_routes


class DailyTaskRoutesTests(unittest.TestCase):
    def setUp(self):
        from APP.backend.main import app

        self.app = app
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add_all([
                database.UserModel(id=1, username="learner", email="a@example.com", hashed_password="x"),
                database.UserModel(id=2, username="other", email="b@example.com", hashed_password="x"),
                database.DailyTaskItemRecord(
                    task_item_id="ITEM_1", host_task_id="TASK_1", host_task_version=1,
                    user_id=1, kp_id="KP_1", item_kind="knowledge_practice",
                    ordinal=1, required_question_count=2, status="pending",
                ),
                database.DailyTaskQuestionSnapshotRecord(
                    task_item_id="ITEM_1", user_id=1, question_id="Q_1",
                    question_version_id="QV_1", question_type="single_choice",
                    stem_snapshot="第一题", options_snapshot_json='["A", "B"]',
                    answer_snapshot="A", rubric_snapshot="选A", kp_snapshot_json='["KP_1"]',
                    source_kind="formal-content:test", audit_decision="pass", audit_status="completed",
                    submitted_answer="A", attempt_status="reviewed",
                ),
                database.DailyTaskQuestionSnapshotRecord(
                    task_item_id="ITEM_1", user_id=1, question_id="Q_2",
                    question_version_id="QV_2", question_type="short_answer",
                    stem_snapshot="第二题", options_snapshot_json="[]",
                    answer_snapshot="秘密答案", rubric_snapshot="秘密规则", kp_snapshot_json='["KP_1"]',
                    source_kind="formal-content:test", audit_decision="pending", audit_status="pending",
                ),
            ])
            db.commit()

        def override_db():
            with self.Session() as db:
                yield db

        self.app.dependency_overrides[get_db] = override_db
        self.app.dependency_overrides[get_current_user] = lambda: database.UserModel(id=1, username="learner", hashed_password="x")
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.engine.dispose()

    def test_next_returns_first_unreviewed_snapshot_without_answers_and_404s_for_other_user(self):
        response = self.client.get("/daily-task-items/ITEM_1/practice/next")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["available"])
        self.assertEqual(body["question"]["question_id"], "Q_2")
        self.assertEqual(body["progress"], {"reviewed": 1, "required": 2})
        self.assertNotIn("秘密答案", response.text)
        self.assertNotIn("秘密规则", response.text)

        self.app.dependency_overrides[get_current_user] = lambda: database.UserModel(id=2, username="other", hashed_password="x")
        self.assertEqual(self.client.get("/daily-task-items/ITEM_1/practice/next").status_code, 404)

    def test_bound_claim_rejects_forged_question_and_replay_does_not_repeat_completion(self):
        issued = self.client.get("/daily-task-items/ITEM_1/practice/next").json()
        request_id = issued["question"]["request_id"]
        forged = self.client.post("/training/practice/grade", json={
            "question_id": "Q_FORGED", "stem": "伪造", "student_answer": "答案", "request_id": request_id,
        })
        self.assertEqual(forged.status_code, 422)

        runner_payload = {
            "score": 100, "max_score": 100, "is_correct": True,
            "error_types": [], "error_reason": "", "confidence": 1.0,
            "feedback": "正确", "audit": {"decision": "pass", "reason": "ok", "confidence": 1.0},
        }
        with patch.object(training_routes, "practice_grading_runner", return_value=runner_payload):
            graded = self.client.post("/training/practice/grade", json={
                "question_id": "Q_2", "stem": "伪造题干", "student_answer": "我的答案", "request_id": request_id,
            })
            replay = self.client.post("/training/practice/grade", json={
                "question_id": "Q_2", "stem": "伪造题干", "student_answer": "我的答案", "request_id": request_id,
            })
        self.assertEqual(graded.status_code, 200)
        self.assertEqual(replay.status_code, 409)

        completed = self.client.get("/daily-task-items/ITEM_1/practice/next")
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json(), {
            "available": False,
            "reason": "daily_task_item_completed",
            "progress": {"reviewed": 2, "required": 2},
        })
        with self.Session() as db:
            self.assertEqual(db.query(database.LearningAttemptRecord).filter_by(
                daily_task_item_id="ITEM_1", request_id=request_id
            ).count(), 1)

    def test_claim_grades_the_exact_snapshot_when_question_has_multiple_versions(self):
        issued = self.client.get("/daily-task-items/ITEM_1/practice/next").json()
        request_id = issued["question"]["request_id"]
        with self.Session() as db:
            db.add(database.DailyTaskQuestionSnapshotRecord(
                task_item_id="ITEM_1", user_id=1, question_id="Q_2",
                question_version_id="QV_2_REVISED", question_type="short_answer",
                stem_snapshot="修订后的第二题", answer_snapshot="修订答案",
                rubric_snapshot="修订规则", kp_snapshot_json='["KP_1"]',
                source_kind="formal-content:test",
            ))
            db.commit()

        runner_payload = {
            "score": 100, "max_score": 100, "is_correct": True,
            "error_types": [], "error_reason": "", "confidence": 1.0,
            "feedback": "正确", "audit": {"decision": "pass", "reason": "ok", "confidence": 1.0},
        }
        with patch.object(training_routes, "practice_grading_runner", return_value=runner_payload) as runner:
            response = self.client.post("/training/practice/grade", json={
                "question_id": "Q_2", "stem": "伪造题干", "student_answer": "我的答案", "request_id": request_id,
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(runner.call_args.kwargs["submission"]["standard_answer"], "秘密答案")
        with self.Session() as db:
            attempt_item = db.query(database.LearningAttemptItemRecord).one()
            self.assertEqual(attempt_item.question_version_id, "QV_2")
            original = db.query(database.DailyTaskQuestionSnapshotRecord).filter_by(
                question_version_id="QV_2"
            ).one()
            revised = db.query(database.DailyTaskQuestionSnapshotRecord).filter_by(
                question_version_id="QV_2_REVISED"
            ).one()
            self.assertEqual(original.audit_decision, "pass")
            self.assertEqual(revised.audit_decision, "pending")

    def test_next_reuses_one_claim_and_terminal_snapshot_rejects_submission(self):
        first = self.client.get("/daily-task-items/ITEM_1/practice/next").json()
        second = self.client.get("/daily-task-items/ITEM_1/practice/next").json()
        self.assertEqual(first["question"]["request_id"], second["question"]["request_id"])
        request_id = first["question"]["request_id"]
        with self.Session() as db:
            snapshot = db.query(database.DailyTaskQuestionSnapshotRecord).filter_by(
                question_version_id="QV_2"
            ).one()
            self.assertEqual(db.query(database.CorePracticeSubmissionClaim).filter_by(
                user_id=1, daily_task_snapshot_id=snapshot.id
            ).count(), 1)
            snapshot.audit_decision = "pass"
            snapshot.audit_status = "completed"
            db.commit()

        response = self.client.post("/training/practice/grade", json={
            "question_id": "Q_2", "stem": "伪造题干", "student_answer": "我的答案", "request_id": request_id,
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "daily task question is already terminal")
        with self.Session() as db:
            self.assertEqual(db.query(database.LearningAttemptRecord).filter_by(
                daily_task_item_id="ITEM_1", request_id=request_id
            ).count(), 0)


if __name__ == "__main__":
    unittest.main()

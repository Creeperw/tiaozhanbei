"""Isolated route tests: no live container, model, server, or production database."""
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database as d
from APP.backend.auth import get_current_user
from APP.backend.routers import daily_task_routes, training_routes


class PracticeQuestionListTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        d.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.user_id = 1
        with self.Session() as db:
            db.add_all([
                d.UserModel(id=1, username="one", hashed_password="x"),
                d.UserModel(id=2, username="two", hashed_password="x"),
                d.KnowledgePoint(kp_id="KP", name="知识点", status="active"),
                d.QuestionBankItem(question_id="Q1", stem="第一题", answer="secret", analysis="secret-analysis", kp_ids_json='["KP"]'),
                d.QuestionBankItem(question_id="Q2", stem="第二题", answer="secret", kp_ids_json='["KP"]'),
                d.QuestionBankItem(question_id="Q3", stem="未关联", answer="secret", kp_ids_json='[]'),
                d.UserQuestionItem(question_id="PRIVATE", job_id="IMPORT", content_hash="private-test", status="active", owner_user_id=2, stem="他人私题", kp_ids_json='["KP"]'),
                d.DailyTaskItemRecord(task_item_id="TASK", user_id=1, host_task_id="HOST", host_task_version=1,
                    kp_id="KP", item_kind="knowledge_practice", required_question_count=3),
            ])
            for i in range(1, 4):
                db.add(d.DailyTaskQuestionSnapshotRecord(
                    task_item_id="TASK", user_id=1, question_id=f"Q{i}", question_version_id=f"V{i}",
                    stem_snapshot=f"快照{i}", answer_snapshot="secret", rubric_snapshot="secret-rubric",
                    kp_snapshot_json='["KP"]', audit_decision="pass" if i == 1 else "pending",
                    submitted_answer="我的原答案" if i == 1 else "",
                ))
            db.commit()
        app = FastAPI()
        app.include_router(training_routes.stable_practice_router)
        app.include_router(daily_task_routes.router)
        def get_db():
            with self.Session() as db:
                yield db
        app.dependency_overrides[d.get_db] = get_db
        app.dependency_overrides[get_current_user] = lambda: d.UserModel(id=self.user_id, username="test")
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def test_public_list_is_complete_stable_and_does_not_issue_claims(self):
        url = "/v1/workshop/practice/questions?kp_id=KP&mode=all&scope=all"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([q["question_id"] for q in response.json()["questions"]], ["Q1", "Q2"])
        self.assertEqual(response.json(), self.client.get(url).json())
        self.assertNotIn("secret", response.text)
        self.assertNotIn("PRIVATE", response.text)
        with self.Session() as db:
            self.assertEqual(db.query(d.CorePracticeSubmissionClaim).count(), 0)
        self.assertEqual(self.client.get(url.replace("KP", "MISSING")).json()["total"], 0)

    def test_daily_list_keeps_reviewed_questions_and_issues_exact_selected_snapshot(self):
        response = self.client.get("/daily-task-items/TASK/practice/questions")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 3)
        self.assertTrue(body["questions"][0]["reviewed"])
        self.assertEqual(body["questions"][0]["submitted_answer"], "我的原答案")
        self.assertNotIn("secret", response.text)
        selected = body["questions"][2]
        issued = self.client.get(f'/daily-task-items/TASK/practice/next?snapshot_id={selected["snapshot_id"]}')
        self.assertEqual(issued.status_code, 200)
        self.assertEqual(issued.json()["question"]["question_version_id"], "V3")
        first = body["questions"][0]["snapshot_id"]
        self.assertEqual(self.client.get(f"/daily-task-items/TASK/practice/next?snapshot_id={first}").status_code, 409)
        self.user_id = 2
        self.assertEqual(self.client.get("/daily-task-items/TASK/practice/questions").status_code, 404)


if __name__ == "__main__":
    unittest.main()
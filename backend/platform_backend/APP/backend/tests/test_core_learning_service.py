import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.core_learning_service import record_agent_context, record_practice_outcome


class CoreLearningServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.db.add_all([
            database.KnowledgePoint(kp_id="kp-1", name="测试知识点", status="active"),
            database.LearningKnowledgePoint(kp_id="kp-1"),
            database.QuestionBankItem(
                question_id="question-1",
                stem="题干",
                answer="A",
                kp_ids_json='["kp-1"]',
                status="active",
            ),
            database.LearningQuestion(question_id="question-1"),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_record_practice_outcome_creates_attempt_stat_and_mastery_state(self):
        result = record_practice_outcome(
            self.db,
            user_id=1,
            task_id=None,
            request_id="request-1",
            submission={
                "question_id": "question-1",
                "student_answer": "A",
                "knowledge_points": ["kp-1"],
            },
            grading={
                "is_correct": True,
                "score": 100,
                "error_reason": "",
            },
        )
        self.db.commit()

        self.assertEqual(result.attempt.question_id, "question-1")
        self.assertEqual(json.loads(result.attempt.submitted_answer_json), ["A"])
        self.assertTrue(result.attempt.is_correct)
        self.assertEqual(result.stat.answer_accuracy, 1.0)
        self.assertEqual(result.state.knowledge_mastery, 1.0)
        self.assertEqual(result.state.kp_review_status, "active")

    def test_record_practice_outcome_accumulates_wrong_answer_and_requires_review(self):
        for request_id, is_correct in (("request-2a", True), ("request-2b", False)):
            record_practice_outcome(
                self.db,
                user_id=1,
                task_id=None,
                request_id=request_id,
                submission={
                    "question_id": "question-1",
                    "student_answer": "A",
                    "knowledge_points": ["kp-1"],
                },
                grading={
                    "is_correct": is_correct,
                    "score": 100 if is_correct else 0,
                    "error_reason": "概念混淆" if not is_correct else "",
                },
            )
        self.db.commit()

        stat = self.db.query(database.QuestionLearningStat).one()
        state = self.db.query(database.UserKnowledgeState).one()
        self.assertEqual(stat.attempt_count, 2)
        self.assertEqual(stat.correct_count, 1)
        self.assertEqual(stat.answer_accuracy, 0.5)
        self.assertEqual(stat.reason_for_mistake, "概念混淆")
        self.assertEqual(state.attempt_count, 2)
        self.assertEqual(state.correct_count, 1)
        self.assertEqual(state.knowledge_mastery, 0.5)
        self.assertEqual(state.kp_review_status, "review")

    def test_record_practice_outcome_ignores_a_repeated_request(self):
        for _ in range(2):
            record_practice_outcome(
                self.db,
                user_id=1,
                task_id=None,
                request_id="request-repeat",
                submission={
                    "question_id": "question-1",
                    "student_answer": "A",
                    "knowledge_points": ["kp-1"],
                },
                grading={"is_correct": True, "score": 100, "error_reason": ""},
            )
        self.db.commit()

        self.assertEqual(self.db.query(database.LearningQuestionAttempt).count(), 1)
        self.assertEqual(self.db.query(database.QuestionLearningStat).one().attempt_count, 1)
        self.assertEqual(self.db.query(database.UserKnowledgeState).one().attempt_count, 1)

    def test_record_agent_context_keeps_only_minimal_chat_payload(self):
        context = record_agent_context(
            self.db,
            user_id=1,
            session_id=None,
            source_agent="chat_route",
            target_agent="health_workflow",
            purpose="generate_reply",
            user_input="解释阴阳",
            tools_enabled=True,
            files=[{"id": "file-1", "name": "notes.pdf", "unrelated": "omit"}],
        )
        self.db.commit()

        payload = json.loads(context.payload_json)
        self.assertEqual(payload, {
            "user_input": "解释阴阳",
            "tools_enabled": True,
            "files": [{"id": "file-1", "name": "notes.pdf"}],
        })
        self.assertEqual(context.source_agent, "chat_route")
        self.assertEqual(context.target_agent, "health_workflow")


if __name__ == "__main__":
    unittest.main()

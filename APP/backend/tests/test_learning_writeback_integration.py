import os
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from APP.backend import grading_application_service as grading
from APP.backend.database import (
    AuditResultRecord,
    Base,
    GradingResultRecord,
    KnowledgeMasteryState,
    LearnerKPReviewState,
    LearnerKnowledgeMastery,
    LearningAttemptItemRecord,
    LearningAttemptRecord,
    LearningWritebackReceipt,
    MasteryHistoryRecord,
    MistakeRecord,
    ReviewTaskRecord,
    UserModel,
)


class LearningWritebackIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(os.environ["DATABASE_URL"])
        event.listen(self.engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add(UserModel(id=1, username="integration-learner", hashed_password="x"))
            db.commit()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    def runner(*, profile, memories, submission):
        is_correct = submission["submitted_answer"] == "脾胃气虚证"
        return {
            "score": 90 if is_correct else 20,
            "max_score": 100,
            "is_correct": is_correct,
            "error_types": [] if is_correct else ["概念混淆"],
            "error_reason": "" if is_correct else "证型判断错误",
            "confidence": 0.9,
            "feedback": "正确" if is_correct else "请复习脾胃气虚证",
        }

    @staticmethod
    def legacy(answer, request_id):
        return grading.from_legacy_route_request(
            1,
            {
                "question_id": "qv-integration",
                "question_type": "short_answer",
                "stem": "四君子汤主治什么证型？",
                "student_answer": answer,
                "standard_answer": "脾胃气虚证",
                "knowledge_points": ["kp-sijunzi"],
                "difficulty": 2,
            },
            profile={}, memories=[], request_id=request_id,
        )

    @staticmethod
    def workspace(answer, task_id):
        return grading.from_workspace_request(
            1,
            {
                "task_id": task_id,
                "inputs": {
                    "question_id": "qv-integration",
                    "question_type": "short_answer",
                    "stem": "四君子汤主治什么证型？",
                    "submitted_answer": answer,
                    "standard_answer": "脾胃气虚证",
                    "kp_ids": ["kp-sijunzi"],
                    "difficulty": 2,
                },
            },
            profile={}, memories=[], request_id=task_id,
        )

    def assert_result_ids(self, result):
        self.assertTrue(result.attempt_id)
        self.assertTrue(result.attempt_item_id)
        self.assertTrue(result.grading_artifact_id)
        self.assertEqual(result.grading_artifact_version, 1)
        self.assertTrue(result.audit_id)
        self.assertEqual(result.writeback.status, "applied")
        self.assertTrue(result.writeback.receipt_id)

    def test_equivalent_legacy_and_workspace_entries_create_authoritative_chains(self):
        with self.Session() as db:
            passed = grading.apply_practice_grading(
                db, self.legacy("脾胃气虚证", "legacy-pass"), runner=self.runner
            )
            wrong = grading.apply_practice_grading(
                db, self.workspace("中焦虚寒证", "workspace-wrong"), runner=self.runner
            )
            self.assert_result_ids(passed)
            self.assert_result_ids(wrong)
            self.assertTrue(wrong.writeback.mistake_ids)
            self.assertTrue(wrong.writeback.mastery_updates)
            self.assertTrue(wrong.writeback.review_task_ids)
            self.assertEqual(db.query(LearningAttemptRecord).count(), 2)
            self.assertEqual(db.query(LearningAttemptItemRecord).count(), 2)
            self.assertEqual(db.query(GradingResultRecord).count(), 2)
            self.assertEqual(db.query(AuditResultRecord).count(), 2)
            self.assertEqual(db.query(LearningWritebackReceipt).count(), 2)
            self.assertEqual(db.query(MasteryHistoryRecord).count(), 2)
            self.assertEqual(db.query(ReviewTaskRecord).count(), 1)
            self.assertEqual(db.query(MistakeRecord).count(), 1)

    def test_b_failure_after_mastery_history_rolls_back_all_b_effects_and_projections(self):
        with self.Session() as db:
            original_add = db.add

            def fail_after_history(instance, *args, **kwargs):
                original_add(instance, *args, **kwargs)
                if isinstance(instance, MasteryHistoryRecord):
                    raise RuntimeError("injected after mastery history")

            with patch.object(db, "add", side_effect=fail_after_history):
                with self.assertRaisesRegex(RuntimeError, "injected after mastery history"):
                    grading.apply_practice_grading(
                        db, self.workspace("中焦虚寒证", "workspace-failure"), runner=self.runner
                    )

            self.assertEqual(db.query(LearningAttemptRecord).count(), 1)
            self.assertEqual(db.query(LearningAttemptItemRecord).count(), 1)
            for model in (
                GradingResultRecord, AuditResultRecord, LearningWritebackReceipt,
                KnowledgeMasteryState, MasteryHistoryRecord, LearnerKPReviewState,
                LearnerKnowledgeMastery, ReviewTaskRecord, MistakeRecord,
            ):
                with self.subTest(model=model.__name__):
                    self.assertEqual(db.query(model).count(), 0)


if __name__ == "__main__":
    unittest.main()

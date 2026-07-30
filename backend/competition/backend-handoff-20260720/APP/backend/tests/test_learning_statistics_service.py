import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.learning_statistics_service import build_learning_statistics


class LearningStatisticsServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.Session()
        self.now = datetime.utcnow()
        self.db.add_all([
            database.UserModel(
                id=1, username="statistics-user-1", email="s1@example.com",
                hashed_password="x",
            ),
            database.UserModel(
                id=2, username="statistics-user-2", email="s2@example.com",
                hashed_password="x",
            ),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _add_graded_item(
        self,
        suffix: str,
        *,
        learner_id: int = 1,
        attempt_type: str = "practice",
        submitted_days_ago: int = 0,
        score: float = 100,
        max_score: float = 100,
        is_correct: bool | None = True,
        audit_decision: str = "pass",
        duplicate_pass_audit: bool = False,
        question_id: str | None = None,
        question_version: int = 1,
    ) -> None:
        submitted_at = self.now - timedelta(days=submitted_days_ago)
        attempt_id = f"ATTEMPT_{suffix}"
        item_id = f"ITEM_{suffix}"
        artifact_id = f"GRADE_{suffix}"
        question_version_id = f"QV_{suffix}"
        self.db.add(database.QuestionVersionRecord(
            question_version_id=question_version_id,
            question_id=question_id or f"Q_{suffix}",
            version=question_version,
        ))
        self.db.add(database.LearningAttemptRecord(
            attempt_id=attempt_id,
            learner_id=learner_id,
            attempt_type=attempt_type,
            status="submitted",
            submitted_at=submitted_at,
            created_at=submitted_at,
        ))
        self.db.add(database.LearningAttemptItemRecord(
            attempt_item_id=item_id,
            attempt_id=attempt_id,
            question_version_id=question_version_id,
            kp_snapshot_json='[{"kp_id":"KP_1"}]',
        ))
        self.db.add(database.GradingResultRecord(
            artifact_id=artifact_id,
            attempt_item_id=item_id,
            version=1,
            score=score,
            max_score=max_score,
            is_correct=is_correct,
            kp_ids_json='["KP_1"]',
            status="reviewed",
        ))
        self.db.add(database.AuditResultRecord(
            audit_id=f"AUDIT_{suffix}",
            source_artifact_id=artifact_id,
            source_artifact_version=1,
            decision=audit_decision,
            status="completed",
        ))
        if duplicate_pass_audit:
            self.db.add(database.AuditResultRecord(
                audit_id=f"AUDIT_{suffix}_SECOND",
                source_artifact_id=artifact_id,
                source_artifact_version=1,
                decision="pass",
                status="completed",
            ))

    def test_counts_only_audited_items_and_keeps_lifetime_and_window_separate(self):
        self._add_graded_item(
            "RECENT",
            score=80,
            is_correct=True,
            question_id="SHARED_QUESTION",
            duplicate_pass_audit=True,
        )
        self._add_graded_item(
            "OLD",
            attempt_type="paper",
            submitted_days_ago=60,
            score=20,
            is_correct=False,
            question_id="SHARED_QUESTION",
            question_version=2,
        )
        self._add_graded_item("REJECTED", audit_decision="reject")
        self._add_graded_item("OTHER_USER", learner_id=2)
        self.db.add_all([
            database.PaperInstanceRecord(
                paper_id="PAPER_1",
                task_id="PAPER_TASK_1",
                learner_id=1,
                status="completed",
            ),
            database.PaperSubmissionRecord(
                paper_id="PAPER_1",
                learner_id=1,
                request_id="SUBMIT_1",
                status="completed",
                created_at=self.now - timedelta(days=60),
            ),
            database.MistakeRecord(
                user_id=1,
                question_id="SHARED_QUESTION",
                status="active",
                created_at=self.now,
            ),
            database.LearningFocusSession(
                focus_session_id="FOCUS_1",
                user_id=1,
                status="completed",
                active_seconds=900,
                started_at=self.now,
                ended_at=self.now,
            ),
            database.KnowledgeMasteryState(
                mastery_state_id="MASTER_1",
                learner_id=1,
                kp_id="KP_1",
                mastery_score=85,
            ),
            database.LearnerKPReviewState(
                review_state_id="REVIEW_1",
                learner_id=1,
                kp_id="KP_1",
                status="active",
                next_review_at=self.now - timedelta(minutes=1),
            ),
            database.ReviewTaskRecord(
                review_task_id="REVIEW_TASK_1",
                learner_id=1,
                review_state_id="REVIEW_1",
                primary_kp_id="KP_1",
                status="pending",
            ),
            database.KnowledgeCardRecord(
                card_id="CARD_1",
                user_id=1,
                kp_id="KP_1",
                title="知识卡",
            ),
        ])
        self.db.commit()

        result = build_learning_statistics(self.db, 1, days=30, now=self.now)

        self.assertEqual(result["lifetime"]["questions_completed"], 2)
        self.assertEqual(result["lifetime"]["audited_question_items_completed"], 2)
        self.assertEqual(result["lifetime"]["paper_questions_completed"], 1)
        self.assertEqual(result["lifetime"]["unique_questions_completed"], 1)
        self.assertEqual(result["lifetime"]["correct_answers"], 1)
        self.assertEqual(result["lifetime"]["incorrect_answers"], 1)
        self.assertEqual(result["lifetime"]["paper_attempts_completed"], 1)
        self.assertEqual(result["lifetime"]["active_mistakes"], 1)
        self.assertEqual(result["lifetime"]["focus_minutes"], 15)
        self.assertEqual(result["lifetime"]["knowledge_points_mastered"], 1)
        self.assertEqual(result["lifetime"]["reviews_due"], 1)
        self.assertEqual(result["lifetime"]["review_tasks_pending"], 1)
        self.assertEqual(result["current_window"]["questions_completed"], 1)
        self.assertEqual(result["current_window"]["paper_attempts_completed"], 0)
        self.assertEqual(result["current_window"]["score_rate"], 0.8)
        self.assertEqual(result["current_window"]["retry_count"], 0)
        self.assertIn("retry_count", result["metric_definitions"])

    def test_empty_user_returns_null_rate_instead_of_false_zero_ability(self):
        result = build_learning_statistics(self.db, 1, days=7, now=self.now)

        self.assertEqual(result["lifetime"]["questions_completed"], 0)
        self.assertEqual(result["lifetime"]["paper_questions_completed"], 0)
        self.assertIsNone(result["lifetime"]["score_rate"])
        self.assertEqual(result["current_window"]["questions_completed"], 0)
        self.assertFalse(result["counting_policy"]["drafts_counted"])

    def test_window_focus_clips_a_session_that_crosses_the_window_boundary(self):
        boundary = self.now - timedelta(days=7)
        self.db.add(database.LearningFocusSession(
            focus_session_id="FOCUS_WINDOW_BOUNDARY",
            user_id=1,
            status="completed",
            active_seconds=3_600,
            started_at=boundary - timedelta(minutes=30),
            ended_at=boundary + timedelta(minutes=30),
            updated_at=boundary + timedelta(minutes=30),
        ))
        self.db.commit()

        result = build_learning_statistics(self.db, 1, days=7, now=self.now)

        self.assertEqual(result["lifetime"]["focus_minutes"], 60)
        self.assertEqual(result["current_window"]["focus_minutes"], 30)

    def test_rejects_unsupported_window(self):
        with self.assertRaisesRegex(ValueError, "7, 30, 90"):
            build_learning_statistics(self.db, 1, days=14, now=self.now)


if __name__ == "__main__":
    unittest.main()

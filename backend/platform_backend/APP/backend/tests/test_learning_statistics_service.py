import unittest
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.learning_statistics_service import build_learning_statistics, build_practice_history, practice_window_start


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

    def test_history_restores_original_import_stem_only_with_known_provenance(self):
        for index, source in enumerate(['synthetic_usage_v1', 'unknown']):
            at = self.now - timedelta(minutes=index)
            self.db.add(database.QuestionAttempt(
                user_id=1, question_id='Q_SJZ_016', score=70, created_at=at))
            self.db.add(database.LearningActivityRecord(
                user_id=1, activity_type='question_attempt',
                resource_id='Q_SJZ_016', completion_status='completed', created_at=at,
                payload_json=json.dumps({'source': source})))
        self.db.commit()
        history = build_practice_history(self.db, 1, now=self.now)
        self.assertEqual(history['total'], 2)
        self.assertEqual(history['recent_activities'][0]['title'], '学习方剂知识是否能替代医生诊断和处方？')
        self.assertEqual(history['recent_activities'][1]['title'], '历史作答（题干暂不可用）')
        self.assertEqual([row.score for row in self.db.query(database.QuestionAttempt).all()], [70, 70])

    def test_empty_user_returns_null_rate_instead_of_false_zero_ability(self):
        result = build_learning_statistics(self.db, 1, days=7, now=self.now)

        self.assertEqual(result["lifetime"]["questions_completed"], 0)
        self.assertEqual(result["lifetime"]["paper_questions_completed"], 0)
        self.assertIsNone(result["lifetime"]["score_rate"])
        self.assertEqual(result["current_window"]["questions_completed"], 0)
        self.assertFalse(result["counting_policy"]["drafts_counted"])

    def test_window_focus_clips_a_session_that_crosses_the_window_boundary(self):
        boundary = practice_window_start(self.now, 7)
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

    def test_today_metrics_use_beijing_calendar_day_and_do_not_leak_window_focus(self):
        # 2026-08-10 00:30 Asia/Shanghai == 2026-08-09 16:30 UTC.
        calculated_at = datetime(2026, 8, 9, 16, 30)
        yesterday_focus = database.LearningFocusSession(
            focus_session_id="FOCUS_YESTERDAY",
            user_id=1,
            status="completed",
            active_seconds=3_600,
            started_at=calculated_at - timedelta(hours=2),
            ended_at=calculated_at - timedelta(hours=1),
        )
        today_focus = database.LearningFocusSession(
            focus_session_id="FOCUS_TODAY",
            user_id=1,
            status="completed",
            active_seconds=1_200,
            started_at=calculated_at - timedelta(minutes=20),
            ended_at=calculated_at,
        )
        self.db.add_all([yesterday_focus, today_focus])
        self.db.commit()

        result = build_learning_statistics(self.db, 1, days=30, now=calculated_at)

        self.assertEqual(result["today"]["focus_minutes"], 20)
        self.assertEqual(result["today"]["questions_completed"], 0)
        self.assertIsNone(result["today"]["score_rate"])

    def test_rejects_unsupported_window(self):
        with self.assertRaisesRegex(ValueError, "7, 30, 90"):
            build_learning_statistics(self.db, 1, days=14, now=self.now)

    def test_all_accounts_include_legacy_and_history_matches_without_activity_limit(self):
        self._add_graded_item('NEW', score=80, is_correct=False)
        for index in range(120):
            self.db.add(database.QuestionAttempt(user_id=1, question_id=f'OLD_{index}',
                answer='A', score=100, is_correct=True, created_at=self.now))
            self.db.add(database.LearningActivityRecord(user_id=1, activity_type='login',
                created_at=self.now, completion_status='completed'))
        self.db.add(database.QuestionAttempt(user_id=2, question_id='OTHER', score=90, created_at=self.now))
        self.db.commit()
        result = build_learning_statistics(self.db, 1, now=self.now)
        history = build_practice_history(self.db, 1, now=self.now)
        self.assertEqual(result['current_window']['questions_completed'], 121)
        self.assertEqual(history['total'], 121)
        self.assertEqual(result['current_window']['audited_question_items_completed'], 1)
        self.assertEqual(result['current_window']['legacy_question_items_completed'], 120)
        self.assertEqual(result['current_window']['score_rate'], round(12080 / 12100, 4))
        self.assertEqual(result['current_window']['accuracy'], round(120 / 121, 4))
        self.assertEqual(build_learning_statistics(self.db, 2, now=self.now)['current_window']['questions_completed'], 1)

    def test_canonical_mirrors_do_not_duplicate_or_bypass_rejection(self):
        for suffix, decision in [('PASS', 'pass'), ('FAIL', 'reject')]:
            self._add_graded_item(suffix, audit_decision=decision)
            self.db.add(database.QuestionAttempt(user_id=1, question_id=f'Q_{suffix}',
                answer='', score=100, is_correct=True, created_at=self.now))
        self.db.commit()
        result = build_learning_statistics(self.db, 1, now=self.now)
        self.assertEqual(result['current_window']['questions_completed'], 1)
        self.assertEqual(build_practice_history(self.db, 1, now=self.now)['total'], 1)

    def test_legacy_missing_score_and_calendar_boundary(self):
        boundary = practice_window_start(self.now, 30)
        for index, timestamp in enumerate([boundary, boundary - timedelta(seconds=1), self.now + timedelta(seconds=1)]):
            self.db.add(database.QuestionAttempt(user_id=1, question_id=f'BOUND_{index}',
                score=None, is_correct=False, created_at=timestamp))
        self.db.commit()
        result = build_learning_statistics(self.db, 1, now=self.now)
        self.assertEqual(result['current_window']['questions_completed'], 1)
        self.assertIsNone(result['current_window']['score_rate'])
        self.assertEqual(build_practice_history(self.db, 1, now=self.now)['total'], 1)

    def test_completed_paper_items_are_unioned_not_global_max(self):
        self._add_graded_item('PAPER_CANONICAL', attempt_type='paper')
        self.db.add(database.PaperSubmissionRecord(paper_id='OTHER_PAPER', learner_id=1,
            request_id='SUBMIT', status='completed', created_at=self.now,
            result_json=json.dumps({'items': [{'paper_item_id': 'ONE', 'score': 5, 'max_score': 10}]})))
        self.db.commit()
        result = build_learning_statistics(self.db, 1, now=self.now)
        self.assertEqual(result['current_window']['questions_completed'], 2)
        self.assertEqual(result['current_window']['paper_questions_completed'], 2)
        self.assertEqual(build_practice_history(self.db, 1, now=self.now)['total'], 2)

    def test_history_prefers_submitted_version_stem_over_internal_identifier(self):
        self._add_graded_item('TITLE')
        self.db.flush()
        version = self.db.query(database.QuestionVersionRecord).filter_by(question_version_id='QV_TITLE').one()
        version.stem = '阴阳学说的基本内容是什么？'
        self.db.add(database.QuestionBankItem(question_id='Q_TITLE', stem='当前题库已更新的题干'))
        self.db.commit()
        history = build_practice_history(self.db, 1, now=self.now)
        self.assertEqual(history['recent_activities'][0]['title'], version.stem)

    def test_case_sessions_are_separate_from_questions_and_scores(self):
        self._add_graded_item('CASE', attempt_type='case', score=10, is_correct=False)
        self._add_graded_item('QUESTION', score=80)
        self.db.add(database.LearningActivityRecord(user_id=1, activity_type='case_training',
            resource_id='LEGACY_CASE', resource_type='case_session',
            completion_status='completed', created_at=self.now))
        self.db.commit()
        metrics = build_learning_statistics(self.db, 1, now=self.now)['current_window']
        self.assertEqual(metrics['questions_completed'], 1)
        self.assertEqual(metrics['case_sessions_completed'], 2)
        self.assertEqual(metrics['score_rate'], .8)
        self.assertEqual(metrics['accuracy'], 1)
        self.assertEqual(build_practice_history(self.db, 1, now=self.now)['total'], 3)

    def test_real_retries_remain_separate_and_paper_rejection_cannot_be_bypassed(self):
        self._add_graded_item('REJECTED_PAPER', attempt_type='paper', audit_decision='reject')
        self.db.flush()
        attempt = self.db.query(database.LearningAttemptRecord).filter_by(attempt_id='ATTEMPT_REJECTED_PAPER').one()
        attempt.request_id = 'REJECT:ONE'
        for request in ['FIRST', 'SECOND', 'REJECT']:
            self.db.add(database.PaperSubmissionRecord(paper_id='P', learner_id=1,
                request_id=request, status='completed', created_at=self.now,
                result_json=json.dumps({'items': [{'paper_item_id': 'ONE', 'score': 5, 'max_score': 10}]})))
        self.db.commit()
        metrics = build_learning_statistics(self.db, 1, now=self.now)['current_window']
        self.assertEqual(metrics['paper_questions_completed'], 2)
        history = build_practice_history(self.db, 1, now=self.now)
        self.assertEqual(history['total'], 2)
        self.assertEqual(len({r['activity_id'] for r in history['recent_activities']}), 2)


if __name__ == "__main__":
    unittest.main()

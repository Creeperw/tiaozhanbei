import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.learning_governance_service import (
    build_learning_insights,
    build_resource_match_report,
    decide_plan_review,
    list_notifications,
    record_intervention_feedback,
    run_automation_cycle,
    update_notification_preferences,
)


class LearningGovernanceServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.Session()
        self.db.add(database.UserModel(
            id=1,
            username="governance-learner",
            email="governance@example.com",
            hashed_password="x",
        ))
        self.db.add(database.UserProfile(
            user_id=1,
            display_name="林同学",
            constitution="跨专业",
            health_goals="中医执业医师资格考试",
            exercise_preferences="knowledge_card、question",
        ))
        now = datetime.utcnow()
        self.db.add_all([
            database.LearningTask(
                task_id="TASK_1",
                user_id=1,
                task_type="learning",
                kp_ids_json='["KP_FJ_001"]',
                task_content="学习四君子汤",
                estimated_minutes=25,
                status="pending",
                created_at=now,
            ),
            database.KnowledgePoint(kp_id="KP_FJ_001", name="四君子汤"),
            database.KnowledgeMasteryState(
                mastery_state_id="MASTER_1",
                learner_id=1,
                kp_id="KP_FJ_001",
                mastery_score=30.0,
                mastery_confidence=0.8,
                attempt_count=5,
            ),
            database.LearnerKPReviewState(
                review_state_id="REVIEW_1",
                learner_id=1,
                kp_id="KP_FJ_001",
                review_stage="learning",
                stability_seconds=3600,
                last_review_at=now - timedelta(hours=1),
                retention_estimate=0.99,
                next_review_at=now - timedelta(hours=1),
                status="active",
            ),
            database.MistakeRecord(
                user_id=1,
                question_id="Q_1",
                kp_ids_json='["KP_FJ_001"]',
                error_type="配伍关系混淆",
            ),
            database.KnowledgeCardRecord(
                card_id="CARD_1",
                user_id=1,
                kp_id="KP_FJ_001",
                title="四君子汤知识卡",
            ),
            database.QuestionBankItem(
                question_id="QUESTION_0",
                stem="四君子汤的君药是什么？",
                kp_ids_json='["KP_FJ_001"]',
                difficulty=2,
                quality_score=0.85,
                source="curated_question_bank",
                status="active",
            ),
            database.LearningActivityRecord(
                user_id=1,
                activity_type="login",
                completion_status="completed",
                created_at=now,
            ),
        ])
        for index in range(5):
            self.db.add(database.LearningQuestionAttempt(
                attempt_id=f"ATTEMPT_{index}",
                user_id=1,
                question_id=f"QUESTION_{index}",
                is_correct=index < 2,
                response_time_seconds=120,
                answered_at=now,
            ))
        self.db.add(database.LearningQuestionAttempt(
            attempt_id="ATTEMPT_OLD",
            user_id=1,
            question_id="QUESTION_OLD",
            is_correct=True,
            answered_at=now - timedelta(days=20),
        ))
        self.db.add(database.MistakeRecord(
            user_id=1,
            question_id="Q_OLD",
            kp_ids_json='["KP_FJ_001"]',
            error_type="不应进入七日窗口",
            created_at=now - timedelta(days=20),
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_builds_explainable_insights_and_resource_report(self):
        insights = build_learning_insights(self.db, 1, days=7)
        report = build_resource_match_report(
            self.db,
            1,
            insights=insights,
            plan_context={"learning_task": {"kp_ids": ["KP_FJ_001"], "estimated_minutes": 25}},
        )

        self.assertEqual(insights["mastery_heatmap"][0]["kp_name"], "四君子汤")
        self.assertAlmostEqual(insights["mastery_heatmap"][0]["score"], 0.3)
        self.assertEqual(insights["mastery_heatmap"][0]["score_unit"], "percent_0_100")
        self.assertEqual(insights["mastery_heatmap"][0]["retention_source"], "dynamic_exponential")
        self.assertEqual(insights["mistake_distribution"][0]["error_type"], "配伍关系混淆")
        self.assertNotIn("不应进入七日窗口", [item["error_type"] for item in insights["mistake_distribution"]])
        self.assertEqual(insights["data_quality"]["attempt_count"], 5)
        self.assertGreater(insights["data_quality"]["sample_count"], 0)
        self.assertEqual(insights["overview"]["confidence_interpretation"], "data_coverage_score_not_statistical_confidence")
        self.assertTrue(insights["data_sources"])
        self.assertTrue(insights["methodology"]["references"])
        card_match = next(item for item in report["matches"] if item["resource_id"] == "CARD_1")
        self.assertGreater(card_match["components"]["knowledge_fit"], 0)
        question_match = next(item for item in report["matches"] if item["resource_id"] == "QUESTION_0")
        self.assertIsNotNone(question_match["components"]["difficulty_fit"])
        self.assertEqual(question_match["estimated_minutes_basis"], "user_response_time_mean_30d")
        self.assertEqual(report["summary"]["coverage"], 1.0)
        self.assertTrue(report["data_sources"])

    def test_resource_report_refuses_untargeted_recommendations(self):
        insights = build_learning_insights(self.db, 1, days=7)
        insights["weak_points"] = []
        report = build_resource_match_report(self.db, 1, insights=insights, plan_context={})

        self.assertEqual(report["matches"], [])
        self.assertIn("不会生成无依据推荐", report["no_match_reason"])

    def test_automation_is_idempotent_and_records_feedback(self):
        first = run_automation_cycle(
            self.db,
            1,
            plan_context={"learning_task": {"task_id": "TASK_1"}},
            days=7,
        )
        self.db.commit()
        second = run_automation_cycle(
            self.db,
            1,
            plan_context={"learning_task": {"task_id": "TASK_1"}},
            days=7,
        )
        self.db.commit()

        self.assertEqual(first["plan_review"]["review_id"], second["plan_review"]["review_id"])
        notifications = list_notifications(self.db, 1)
        self.assertEqual(notifications["unread_count"], len(notifications["items"]))
        if first["intervention"]:
            feedback = record_intervention_feedback(
                self.db, 1, first["intervention"]["intervention_id"], "accept"
            )
            self.assertEqual(feedback["lifecycle_status"], "accepted")

    def test_notification_preferences_and_plan_review_decision_are_user_owned(self):
        preferences = update_notification_preferences(
            self.db,
            1,
            {
                "digest_frequency": "daily",
                "categories": {"review_due": False},
                "quiet_hours": {"start": "21:30", "end": "07:30"},
            },
        )
        cycle = run_automation_cycle(self.db, 1, plan_context={}, days=7)
        review = cycle["plan_review"]
        if review["status"] == "proposal_pending":
            decided = decide_plan_review(self.db, 1, review["review_id"], "accept")
            self.assertEqual(decided["status"], "accepted")

        self.assertEqual(preferences["digest_frequency"], "daily")
        self.assertFalse(preferences["categories"]["review_due"])
        self.assertEqual(preferences["quiet_hours"]["start"], "21:30")


if __name__ == "__main__":
    unittest.main()

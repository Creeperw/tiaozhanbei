import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.learning_governance_service import (
    build_learning_insights,
    build_resource_match_report,
    build_resource_effectiveness_report,
    build_task_load_policy,
    decide_plan_review,
    list_notifications,
    record_intervention_feedback,
    record_plan_progression_event,
    record_resource_recommendation_event,
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
                difficulty_source="curated_question_bank",
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
        now = datetime.utcnow()
        for index in range(4):
            self.db.add(database.LearningQuestionAttempt(
                attempt_id=f"ATTEMPT_Q0_{index}",
                user_id=1,
                question_id="QUESTION_0",
                is_correct=index < 3,
                response_time_seconds=90,
                answered_at=now,
            ))
        self.db.commit()
        insights = build_learning_insights(self.db, 1, days=7)
        report = build_resource_match_report(
            self.db,
            1,
            insights=insights,
            plan_context={
                "learning_task": {
                    "items": [
                        {
                            "item_type": "knowledge_practice",
                            "kp_id": "KP_FJ_001",
                        }
                    ],
                    "estimated_minutes": 25,
                }
            },
        )

        self.assertEqual(insights["mastery_heatmap"][0]["kp_name"], "四君子汤")
        self.assertAlmostEqual(insights["mastery_heatmap"][0]["score"], 0.3)
        self.assertEqual(insights["mastery_heatmap"][0]["score_unit"], "percent_0_100")
        self.assertEqual(insights["mastery_heatmap"][0]["retention_source"], "dynamic_exponential")
        self.assertEqual(insights["mistake_distribution"][0]["error_type"], "配伍关系混淆")
        self.assertNotIn("不应进入七日窗口", [item["error_type"] for item in insights["mistake_distribution"]])
        self.assertEqual(insights["data_quality"]["attempt_count"], 9)
        self.assertGreater(insights["data_quality"]["sample_count"], 0)
        self.assertEqual(insights["overview"]["confidence_interpretation"], "data_coverage_score_not_statistical_confidence")
        self.assertTrue(insights["data_sources"])
        self.assertTrue(insights["methodology"]["references"])
        card_match = next(item for item in report["matches"] if item["resource_id"] == "CARD_1")
        self.assertGreater(card_match["components"]["knowledge_fit"], 0)
        question_match = next(item for item in report["matches"] if item["resource_id"] == "QUESTION_0")
        self.assertIn("difficulty_fit", question_match["components"])
        self.assertEqual(question_match["difficulty"], 2)
        self.assertIsNotNone(question_match["components"]["difficulty_fit"])
        self.assertEqual(
            question_match["component_sources"]["difficulty_fit"],
            "question_attempt:difficulty:2",
        )
        self.assertEqual(question_match["estimated_minutes_basis"], "user_response_time_mean_30d")
        self.assertEqual(report["summary"]["coverage"], 1.0)
        self.assertTrue(report["data_sources"])
        self.assertTrue(report["recommendation_view_id"].startswith("recommendation-view:"))
        self.assertEqual(
            report["matches"][0]["feedback"]["event_endpoint"],
            "/api/v1/resource-recommendations/events",
        )

    def test_task_load_policy_uses_missing_data_neutrally_and_reserves_review(self):
        policy = build_task_load_policy(
            self.db,
            1,
            plan_context={
                "learning_task": {"estimated_minutes": 25},
                "short_term_plan": {},
            },
            review_projection={
                "source": "canonical_review_memory",
                "due_count": 2,
            },
            days=7,
        )

        self.assertEqual(policy["policy_id"], "next-day-load-v1")
        self.assertEqual(policy["baseline_minutes"], 25)
        self.assertEqual(policy["recommended_minutes"], 25)
        self.assertEqual(policy["allocation"]["review_minutes"], 8)
        self.assertTrue(policy["constraints"]["does_not_fill_available_time"])
        self.assertFalse(
            policy["evidence_availability"]["task_completion_rate"]
        )

    def test_resource_feedback_funnel_and_mastery_gain_are_auditable(self):
        insights = build_learning_insights(self.db, 1, days=7)
        report = build_resource_match_report(
            self.db,
            1,
            insights=insights,
            plan_context={
                "learning_task": {
                    "items": [{"kp_id": "KP_FJ_001"}],
                    "estimated_minutes": 25,
                }
            },
        )
        recommendation = next(
            item for item in report["matches"] if item["resource_id"] == "CARD_1"
        )
        feedback = recommendation["feedback"]
        impression = record_resource_recommendation_event(
            self.db,
            1,
            event_type="impression",
            recommendation_view_id=feedback["recommendation_view_id"],
            resource_id=feedback["resource_id"],
            resource_type=feedback["resource_type"],
            kp_ids=feedback["kp_ids"],
        )
        click = record_resource_recommendation_event(
            self.db,
            1,
            event_type="click",
            recommendation_view_id=feedback["recommendation_view_id"],
            resource_id=feedback["resource_id"],
            resource_type=feedback["resource_type"],
            kp_ids=feedback["kp_ids"],
        )
        complete = record_resource_recommendation_event(
            self.db,
            1,
            event_type="complete",
            recommendation_view_id=feedback["recommendation_view_id"],
            resource_id=feedback["resource_id"],
            resource_type=feedback["resource_type"],
            kp_ids=feedback["kp_ids"],
        )
        self.db.query(database.LearningActivityRecord).filter_by(
            user_id=1,
            activity_type="resource_complete",
            resource_id=feedback["resource_id"],
        ).update(
            {
                database.LearningActivityRecord.created_at:
                    datetime.utcnow() - timedelta(minutes=1)
            }
        )
        self.db.add(
            database.LearningQuestionAttempt(
                attempt_id="ATTEMPT_AFTER_RESOURCE",
                user_id=1,
                question_id="QUESTION_0",
                is_correct=True,
                answered_at=datetime.utcnow(),
            )
        )
        self.db.query(database.KnowledgeMasteryState).filter_by(
            learner_id=1, kp_id="KP_FJ_001"
        ).update({database.KnowledgeMasteryState.mastery_score: 50.0})
        self.db.flush()

        effectiveness = build_resource_effectiveness_report(
            self.db, 1, days=30
        )

        self.assertTrue(impression["recorded"])
        self.assertTrue(click["recorded"])
        self.assertTrue(complete["recorded"])
        self.assertGreaterEqual(
            effectiveness["funnel"]["displayed_resource_count"], 1
        )
        self.assertGreaterEqual(
            effectiveness["funnel"]["clicked_resource_count"], 1
        )
        self.assertGreaterEqual(
            effectiveness["funnel"]["completed_resource_count"], 1
        )
        self.assertAlmostEqual(
            effectiveness["learning_outcomes"]["mastery_delta"], 0.2
        )
        self.assertGreaterEqual(
            effectiveness["learning_outcomes"]["post_resource_attempt_count"], 1
        )
        self.assertEqual(
            effectiveness["learning_outcomes"]["post_resource_accuracy"], 1.0
        )

    def test_practice_score_rate_uses_passed_practice_and_paper_scores(self):
        now = datetime.utcnow()

        def add_grading(
            suffix: str,
            *,
            attempt_type: str,
            score: float,
            max_score: float,
            decision: str = "pass",
        ) -> None:
            attempt_id = f"LEARNING_ATTEMPT_{suffix}"
            item_id = f"ATTEMPT_ITEM_{suffix}"
            artifact_id = f"GRADING_{suffix}"
            self.db.add(database.LearningAttemptRecord(
                attempt_id=attempt_id,
                learner_id=1,
                attempt_type=attempt_type,
                status="submitted",
                submitted_at=now,
            ))
            self.db.add(database.LearningAttemptItemRecord(
                attempt_item_id=item_id,
                attempt_id=attempt_id,
                question_version_id=f"QUESTION_VERSION_{suffix}",
            ))
            self.db.add(database.GradingResultRecord(
                artifact_id=artifact_id,
                attempt_item_id=item_id,
                version=1,
                score=score,
                max_score=max_score,
                status="reviewed",
            ))
            self.db.add(database.AuditResultRecord(
                audit_id=f"AUDIT_{suffix}",
                source_artifact_id=artifact_id,
                source_artifact_version=1,
                decision=decision,
                status="completed",
            ))

        add_grading("PRACTICE", attempt_type="practice", score=30, max_score=100)
        add_grading("PAPER", attempt_type="paper", score=40, max_score=50)
        add_grading("CASE", attempt_type="case", score=100, max_score=100)
        add_grading(
            "REJECTED", attempt_type="practice", score=100, max_score=100,
            decision="reject",
        )
        self.db.add(database.AuditResultRecord(
            audit_id="AUDIT_PRACTICE_DUPLICATE",
            source_artifact_id="GRADING_PRACTICE",
            source_artifact_version=1,
            decision="pass",
            status="completed",
        ))
        self.db.commit()

        insights = build_learning_insights(self.db, 1, days=7)
        score_rate = next(
            item for item in insights["dimensions"] if item["key"] == "accuracy"
        )

        self.assertEqual(score_rate["label"], "练习得分率")
        self.assertAlmostEqual(score_rate["value"], 70 / 150, places=4)
        self.assertEqual(score_rate["evidence_count"], 2)
        self.assertEqual(
            score_rate["formula"],
            "sum(passed_practice_and_paper_scores)/sum(corresponding_max_scores)",
        )
        self.assertEqual(
            score_rate["source_ids"],
            ["grading_result_records", "learning_attempts", "audit_result_records"],
        )

    def test_execution_dimension_declares_daily_atomic_task_provenance(self):
        insights = build_learning_insights(self.db, 1, days=7)
        execution = next(item for item in insights["dimensions"] if item["key"] == "execution")

        self.assertEqual(execution["source_ids"], ["daily_task_instances", "daily_task_items"])
        self.assertEqual(
            execution["formula"],
            "completed_non_cancelled_daily_items/non_cancelled_published_daily_items",
        )
        self.assertNotIn("learning_tasks", insights["data_quality"]["sources"])

    def test_empty_dimension_is_null_instead_of_false_zero(self):
        self.db.add(database.UserModel(
            id=2,
            username="empty-governance-learner",
            email="empty-governance@example.com",
            hashed_password="x",
        ))
        self.db.commit()

        insights = build_learning_insights(self.db, 2, days=7)

        self.assertTrue(insights["dimensions"])
        for dimension in insights["dimensions"]:
            self.assertEqual(dimension["status"], "insufficient_evidence")
            self.assertIsNone(dimension["value"])

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

    def test_three_consecutive_low_completion_days_request_short_replanning(self):
        initial_cycle = run_automation_cycle(
            self.db,
            1,
            plan_context={
                "short_term_plan": {
                    "plan_id": "SHORT_LOW_1",
                    "recovery_policy": {
                        "trigger_conditions": ["连续3天任务完成率低于50%"]
                    },
                }
            },
            days=7,
        )
        initial_review_id = initial_cycle["plan_review"]["review_id"]
        now = datetime.utcnow()
        for offset in range(3):
            created_at = now - timedelta(days=offset)
            host_task_id = f"LOW_COMPLETION_TASK_{offset}"
            self.db.add(database.DailyTaskInstanceRecord(
                host_task_id=host_task_id,
                host_task_version=1,
                user_id=1,
                status="active",
                created_at=created_at,
            ))
            for ordinal in range(2):
                self.db.add(database.DailyTaskItemRecord(
                    task_item_id=f"LOW_COMPLETION_ITEM_{offset}_{ordinal}",
                    host_task_id=host_task_id,
                    host_task_version=1,
                    user_id=1,
                    kp_id="KP_FJ_001",
                    ordinal=ordinal,
                    status="pending",
                    created_at=created_at,
                ))
        self.db.flush()

        cycle = run_automation_cycle(
            self.db,
            1,
            plan_context={
                "short_term_plan": {
                    "plan_id": "SHORT_LOW_1",
                    "recovery_policy": {
                        "trigger_conditions": ["连续3天任务完成率低于50%"]
                    },
                }
            },
            days=7,
        )

        review = cycle["plan_review"]
        self.assertEqual(review["review_id"], initial_review_id)
        self.assertEqual(review["outcome"], "short_replan_suggested")
        self.assertEqual(review["low_completion_streak_days"], 3)
        self.assertEqual(
            review["proposal"]["operation"],
            "replan_for_low_completion",
        )
        self.assertTrue(review["proposal"]["requires_confirmation"])
        workflow_request = review["proposal"]["workflow_request"]
        self.assertEqual(workflow_request["plan_scope"], "short_term")
        self.assertIn("强制调整", workflow_request["user_request"])
        notification = next(
            item
            for item in list_notifications(self.db, 1)["items"]
            if item["category"] == "plan_review"
        )
        self.assertEqual(
            notification["action"]["workflow_request"],
            workflow_request,
        )

    def test_canonical_review_projection_drives_insights_notifications_and_plan_review(self):
        projection = {
            "source": "canonical_review_memory",
            "due_count": 6,
            "total_count": 7,
            "active_task_count": 2,
        }

        insights = build_learning_insights(
            self.db,
            1,
            days=7,
            review_projection=projection,
        )
        cycle = run_automation_cycle(
            self.db,
            1,
            plan_context={},
            days=7,
            review_projection=projection,
        )

        self.assertEqual(insights["overview"]["due_review_count"], 6)
        self.assertEqual(
            insights["overview"]["review_projection_source"],
            "canonical_review_memory",
        )
        self.assertEqual(
            cycle["plan_review"]["proposal"]["operation"],
            "add_review_window",
        )
        notifications = list_notifications(self.db, 1)["items"]
        due_notification = next(
            item for item in notifications if item["category"] == "review_due"
        )
        self.assertIn("6 个", due_notification["message"])

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

    def test_plan_progression_notification_is_idempotent(self):
        event = {
            "event_id": "PROGRESSION_TASK_1_1_1_1",
            "completed_layer": "daily_task",
            "task_id": "TASK_1",
            "stage": 1,
            "next_stage": 2,
            "short_term_completed": True,
            "long_term_stage_passed": True,
            "long_term_completed": False,
        }

        first = record_plan_progression_event(self.db, 1, event)
        second = record_plan_progression_event(self.db, 1, event)

        self.assertEqual(first["notification_id"], second["notification_id"])
        self.assertEqual(
            self.db.query(database.LearningActivityRecord).filter_by(
                user_id=1,
                activity_type="plan_progression",
                resource_id=event["event_id"],
            ).count(),
            1,
        )


if __name__ == "__main__":
    unittest.main()

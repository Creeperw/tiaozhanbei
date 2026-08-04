import unittest
from datetime import datetime, timedelta, timezone


UTC = timezone.utc

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from APP.backend import database


class MemoryAgentServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def test_builds_global_learner_context_brief_from_learning_records(self):
        from APP.backend.memory_agent_service import build_learner_context_brief

        db = self.Session()
        try:
            db.add(database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x"))
            db.add(
                database.UserProfile(
                    user_id=1,
                    display_name="Alice",
                    constitution="跨专业进阶群体",
                    health_goals="4 周内掌握脾胃气虚证与四君子汤",
                    diet_restrictions="每天 45 分钟",
                    exercise_preferences="知识卡、错题复盘、案例辨证",
                    medical_history="证型到方剂匹配薄弱",
                    custom_needs="需要循序渐进规划",
                )
            )
            db.add_all(
                [
                    database.PersonalizationMemory(
                        user_id=1,
                        category="short_term",
                        title="近期薄弱点",
                        content="最近错在脾胃气虚证和中焦虚寒证辨析",
                        confidence=0.86,
                    ),
                    database.PersonalizationMemory(
                        user_id=1,
                        category="long_term",
                        title="长期目标",
                        content="备考中医执业医师",
                        confidence=0.9,
                    ),
                    database.PersonalizationMemory(
                        user_id=1,
                        category="preference",
                        title="资源偏好",
                        content="偏好知识卡和错题变式",
                        confidence=0.82,
                    ),
                ]
            )
            db.add(
                database.LearnerKnowledgeMastery(
                    user_id=1,
                    kp_id="KP_FJ_001",
                    mastery=0.58,
                    confidence=0.74,
                    wrong_count=3,
                    review_count=2,
                    next_review_at=datetime.now(UTC) + timedelta(days=1),
                    mastery_status="weak",
                )
            )
            db.add(
                database.LearningPlanRecord(
                    user_id=1,
                    plan_type="weekly",
                    title="四君子汤专题突破",
                    summary="本周完成知识卡、短练和错题复盘",
                    status="active",
                    payload_json='{"daily_tasks": 3}',
                )
            )
            db.add(
                database.LearningActivityRecord(
                    user_id=1,
                    activity_type="practice",
                    resource_id="q_sijunzi_001",
                    resource_type="question",
                    duration_minutes=25,
                    completion_status="completed",
                    score=60.0,
                    payload_json='{"kp_id": "KP_FJ_001"}',
                )
            )
            db.add(
                database.LearningInterventionRecord(
                    user_id=1,
                    t_stage="T1",
                    action="generate_mistake_review_card",
                    reason="高耗低效，需要错题复盘",
                    effect_status="pending",
                    cooldown_hours=24,
                )
            )
            db.add(
                database.MemoryCandidate(
                    user_id=1,
                    title="待确认记忆",
                    content="用户近期希望减少任务量",
                    reason="近期完成率下降",
                    confidence=0.7,
                )
            )
            db.add(
                database.MemorySummary(
                    user_id=1,
                    description="近期聊天摘要",
                    key_facts='{"active_goal": "四君子汤专题突破"}',
                    confidence=0.78,
                )
            )
            db.add(
                database.FeedbackRecord(
                    user_id=1,
                    feedback_type="problem",
                    rating="low",
                    reason="任务偏难",
                    user_feedback="希望先看知识卡再做题",
                )
            )
            db.commit()

            brief = build_learner_context_brief(db, 1)
            payload = brief.model_dump()

            self.assertEqual(payload["learner_id"], "1")
            self.assertEqual(payload["learner_group"], "跨专业进阶群体")
            self.assertEqual(payload["goal"], "4 周内掌握脾胃气虚证与四君子汤")
            self.assertEqual(payload["profile"]["learning_goal"], "4 周内掌握脾胃气虚证与四君子汤")
            self.assertEqual(payload["short_term_memory"]["active_items"][0]["title"], "近期薄弱点")
            long_term_titles = {item["title"] for item in payload["long_term_memory"]["stable_items"]}
            self.assertIn("长期目标", long_term_titles)
            self.assertIn("资源偏好", long_term_titles)
            self.assertEqual(payload["planning_memory"]["active_plans"][0]["title"], "四君子汤专题突破")
            self.assertEqual(payload["learning_state"]["weak_kp_ids"], ["KP_FJ_001"])
            self.assertEqual(payload["learning_state"]["recent_activities"][0]["activity_type"], "practice")
            self.assertEqual(payload["learning_state"]["interventions"][0]["t_stage"], "T1")
            self.assertEqual(payload["short_term_memory"]["pending_candidates"][0]["title"], "待确认记忆")
            self.assertEqual(payload["short_term_memory"]["summaries"][0]["description"], "近期聊天摘要")
            self.assertEqual(payload["learning_state"]["feedback"][0]["feedback_type"], "problem")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()

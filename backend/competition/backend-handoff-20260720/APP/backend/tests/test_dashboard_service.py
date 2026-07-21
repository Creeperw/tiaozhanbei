import json
import unittest
from pathlib import Path

from APP.backend.dashboard_service import build_dashboard_payload


class BuildDashboardPayloadTests(unittest.TestCase):
    def test_builds_training_dashboard_from_existing_user_context(self):
        payload = build_dashboard_payload(
            user={"username": "alice"},
            profile={
                "display_name": "Alice",
                "health_goals": "6 个月内完成中医基础学习",
                "custom_needs": "希望每天 20 分钟微学习",
                "exercise_preferences": "案例和题目结合",
            },
            active_memories=[
                {"category": "long_term", "content": "最近重点在方剂学", "title": "学习重点"},
                {"category": "preference", "content": "偏好案例式学习", "title": "资源偏好"},
            ],
            recent_events=[
                {"agent_name": "planner", "event_type": "intent", "output_summary": "生成方剂学习建议"},
                {"agent_name": "knowledge", "event_type": "run", "output_summary": "检索到四君子汤资料"},
            ],
            recent_sessions=[
                {"id": "s1", "title": "四君子汤学习", "created_at": "2026-07-06T10:00:00"},
                {"id": "s2", "title": "脾胃气虚辨析", "created_at": "2026-07-05T10:00:00"},
            ],
        )

        self.assertEqual(payload["hero"]["greeting"], "Alice，你的培训助手已准备就绪")
        self.assertEqual(payload["hero"]["goal"], "6 个月内完成中医基础学习")
        self.assertEqual(payload["hero"]["focus"], "希望每天 20 分钟微学习")
        self.assertEqual(payload["business_modules"][0]["key"], "assistant")
        self.assertEqual(payload["recommendations"][0]["reason"], "基于你的长期目标与近期关注内容生成")
        self.assertTrue(any(card["key"] == "activity" for card in payload["status_cards"]))
        self.assertEqual(payload["continue_learning"][0]["session_id"], "s1")

    def test_active_learning_target_overrides_legacy_profile_goal(self):
        payload = build_dashboard_payload(
            user={"username": "learner"},
            profile={"health_goals": "旧的自由文本目标"},
            learning_target={
                "target_type": "certification",
                "exam_track_id": "EXAM_2025_TCM_PHYSICIAN",
                "exam_name": "2025 中医执业医师资格考试",
                "syllabus_version": "2.0.0",
                "exam_date": "2026-10-01",
            },
            active_memories=[],
            recent_events=[],
            recent_sessions=[],
        )

        self.assertEqual(payload["hero"]["goal"], "2025 中医执业医师资格考试")
        self.assertEqual(payload["learning_target"]["exam_track_id"], "EXAM_2025_TCM_PHYSICIAN")
        self.assertIn("2025 中医执业医师资格考试", payload["recommendations"][1]["summary"])
        self.assertEqual(
            payload["monitoring_summary"]["l0_baseline"]["goal"],
            "2025 中医执业医师资格考试",
        )

    def test_falls_back_to_default_dashboard_values_when_context_is_sparse(self):
        payload = build_dashboard_payload(
            user={"username": "learner"},
            profile={},
            active_memories=[],
            recent_events=[],
            recent_sessions=[],
        )

        self.assertEqual(payload["hero"]["greeting"], "learner，你的培训助手已准备就绪")
        self.assertEqual(payload["hero"]["goal"], "先完善学习目标，系统会据此生成更精准的推荐")
        self.assertEqual(payload["continue_learning"], [])
        self.assertEqual(payload["recommendations"][0]["resource_type"], "question")
        self.assertIn("先完善学习目标", payload["recommendations"][0]["summary"])
        self.assertTrue(any(card["value"] == "待积累" for card in payload["status_cards"]))

    def test_continue_learning_prefers_last_activity_time_over_creation_time(self):
        payload = build_dashboard_payload(
            user={"username": "learner"},
            profile={},
            active_memories=[],
            recent_events=[],
            recent_sessions=[
                {
                    "id": "older-session",
                    "title": "较早创建但最近活跃",
                    "created_at": "2026-07-01T10:00:00",
                    "updated_at": "2026-07-06T18:30:00",
                }
            ],
        )

        self.assertEqual(payload["continue_learning"][0]["updated_at"], "2026-07-06T18:30:00")

    def test_recommendations_include_question_case_video_and_resource_tracks(self):
        payload = build_dashboard_payload(
            user={"username": "learner"},
            profile={
                "health_goals": "备考方剂学",
                "exercise_preferences": "题目、案例、视频",
                "medical_history": "四君子汤和理中丸容易混淆",
                "custom_needs": "每天 20 分钟",
            },
            active_memories=[{"title": "薄弱点", "content": "四君子汤和理中丸容易混淆"}],
            recent_events=[{"agent_name": "planner", "output_summary": "规划了方剂学短练"}],
            recent_sessions=[{"id": "s1", "title": "方剂复习", "updated_at": "2026-07-06T18:30:00"}],
        )

        resource_types = [item["resource_type"] for item in payload["recommendations"]]
        self.assertEqual(resource_types, ["question", "case", "video", "resource"])
        self.assertTrue(all(item["reason"] for item in payload["recommendations"]))
        self.assertTrue(all(item["source_signal"] for item in payload["recommendations"]))
        self.assertEqual(payload["recommendations"][0]["target_page"], "practice")
        self.assertIn("四君子汤", payload["recommendations"][0]["summary"])

    def test_dashboard_includes_l0_l3_t_stage_and_today_tasks(self):
        payload = build_dashboard_payload(
            user={"username": "learner"},
            profile={
                "constitution": "跨专业进阶群体",
                "health_goals": "6 个月内备考中医执业医师",
                "diet_restrictions": "每天 30 分钟",
                "exercise_preferences": "案例、知识卡",
            },
            active_memories=[{"title": "薄弱点", "content": "脾胃气虚证辨析"}],
            recent_events=[{"agent_name": "diagnosis", "output_summary": "近期专注方剂学补弱"}],
            recent_sessions=[{"id": "s1", "title": "脾胃辨析", "updated_at": "2026-07-06T18:30:00"}],
        )

        self.assertEqual(payload["monitoring_summary"]["l0_baseline"]["learner_group"], "跨专业进阶群体")
        self.assertEqual(payload["monitoring_summary"]["l3_monitoring"]["agent_events"], 1)
        self.assertEqual(payload["monitoring_summary"]["t_stage"]["stage_id"], "observing")
        self.assertEqual(payload["today_tasks"][0]["duration"], "每天 30 分钟")
        self.assertTrue(any(card["key"] == "t-stage" for card in payload["status_cards"]))
        self.assertFalse(any(card["key"] == "today" for card in payload["status_cards"]))

    def test_today_task_ignores_serialized_onboarding_memory(self):
        onboarding_payload = {
            "status": "onboarding_completed",
            "survey_answers": {
                "current_difficulties": "方剂组成混淆、缺少练习反馈",
                "daily_available_minutes": 45,
            },
        }
        payload = build_dashboard_payload(
            user={"username": "learner"},
            profile={
                "medical_history": "方剂组成混淆、缺少练习反馈",
                "diet_restrictions": "每天 45 分钟；偏好时段 晚间固定学习时段",
            },
            active_memories=[
                {
                    "category": "note",
                    "source": "onboarding_survey",
                    "title": "Onboarding Survey",
                    "content": json.dumps(onboarding_payload, ensure_ascii=False),
                }
            ],
            recent_events=[],
            recent_sessions=[],
        )

        reason = payload["today_tasks"][0]["reason"]
        self.assertIn("方剂组成混淆、缺少练习反馈", reason)
        self.assertNotIn("onboarding_completed", reason)
        self.assertNotIn("survey_answers", reason)

    def test_dashboard_payload_includes_engagement_notices(self):
        payload = build_dashboard_payload(
            user={"username": "demo"},
            profile={"health_goals": "方剂学复习", "diet_restrictions": "晚间20:00–21:00"},
            active_memories=[],
            recent_events=[],
            recent_sessions=[],
            announcements=[{"notice_id": "NOTICE_1", "type": "profile_conflict", "title": "学习时段可能有变化"}],
            checkin_status={"checked_in_today": False, "streak": 0},
            difficulty_notice={"notice_id": "NOTICE_DIFF_1", "suggested_difficulty": "D2"},
        )

        self.assertEqual(payload["announcements"][0]["type"], "profile_conflict")
        self.assertFalse(payload["checkin_status"]["checked_in_today"])
        self.assertEqual(payload["difficulty_notice"]["suggested_difficulty"], "D2")

    def test_phase3_sample_data_drives_recommendations_and_status_summary(self):
        sample_path = Path(__file__).resolve().parents[1] / "sample_data" / "phase3_dashboard_seed.json"
        seed = json.loads(sample_path.read_text(encoding="utf-8"))
        item = seed["users"][0]

        payload = build_dashboard_payload(
            user={"username": item["username"]},
            profile=item["profile"],
            active_memories=item["memories"],
            recent_events=item["agent_events"],
            recent_sessions=item["sessions"],
        )

        self.assertEqual(payload["monitoring_summary"]["l0_baseline"]["learner_group"], "跨专业进阶群体")
        self.assertIn("四君子汤", payload["recommendations"][0]["summary"])
        self.assertEqual(payload["monitoring_summary"]["t_stage"]["stage_id"], "observing")
        self.assertTrue(payload["today_tasks"])


if __name__ == "__main__":
    unittest.main()

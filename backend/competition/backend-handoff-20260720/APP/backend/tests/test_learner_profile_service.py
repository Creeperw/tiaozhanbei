import unittest

from APP.backend.learner_profile_service import (
    apply_learner_profile_update,
    build_learner_profile_payload,
    map_learner_profile_update,
)


class LearnerProfileServiceTests(unittest.TestCase):
    def test_maps_existing_profile_fields_to_learning_profile_semantics(self):
        payload = build_learner_profile_payload({
            "display_name": "Alice",
            "constitution": "跨专业进阶群体",
            "health_goals": "6 个月内备考中医执业医师",
            "diet_restrictions": "工作日每天 30 分钟，周末 2 小时",
            "exercise_preferences": "案例、对比卡、短视频",
            "medical_history": "方剂学薄弱，容易混淆证型",
            "custom_needs": "希望先补脾胃辨证",
        })

        self.assertEqual(payload["display_name"], "Alice")
        self.assertEqual(payload["learner_group"], "跨专业进阶群体")
        self.assertEqual(payload["learning_goal"], "6 个月内备考中医执业医师")
        self.assertEqual(payload["time_constraints"], "工作日每天 30 分钟，周末 2 小时")
        self.assertEqual(payload["resource_preferences"], "案例、对比卡、短视频")
        self.assertEqual(payload["current_difficulties"], "方剂学薄弱，容易混淆证型")
        self.assertEqual(payload["learning_needs"], "希望先补脾胃辨证")

    def test_maps_learning_profile_updates_back_to_existing_profile_fields(self):
        update = map_learner_profile_update({
            "display_name": "Bob",
            "learner_group": "学历教育群体",
            "learning_goal": "完成方剂学期末复习",
            "time_constraints": "每天 45 分钟",
            "resource_preferences": "题目和知识卡",
            "current_difficulties": "中药功效记忆不牢",
            "learning_needs": "希望推荐每日练习",
        })

        self.assertEqual(update, {
            "display_name": "Bob",
            "constitution": "学历教育群体",
            "health_goals": "完成方剂学期末复习",
            "diet_restrictions": "每天 45 分钟",
            "exercise_preferences": "题目和知识卡",
            "medical_history": "中药功效记忆不牢",
            "custom_needs": "希望推荐每日练习",
        })

    def test_ignores_unknown_update_fields(self):
        update = map_learner_profile_update({
            "learning_goal": "学习中医基础",
            "unknown": "不应写入",
        })

        self.assertEqual(update, {"health_goals": "学习中医基础"})
    def test_auto_update_skips_locked_learner_profile_fields(self):
        class DummyProfile:
            display_name = ""
            constitution = "学历教育"
            health_goals = "通过方剂学期末考试"
            diet_restrictions = "晚间20:00–21:00"
            exercise_preferences = "知识卡片"
            medical_history = "类方鉴别困难"
            custom_needs = ""
            locked_fields_json = '["time_constraints"]'
            lock_reason_json = '{"time_constraints":"用户手动确认"}'

        profile = DummyProfile()
        changed = apply_learner_profile_update(
            profile,
            {
                "time_constraints": "行为日志显示18:00–19:00更活跃",
                "resource_preferences": "刷题",
            },
            source="learning_analytics_service",
        )

        self.assertEqual(changed, {"exercise_preferences": "刷题"})
        self.assertEqual(profile.diet_restrictions, "晚间20:00–21:00")
        self.assertEqual(profile.exercise_preferences, "刷题")
        self.assertEqual(build_learner_profile_payload(profile)["time_constraints"], "晚间20:00–21:00")


if __name__ == "__main__":
    unittest.main()

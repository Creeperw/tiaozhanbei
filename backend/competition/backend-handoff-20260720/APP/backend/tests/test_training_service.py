import importlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch


class TrainingServicePhase4Tests(unittest.TestCase):
    def _service(self):
        try:
            return importlib.import_module("APP.backend.training_service")
        except ModuleNotFoundError as exc:
            self.fail(f"training_service module is missing: {exc}")

    def test_grades_answer_and_builds_mistake_record_for_wrong_submission(self):
        service = self._service()

        payload = service.grade_practice_submission(
            profile={
                "constitution": "跨专业进阶群体",
                "health_goals": "6 个月内完成方剂学补弱",
                "exercise_preferences": "对比卡和短练",
                "medical_history": "四君子汤和理中丸容易混淆",
            },
            memories=[{"category": "short_term", "title": "薄弱点", "content": "脾胃气虚证辨析"}],
            submission={
                "question_id": "q_sijunzi_001",
                "question_type": "single_choice",
                "stem": "四君子汤主治的核心证型是？",
                "student_answer": "中焦虚寒证",
                "standard_answer": "脾胃气虚证",
                "rubric": "答出脾胃气虚证得满分，混为中焦虚寒证需回看四君子汤与理中丸对比。",
                "knowledge_points": ["四君子汤", "脾胃气虚证"],
                "difficulty": 2,
            },
        )

        self.assertEqual(payload["grading"]["is_correct"], False)
        self.assertLess(payload["grading"]["score"], 100)
        self.assertEqual(payload["grading"]["error_type"], "待结合作答情况分析")
        self.assertIn("错因暂不自动下结论", payload["grading"]["analysis"])
        self.assertNotIn("知识点掌握不牢", payload["grading"]["analysis"])
        self.assertIn("四君子汤", payload["grading"]["analysis"])
        self.assertEqual(payload["mistake_record"]["category"], "mistake")
        self.assertEqual(payload["mistake_record"]["source"], "objective_practice_grading")
        self.assertNotIn("remediation", payload)
        self.assertEqual(payload["agent_trace"][0]["agent"], "planner_agent")
        self.assertEqual(payload["agent_trace"][-1]["agent"], "memory_agent")

    def test_multiple_choice_with_any_wrong_option_scores_zero(self):
        service = self._service()
        payload = service.grade_practice_submission(
            profile={},
            memories=[],
            submission={
                "question_id": "q-multiple",
                "question_type": "multiple_choice",
                "stem": "请选择正确项",
                "student_answer": "A,C",
                "standard_answer": "A,B",
                "knowledge_points": ["022758"],
                "knowledge_point_names": ["感冒辨证"],
            },
        )
        self.assertEqual(payload["grading"]["score"], 0)
        self.assertFalse(payload["grading"]["is_correct"])
        self.assertIn("感冒辨证", payload["grading"]["analysis"])
        self.assertNotIn("022758", payload["grading"]["analysis"])

    def test_subjective_answer_uses_model_expert_and_independent_audit(self):
        service = self._service()
        expert_service = importlib.import_module("APP.backend.expert_agent_service")

        class FakeClient:
            def __init__(self, role):
                self.role = role

            def chat(self, *args, **kwargs):
                if self.role == "executor":
                    return json.dumps({
                        "score": 72,
                        "max_score": 100,
                        "is_correct": False,
                        "error_types": ["宣肺散寒要点遗漏"],
                        "error_reason": "治法表述不完整",
                        "feedback": "已答出辛温解表，但遗漏宣肺散寒。",
                        "dimension_scores": {"治法方向": 50, "关键要点": 22},
                        "confidence": 0.92,
                    }, ensure_ascii=False)
                return json.dumps({
                    "decision": "pass",
                    "reason": "评分符合参考答案与评分要点",
                    "confidence": 0.9,
                }, ensure_ascii=False)

        with patch.object(expert_service, "build_llm_client", side_effect=lambda role: FakeClient(role)):
            payload = service.grade_practice_submission(
                profile={},
                memories=[],
                submission={
                    "question_id": "q-short",
                    "question_type": "short_answer",
                    "stem": "简述风寒感冒治法",
                    "student_answer": "辛温解表",
                    "standard_answer": "辛温解表，宣肺散寒",
                    "rubric": "治法方向50分，完整说明宣肺散寒50分",
                    "knowledge_points": ["022758"],
                    "knowledge_point_names": ["风寒感冒"],
                },
            )
        self.assertEqual(payload["grading"]["grading_source"], "expert_agent_model")
        self.assertEqual(payload["grading"]["score"], 72)
        self.assertEqual(payload["audit"]["audit_source"], "audit_agent_model")
        self.assertEqual(payload["audit"]["decision"], "pass")
        self.assertTrue(any(item["agent"] == "expert_agent" and item["status"] == "success" for item in payload["agent_trace"]))

    def test_subjective_model_failure_is_visible_and_withholds_audit(self):
        service = self._service()
        expert_service = importlib.import_module("APP.backend.expert_agent_service")
        with patch.object(expert_service, "build_llm_client", side_effect=RuntimeError("offline")):
            payload = service.grade_practice_submission(
                profile={}, memories=[], submission={
                    "question_id": "q-short-fallback",
                    "question_type": "short_answer",
                    "stem": "简述治法",
                    "student_answer": "辛温解表",
                    "standard_answer": "辛温解表，宣肺散寒",
                    "knowledge_points": ["022758"],
                },
            )
        self.assertEqual(payload["grading"]["grading_source"], "rule_fallback")
        self.assertEqual(payload["audit"]["decision"], "needs_human_review")
        self.assertTrue(any(item["agent"] == "expert_agent" and item["status"] == "failed" for item in payload["agent_trace"]))

    def test_builds_learning_plan_summary_with_daily_tasks_from_profile_and_mistakes(self):
        service = self._service()

        payload = service.build_learning_plan_summary(
            profile={
                "constitution": "学历教育群体",
                "health_goals": "4 周内完成方剂学期末复习",
                "diet_restrictions": "每天 45 分钟",
                "exercise_preferences": "题目、案例辨证和知识热力图",
                "medical_history": "病机到治法的推理链薄弱",
            },
            memories=[
                {"category": "mistake", "title": "错题：四君子汤", "content": "错因：证型-方剂匹配错误；知识点：四君子汤、脾胃气虚证"},
                {"category": "preference", "title": "资源偏好", "content": "偏好案例辨证"},
            ],
            events=[{"agent_name": "diagnosis_agent", "output_summary": "建议先补证型与方剂匹配"}],
        )

        self.assertEqual(payload["plan_summary"]["goal"], "4 周内完成方剂学期末复习")
        self.assertEqual(payload["plan_summary"]["learner_group"], "学历教育群体")
        self.assertTrue(payload["weekly_plan"]["focus"])
        self.assertGreaterEqual(len(payload["daily_tasks"]), 3)
        self.assertTrue(any(task["type"] == "mistake_review" for task in payload["daily_tasks"]))
        self.assertIn("45", payload["constraints"]["time_budget"])

    def test_builds_learning_report_with_weak_points_resource_match_and_t_stage(self):
        service = self._service()

        payload = service.build_learning_report(
            profile={
                "constitution": "大众兴趣群体",
                "health_goals": "系统了解中医基础文化",
                "diet_restrictions": "每周 3 次，每次 15 分钟",
                "exercise_preferences": "通俗知识卡、视频和生活化案例",
                "medical_history": "术语基础薄弱",
            },
            memories=[
                {"category": "mistake", "title": "错题：阴阳", "content": "错因：概念混淆；知识点：阴阳五行"},
                {"category": "short_term", "title": "薄弱点", "content": "术语基础薄弱"},
            ],
            events=[
                {"agent_name": "practice_agent", "event_type": "grading", "output_summary": "完成 1 次练习批改"},
                {"agent_name": "diagnosis_agent", "event_type": "report", "output_summary": "建议降维解释"},
            ],
        )

        self.assertEqual(payload["learner_overview"]["learner_group"], "大众兴趣群体")
        self.assertTrue(any(item["name"] == "中医基础" for item in payload["mastery_radar"]))
        self.assertTrue(payload["weak_points"])
        self.assertEqual(payload["mistake_summary"]["total_mistakes"], 1)
        self.assertGreaterEqual(payload["resource_match"]["difficulty_match"], 0.85)
        self.assertIn(payload["t_stage"]["stage_id"], ["T0", "T5", "insufficient_data"])
        self.assertTrue(payload["next_actions"])
    def test_phase4_sample_data_drives_training_loop_outputs(self):
        sample_path = Path(__file__).resolve().parents[1] / "sample_data" / "phase4_training_seed.json"
        seed = json.loads(sample_path.read_text(encoding="utf-8"))
        item = seed["users"][0]
        submission = item["practice_submissions"][0]

        service = self._service()
        graded = service.grade_practice_submission(
            profile=item["profile"],
            memories=item["memories"],
            submission=submission,
        )
        memories = [*item["memories"], graded["mistake_record"]]
        plan = service.build_learning_plan_summary(
            profile=item["profile"],
            memories=memories,
            events=item["agent_events"],
        )
        report = service.build_learning_report(
            profile=item["profile"],
            memories=memories,
            events=item["agent_events"],
        )

        self.assertEqual(graded["grading"]["is_correct"], False)
        self.assertTrue(any(task["type"] == "mistake_review" for task in plan["daily_tasks"]))
        self.assertGreaterEqual(report["mistake_summary"]["total_mistakes"], 1)


if __name__ == "__main__":
    unittest.main()

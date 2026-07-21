import importlib
import unittest
from unittest.mock import patch

from APP.backend.agent_contracts import DiagnosisReport, EvidenceItem, EvidencePack, ExpertArtifact, LearnerContextBrief, ReviewDecision


class CrossValidationAdoptionTests(unittest.TestCase):
    def _review(self, decision: str = "pass"):
        return ReviewDecision(
            decision=decision,
            reviewer="cross_validation_service",
            reason="test",
            source_scope="cross_validation",
            source_id="review-001",
            kp_ids=["KP_FJ_001"],
            confidence=0.9,
            fact_consistency=1.0,
            evidence_coverage=1.0,
            difficulty_match=1.0,
            knowledge_coverage=1.0,
            safety_risk="low",
            conflicts=[],
        )

    def _summary(self, decision: str = "pass"):
        return {
            "decision": decision,
            "overall_score": 1.0,
            "needs_human_review": decision == "human_review",
            "safety_risk": "low",
            "conflicts": [],
            "warnings": [],
            "lenses": {"audit": {"score": 1.0}},
        }

    def _learner_context(self):
        return LearnerContextBrief(
            learner_id="learner-009",
            learner_group="方剂学补弱班",
            goal="稳定掌握四君子汤与理中丸的辨析",
            source_scope="memory_agent",
            source_id="learner:9",
            kp_ids=["KP_FJ_001", "KP_ZD_021"],
            confidence=0.9,
            learning_state={"target_difficulty": 2},
        )

    def _evidence_pack(self):
        return EvidencePack(
            source_scope="knowledge_base_agent",
            source_id="pack-009",
            kp_ids=["KP_FJ_001", "KP_ZD_021"],
            resolved_kp_ids=["KP_FJ_001", "KP_ZD_021"],
            confidence=0.95,
            items=[
                EvidenceItem(
                    source_scope="knowledge_base",
                    source_id="SRC_FJ_001",
                    summary="四君子汤主治脾胃气虚证。",
                    kp_ids=["KP_FJ_001", "KP_ZD_021"],
                    confidence=0.98,
                )
            ],
        )

    def _diagnosis_report(self):
        return DiagnosisReport(
            diagnosis_id="diag-009",
            stage_id="T5",
            stage_name="难度不适",
            summary="建议维持难度 2。",
            source_scope="diagnosis_agent",
            source_id="diag-source-009",
            kp_ids=["KP_FJ_001", "KP_ZD_021"],
            confidence=0.88,
        )

    def test_resource_generation_surfaces_call_cross_validation_service(self):
        service = importlib.import_module("APP.backend.expert_agent_service")
        with patch("APP.backend.expert_agent_service.cross_validate_output", return_value=(self._review(), self._summary())) as patched:
            artifact = service.generate_handout(
                learner_context=self._learner_context(),
                evidence_pack=self._evidence_pack(),
                diagnosis_report=self._diagnosis_report(),
                request={"topic": "脾胃气虚证 + 四君子汤", "difficulty": 2},
            )

        self.assertEqual(artifact.content["review_decision"]["decision"], "pass")
        patched.assert_called_once()

    def test_grading_surface_calls_cross_validation_service(self):
        service = importlib.import_module("APP.backend.expert_agent_service")
        with patch("APP.backend.expert_agent_service.cross_validate_grading_output", return_value=(self._review(), self._summary())) as patched:
            artifact = service.grade_submission(
                learner_context=self._learner_context(),
                evidence_pack=self._evidence_pack(),
                diagnosis_report=self._diagnosis_report(),
                submission={
                    "question_id": "q-1",
                    "stem": "四君子汤主治的核心证型是？",
                    "student_answer": "中焦虚寒证",
                    "standard_answer": "脾胃气虚证",
                    "rubric": "答出脾胃气虚证得满分。",
                    "knowledge_points": ["四君子汤", "脾胃气虚证"],
                    "difficulty": 2,
                },
            )

        self.assertEqual(artifact.content["review_decision"]["decision"], "pass")
        patched.assert_called_once()

    def test_planning_surface_calls_cross_validation_service(self):
        service = importlib.import_module("APP.backend.planner_agent_service")
        with patch("APP.backend.planner_agent_service.cross_validate_output", return_value=(self._review(), self._summary())) as patched:
            plan = service.generate_agent_execution_plan(
                learner_context=self._learner_context(),
                user_request="帮我制定四君子汤学习路径",
                available_tools=["search_rag"],
            )

        self.assertEqual(plan.plan_summary["review_decision"]["decision"], "pass")
        patched.assert_called_once()

    def test_dynamic_question_insertion_surface_calls_cross_validation_service(self):
        service = importlib.import_module("APP.backend.deep_training_service")
        with patch("APP.backend.deep_training_service.validate_dynamic_question_selection", return_value=(self._review(), self._summary())) as patched:
            result = service.select_practice_questions(
                target_kp_ids=["KP_FJ_001"],
                mistakes=[{"kp_ids": ["KP_FJ_001"], "error_type": "证型-方剂匹配错误"}],
                question_bank=[
                    {"question_id": "Q1", "stem": "四君子汤主治？", "kp_ids": ["KP_FJ_001"], "difficulty": 2, "quality_score": 0.9},
                    {"question_id": "Q2", "stem": "理中丸主治？", "kp_ids": ["KP_FJ_018"], "difficulty": 3, "quality_score": 0.8},
                ],
                limit=1,
            )

        self.assertEqual(result["review_decision"]["decision"], "pass")
        patched.assert_called_once()

    def test_visual_parsing_surface_calls_cross_validation_service(self):
        service = importlib.import_module("APP.backend.vision_parse_service")

        def fake_post_json(url, payload, headers, timeout):
            return {
                "id": "chatcmpl-test",
                "choices": [
                    {
                        "message": {
                            "content": '{"image_type":"question_photo","question":"四君子汤主治哪类证候？","student_answer":"脾胃气虚证","visual_observations":["图片中包含一道方剂学题目"],"uncertain_parts":[],"confidence":0.86}'
                        }
                    }
                ],
            }

        with patch("APP.backend.vision_parse_service.cross_validate_output", return_value=(self._review(), self._summary())) as patched:
            result = service.parse_visual_task(
                image_base64="ZmFrZS1pbWFnZQ==",
                task_hint="question_photo",
                mime_type="image/png",
                http_post=fake_post_json,
                api_base_url="https://vision.example.test/v1",
                api_key="test-key",
            )

        self.assertEqual(result.raw_model_metadata["review_decision"]["decision"], "pass")
        patched.assert_called_once()


if __name__ == "__main__":
    unittest.main()

import importlib
import json
import unittest
from pathlib import Path


class DeepTrainingServicePhase5Tests(unittest.TestCase):
    def _service(self):
        try:
            return importlib.import_module("APP.backend.deep_training_service")
        except ModuleNotFoundError as exc:
            self.fail(f"deep_training_service module is missing: {exc}")

    def test_aligns_question_to_kp_id_and_candidate_when_no_exact_match(self):
        service = self._service()

        result = service.align_knowledge_points(
            text="四君子汤主治脾胃气虚证，学生容易与理中丸混淆。",
            knowledge_points=[
                {"kp_id": "KP_FJ_001", "name": "四君子汤", "aliases": ["补气剂四君子汤"], "difficulty": 2},
                {"kp_id": "KP_ZD_021", "name": "脾胃气虚证", "aliases": ["脾气虚证"], "difficulty": 2},
            ],
        )

        self.assertEqual(result["resolved_kp_ids"], ["KP_FJ_001", "KP_ZD_021"])
        self.assertEqual(result["label_status"], "matched")
        self.assertEqual(result["candidate_kp_ids"], [])

        candidate = service.align_knowledge_points(
            text="舌淡胖嫩与脾阳虚关系不清。",
            knowledge_points=[{"kp_id": "KP_ZD_010", "name": "舌诊", "aliases": [], "difficulty": 2}],
        )
        self.assertEqual(candidate["label_status"], "pending_review")
        self.assertTrue(candidate["candidate_kp_ids"])

    def test_selects_questions_and_generates_mistake_variant(self):
        service = self._service()
        question_bank = [
            {"question_id": "Q1", "stem": "四君子汤主治？", "kp_ids": ["KP_FJ_001"], "difficulty": 2, "quality_score": 0.9},
            {"question_id": "Q2", "stem": "理中丸主治？", "kp_ids": ["KP_FJ_018"], "difficulty": 3, "quality_score": 0.85},
            {"question_id": "Q3", "stem": "阴阳含义？", "kp_ids": ["KP_JC_001"], "difficulty": 1, "quality_score": 0.7},
        ]
        mistakes = [{"kp_ids": ["KP_FJ_001"], "error_type": "证型-方剂匹配错误", "wrong_count": 2}]

        paper = service.select_practice_questions(
            target_kp_ids=["KP_FJ_001", "KP_FJ_018"],
            mistakes=mistakes,
            question_bank=question_bank,
            limit=2,
        )
        variant = service.generate_mistake_variant(mistakes[0], question_bank[0])

        self.assertEqual([item["question_id"] for item in paper["questions"]], ["Q1", "Q2"])
        self.assertGreater(paper["coverage_report"]["target_coverage"], 0.9)
        self.assertEqual(paper["review_decision"]["decision"], "pass")
        self.assertEqual(variant["source_question_id"], "Q1")
        self.assertIn("变式", variant["stem"])

    def test_dynamic_question_selection_revises_when_selected_questions_miss_target_kp(self):
        service = self._service()

        paper = service.select_practice_questions(
            target_kp_ids=["KP_FJ_001", "KP_FJ_018"],
            mistakes=[{"kp_ids": ["KP_FJ_001"], "error_type": "证型-方剂匹配错误", "wrong_count": 2}],
            question_bank=[
                {"question_id": "Q1", "stem": "四君子汤主治？", "kp_ids": ["KP_FJ_001"], "difficulty": 2, "quality_score": 0.95},
                {"question_id": "Q3", "stem": "阴阳含义？", "kp_ids": ["KP_JC_001"], "difficulty": 1, "quality_score": 0.5},
            ],
            limit=1,
        )

        self.assertEqual([item["question_id"] for item in paper["questions"]], ["Q1"])
        self.assertLess(paper["coverage_report"]["target_coverage"], 1.0)
        self.assertNotEqual(paper["review_decision"]["decision"], "pass")
        self.assertTrue(any("knowledge_gap" in item for item in paper["review_summary"]["conflicts"]))

    def test_diagnoses_t_stage_and_creates_intervention(self):
        service = self._service()

        diagnosis = service.diagnose_learning_state(
            l0_baseline={"daily_available_minutes": 45, "default_daily_tasks": 3, "preferred_time_slot": "20:00-21:00"},
            l3_behavior={"login_weekly_change": -0.5, "focus_time_change": -0.45, "task_completion_rate": 0.42, "retry_count": 1},
            mistakes=[],
        )
        intervention = service.create_intervention(diagnosis)

        self.assertEqual(diagnosis["t_stage"]["stage_id"], "T2")
        self.assertEqual(diagnosis["attribution"]["primary"], "节奏下降")
        self.assertEqual(intervention["action"], "reduce_daily_tasks_and_send_popup")
        self.assertIn("为什么", intervention["explainable_message"])

    def test_cross_validation_and_metrics_summarize_quality(self):
        service = self._service()
        generated = {
            "claims": ["四君子汤主治脾胃气虚证", "四君子汤由人参、白术、茯苓、甘草组成"],
            "kp_ids": ["KP_FJ_001", "KP_ZD_021"],
            "difficulty": 2,
            "safety_risk": "low",
        }
        evidence = {
            "source_ids": ["SRC_FJ_001"],
            "supported_claims": ["四君子汤主治脾胃气虚证", "四君子汤由人参、白术、茯苓、甘草组成"],
            "required_kp_ids": ["KP_FJ_001", "KP_ZD_021"],
            "expected_difficulty": 2,
        }

        review = service.cross_validate_output(generated=generated, evidence=evidence)
        metrics = service.compute_evaluation_metrics([review])

        self.assertEqual(review["decision"], "pass")
        self.assertGreaterEqual(review["fact_consistency"], 0.95)
        self.assertGreaterEqual(metrics["knowledge_coverage_rate"], 0.9)
        self.assertLess(metrics["hallucination_rate"], 0.05)

    def test_phase5_sample_data_drives_deep_training_outputs(self):
        sample_path = Path(__file__).resolve().parents[1] / "sample_data" / "phase5_deep_training_seed.json"
        seed = json.loads(sample_path.read_text(encoding="utf-8"))
        service = self._service()

        result = service.align_knowledge_points(
            text=seed["tasks"][0]["text"],
            knowledge_points=seed["knowledge_points"],
        )
        paper = service.select_practice_questions(
            target_kp_ids=result["resolved_kp_ids"],
            mistakes=seed["mistakes"],
            question_bank=seed["question_bank"],
            limit=2,
        )
        diagnosis = service.diagnose_learning_state(
            l0_baseline=seed["analytics"]["l0_baseline"],
            l3_behavior=seed["analytics"]["l3_behavior"],
            mistakes=seed["mistakes"],
        )

        self.assertTrue(result["resolved_kp_ids"])
        self.assertTrue(paper["questions"])
        self.assertIn(diagnosis["t_stage"]["stage_id"], ["T1", "T2", "T4", "T5"])


if __name__ == "__main__":
    unittest.main()

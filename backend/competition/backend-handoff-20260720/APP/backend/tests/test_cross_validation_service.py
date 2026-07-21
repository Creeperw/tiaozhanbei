import importlib
import json
import unittest
from unittest.mock import Mock

from APP.backend.agent_contracts import DiagnosisReport, EvidenceItem, EvidencePack, ExpertArtifact, LearnerContextBrief


class CrossValidationServiceTests(unittest.TestCase):
    def _service(self):
        try:
            return importlib.import_module("APP.backend.cross_validation_service")
        except ModuleNotFoundError as exc:
            self.fail(f"cross_validation_service module is missing: {exc}")

    def _learner_context(self):
        return LearnerContextBrief(
            learner_id="learner-cross-001",
            learner_group="方剂学强化班",
            goal="在短时复盘中稳定区分四君子汤与理中丸",
            source_scope="learner_profile",
            source_id="profile-cross-001",
            kp_ids=["KP_FJ_001", "KP_ZD_021"],
            confidence=0.95,
            learning_state={"target_difficulty": 2},
        )

    def _evidence_pack(self):
        return EvidencePack(
            source_scope="knowledge_base_agent",
            source_id="PACK_CROSS_001",
            kp_ids=["KP_FJ_001", "KP_ZD_021"],
            resolved_kp_ids=["KP_FJ_001", "KP_ZD_021"],
            confidence=0.97,
            items=[
                EvidenceItem(
                    source_scope="knowledge_base",
                    source_id="SRC_FJ_001",
                    summary="四君子汤主治脾胃气虚证。",
                    kp_ids=["KP_FJ_001", "KP_ZD_021"],
                    confidence=0.99,
                ),
                EvidenceItem(
                    source_scope="knowledge_base",
                    source_id="SRC_COMPARE_001",
                    summary="理中丸偏于中焦虚寒证。",
                    kp_ids=["KP_FJ_001", "KP_ZD_021"],
                    confidence=0.96,
                ),
            ],
        )

    def _diagnosis_report(self, *, risk_notes=None):
        return DiagnosisReport(
            diagnosis_id="diag-cross-001",
            stage_id="T5",
            stage_name="难度不适",
            summary="建议维持难度 2，并避免引入诊疗建议。",
            source_scope="diagnosis_agent",
            source_id="diag-source-cross-001",
            kp_ids=["KP_FJ_001", "KP_ZD_021"],
            interventions=["短练", "对比讲义"],
            risk_notes=list(risk_notes or []),
            confidence=0.9,
        )

    def _artifact(self, *, difficulty=2, claims=None, risk_notes=None, content_overrides=None):
        content = {
            "schema_version": "v1",
            "source_ids": ["SRC_FJ_001", "SRC_COMPARE_001"],
            "kp_ids": ["KP_FJ_001", "KP_ZD_021"],
            "difficulty": difficulty,
            "claims": claims
            or [
                {"text": "四君子汤主治脾胃气虚证", "evidence_ids": ["SRC_FJ_001"]},
                {"text": "理中丸偏于中焦虚寒证", "evidence_ids": ["SRC_COMPARE_001"]},
            ],
            "sections": [{"title": "讲解", "bullets": ["围绕证型与方剂匹配展开。"]}],
        }
        if content_overrides:
            content.update(content_overrides)
        return ExpertArtifact(
            artifact_type="handout",
            title="交叉校验讲义",
            content=content,
            source_scope="expert_handout",
            source_id="artifact-cross-001",
            kp_ids=["KP_FJ_001", "KP_ZD_021"],
            risk_notes=list(risk_notes or []),
            confidence=0.92,
        )

    def test_cross_validation_returns_review_decision_and_summary_for_pass_case(self):
        service = self._service()
        llm_judge = Mock(return_value={"confidence": 0.96, "reason": "No extra concerns"})

        review, summary = service.cross_validate_output(
            artifact=self._artifact(),
            evidence_pack=self._evidence_pack(),
            learner_context=self._learner_context(),
            diagnosis_report=self._diagnosis_report(),
            llm_judge=llm_judge,
        )

        self.assertEqual(review.decision, "pass")
        self.assertEqual(summary["decision"], "pass")
        self.assertGreaterEqual(summary["overall_score"], 0.95)
        self.assertEqual(summary["needs_human_review"], False)
        self.assertIn("knowledge", summary["lenses"])
        self.assertIn("audit", summary["lenses"])
        llm_judge.assert_called_once()

    def test_cross_validation_rejects_unsupported_claim(self):
        service = self._service()
        artifact = self._artifact(
            claims=[
                {"text": "四君子汤主治脾胃气虚证", "evidence_ids": ["SRC_FJ_001"]},
                {"text": "该内容可替代急诊判断", "evidence_ids": ["SRC_UNKNOWN"]},
            ]
        )

        review, summary = service.cross_validate_output(
            artifact=artifact,
            evidence_pack=self._evidence_pack(),
            learner_context=self._learner_context(),
            diagnosis_report=self._diagnosis_report(),
        )

        self.assertEqual(review.decision, "reject")
        self.assertLess(summary["overall_score"], 0.9)
        self.assertTrue(any("unsupported" in item for item in summary["conflicts"]))

    def test_cross_validation_rejects_claim_without_evidence_ids(self):
        service = self._service()
        artifact = self._artifact(
            claims=[
                {"text": "四君子汤主治脾胃气虚证", "evidence_ids": ["SRC_FJ_001"]},
                {"text": "理中丸偏于中焦虚寒证"},
            ]
        )

        review, summary = service.cross_validate_output(
            artifact=artifact,
            evidence_pack=self._evidence_pack(),
            learner_context=self._learner_context(),
            diagnosis_report=self._diagnosis_report(),
        )

        self.assertEqual(review.decision, "reject")
        self.assertLess(review.fact_consistency, 1.0)
        self.assertTrue(any("missing_evidence_ids" in item for item in summary["conflicts"]))

    def test_cross_validation_revises_partial_kp_coverage(self):
        service = self._service()
        artifact = self._artifact(content_overrides={"kp_ids": ["KP_FJ_001"]})

        review, summary = service.cross_validate_output(
            artifact=artifact,
            evidence_pack=self._evidence_pack(),
            learner_context=self._learner_context(),
            diagnosis_report=self._diagnosis_report(),
        )

        self.assertEqual(review.decision, "revise")
        self.assertLess(review.knowledge_coverage, 1.0)
        self.assertTrue(any("knowledge_gap" in item for item in summary["conflicts"]))

    def test_cross_validation_rejects_difficulty_mismatch(self):
        service = self._service()

        review, summary = service.cross_validate_output(
            artifact=self._artifact(difficulty=5),
            evidence_pack=self._evidence_pack(),
            learner_context=self._learner_context(),
            diagnosis_report=self._diagnosis_report(),
        )

        self.assertEqual(review.decision, "reject")
        self.assertLess(review.difficulty_match, 0.7)
        self.assertTrue(any("difficulty" in item for item in summary["conflicts"]))

    def test_cross_validation_marks_high_risk_medical_content_for_human_review(self):
        service = self._service()
        artifact = self._artifact(risk_notes=["medical_high_risk: 包含急性胸痛处置建议"])

        review, summary = service.cross_validate_output(
            artifact=artifact,
            evidence_pack=self._evidence_pack(),
            learner_context=self._learner_context(),
            diagnosis_report=self._diagnosis_report(risk_notes=["medical_high_risk: 需人工复核"]),
        )

        self.assertEqual(review.decision, "human_review")
        self.assertEqual(summary["needs_human_review"], True)
        self.assertEqual(summary["safety_risk"], "high")

    def test_cross_validation_preserves_pass_and_emits_copyright_warning(self):
        service = self._service()
        artifact = self._artifact(content_overrides={"copyright_flags": ["quoted_text_over_limit"]})

        review, summary = service.cross_validate_output(
            artifact=artifact,
            evidence_pack=self._evidence_pack(),
            learner_context=self._learner_context(),
            diagnosis_report=self._diagnosis_report(),
        )

        self.assertEqual(review.decision, "pass")
        self.assertTrue(any("copyright" in note for note in review.risk_notes))
        self.assertTrue(any("copyright" in item for item in summary["warnings"]))
    def test_cross_validation_persists_agent_event_when_db_context_provided(self):
        service = self._service()

        class DummyDb:
            def __init__(self):
                self.added = []
                self.committed = False

            def add(self, item):
                self.added.append(item)

            def commit(self):
                self.committed = True

        db = DummyDb()
        review, summary = service.cross_validate_output(
            artifact=self._artifact(),
            evidence_pack=self._evidence_pack(),
            learner_context=self._learner_context(),
            diagnosis_report=self._diagnosis_report(),
            db=db,
            user_id=7,
            session_id="session-007",
        )

        self.assertEqual(review.decision, "pass")
        self.assertEqual(summary["decision"], "pass")
        self.assertTrue(db.committed)
        self.assertEqual(len(db.added), 1)
        self.assertEqual(db.added[0].agent_name, "cross_validation_service")
        self.assertEqual(db.added[0].event_type, "cross_validation")
    def test_visual_validation_persists_final_conflicts_consistently(self):
        service = self._service()

        class DummyDb:
            def __init__(self):
                self.added = []
                self.committed = False

            def add(self, item):
                self.added.append(item)

            def commit(self):
                self.committed = True

        class DummyVisualResult:
            question = "请根据图片直接给出急诊诊断和处方"
            image_type = "question_photo"
            visual_observations = ["图片中似乎是一道题"]
            confidence = 0.9
            raw_model_metadata = {"id": "vis-1", "model": "qwen3-vl-flash", "evidence_spans": []}

        db = DummyDb()
        review, summary = service.validate_visual_parse_result(
            result=DummyVisualResult(),
            task_hint="question_photo",
            db=db,
            user_id=9,
            session_id="session-visual-009",
        )

        self.assertEqual(review.decision, "human_review")
        self.assertTrue(any("visual_unanchored" in item for item in review.conflicts))
        self.assertTrue(db.committed)
        self.assertEqual(len(db.added), 1)
        payload = json.loads(db.added[0].payload)
        self.assertEqual(payload["review"]["decision"], review.decision)
        self.assertEqual(payload["summary"]["decision"], summary["decision"])
        self.assertIn("visual_unanchored:no_independent_evidence_anchor", payload["review"]["conflicts"])
        self.assertIn("visual_unanchored:no_independent_evidence_anchor", payload["summary"]["conflicts"])


if __name__ == "__main__":
    unittest.main()

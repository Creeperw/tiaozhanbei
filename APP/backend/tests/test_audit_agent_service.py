import importlib
import unittest
from unittest.mock import Mock

from APP.backend.agent_contracts import DiagnosisReport, EvidenceItem, EvidencePack, ExpertArtifact, LearnerContextBrief


class AuditAgentServiceTests(unittest.TestCase):
    def _service(self):
        try:
            return importlib.import_module("APP.backend.audit_agent_service")
        except ModuleNotFoundError as exc:
            self.fail(f"audit_agent_service module is missing: {exc}")

    def _learner_context(self, *, target_difficulty: int = 2):
        return LearnerContextBrief(
            learner_id="learner-008",
            learner_group="方剂学补弱班",
            goal="掌握四君子汤与理中丸的辨证区别",
            source_scope="learner_profile",
            source_id="profile-008",
            kp_ids=["KP_FJ_001", "KP_ZD_021"],
            confidence=0.93,
            learning_state={"target_difficulty": target_difficulty},
        )

    def _evidence_pack(self):
        return EvidencePack(
            source_scope="knowledge_base_agent",
            source_id="PACK_AUDIT_001",
            kp_ids=["KP_FJ_001", "KP_ZD_021"],
            resolved_kp_ids=["KP_FJ_001", "KP_ZD_021"],
            confidence=0.96,
            items=[
                EvidenceItem(
                    source_scope="knowledge_base",
                    source_id="SRC_FJ_001",
                    summary="四君子汤主治脾胃气虚证。",
                    kp_ids=["KP_FJ_001", "KP_ZD_021"],
                    confidence=0.98,
                ),
                EvidenceItem(
                    source_scope="knowledge_base",
                    source_id="SRC_COMPARE_001",
                    summary="四君子汤补气健脾，理中丸温中祛寒。",
                    kp_ids=["KP_FJ_001", "KP_ZD_021"],
                    confidence=0.97,
                ),
            ],
        )

    def _diagnosis_report(self, *, risk_notes=None):
        return DiagnosisReport(
            diagnosis_id="diag-audit-001",
            stage_id="T5",
            stage_name="难度不适",
            summary="当前更适合难度 2 的短练与对比讲解。",
            source_scope="diagnosis_agent",
            source_id="diag-source-audit-001",
            kp_ids=["KP_FJ_001", "KP_ZD_021"],
            interventions=["降低难度", "先做对比卡"],
            risk_notes=list(risk_notes or []),
            confidence=0.87,
        )

    def _artifact(self, *, difficulty: int = 2, claims=None, risk_notes=None, content_overrides=None):
        content = {
            "schema_version": "v1",
            "source_ids": ["SRC_FJ_001", "SRC_COMPARE_001"],
            "kp_ids": ["KP_FJ_001", "KP_ZD_021"],
            "difficulty": difficulty,
            "claims": claims
            or [
                {"text": "四君子汤主治脾胃气虚证", "evidence_ids": ["SRC_FJ_001"]},
                {"text": "理中丸更偏中焦虚寒证", "evidence_ids": ["SRC_COMPARE_001"]},
            ],
            "sections": [{"title": "核心辨证", "bullets": ["先辨脾胃气虚，再区分中焦虚寒。"]}],
        }
        if content_overrides:
            content.update(content_overrides)
        return ExpertArtifact(
            artifact_type="handout",
            title="讲义：四君子汤与理中丸",
            content=content,
            source_scope="expert_handout",
            source_id="artifact-audit-001",
            kp_ids=["KP_FJ_001", "KP_ZD_021"],
            risk_notes=list(risk_notes or []),
            confidence=0.91,
        )

    def test_audit_passes_supported_artifact_and_calls_optional_llm_hook(self):
        service = self._service()
        llm_judge = Mock(return_value={"confidence": 0.94, "reason": "LLM hook agrees with rule-first audit"})

        review = service.audit_artifact(
            artifact=self._artifact(),
            evidence_pack=self._evidence_pack(),
            learner_context=self._learner_context(),
            diagnosis_report=self._diagnosis_report(),
            llm_judge=llm_judge,
        )

        self.assertEqual(review.decision, "pass")
        self.assertEqual(review.reviewer, "audit_agent")
        self.assertGreaterEqual(review.fact_consistency, 1.0)
        self.assertGreaterEqual(review.knowledge_coverage, 1.0)
        self.assertGreaterEqual(review.difficulty_match, 1.0)
        self.assertEqual(review.conflicts, [])
        llm_judge.assert_called_once()

    def test_audit_rejects_unsupported_claim(self):
        service = self._service()
        artifact = self._artifact(
            claims=[
                {"text": "四君子汤主治脾胃气虚证", "evidence_ids": ["SRC_FJ_001"]},
                {"text": "四君子汤可以直接替代急诊处理", "evidence_ids": ["SRC_UNSUPPORTED_001"]},
            ]
        )

        review = service.audit_artifact(
            artifact=artifact,
            evidence_pack=self._evidence_pack(),
            learner_context=self._learner_context(),
            diagnosis_report=self._diagnosis_report(),
        )

        self.assertEqual(review.decision, "reject")
        self.assertLess(review.fact_consistency, 1.0)
        self.assertTrue(any("unsupported" in item for item in review.conflicts))

    def test_audit_rejects_claim_without_evidence_ids(self):
        service = self._service()
        artifact = self._artifact(
            claims=[
                {"text": "四君子汤主治脾胃气虚证", "evidence_ids": ["SRC_FJ_001"]},
                {"text": "理中丸偏于中焦虚寒证"},
            ]
        )

        review = service.audit_artifact(
            artifact=artifact,
            evidence_pack=self._evidence_pack(),
            learner_context=self._learner_context(),
            diagnosis_report=self._diagnosis_report(),
        )

        self.assertEqual(review.decision, "reject")
        self.assertLess(review.fact_consistency, 1.0)
        self.assertTrue(any("missing_evidence_ids" in item for item in review.conflicts))

    def test_audit_revises_partial_kp_coverage(self):
        service = self._service()
        artifact = self._artifact(content_overrides={"kp_ids": ["KP_FJ_001"]})

        review = service.audit_artifact(
            artifact=artifact,
            evidence_pack=self._evidence_pack(),
            learner_context=self._learner_context(),
            diagnosis_report=self._diagnosis_report(),
        )

        self.assertEqual(review.decision, "revise")
        self.assertLess(review.knowledge_coverage, 1.0)
        self.assertTrue(any("knowledge_gap" in item for item in review.conflicts))

    def test_audit_rejects_difficulty_mismatch(self):
        service = self._service()

        review = service.audit_artifact(
            artifact=self._artifact(difficulty=5),
            evidence_pack=self._evidence_pack(),
            learner_context=self._learner_context(target_difficulty=2),
            diagnosis_report=self._diagnosis_report(),
        )

        self.assertEqual(review.decision, "reject")
        self.assertLess(review.difficulty_match, 0.7)
        self.assertTrue(any("difficulty" in item for item in review.conflicts))

    def test_audit_sends_high_risk_medical_content_to_human_review(self):
        service = self._service()
        artifact = self._artifact(risk_notes=["medical_high_risk: 涉及急性胸痛处置建议"])

        review = service.audit_artifact(
            artifact=artifact,
            evidence_pack=self._evidence_pack(),
            learner_context=self._learner_context(),
            diagnosis_report=self._diagnosis_report(risk_notes=["medical_high_risk: 需人工复核"]),
        )

        self.assertEqual(review.decision, "human_review")
        self.assertEqual(review.safety_risk, "high")
        self.assertTrue(any("medical" in item for item in review.conflicts))

    def test_audit_keeps_pass_with_copyright_warning(self):
        service = self._service()
        artifact = self._artifact(content_overrides={"copyright_flags": ["quoted_text_over_limit"]})

        review = service.audit_artifact(
            artifact=artifact,
            evidence_pack=self._evidence_pack(),
            learner_context=self._learner_context(),
            diagnosis_report=self._diagnosis_report(),
        )

        self.assertEqual(review.decision, "pass")
        self.assertTrue(any("copyright" in note for note in review.risk_notes))
        self.assertEqual(review.safety_risk, "low")


if __name__ == "__main__":
    unittest.main()

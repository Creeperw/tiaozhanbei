import json
import unittest

from APP.backend.case_patient_orchestration import (
    MEDICAL_TRAINING_DISCLAIMER,
    CasePatientOrchestrationRequest,
    orchestrate_case_patient_reply,
)


class CasePatientOrchestrationTests(unittest.TestCase):
    def make_request(self, learner_message="最近总觉得没力气，胃口也不好。"):
        return CasePatientOrchestrationRequest(
            session_id="CS_001",
            learner_message=learner_message,
            conversation=(
                {"role": "learner", "content": "哪里不舒服？"},
                {"role": "patient", "content": "最近容易疲倦。"},
            ),
            patient_context={
                "reported_symptoms": ["乏力", "食欲不振"],
                "syndrome": "HIDDEN_SYNDROME_SENTINEL",
                "prescription": "HIDDEN_PRESCRIPTION_SENTINEL",
                "gold_answer": "HIDDEN_GOLD_ANSWER_SENTINEL",
            },
        )

    def test_pass_uses_structured_hidden_context_and_returns_persistable_reply(self):
        runner_calls = []
        auditor_calls = []

        def runner(**kwargs):
            runner_calls.append(kwargs)
            return {"reply": "这阵子常觉得乏力，吃东西也没什么胃口。"}

        def auditor(**kwargs):
            auditor_calls.append(kwargs)
            return {"decision": "pass"}

        result = orchestrate_case_patient_reply(
            self.make_request(),
            patient_runner=runner,
            auditor=auditor,
        )

        self.assertEqual(result.status, "completed")
        self.assertTrue(result.persistable)
        self.assertEqual(result.reply, "这阵子常觉得乏力，吃东西也没什么胃口。")
        self.assertEqual(result.disclaimer, MEDICAL_TRAINING_DISCLAIMER)
        self.assertEqual(len(runner_calls), 1)
        self.assertEqual(runner_calls[0]["patient_context"]["syndrome"], "HIDDEN_SYNDROME_SENTINEL")
        self.assertEqual(runner_calls[0]["learner_message"], "最近总觉得没力气，胃口也不好。")
        self.assertNotIn("query", runner_calls[0])
        self.assertEqual(auditor_calls[0]["patient_context"]["prescription"], "HIDDEN_PRESCRIPTION_SENTINEL")

    def test_revise_regenerates_only_up_to_the_configured_limit(self):
        runner_calls = []
        decisions = iter(({"decision": "revise", "reason": "回答过于直接"}, {"decision": "pass"}))

        def runner(**kwargs):
            runner_calls.append(kwargs)
            return {"reply": f"患者回答 {len(runner_calls)}"}

        result = orchestrate_case_patient_reply(
            self.make_request(),
            patient_runner=runner,
            auditor=lambda **kwargs: next(decisions),
            max_revisions=1,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.reply, "患者回答 2")
        self.assertEqual(len(runner_calls), 2)
        self.assertEqual(runner_calls[1]["revision_instruction"], "回答过于直接")

    def test_revise_limit_exhaustion_discards_the_draft(self):
        calls = []

        def runner(**kwargs):
            calls.append(kwargs)
            return {"reply": "UNREVIEWED_DRAFT_SENTINEL"}

        result = orchestrate_case_patient_reply(
            self.make_request(),
            patient_runner=runner,
            auditor=lambda **kwargs: {"decision": "revise", "reason": "仍需修改"},
            max_revisions=1,
        )

        self.assertEqual(result.status, "failed")
        self.assertFalse(result.persistable)
        self.assertEqual(result.reply, "暂时无法生成安全的模拟患者回答，请稍后重试。")
        self.assertEqual(result.error_code, "patient_reply_not_publishable")
        self.assertEqual(len(calls), 2)
        self.assertNotIn("UNREVIEWED_DRAFT_SENTINEL", json.dumps(result.to_public_dict(), ensure_ascii=False))

    def test_reject_and_human_review_never_return_unreviewed_draft(self):
        for decision in ("reject", "needs_human_review", "human_review"):
            with self.subTest(decision=decision):
                result = orchestrate_case_patient_reply(
                    self.make_request(),
                    patient_runner=lambda **kwargs: {"reply": "UNREVIEWED_DRAFT_SENTINEL"},
                    auditor=lambda **kwargs: {"decision": decision, "reason": "HIDDEN_TOOL_PARAMS"},
                )

                public = json.dumps(result.to_public_dict(), ensure_ascii=False)
                self.assertFalse(result.persistable)
                self.assertNotIn("UNREVIEWED_DRAFT_SENTINEL", public)
                self.assertNotIn("HIDDEN_TOOL_PARAMS", public)
                self.assertIn(result.status, {"failed", "needs_human_review"})

    def test_hidden_case_values_are_fail_closed_even_when_auditor_passes(self):
        for secret in (
            "HIDDEN_SYNDROME_SENTINEL",
            "HIDDEN_PRESCRIPTION_SENTINEL",
            "HIDDEN_GOLD_ANSWER_SENTINEL",
        ):
            with self.subTest(secret=secret):
                result = orchestrate_case_patient_reply(
                    self.make_request(),
                    patient_runner=lambda **kwargs: {"reply": f"患者透露：{secret}"},
                    auditor=lambda **kwargs: {"decision": "pass"},
                )

                serialized = json.dumps(result.to_public_dict(), ensure_ascii=False)
                self.assertFalse(result.persistable)
                self.assertEqual(result.error_code, "patient_reply_not_publishable")
                self.assertNotIn(secret, serialized)

    def test_runner_cannot_mutate_hidden_context_to_bypass_leak_gate(self):
        request = self.make_request()

        def mutating_runner(**kwargs):
            secret = kwargs["patient_context"].pop("syndrome")
            return {"reply": f"辨证答案是{secret}"}

        result = orchestrate_case_patient_reply(
            request,
            patient_runner=mutating_runner,
            auditor=lambda **kwargs: {"decision": "pass"},
        )

        public = json.dumps(result.to_public_dict(), ensure_ascii=False)
        self.assertFalse(result.persistable)
        self.assertNotIn("HIDDEN_SYNDROME_SENTINEL", public)
        self.assertEqual(request.patient_context["syndrome"], "HIDDEN_SYNDROME_SENTINEL")

    def test_nested_and_short_hidden_answers_are_fail_closed(self):
        request = CasePatientOrchestrationRequest(
            session_id="CS_002",
            learner_message="您觉得哪里不舒服？",
            conversation=(),
            patient_context={
                "reported_symptoms": ["乏力"],
                "hidden_case": {
                    "syndrome": "脾虚",
                    "golden_standard": {"prescription": "参汤"},
                },
            },
        )

        for secret in ("脾虚", "参汤"):
            with self.subTest(secret=secret):
                result = orchestrate_case_patient_reply(
                    request,
                    patient_runner=lambda **kwargs: {"reply": f"答案是{secret}"},
                    auditor=lambda **kwargs: {"decision": "pass"},
                )

                self.assertFalse(result.persistable)
                self.assertNotIn(secret, json.dumps(result.to_public_dict(), ensure_ascii=False))

    def test_patient_can_repeat_reported_symptoms_without_exposing_hidden_answers(self):
        result = orchestrate_case_patient_reply(
            self.make_request(),
            patient_runner=lambda **kwargs: {"reply": "我主要感觉乏力、食欲不振。"},
            auditor=lambda **kwargs: {"decision": "pass"},
        )

        self.assertEqual(result.status, "completed")
        self.assertTrue(result.persistable)

    def test_unknown_audit_decision_is_normalized_before_public_trace(self):
        result = orchestrate_case_patient_reply(
            self.make_request(),
            patient_runner=lambda **kwargs: {"reply": "最近总觉得没力气。"},
            auditor=lambda **kwargs: {"decision": "HIDDEN_TOOL_PARAMS"},
        )

        serialized = json.dumps(result.to_public_dict(), ensure_ascii=False)
        self.assertEqual(result.status, "failed")
        self.assertNotIn("HIDDEN_TOOL_PARAMS", serialized)
        self.assertEqual(result.trace[-1]["status"], "invalid")

    def test_trace_and_runner_failure_use_only_safe_codes(self):
        def runner(**kwargs):
            raise RuntimeError("HIDDEN_GOLD_ANSWER_SENTINEL HIDDEN_TOOL_PARAMS")

        result = orchestrate_case_patient_reply(
            self.make_request(),
            patient_runner=runner,
            auditor=lambda **kwargs: {"decision": "pass"},
        )

        serialized = json.dumps(result.to_public_dict(), ensure_ascii=False)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "patient_runner_failed")
        self.assertNotIn("HIDDEN_GOLD_ANSWER_SENTINEL", serialized)
        self.assertNotIn("HIDDEN_TOOL_PARAMS", serialized)
        self.assertEqual(set(result.trace[0]), {"stage", "status", "attempt"})

    def test_real_emergency_symptoms_stop_role_play_without_calling_runner(self):
        runner_calls = []
        result = orchestrate_case_patient_reply(
            self.make_request("这不是演练，我现在胸痛、呼吸困难，快要晕倒了。"),
            patient_runner=lambda **kwargs: runner_calls.append(kwargs),
            auditor=lambda **kwargs: {"decision": "pass"},
        )

        self.assertEqual(result.status, "safety_stopped")
        self.assertFalse(result.persistable)
        self.assertEqual(result.error_code, "real_world_emergency")
        self.assertIn("立即联系急救", result.reply)
        self.assertEqual(result.disclaimer, MEDICAL_TRAINING_DISCLAIMER)
        self.assertEqual(runner_calls, [])


if __name__ == "__main__":
    unittest.main()

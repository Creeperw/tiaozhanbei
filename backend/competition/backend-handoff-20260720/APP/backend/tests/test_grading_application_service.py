import json
import unittest
from dataclasses import FrozenInstanceError

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend.database import (
    AuditResultRecord,
    Base,
    EvidencePackRecord,
    GradingResultRecord,
    LearningAttemptItemRecord,
    LearningAttemptRecord,
    LearningWritebackReceipt,
    UserModel,
    QuestionBankItem,
    QuestionVersionRecord,
)
from APP.backend import grading_application_service as service


class GradingApplicationServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        event.listen(self.engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add(UserModel(id=1, username="learner", hashed_password="x"))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def command(self):
        return service.GradePracticeCommand(
            learner_id=1,
            source_channel="legacy_route",
            source_task_id=None,
            request_id="request-1",
            question_version_id="qv-1",
            question_type="short_answer",
            stem="Explain the principle.",
            submitted_answer="answer",
            standard_answer="standard",
            rubric="rubric",
            kp_ids=("kp-1",),
            difficulty=2,
            duration_sec=30,
            hint_used=False,
            profile={"constitution": "learner"},
            memories=({"category": "mistake"},),
        )

    @staticmethod
    def pass_runner(*, profile, memories, submission):
        return {
            "score": 90,
            "max_score": 100,
            "is_correct": True,
            "error_types": [],
            "error_reason": "",
            "confidence": 0.9,
            "feedback": "good",
            "mistake_record": None,
        }

    @staticmethod
    def revise_runner(*, profile, memories, submission):
        payload = GradingApplicationServiceTests.pass_runner(
            profile=profile, memories=memories, submission=submission
        )
        return {**payload, "audit": {"decision": "revise", "reason": "review needed"}}

    @staticmethod
    def legacy_runner(*, profile, memories, submission):
        if submission.get("student_answer") != "answer":
            raise AssertionError("legacy runner did not receive student_answer")
        return {
            "grading": {
                "question_id": "runner-owned-id",
                "is_correct": True,
                "score": 90,
                "error_type": "已掌握",
                "analysis": "good",
                "standard_answer": "standard",
            },
            "mistake_record": None,
            "remediation": {"review_card": {}},
            "agent_trace": [],
        }

    def artifact_counts(self):
        return tuple(
            self.db.query(model).count()
            for model in (EvidencePackRecord, GradingResultRecord, AuditResultRecord, LearningWritebackReceipt)
        )

    def test_command_and_result_are_immutable(self):
        command = self.command()
        with self.assertRaises(FrozenInstanceError):
            command.request_id = "other"

    def test_adapters_only_normalize_inputs(self):
        legacy = service.from_legacy_route_request(
            1,
            {"question_id": "qv-1", "type": "short_answer", "stem": "s", "answer": "a", "standard_answer": "b", "rubric": "r", "knowledge_points": ["kp-1"], "difficulty": 2},
            profile={}, memories=[], request_id="req-1",
        )
        workspace = service.from_workspace_request(
            1,
            {"task_id": "task-1", "inputs": {"question_id": "qv-2", "question_type": "case", "stem": "s", "submitted_answer": "a", "standard_answer": "b", "rubric": "r", "kp_ids": ["kp-2"], "difficulty": 3}},
            profile={}, memories=[], request_id="req-2",
        )
        self.assertEqual((legacy.source_channel, legacy.question_version_id, legacy.kp_ids), ("legacy_route", "qv-1", ("kp-1",)))
        self.assertEqual((workspace.source_channel, workspace.source_task_id, workspace.question_version_id), ("workspace", "task-1", "qv-2"))

    def test_runner_failure_preserves_committed_attempt_facts(self):
        def failing_runner(**_):
            raise RuntimeError("runner failed")

        with self.assertRaisesRegex(RuntimeError, "runner failed"):
            service.apply_practice_grading(self.db, self.command(), runner=failing_runner)
        self.assertEqual(self.db.query(LearningAttemptRecord).count(), 1)
        item = self.db.query(LearningAttemptItemRecord).one()
        self.assertEqual(json.loads(item.kp_snapshot_json), [{"kp_id": "kp-1", "relation_type": "primary", "confidence": 1.0}])
        self.assertEqual(self.artifact_counts(), (0, 0, 0, 0))

    def test_atomic_runner_failure_rolls_back_attempt_and_all_authoritative_rows(self):
        def failing_runner(**_):
            raise RuntimeError("runner failed late")

        with self.assertRaisesRegex(RuntimeError, "runner failed late"):
            service.apply_practice_grading(
                self.db, self.command(), runner=failing_runner, atomic=True
            )

        self.assertEqual(self.db.query(LearningAttemptRecord).count(), 0)
        self.assertEqual(self.db.query(LearningAttemptItemRecord).count(), 0)
        self.assertEqual(self.artifact_counts(), (0, 0, 0, 0))

    def test_atomic_late_projection_failure_rolls_back_every_authoritative_row(self):
        def fail_projection(_):
            raise RuntimeError("projection failed late")

        with self.assertRaisesRegex(RuntimeError, "projection failed late"):
            service.apply_practice_grading(
                self.db,
                self.command(),
                runner=self.pass_runner,
                before_commit=fail_projection,
                atomic=True,
            )

        self.assertEqual(self.db.query(LearningAttemptRecord).count(), 0)
        self.assertEqual(self.db.query(LearningAttemptItemRecord).count(), 0)
        self.assertEqual(self.artifact_counts(), (0, 0, 0, 0))

    def test_pass_persists_artifacts_and_writeback_in_transaction_b(self):
        result = service.apply_practice_grading(self.db, self.command(), runner=self.pass_runner)
        self.assertIsNotNone(result.grading_artifact_id)
        self.assertIsNotNone(result.audit_id)
        self.assertEqual(result.writeback.status, "applied")
        self.assertEqual(self.artifact_counts(), (1, 1, 1, 1))

    def test_first_grading_explanation_is_cached_and_reused(self):
        self.db.add(QuestionVersionRecord(
            question_version_id="qv-1", question_id="q-1", version=1,
            stem="Explain the principle.", answer="standard", analysis="", status="active",
        ))
        self.db.commit()
        first = service.apply_practice_grading(self.db, self.command(), runner=self.pass_runner)
        self.assertEqual(first.grading_payload["question_explanation"], "good")
        self.assertEqual(first.grading_payload["explanation_source"], "generated_on_first_attempt")

        second_command = service.GradePracticeCommand(
            **{**self.command().__dict__, "request_id": "request-2"}
        )
        second = service.apply_practice_grading(
            self.db,
            second_command,
            runner=lambda **kwargs: {**self.pass_runner(**kwargs), "feedback": "different"},
        )
        self.assertEqual(second.grading_payload["question_explanation"], "good")
        self.assertEqual(second.grading_payload["explanation_source"], "question_version_cache")

    def test_first_explanation_is_generated_once_and_cached_without_question_version(self):
        self.db.add(QuestionBankItem(
            question_id="q-bank-only",
            stem="四君子汤的功效是什么？",
            answer="益气健脾",
            analysis="",
            kp_ids_json='["kp-1"]',
            question_type="single_choice",
            status="active",
        ))
        self.db.commit()
        command = service.GradePracticeCommand(
            **{
                **self.command().__dict__,
                "question_version_id": "q-bank-only",
                "question_type": "single_choice",
                "stem": "四君子汤的功效是什么？",
                "standard_answer": "益气健脾",
            }
        )
        calls = []

        def explain_once(*, submission):
            calls.append(submission)
            return "四君子汤以补益脾气为核心，因此其功效是益气健脾。"

        first = service.apply_practice_grading(
            self.db,
            command,
            runner=self.pass_runner,
            explanation_runner=explain_once,
        )
        second = service.apply_practice_grading(
            self.db,
            service.GradePracticeCommand(
                **{**command.__dict__, "request_id": "request-bank-2"}
            ),
            runner=self.pass_runner,
            explanation_runner=lambda **_: self.fail("cached explanation must be reused"),
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(first.grading_payload["explanation_source"], "generated_on_first_attempt")
        self.assertEqual(second.grading_payload["explanation_source"], "question_bank_cache")
        self.assertEqual(
            self.db.query(QuestionBankItem).filter_by(question_id="q-bank-only").one().analysis,
            first.grading_payload["question_explanation"],
        )

    def test_legacy_grading_feedback_cache_is_replaced_by_real_explanation(self):
        self.db.add(QuestionVersionRecord(
            question_version_id="qv-1",
            question_id="q-1",
            version=1,
            stem="Explain the principle.",
            answer="standard",
            analysis=(
                "本题考查基本概念。客观题由系统依据标准答案自动判分。"
                "错因暂不自动下结论，请到错题变式中补充判断过程。"
            ),
            status="active",
        ))
        self.db.commit()

        result = service.apply_practice_grading(
            self.db,
            self.command(),
            runner=self.pass_runner,
            explanation_runner=lambda **_: "标准答案成立，因为题干条件与核心概念能够逐项对应。",
        )

        self.assertEqual(
            result.grading_payload["question_explanation"],
            "标准答案成立，因为题干条件与核心概念能够逐项对应。",
        )
        self.assertEqual(
            result.grading_payload["explanation_source"],
            "generated_on_first_attempt",
        )
        self.assertEqual(
            self.db.query(QuestionVersionRecord).filter_by(
                question_version_id="qv-1"
            ).one().analysis,
            result.grading_payload["question_explanation"],
        )

    def test_current_legacy_runner_shape_is_normalized_without_relational_ids(self):
        result = service.apply_practice_grading(self.db, self.command(), runner=self.legacy_runner)
        grading = self.db.query(GradingResultRecord).one()
        self.assertEqual((grading.score, grading.max_score, grading.is_correct), (90, 100, True))
        self.assertEqual(json.loads(grading.payload_json)["question_version_id"], "qv-1")
        self.assertNotEqual(grading.artifact_id, "runner-owned-id")
        self.assertEqual(result.audit["decision"], "pass")

    def test_invalid_runner_numbers_fail_after_a_without_b_artifacts(self):
        invalid_fields = {
            "score": (float("nan"), float("inf"), "90", True, -1, 101),
            "max_score": (float("nan"), float("inf"), "100", True, 0, -1),
            "is_correct": ("true", 1, None),
            "confidence": (float("nan"), float("inf"), "0.9", True, -0.1, 1.1),
        }
        for field, values in invalid_fields.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    def invalid_runner(*, profile, memories, submission, field=field, value=value):
                        return {**self.pass_runner(profile=profile, memories=memories, submission=submission), field: value}

                    with self.assertRaises(ValueError):
                        service.apply_practice_grading(self.db, self.command(), runner=invalid_runner)
                    self.assertEqual(self.db.query(LearningAttemptRecord).count(), 1)
                    self.assertEqual(self.db.query(LearningAttemptItemRecord).count(), 1)
                    self.assertEqual(self.artifact_counts(), (0, 0, 0, 0))
                    self.db.rollback()
                    Base.metadata.drop_all(self.engine)
                    Base.metadata.create_all(self.engine)
                    self.db.add(UserModel(id=1, username="learner", hashed_password="x"))
                    self.db.commit()

    def test_runner_relational_fields_are_not_persisted_as_payload(self):
        spoofed = {
            "artifact_id": "spoofed-artifact",
            "attempt_item_id": "spoofed-item",
            "source_artifact_id": "spoofed-source-artifact",
            "source_artifact_version": 999,
            "audit_id": "spoofed-audit",
        }

        def spoofing_runner(*, profile, memories, submission):
            return {
                **self.pass_runner(profile=profile, memories=memories, submission=submission),
                **spoofed,
                "audit": {"decision": "pass", **spoofed},
            }

        service.apply_practice_grading(self.db, self.command(), runner=spoofing_runner)
        grading_payload = json.loads(self.db.query(GradingResultRecord).one().payload_json)
        audit_payload = json.loads(self.db.query(AuditResultRecord).one().payload_json)
        for value in spoofed.values():
            self.assertNotIn(value, grading_payload.values())
            self.assertNotIn(value, audit_payload.values())
        self.assertEqual(grading_payload["question_version_id"], "qv-1")

    def test_nonpass_persists_artifacts_without_formal_effects(self):
        result = service.apply_practice_grading(self.db, self.command(), runner=self.revise_runner)
        self.assertEqual(result.audit["decision"], "revise")
        self.assertIsNone(result.writeback)
        self.assertEqual(self.artifact_counts(), (1, 1, 1, 0))

    def test_writeback_failure_rolls_back_b_but_preserves_a(self):
        def failing_writeback(*_):
            raise RuntimeError("writeback failed")

        with self.assertRaisesRegex(RuntimeError, "writeback failed"):
            service.apply_practice_grading(
                self.db, self.command(), runner=self.pass_runner, writeback=failing_writeback
            )
        self.assertEqual(self.db.query(LearningAttemptRecord).count(), 1)
        self.assertEqual(self.db.query(LearningAttemptItemRecord).count(), 1)
        self.assertEqual(self.artifact_counts(), (0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()

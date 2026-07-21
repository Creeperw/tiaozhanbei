import json
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend.database import (
    AuditResultRecord, Base, EvidencePackRecord, GradingResultRecord,
    KnowledgeMasteryState, LearnerKPReviewState, LearnerKnowledgeMastery,
    LearningAttemptItemRecord, LearningAttemptRecord, LearningWritebackReceipt,
    MasteryHistoryRecord, MistakeRecord, ReviewTaskRecord, UserModel,
)
from APP.backend.learning_writeback_service import (
    GradingWritebackCommand, LearningWritebackResult, apply_grading_writeback,
)
from APP.backend.review_formula import lambda_per_day, mastery_after_attempt


class LearningWritebackServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        event.listen(self.engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add(UserModel(id=1, username="learner", hashed_password="x"))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def seed(self, *, decision="pass", correct=False, learner_id=1, grading_kps=("kp-1",), snapshot=({"kp_id": "kp-1", "relation_type": "primary", "confidence": 1.0},), evidence=True, artifact="grade-1", version=1, audit_artifact=None, audit_id="audit-1", question_version="qv-1", reuse_attempt_item=False):
        if not reuse_attempt_item:
            self.db.add(LearningAttemptRecord(attempt_id="attempt-1", learner_id=learner_id, status="submitted"))
            self.db.flush()
            self.db.add(LearningAttemptItemRecord(attempt_item_id="item-1", attempt_id="attempt-1", question_version_id=question_version, kp_snapshot_json=json.dumps(snapshot)))
            self.db.flush()
        if evidence:
            self.db.add(EvidencePackRecord(pack_id=f"pack-{artifact}-v{version}", user_id=learner_id, resolved_kp_ids_json=json.dumps(grading_kps), payload_json=json.dumps({"attempt_item_id": "item-1", "question_version_id": question_version})))
        self.db.add(GradingResultRecord(artifact_id=artifact, attempt_item_id="item-1", version=version, score=100 if correct else 0, max_score=100, is_correct=correct, kp_ids_json=json.dumps(grading_kps), evidence_pack_id=f"pack-{artifact}-v{version}", confidence=.9, status="reviewed", payload_json=json.dumps({"question_version_id": question_version})))
        self.db.flush()
        self.db.add(AuditResultRecord(audit_id=audit_id, source_artifact_id=audit_artifact or artifact, source_artifact_version=version, decision=decision, status="completed"))
        self.db.commit()
        return GradingWritebackCommand("item-1", artifact, version, audit_id)

    def official_counts(self):
        models = (MistakeRecord, KnowledgeMasteryState, MasteryHistoryRecord, LearnerKPReviewState, ReviewTaskRecord, LearningWritebackReceipt)
        return tuple(self.db.query(model).count() for model in models)

    def reset_schema(self):
        self.db.rollback()
        Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)
        self.db.add(UserModel(id=1, username="learner", hashed_password="x"))
        self.db.commit()

    def test_command_and_result_are_immutable(self):
        command = GradingWritebackCommand("i", "g", 1, "a")
        with self.assertRaises(FrozenInstanceError):
            command.audit_id = "other"
        result = LearningWritebackResult("skipped", None, (), (), (), "v")
        with self.assertRaises(FrozenInstanceError):
            result.status = "applied"

    def test_pass_error_applies_official_effects_and_recovery(self):
        before = datetime.utcnow()
        result = apply_grading_writeback(self.db, 1, self.seed())
        self.assertEqual((result.status, result.formula_version), ("applied", "ebbinghaus_classic_hybrid_v1_1"))
        self.assertEqual(self.official_counts(), (1, 1, 1, 1, 1, 1))
        mistake = self.db.query(MistakeRecord).one()
        task = self.db.query(ReviewTaskRecord).one()
        self.assertEqual((mistake.status, task.review_type), ("active", "recovery_retry"))
        self.assertGreaterEqual(task.scheduled_at, before + timedelta(seconds=300))

    def test_pass_correct_has_no_mistake_or_recovery(self):
        result = apply_grading_writeback(self.db, 1, self.seed(correct=True))
        self.assertEqual(result.status, "applied")
        self.assertEqual(self.official_counts(), (0, 1, 1, 1, 0, 1))

    def test_nonpass_decisions_skip_without_effects(self):
        for decision in ("reject", "revise", "needs_human_review", "human_review"):
            with self.subTest(decision=decision):
                result = apply_grading_writeback(self.db, 1, self.seed(decision=decision))
                self.assertEqual(result.status, "skipped")
                self.assertEqual(self.official_counts(), (0, 0, 0, 0, 0, 0))
                self.reset_schema()

    def test_empty_grading_kps_returns_degraded_without_official_effects(self):
        result = apply_grading_writeback(self.db, 1, self.seed(grading_kps=()))
        self.assertEqual(result.status, "degraded")
        self.assertEqual(self.official_counts(), (0, 0, 0, 0, 0, 0))

    def test_rejects_malformed_authoritative_snapshot(self):
        for snapshot in (("kp-1",), ({},), ({"kp_id": " "},)):
            with self.subTest(snapshot=snapshot):
                command = self.seed(snapshot=snapshot)
                with self.assertRaises(ValueError):
                    apply_grading_writeback(self.db, 1, command)
                self.assertEqual(self.official_counts(), (0, 0, 0, 0, 0, 0))
                self.reset_schema()

    def test_rejects_mismatched_artifact_learner_kp_and_missing_evidence(self):
        valid = self.seed()
        mismatched = GradingWritebackCommand(valid.attempt_item_id, "other", valid.grading_artifact_version, valid.audit_id)
        with self.assertRaises(ValueError):
            apply_grading_writeback(self.db, 1, mismatched)
        self.assertEqual(self.official_counts(), (0, 0, 0, 0, 0, 0))
        self.reset_schema()

        for kwargs in ({"learner_id": 2}, {"grading_kps": ("kp-2",)}, {"evidence": False}):
            with self.subTest(kwargs=kwargs):
                if kwargs.get("learner_id") == 2:
                    self.db.add(UserModel(id=2, username="other", hashed_password="x"))
                    self.db.commit()
                command = self.seed(**kwargs)
                with self.assertRaises(ValueError):
                    apply_grading_writeback(self.db, 1, command)
                self.assertEqual(self.official_counts(), (0, 0, 0, 0, 0, 0))
                self.reset_schema()

    def test_idempotency_prevents_duplicate_effects(self):
        command = self.seed()
        first = apply_grading_writeback(self.db, 1, command)
        second = apply_grading_writeback(self.db, 1, command)
        self.assertEqual(first, second)
        self.assertEqual(self.official_counts(), (1, 1, 1, 1, 1, 1))

    def test_rederives_decay_rate_from_persisted_review_counters(self):
        assessed_at = datetime.utcnow() - timedelta(days=2)
        self.db.add(KnowledgeMasteryState(
            mastery_state_id="mastery-1",
            learner_id=1,
            kp_id="kp-1",
            mastery_score=80.0,
            last_assessed_at=assessed_at,
        ))
        self.db.add(LearnerKPReviewState(
            review_state_id="review-1",
            learner_id=1,
            kp_id="kp-1",
            lambda_per_day=0.08,
            recent_five_wrong_count=5,
            consecutive_independent_correct=0,
            review_stage="4",
        ))
        self.db.commit()

        apply_grading_writeback(self.db, 1, self.seed(correct=True))
        expected_rate = lambda_per_day(5, 0)
        history = self.db.query(MasteryHistoryRecord).one()
        formula_input = json.loads(history.formula_input_json)
        expected_score = mastery_after_attempt(
            previous_score=80.0,
            q_t=1.0,
            lambda_value=expected_rate,
            delta_days=formula_input["delta_days"],
        )
        review = self.db.query(LearnerKPReviewState).one()
        self.assertAlmostEqual(formula_input["lambda_per_day"], expected_rate)
        self.assertAlmostEqual(history.mastery_score, expected_score)
        self.assertAlmostEqual(review.lambda_per_day, expected_rate)
        self.assertEqual((review.recent_five_wrong_count, review.consecutive_independent_correct, review.review_stage), (5, 0, "4"))

    def test_artifact_versions_have_independent_idempotency_receipts(self):
        first_command = self.seed(artifact="grade-shared", version=1, audit_id="audit-v1")
        first = apply_grading_writeback(self.db, 1, first_command)
        self.db.commit()
        second_command = self.seed(
            artifact="grade-shared",
            version=2,
            audit_id="audit-v2",
            reuse_attempt_item=True,
        )
        second = apply_grading_writeback(self.db, 1, second_command)
        self.db.commit()

        self.assertNotEqual(first.receipt_id, second.receipt_id)
        self.assertEqual(self.db.query(LearningWritebackReceipt).count(), 2)
        self.assertEqual(self.db.query(MasteryHistoryRecord).count(), 2)
        self.assertEqual(self.db.query(ReviewTaskRecord).count(), 2)

        self.assertEqual(apply_grading_writeback(self.db, 1, first_command), first)
        self.assertEqual(apply_grading_writeback(self.db, 1, second_command), second)
        self.assertEqual(self.db.query(LearningWritebackReceipt).count(), 2)
        self.assertEqual(self.db.query(MasteryHistoryRecord).count(), 2)
        self.assertEqual(self.db.query(ReviewTaskRecord).count(), 2)

    def test_regrades_same_attempt_item_updates_one_active_mistake(self):
        first = apply_grading_writeback(self.db, 1, self.seed())
        self.db.commit()
        second_command = self.seed(artifact="grade-2", version=2, audit_id="audit-2", reuse_attempt_item=True)
        second = apply_grading_writeback(self.db, 1, second_command)
        self.assertNotEqual(first.receipt_id, second.receipt_id)
        self.assertEqual(self.db.query(MistakeRecord).filter_by(user_id=1, question_id="qv-1", status="active").count(), 1)

    def test_legacy_point_eight_seeds_eighty_and_projects_fraction(self):
        self.db.add(LearnerKnowledgeMastery(user_id=1, kp_id="kp-1", mastery=.8))
        self.db.commit()
        apply_grading_writeback(self.db, 1, self.seed(correct=True))
        state = self.db.query(KnowledgeMasteryState).one()
        legacy = self.db.query(LearnerKnowledgeMastery).one()
        self.assertAlmostEqual(state.mastery_score, 87.0)
        self.assertAlmostEqual(legacy.mastery, state.mastery_score / 100)

    def test_error_with_multiple_kps_creates_exactly_one_recovery(self):
        result = apply_grading_writeback(
            self.db, 1, self.seed(grading_kps=("kp-1", "kp-2"), snapshot=(
                {"kp_id": "kp-1", "relation_type": "primary", "confidence": 1.0},
                {"kp_id": "kp-2", "relation_type": "secondary", "confidence": 1.0},
            )),
        )
        self.assertEqual(len(result.mastery_updates), 2)
        self.assertEqual(self.db.query(ReviewTaskRecord).count(), 1)

    def test_caller_rollback_removes_all_pending_effects(self):
        apply_grading_writeback(self.db, 1, self.seed())
        self.db.rollback()
        self.assertEqual(self.official_counts(), (0, 0, 0, 0, 0, 0))

    def test_mid_write_flush_error_propagates_and_caller_rollback_is_atomic(self):
        command = self.seed()
        original_flush = self.db.flush
        calls = 0

        def fail_second_flush(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected writeback failure")
            return original_flush(*args, **kwargs)

        self.db.flush = fail_second_flush
        with self.assertRaisesRegex(RuntimeError, "injected writeback failure"):
            apply_grading_writeback(self.db, 1, command)
        self.db.rollback()
        self.assertEqual(self.official_counts(), (0, 0, 0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()

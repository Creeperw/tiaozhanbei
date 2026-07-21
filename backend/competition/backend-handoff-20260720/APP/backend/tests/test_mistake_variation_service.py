import json
import unittest
from unittest.mock import Mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.mistake_variation_service import (
    MistakeVariationNotFound,
    apply_mistake_variations,
    list_available_variation_sources,
)


class MistakeVariationServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def seed_source(self, db, *, user_id=1, mistake_id=91, audited=True):
        suffix = str(user_id)
        db.add(database.UserModel(id=user_id, username=f"owner-{suffix}", hashed_password="hash"))
        db.add(database.QuestionVersionRecord(
            question_version_id=f"QV_SOURCE_{suffix}",
            question_id=f"Q_SOURCE_{suffix}",
            version=1,
            stem="原题",
            answer="原题秘密答案",
            analysis="原题秘密解析",
        ))
        db.add(database.LearningAttemptRecord(
            attempt_id=f"ATT_SOURCE_{suffix}", learner_id=user_id, attempt_type="practice",
        ))
        db.add(database.LearningAttemptItemRecord(
            attempt_item_id=f"ITEM_SOURCE_{suffix}",
            attempt_id=f"ATT_SOURCE_{suffix}",
            question_version_id=f"QV_SOURCE_{suffix}",
        ))
        db.add(database.GradingResultRecord(
            artifact_id=f"GRADE_SOURCE_{suffix}",
            attempt_item_id=f"ITEM_SOURCE_{suffix}",
            version=1,
        ))
        if audited:
            db.add(database.AuditResultRecord(
                audit_id=f"AUD_SOURCE_{suffix}",
                source_artifact_id=f"GRADE_SOURCE_{suffix}",
                source_artifact_version=1,
                decision="pass",
                status="completed",
            ))
        db.add(database.MistakeRecord(
            id=mistake_id,
            user_id=user_id,
            question_id=f"Q_SOURCE_{suffix}",
            attempt_item_id=f"ITEM_SOURCE_{suffix}",
            question_version_id=f"QV_SOURCE_{suffix}",
            status="active",
        ))
        db.add(database.QuestionKPLinkRecord(
            question_version_id=f"QV_SOURCE_{suffix}", kp_id="KP_1",
        ))
        db.commit()

    @staticmethod
    def runner(**kwargs):
        request = kwargs["request"]
        context = request.task_context
        source_id = f"QV_VARIATION_{context.correlation_id}"
        return {
            "status": "success",
            "run_id": f"RUN_{context.correlation_id}",
            "steps": [],
            "final": {
                "artifact": {
                    "artifact_type": "question_variation",
                    "title": "变式",
                    "source_id": source_id,
                    "content": {
                        "stem": f"安全变式题干 {context.correlation_id}",
                        "question_type": "short_answer",
                        "difficulty": 2,
                        "kp_ids": ["KP_1"],
                        "source_mistake_id": context.mistake_id,
                        "source_question_version_id": context.source_question_version_id,
                        "source_question_id": context.source_question_id,
                        "answer": "SENTINEL_ANSWER",
                        "analysis": "SENTINEL_ANALYSIS",
                    },
                },
                "evidence_pack": {
                    "pack_id": f"EP_{context.correlation_id}",
                    "source_scope": "mistake_variation",
                    "source_id": context.source_question_version_id,
                    "resolved_kp_ids": ["KP_1"],
                    "items": [],
                },
                "audit": {
                    "decision": "pass",
                    "reason": "审核通过",
                    "source_scope": "audit_agent",
                    "source_id": source_id,
                },
            },
        }

    def test_lists_only_owned_active_sources_with_current_passed_audit(self):
        with self.Session() as db:
            self.seed_source(db, user_id=1, mistake_id=91)
            self.seed_source(db, user_id=2, mistake_id=92)
            self.seed_source(db, user_id=3, mistake_id=93, audited=False)

            sources = list_available_variation_sources(db, 1)

        self.assertEqual(sources, [{
            "mistake_id": 91,
            "question_version_id": "QV_SOURCE_1",
            "stem": "原题",
            "question_type": "single_choice",
            "difficulty": 2,
            "kp_ids": ["KP_1"],
        }])
        self.assertNotIn("原题秘密答案", json.dumps(sources, ensure_ascii=False))

    def test_accepts_variation_count_boundaries_and_rejects_outside_1_to_5(self):
        for count in (1, 5):
            with self.subTest(count=count), self.Session() as db:
                self.seed_source(db, user_id=count, mistake_id=90 + count)
                result = apply_mistake_variations(
                    db, count, 90 + count, count, runner=self.runner,
                )
                self.assertEqual(len(result["questions"]), count)

        for count in (0, 6, True):
            with self.subTest(count=count), self.Session() as db:
                runner = Mock()
                with self.assertRaises(ValueError):
                    apply_mistake_variations(db, 1, 91, count, runner=runner)
                runner.assert_not_called()

    def test_only_owner_with_passed_current_grading_audit_can_generate(self):
        with self.Session() as db:
            self.seed_source(db)
            runner = Mock()
            with self.assertRaises(MistakeVariationNotFound):
                apply_mistake_variations(db, 2, 91, 1, runner=runner)
            runner.assert_not_called()

    def test_later_rejected_grading_audit_invalidates_historical_pass(self):
        with self.Session() as db:
            self.seed_source(db)
            db.add(database.AuditResultRecord(
                audit_id="AUD_SOURCE_REJECTED",
                source_artifact_id="GRADE_SOURCE_1",
                source_artifact_version=1,
                decision="reject",
                status="completed",
            ))
            db.commit()
            runner = Mock()

            with self.assertRaises(MistakeVariationNotFound):
                apply_mistake_variations(db, 1, 91, 1, runner=runner)

            runner.assert_not_called()

    def test_generation_runner_starts_after_request_read_transaction_ends(self):
        with self.Session() as db:
            self.seed_source(db)

            def runner(**kwargs):
                self.assertFalse(db.in_transaction())
                return self.runner(**kwargs)

            result = apply_mistake_variations(db, 1, 91, 1, runner=runner)

        self.assertEqual(len(result["questions"]), 1)

    def test_rejects_publication_when_source_audit_is_revoked_after_compute(self):
        with self.Session() as db:
            self.seed_source(db)
            before = self._candidate_counts(db)

            def revoke_audit():
                db.add(database.AuditResultRecord(
                    audit_id="AUD_SOURCE_REVOKED",
                    source_artifact_id="GRADE_SOURCE_1",
                    source_artifact_version=1,
                    decision="reject",
                    status="completed",
                ))
                db.commit()

            with self.assertRaises(MistakeVariationNotFound):
                apply_mistake_variations(
                    db, 1, 91, 1, runner=self.runner, before_persist=revoke_audit,
                )

            self.assertEqual(self._candidate_counts(db), before)

    def test_rejects_publication_when_source_is_archived_after_compute(self):
        with self.Session() as db:
            self.seed_source(db)
            before = self._candidate_counts(db)

            def archive_source():
                db.query(database.MistakeRecord).filter_by(id=91).one().status = "resolved"
                db.commit()

            with self.assertRaises(MistakeVariationNotFound):
                apply_mistake_variations(
                    db, 1, 91, 1, runner=self.runner, before_persist=archive_source,
                )

            self.assertEqual(self._candidate_counts(db), before)

        with self.Session() as db:
            self.seed_source(db, user_id=3, mistake_id=93, audited=False)
            runner = Mock()
            with self.assertRaises(MistakeVariationNotFound):
                apply_mistake_variations(db, 3, 93, 1, runner=runner)
            runner.assert_not_called()

    def test_pass_publishes_private_answer_free_questions_with_exact_provenance(self):
        with self.Session() as db:
            self.seed_source(db)
            result = apply_mistake_variations(db, 1, 91, 2, runner=self.runner)
            variations = db.query(database.VariationSetRecord).order_by(
                database.VariationSetRecord.variation_set_id
            ).all()
            private_links = db.query(database.VariationQuestionVersionRecord).all()
            audits = {row.audit_id: row for row in db.query(database.AuditResultRecord).filter(
                database.AuditResultRecord.audit_id.in_([row.audit_id for row in variations])
            ).all()}

        self.assertEqual(len(variations), 2)
        self.assertTrue(all(row.owner_user_id == 1 for row in variations))
        self.assertTrue(all(row.source_mistake_id == 91 for row in variations))
        self.assertTrue(all(row.source_question_version_id == "QV_SOURCE_1" for row in variations))
        self.assertTrue(all(row.scope == "user" and row.owner_user_id == 1 for row in private_links))
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("SENTINEL_ANSWER", serialized)
        self.assertNotIn("SENTINEL_ANALYSIS", serialized)
        self.assertNotIn("原题秘密答案", serialized)
        self.assertEqual(
            set(result["questions"][0]),
            {"question_version_id", "question_id", "stem", "question_type", "difficulty", "kp_ids", "source_kind"},
        )
        self.assertTrue(all(
            audits[variation.audit_id].decision == "pass"
            and audits[variation.audit_id].status == "completed"
            for variation in variations
        ))

    @staticmethod
    def _candidate_counts(db):
        return (
            db.query(database.AuditResultRecord).filter(
                database.AuditResultRecord.source_artifact_id.like("QV_VARIATION_%")
            ).count(),
            *(db.query(model).count() for model in (
                database.VariationRubricRecord,
                database.VariationSetRecord,
                database.VariationQuestionVersionRecord,
            )),
            db.query(database.QuestionVersionRecord).filter(
                database.QuestionVersionRecord.source_kind == "variation"
            ).count(),
        )

    def test_correct_variation_answer_uses_unified_grading_without_closing_source_mistake(self):
        with self.Session() as db:
            self.seed_source(db)
            generated = apply_mistake_variations(db, 1, 91, 1, runner=self.runner)
            question = generated["questions"][0]
            grading = apply_mistake_variations(
                db,
                1,
                91,
                1,
                answer_request_id="answer-correct-1",
                answer={
                    "question_version_id": question["question_version_id"],
                    "student_answer": "脾胃气虚证",
                    "standard_answer": "脾胃气虚证",
                },
                grading_runner=lambda **_: {
                    "score": 100, "max_score": 100, "is_correct": True,
                    "error_types": [], "error_reason": "", "confidence": 0.9,
                    "feedback": "回答正确。",
                },
            )
            mistake = db.query(database.MistakeRecord).filter_by(id=91).one()

        self.assertEqual(grading["grading"]["writeback"]["status"], "applied")
        self.assertTrue(grading["grading"]["grading"]["is_correct"])
        self.assertEqual(mistake.status, "active")


    def test_forged_standard_answer_is_ignored_and_mismatched_mistake_rejects_before_runner(self):
        with self.Session() as db:
            self.seed_source(db)
            question = apply_mistake_variations(db, 1, 91, 1, runner=self.runner)["questions"][0]
            captured = {}

            def grading_runner(**kwargs):
                captured.update(kwargs["submission"])
                return {"score": 0, "max_score": 100, "is_correct": False,
                        "error_types": ["incorrect"], "error_reason": "wrong",
                        "confidence": 0.9, "feedback": "wrong"}

            apply_mistake_variations(
                db, 1, 91, 1,
                answer_request_id="answer-forged-1",
                answer={"question_version_id": question["question_version_id"],
                        "student_answer": "FORGED", "standard_answer": "FORGED"},
                grading_runner=grading_runner,
            )
            self.assertEqual(captured["standard_answer"], "SENTINEL_ANSWER")

            db.add(database.MistakeRecord(
                id=999, user_id=1, question_id="Q_SOURCE_1", attempt_item_id="ITEM_SOURCE_1",
                question_version_id="QV_SOURCE_1", status="active",
            ))
            db.commit()
            before = tuple(db.query(model).count() for model in (
                database.LearningAttemptRecord,
                database.GradingResultRecord,
                database.AuditResultRecord,
                database.MistakeRecord,
            ))
            rejected_runner = Mock()
            with self.assertRaises(MistakeVariationNotFound):
                apply_mistake_variations(
                    db, 1, 999, 1,
                    answer_request_id="answer-mismatch-1",
                    answer={"question_version_id": question["question_version_id"],
                            "student_answer": "x", "standard_answer": "x"},
                    grading_runner=rejected_runner,
                )
            rejected_runner.assert_not_called()
            self.assertEqual(tuple(db.query(model).count() for model in (
                database.LearningAttemptRecord,
                database.GradingResultRecord,
                database.AuditResultRecord,
                database.MistakeRecord,
            )), before)


if __name__ == "__main__":
    unittest.main()

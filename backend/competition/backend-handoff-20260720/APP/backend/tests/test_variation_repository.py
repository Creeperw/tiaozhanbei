import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from APP.backend.database import (
    AuditResultRecord,
    Base,
    GradingResultRecord,
    LearningAttemptItemRecord,
    LearningAttemptRecord,
    MistakeRecord,
    QuestionKPLinkRecord,
    QuestionVersionRecord,
    UserModel,
    VariationQuestionVersionRecord,
    VariationRubricRecord,
    VariationSetRecord,
    append_audit_result,
    ensure_runtime_schema_for,
)
from APP.backend.question_repository import QuestionRepository, QuestionSelectionCriteria
from APP.backend.variation_repository import VariationRepository


class VariationRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self._seed_records()
        self.repository = VariationRepository(self.Session)

    def tearDown(self):
        self.engine.dispose()

    def _seed_records(self):
        session = self.Session()
        try:
            session.add_all((
                UserModel(username="learner-one", hashed_password="hash"),
                UserModel(username="learner-two", hashed_password="hash"),
                MistakeRecord(user_id=1, question_id="source-active", status="active", attempt_item_id="item-1", question_version_id="source-qv-1"),
                MistakeRecord(user_id=1, question_id="source-archived", status="archived"),
                MistakeRecord(user_id=2, question_id="source-other", status="active"),
                QuestionVersionRecord(
                    question_version_id="source-qv-1",
                    question_id="source-active",
                    version=1,
                    stem="safe stem",
                    answer="secret answer",
                    analysis="secret analysis",
                ),
                LearningAttemptRecord(attempt_id="attempt-1", learner_id=1, attempt_type="practice"),
                LearningAttemptRecord(attempt_id="attempt-2", learner_id=2, attempt_type="practice"),
                LearningAttemptItemRecord(
                    attempt_item_id="item-1", attempt_id="attempt-1", question_version_id="source-qv-1",
                ),
                LearningAttemptItemRecord(
                    attempt_item_id="item-2", attempt_id="attempt-2", question_version_id="source-qv-1",
                ),
                GradingResultRecord(artifact_id="grading-1", attempt_item_id="item-1", version=1),
                GradingResultRecord(artifact_id="grading-other", attempt_item_id="item-2", version=1),
                GradingResultRecord(artifact_id="private-qv-1", attempt_item_id="item-1", version=1),
                AuditResultRecord(
                    audit_id="source-audit-1",
                    source_artifact_id="grading-1",
                    source_artifact_version=1,
                    decision="pass",
                    status="completed",
                ),
                AuditResultRecord(
                    audit_id="audit-1",
                    source_artifact_id="private-qv-1",
                    source_artifact_version=1,
                    decision="pass",
                    status="completed",
                ),
                AuditResultRecord(
                    audit_id="audit-other",
                    source_artifact_id="grading-other",
                    source_artifact_version=1,
                ),
            ))
            session.commit()
        finally:
            session.close()

    def test_get_owned_mistake_returns_current_users_active_or_archived_record(self):
        self.assertEqual(
            self.repository.get_owned_mistake(1, 1).question_id,
            "source-active",
        )
        self.assertEqual(
            self.repository.get_owned_mistake(1, 2).question_id,
            "source-archived",
        )

    def test_get_owned_mistake_returns_none_for_other_user_or_missing_record(self):
        self.assertIsNone(self.repository.get_owned_mistake(1, 3))
        self.assertIsNone(self.repository.get_owned_mistake(1, 999))

    def test_publish_variation_creates_user_scoped_question_version(self):
        published = self.repository.publish_variation(
            variation_set_id="variation-set-1",
            question_version_id="private-qv-1",
            question_id="private-question-1",
            owner_user_id=1,
            source_mistake_id=1,
            source_question_version_id="source-qv-1",
            audit_id="audit-1",
            source_artifact_id="grading-1",
            source_artifact_version=1,
            source_audit_id="source-audit-1",
            source_audit_generation=1,
            standard_answer="trusted answer",
            rubric={"required_points": ["trusted point"]},
        )
        session = self.Session()
        try:
            rubric = session.query(VariationRubricRecord).one()
            self.assertEqual(rubric.standard_answer, "trusted answer")
        finally:
            session.close()

        self.assertEqual(published.variation_set_id, "variation-set-1")
        self.assertEqual(published.owner_user_id, 1)
        self.assertEqual(published.question_version_id, "private-qv-1")
        self.assertEqual(published.scope, "user")
        self.assertEqual(published.status, "published")
        session = self.Session()
        try:
            self.assertEqual(session.query(GradingResultRecord.audit_generation).filter_by(
                artifact_id="grading-1", version=1,
            ).scalar(), 1)
        finally:
            session.close()

    def test_publish_rejects_stale_source_audit_snapshot_without_candidate_writes(self):
        with TemporaryDirectory() as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'race.db'}")
            Base.metadata.create_all(engine)
            sessions = sessionmaker(bind=engine)
            self._seed_source_audit_race(sessions)

            publisher = sessions()
            writer = sessions()
            try:
                source = publisher.query(GradingResultRecord).filter_by(
                    artifact_id="source-grade-race", version=1,
                ).one()
                source_generation = source.audit_generation

                writer.add(AuditResultRecord(
                    audit_id="source-audit-reject-race",
                    source_artifact_id="source-grade-race",
                    source_artifact_version=1,
                    decision="reject",
                    status="completed",
                ))
                writer.commit()

                repository = VariationRepository(session=publisher)
                with self.assertRaisesRegex(ValueError, "source audit"):
                    repository.publish_variation(**self._publish_kwargs(
                        source_artifact_id="source-grade-race",
                        source_artifact_version=1,
                        source_audit_id="source-audit-pass-race",
                        source_audit_generation=source_generation,
                    ))
                publisher.rollback()
            finally:
                publisher.close()
                writer.close()

            verification = sessions()
            try:
                self.assertEqual(verification.query(AuditResultRecord).filter(
                    AuditResultRecord.source_artifact_id == "private-qv-1"
                ).count(), 1)
                for model in (
                    VariationRubricRecord,
                    VariationSetRecord,
                    VariationQuestionVersionRecord,
                ):
                    self.assertEqual(verification.query(model).count(), 0)
                self.assertEqual(verification.query(QuestionVersionRecord).filter_by(
                    source_kind="variation"
                ).count(), 0)
            finally:
                verification.close()
                engine.dispose()

    def test_publish_rejects_legally_revoked_source_audit_without_candidate_writes(self):
        with TemporaryDirectory() as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'revocation-race.db'}")
            Base.metadata.create_all(engine)
            sessions = sessionmaker(bind=engine)
            self._seed_source_audit_race(sessions)

            publisher = sessions()
            writer = sessions()
            try:
                source = publisher.query(GradingResultRecord).filter_by(
                    artifact_id="source-grade-race", version=1,
                ).one()
                source_generation = source.audit_generation

                append_audit_result(
                    writer,
                    previous_audit_id="source-audit-pass-race",
                    audit_id="source-audit-revoked-race",
                    decision="reject",
                    status="completed",
                    reason="source evidence was revoked",
                )
                writer.commit()

                repository = VariationRepository(session=publisher)
                with self.assertRaisesRegex(ValueError, "source audit"):
                    repository.publish_variation(**self._publish_kwargs(
                        source_artifact_id="source-grade-race",
                        source_artifact_version=1,
                        source_audit_id="source-audit-pass-race",
                        source_audit_generation=source_generation,
                    ))
                publisher.rollback()
            finally:
                publisher.close()
                writer.close()

            verification = sessions()
            try:
                self.assertEqual(verification.query(AuditResultRecord).filter(
                    AuditResultRecord.source_artifact_id == "private-qv-1"
                ).count(), 1)
                for model in (
                    VariationRubricRecord,
                    VariationSetRecord,
                    VariationQuestionVersionRecord,
                ):
                    self.assertEqual(verification.query(model).count(), 0)
                self.assertEqual(verification.query(QuestionVersionRecord).filter_by(
                    source_kind="variation"
                ).count(), 0)
            finally:
                verification.close()
                engine.dispose()

    def test_source_audit_decision_and_status_cannot_be_bulk_updated(self):
        session = self.Session()
        try:
            with self.assertRaisesRegex(ValueError, "append"):
                session.query(AuditResultRecord).filter_by(audit_id="source-audit-1").update({
                    AuditResultRecord.decision: "reject",
                    AuditResultRecord.status: "revoked",
                })
            session.rollback()
        finally:
            session.close()

    def test_source_audit_decision_and_status_cannot_be_updated_in_place(self):
        session = self.Session()
        try:
            audit = session.query(AuditResultRecord).filter_by(audit_id="source-audit-1").one()
            audit.decision = "reject"
            audit.status = "revoked"
            with self.assertRaisesRegex(ValueError, "append"):
                session.commit()
            session.rollback()
        finally:
            session.close()

        verification = self.Session()
        try:
            audit = verification.query(AuditResultRecord).filter_by(audit_id="source-audit-1").one()
            self.assertEqual((audit.decision, audit.status), ("pass", "completed"))
            self.assertEqual(verification.query(GradingResultRecord.audit_generation).filter_by(
                artifact_id="grading-1", version=1,
            ).scalar(), 1)
        finally:
            verification.close()

    def test_persisted_audit_evidence_fields_are_immutable(self):
        replacements = {
            "audit_id": "changed-audit",
            "source_artifact_id": "changed-artifact",
            "source_artifact_version": 99,
            "decision": "reject",
            "reason": "changed reason",
            "confidence": 0.01,
            "status": "revoked",
            "schema_version": "v99",
            "payload_json": '{"secret":"must-not-leak"}',
        }
        for field, value in replacements.items():
            session = self.Session()
            try:
                audit = session.query(AuditResultRecord).filter_by(audit_id="source-audit-1").one()
                setattr(audit, field, value)
                with self.subTest(field=field):
                    with self.assertRaisesRegex(ValueError, "append"):
                        session.flush()
                session.rollback()
            finally:
                session.close()

    def test_bulk_update_of_audit_evidence_is_rejected_without_payload_leak(self):
        session = self.Session()
        try:
            with self.assertRaisesRegex(ValueError, "append") as raised:
                session.query(AuditResultRecord).filter_by(audit_id="source-audit-1").update({
                    AuditResultRecord.payload_json: '{"secret":"must-not-leak"}',
                })
            self.assertNotIn("must-not-leak", str(raised.exception))
            session.rollback()
        finally:
            session.close()

    def test_append_correction_preserves_source_and_advances_generation(self):
        session = self.Session()
        try:
            before = session.query(GradingResultRecord.audit_generation).filter_by(
                artifact_id="grading-1", version=1,
            ).scalar()
            correction = append_audit_result(
                session, previous_audit_id="source-audit-1", audit_id="source-audit-2",
                decision="reject", status="completed", reason="corrected evidence",
                confidence=0.9, payload_json='{"revision":2}',
            )
            session.commit()
            self.assertEqual(correction.source_artifact_id, "grading-1")
            self.assertEqual(correction.source_artifact_version, 1)
            self.assertEqual(session.query(GradingResultRecord.audit_generation).filter_by(
                artifact_id="grading-1", version=1,
            ).scalar(), before + 1)
        finally:
            session.close()

    def test_publish_snapshot_rejects_appended_audit_with_changed_evidence(self):
        publisher = self.Session()
        writer = self.Session()
        try:
            generation = publisher.query(GradingResultRecord.audit_generation).filter_by(
                artifact_id="grading-1", version=1,
            ).scalar()
            append_audit_result(
                writer, previous_audit_id="source-audit-1", audit_id="source-audit-evidence-2",
                decision="pass", status="completed", reason="new evidence source",
                payload_json='{"evidence_source":"replacement"}',
            )
            writer.commit()
            with self.assertRaisesRegex(ValueError, "source audit"):
                VariationRepository(session=publisher).publish_variation(**self._publish_kwargs(
                    source_audit_generation=generation,
                ))
            publisher.rollback()
        finally:
            publisher.close()
            writer.close()

    @staticmethod
    def _seed_source_audit_race(sessions):
        session = sessions()
        try:
            session.add_all((
                UserModel(id=1, username="race-owner", hashed_password="hash"),
                QuestionVersionRecord(
                    question_version_id="source-qv-1", question_id="source-active",
                    version=1, stem="source",
                ),
                LearningAttemptRecord(
                    attempt_id="attempt-1", learner_id=1, attempt_type="practice",
                ),
                LearningAttemptItemRecord(
                    attempt_item_id="item-1", attempt_id="attempt-1",
                    question_version_id="source-qv-1",
                ),
                MistakeRecord(
                    id=1, user_id=1, question_id="source-active", status="active",
                    attempt_item_id="item-1", question_version_id="source-qv-1",
                ),
            ))
            session.flush()
            session.add(GradingResultRecord(
                artifact_id="source-grade-race", attempt_item_id="item-1", version=1,
            ))
            session.flush()
            session.add(AuditResultRecord(
                audit_id="source-audit-pass-race",
                source_artifact_id="source-grade-race",
                source_artifact_version=1,
                decision="pass",
                status="completed",
            ))
            session.add(GradingResultRecord(
                artifact_id="private-qv-1", attempt_item_id="item-1", version=1,
            ))
            session.flush()
            session.add(AuditResultRecord(
                audit_id="audit-1", source_artifact_id="private-qv-1",
                source_artifact_version=1, decision="pass", status="completed",
            ))
            session.commit()
        finally:
            session.close()

    def test_publish_rejects_invalid_identifiers_before_writes(self):
        cases = (("owner_user_id", 0), ("source_mistake_id", -1)) + tuple(
            (field, "  ") for field in (
                "variation_set_id", "question_version_id", "question_id",
                "source_question_version_id", "audit_id",
            )
        )
        for field, value in cases:
            kwargs = self._publish_kwargs()
            kwargs[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.repository.publish_variation(**kwargs)
        self._assert_no_variations()

    def test_publish_rejects_cross_user_or_missing_source_mistakes(self):
        for mistake_id in (3, 999):
            kwargs = self._publish_kwargs(source_mistake_id=mistake_id)
            with self.subTest(mistake_id=mistake_id), self.assertRaises(ValueError):
                self.repository.publish_variation(**kwargs)
        self._assert_no_variations()

    def test_publish_rejects_archived_source_mistake(self):
        with self.assertRaises(ValueError):
            self.repository.publish_variation(**self._publish_kwargs(source_mistake_id=2))
        self._assert_no_variations()

    def test_publish_rejects_unrelated_source_question_and_cross_owner_audit(self):
        session = self.Session()
        try:
            session.add(QuestionVersionRecord(
                question_version_id="unrelated-qv", question_id="unrelated-question", version=1,
            ))
            session.commit()
        finally:
            session.close()
        for overrides in (
            {"source_question_version_id": "unrelated-qv"},
            {"audit_id": "audit-other"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.repository.publish_variation(**self._publish_kwargs(**overrides))
        self._assert_no_variations()

    def test_publish_rejects_same_question_but_different_mistake_version(self):
        session = self.Session()
        try:
            session.add(QuestionVersionRecord(
                question_version_id="source-qv-2", question_id="source-active", version=2,
            ))
            session.commit()
        finally:
            session.close()
        with self.assertRaisesRegex(ValueError, "mistake.*version"):
            self.repository.publish_variation(**self._publish_kwargs(
                source_question_version_id="source-qv-2",
            ))
        self._assert_no_variations()

    def test_publish_rejects_invalid_status_and_scope(self):
        for field, value in (("status", "draft"), ("scope", "public")):
            kwargs = self._publish_kwargs()
            kwargs[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.repository.publish_variation(**kwargs)
        self._assert_no_variations()

    def test_owned_selection_requires_published_set_and_returns_answer_free_frozen_dto(self):
        self.repository.publish_variation(**self._publish_kwargs())
        selected = self.repository.select_owned_question_versions(1)
        self.assertEqual(len(selected), 1)
        self.assertFalse(hasattr(selected[0], "answer"))
        self.assertFalse(hasattr(selected[0], "analysis"))
        with self.assertRaises(Exception):
            selected[0].stem = "changed"
        session = self.Session()
        try:
            session.query(VariationSetRecord).filter_by(variation_set_id="variation-set-1").update(
                {"status": "archived"}
            )
            session.commit()
        finally:
            session.close()
        self.assertEqual(self.repository.select_owned_question_versions(1), ())

    def test_duplicate_question_id_increments_version_deterministically(self):
        self.repository.publish_variation(**self._publish_kwargs())
        session = self.Session()
        try:
            session.add(GradingResultRecord(artifact_id="private-qv-2", attempt_item_id="item-1", version=1))
            session.add(AuditResultRecord(
                audit_id="audit-2", source_artifact_id="private-qv-2",
                source_artifact_version=1, decision="pass", status="completed",
            ))
            session.commit()
        finally:
            session.close()
        self.repository.publish_variation(**self._publish_kwargs(
            variation_set_id="variation-set-2", question_version_id="private-qv-2",
            audit_id="audit-2",
        ))
        session = self.Session()
        try:
            versions = session.query(QuestionVersionRecord.version).filter_by(
                question_id="private-question-1"
            ).order_by(QuestionVersionRecord.version).all()
        finally:
            session.close()
        self.assertEqual(versions, [(1,), (2,)])

    def test_publish_retries_version_conflict_and_uses_recomputed_version(self):
        session = self.Session()
        original_flush = session.flush
        flush_calls = 0

        def flush_with_concurrent_version_conflict(*args, **kwargs):
            nonlocal flush_calls
            has_target_version = any(
                isinstance(record, QuestionVersionRecord)
                and record.question_version_id == "private-qv-1"
                for record in session.new
            )
            if has_target_version:
                flush_calls += 1
            if flush_calls == 1 and has_target_version:
                raise IntegrityError(
                    "INSERT INTO question_version_records",
                    {},
                    Exception(
                        "UNIQUE constraint failed: "
                        "question_version_records.question_id, question_version_records.version"
                    ),
                )
            return original_flush(*args, **kwargs)

        session.flush = flush_with_concurrent_version_conflict
        repository = VariationRepository(lambda: session)

        published = repository.publish_variation(**self._publish_kwargs())

        self.assertEqual(published.question_version_id, "private-qv-1")
        self.assertEqual(flush_calls, 2)
        verification = self.Session()
        try:
            version = verification.query(QuestionVersionRecord.version).filter_by(
                question_version_id="private-qv-1"
            ).scalar()
        finally:
            verification.close()
        self.assertEqual(version, 1)

    def test_version_conflict_retry_preserves_uncommitted_current_audit(self):
        session = self.Session()
        session.add(AuditResultRecord(
            audit_id="audit-current", source_artifact_id="private-qv-current",
            source_artifact_version=1, decision="pass", status="completed",
        ))
        session.flush()
        original_flush = session.flush
        injected = False

        def flush_with_real_version_conflict(*args, **kwargs):
            nonlocal injected
            has_target = any(
                isinstance(record, QuestionVersionRecord)
                and record.question_version_id == "private-qv-current"
                for record in session.new
            )
            if has_target and not injected:
                injected = True
                session.add(QuestionVersionRecord(
                    question_version_id="conflicting-current-qv",
                    question_id="private-question-current", version=1,
                ))
            return original_flush(*args, **kwargs)

        session.flush = flush_with_real_version_conflict
        repository = VariationRepository(session=session)
        published = repository.publish_variation(**self._publish_kwargs(
            variation_set_id="variation-set-current",
            question_version_id="private-qv-current",
            question_id="private-question-current",
            audit_id="audit-current",
        ))
        session.commit()
        session.close()

        verification = self.Session()
        try:
            self.assertEqual(published.question_version_id, "private-qv-current")
            self.assertIsNotNone(verification.query(AuditResultRecord).filter_by(audit_id="audit-current").one_or_none())
            self.assertIsNotNone(verification.query(VariationSetRecord).filter_by(variation_set_id="variation-set-current").one_or_none())
        finally:
            verification.close()

    def test_batch_version_conflict_preserves_prior_published_variation(self):
        session = self.Session()
        repository = VariationRepository(session=session)
        first = repository.publish_variation(**self._publish_kwargs())
        session.add(AuditResultRecord(
            audit_id="audit-batch-2", source_artifact_id="private-qv-batch-2",
            source_artifact_version=1, decision="pass", status="completed",
        ))
        session.flush()
        original_flush = session.flush
        injected = False

        def flush_with_second_conflict(*args, **kwargs):
            nonlocal injected
            has_second = any(
                isinstance(record, QuestionVersionRecord)
                and record.question_version_id == "private-qv-batch-2"
                for record in session.new
            )
            if has_second and not injected:
                injected = True
                session.add(QuestionVersionRecord(
                    question_version_id="conflicting-batch-qv",
                    question_id="private-question-batch", version=1,
                ))
            return original_flush(*args, **kwargs)

        session.flush = flush_with_second_conflict
        second = repository.publish_variation(**self._publish_kwargs(
            variation_set_id="variation-set-batch-2",
            question_version_id="private-qv-batch-2",
            question_id="private-question-batch",
            audit_id="audit-batch-2",
        ))
        session.commit()
        session.close()

        verification = self.Session()
        try:
            persisted_ids = {
                row[0] for row in verification.query(VariationQuestionVersionRecord.question_version_id).all()
            }
            self.assertEqual({first.question_version_id, second.question_version_id}, persisted_ids)
        finally:
            verification.close()

    def test_publish_version_conflict_exhaustion_is_controlled_and_atomic(self):
        session = self.Session()
        original_flush = session.flush
        flush_calls = 0

        def always_conflict(*args, **kwargs):
            nonlocal flush_calls
            if any(isinstance(record, QuestionVersionRecord) for record in session.new):
                flush_calls += 1
                raise IntegrityError(
                    "INSERT INTO question_version_records",
                    {},
                    Exception(
                        "UNIQUE constraint failed: "
                        "question_version_records.question_id, question_version_records.version"
                    ),
                )
            return original_flush(*args, **kwargs)

        session.flush = always_conflict
        repository = VariationRepository(lambda: session)

        with self.assertRaisesRegex(RuntimeError, "version allocation retries exhausted"):
            repository.publish_variation(**self._publish_kwargs())

        self.assertEqual(flush_calls, repository.VERSION_ALLOCATION_ATTEMPTS)
        self._assert_no_variations()
        verification = self.Session()
        try:
            self.assertIsNone(verification.query(QuestionVersionRecord).filter_by(
                question_version_id="private-qv-1"
            ).one_or_none())
        finally:
            verification.close()

    def test_publish_does_not_retry_unrelated_integrity_error(self):
        session = self.Session()
        original_flush = session.flush
        flush_calls = 0

        def unrelated_conflict(*args, **kwargs):
            nonlocal flush_calls
            if any(isinstance(record, QuestionVersionRecord) for record in session.new):
                flush_calls += 1
                raise IntegrityError(
                    "INSERT INTO variation_sets",
                    {},
                    Exception("UNIQUE constraint failed: variation_sets.variation_set_id"),
                )
            return original_flush(*args, **kwargs)

        session.flush = unrelated_conflict
        repository = VariationRepository(lambda: session)

        with self.assertRaises(IntegrityError):
            repository.publish_variation(**self._publish_kwargs())

        self.assertEqual(flush_calls, 1)
        self._assert_no_variations()

    def test_public_selection_returns_only_active_nonvariation_versions_as_safe_dtos(self):
        self.repository.publish_variation(**self._publish_kwargs())
        selected = self.repository.select_public_question_versions()
        self.assertEqual([item.question_version_id for item in selected], ["source-qv-1"])
        self.assertFalse(hasattr(selected[0], "answer"))
        self.assertFalse(hasattr(selected[0], "analysis"))

    def test_runtime_schema_creation_is_repeatable_for_variation_tables(self):
        engine = create_engine("sqlite://")
        try:
            ensure_runtime_schema_for(engine)
            ensure_runtime_schema_for(engine)
            names = set(inspect(engine).get_table_names())
            self.assertTrue({"variation_sets", "variation_question_versions"}.issubset(names))
        finally:
            engine.dispose()

    def _publish_kwargs(self, **overrides):
        values = {
            "variation_set_id": "variation-set-1",
            "question_version_id": "private-qv-1",
            "question_id": "private-question-1",
            "owner_user_id": 1,
            "source_mistake_id": 1,
            "source_question_version_id": "source-qv-1",
            "audit_id": "audit-1",
            "source_artifact_id": "grading-1",
            "source_artifact_version": 1,
            "source_audit_id": "source-audit-1",
            "source_audit_generation": 1,
            "standard_answer": "trusted answer",
            "rubric": {"required_points": ["trusted point"]},
        }
        values.update(overrides)
        return values

    def _assert_no_variations(self):
        session = self.Session()
        try:
            self.assertEqual(session.query(VariationSetRecord).count(), 0)
            self.assertEqual(session.query(VariationQuestionVersionRecord).count(), 0)
        finally:
            session.close()

    def test_variation_provenance_refs_are_required(self):
        session = self.Session()
        try:
            from APP.backend.database import VariationSetRecord

            session.add(VariationSetRecord(
                variation_set_id="invalid-variation-set",
                owner_user_id=1,
                source_mistake_id=1,
                source_question_version_id="source-qv-1",
            ))
            with self.assertRaises(IntegrityError):
                session.commit()
        finally:
            session.rollback()
            session.close()

    def test_public_question_selection_does_not_return_another_users_private_question(self):
        self.repository.publish_variation(
            variation_set_id="variation-set-1",
            question_version_id="private-qv-1",
            question_id="private-question-1",
            owner_user_id=1,
            source_mistake_id=1,
            source_question_version_id="source-qv-1",
            audit_id="audit-1",
            source_artifact_id="grading-1",
            source_artifact_version=1,
            source_audit_id="source-audit-1",
            source_audit_generation=1,
            standard_answer="trusted answer",
            rubric={},
        )

        session = self.Session()
        try:
            session.add(QuestionKPLinkRecord(question_version_id="source-qv-1", kp_id="kp-1"))
            session.commit()
        finally:
            session.close()
        selected = QuestionRepository(self.Session).select(QuestionSelectionCriteria(
            kp_ids=("kp-1",),
            type_difficulty_counts=(("single_choice", 2, 1),),
        ))

        self.assertEqual(
            [item.question_version_id for item in self.repository.select_public_question_versions()],
            ["source-qv-1"],
        )
        self.assertEqual(
            [item.question_version_id for item in selected],
            ["source-qv-1"],
        )
        self.assertNotIn("private-qv-1", [item.question_version_id for item in selected])
        self.assertEqual(
            [item.question_version_id for item in self.repository.select_owned_question_versions(1)],
            ["private-qv-1"],
        )
        self.assertEqual(self.repository.select_owned_question_versions(2), ())


if __name__ == "__main__":
    unittest.main()

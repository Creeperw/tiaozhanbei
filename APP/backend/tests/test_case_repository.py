import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from APP.backend.database import Base, UserModel, ensure_runtime_schema_for
from APP.backend.case_repository import CaseRepository


class CaseRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self._seed_users()
        self.repository = CaseRepository(self.Session)

    def tearDown(self):
        self.engine.dispose()

    def _seed_users(self):
        session = self.Session()
        try:
            session.add_all((
                UserModel(id=1, username="learner-one", hashed_password="hash"),
                UserModel(id=2, username="learner-two", hashed_password="hash"),
            ))
            session.commit()
        finally:
            session.close()

    def test_public_session_omits_hidden_case_material_and_orders_messages(self):
        self.repository.create_case(
            case_definition_id="case-1",
            case_version_id="case-1-v1",
            title="咳嗽辨证训练",
            visible_context={"chief_complaint": "咳嗽三日"},
            patient_context={"response_style": "简短"},
            golden_standard={"diagnosis": "风热犯肺"},
            rubric={"required": ["问寒热"]},
        )
        self.repository.create_session("session-1", 1, "case-1-v1")
        self.repository.append_message(1, "session-1", "patient", "咳嗽", sequence=1)
        self.repository.append_message(1, "session-1", "learner", "您好", sequence=2)

        session = self.repository.get_owned_session(1, "session-1")

        self.assertEqual(session.case_definition_id, "case-1")
        self.assertEqual(session.case_version_id, "case-1-v1")
        self.assertEqual(session.visible_context, {"chief_complaint": "咳嗽三日"})
        self.assertEqual(session.patient_context, {"response_style": "简短"})
        self.assertEqual([(message.role, message.sequence) for message in session.messages], [
            ("patient", 1), ("learner", 2),
        ])
        self.assertFalse(hasattr(session, "golden_standard"))
        self.assertFalse(hasattr(session, "rubric"))

    def test_other_owner_and_missing_session_are_indistinguishable(self):
        self.repository.create_case(
            case_definition_id="case-1", case_version_id="case-1-v1", title="训练",
            visible_context={}, patient_context={}, golden_standard={}, rubric={},
        )
        self.repository.create_session("session-1", 1, "case-1-v1")

        self.assertIsNone(self.repository.get_owned_session(2, "session-1"))
        self.assertIsNone(self.repository.get_owned_session(1, "missing"))

    def test_help_record_is_unique_per_session_and_expired_facts_are_retained(self):
        self.repository.create_case(
            case_definition_id="case-1", case_version_id="case-1-v1", title="训练",
            visible_context={}, patient_context={}, golden_standard={}, rubric={},
        )
        self.repository.create_session("session-1", 1, "case-1-v1")
        expired_at = datetime.utcnow() - timedelta(days=1)
        self.repository.append_message(
            1, "session-1", "patient", "旧症状", sequence=1,
            facts={"symptom": "旧症状"}, facts_expires_at=expired_at,
        )
        self.repository.save_help(1, "session-1", {"hint": "先问寒热"})
        self.repository.save_help(1, "session-1", {"hint": "重复保存"})

        session = self.Session()
        try:
            from APP.backend.case_training_models import CaseHelpRecord, CaseSessionMessageRecord

            self.assertEqual(session.query(CaseHelpRecord).filter_by(session_id="session-1").count(), 1)
            message = session.query(CaseSessionMessageRecord).filter_by(session_id="session-1").one()
            self.assertEqual(message.facts_json, '{"symptom": "旧症状"}')
            self.assertEqual(message.facts_expires_at, expired_at)
        finally:
            session.close()

    def test_cross_owner_message_and_help_writes_fail_without_mutation(self):
        self.repository.create_case(
            case_definition_id="case-1", case_version_id="case-1-v1", title="训练",
            visible_context={}, patient_context={}, golden_standard={}, rubric={},
        )
        self.repository.create_session("session-1", 1, "case-1-v1")

        with self.assertRaisesRegex(ValueError, "^case session unavailable$"):
            self.repository.append_message(2, "session-1", "patient", "越权", sequence=1)
        with self.assertRaisesRegex(ValueError, "^case session unavailable$"):
            self.repository.append_message(2, "missing", "patient", "缺失", sequence=1)
        with self.assertRaisesRegex(ValueError, "^case session unavailable$"):
            self.repository.save_help(2, "session-1", {"hint": "越权"})
        with self.assertRaisesRegex(ValueError, "^case session unavailable$"):
            self.repository.save_help(2, "missing", {"hint": "缺失"})

        session = self.Session()
        try:
            from APP.backend.case_training_models import CaseHelpRecord, CaseSessionMessageRecord

            self.assertEqual(session.query(CaseSessionMessageRecord).count(), 0)
            self.assertEqual(session.query(CaseHelpRecord).count(), 0)
        finally:
            session.close()

    def test_append_message_rejects_unknown_or_blank_roles(self):
        self.repository.create_case(
            case_definition_id="case-1", case_version_id="case-1-v1", title="训练",
            visible_context={}, patient_context={}, golden_standard={}, rubric={},
        )
        self.repository.create_session("session-1", 1, "case-1-v1")

        for role in ("", "assistant", "system"):
            with self.assertRaisesRegex(ValueError, "^invalid case message role$"):
                self.repository.append_message(1, "session-1", role, "内容", sequence=1)

    def test_append_message_rejects_content_over_8192_utf8_bytes_without_persisting(self):
        self.repository.create_case(
            case_definition_id="case-1", case_version_id="case-1-v1", title="训练",
            visible_context={}, patient_context={}, golden_standard={}, rubric={},
        )
        self.repository.create_session("session-1", 1, "case-1-v1")
        oversized_content = "中" * 2731

        self.assertEqual(len(oversized_content.encode("utf-8")), 8193)
        with self.assertRaisesRegex(ValueError, "^case message content exceeds 8192 bytes$"):
            self.repository.append_message(1, "session-1", "learner", oversized_content, sequence=1)

        session = self.Session()
        try:
            from APP.backend.case_training_models import CaseSessionMessageRecord

            self.assertEqual(session.query(CaseSessionMessageRecord).count(), 0)
        finally:
            session.close()

    def test_append_message_requires_positive_contiguous_sequences(self):
        self.repository.create_case(
            case_definition_id="case-1", case_version_id="case-1-v1", title="训练",
            visible_context={}, patient_context={}, golden_standard={}, rubric={},
        )
        self.repository.create_session("session-1", 1, "case-1-v1")

        for sequence in (0, -1, 2):
            with self.assertRaisesRegex(ValueError, "^invalid case message sequence$"):
                self.repository.append_message(1, "session-1", "patient", "内容", sequence=sequence)
        self.repository.append_message(1, "session-1", "patient", "首条", sequence=1)
        for sequence in (1, 3):
            with self.assertRaisesRegex(ValueError, "^invalid case message sequence$"):
                self.repository.append_message(1, "session-1", "learner", "内容", sequence=sequence)
        self.repository.append_message(1, "session-1", "learner", "次条", sequence=2)

        self.assertEqual(
            [message.sequence for message in self.repository.get_owned_session(1, "session-1").messages],
            [1, 2],
        )

    def test_runtime_schema_upgrades_existing_case_session_table_with_state_fields(self):
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE case_session_records (id INTEGER PRIMARY KEY, session_id VARCHAR(120), owner_user_id INTEGER, case_version_id VARCHAR(120), created_at DATETIME)")
            ensure_runtime_schema_for(engine)
            columns = {column["name"] for column in inspect(engine).get_columns("case_session_records")}
            self.assertTrue({
                "mode", "status", "learner_messages", "scoring_enabled", "help_used", "expires_at",
            }.issubset(columns))
        finally:
            engine.dispose()

    def test_runtime_schema_skips_case_session_upgrade_when_table_is_absent(self):
        engine = create_engine("sqlite://")
        try:
            Base.metadata.create_all(bind=engine, tables=[UserModel.__table__])
            ensure_runtime_schema_for(engine)
        finally:
            engine.dispose()

    def test_runtime_schema_creation_is_repeatable_for_case_tables(self):
        engine = create_engine("sqlite://")
        try:
            ensure_runtime_schema_for(engine)
            ensure_runtime_schema_for(engine)
            names = set(engine.dialect.get_table_names(engine.connect()))
            self.assertTrue({
                "case_definition_records", "case_version_records", "case_session_records",
                "case_session_message_records", "case_help_records",
            }.issubset(names))
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()

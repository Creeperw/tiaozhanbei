import json
import os
import tempfile
import unittest
import sqlite3
from dataclasses import FrozenInstanceError
from unittest.mock import ANY, patch

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, ForeignKeyConstraint, Index, event,
    Integer, MetaData, String, Table, Text, UniqueConstraint, create_engine, text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.pool import StaticPool

from APP.backend import database


class InterruptedMigration(Exception):
    pass


class LearningSchemaTests(unittest.TestCase):
    def make_engine(self):
        return create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

    def make_file_engine(self):
        handle, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        engine = create_engine(f"sqlite:///{path}")
        self.addCleanup(engine.dispose)
        return engine

    def create_target_schema(self, engine):
        with engine.begin() as connection:
            database.Base.metadata.tables["users"].create(connection)
            for table_name in database._AUTHORITATIVE_LEARNING_TABLES:
                database.Base.metadata.tables[table_name].create(connection)

    def create_target_schema_missing_index(self, engine, table_name, column_name):
        self.create_target_schema(engine)
        with engine.begin() as connection:
            index = next(
                item["name"] for item in database.inspect(connection).get_indexes(table_name)
                if item["column_names"] == [column_name]
            )
            connection.execute(text(f'DROP INDEX "{index}"'))

    def create_target_schema_with_wrong_column_type(self, engine, table_name, column_name):
        with engine.begin() as connection:
            database.Base.metadata.tables["users"].create(connection)
            for name in database._AUTHORITATIVE_LEARNING_TABLES:
                if name != table_name:
                    database.Base.metadata.tables[name].create(connection)
            connection.execute(text("""
                CREATE TABLE learning_attempts (
                    id INTEGER NOT NULL PRIMARY KEY,
                    attempt_id VARCHAR(120) NOT NULL UNIQUE,
                    learner_id INTEGER NOT NULL,
                    attempt_type INTEGER,
                    source_task_id VARCHAR(120),
                    request_id VARCHAR(120),
                    status VARCHAR(50),
                    submitted_at DATETIME,
                    source_kind VARCHAR(80),
                    schema_version VARCHAR(40),
                    created_at DATETIME,
                    FOREIGN KEY(learner_id) REFERENCES users (id)
                )
            """))

    def test_physical_schema_fingerprint_rejects_missing_index(self):
        engine = self.make_file_engine()
        self.create_target_schema_missing_index(engine, "learning_attempts", "request_id")
        canonical_names = {name: name for name in database._AUTHORITATIVE_LEARNING_TABLES}

        self.assertFalse(database.physical_schema_matches_target(engine, canonical_names))

    def test_physical_schema_fingerprint_rejects_wrong_column_type(self):
        engine = self.make_file_engine()
        self.create_target_schema_with_wrong_column_type(engine, "learning_attempts", "attempt_type")
        canonical_names = {name: name for name in database._AUTHORITATIVE_LEARNING_TABLES}

        self.assertFalse(database.physical_schema_matches_target(engine, canonical_names))

    def test_physical_target_includes_audit_artifact_composite_index(self):
        engine = self.make_file_engine()
        self.create_target_schema(engine)
        snapshot = database.inspect_authoritative_schema_snapshot(engine, {})
        self.assertIn(
            (("source_artifact_id", "source_artifact_version"), False),
            snapshot["audit_result_records"]["indexes"],
        )

    def test_physical_schema_fingerprint_rejects_missing_audit_artifact_composite_index(self):
        engine = self.make_file_engine()
        self.create_target_schema(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP INDEX ix_audit_result_records_source_artifact"))
        self.assertFalse(database.physical_schema_matches_target(engine))

    def test_physical_snapshot_contract_covers_all_authoritative_tables(self):
        engine = self.make_file_engine()
        self.create_target_schema(engine)
        snapshot = database.inspect_authoritative_schema_snapshot(engine, {})
        self.assertEqual(set(snapshot), set(database._AUTHORITATIVE_LEARNING_TABLES))
        for table_name in database._AUTHORITATIVE_LEARNING_TABLES:
            table_snapshot = snapshot[table_name]
            self.assertTrue(table_snapshot["primary_key"])
            self.assertTrue(table_snapshot["columns"])
            self.assertTrue(table_snapshot["indexes"])
            self.assertIsInstance(table_snapshot["uniques"], list)
            self.assertIsInstance(table_snapshot["foreign_keys"], list)
            self.assertEqual(table_snapshot["primary_key"], list(table_snapshot["primary_key"]))
            for name, type_name, nullable in table_snapshot["columns"]:
                self.assertIsInstance(name, str)
                self.assertIsInstance(type_name, str)
                self.assertIsInstance(nullable, bool)
            self.assertEqual(table_snapshot["uniques"], sorted(table_snapshot["uniques"]))
            self.assertEqual(table_snapshot["foreign_keys"], sorted(table_snapshot["foreign_keys"]))
            self.assertEqual(table_snapshot["indexes"], sorted(table_snapshot["indexes"]))
        self.assertEqual(snapshot["audit_result_records"]["uniques"], [("audit_id",)])
        self.assertEqual(snapshot["audit_result_records"]["foreign_keys"], [
            (("source_artifact_id", "source_artifact_version"), "grading_result_records", ("artifact_id", "version"), None, None)
        ])

    def test_physical_snapshot_maps_shadow_audit_foreign_key_to_canonical_table(self):
        engine = self.make_file_engine()
        self.create_target_schema(engine)
        physical_names = {
            name: database.controlled_sqlite_name(name, "shadow")
            for name in database._AUTHORITATIVE_LEARNING_TABLES
        }
        with engine.begin() as connection:
            for table_name in database._AUTHORITATIVE_LEARNING_TABLES:
                connection.execute(text(
                    f'ALTER TABLE "{table_name}" RENAME TO "{physical_names[table_name]}"'
                ))
        snapshot = database.inspect_authoritative_schema_snapshot(engine, physical_names)
        self.assertIn(
            (("source_artifact_id", "source_artifact_version"), "grading_result_records", ("artifact_id", "version"), None, None),
            snapshot["audit_result_records"]["foreign_keys"],
        )

    def test_physical_target_includes_audit_artifact_foreign_key(self):
        engine = self.make_file_engine()
        self.create_target_schema(engine)
        snapshot = database.inspect_authoritative_schema_snapshot(engine, {})
        self.assertIn(
            (("source_artifact_id", "source_artifact_version"), "grading_result_records", ("artifact_id", "version"), None, None),
            snapshot["audit_result_records"]["foreign_keys"],
        )

    def test_physical_schema_fingerprint_rejects_missing_audit_artifact_foreign_key(self):
        engine = self.make_file_engine()
        self.create_target_schema(engine)
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE audit_result_records RENAME TO audit_result_records_old"))
            connection.execute(text("""
                CREATE TABLE audit_result_records (
                    id INTEGER NOT NULL PRIMARY KEY,
                    audit_id VARCHAR(120) NOT NULL UNIQUE,
                    source_artifact_id VARCHAR(120) NOT NULL,
                    source_artifact_version INTEGER NOT NULL,
                    decision VARCHAR(50), reason TEXT, confidence FLOAT, status VARCHAR(50),
                    schema_version VARCHAR(40), payload_json TEXT
                )
            """))
        self.assertFalse(database.physical_schema_matches_target(engine))

    def test_physical_schema_fingerprint_rejects_missing_receipt_unique_index(self):
        engine = self.make_file_engine()
        self.create_target_schema(engine)
        with engine.begin() as connection:
            index = next(
                item["name"] for item in database.inspect(connection).get_indexes("learning_writeback_receipts")
                if item["column_names"] == ["idempotency_key"]
            )
            connection.execute(text(f'DROP INDEX "{index}"'))
        self.assertFalse(database.physical_schema_matches_target(engine))

    def test_physical_snapshot_preserves_duplicate_index_multiplicity(self):
        engine = self.make_file_engine()
        self.create_target_schema(engine)
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE INDEX ix_learning_attempts_request_id_duplicate ON learning_attempts (request_id)"
            ))
        snapshot = database.inspect_authoritative_schema_snapshot(engine, {})
        self.assertEqual(snapshot["learning_attempts"]["indexes"].count((("request_id",), False)), 2)

    def enable_foreign_keys(self, engine):
        with engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys = ON"))

    def create_v1_authoritative_fixture(self, engine):
        metadata = MetaData()
        users = Table("users", metadata, Column("id", Integer, primary_key=True))
        attempts = Table("learning_attempts", metadata,
            Column("id", Integer, primary_key=True), Column("attempt_id", String(120), nullable=False),
            Column("learner_id", Integer, ForeignKey("users.id"), nullable=False), Column("attempt_type", String(80)),
            Column("source_task_id", String(120)), Column("request_id", String(120)), Column("status", String(50)),
            UniqueConstraint("attempt_id"), Index("ix_v1_attempt_request", "request_id"))
        items = Table("learning_attempt_items", metadata,
            Column("id", Integer, primary_key=True), Column("attempt_item_id", String(120), nullable=False),
            Column("attempt_id", String(120), ForeignKey("learning_attempts.attempt_id"), nullable=False),
            Column("question_version_id", String(120)), Column("submitted_answer", Text), Column("duration_sec", Integer),
            Column("hint_used", Boolean), Column("kp_snapshot_json", Text), UniqueConstraint("attempt_item_id"))
        artifacts = Table("grading_result_records", metadata,
            Column("id", Integer, primary_key=True), Column("artifact_id", String(120), nullable=False),
            Column("attempt_item_id", String(120), ForeignKey("learning_attempt_items.attempt_item_id"), nullable=False),
            Column("version", Integer, nullable=False), Column("score", Float), Column("max_score", Float),
            Column("is_correct", Boolean), Column("error_types_json", Text), Column("error_reason", Text),
            Column("kp_ids_json", Text), Column("evidence_pack_id", String(120)), Column("confidence", Float), Column("status", String(50)),
            UniqueConstraint("artifact_id", "version"))
        audits = Table("audit_result_records", metadata,
            Column("id", Integer, primary_key=True), Column("audit_id", String(120), nullable=False),
            Column("source_artifact_id", String(120), nullable=False), Column("source_artifact_version", Integer, nullable=False),
            Column("decision", String(50)), Column("reason", Text), Column("confidence", Float), Column("status", String(50)),
            UniqueConstraint("audit_id"), ForeignKeyConstraint(["source_artifact_id", "source_artifact_version"], ["grading_result_records.artifact_id", "grading_result_records.version"]))
        receipts = Table("learning_writeback_receipts", metadata,
            Column("id", Integer, primary_key=True), Column("receipt_id", String(120), nullable=False), Column("idempotency_key", String(200), nullable=False),
            Column("attempt_item_id", String(120), ForeignKey("learning_attempt_items.attempt_item_id"), nullable=False),
            Column("grading_artifact_id", String(120), nullable=False), Column("grading_artifact_version", Integer, nullable=False),
            Column("audit_id", String(120), ForeignKey("audit_result_records.audit_id"), nullable=False), Column("status", String(50)), Column("effect_refs_json", Text),
            UniqueConstraint("receipt_id"), UniqueConstraint("idempotency_key"), ForeignKeyConstraint(["grading_artifact_id", "grading_artifact_version"], ["grading_result_records.artifact_id", "grading_result_records.version"]))
        mastery = Table("knowledge_mastery_states", metadata, Column("id", Integer, primary_key=True), Column("mastery_state_id", String(120), nullable=False), Column("learner_id", Integer, ForeignKey("users.id"), nullable=False), Column("kp_id", String(120), nullable=False), Column("mastery_score", Float), Column("mastery_confidence", Float), Column("attempt_count", Integer), UniqueConstraint("mastery_state_id"), UniqueConstraint("learner_id", "kp_id"))
        history = Table("mastery_history_records", metadata, Column("id", Integer, primary_key=True), Column("history_id", String(120), nullable=False), Column("learner_id", Integer, ForeignKey("users.id"), nullable=False), Column("kp_id", String(120)), Column("trigger_attempt_item_id", String(120), ForeignKey("learning_attempt_items.attempt_item_id")), Column("mastery_score", Float), Column("mastery_confidence", Float), UniqueConstraint("history_id"))
        states = Table("learner_kp_review_states", metadata, Column("id", Integer, primary_key=True), Column("review_state_id", String(120), nullable=False), Column("learner_id", Integer, ForeignKey("users.id"), nullable=False), Column("kp_id", String(120), nullable=False), Column("lambda_per_day", Float), Column("recent_five_wrong_count", Integer), Column("consecutive_independent_correct", Integer), Column("consecutive_wrong_count", Integer), Column("review_stage", String(50)), Column("stability_seconds", Float), Column("retention_estimate", Float), Column("requires_remediation", Boolean), Column("status", String(50)), UniqueConstraint("review_state_id"), UniqueConstraint("learner_id", "kp_id"))
        tasks = Table("review_tasks", metadata, Column("id", Integer, primary_key=True), Column("review_task_id", String(120), nullable=False), Column("learner_id", Integer, ForeignKey("users.id"), nullable=False), Column("review_state_id", String(120), ForeignKey("learner_kp_review_states.review_state_id"), nullable=False), Column("primary_kp_id", String(120)), Column("source_type", String(80)), Column("review_type", String(80)), Column("reason_codes_json", Text), Column("status", String(50)), Column("source_attempt_item_id", String(120), ForeignKey("learning_attempt_items.attempt_item_id")), UniqueConstraint("review_task_id"))
        with engine.begin() as connection:
            metadata.create_all(connection)
            connection.execute(users.insert(), {"id": 1})
            connection.execute(attempts.insert(), {"id": 1, "attempt_id": "attempt-v1", "learner_id": 1, "attempt_type": "practice", "source_task_id": "task-v1", "request_id": "request-v1", "status": "submitted"})
            connection.execute(items.insert(), {"id": 1, "attempt_item_id": "item-v1", "attempt_id": "attempt-v1", "question_version_id": "question-v1", "submitted_answer": "answer", "duration_sec": 1, "hint_used": False, "kp_snapshot_json": "[]"})
            connection.execute(artifacts.insert(), {"id": 1, "artifact_id": "artifact-v1", "attempt_item_id": "item-v1", "version": 1, "score": 1, "max_score": 1, "is_correct": True, "error_types_json": "[]", "error_reason": "", "kp_ids_json": "[]", "evidence_pack_id": "", "confidence": 1, "status": "completed"})
            connection.execute(audits.insert(), {"id": 1, "audit_id": "audit-v1", "source_artifact_id": "artifact-v1", "source_artifact_version": 1, "decision": "approved", "reason": "", "confidence": 1, "status": "completed"})
            connection.execute(receipts.insert(), {"id": 1, "receipt_id": "receipt-v1", "idempotency_key": "key-v1", "attempt_item_id": "item-v1", "grading_artifact_id": "artifact-v1", "grading_artifact_version": 1, "audit_id": "audit-v1", "status": "completed", "effect_refs_json": "[]"})
            connection.execute(mastery.insert(), {"id": 1, "mastery_state_id": "mastery-v1", "learner_id": 1, "kp_id": "kp-v1", "mastery_score": 0, "mastery_confidence": 0, "attempt_count": 0})
            connection.execute(history.insert(), {"id": 1, "history_id": "history-v1", "learner_id": 1, "kp_id": "kp-v1", "trigger_attempt_item_id": "item-v1", "mastery_score": 0, "mastery_confidence": 0})
            connection.execute(states.insert(), {"id": 1, "review_state_id": "review-state-v1", "learner_id": 1, "kp_id": "kp-v1", "lambda_per_day": 0, "recent_five_wrong_count": 0, "consecutive_independent_correct": 0, "consecutive_wrong_count": 0, "review_stage": "new", "stability_seconds": 0, "retention_estimate": 0, "requires_remediation": False, "status": "active"})
            connection.execute(tasks.insert(), {"id": 1, "review_task_id": "task-v1", "learner_id": 1, "review_state_id": "review-state-v1", "primary_kp_id": "kp-v1", "source_type": "training_workshop", "review_type": "review", "reason_codes_json": "[]", "status": "pending", "source_attempt_item_id": "item-v1"})
            return database.build_authoritative_manifest(connection, database._AUTHORITATIVE_LEARNING_TABLES)

    def restart_engine_and_run_upgrade(self, engine):
        path = engine.url.database
        engine.dispose()
        restarted = create_engine(f"sqlite:///{path}")
        self.addCleanup(restarted.dispose)
        database.ensure_runtime_schema_for(restarted)
        return restarted

    def stop_after(self, stage):
        def checkpoint(current_stage):
            if current_stage == stage:
                raise InterruptedMigration(stage)

        return checkpoint

    def migration_status(self, engine):
        with engine.begin() as connection:
            return connection.execute(text(
                "SELECT status FROM runtime_schema_migrations "
                "WHERE migration_id = 'authoritative_learning_records_v2'"
            )).scalar_one()

    def read_receipt_key(self, engine):
        with engine.begin() as connection:
            return connection.execute(text(
                "SELECT idempotency_key FROM learning_writeback_receipts WHERE receipt_id = 'receipt-v1'"
            )).scalar_one()

    def read_attempt_id(self, engine):
        with engine.begin() as connection:
            return connection.execute(text("SELECT attempt_id FROM learning_attempts")).scalar_one()

    def foreign_key_check(self, engine):
        with engine.begin() as connection:
            return connection.execute(text("PRAGMA foreign_key_check")).all()

    def restart_after(self, stage):
        engine = self.make_file_engine()
        self.enable_foreign_keys(engine)
        self.create_v1_authoritative_fixture(engine)
        with self.assertRaises(InterruptedMigration):
            database.ensure_runtime_schema_for(engine, checkpoint=self.stop_after(stage))
        restarted = self.restart_engine_and_run_upgrade(engine)
        self.assertEqual(self.foreign_key_check(restarted), [])
        self.assert_no_controlled_objects(restarted)
        return self.migration_status(restarted)

    def test_switching_checkpoint_recovers_with_fresh_engine(self):
        self.assertEqual(self.restart_after("switching_committed_before_begin"), "verified")

    def test_switched_checkpoint_recovers_with_fresh_engine(self):
        self.assertEqual(self.restart_after("switched_committed_before_verify"), "verified")

    def test_divergent_candidates_fail_closed(self):
        engine = self.make_file_engine()
        self.create_v1_authoritative_fixture(engine)
        with self.assertRaises(InterruptedMigration):
            database.ensure_runtime_schema_for(
                engine, checkpoint=self.stop_after("switched_committed_before_verify")
            )
        controlled = database.controlled_sqlite_name("learning_attempts", "backup")
        with engine.begin() as connection:
            connection.execute(text("UPDATE learning_attempts SET attempt_id = 'current-other'"))
            connection.execute(text(
                f'UPDATE "{controlled}" SET attempt_id = \'backup-other\''
            ))
        path = engine.url.database
        engine.dispose()
        restarted = create_engine(f"sqlite:///{path}")
        self.addCleanup(restarted.dispose)
        with self.assertRaisesRegex(RuntimeError, "authoritative_learning_schema_recovery_failed"):
            database.ensure_runtime_schema_for(restarted)
        self.assertEqual(self.migration_status(restarted), "recovery_failed")

    def test_prepared_checkpoint_recovers_with_fresh_engine(self):
        engine = self.make_file_engine()
        self.create_v1_authoritative_fixture(engine)

        with self.assertRaises(InterruptedMigration):
            database.ensure_runtime_schema_for(
                engine, checkpoint=self.stop_after("prepared_committed")
            )

        restarted = self.restart_engine_and_run_upgrade(engine)
        self.assertEqual(self.migration_status(restarted), "verified")
        self.assertEqual(self.read_attempt_id(restarted), "attempt-v1")
        self.assert_verified_schema(restarted)

    def test_staged_checkpoint_recovers_with_fresh_engine(self):
        engine = self.make_file_engine()
        self.create_v1_authoritative_fixture(engine)

        with self.assertRaises(InterruptedMigration):
            database.ensure_runtime_schema_for(
                engine, checkpoint=self.stop_after("staged_committed")
            )

        restarted = self.restart_engine_and_run_upgrade(engine)
        self.assertEqual(self.migration_status(restarted), "verified")
        self.assertEqual(self.read_attempt_id(restarted), "attempt-v1")
        self.assert_verified_schema(restarted)

    def test_staged_restart_rebuilds_ledger_controlled_incomplete_shadow(self):
        engine = self.make_file_engine()
        self.create_v1_authoritative_fixture(engine)
        with self.assertRaises(InterruptedMigration):
            database.ensure_runtime_schema_for(
                engine, checkpoint=self.stop_after("staged_committed")
            )
        with engine.begin() as connection:
            connection.execute(text(
                "DELETE FROM review_tasks__authoritative_learning_records_v2__shadow "
                "WHERE review_task_id = 'task-v1'"
            ))

        restarted = self.restart_engine_and_run_upgrade(engine)
        self.assertEqual(self.migration_status(restarted), "verified")
        self.assert_verified_schema(restarted)

    def test_staged_restart_rejects_unlisted_controlled_object(self):
        engine = self.make_file_engine()
        self.create_v1_authoritative_fixture(engine)
        with self.assertRaises(InterruptedMigration):
            database.ensure_runtime_schema_for(
                engine, checkpoint=self.stop_after("staged_committed")
            )
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE learning_attempts__authoritative_learning_records_v2__backup "
                "(id INTEGER PRIMARY KEY)"
            ))

        path = engine.url.database
        engine.dispose()
        restarted = create_engine(f"sqlite:///{path}")
        self.addCleanup(restarted.dispose)
        with self.assertRaisesRegex(RuntimeError, "authoritative_learning_schema_recovery_failed"):
            database.ensure_runtime_schema_for(restarted)
        self.assertEqual(self.migration_status(restarted), "recovery_failed")

    def test_staged_restart_preserves_untrusted_ledger_shadow_and_fails_closed(self):
        engine = self.make_file_engine()
        self.create_v1_authoritative_fixture(engine)
        with self.assertRaises(InterruptedMigration):
            database.ensure_runtime_schema_for(
                engine, checkpoint=self.stop_after("staged_committed")
            )
        shadow = database.controlled_sqlite_name("learning_attempts", "shadow")
        with engine.begin() as connection:
            connection.execute(text(f'DROP TABLE "{shadow}"'))
            connection.execute(text(f'CREATE TABLE "{shadow}" (id INTEGER PRIMARY KEY)'))

        path = engine.url.database
        engine.dispose()
        restarted = create_engine(f"sqlite:///{path}")
        self.addCleanup(restarted.dispose)
        with self.assertRaisesRegex(RuntimeError, "authoritative_learning_schema_recovery_failed"):
            database.ensure_runtime_schema_for(restarted)
        self.assertEqual(self.migration_status(restarted), "recovery_failed")
        with restarted.begin() as connection:
            self.assertEqual(
                connection.execute(text(f'SELECT name FROM sqlite_master WHERE name = "{shadow}"')).scalar_one(),
                shadow,
            )

    def test_prepared_restart_rebuilds_safe_partial_ledger_shadow(self):
        engine = self.make_file_engine()
        self.create_v1_authoritative_fixture(engine)
        with self.assertRaises(InterruptedMigration):
            database.ensure_runtime_schema_for(
                engine, checkpoint=self.stop_after("prepared_committed")
            )
        shadow_names = {
            name: database.controlled_sqlite_name(name, "shadow")
            for name in database._AUTHORITATIVE_LEARNING_TABLES
        }
        with engine.begin() as connection:
            for table_name, shadow in shadow_names.items():
                database.clone_authoritative_table(table_name, shadow).create(connection)

        restarted = self.restart_engine_and_run_upgrade(engine)
        self.assertEqual(self.migration_status(restarted), "verified")
        self.assert_verified_schema(restarted)

    def test_prepared_restart_rejects_ledger_listed_backup(self):
        engine = self.make_file_engine()
        self.create_v1_authoritative_fixture(engine)
        with self.assertRaises(InterruptedMigration):
            database.ensure_runtime_schema_for(
                engine, checkpoint=self.stop_after("prepared_committed")
            )
        backup = database.controlled_sqlite_name("learning_attempts", "backup")
        with engine.begin() as connection:
            connection.execute(text(f'CREATE TABLE "{backup}" (id INTEGER PRIMARY KEY)'))

        path = engine.url.database
        engine.dispose()
        restarted = create_engine(f"sqlite:///{path}")
        self.addCleanup(restarted.dispose)
        with self.assertRaisesRegex(RuntimeError, "authoritative_learning_schema_recovery_failed"):
            database.ensure_runtime_schema_for(restarted)
        self.assertEqual(self.migration_status(restarted), "recovery_failed")

    def assert_staged_shadows_match_source(self, engine):
        shadow_names = {
            name: database.controlled_sqlite_name(name, "shadow")
            for name in database._AUTHORITATIVE_LEARNING_TABLES
        }
        self.assertTrue(database.physical_schema_matches_target(engine, shadow_names))
        with engine.begin() as connection:
            source = database.build_authoritative_manifest(
                connection, database._AUTHORITATIVE_LEARNING_TABLES
            )
            shadows = database.build_authoritative_manifest(
                connection, database._AUTHORITATIVE_LEARNING_TABLES, shadow_names
            )
        self.assertEqual(shadows["tables"], source["tables"])

    def assert_verified_schema(self, engine):
        self.assertTrue(database.physical_schema_matches_target(engine))
        self.assertEqual(self.foreign_key_check(engine), [])
        self.assert_no_controlled_objects(engine)

    def test_upgrade_cleans_controlled_objects_with_foreign_keys_enabled(self):
        engine = self.make_engine()
        try:
            self.enable_foreign_keys(engine)
            self.create_v1_authoritative_fixture(engine)
            database.ensure_runtime_schema_for(engine)
            self.assert_verified_schema(engine)
            with engine.begin() as connection:
                self.assertEqual(connection.execute(text("PRAGMA foreign_keys")).scalar_one(), 1)
        finally:
            engine.dispose()

    def test_runtime_schema_upgrades_v1_records_and_verifies_ledger(self):
        engine = self.make_engine()
        try:
            self.enable_foreign_keys(engine)
            self.create_v1_authoritative_fixture(engine)
            database.ensure_runtime_schema_for(engine)
            database.ensure_runtime_schema_for(engine)
            self.assertEqual(self.migration_status(engine), "verified")
            self.assertEqual(self.read_receipt_key(engine), "key-v1")
            self.assertEqual(self.foreign_key_check(engine), [])
            self.assert_verified_schema(engine)
        finally:
            engine.dispose()

    def insert_switching_ledger(self, engine, manifest, canonical_state, backup_state):
        controlled = {
            table_name: {
                "shadow": database.controlled_sqlite_name(table_name, "shadow"),
                "backup": database.controlled_sqlite_name(table_name, "backup"),
            }
            for table_name in database._AUTHORITATIVE_LEARNING_TABLES
        }
        with engine.begin() as connection:
            previous_fk = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
            connection.execute(text("PRAGMA foreign_keys = OFF"))
            try:
                for table_name in database._AUTHORITATIVE_LEARNING_TABLES:
                    backup = controlled[table_name]["backup"]
                    shadow = controlled[table_name]["shadow"]
                    connection.execute(text(f'ALTER TABLE "{table_name}" RENAME TO "{backup}"'))
                    database.clone_authoritative_table(table_name, shadow).create(connection)
                    source_columns = {column["name"] for column in database.inspect(connection).get_columns(backup)}
                    columns = [column.name for column in database.Base.metadata.tables[table_name].columns if column.name in source_columns]
                    quoted = ", ".join(f'"{column}"' for column in columns)
                    connection.execute(text(f'INSERT INTO "{shadow}" ({quoted}) SELECT {quoted} FROM "{backup}"'))
                    connection.execute(text(f'ALTER TABLE "{shadow}" RENAME TO "{table_name}"'))
                if canonical_state == "incomplete":
                    connection.execute(text("DELETE FROM learning_attempts WHERE attempt_id = 'attempt-v1'"))
                if canonical_state == "divergent":
                    connection.execute(text("UPDATE learning_attempts SET attempt_id = 'attempt-other' WHERE attempt_id = 'attempt-v1'"))
                if backup_state == "divergent":
                    connection.execute(text(
                        f'UPDATE "{controlled["learning_attempts"]["backup"]}" '
                        "SET attempt_id = 'attempt-backup-other' WHERE attempt_id = 'attempt-v1'"
                    ))
            finally:
                connection.execute(text(f"PRAGMA foreign_keys = {previous_fk}"))
            database.RuntimeSchemaMigration.__table__.create(connection, checkfirst=True)
            connection.execute(database.RuntimeSchemaMigration.__table__.insert().values(
                migration_id=database.AUTHORITATIVE_LEARNING_MIGRATION_ID,
                target_version=database.AUTHORITATIVE_LEARNING_TARGET_VERSION,
                status="switching",
                current_step="switch",
                attempt_count=1,
                controlled_objects_json=json.dumps(controlled, sort_keys=True),
                source_manifest_json=json.dumps(manifest, sort_keys=True),
                verification_summary_json="{}",
            ))

    def assert_no_controlled_objects(self, engine):
        with engine.begin() as connection:
            names = connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name LIKE '%__authoritative_learning_records_v2__%'"
            )).scalars().all()
        self.assertEqual(names, [])

    def test_runtime_schema_restores_sole_matching_backup_when_current_is_incomplete(self):
        engine = self.make_engine()
        try:
            self.enable_foreign_keys(engine)
            manifest = self.create_v1_authoritative_fixture(engine)
            self.insert_switching_ledger(engine, manifest, "incomplete", "matches_manifest")
            database.ensure_runtime_schema_for(engine)
            self.assertEqual(self.migration_status(engine), "verified")
            self.assert_verified_schema(engine)
        finally:
            engine.dispose()

    def test_switching_v1_canonical_without_candidates_is_not_verified(self):
        engine = self.make_engine()
        try:
            self.enable_foreign_keys(engine)
            manifest = self.create_v1_authoritative_fixture(engine)
            database.RuntimeSchemaMigration.__table__.create(engine, checkfirst=True)
            controlled = {
                name: {role: database.controlled_sqlite_name(name, role) for role in ("shadow", "backup")}
                for name in database._AUTHORITATIVE_LEARNING_TABLES
            }
            with engine.begin() as connection:
                connection.execute(database.RuntimeSchemaMigration.__table__.insert().values(
                    migration_id=database.AUTHORITATIVE_LEARNING_MIGRATION_ID,
                    target_version="v2", status="switching", current_step="switch",
                    attempt_count=1, controlled_objects_json=json.dumps(controlled, sort_keys=True),
                    source_manifest_json=json.dumps(manifest, sort_keys=True), verification_summary_json="{}",
                ))
            with self.assertRaisesRegex(RuntimeError, "authoritative_learning_schema_recovery_failed"):
                database.ensure_runtime_schema_for(engine)
        finally:
            engine.dispose()

    def test_runtime_schema_fails_closed_for_divergent_candidates(self):
        engine = self.make_engine()
        try:
            self.enable_foreign_keys(engine)
            manifest = self.create_v1_authoritative_fixture(engine)
            self.insert_switching_ledger(engine, manifest, "divergent", "divergent")
            with self.assertRaisesRegex(RuntimeError, "authoritative_learning_schema_recovery_failed"):
                database.ensure_runtime_schema_for(engine)
            self.assertEqual(self.migration_status(engine), "recovery_failed")
        finally:
            engine.dispose()

    def test_authoritative_migration_ledger_records_manifest(self):
        engine = self.make_engine()
        try:
            database.ensure_runtime_schema_for(engine)
            with engine.begin() as connection:
                self.assertIsNone(connection.execute(text(
                    "SELECT 1 FROM runtime_schema_migrations "
                    "WHERE migration_id = 'authoritative_learning_records_v2'"
                )).scalar())
                connection.execute(text("DROP TABLE learning_attempts"))
                connection.execute(text("""
                    CREATE TABLE learning_attempts (
                        id INTEGER PRIMARY KEY,
                        attempt_id VARCHAR(120)
                    )
                """))

            database.ensure_runtime_schema_for(engine)

            with engine.begin() as connection:
                row = connection.execute(text("""
                    SELECT migration_id, target_version, status, current_step,
                           completed_at, source_manifest_json
                    FROM runtime_schema_migrations
                    WHERE migration_id = 'authoritative_learning_records_v2'
                """)).one()
            self.assertEqual(row.migration_id, "authoritative_learning_records_v2")
            self.assertEqual(row.target_version, "v2")
            self.assertEqual(row.status, "verified")
            self.assertEqual(row.current_step, "verified")
            self.assertIsNotNone(row.completed_at)
            self.assertTrue(json.loads(row.source_manifest_json))
        finally:
            engine.dispose()

    def test_prepared_ledger_is_preserved_for_task_two(self):
        engine = self.make_engine()
        try:
            database.ensure_runtime_schema_for(engine)
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE learning_attempts"))
                connection.execute(text("""
                    CREATE TABLE learning_attempts (
                        id INTEGER PRIMARY KEY,
                        attempt_id VARCHAR(120)
                    )
                """))
            database.ensure_runtime_schema_for(engine)
            database.ensure_runtime_schema_for(engine)

            with engine.begin() as connection:
                row = connection.execute(text("""
                    SELECT status, current_step, completed_at, attempt_count
                    FROM runtime_schema_migrations
                    WHERE migration_id = 'authoritative_learning_records_v2'
                """)).one()
            self.assertEqual((row.status, row.current_step), ("verified", "verified"))
            self.assertIsNotNone(row.completed_at)
            self.assertEqual(row.attempt_count, 1)
        finally:
            engine.dispose()

    def test_incomplete_constraints_create_prepared_ledger(self):
        engine = self.make_engine()
        try:
            database.ensure_runtime_schema_for(engine)
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE audit_result_records"))
                connection.execute(text("""
                    CREATE TABLE audit_result_records (
                        id INTEGER PRIMARY KEY,
                        audit_id VARCHAR(120),
                        source_artifact_id VARCHAR(120),
                        source_artifact_version INTEGER,
                        created_at DATETIME
                    )
                """))

            database.ensure_runtime_schema_for(engine)

            with engine.begin() as connection:
                status = connection.execute(text("""
                    SELECT status FROM runtime_schema_migrations
                    WHERE migration_id = 'authoritative_learning_records_v2'
                """)).scalar_one()
            self.assertEqual(status, "verified")
        finally:
            engine.dispose()

    def test_verified_runtime_migration_creates_question_version_tables_on_repeat(self):
        engine = self.make_engine()
        try:
            database.ensure_runtime_schema_for(engine)
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE question_kp_link_records"))
                connection.execute(text("DROP TABLE question_version_records"))
                connection.execute(database.RuntimeSchemaMigration.__table__.insert().values(
                    migration_id=database.AUTHORITATIVE_LEARNING_MIGRATION_ID,
                    target_version=database.AUTHORITATIVE_LEARNING_TARGET_VERSION,
                    status="verified",
                    current_step="verified",
                    attempt_count=1,
                    controlled_objects_json="{}",
                    source_manifest_json="{}",
                    verification_summary_json='{"cleanup_status":"completed"}',
                ))

            database.ensure_runtime_schema_for(engine)

            self.assertTrue(database.inspect(engine).has_table("question_version_records"))
            self.assertTrue(database.inspect(engine).has_table("question_kp_link_records"))
        finally:
            engine.dispose()

    def test_runtime_schema_migrates_preexisting_database_with_case_training_tables(self):
        engine = self.make_engine()
        try:
            database.Base.metadata.tables["users"].create(engine)

            database.ensure_runtime_schema_for(engine)

            inspector = database.inspect(engine)
            self.assertTrue({
                "case_definition_records", "case_version_records", "case_session_records",
                "case_session_message_records", "case_help_records",
            }.issubset(inspector.get_table_names()))
        finally:
            engine.dispose()

    def test_runtime_schema_migrates_preexisting_database_with_paper_tables(self):
        engine = self.make_engine()
        try:
            with engine.begin() as connection:
                database.Base.metadata.tables["users"].create(connection)
                database.Base.metadata.tables["training_task_records"].create(connection)
            database.ensure_runtime_schema_for(engine)
            with engine.begin() as connection:
                connection.execute(database.RuntimeSchemaMigration.__table__.insert().values(
                    migration_id=database.AUTHORITATIVE_LEARNING_MIGRATION_ID,
                    target_version=database.AUTHORITATIVE_LEARNING_TARGET_VERSION,
                    status="verified",
                    current_step="verified",
                    attempt_count=1,
                    controlled_objects_json="{}",
                    source_manifest_json="{}",
                    verification_summary_json='{"cleanup_status":"completed"}',
                ))
                connection.execute(text("DROP TABLE paper_items"))
                connection.execute(text("DROP TABLE paper_instances"))
            self.assertFalse(database.inspect(engine).has_table("paper_instances"))
            self.assertFalse(database.inspect(engine).has_table("paper_items"))

            database.ensure_runtime_schema_for(engine)
            database.ensure_runtime_schema_for(engine)

            inspector = database.inspect(engine)
            self.assertTrue(inspector.has_table("paper_instances"))
            self.assertTrue(inspector.has_table("paper_items"))
            self.assertTrue(any(
                fk["referred_table"] == "paper_instances"
                and fk["constrained_columns"] == ["paper_id"]
                for fk in inspector.get_foreign_keys("paper_items")
            ))
        finally:
            engine.dispose()

    def test_mysql_skips_task_one_schema_and_ledger_ddl(self):
        class FakeMySQLBind:
            class dialect:
                name = "mysql"

        with patch.object(database.RuntimeSchemaMigration.__table__, "create") as create, patch.object(
            database.Base.metadata, "create_all"
        ) as create_all, patch.object(database, "inspect") as schema_inspect:
            database.ensure_runtime_schema_for(FakeMySQLBind())

        create.assert_not_called()
        create_all.assert_not_called()
        schema_inspect.assert_not_called()

    def test_mysql_upgrades_phase_two_inventory_to_phase_three_once(self):
        executed = []
        created = []
        tables = {
            "users", "mistake_records", "question_version_records",
            "audit_result_records", "learning_attempt_items",
            "grading_result_records", "training_task_records",
        }
        columns = {
            name: {column.name for column in database.Base.metadata.tables[name].columns}
            for name in tables
        }
        columns.update({
            "grading_result_records": columns["grading_result_records"] - {"audit_generation"},
            "training_task_records": columns["training_task_records"] - {"claim_owner", "claim_expires_at"},
            "mistake_records": columns["mistake_records"] - {"attempt_item_id", "question_version_id"},
        })

        class Connection:
            def execute(self, statement):
                ddl = str(statement)
                executed.append(ddl)
                parts = ddl.split()
                if parts[:2] == ["ALTER", "TABLE"]:
                    columns[parts[2]].add(parts[5])
                elif ddl.startswith("SELECT COUNT(*)"):
                    return type("Result", (), {"scalar_one": lambda self: 0})()

        class Begin:
            def __enter__(self):
                return Connection()

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        class MySQLBind:
            dialect = mysql.dialect()

            def begin(self):
                return Begin()

        class Inspector:
            def has_table(self, table_name):
                return table_name in tables

            def get_columns(self, table_name):
                return [{"name": name} for name in columns[table_name]]

            def get_indexes(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{
                    "name": index.name,
                    "column_names": [column.name for column in index.columns],
                    "unique": index.unique,
                } for index in table.indexes
                    if all(column.name in columns[table_name] for column in index.columns)]

            def get_pk_constraint(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return {"constrained_columns": [column.name for column in table.primary_key.columns]}

            def get_unique_constraints(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{"column_names": [column.name for column in constraint.columns]}
                        for constraint in table.constraints
                        if isinstance(constraint, UniqueConstraint)
                        and all(column.name in columns[table_name]
                                for column in constraint.columns)]

            def get_foreign_keys(self, table_name):
                if table_name not in created:
                    return []
                table = database.Base.metadata.tables[table_name]
                return [{
                    "constrained_columns": [element.parent.name for element in constraint.elements],
                    "referred_table": constraint.elements[0].column.table.name,
                    "referred_columns": [element.column.name for element in constraint.elements],
                } for constraint in table.foreign_key_constraints]

        def create_table(table):
            def create(bind, checkfirst=False):
                self.assertTrue(checkfirst)
                created.append(table.name)
                tables.add(table.name)
                columns[table.name] = {column.name for column in table.columns}
            return create

        phase_three_tables = [
            database.Base.metadata.tables[name]
            for name in (
                "variation_sets", "variation_question_versions", "variation_rubrics"
            )
        ]
        patches = [
            patch.object(table, "create", side_effect=create_table(table))
            for table in phase_three_tables
        ]
        with patch.object(database, "inspect", return_value=Inspector()):
            for table_patch in patches:
                table_patch.start()
                self.addCleanup(table_patch.stop)
            database.ensure_runtime_schema_for(MySQLBind())

        self.assertEqual(created, [
            "variation_sets", "variation_question_versions", "variation_rubrics"
        ])
        self.assertCountEqual(
            [ddl for ddl in executed if " ADD COLUMN " in ddl],
            [
            "ALTER TABLE grading_result_records ADD COLUMN audit_generation INT NOT NULL DEFAULT 0",
            "ALTER TABLE training_task_records ADD COLUMN claim_owner VARCHAR(64) NULL",
            "ALTER TABLE training_task_records ADD COLUMN claim_expires_at DATETIME NULL",
            "ALTER TABLE mistake_records ADD COLUMN attempt_item_id VARCHAR(120) NULL",
            "ALTER TABLE mistake_records ADD COLUMN question_version_id VARCHAR(120) NULL",
            ],
        )
        self.assertTrue(any(
            "CREATE INDEX" in ddl and "claim_owner" in ddl for ddl in executed
        ))
        mistake_fk_ddl = [
            ddl for ddl in executed
            if ddl.startswith("ALTER TABLE mistake_records") and "FOREIGN KEY" in ddl
        ]
        self.assertEqual(
            sum("FOREIGN KEY (attempt_item_id)" in ddl for ddl in mistake_fk_ddl), 1,
        )
        self.assertEqual(
            sum("FOREIGN KEY (question_version_id)" in ddl for ddl in mistake_fk_ddl), 1,
        )

    def test_mysql_adds_nullable_dependency_columns_before_querying_them(self):
        executed = []
        tables = {
            "users", "mistake_records", "question_version_records",
            "audit_result_records", "learning_attempt_items",
            "grading_result_records", "training_task_records",
            "variation_sets", "variation_question_versions", "variation_rubrics",
        }
        columns = {
            name: {column.name for column in database.Base.metadata.tables[name].columns}
            for name in tables
        }
        columns["mistake_records"] -= {"attempt_item_id", "question_version_id"}

        class Result:
            def scalar_one(self):
                return 0

        class Connection:
            def execute(self, statement):
                sql = str(statement)
                if sql.startswith("SELECT COUNT(*) FROM mistake_records"):
                    referenced = {name for name in ("attempt_item_id", "question_version_id") if name in sql}
                    if not referenced <= columns["mistake_records"]:
                        raise RuntimeError("unknown future column")
                executed.append(sql)
                parts = sql.split()
                if parts[:2] == ["ALTER", "TABLE"] and "ADD COLUMN" in sql:
                    columns[parts[2]].add(parts[5])
                return Result()

        class Begin:
            def __enter__(self):
                return Connection()

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        class MySQLBind:
            dialect = mysql.dialect()

            def begin(self):
                return Begin()

        class Inspector:
            def has_table(self, table_name):
                return table_name in tables

            def get_columns(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{"name": column.name, "type": column.type, "nullable": column.nullable}
                        for column in table.columns if column.name in columns[table_name]]

            def get_indexes(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{"column_names": [column.name for column in index.columns],
                         "unique": index.unique}
                        for index in table.indexes
                        if all(column.name in columns[table_name] for column in index.columns)]

            def get_pk_constraint(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return {"constrained_columns": [column.name for column in table.primary_key.columns]}

            def get_unique_constraints(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{"column_names": [column.name for column in constraint.columns]}
                        for constraint in table.constraints if isinstance(constraint, UniqueConstraint)]

            def get_foreign_keys(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{
                    "constrained_columns": [element.parent.name for element in constraint.elements],
                    "referred_table": constraint.elements[0].column.table.name,
                    "referred_columns": [element.column.name for element in constraint.elements],
                } for constraint in table.foreign_key_constraints
                    if all(element.parent.name in columns[table_name] for element in constraint.elements)]

        with patch.object(database, "inspect", return_value=Inspector()):
            database.ensure_runtime_schema_for(MySQLBind())

        orphan_queries = [sql for sql in executed
                          if sql.startswith("SELECT COUNT(*) FROM mistake_records")]
        self.assertEqual(orphan_queries, [])
        for column_name in ("attempt_item_id", "question_version_id"):
            add_position = next(i for i, sql in enumerate(executed)
                                if f"ADD COLUMN {column_name}" in sql)
            fk_position = next(i for i, sql in enumerate(executed)
                               if sql.startswith("ALTER TABLE mistake_records")
                               and f"FOREIGN KEY ({column_name})" in sql)
            self.assertLess(add_position, fk_position)

    def test_mysql_rejects_incomplete_existing_dependency_table_before_ddl(self):
        begin_calls = []

        class MySQLBind:
            dialect = mysql.dialect()

            def begin(self):
                begin_calls.append(True)
                raise AssertionError("DDL must not begin for an incompatible dependency table")

        class Inspector:
            def has_table(self, table_name):
                return table_name in {
                    "users", "mistake_records", "question_version_records",
                    "audit_result_records", "learning_attempt_items",
                    "grading_result_records", "training_task_records",
                }

            def get_columns(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{"name": column.name, "type": column.type, "nullable": column.nullable}
                        for column in table.columns
                        if not (table_name == "grading_result_records" and column.name == "version")]

            def get_indexes(self, table_name):
                return []

            def get_pk_constraint(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return {"constrained_columns": [column.name for column in table.primary_key.columns]}

            def get_unique_constraints(self, table_name):
                return []

            def get_foreign_keys(self, table_name):
                return []

        with patch.object(database, "inspect", return_value=Inspector()), patch.object(
            database.Base.metadata.tables["variation_sets"], "create",
            side_effect=AssertionError("table DDL must not run"),
        ):
            with self.assertRaisesRegex(RuntimeError, "^phase3_mysql_schema_migration_failed$"):
                database.ensure_runtime_schema_for(MySQLBind())

        self.assertEqual(begin_calls, [])

    def test_mysql_rejects_unindexed_foreign_key_targets_before_ddl(self):
        begin_calls = []
        tables = {
            "users", "mistake_records", "question_version_records",
            "audit_result_records", "learning_attempt_items",
            "grading_result_records", "training_task_records",
        }
        unkeyed_targets = {
            ("audit_result_records", "audit_id"),
            ("question_version_records", "question_version_id"),
            ("learning_attempt_items", "attempt_item_id"),
        }

        class MySQLBind:
            dialect = mysql.dialect()

            def begin(self):
                begin_calls.append(True)
                raise AssertionError("DDL must not begin for an unindexed foreign key target")

        class Inspector:
            def has_table(self, table_name):
                return table_name in tables

            def get_columns(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{"name": column.name, "type": column.type, "nullable": column.nullable}
                        for column in table.columns]

            def get_indexes(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{"column_names": [column.name for column in index.columns],
                         "unique": index.unique}
                        for index in table.indexes
                        if not any((table_name, column.name) in unkeyed_targets
                                   for column in index.columns)]

            def get_pk_constraint(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return {"constrained_columns": [column.name for column in table.primary_key.columns]}

            def get_unique_constraints(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{"column_names": [column.name for column in constraint.columns]}
                        for constraint in table.constraints
                        if isinstance(constraint, UniqueConstraint)
                        and not any((table_name, column.name) in unkeyed_targets
                                    for column in constraint.columns)]

            def get_foreign_keys(self, table_name):
                return []

        with patch.object(database, "inspect", return_value=Inspector()), patch.object(
            database.Base.metadata.tables["variation_sets"], "create",
            side_effect=AssertionError("table DDL must not run"),
        ):
            with self.assertRaisesRegex(RuntimeError, "^phase3_mysql_schema_migration_failed$"):
                database.ensure_runtime_schema_for(MySQLBind())

        self.assertEqual(begin_calls, [])

    def test_mysql_repairs_dependency_indexes_and_foreign_keys_once(self):
        executed = []
        tables = {
            "users", "mistake_records", "question_version_records",
            "audit_result_records", "learning_attempt_items",
            "grading_result_records", "training_task_records",
            "variation_sets", "variation_question_versions", "variation_rubrics",
        }
        phase_three_columns = {
            "training_task_records": {"claim_owner", "claim_expires_at"},
            "mistake_records": {"attempt_item_id", "question_version_id"},
        }
        indexes = {
            name: [{
                "column_names": [column.name for column in index.columns],
                "unique": index.unique,
            } for index in database.Base.metadata.tables[name].indexes
                if not ({column.name for column in index.columns}
                        & phase_three_columns.get(name, set()))]
            for name in tables
        }
        foreign_keys = {name: [] for name in tables}

        class Result:
            def scalar_one(self):
                return 0

        class Connection:
            def execute(self, statement):
                ddl = str(statement)
                executed.append(ddl)
                parts = ddl.split()
                if parts[0] == "CREATE" and "INDEX" in parts[:3]:
                    table_name, raw_columns = ddl.split(" ON ", 1)[1].split("(", 1)
                    indexes[table_name].append({
                        "column_names": raw_columns.rstrip(")").split(","), "unique": False,
                    })
                elif parts[:2] == ["ALTER", "TABLE"] and "FOREIGN KEY" in ddl:
                    table_name = parts[2]
                    foreign_keys[table_name].append({
                        "constrained_columns": ddl.split("FOREIGN KEY (", 1)[1].split(")", 1)[0].split(","),
                        "referred_table": ddl.split("REFERENCES ", 1)[1].split("(", 1)[0],
                        "referred_columns": ddl.rsplit("(", 1)[1].rstrip(")").split(","),
                    })
                return Result()

        class Begin:
            def __enter__(self):
                return Connection()

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        class MySQLBind:
            dialect = mysql.dialect()

            def begin(self):
                return Begin()

        class Inspector:
            def has_table(self, table_name):
                return table_name in tables

            def get_columns(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{"name": column.name, "type": column.type, "nullable": column.nullable}
                        for column in table.columns]

            def get_indexes(self, table_name):
                return indexes[table_name]

            def get_pk_constraint(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return {"constrained_columns": [column.name for column in table.primary_key.columns]}

            def get_unique_constraints(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{"column_names": [column.name for column in constraint.columns]}
                        for constraint in table.constraints if isinstance(constraint, UniqueConstraint)]

            def get_foreign_keys(self, table_name):
                return foreign_keys[table_name]

        with patch.object(database, "inspect", return_value=Inspector()):
            database.ensure_runtime_schema_for(MySQLBind())
            first_run = list(executed)
            database.ensure_runtime_schema_for(MySQLBind())

        self.assertEqual(executed, first_run)
        expected_indexes = {
            "CREATE INDEX ix_training_task_records_claim_owner ON training_task_records(claim_owner)",
            "CREATE INDEX ix_training_task_records_claim_expires_at ON training_task_records(claim_expires_at)",
            "CREATE INDEX ix_mistake_records_attempt_item_id ON mistake_records(attempt_item_id)",
            "CREATE INDEX ix_mistake_records_question_version_id ON mistake_records(question_version_id)",
        }
        self.assertTrue(expected_indexes <= set(executed))
        expected_fks = {
            "ALTER TABLE mistake_records ADD CONSTRAINT fk_mistake_records_attempt_item_id FOREIGN KEY (attempt_item_id) REFERENCES learning_attempt_items(attempt_item_id)",
            "ALTER TABLE mistake_records ADD CONSTRAINT fk_mistake_records_question_version_id FOREIGN KEY (question_version_id) REFERENCES question_version_records(question_version_id)",
        }
        self.assertTrue(expected_fks <= set(executed))
        self.assertLess(
            executed.index("CREATE INDEX ix_mistake_records_attempt_item_id ON mistake_records(attempt_item_id)"),
            min(executed.index(ddl) for ddl in expected_fks),
        )

    def test_mysql_dependency_foreign_key_repair_rejects_orphans(self):
        class Connection:
            def execute(self, statement):
                if str(statement).startswith("SELECT COUNT(*) FROM mistake_records"):
                    return type("Result", (), {"scalar_one": lambda self: 1})()
                self.fail("foreign key DDL must not run when orphan rows exist")

        class Begin:
            def __enter__(self):
                return Connection()

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        class MySQLBind:
            dialect = mysql.dialect()

            def begin(self):
                return Begin()

        class Inspector:
            def has_table(self, table_name):
                return True

            def get_columns(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{"name": column.name, "type": column.type, "nullable": column.nullable}
                        for column in table.columns]

            def get_indexes(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{"column_names": [column.name for column in index.columns], "unique": index.unique}
                        for index in table.indexes]

            def get_pk_constraint(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return {"constrained_columns": [column.name for column in table.primary_key.columns]}

            def get_unique_constraints(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{"column_names": [column.name for column in constraint.columns]}
                        for constraint in table.constraints if isinstance(constraint, UniqueConstraint)]

            def get_foreign_keys(self, table_name):
                table = database.Base.metadata.tables[table_name]
                values = []
                for constraint in table.foreign_key_constraints:
                    local = [element.parent.name for element in constraint.elements]
                    if table_name == "mistake_records" and local == ["attempt_item_id"]:
                        continue
                    values.append({
                        "constrained_columns": local,
                        "referred_table": constraint.elements[0].column.table.name,
                        "referred_columns": [element.column.name for element in constraint.elements],
                    })
                return values

        with patch.object(database, "inspect", return_value=Inspector()):
            with self.assertRaisesRegex(RuntimeError, "^phase3_mysql_schema_migration_failed$"):
                database.ensure_runtime_schema_for(MySQLBind())

    def test_mysql_rejects_strict_not_null_phase_three_nullable_columns(self):
        class MySQLBind:
            dialect = mysql.dialect()

        class Inspector:
            def has_table(self, table_name):
                return table_name in {
                    "grading_result_records", "training_task_records", "mistake_records",
                    "variation_sets", "variation_question_versions", "variation_rubrics",
                }

            def get_columns(self, table_name):
                table = database.Base.metadata.tables[table_name]
                strict_nullable_columns = {
                    ("training_task_records", "claim_owner"),
                    ("mistake_records", "attempt_item_id"),
                }
                return [{
                    "name": column.name,
                    "type": column.type,
                    "nullable": False if (table_name, column.name) in strict_nullable_columns
                    else column.nullable,
                } for column in table.columns]

            def get_indexes(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{
                    "name": index.name,
                    "column_names": [column.name for column in index.columns],
                    "unique": index.unique,
                } for index in table.indexes]

            def get_pk_constraint(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return {"constrained_columns": [column.name for column in table.primary_key.columns]}

            def get_unique_constraints(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{"column_names": [column.name for column in constraint.columns]}
                        for constraint in table.constraints
                        if isinstance(constraint, UniqueConstraint)]

            def get_foreign_keys(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{
                    "constrained_columns": [element.parent.name for element in constraint.elements],
                    "referred_table": constraint.elements[0].column.table.name,
                    "referred_columns": [element.column.name for element in constraint.elements],
                } for constraint in table.foreign_key_constraints]

        with patch.object(database, "inspect", return_value=Inspector()):
            with self.assertRaisesRegex(RuntimeError, "^phase3_mysql_schema_migration_failed$"):
                database.ensure_runtime_schema_for(MySQLBind())

    def test_mysql_rejects_too_short_varchar_without_altering_column(self):
        class MySQLBind:
            dialect = mysql.dialect()

        class Inspector:
            def has_table(self, table_name):
                return table_name in {"variation_sets", "variation_question_versions", "variation_rubrics"}

            def get_columns(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{
                    "name": column.name,
                    "type": String(16) if table_name == "variation_sets" and column.name == "variation_set_id" else column.type,
                    "nullable": column.nullable,
                } for column in table.columns]

            def get_indexes(self, table_name):
                return []

            def get_pk_constraint(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return {"constrained_columns": [column.name for column in table.primary_key.columns]}

            def get_unique_constraints(self, table_name):
                return []

            def get_foreign_keys(self, table_name):
                return []

        with patch.object(database, "inspect", return_value=Inspector()):
            with self.assertRaisesRegex(RuntimeError, "^phase3_mysql_schema_migration_failed$"):
                database.ensure_runtime_schema_for(MySQLBind())

    def test_mysql_repairs_partial_phase_three_tables_once(self):
        executed = []
        tables = {
            "users", "mistake_records", "question_version_records",
            "audit_result_records", "learning_attempt_items",
            "grading_result_records", "training_task_records",
            "variation_sets", "variation_question_versions", "variation_rubrics",
        }
        columns = {
            name: {column.name for column in database.Base.metadata.tables[name].columns}
            for name in tables
        }
        columns["variation_sets"] -= {"status", "created_at"}
        columns["variation_question_versions"] -= {"scope", "created_at"}
        columns["variation_rubrics"] -= {"rubric_json", "created_at"}
        indexes = {
            name: [{
                "name": index.name,
                "column_names": [column.name for column in index.columns],
                "unique": index.unique,
            } for index in database.Base.metadata.tables[name].indexes
                if name not in database._MYSQL_PHASE_THREE_TABLES]
            for name in tables
        }
        unique_constraints = {
            name: [{"column_names": [column.name for column in constraint.columns]}
                   for constraint in database.Base.metadata.tables[name].constraints
                   if isinstance(constraint, UniqueConstraint)
                   and name not in database._MYSQL_PHASE_THREE_TABLES]
            for name in tables
        }
        foreign_keys = {name: [] for name in tables}

        class Connection:
            def execute(self, statement):
                ddl = str(statement)
                executed.append(ddl)
                parts = ddl.split()
                if parts[:2] == ["ALTER", "TABLE"] and "ADD COLUMN" in ddl:
                    columns[parts[2]].add(parts[5])
                elif parts[0] == "CREATE" and "INDEX" in parts[:3]:
                    index_position = parts.index("INDEX")
                    index_name, table_columns = parts[index_position + 1], ddl.split(" ON ", 1)[1]
                    table_name, raw_columns = table_columns.split("(", 1)
                    indexes[table_name].append({
                        "name": index_name,
                        "column_names": [item.strip() for item in raw_columns.rstrip(")").split(",")],
                        "unique": False,
                    })
                elif parts[:2] == ["ALTER", "TABLE"] and "FOREIGN KEY" in ddl:
                    table_name = parts[2]
                    constrained = ddl.split("FOREIGN KEY (", 1)[1].split(")", 1)[0].split(",")
                    referred_table = ddl.split("REFERENCES ", 1)[1].split("(", 1)[0]
                    referred = ddl.rsplit("(", 1)[1].rstrip(")").split(",")
                    foreign_keys[table_name].append({
                        "name": parts[5], "constrained_columns": constrained,
                        "referred_table": referred_table, "referred_columns": referred,
                    })
                elif parts[:2] == ["ALTER", "TABLE"] and "UNIQUE" in ddl:
                    table_name = parts[2]
                    unique_constraints[table_name].append({
                        "name": parts[5],
                        "column_names": ddl.rsplit("(", 1)[1].rstrip(")").split(","),
                    })
                elif ddl.startswith("SELECT COUNT(*)"):
                    return type("Result", (), {"scalar_one": lambda self: 0})()

        class Begin:
            def __enter__(self):
                return Connection()

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        class MySQLBind:
            dialect = mysql.dialect()

            def begin(self):
                return Begin()

        class Inspector:
            def has_table(self, table_name):
                return table_name in tables

            def get_columns(self, table_name):
                model = database.Base.metadata.tables[table_name]
                return [{
                    "name": name,
                    "type": model.columns[name].type,
                    "nullable": model.columns[name].nullable,
                } for name in columns[table_name]]

            def get_indexes(self, table_name):
                return indexes[table_name]

            def get_pk_constraint(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return {"constrained_columns": [column.name for column in table.primary_key.columns]}

            def get_unique_constraints(self, table_name):
                return unique_constraints[table_name]

            def get_foreign_keys(self, table_name):
                return foreign_keys[table_name]

        with patch.object(database, "inspect", return_value=Inspector()):
            database.ensure_runtime_schema_for(MySQLBind())
            first_run = list(executed)
            database.ensure_runtime_schema_for(MySQLBind())

        self.assertEqual(executed, first_run)
        self.assertTrue(any("variation_sets ADD COLUMN status" in ddl for ddl in executed))
        self.assertTrue(any("CREATE INDEX" in ddl and "variation_set_id" in ddl for ddl in executed))
        self.assertCountEqual(
            [ddl for ddl in executed if " ADD CONSTRAINT " in ddl and " UNIQUE " in ddl],
            [
                "ALTER TABLE grading_result_records ADD CONSTRAINT uq_grading_result_artifact_version UNIQUE (artifact_id,version)",
                "ALTER TABLE training_task_records ADD CONSTRAINT uq_training_task_records_task_id UNIQUE (task_id)",
                "ALTER TABLE variation_sets ADD CONSTRAINT uq_variation_sets_variation_set_id UNIQUE (variation_set_id)",
                "ALTER TABLE variation_question_versions ADD CONSTRAINT uq_variation_question_versions_question_version_id UNIQUE (question_version_id)",
                "ALTER TABLE variation_rubrics ADD CONSTRAINT uq_variation_rubrics_question_version_id UNIQUE (question_version_id)",
            ],
        )
        self.assertTrue(any("FOREIGN KEY" in ddl and "variation_sets" in ddl for ddl in executed))

    def test_mysql_phase_three_unique_repair_blocks_duplicate_values(self):
        class Result:
            def scalar_one(self):
                return 1

        class Connection:
            def execute(self, statement):
                if str(statement).startswith("SELECT COUNT(*) FROM ("):
                    return Result()
                self.fail("unique DDL must not run when duplicate values exist")

        class Begin:
            def __enter__(self):
                return Connection()

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        class MySQLBind:
            dialect = mysql.dialect()

            def begin(self):
                return Begin()

        class Inspector:
            def has_table(self, table_name):
                return table_name in {"variation_sets", "variation_question_versions", "variation_rubrics"}

            def get_columns(self, table_name):
                return [{"name": column.name, "type": column.type, "nullable": column.nullable}
                        for column in database.Base.metadata.tables[table_name].columns]

            def get_indexes(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{
                    "name": index.name,
                    "column_names": [column.name for column in index.columns],
                    "unique": False,
                } for index in table.indexes]

            def get_pk_constraint(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return {"constrained_columns": [column.name for column in table.primary_key.columns]}

            def get_unique_constraints(self, table_name):
                return []

            def get_foreign_keys(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{
                    "constrained_columns": [element.parent.name for element in constraint.elements],
                    "referred_table": constraint.elements[0].column.table.name,
                    "referred_columns": [element.column.name for element in constraint.elements],
                } for constraint in table.foreign_key_constraints]

        with patch.object(database, "inspect", return_value=Inspector()):
            with self.assertRaisesRegex(RuntimeError, "^phase3_mysql_schema_migration_failed$"):
                database.ensure_runtime_schema_for(MySQLBind())

    def test_mysql_phase_three_data_preflight_runs_before_any_planned_ddl(self):
        executed = []

        class Connection:
            def execute(self, statement):
                sql = str(statement)
                if sql.startswith("SELECT COUNT(*) FROM ("):
                    return type("Result", (), {"scalar_one": lambda self: 1})()
                executed.append(sql)

        class Begin:
            def __enter__(self):
                return Connection()

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        class MySQLBind:
            dialect = mysql.dialect()

            def begin(self):
                return Begin()

        class Inspector:
            def has_table(self, table_name):
                return table_name != "variation_sets"

            def get_columns(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [
                    {"name": column.name, "type": column.type, "nullable": column.nullable}
                    for column in table.columns
                ]

            def get_indexes(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{
                    "name": index.name,
                    "column_names": [column.name for column in index.columns],
                    "unique": False,
                } for index in table.indexes]

            def get_pk_constraint(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return {"constrained_columns": [column.name for column in table.primary_key.columns]}

            def get_unique_constraints(self, table_name):
                return []

            def get_foreign_keys(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{
                    "constrained_columns": [element.parent.name for element in constraint.elements],
                    "referred_table": constraint.elements[0].column.table.name,
                    "referred_columns": [element.column.name for element in constraint.elements],
                } for constraint in table.foreign_key_constraints]

        variation_sets = database.Base.metadata.tables["variation_sets"]
        with patch.object(database, "inspect", return_value=Inspector()), patch.object(
            variation_sets, "create", side_effect=lambda **kwargs: executed.append("CREATE variation_sets")
        ):
            with self.assertRaisesRegex(RuntimeError, "^phase3_mysql_schema_migration_failed$"):
                database.ensure_runtime_schema_for(MySQLBind())

        self.assertEqual(executed, [])

    def test_real_mysql_inspector_missing_constraint_capability_fails_closed(self):
        executed = []

        class Connection:
            def execute(self, statement):
                executed.append(str(statement))

        class Begin:
            def __enter__(self):
                return Connection()

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        class MySQLBind:
            dialect = mysql.dialect()

            def begin(self):
                return Begin()

        class Inspector:
            def has_table(self, table_name):
                return table_name in {
                    "grading_result_records", "training_task_records", "mistake_records",
                    "variation_sets", "variation_question_versions", "variation_rubrics",
                }

            def get_columns(self, table_name):
                if table_name in {
                        "grading_result_records", "training_task_records", "mistake_records"}:
                    return [{"name": "id", "type": Integer(), "nullable": False}]
                return [{"name": column.name, "type": column.type, "nullable": column.nullable}
                        for column in database.Base.metadata.tables[table_name].columns]

            def get_indexes(self, table_name):
                return []

            def get_pk_constraint(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return {"constrained_columns": [column.name for column in table.primary_key.columns]}

            def get_unique_constraints(self, table_name):
                raise NotImplementedError("unique constraint reflection unavailable")

            def get_foreign_keys(self, table_name):
                raise NotImplementedError("foreign key reflection unavailable")

        with patch.object(database, "inspect", return_value=Inspector()):
            with self.assertRaisesRegex(RuntimeError, "^phase3_mysql_schema_migration_failed$"):
                database.ensure_runtime_schema_for(MySQLBind())
        self.assertEqual(executed, [])

    def test_mysql_partial_phase_three_schema_fails_closed_on_incompatible_column(self):
        class MySQLBind:
            dialect = mysql.dialect()

        class Inspector:
            def has_table(self, table_name):
                return table_name in {
                    "variation_sets", "variation_question_versions", "variation_rubrics",
                }

            def get_columns(self, table_name):
                if table_name == "variation_sets":
                    return [{"name": "variation_set_id", "type": Integer(), "nullable": False}]
                return [{
                    "name": column.name, "type": column.type, "nullable": column.nullable,
                } for column in database.Base.metadata.tables[table_name].columns]

            def get_indexes(self, table_name):
                return []

            def get_pk_constraint(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return {"constrained_columns": [column.name for column in table.primary_key.columns]}

            def get_unique_constraints(self, table_name):
                return []

            def get_foreign_keys(self, table_name):
                return []

        with patch.object(database, "inspect", return_value=Inspector()):
            with self.assertRaisesRegex(RuntimeError, "phase3_mysql_schema_migration_failed"):
                database.ensure_runtime_schema_for(MySQLBind())

    def test_mysql_phase_three_ddl_failure_uses_stable_migration_error(self):
        class Connection:
            def execute(self, statement):
                raise ValueError("sensitive historical row")

        class Begin:
            def __enter__(self):
                return Connection()

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        class MySQLBind:
            dialect = mysql.dialect()

            def begin(self):
                return Begin()

        class Inspector:
            def has_table(self, table_name):
                return table_name in {
                    "variation_sets", "variation_question_versions", "variation_rubrics",
                }

            def get_columns(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return [{
                    "name": column.name, "type": column.type, "nullable": column.nullable,
                } for column in table.columns if column.name != "created_at"]

            def get_indexes(self, table_name):
                return []

            def get_pk_constraint(self, table_name):
                table = database.Base.metadata.tables[table_name]
                return {"constrained_columns": [column.name for column in table.primary_key.columns]}

            def get_unique_constraints(self, table_name):
                return []

            def get_foreign_keys(self, table_name):
                return []

        with patch.object(database, "inspect", return_value=Inspector()):
            with self.assertRaisesRegex(
                RuntimeError, "^phase3_mysql_schema_migration_failed$"
            ) as raised:
                database.ensure_runtime_schema_for(MySQLBind())
        self.assertNotIn("sensitive", str(raised.exception))

    def test_switched_ledger_blocks_runtime_schema_updates(self):
        engine = self.make_engine()
        try:
            database.ensure_runtime_schema_for(engine)
            with engine.begin() as connection:
                connection.execute(text("""
                    INSERT INTO runtime_schema_migrations (
                        migration_id, target_version, status, current_step,
                        attempt_count, controlled_objects_json, source_manifest_json,
                        verification_summary_json, started_at
                    ) VALUES (
                        'authoritative_learning_records_v2', 'v2',
                        'switched', 'switch', 1, '[]', '{}', '{}',
                        CURRENT_TIMESTAMP
                    )
                """))

            with self.assertRaisesRegex(
                RuntimeError, "^authoritative_learning_schema_recovery_failed$"
            ):
                database.ensure_runtime_schema_for(engine)
        finally:
            engine.dispose()

        engine = self.make_engine()
        try:
            database.ensure_runtime_schema_for(engine)
            with engine.begin() as connection:
                connection.execute(text("""
                    INSERT INTO runtime_schema_migrations (
                        migration_id, target_version, status, current_step,
                        attempt_count, controlled_objects_json, source_manifest_json,
                        verification_summary_json, started_at
                    ) VALUES (
                        'authoritative_learning_records_v2', 'v2',
                        'recovery_failed', 'recover', 1, '[]', '{}', '{}',
                        CURRENT_TIMESTAMP
                    )
                """))

            with self.assertRaisesRegex(
                RuntimeError, "^authoritative_learning_schema_recovery_failed$"
            ):
                database.ensure_runtime_schema_for(engine)
        finally:
            engine.dispose()

    def recovery_snapshot(
        self,
        *,
        ledger_status="switching",
        current_valid=False,
        shadow_valid=False,
        backup_valid=False,
        invalidate_current=None,
        invalidate_shadow=None,
        invalidate_backup=None,
        unexpected_controlled_objects=(),
    ):
        names = tuple(database._AUTHORITATIVE_LEARNING_TABLES)

        def candidate(role, valid, invalidation):
            values = {
                "role": role,
                "expected_names": names,
                "discovered_names": names if valid else (),
                "is_complete": valid,
                "objects_are_tables": valid,
                "ledger_owned": True,
                "has_extra_objects": False,
                "manifest_matches_source": valid,
                "physical_v2_matches": valid,
                "foreign_key_violations": (),
            }
            if invalidation == "manifest":
                values["discovered_names"] = names
                values["manifest_matches_source"] = False
            elif invalidation == "physical_v2":
                values["discovered_names"] = names
                values["physical_v2_matches"] = False
            elif invalidation == "foreign_keys":
                values["discovered_names"] = names
                values["foreign_key_violations"] = (("learning_attempts", 1, "users", 0),)
            elif invalidation == "ownership":
                values["discovered_names"] = names
                values["ledger_owned"] = False
            elif invalidation == "missing_table":
                values["is_complete"] = False
                values["discovered_names"] = names[:-1]
            elif invalidation == "view":
                values["discovered_names"] = names
                values["objects_are_tables"] = False
            elif invalidation == "extra_object":
                values["discovered_names"] = names
                values["has_extra_objects"] = True
            return database.RecoveryCandidateSnapshot(**values)

        return database.RecoverySnapshot(
            ledger_status=ledger_status,
            current=candidate("current", current_valid, invalidate_current),
            shadow=candidate("shadow", shadow_valid, invalidate_shadow),
            backup=candidate("backup", backup_valid, invalidate_backup),
            unexpected_controlled_objects=unexpected_controlled_objects,
        )

    def test_recovery_snapshot_is_immutable(self):
        snapshot = self.recovery_snapshot(current_valid=True)

        with self.assertRaises(FrozenInstanceError):
            snapshot.ledger_status = "verified"
        with self.assertRaises(AttributeError):
            snapshot.current.discovered_names += ("extra",)

    def test_recovery_decision_has_only_approved_actions(self):
        actions = {
            database.decide_sqlite_recovery(self.recovery_snapshot(current_valid=True)).action,
            database.decide_sqlite_recovery(self.recovery_snapshot(shadow_valid=True)).action,
            database.decide_sqlite_recovery(self.recovery_snapshot(backup_valid=True)).action,
            database.decide_sqlite_recovery(self.recovery_snapshot()).action,
        }

        self.assertTrue(actions <= {"finalize_current", "perform_switch", "restore_backup", "fail_closed"})

    def test_current_requires_manifest_v2_fk_and_ownership(self):
        for override in ("manifest", "physical_v2", "foreign_keys", "ownership"):
            with self.subTest(override=override):
                self.assertEqual(
                    database.decide_sqlite_recovery(self.recovery_snapshot(
                        current_valid=True, invalidate_current=override,
                    )).action,
                    "fail_closed",
                )

    def test_full_shadow_without_backup_performs_switch(self):
        self.assertEqual(
            database.decide_sqlite_recovery(self.recovery_snapshot(shadow_valid=True)).action,
            "perform_switch",
        )

    def test_invalid_shadow_fails_closed(self):
        for override in ("missing_table", "view", "extra_object", "foreign_keys", "manifest"):
            with self.subTest(override=override):
                self.assertEqual(
                    database.decide_sqlite_recovery(self.recovery_snapshot(
                        shadow_valid=True, invalidate_shadow=override,
                    )).action,
                    "fail_closed",
                )

    def test_valid_backup_restores_only_when_current_cannot_finalize(self):
        self.assertEqual(
            database.decide_sqlite_recovery(self.recovery_snapshot(
                ledger_status="switched", current_valid=True, backup_valid=True,
            )).action,
            "finalize_current",
        )
        self.assertEqual(
            database.decide_sqlite_recovery(self.recovery_snapshot(backup_valid=True)).action,
            "restore_backup",
        )

    def test_untrusted_backup_fails_closed(self):
        self.assertEqual(
            database.decide_sqlite_recovery(self.recovery_snapshot(
                backup_valid=True, invalidate_backup="extra_object",
            )).action,
            "fail_closed",
        )

    def test_recovery_snapshots_reject_mutable_collection_inputs(self):
        names = list(database._AUTHORITATIVE_LEARNING_TABLES)
        with self.assertRaises(TypeError):
            database.RecoveryCandidateSnapshot(
                "current", names, (), False, False, True, False, False, False, (),
            )
        with self.assertRaises(TypeError):
            database.RecoveryCandidateSnapshot(
                "current", (), (), False, False, True, False, False, False,
                [["learning_attempts", 1, "users", 0]],
            )
        candidate = database.RecoveryCandidateSnapshot(
            "current", (), (), False, False, True, False, False, False, (),
        )
        with self.assertRaises(TypeError):
            database.RecoverySnapshot("switching", candidate, candidate, candidate, [("x", "table")])

    def test_recovery_snapshots_reject_mutable_values_nested_in_tuples(self):
        nested_violation = ((["learning_attempts", 1, "users", 0],),)
        with self.assertRaises(TypeError):
            database.RecoveryCandidateSnapshot(
                "current", (), (), False, False, True, False, False, False,
                nested_violation,
            )
        candidate = database.RecoveryCandidateSnapshot(
            "current", (), (), False, False, True, False, False, False, (),
        )
        nested_object = ((["unexpected", "table"],),)
        with self.assertRaises(TypeError):
            database.RecoverySnapshot("switching", candidate, candidate, candidate, nested_object)

    def test_only_switched_finalizes_valid_current_with_backup(self):
        for status in ("prepared", "staged", "switching"):
            with self.subTest(status=status):
                self.assertEqual(
                    database.decide_sqlite_recovery(self.recovery_snapshot(
                        ledger_status=status, current_valid=True,
                    )).action,
                    "fail_closed",
                )
        self.assertEqual(
            database.decide_sqlite_recovery(self.recovery_snapshot(
                ledger_status="switched", current_valid=True, backup_valid=True,
            )).action,
            "finalize_current",
        )

    def test_switched_finalizes_v2_current_with_v1_backup(self):
        snapshot = self.recovery_snapshot(
            ledger_status="switched", current_valid=True, backup_valid=True,
        )
        v1_backup = database.RecoveryCandidateSnapshot(
            role="backup",
            expected_names=snapshot.backup.expected_names,
            discovered_names=snapshot.backup.discovered_names,
            is_complete=True,
            objects_are_tables=True,
            ledger_owned=True,
            has_extra_objects=False,
            manifest_matches_source=True,
            physical_v2_matches=False,
            foreign_key_violations=(),
        )
        self.assertEqual(
            database.decide_sqlite_recovery(database.RecoverySnapshot(
                "switched", snapshot.current, snapshot.shadow, v1_backup, (),
            )).action,
            "finalize_current",
        )

    def test_restore_backup_requires_intermediate_recovery_status(self):
        for status in ("verified", "prepared", "staged", "unknown"):
            with self.subTest(status=status):
                self.assertEqual(
                    database.decide_sqlite_recovery(self.recovery_snapshot(
                        ledger_status=status, backup_valid=True,
                    )).action,
                    "fail_closed",
                )
        for status in ("switching", "switched"):
            with self.subTest(status=status):
                self.assertEqual(
                    database.decide_sqlite_recovery(self.recovery_snapshot(
                        ledger_status=status, backup_valid=True,
                    )).action,
                    "restore_backup",
                )

    def test_absent_candidates_cannot_be_selected(self):
        snapshot = self.recovery_snapshot(
            ledger_status="switching", current_valid=True, shadow_valid=True,
        )
        absent_shadow = database.RecoveryCandidateSnapshot(
            "shadow", snapshot.shadow.expected_names, (), True, True, True, False, True, True, (),
        )
        self.assertFalse(absent_shadow.is_complete)
        self.assertFalse(absent_shadow.objects_are_tables)
        self.assertFalse(absent_shadow.manifest_matches_source)
        self.assertFalse(absent_shadow.physical_v2_matches)
        self.assertEqual(
            database.decide_sqlite_recovery(database.RecoverySnapshot(
                "switching", snapshot.current, absent_shadow, snapshot.backup, (),
            )).action,
            "fail_closed",
        )

        snapshot = self.recovery_snapshot(ledger_status="switching", backup_valid=True)
        absent_backup = database.RecoveryCandidateSnapshot(
            "backup", snapshot.backup.expected_names, (), True, True, True, False, True, False, (),
        )
        self.assertEqual(
            database.decide_sqlite_recovery(database.RecoverySnapshot(
                "switching", snapshot.current, snapshot.shadow, absent_backup, (),
            )).action,
            "fail_closed",
        )

        snapshot = self.recovery_snapshot(ledger_status="switched", backup_valid=True)
        absent_current = database.RecoveryCandidateSnapshot(
            "current", snapshot.current.expected_names, (), True, True, True, False, True, True, (),
        )
        self.assertEqual(
            database.decide_sqlite_recovery(database.RecoverySnapshot(
                "switched", absent_current, snapshot.shadow, snapshot.backup, (),
            )).action,
            "restore_backup",
        )

    def test_recovery_snapshots_reject_custom_nested_values(self):
        class MutableEvidence:
            pass

        with self.assertRaises(TypeError):
            database.RecoveryCandidateSnapshot(
                "current", (), (), False, False, True, False, False, False,
                ((MutableEvidence(),),),
            )

    def test_recovery_snapshots_reject_invalid_scalar_and_candidate_fields(self):
        class Truthy:
            def __bool__(self):
                return True

        candidate_args = (
            (), (), False, False, True, False, False, False, (),
        )
        with self.assertRaises(TypeError):
            database.RecoveryCandidateSnapshot([], *candidate_args)
        with self.assertRaises(TypeError):
            database.RecoveryCandidateSnapshot(
                "current", (), ("learning_attempts",), Truthy(), False, True, False, False, False, (),
            )

        candidate = database.RecoveryCandidateSnapshot("current", *candidate_args)
        with self.assertRaises(TypeError):
            database.RecoverySnapshot([], candidate, candidate, candidate, ())
        with self.assertRaises(TypeError):
            database.RecoverySnapshot("switching", candidate, candidate, object(), ())

    def test_recovery_decisions_reject_invalid_runtime_values(self):
        with self.assertRaises(TypeError):
            database.RecoveryDecision([])
        with self.assertRaises(ValueError):
            database.RecoveryDecision("continue_current")
        with self.assertRaises(TypeError):
            database.RecoveryDecision("fail_closed", {})
        self.assertEqual(database.RecoveryDecision("fail_closed", "reason").action, "fail_closed")

    def test_partial_current_blocks_valid_shadow_switch(self):
        self.assertEqual(
            database.decide_sqlite_recovery(self.recovery_snapshot(
                shadow_valid=True, invalidate_current="missing_table",
            )).action,
            "fail_closed",
        )

    def test_invalid_shadow_blocks_valid_backup_restore(self):
        for override in ("missing_table", "view", "physical_v2", "foreign_keys"):
            with self.subTest(override=override):
                self.assertEqual(
                    database.decide_sqlite_recovery(self.recovery_snapshot(
                        backup_valid=True, invalidate_shadow=override,
                    )).action,
                    "fail_closed",
                )

    def test_restore_backup_requires_no_shadow_objects(self):
        snapshot = self.recovery_snapshot(backup_valid=True)
        shadow = database.RecoveryCandidateSnapshot(
            role="shadow",
            expected_names=snapshot.shadow.expected_names,
            discovered_names=(snapshot.shadow.expected_names[0],),
            is_complete=False,
            objects_are_tables=True,
            ledger_owned=True,
            has_extra_objects=False,
            manifest_matches_source=False,
            physical_v2_matches=False,
            foreign_key_violations=(),
        )
        self.assertEqual(
            database.decide_sqlite_recovery(database.RecoverySnapshot(
                snapshot.ledger_status, snapshot.current, shadow, snapshot.backup,
                snapshot.unexpected_controlled_objects,
            )).action,
            "fail_closed",
        )
    # Historical RED note: these production-boundary cases were absent before 885c201.
    # They exercise the committed checkpoint path on file-backed SQLite engines only.
    def _switching_engine(self, checkpoint="switching_committed_before_begin"):
        engine = self.make_file_engine()
        self.enable_foreign_keys(engine)
        self.create_v1_authoritative_fixture(engine)
        with self.assertRaises(InterruptedMigration):
            database.ensure_runtime_schema_for(engine, checkpoint=self.stop_after(checkpoint))
        with engine.begin() as connection:
            migration = database._load_authoritative_learning_migration(connection)
        return engine, migration

    def test_snapshot_collects_all_candidates_once(self):
        engine, migration = self._switching_engine()
        catalog_queries = []

        def record_catalog_query(connection, cursor, statement, parameters, context, executemany):
            if ("SELECT name, type FROM sqlite_master" in statement
                    and "type IN ('table', 'view')" in statement):
                catalog_queries.append(statement)

        event.listen(engine, "before_cursor_execute", record_catalog_query)
        try:
            with engine.begin() as connection:
                snapshot = database.build_sqlite_recovery_snapshot(connection, migration)
        finally:
            event.remove(engine, "before_cursor_execute", record_catalog_query)
        self.assertEqual(len(catalog_queries), 1)
        self.assertEqual(snapshot.ledger_status, "switching")

    def test_snapshot_constructs_real_checkpoint_candidate_evidence(self):
        checkpoints = {
            "prepared": "prepared_committed",
            "staged": "staged_committed",
            "switching": "switching_committed_before_begin",
            "switched": "switched_committed_before_verify",
        }
        for expected_status, checkpoint in checkpoints.items():
            with self.subTest(status=expected_status):
                engine, migration = self._switching_engine(checkpoint)
                with engine.begin() as connection:
                    snapshot = database.build_sqlite_recovery_snapshot(connection, migration)
                self.assertEqual(snapshot.ledger_status, expected_status)
                self.assertTrue(snapshot.current.is_complete)
                self.assertTrue(snapshot.current.objects_are_tables)
                if expected_status == "prepared":
                    self.assertFalse(snapshot.shadow.discovered_names)
                    self.assertFalse(snapshot.backup.discovered_names)
                elif expected_status in {"staged", "switching"}:
                    self.assertTrue(snapshot.shadow.is_complete)
                    self.assertTrue(snapshot.shadow.physical_v2_matches)
                    self.assertFalse(snapshot.backup.discovered_names)
                else:
                    self.assertTrue(snapshot.current.physical_v2_matches)
                    self.assertTrue(snapshot.backup.is_complete)
                    self.assertTrue(snapshot.backup.manifest_matches_source)
                    self.assertFalse(snapshot.backup.physical_v2_matches)

    def test_snapshot_marks_physical_shadow_defects_not_v2(self):
        for defect, statement in (
            ("missing_index", 'DROP INDEX "ix_learning_attempts_request_id__authoritative_learning_records_v2__shadow"'),
            ("wrong_type", 'ALTER TABLE "learning_attempts__authoritative_learning_records_v2__shadow" RENAME COLUMN attempt_type TO attempt_type_old'),
            ("missing_fk", None),
        ):
            with self.subTest(defect=defect):
                engine, migration = self._switching_engine("staged_committed")
                shadow_attempts = database.controlled_sqlite_name("learning_attempts", "shadow")
                with engine.begin() as connection:
                    if defect == "wrong_type":
                        connection.execute(text(statement))
                        connection.execute(text(
                            f'ALTER TABLE "{shadow_attempts}" ADD COLUMN attempt_type INTEGER'
                        ))
                    elif defect == "missing_fk":
                        connection.execute(text(f'ALTER TABLE "{shadow_attempts}" RENAME TO "bad_shadow"'))
                        connection.execute(text(f'''CREATE TABLE "{shadow_attempts}" (
                            id INTEGER NOT NULL PRIMARY KEY, attempt_id VARCHAR(120) NOT NULL UNIQUE,
                            learner_id INTEGER NOT NULL, attempt_type VARCHAR(50), source_task_id VARCHAR(120),
                            request_id VARCHAR(120), status VARCHAR(50), submitted_at DATETIME,
                            source_kind VARCHAR(80), schema_version VARCHAR(40), created_at DATETIME
                        )'''))
                        connection.execute(text(f'INSERT INTO "{shadow_attempts}" SELECT * FROM "bad_shadow"'))
                    else:
                        connection.execute(text(statement))
                    snapshot = database.build_sqlite_recovery_snapshot(connection, migration)
                self.assertFalse(snapshot.shadow.physical_v2_matches)

    def test_snapshot_keeps_foreign_key_evidence_local_to_shadow_candidate(self):
        engine, migration = self._switching_engine("staged_committed")
        shadow_attempts = database.controlled_sqlite_name("learning_attempts", "shadow")
        with engine.connect() as connection:
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
            connection.execute(text(
                f'UPDATE "{shadow_attempts}" SET learner_id = -1 WHERE id = 1'
            ))
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys = ON")
            snapshot = database.build_sqlite_recovery_snapshot(connection, migration)
        self.assertFalse(snapshot.current.foreign_key_violations)
        self.assertTrue(snapshot.shadow.foreign_key_violations)
        self.assertFalse(snapshot.backup.foreign_key_violations)

    def test_snapshot_marks_partial_or_unlisted_objects_untrusted(self):
        engine, migration = self._switching_engine()
        extra = database.controlled_sqlite_name("learning_attempts", "backup")
        with engine.begin() as connection:
            connection.execute(text(f'CREATE TABLE "{extra}" (id INTEGER PRIMARY KEY)'))
            snapshot = database.build_sqlite_recovery_snapshot(connection, migration)
        self.assertTrue(
            snapshot.unexpected_controlled_objects or snapshot.backup.has_extra_objects
        )
        self.assertEqual(database.decide_sqlite_recovery(snapshot).action, "fail_closed")

    def test_switch_failure_persists_stable_contract_and_restores_pragma(self):
        engine, migration = self._switching_engine()
        observed = []

        def fail_rename(raw_connection, statements, ledger_id, status):
            observed.append(raw_connection.execute("PRAGMA foreign_keys").fetchone()[0])
            raise RuntimeError("raw rename failure must not escape")

        with patch.object(database, "_run_sqlite_rename_transaction", side_effect=fail_rename):
            with self.assertRaisesRegex(RuntimeError, "^authoritative_learning_schema_recovery_failed$"):
                database.switch_authoritative_learning_schema_for_sqlite(engine, migration)
        with engine.begin() as connection:
            row = connection.execute(text(
                "SELECT status, failure_reason FROM runtime_schema_migrations "
                "WHERE migration_id = :migration_id"
            ), {"migration_id": database.AUTHORITATIVE_LEARNING_MIGRATION_ID}).one()
            pragma = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
        self.assertEqual(observed, [0])
        self.assertEqual(row.status, "recovery_failed")
        self.assertEqual(row.failure_reason, "sqlite_switch_failed")
        self.assertEqual(pragma, 1)

    def test_begin_failure_uses_stable_error_and_restores_pragma(self):
        engine, migration = self._switching_engine()
        with patch.object(
            database, "_run_sqlite_rename_transaction",
            side_effect=sqlite3.OperationalError("begin"),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "^authoritative_learning_schema_recovery_failed$"
            ):
                database.switch_authoritative_learning_schema_for_sqlite(engine, migration)
        with engine.begin() as connection:
            self.assertEqual(connection.execute(text("PRAGMA foreign_keys")).scalar_one(), 1)
        path = engine.url.database
        engine.dispose()
        restarted = create_engine(f"sqlite:///{path}")
        self.addCleanup(restarted.dispose)
        self.assertEqual(self.migration_status(restarted), "recovery_failed")

    def test_recovery_action_close_failure_preserves_original_reason_and_contract(self):
        engine, migration = self._switching_engine()
        connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")

        class ClosingConnection:
            def __init__(self, wrapped):
                self.wrapped = wrapped
                self.connection = wrapped.connection

            def execution_options(self, **_options):
                return self

            def close(self):
                self.wrapped.close()
                raise RuntimeError("pool return failed")

        class ClosingBind:
            def connect(self):
                return ClosingConnection(connection)

        with patch.object(database, "_record_recovery_failure") as record:
            with self.assertRaisesRegex(
                RuntimeError, "^authoritative_learning_schema_recovery_failed$"
            ):
                database.run_recovery_action(
                    ClosingBind(), migration, action_name="sqlite_switch",
                    requires_foreign_keys_off=False,
                    operation=lambda _raw: (_ for _ in ()).throw(
                        database.RecoveryActionError("begin_immediate_failed")
                    ),
                )
        record.assert_called_once_with(ANY, migration, "begin_immediate_failed")
        path = engine.url.database
        engine.dispose()
        restarted = create_engine(f"sqlite:///{path}")
        self.addCleanup(restarted.dispose)
        self.assertEqual(self.migration_status(restarted), "switching")

    def test_recovery_action_close_only_failure_uses_controlled_reason(self):
        engine, migration = self._switching_engine()
        connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")

        class ClosingConnection:
            def __init__(self, wrapped):
                self.connection = wrapped.connection
                self.wrapped = wrapped

            def execution_options(self, **_options):
                return self

            def close(self):
                self.wrapped.close()
                raise RuntimeError("pool return failed")

        bind = type("Bind", (), {"connect": lambda _self: ClosingConnection(connection)})()
        with patch.object(database, "_record_recovery_failure") as record:
            with self.assertRaisesRegex(
                RuntimeError, "^authoritative_learning_schema_recovery_failed$"
            ):
                database.run_recovery_action(
                    bind, migration, action_name="sqlite_switch",
                    requires_foreign_keys_off=False, operation=lambda _raw: None,
                )
        record.assert_called_once_with(ANY, migration, "connection_close_failed")

    def _switch_rename_statements(self, controlled):
        return [
            f'ALTER TABLE "{table_name}" RENAME TO "{controlled[table_name]["backup"]}"'
            for table_name in reversed(database._AUTHORITATIVE_LEARNING_TABLES)
        ] + [
            f'ALTER TABLE "{controlled[table_name]["shadow"]}" RENAME TO "{table_name}"'
            for table_name in database._AUTHORITATIVE_LEARNING_TABLES
        ]

    def _restore_rename_statements(self, controlled):
        return [
            f'ALTER TABLE "{table_name}" RENAME TO "{controlled[table_name]["shadow"]}"'
            for table_name in reversed(database._AUTHORITATIVE_LEARNING_TABLES)
        ] + [
            f'ALTER TABLE "{controlled[table_name]["backup"]}" RENAME TO "{table_name}"'
            for table_name in database._AUTHORITATIVE_LEARNING_TABLES
        ]

    def _assert_filebacked_transaction_rollback(self, checkpoint, reason_code, *, status, statements, engine, migration):
        controlled = database._controlled_objects(migration)
        with engine.begin() as connection:
            before_names = set(connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )).scalars())
            before_ledger_status = connection.execute(text(
                "SELECT status FROM runtime_schema_migrations WHERE id = :id"
            ), {"id": migration.id}).scalar_one()

        def fail(stage):
            if stage == checkpoint:
                raise sqlite3.OperationalError(stage)

        with engine.connect() as connection:
            raw = connection.connection.driver_connection
            with self.assertRaises(database.RecoveryActionError) as raised:
                database._run_sqlite_rename_transaction(
                    raw, statements(controlled), migration.id, status, fault_hook=fail
                )
        self.assertEqual(raised.exception.reason_code, reason_code)
        path = engine.url.database
        engine.dispose()
        restarted = create_engine(f"sqlite:///{path}")
        self.addCleanup(restarted.dispose)
        with restarted.begin() as connection:
            names = set(connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )).scalars())
            ledger_status = connection.execute(text(
                "SELECT status FROM runtime_schema_migrations WHERE id = :id"
            ), {"id": migration.id}).scalar_one()
        self.assertEqual(ledger_status, before_ledger_status)
        self.assertEqual(names, before_names)

    def test_actual_switch_begin_failure_is_atomic_on_fresh_engine(self):
        engine, migration = self._switching_engine()
        self._assert_filebacked_transaction_rollback(
            "before_begin_immediate", "begin_immediate_failed", status="switched",
            statements=self._switch_rename_statements, engine=engine, migration=migration,
        )

    def test_actual_switch_rename_ledger_and_commit_failures_are_atomic_on_fresh_engine(self):
        for checkpoint, reason_code in (
            ("after_rename_0", "switch_rename_failed"),
            ("before_ledger_update", "ledger_transition_failed"),
            ("before_commit", "sqlite_action_commit_failed"),
        ):
            with self.subTest(checkpoint=checkpoint):
                engine, migration = self._switching_engine()
                self._assert_filebacked_transaction_rollback(
                    checkpoint, reason_code, status="switched", statements=self._switch_rename_statements,
                    engine=engine, migration=migration,
                )

    def test_actual_restore_begin_failure_is_atomic_on_fresh_engine(self):
        engine, migration = self._switching_engine("switched_committed_before_verify")
        self._assert_filebacked_transaction_rollback(
            "before_begin_immediate", "begin_immediate_failed", status="prepared",
            statements=self._restore_rename_statements, engine=engine, migration=migration,
        )

    def test_actual_restore_rename_ledger_and_commit_failures_are_atomic_on_fresh_engine(self):
        for checkpoint, reason_code in (
            ("after_rename_0", "restore_rename_failed"),
            ("before_ledger_update", "ledger_transition_failed"),
            ("before_commit", "sqlite_action_commit_failed"),
        ):
            with self.subTest(checkpoint=checkpoint):
                engine, migration = self._switching_engine("switched_committed_before_verify")
                self._assert_filebacked_transaction_rollback(
                    checkpoint, reason_code, status="prepared", statements=self._restore_rename_statements,
                    engine=engine, migration=migration,
                )

    def test_recovery_action_failure_record_write_never_leaks(self):
        engine, migration = self._switching_engine()
        with patch.object(database, "_record_recovery_failure", side_effect=RuntimeError("record")):
            with self.assertRaisesRegex(
                RuntimeError, "^authoritative_learning_schema_recovery_failed$"
            ):
                database.run_recovery_action(
                    engine, migration, action_name="sqlite_switch",
                    requires_foreign_keys_off=False,
                    operation=lambda _raw: (_ for _ in ()).throw(RuntimeError("operation")),
                )

    def test_recovery_action_pragma_failures_use_stable_error_and_restore_when_possible(self):
        engine, migration = self._switching_engine()

        class RawProxy:
            def __init__(self, raw, fail_on):
                self.raw = raw
                self.fail_on = fail_on
                self.calls = []

            def execute(self, statement):
                self.calls.append(statement)
                if statement == self.fail_on:
                    raise sqlite3.OperationalError("pragma")
                return self.raw.execute(statement)

        class ConnectionProxy:
            def __init__(self, connection, raw):
                self.connection = type("Driver", (), {"driver_connection": raw})()
                self.wrapped = connection

            def execution_options(self, **_options):
                return self

            def close(self):
                self.wrapped.close()

        for fail_on in ("PRAGMA foreign_keys", "PRAGMA foreign_keys = OFF"):
            with self.subTest(fail_on=fail_on):
                connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
                raw = RawProxy(connection.connection.driver_connection, fail_on)
                bind = type("Bind", (), {"connect": lambda _self: ConnectionProxy(connection, raw)})()
                with self.assertRaisesRegex(
                    RuntimeError, "^authoritative_learning_schema_recovery_failed$"
                ):
                    database.run_recovery_action(
                        bind, migration, action_name="sqlite_switch",
                        requires_foreign_keys_off=True, operation=lambda _raw: None,
                    )

    def test_recovery_action_pragma_restore_failure_uses_stable_error(self):
        engine, migration = self._switching_engine()
        connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")

        class RawProxy:
            def __init__(self, raw):
                self.raw = raw
                self.restore_attempts = 0

            def execute(self, statement):
                if statement == "PRAGMA foreign_keys = 1":
                    self.restore_attempts += 1
                    raise sqlite3.OperationalError("restore")
                return self.raw.execute(statement)

        raw = RawProxy(connection.connection.driver_connection)

        class ConnectionProxy:
            def __init__(self):
                self.connection = type("Driver", (), {"driver_connection": raw})()

            def execution_options(self, **_options):
                return self

            def close(self):
                connection.close()

        bind = type("Bind", (), {"connect": lambda _self: ConnectionProxy()})()
        with self.assertRaisesRegex(RuntimeError, "^authoritative_learning_schema_recovery_failed$"):
            database.run_recovery_action(
                bind, migration, action_name="sqlite_switch",
                requires_foreign_keys_off=True, operation=lambda _raw: None,
            )
        self.assertEqual(raw.restore_attempts, 1)

    def _reopen_and_assert_atomic_recovery_state(self, engine, expected_status):
        path = engine.url.database
        engine.dispose()
        restarted = create_engine(f"sqlite:///{path}")
        self.addCleanup(restarted.dispose)
        self.assertEqual(self.migration_status(restarted), expected_status)
        with restarted.begin() as connection:
            return set(connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )).scalars())

    def test_recovery_action_filebacked_wrapper_faults_persist_or_preserve_ledger(self):
        for fault in ("pragma_read", "pragma_disable", "pragma_restore", "close", "failure_writer"):
            with self.subTest(fault=fault):
                engine, migration = self._switching_engine()
                with engine.begin() as connection:
                    before_names = set(connection.execute(text(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )).scalars())
                connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
                raw_connection = connection.connection.driver_connection

                class RawProxy:
                    def execute(self, statement):
                        if fault == "pragma_read" and statement == "PRAGMA foreign_keys":
                            raise sqlite3.OperationalError("read")
                        if fault == "pragma_disable" and statement == "PRAGMA foreign_keys = OFF":
                            raise sqlite3.OperationalError("disable")
                        if fault == "pragma_restore" and statement == "PRAGMA foreign_keys = 1":
                            raise sqlite3.OperationalError("restore")
                        return raw_connection.execute(statement)

                raw = RawProxy()

                class ConnectionProxy:
                    def __init__(self):
                        self.connection = type("Driver", (), {"driver_connection": raw})()

                    def execution_options(self, **_options):
                        return self

                    def close(self):
                        connection.close()
                        if fault == "close":
                            raise RuntimeError("close")

                bind = type("Bind", (), {
                    "connect": lambda _self: ConnectionProxy(),
                    "begin": lambda _self: engine.begin(),
                })()
                operation = lambda _raw: None
                if fault == "failure_writer":
                    operation = lambda _raw: (_ for _ in ()).throw(RuntimeError("operation"))
                writer = patch.object(
                    database, "_record_recovery_failure",
                    side_effect=RuntimeError("writer") if fault == "failure_writer" else database._record_recovery_failure,
                )
                with writer:
                    with self.assertRaisesRegex(RuntimeError, "^authoritative_learning_schema_recovery_failed$"):
                        database.run_recovery_action(
                            bind, migration, action_name="sqlite_switch",
                            requires_foreign_keys_off=fault.startswith("pragma"), operation=operation,
                        )
                names = self._reopen_and_assert_atomic_recovery_state(
                    engine, "switching" if fault == "failure_writer" else "recovery_failed"
                )
                self.assertEqual(names, before_names)

    def test_actual_rollback_failure_preserves_original_transaction_reason(self):
        engine, migration = self._switching_engine()
        controlled = database._controlled_objects(migration)

        class RollbackFailingRaw:
            def __init__(self, raw):
                self.raw = raw

            def cursor(self):
                return self.raw.cursor()

            def commit(self):
                return self.raw.commit()

            def rollback(self):
                raise sqlite3.OperationalError("rollback")

        def fail(stage):
            if stage == "after_rename_0":
                raise sqlite3.OperationalError(stage)

        with engine.connect() as connection:
            raw = RollbackFailingRaw(connection.connection.driver_connection)
            with self.assertRaises(database.RecoveryActionError) as raised:
                database._run_sqlite_rename_transaction(
                    raw, self._switch_rename_statements(controlled), migration.id, "switched",
                    fault_hook=fail,
                )
        self.assertEqual(raised.exception.reason_code, "switch_rename_failed")

    def test_switch_rollback_fault_keeps_filebacked_state_atomic_after_reopen(self):
        self._assert_wrapper_rollback_fault_acceptance(
            "switching_committed_before_begin", "switched", self._switch_rename_statements,
        )

    def test_restore_rollback_fault_keeps_filebacked_state_atomic_after_reopen(self):
        self._assert_wrapper_rollback_fault_acceptance(
            "switched_committed_before_verify", "prepared", self._restore_rename_statements,
        )

    def _assert_wrapper_rollback_fault_acceptance(self, checkpoint, status, statements):
        engine, migration = self._switching_engine(checkpoint)
        controlled = database._controlled_objects(migration)
        with engine.begin() as connection:
            before_names = set(connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )).scalars())

        class RawProxy:
            def __init__(self, raw):
                self.raw = raw

            def cursor(self):
                return self.raw.cursor()

            def execute(self, statement):
                return self.raw.execute(statement)

            def commit(self):
                return self.raw.commit()

            def rollback(self):
                raise sqlite3.OperationalError("rollback")

        connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        raw = RawProxy(connection.connection.driver_connection)

        class ConnectionProxy:
            def __init__(self):
                self.connection = type("Driver", (), {"driver_connection": raw})()

            def execution_options(self, **_options):
                return self

            def close(self):
                connection.close()

        bind = type("Bind", (), {
            "connect": lambda _self: ConnectionProxy(),
            "begin": lambda _self: engine.begin(),
        })()

        def operation(_raw):
            database._run_sqlite_rename_transaction(
                raw, statements(controlled), migration.id, status,
                fault_hook=lambda stage: (
                    (_ for _ in ()).throw(sqlite3.OperationalError(stage))
                    if stage == "after_rename_0" else None
                ),
            )

        with self.assertRaisesRegex(RuntimeError, "^authoritative_learning_schema_recovery_failed$"):
            database.run_recovery_action(
                bind, migration, action_name="sqlite_switch",
                requires_foreign_keys_off=False, operation=operation,
            )
        path = engine.url.database
        engine.dispose()
        restarted = create_engine(f"sqlite:///{path}")
        self.addCleanup(restarted.dispose)
        with restarted.begin() as connection:
            names = set(connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )).scalars())
        self.assertEqual(names, before_names)
        self.assertEqual(self.migration_status(restarted), "recovery_failed")

    def test_wrapper_preserves_transaction_reason_when_switch_or_restore_rollback_fails(self):
        for checkpoint, status, statements in (
            ("switching_committed_before_begin", "switched", self._switch_rename_statements),
            ("switched_committed_before_verify", "prepared", self._restore_rename_statements),
        ):
            with self.subTest(status=status):
                engine, migration = self._switching_engine(checkpoint)
                controlled = database._controlled_objects(migration)
                with engine.begin() as connection:
                    before_names = set(connection.execute(text(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )).scalars())

                class RawProxy:
                    def __init__(self, raw):
                        self.raw = raw

                    def cursor(self):
                        return self.raw.cursor()

                    def execute(self, statement):
                        return self.raw.execute(statement)

                    def commit(self):
                        return self.raw.commit()

                    def rollback(self):
                        raise sqlite3.OperationalError("rollback")

                connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
                raw = RawProxy(connection.connection.driver_connection)

                class ConnectionProxy:
                    def __init__(self):
                        self.connection = type("Driver", (), {"driver_connection": raw})()

                    def execution_options(self, **_options):
                        return self

                    def close(self):
                        connection.close()

                bind = type("Bind", (), {
                    "connect": lambda _self: ConnectionProxy(),
                    "begin": lambda _self: engine.begin(),
                })()

                def operation(_raw):
                    database._run_sqlite_rename_transaction(
                        raw, statements(controlled), migration.id, status,
                        fault_hook=lambda stage: (
                            (_ for _ in ()).throw(sqlite3.OperationalError(stage))
                            if stage == "after_rename_0" else None
                        ),
                    )

                with self.assertRaisesRegex(RuntimeError, "^authoritative_learning_schema_recovery_failed$"):
                    database.run_recovery_action(
                        bind, migration, action_name="sqlite_switch",
                        requires_foreign_keys_off=False, operation=operation,
                    )
                path = engine.url.database
                engine.dispose()
                restarted = create_engine(f"sqlite:///{path}")
                self.addCleanup(restarted.dispose)
                with restarted.begin() as connection:
                    names = set(connection.execute(text(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )).scalars())
                self.assertEqual(names, before_names)
                self.assertEqual(self.migration_status(restarted), "recovery_failed")

    def test_switch_uses_single_snapshot_decision_without_shadow_heuristic(self):
        engine, migration = self._switching_engine()
        with patch.object(
            database, "build_sqlite_recovery_snapshot",
            wraps=database.build_sqlite_recovery_snapshot,
        ) as snapshot, patch.object(
            database, "decide_sqlite_recovery", wraps=database.decide_sqlite_recovery,
        ) as decide:
            database.recover_authoritative_learning_schema_for_sqlite(engine, migration)
        self.assertEqual(snapshot.call_count, 1)
        self.assertEqual(decide.call_count, 1)

    def test_staged_safe_partial_shadow_rebuilds_from_subset_evidence(self):
        engine, migration = self._switching_engine("staged_committed")
        shadow = database.controlled_sqlite_name("review_tasks", "shadow")
        with engine.begin() as connection:
            connection.execute(text(f'DELETE FROM "{shadow}" WHERE review_task_id = \'task-v1\''))
            snapshot = database.build_sqlite_recovery_snapshot(connection, migration)
        self.assertTrue(snapshot.shadow.safe_partial_data)
        self.assertEqual(database.decide_sqlite_recovery(snapshot).action, "perform_switch")

    def test_staged_shadow_parent_deletion_fails_closed_from_fk_evidence(self):
        engine, migration = self._switching_engine("staged_committed")
        shadow = database.controlled_sqlite_name("learning_attempts", "shadow")
        with engine.connect() as connection:
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
            connection.execute(text(f'DELETE FROM "{shadow}" WHERE attempt_id = \'attempt-v1\''))
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys = ON")
            snapshot = database.build_sqlite_recovery_snapshot(connection, migration)
        self.assertTrue(snapshot.shadow.foreign_key_violations)
        self.assertFalse(snapshot.shadow.safe_partial_data)
        self.assertEqual(database.decide_sqlite_recovery(snapshot).action, "fail_closed")

    def test_staged_shadow_foreign_stable_id_fails_closed(self):
        engine, migration = self._switching_engine("staged_committed")
        shadow = database.controlled_sqlite_name("review_tasks", "shadow")
        with engine.begin() as connection:
            connection.execute(text(
                f"UPDATE \"{shadow}\" SET review_task_id = 'foreign-task' "
                "WHERE review_task_id = 'task-v1'"
            ))
            snapshot = database.build_sqlite_recovery_snapshot(connection, migration)
        self.assertFalse(snapshot.shadow.safe_partial_data)
        self.assertEqual(database.decide_sqlite_recovery(snapshot).action, "fail_closed")

    def test_prepared_and_staged_restarts_use_one_snapshot_and_decision(self):
        for checkpoint in ("prepared_committed", "staged_committed"):
            with self.subTest(checkpoint=checkpoint):
                engine, _ = self._switching_engine(checkpoint)
                with patch.object(
                    database, "build_sqlite_recovery_snapshot",
                    wraps=database.build_sqlite_recovery_snapshot,
                ) as snapshot, patch.object(
                    database, "decide_sqlite_recovery",
                    wraps=database.decide_sqlite_recovery,
                ) as decide:
                    database.ensure_runtime_schema_for(engine)
                self.assertEqual(snapshot.call_count, 1)
                self.assertEqual(decide.call_count, 1)
                self.assertEqual(self.migration_status(engine), "verified")

    def test_prepared_corrupt_shadow_fails_closed_before_cleanup_on_fresh_engine(self):
        engine, _ = self._switching_engine("prepared_committed")
        shadow = database.controlled_sqlite_name("learning_attempts", "shadow")
        with engine.begin() as connection:
            connection.execute(text(f'CREATE VIEW "{shadow}" AS SELECT 1 AS id'))
        path = engine.url.database
        engine.dispose()
        restarted = create_engine(f"sqlite:///{path}")
        self.addCleanup(restarted.dispose)
        with self.assertRaisesRegex(RuntimeError, "^authoritative_learning_schema_recovery_failed$"):
            database.ensure_runtime_schema_for(restarted)
        self.assertEqual(self.migration_status(restarted), "recovery_failed")
        with restarted.begin() as connection:
            self.assertEqual(
                connection.execute(text(f'SELECT name FROM sqlite_master WHERE name = "{shadow}"')).scalar_one(),
                shadow,
            )

    def test_restage_switching_bridge_failure_is_stable_on_fresh_engine(self):
        engine, migration = self._switching_engine("staged_committed")
        with patch.object(database, "_mark_restage_switching", side_effect=RuntimeError("bridge")):
            with self.assertRaisesRegex(RuntimeError, "^authoritative_learning_schema_recovery_failed$"):
                database._perform_switch_from_snapshot(
                    engine, migration, database._controlled_objects(migration), lambda _stage: None,
                )
        path = engine.url.database
        engine.dispose()
        restarted = create_engine(f"sqlite:///{path}")
        self.addCleanup(restarted.dispose)
        self.assertEqual(self.migration_status(restarted), "recovery_failed")
        with restarted.begin() as connection:
            self.assertIn("learning_attempts", database.inspect(connection).get_table_names())

    def test_restore_prepared_bridge_failure_is_stable_on_fresh_engine(self):
        engine, migration = self._switching_engine("switched_committed_before_verify")
        with engine.connect() as connection:
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
            connection.execute(text("DELETE FROM learning_attempts WHERE attempt_id = 'attempt-v1'"))
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        with patch.object(database, "_load_prepared_recovery_migration", side_effect=RuntimeError("bridge")):
            with self.assertRaisesRegex(RuntimeError, "^authoritative_learning_schema_recovery_failed$"):
                database._restore_backup_from_snapshot(
                    engine, migration, database._controlled_objects(migration), lambda _stage: None,
                )
        path = engine.url.database
        engine.dispose()
        restarted = create_engine(f"sqlite:///{path}")
        self.addCleanup(restarted.dispose)
        self.assertEqual(self.migration_status(restarted), "recovery_failed")
        with restarted.begin() as connection:
            self.assertIn("learning_attempts", database.inspect(connection).get_table_names())

    def test_restore_post_rename_restage_cleanup_failure_is_stable_on_fresh_engine(self):
        engine, migration = self._switching_engine("switched_committed_before_verify")
        with engine.connect() as connection:
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
            connection.execute(text("DELETE FROM learning_attempts WHERE attempt_id = 'attempt-v1'"))
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        with patch.object(database, "_drop_controlled_shadows", side_effect=RuntimeError("cleanup")):
            with self.assertRaisesRegex(RuntimeError, "^authoritative_learning_schema_recovery_failed$"):
                database.recover_authoritative_learning_schema_for_sqlite(engine, migration)
        path = engine.url.database
        engine.dispose()
        restarted = create_engine(f"sqlite:///{path}")
        self.addCleanup(restarted.dispose)
        self.assertEqual(self.migration_status(restarted), "recovery_failed")
        with restarted.begin() as connection:
            tables = set(database.inspect(connection).get_table_names())
        self.assertIn("learning_attempts", tables)

    def test_switch_pragma_boundary_precedes_begin_and_restores_after_rename_failure(self):
        engine, migration = self._switching_engine()
        events = []
        original_execute = database._run_sqlite_rename_transaction

        def record_begin(raw_connection, statements, ledger_id, status):
            events.append(("begin", raw_connection.execute("PRAGMA foreign_keys").fetchone()[0]))
            raise RuntimeError("forced rename failure")

        with patch.object(database, "_run_sqlite_rename_transaction", side_effect=record_begin):
            with self.assertRaisesRegex(RuntimeError, "^authoritative_learning_schema_recovery_failed$"):
                database.switch_authoritative_learning_schema_for_sqlite(engine, migration)
        self.assertEqual(events, [("begin", 0)])
        with engine.begin() as connection:
            self.assertEqual(connection.execute(text("PRAGMA foreign_keys")).scalar_one(), 1)
        self.assertIsNotNone(original_execute)

    def test_switching_shadow_with_extra_backup_fails_closed_on_fresh_engine(self):
        engine, _ = self._switching_engine()
        controlled = database.controlled_sqlite_name("learning_attempts", "backup")
        with engine.begin() as connection:
            connection.execute(text(f'CREATE TABLE "{controlled}" (id INTEGER PRIMARY KEY)'))
        path = engine.url.database
        engine.dispose()
        restarted = create_engine(f"sqlite:///{path}")
        self.addCleanup(restarted.dispose)
        with self.assertRaisesRegex(RuntimeError, "^authoritative_learning_schema_recovery_failed$"):
            database.ensure_runtime_schema_for(restarted)
        self.assertEqual(self.migration_status(restarted), "recovery_failed")

    def test_prepared_restart_rejects_canonical_mutation_without_touching_shadows(self):
        engine = self.make_file_engine()
        self.create_v1_authoritative_fixture(engine)
        with self.assertRaises(InterruptedMigration):
            database.ensure_runtime_schema_for(engine, checkpoint=self.stop_after("prepared_committed"))
        with engine.begin() as connection:
            connection.execute(text("UPDATE learning_attempts SET attempt_id = 'mutated'"))
        path = engine.url.database
        engine.dispose()
        restarted = create_engine(f"sqlite:///{path}")
        self.addCleanup(restarted.dispose)
        with self.assertRaisesRegex(RuntimeError, "^authoritative_learning_schema_recovery_failed$"):
            database.ensure_runtime_schema_for(restarted)
        self.assertEqual(self.migration_status(restarted), "recovery_failed")
        with restarted.begin() as connection:
            shadows = connection.execute(text(
                "SELECT name FROM sqlite_master WHERE name LIKE '%__shadow'"
            )).scalars().all()
        self.assertEqual(shadows, [])

    def test_verified_cleanup_failure_keeps_verified_ledger_and_controlled_objects(self):
        engine, migration = self._switching_engine("switched_committed_before_verify")
        with patch.object(database, "_drop_controlled_objects", side_effect=RuntimeError("cleanup")):
            database.recover_authoritative_learning_schema_for_sqlite(engine, migration)
        with engine.begin() as connection:
            row = connection.execute(text(
                "SELECT status, failure_reason, verification_summary_json FROM runtime_schema_migrations "
                "WHERE migration_id = :migration_id"
            ), {"migration_id": database.AUTHORITATIVE_LEARNING_MIGRATION_ID}).one()
            names = connection.execute(text(
                "SELECT name FROM sqlite_master WHERE name LIKE '%__authoritative_learning_records_v2__%'"
            )).scalars().all()
        summary = json.loads(row.verification_summary_json)
        self.assertEqual(row.status, "verified")
        self.assertIsNone(row.failure_reason)
        self.assertEqual(summary["cleanup_status"], "controlled_cleanup_failed")
        self.assertTrue(summary["remaining_controlled_objects"])
        self.assertTrue(names)

    def test_verification_commit_is_visible_before_cleanup_transaction(self):
        engine, migration = self._switching_engine("switched_committed_before_verify")
        observed = {}

        def observe_verified(connection, controlled):
            with engine.begin() as fresh_connection:
                row = fresh_connection.execute(text(
                    "SELECT status, verification_summary_json FROM runtime_schema_migrations "
                    "WHERE migration_id = :migration_id"
                ), {"migration_id": database.AUTHORITATIVE_LEARNING_MIGRATION_ID}).one()
                observed["status"] = row.status
                observed["summary"] = json.loads(row.verification_summary_json)
                observed["objects"] = database._remaining_controlled_objects(
                    fresh_connection, controlled
                )
            database._drop_controlled_objects(connection, controlled)

        with patch.object(database, "_drop_controlled_objects", side_effect=observe_verified):
            database.recover_authoritative_learning_schema_for_sqlite(engine, migration)
        self.assertEqual(observed["status"], "verified")
        self.assertEqual(observed["summary"]["cleanup_status"], "pending")
        self.assertTrue(observed["objects"])

    def test_cleanup_failure_preserves_verified_and_retries_on_fresh_engine(self):
        engine, migration = self._switching_engine("switched_committed_before_verify")
        with patch.object(database, "_drop_controlled_objects", side_effect=RuntimeError("drop failed")):
            database.recover_authoritative_learning_schema_for_sqlite(engine, migration)
        self.assertEqual(self.migration_status(engine), "verified")
        restarted = self.restart_engine_and_run_upgrade(engine)
        self.assertEqual(self.migration_status(restarted), "verified")
        self.assert_no_controlled_objects(restarted)

    def test_verified_restart_with_corrupt_cleanup_ledger_is_nonfatal_and_preserves_unknown_objects(self):
        engine, migration = self._switching_engine("switched_committed_before_verify")
        database.verify_and_mark_authoritative_schema(engine, migration)
        unknown_object = "unknown__authoritative_learning_records_v2__backup"
        with engine.begin() as connection:
            completed_at = connection.execute(text(
                "SELECT completed_at FROM runtime_schema_migrations WHERE migration_id = :migration_id"
            ), {"migration_id": database.AUTHORITATIVE_LEARNING_MIGRATION_ID}).scalar_one()
            connection.execute(text(
                "UPDATE runtime_schema_migrations SET controlled_objects_json = '[]' "
                "WHERE migration_id = :migration_id"
            ), {"migration_id": database.AUTHORITATIVE_LEARNING_MIGRATION_ID})
            connection.execute(text(f'CREATE TABLE "{unknown_object}" (id INTEGER PRIMARY KEY)'))
        path = engine.url.database
        engine.dispose()
        restarted = create_engine(f"sqlite:///{path}")
        self.addCleanup(restarted.dispose)

        database.ensure_runtime_schema_for(restarted)

        with restarted.begin() as connection:
            row = connection.execute(text(
                "SELECT status, completed_at, verification_summary_json FROM runtime_schema_migrations "
                "WHERE migration_id = :migration_id"
            ), {"migration_id": database.AUTHORITATIVE_LEARNING_MIGRATION_ID}).one()
            tables = set(database.inspect(connection).get_table_names())
        summary = json.loads(row.verification_summary_json)
        self.assertEqual(row.status, "verified")
        self.assertEqual(row.completed_at, completed_at)
        self.assertEqual(summary["cleanup_status"], "controlled_cleanup_failed")
        self.assertEqual(summary["remaining_controlled_objects"], [])
        self.assertIn(unknown_object, tables)
        self.assertTrue(set(database._AUTHORITATIVE_LEARNING_TABLES).issubset(tables))

    def test_verified_cleanup_summary_write_failure_is_nonfatal(self):
        engine, migration = self._switching_engine("switched_committed_before_verify")
        database.verify_and_mark_authoritative_schema(engine, migration)
        with patch.object(database, "_persist_migration", side_effect=RuntimeError("write failed")):
            database.ensure_runtime_schema_for(engine)
        self.assertEqual(self.migration_status(engine), "verified")

    def test_verified_restart_skips_full_recovery_inspection(self):
        engine, migration = self._switching_engine("switched_committed_before_verify")
        database.recover_authoritative_learning_schema_for_sqlite(engine, migration)
        blockers = (
            "build_sqlite_recovery_snapshot", "build_authoritative_manifest",
            "physical_schema_matches_target", "_foreign_key_check",
            "stage_authoritative_learning_schema_for_sqlite",
            "switch_authoritative_learning_schema_for_sqlite",
        )
        with patch.multiple(database, **{
            name: unittest.mock.DEFAULT for name in blockers
        }) as mocks:
            for mocked in mocks.values():
                mocked.side_effect = AssertionError("full inspection called")
            database.ensure_runtime_schema_for(engine)

    def test_verified_wrong_target_version_fails_closed(self):
        engine, migration = self._switching_engine("switched_committed_before_verify")
        database.recover_authoritative_learning_schema_for_sqlite(engine, migration)
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE runtime_schema_migrations SET target_version = 'wrong' "
                "WHERE migration_id = :migration_id"
            ), {"migration_id": database.AUTHORITATIVE_LEARNING_MIGRATION_ID})
        with self.assertRaisesRegex(RuntimeError, "^authoritative_learning_schema_recovery_failed$"):
            database.ensure_runtime_schema_for(engine)
        self.assertEqual(self.migration_status(engine), "recovery_failed")

    def test_verified_missing_canonical_table_fails_closed(self):
        engine, migration = self._switching_engine("switched_committed_before_verify")
        database.recover_authoritative_learning_schema_for_sqlite(engine, migration)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE review_tasks"))
        with self.assertRaisesRegex(RuntimeError, "^authoritative_learning_schema_recovery_failed$"):
            database.ensure_runtime_schema_for(engine)
        self.assertEqual(self.migration_status(engine), "recovery_failed")


if __name__ == "__main__":
    unittest.main()

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from competition_app.db.migrations import MigrationError, MigrationRunner


def test_migrations_are_idempotent_and_checksum_changes_are_rejected(tmp_path: Path) -> None:
    migration = tmp_path / "001_create_sample.sql"
    migration.write_text("CREATE TABLE sample (id INTEGER PRIMARY KEY);\n", encoding="utf-8")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    runner = MigrationRunner(engine, tmp_path)

    assert runner.run() == ["001_create_sample.sql"]
    assert runner.run() == []

    migration.write_text("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT);\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="checksum"):
        runner.run()


def test_migration_checksum_ignores_platform_newlines(tmp_path: Path) -> None:
    migration = tmp_path / "001_example.sql"
    migration.write_bytes(b"CREATE TABLE example (id INTEGER);\n")
    engine = create_engine("sqlite:///:memory:")
    runner = MigrationRunner(engine, tmp_path)

    runner.run()
    migration.write_bytes(b"CREATE TABLE example (id INTEGER);\r\n")

    assert runner.run() == []


def test_atomic_daily_task_outbox_migration_is_sqlite_compatible_and_idempotent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    migration_dir = Path(__file__).resolve().parents[2] / "migrations"
    runner = MigrationRunner(engine, migration_dir)

    applied = runner.run()

    assert "010_atomic_daily_task_execution.sql" in applied
    assert "014_disable_registration_onboarding.sql" in applied
    assert runner.run() == []
    inspector = inspect(engine)
    assert "learning_task_sync_outbox" in inspector.get_table_names()
    unique_indexes = inspector.get_unique_constraints("learning_task_sync_outbox")
    unique_columns = {tuple(item["column_names"]) for item in unique_indexes}
    index_columns = {
        tuple(item["column_names"])
        for item in inspector.get_indexes("learning_task_sync_outbox")
    }
    assert ("task_id", "task_version", "event_type") in unique_columns | index_columns
    assert ("learner_id", "status", "created_at") in index_columns

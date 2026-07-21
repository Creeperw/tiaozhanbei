from pathlib import Path

import pytest
from sqlalchemy import create_engine

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

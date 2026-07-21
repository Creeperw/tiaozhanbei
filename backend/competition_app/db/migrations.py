from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import Engine, inspect, text


class MigrationError(RuntimeError):
    """Raised when a migration history cannot be applied safely."""


class MigrationRunner:
    def __init__(self, engine: Engine, migration_dir: Path) -> None:
        self.engine = engine
        self.migration_dir = migration_dir

    def run(self) -> list[str]:
        applied_now: list[str] = []
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS schema_migrations ("
                    "version VARCHAR(255) PRIMARY KEY, checksum VARCHAR(64) NOT NULL)"
                )
            )
            applied = {
                row.version: row.checksum
                for row in connection.execute(text("SELECT version, checksum FROM schema_migrations"))
            }
            for path in sorted(self.migration_dir.glob("*.sql")):
                checksum = hashlib.sha256(path.read_bytes()).hexdigest()
                if path.name in applied:
                    if applied[path.name] != checksum:
                        raise MigrationError(f"migration checksum changed: {path.name}")
                    continue
                sql = path.read_text(encoding="utf-8")
                if self.engine.dialect.name == "sqlite":
                    connection.connection.driver_connection.executescript(sql)
                else:
                    for statement in (item.strip() for item in sql.split(";")):
                        if statement:
                            connection.exec_driver_sql(statement)
                connection.execute(
                    text("INSERT INTO schema_migrations (version, checksum) VALUES (:version, :checksum)"),
                    {"version": path.name, "checksum": checksum},
                )
                applied_now.append(path.name)
        return applied_now

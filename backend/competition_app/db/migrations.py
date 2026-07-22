from __future__ import annotations

import hashlib
import re
from pathlib import Path

from sqlalchemy import Engine, inspect, text


class MigrationError(RuntimeError):
    """Raised when a migration history cannot be applied safely."""


class MigrationRunner:
    def __init__(self, engine: Engine, migration_dir: Path) -> None:
        self.engine = engine
        self.migration_dir = migration_dir

    @staticmethod
    def _sqlite_sql(sql: str) -> str:
        indexes: list[tuple[str, str, str]] = []
        statements: list[str] = []
        for raw_statement in sql.split(";"):
            statement = raw_statement.strip()
            if not statement:
                continue
            table_match = re.search(
                r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z0-9_]+)",
                statement,
                flags=re.IGNORECASE,
            )
            if table_match:
                table_name = table_match.group(1)
                pattern = re.compile(
                    r",\s*INDEX\s+(?P<name>[A-Za-z0-9_]+)\s*"
                    r"\((?P<columns>[^)]+)\)",
                    flags=re.IGNORECASE,
                )

                def collect_index(match: re.Match[str]) -> str:
                    indexes.append(
                        (match.group("name"), table_name, match.group("columns"))
                    )
                    return ""

                statement = pattern.sub(collect_index, statement)
            statement = re.sub(r"TIMESTAMP\(6\)", "TIMESTAMP", statement, flags=re.IGNORECASE)
            statement = re.sub(
                r"CURRENT_TIMESTAMP\(6\)",
                "CURRENT_TIMESTAMP",
                statement,
                flags=re.IGNORECASE,
            )
            statement = re.sub(
                r"\s+ON\s+UPDATE\s+CURRENT_TIMESTAMP",
                "",
                statement,
                flags=re.IGNORECASE,
            )
            statements.append(statement + ";")
        statements.extend(
            f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns});"
            for name, table, columns in indexes
        )
        return "\n".join(statements)

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
                    connection.connection.driver_connection.executescript(
                        self._sqlite_sql(sql)
                    )
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

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import Engine, URL, create_engine, event, text

from competition_app.config import Settings, SettingsError
from competition_app.db.migrations import MigrationRunner


def _attach_sqlite_pragmas(engine: Engine) -> None:
    """为 SQLite engine 启用 WAL 模式与长 busy_timeout，支持高并发读写。"""

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=60000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


class DatabaseBootstrap:
    def __init__(self, settings: Settings, migration_dir: Path | None = None) -> None:
        if not (settings.database_url or settings.use_sqlite or settings.mysql_password):
            raise SettingsError("database configuration is required for initialization")
        self.settings = settings
        self.migration_dir = migration_dir or Path(__file__).parents[1] / "migrations"

    def ensure_database(self) -> Engine:
        if self.settings.database_url:
            engine = create_engine(self.settings.database_url, pool_pre_ping=True)
        elif self.settings.use_sqlite:
            sqlite_path = self.settings.sqlite_path.resolve()
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(
                URL.create("sqlite", database=str(sqlite_path)),
                connect_args={"timeout": 60, "check_same_thread": False},
                pool_size=32,
                max_overflow=64,
                pool_pre_ping=True,
            )
            _attach_sqlite_pragmas(engine)
        else:
            engine = self._ensure_mysql_database()
        MigrationRunner(engine, self.migration_dir).run()
        return engine

    def _ensure_mysql_database(self) -> Engine:
        password = quote_plus(self.settings.mysql_password or "")
        server_url = (
            f"mysql+pymysql://{self.settings.mysql_user}:{password}@"
            f"{self.settings.mysql_host}:{self.settings.mysql_port}/?charset=utf8mb4"
        )
        server_engine = create_engine(server_url, pool_pre_ping=True)
        database_name = self.settings.mysql_database.replace("`", "``")
        with server_engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{database_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
        server_engine.dispose()
        database_url = (
            f"mysql+pymysql://{self.settings.mysql_user}:{password}@"
            f"{self.settings.mysql_host}:{self.settings.mysql_port}/{self.settings.mysql_database}"
            "?charset=utf8mb4"
        )
        return create_engine(database_url, pool_pre_ping=True)

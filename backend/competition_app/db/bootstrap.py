from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import Engine, URL, create_engine, text

from competition_app.config import Settings, SettingsError
from competition_app.db.migrations import MigrationRunner


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
            engine = create_engine(URL.create("sqlite", database=str(sqlite_path)))
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

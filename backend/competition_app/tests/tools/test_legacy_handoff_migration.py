from pathlib import Path
import sqlite3
from hashlib import sha256

from competition_app.tools.legacy_handoff_migration import migrate_legacy_handoff


def initialize_main_database(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE app_users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                normalized_username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                password_iterations INTEGER NOT NULL,
                role TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE TABLE auth_sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );
            INSERT INTO app_users VALUES (
                'USER_ADMIN', 'admin', 'admin', 'admin', 'old', 'old', 1, 'user', 'active',
                '2026-01-01T00:00:00+00:00', NULL
            );
            INSERT INTO auth_sessions VALUES (
                'SESSION_OLD', 'USER_ADMIN', 'hash', '2026-12-01T00:00:00+00:00', NULL,
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
            );
            """
        )


def initialize_handoff_database(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                email TEXT,
                hashed_password TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT,
                title_auto_enabled INTEGER,
                active_leaf_message_id INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL,
                parent_id INTEGER,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                files TEXT,
                timestamp TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE learning_activity_records (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                activity_type TEXT,
                resource_id TEXT,
                resource_type TEXT,
                duration_minutes INTEGER,
                completion_status TEXT,
                score REAL,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE mistake_records (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                question_id TEXT,
                kp_ids_json TEXT,
                error_type TEXT,
                summary TEXT,
                status TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                attempt_item_id TEXT,
                question_version_id TEXT
            );
            CREATE TABLE external_identity_links (
                id INTEGER PRIMARY KEY,
                provider TEXT NOT NULL,
                external_user_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                UNIQUE(provider, external_user_id)
            );
            INSERT INTO users VALUES (1, 'admin', NULL, 'legacy', 'admin', '2026-01-01');
            """
        )


def initialize_legacy_database(path: Path) -> None:
    initialize_handoff_database(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users VALUES (2, 'learner', 'learner@example.com', 'legacy', 'user', '2026-01-02')")
        db.execute("INSERT INTO sessions VALUES ('S1', 1, '旧会话', 1, NULL, '2026-01-03')")
        db.execute("INSERT INTO messages VALUES (10, 'S1', NULL, 'user', '旧消息', NULL, '2026-01-03', '2026-01-03')")
        db.execute("INSERT INTO learning_activity_records VALUES (20, 1, 'study', 'R1', 'book', 10, 'completed', 1.0, '{}', '2026-01-04')")
        db.execute("INSERT INTO mistake_records VALUES (30, 1, 'Q1', '[]', 'concept', '旧错题', 'active', '2026-01-05', '2026-01-05', NULL, NULL)")


def test_migration_promotes_admin_and_imports_legacy_records_idempotently(tmp_path: Path) -> None:
    main_db = tmp_path / "main.sqlite3"
    target_db = tmp_path / "target.sqlite3"
    legacy_db = tmp_path / "legacy.sqlite3"
    initialize_main_database(main_db)
    initialize_handoff_database(target_db)
    with sqlite3.connect(target_db) as db:
        username = "core_" + sha256("USER_ADMIN".encode("utf-8")).hexdigest()[:32]
        db.execute(
            "INSERT INTO users VALUES (2, ?, NULL, 'generated', 'user', '2026-01-01')",
            (username,),
        )
        db.execute(
            "INSERT INTO external_identity_links VALUES (1, 'competition_app', 'USER_ADMIN', 2)"
        )
        db.execute(
            "INSERT INTO learning_activity_records VALUES (99, 2, 'recent', 'R2', 'book', 5, 'completed', 1.0, '{}', '2026-01-06')"
        )
    initialize_legacy_database(legacy_db)

    first = migrate_legacy_handoff(
        legacy_db=legacy_db,
        main_db=main_db,
        handoff_db=target_db,
        admin_password="Admin@123456",
    )
    second = migrate_legacy_handoff(
        legacy_db=legacy_db,
        main_db=main_db,
        handoff_db=target_db,
        admin_password="Admin@123456",
    )

    assert first["users_mapped"] == 2
    assert second["sessions_imported"] == 0
    with sqlite3.connect(main_db) as db:
        role, password_hash, salt, iterations = db.execute(
            "SELECT role, password_hash, password_salt, password_iterations FROM app_users WHERE username='admin'"
        ).fetchone()
        assert role == "admin"
        assert password_hash != "old"
        assert salt != "old"
        assert iterations == 310_000
        assert db.execute("SELECT COUNT(*) FROM auth_sessions WHERE user_id='USER_ADMIN' AND revoked_at IS NULL").fetchone()[0] == 0
    with sqlite3.connect(target_db) as db:
        mappings = db.execute(
            "SELECT legacy_user_id, target_user_id, primary_user_id FROM legacy_user_mappings ORDER BY legacy_user_id"
        ).fetchall()
        assert mappings[0] == (1, 1, "USER_ADMIN")
        assert mappings[1][0] == 2
        assert db.execute(
            "SELECT user_id FROM external_identity_links "
            "WHERE provider='competition_app' AND external_user_id='USER_ADMIN'"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM learning_activity_records WHERE user_id=1"
        ).fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM learning_activity_records").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM mistake_records").fetchone()[0] == 1

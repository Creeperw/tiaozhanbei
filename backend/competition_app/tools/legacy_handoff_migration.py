from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from competition_app.services.auth import AuthenticationService


def _row_values(row: sqlite3.Row, *names: str) -> dict[str, Any]:
    return {name: row[name] for name in names}


def _ensure_mapping_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS legacy_user_mappings (
            legacy_user_id INTEGER PRIMARY KEY,
            target_user_id INTEGER NOT NULL,
            primary_user_id TEXT,
            legacy_username TEXT NOT NULL,
            migrated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS legacy_record_mappings (
            source_table TEXT NOT NULL,
            legacy_record_id TEXT NOT NULL,
            target_record_id TEXT NOT NULL,
            migrated_at TEXT NOT NULL,
            PRIMARY KEY (source_table, legacy_record_id)
        );
        CREATE TABLE IF NOT EXISTS legacy_migration_metadata (
            migration_key TEXT PRIMARY KEY,
            completed_at TEXT NOT NULL
        );
        """
    )


def _source_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_source(
    connection: sqlite3.Connection, source: sqlite3.Connection, fingerprint: str
) -> None:
    row = connection.execute(
        "SELECT completed_at FROM legacy_migration_metadata "
        "WHERE migration_key=?",
        (f"source:{fingerprint}",),
    ).fetchone()
    known_sources = connection.execute(
        "SELECT migration_key FROM legacy_migration_metadata "
        "WHERE migration_key LIKE 'source:%'"
    ).fetchall()
    if known_sources and row is None:
        raise ValueError("目标库已绑定到另一份旧数据库，拒绝混合迁移")
    if row is not None:
        expected = {
            "users": source.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "sessions": source.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
            "messages": source.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            "learning_activity_records": source.execute(
                "SELECT COUNT(*) FROM learning_activity_records"
            ).fetchone()[0],
            "mistake_records": source.execute("SELECT COUNT(*) FROM mistake_records").fetchone()[0],
        }
        actual = {
            "users": connection.execute(
                "SELECT COUNT(*) FROM legacy_user_mappings"
            ).fetchone()[0],
            **{
                table: connection.execute(
                    "SELECT COUNT(*) FROM legacy_record_mappings WHERE source_table=?",
                    (table,),
                ).fetchone()[0]
                for table in expected
                if table != "users"
            },
        }
        if actual != expected:
            raise ValueError("目标库已有无法验证来源的迁移映射，拒绝继续迁移")
    else:
        connection.execute(
            "INSERT INTO legacy_migration_metadata (migration_key, completed_at) VALUES (?, ?)",
            (f"source:{fingerprint}", datetime.now(timezone.utc).isoformat()),
        )


def _target_user_id(connection: sqlite3.Connection, legacy_user: sqlite3.Row) -> int:
    mapping = connection.execute(
        "SELECT target_user_id FROM legacy_user_mappings WHERE legacy_user_id=?",
        (legacy_user["id"],),
    ).fetchone()
    if mapping:
        return mapping[0]
    existing = connection.execute(
        "SELECT id FROM users WHERE username=?", (legacy_user["username"],)
    ).fetchone()
    if existing:
        if legacy_user["username"] != "admin":
            raise ValueError(
                f"目标兼容库存在未映射的同名用户: {legacy_user['username']}"
            )
        return existing[0]
    if legacy_user["email"]:
        email_owner = connection.execute(
            "SELECT id FROM users WHERE email=?", (legacy_user["email"],)
        ).fetchone()
        if email_owner:
            raise ValueError(
                f"目标兼容库存在未映射的同邮箱用户: {legacy_user['email']}"
            )
    placeholder_hash = "migrated:" + hashlib.sha256(
        f"{legacy_user['id']}:{legacy_user['username']}".encode("utf-8")
    ).hexdigest()
    cursor = connection.execute(
        "INSERT INTO users (username, email, hashed_password, role, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            legacy_user["username"],
            legacy_user["email"],
            placeholder_hash,
            legacy_user["role"],
            legacy_user["created_at"],
        ),
    )
    return int(cursor.lastrowid)


def _record_mapping(connection: sqlite3.Connection, source_table: str, legacy_id: Any, target_id: Any) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO legacy_record_mappings "
        "(source_table, legacy_record_id, target_record_id, migrated_at) VALUES (?, ?, ?, ?)",
        (source_table, str(legacy_id), str(target_id), datetime.now(timezone.utc).isoformat()),
    )


def _mapped_record_id(connection: sqlite3.Connection, source_table: str, legacy_id: Any) -> str | None:
    row = connection.execute(
        "SELECT target_record_id FROM legacy_record_mappings "
        "WHERE source_table=? AND legacy_record_id=?",
        (source_table, str(legacy_id)),
    ).fetchone()
    return row[0] if row else None


def _promote_admin(main: sqlite3.Connection, password: str) -> str:
    row = main.execute(
        "SELECT user_id FROM app_users WHERE normalized_username='admin'"
    ).fetchone()
    if row is None:
        raise ValueError("当前主库不存在 admin 账户")
    user_id = row[0]
    marker = main.execute(
        "SELECT 1 FROM legacy_admin_migrations WHERE primary_user_id=?", (user_id,)
    ).fetchone()
    if marker:
        return user_id
    salt = secrets.token_bytes(16)
    password_hash = AuthenticationService._derive_password(
        password, salt, AuthenticationService.password_iterations
    ).hex()
    now = datetime.now(timezone.utc).isoformat()
    main.execute(
        "UPDATE app_users SET password_hash=?, password_salt=?, password_iterations=?, "
        "role='admin', updated_at=? WHERE user_id=?",
        (password_hash, salt.hex(), AuthenticationService.password_iterations, now, user_id),
    )
    main.execute(
        "UPDATE auth_sessions SET revoked_at=?, last_seen_at=? "
        "WHERE user_id=? AND revoked_at IS NULL",
        (now, now, user_id),
    )
    main.execute(
        "INSERT INTO legacy_admin_migrations (primary_user_id, completed_at) VALUES (?, ?)",
        (user_id, now),
    )
    return user_id


def _ensure_main_migration_table(main: sqlite3.Connection) -> None:
    main.execute(
        "CREATE TABLE IF NOT EXISTS legacy_admin_migrations ("
        "primary_user_id TEXT PRIMARY KEY, completed_at TEXT NOT NULL)"
    )


def _link_primary_admin(
    connection: sqlite3.Connection, *, primary_user_id: str, target_user_id: int
) -> None:
    existing = connection.execute(
        "SELECT user_id FROM external_identity_links "
        "WHERE provider='competition_app' AND external_user_id=?",
        (primary_user_id,),
    ).fetchone()
    if existing and existing[0] != target_user_id:
        previous_user_id = existing[0]
        expected_username = "core_" + hashlib.sha256(
            primary_user_id.encode("utf-8")
        ).hexdigest()[:32]
        previous = connection.execute(
            "SELECT username FROM users WHERE id=?", (previous_user_id,)
        ).fetchone()
        if previous is None or previous[0] != expected_username:
            raise ValueError("主管理员已连接到未知兼容用户，拒绝重绑定")
        for table in ("sessions", "learning_activity_records", "mistake_records"):
            connection.execute(
                f"UPDATE {table} SET user_id=? WHERE user_id=?",
                (target_user_id, previous_user_id),
            )
    linked_identity = connection.execute(
        "SELECT external_user_id FROM external_identity_links WHERE user_id=?",
        (target_user_id,),
    ).fetchone()
    if linked_identity and linked_identity[0] != primary_user_id:
        raise ValueError("旧管理员兼容用户已连接到其他主账号，拒绝合并")
    connection.execute(
        "INSERT INTO external_identity_links (provider, external_user_id, user_id) "
        "VALUES ('competition_app', ?, ?) "
        "ON CONFLICT(provider, external_user_id) DO UPDATE SET user_id=excluded.user_id",
        (primary_user_id, target_user_id),
    )


def _copy_sessions(source: sqlite3.Connection, target: sqlite3.Connection) -> int:
    imported = 0
    for row in source.execute("SELECT * FROM sessions ORDER BY id"):
        if _mapped_record_id(target, "sessions", row["id"]):
            continue
        user = target.execute(
            "SELECT target_user_id FROM legacy_user_mappings WHERE legacy_user_id=?",
            (row["user_id"],),
        ).fetchone()
        if user is None:
            raise ValueError(f"会话引用了未映射用户: {row['user_id']}")
        target_id = "LEGACY_" + hashlib.sha256(row["id"].encode("utf-8")).hexdigest()[:28]
        collision = target.execute("SELECT 1 FROM sessions WHERE id=?", (target_id,)).fetchone()
        if collision:
            raise ValueError(f"目标兼容库存在冲突会话 ID: {target_id}")
        target.execute(
            "INSERT INTO sessions "
            "(id, user_id, title, title_auto_enabled, active_leaf_message_id, created_at) "
            "VALUES (?, ?, ?, ?, NULL, ?)",
            (target_id, user[0], row["title"], row["title_auto_enabled"], row["created_at"]),
        )
        _record_mapping(target, "sessions", row["id"], target_id)
        imported += 1
    return imported


def _copy_messages(source: sqlite3.Connection, target: sqlite3.Connection) -> int:
    imported = 0
    pending_parents: list[tuple[int, int | None]] = []
    for row in source.execute("SELECT * FROM messages ORDER BY id"):
        if _mapped_record_id(target, "messages", row["id"]):
            continue
        session_id = _mapped_record_id(target, "sessions", row["session_id"])
        if session_id is None:
            raise ValueError(f"消息引用了未映射会话: {row['session_id']}")
        cursor = target.execute(
            "INSERT INTO messages (session_id, parent_id, role, content, files, timestamp, created_at) "
            "VALUES (?, NULL, ?, ?, ?, ?, ?)",
            (session_id, row["role"], row["content"], row["files"], row["timestamp"], row["created_at"]),
        )
        target_message_id = int(cursor.lastrowid)
        _record_mapping(target, "messages", row["id"], target_message_id)
        pending_parents.append((target_message_id, row["parent_id"]))
        imported += 1
    for target_message_id, legacy_parent_id in pending_parents:
        if legacy_parent_id is None:
            continue
        parent_id = _mapped_record_id(target, "messages", legacy_parent_id)
        if parent_id is None:
            raise ValueError(f"消息引用了未映射父消息: {legacy_parent_id}")
        target.execute(
            "UPDATE messages SET parent_id=? WHERE id=?",
            (parent_id, target_message_id),
        )
    for session in source.execute("SELECT id, active_leaf_message_id FROM sessions"):
        if session["active_leaf_message_id"] is None:
            continue
        target_session_id = _mapped_record_id(target, "sessions", session["id"])
        target_leaf_id = _mapped_record_id(target, "messages", session["active_leaf_message_id"])
        if target_session_id is None or target_leaf_id is None:
            raise ValueError(f"会话存在未映射激活叶子: {session['id']}")
        target.execute(
            "UPDATE sessions SET active_leaf_message_id=? WHERE id=?",
            (target_leaf_id, target_session_id),
        )
    return imported


def _copy_activities(source: sqlite3.Connection, target: sqlite3.Connection) -> int:
    imported = 0
    for row in source.execute("SELECT * FROM learning_activity_records ORDER BY id"):
        if _mapped_record_id(target, "learning_activity_records", row["id"]):
            continue
        user = target.execute(
            "SELECT target_user_id FROM legacy_user_mappings WHERE legacy_user_id=?",
            (row["user_id"],),
        ).fetchone()
        if user is None:
            continue
        cursor = target.execute(
            "INSERT INTO learning_activity_records "
            "(user_id, activity_type, resource_id, resource_type, duration_minutes, "
            "completion_status, score, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user[0], row["activity_type"], row["resource_id"], row["resource_type"],
                row["duration_minutes"], row["completion_status"], row["score"],
                row["payload_json"], row["created_at"],
            ),
        )
        _record_mapping(target, "learning_activity_records", row["id"], cursor.lastrowid)
        imported += 1
    return imported


def _copy_mistakes(source: sqlite3.Connection, target: sqlite3.Connection) -> int:
    imported = 0
    for row in source.execute("SELECT * FROM mistake_records ORDER BY id"):
        if _mapped_record_id(target, "mistake_records", row["id"]):
            continue
        user = target.execute(
            "SELECT target_user_id FROM legacy_user_mappings WHERE legacy_user_id=?",
            (row["user_id"],),
        ).fetchone()
        if user is None:
            continue
        cursor = target.execute(
            "INSERT INTO mistake_records "
            "(user_id, question_id, attempt_item_id, question_version_id, kp_ids_json, "
            "error_type, summary, status, created_at, updated_at) "
            "VALUES (?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)",
            (
                user[0], row["question_id"], row["kp_ids_json"], row["error_type"],
                row["summary"], row["status"], row["created_at"], row["updated_at"],
            ),
        )
        _record_mapping(target, "mistake_records", row["id"], cursor.lastrowid)
        imported += 1
    return imported


def migrate_legacy_handoff(
    *,
    legacy_db: Path,
    main_db: Path,
    handoff_db: Path,
    admin_password: str,
) -> dict[str, int]:
    if not isinstance(admin_password, str) or len(admin_password) < 8:
        raise ValueError("管理员新密码至少需要 8 个字符")
    source_fingerprint = _source_fingerprint(legacy_db)
    legacy_uri = f"file:{legacy_db.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(legacy_uri, uri=True) as source, sqlite3.connect(handoff_db) as target:
        source.row_factory = sqlite3.Row
        target.row_factory = sqlite3.Row
        target.execute("PRAGMA foreign_keys=ON")
        _ensure_mapping_tables(target)
        _validate_source(target, source, source_fingerprint)
        users_mapped = 0
        admin_target_user_id: int | None = None
        for legacy_user in source.execute("SELECT * FROM users ORDER BY id"):
            target_user_id = _target_user_id(target, legacy_user)
            if legacy_user["username"] == "admin":
                admin_target_user_id = target_user_id
            existing = target.execute(
                "SELECT 1 FROM legacy_user_mappings WHERE legacy_user_id=?", (legacy_user["id"],)
            ).fetchone()
            if not existing:
                target.execute(
                    "INSERT INTO legacy_user_mappings "
                    "(legacy_user_id, target_user_id, primary_user_id, legacy_username, migrated_at) "
                    "VALUES (?, ?, NULL, ?, ?)",
                    (
                        legacy_user["id"], target_user_id,
                        legacy_user["username"], datetime.now(timezone.utc).isoformat(),
                    ),
                )
                users_mapped += 1
        if admin_target_user_id is None:
            raise ValueError("旧库不存在 admin 账户")
        result = {
            "users_mapped": users_mapped,
            "sessions_imported": _copy_sessions(source, target),
            "messages_imported": _copy_messages(source, target),
            "activities_imported": _copy_activities(source, target),
            "mistakes_imported": _copy_mistakes(source, target),
        }
        target.commit()

    with sqlite3.connect(main_db) as main, sqlite3.connect(handoff_db) as target:
        main.row_factory = sqlite3.Row
        target.execute("PRAGMA foreign_keys=ON")
        _ensure_main_migration_table(main)
        primary_user_id = _promote_admin(main, admin_password)
        target.execute(
            "UPDATE legacy_user_mappings SET primary_user_id=? "
            "WHERE legacy_username='admin' AND primary_user_id IS NULL",
            (primary_user_id,),
        )
        _link_primary_admin(
            target, primary_user_id=primary_user_id, target_user_id=admin_target_user_id
        )
        target.commit()
        main.commit()
        return result

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ROOT = BACKEND_ROOT / "knowledge_atlas_runtime" / "reconciliation"


class ReconciliationSafetyError(RuntimeError):
    """Raised before a reconciliation that cannot prove its no-delete contract."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sqlite_path_from_url(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ReconciliationSafetyError(
            "apply requires a SQLite backup; non-SQLite databases need an operator-managed backup"
        )
    raw = database_url[len(prefix) :]
    if not raw or raw == ":memory:":
        raise ReconciliationSafetyError("apply requires a persistent SQLite database")
    return Path(raw).resolve()


def backup_sqlite_database(
    database_path: str | Path,
    backup_dir: str | Path,
    *,
    stamp: str | None = None,
) -> dict[str, Any]:
    source = Path(database_path).resolve()
    if not source.is_file():
        raise ReconciliationSafetyError(f"SQLite database does not exist: {source}")
    destination_root = Path(backup_dir).resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    destination = destination_root / f"{source.stem}.pre-atlas-{stamp or _stamp()}{source.suffix or '.db'}"
    if destination.exists():
        raise ReconciliationSafetyError(f"backup already exists; refusing overwrite: {destination}")
    source_connection = sqlite3.connect(str(source))
    backup_connection = sqlite3.connect(str(destination))
    try:
        source_connection.backup(backup_connection)
    finally:
        backup_connection.close()
        source_connection.close()
    return {
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "sha256": _sha256_file(destination),
    }


def _write_report(report: dict[str, Any], report_dir: Path, stamp: str) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    destination = report_dir / f"question-reconciliation-{stamp}.json"
    if destination.exists():
        raise ReconciliationSafetyError(f"report already exists; refusing overwrite: {destination}")
    temporary = destination.with_name(f"{destination.name}.tmp-{os.getpid()}")
    report["report_path"] = str(destination)
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination


def _assert_no_delete(report: dict[str, Any], *, phase: str) -> None:
    if int(report.get("deleted") or 0) != 0:
        raise ReconciliationSafetyError(f"{phase} would delete question data; reconciliation aborted")
    atlas_total = int(report.get("atlas_total") or 0)
    matched = int(report.get("matched") or 0)
    if atlas_total <= 0 or matched != atlas_total:
        raise ReconciliationSafetyError(
            f"{phase} cannot prove complete question_id coverage: matched={matched}, atlas_total={atlas_total}"
        )
    if atlas_total == 93111:
        linked = int(report.get("atlas_linked") or 0)
        pending = int(report.get("atlas_pending_link") or 0)
        if (linked, pending) != (71102, 22009):
            raise ReconciliationSafetyError(
                f"Atlas count contract mismatch: linked={linked}, pending={pending}"
            )


def reconcile_with_backup(
    *,
    db: Any,
    atlas_store: Any,
    apply: bool,
    database_url: str,
    backup_dir: str | Path,
    report_dir: str | Path,
    stamp: str | None = None,
) -> dict[str, Any]:
    run_stamp = stamp or _stamp()
    dry_run = atlas_store.reconcile_questions(db, apply=False)
    _assert_no_delete(dry_run, phase="dry-run")
    report: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "backup": None,
        "applied": None,
    }
    if apply:
        database_path = sqlite_path_from_url(database_url)
        report["backup"] = backup_sqlite_database(database_path, backup_dir, stamp=run_stamp)
        applied = atlas_store.reconcile_questions(db, apply=True)
        _assert_no_delete(applied, phase="apply")
        if int(applied.get("db_total") or 0) != int(dry_run.get("db_total") or 0):
            raise ReconciliationSafetyError("database question count changed during reconciliation")
        report["applied"] = applied
    destination = _write_report(report, Path(report_dir), run_stamp)
    report["report_path"] = str(destination)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按 question_id 对账 Atlas 题库；apply 前自动创建一致性 SQLite 备份"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_RUNTIME_ROOT / "backups")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_RUNTIME_ROOT / "reports")
    args = parser.parse_args()

    from APP.backend.config import SQLALCHEMY_DATABASE_URL
    from APP.backend.database import SessionLocal
    from APP.backend.knowledge_atlas_service import atlas_service

    try:
        with SessionLocal() as db:
            report = reconcile_with_backup(
                db=db,
                atlas_store=atlas_service,
                apply=args.apply,
                database_url=SQLALCHEMY_DATABASE_URL,
                backup_dir=args.backup_dir,
                report_dir=args.report_dir,
            )
    except (ReconciliationSafetyError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, **report}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

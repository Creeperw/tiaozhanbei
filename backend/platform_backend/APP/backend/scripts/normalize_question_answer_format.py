"""Normalize legacy JSON-array-encoded standard answers in the question bank.

Root cause: ``daily_task_progress_service`` reused one ``json.dumps`` value for
both the JSON mirror column (``learning_questions.answer_json``) and the
plain-text columns (``question_bank_items.answer`` /
``question_version_records.answer``).  This script rewrites only the plain-text
columns; the JSON mirror is already correct and is left untouched.

Usage:
    python normalize_question_answer_format.py            # dry run
    python normalize_question_answer_format.py --apply    # write changes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pymysql


TABLES = ("question_bank_items", "question_version_records")


def plain_answer(value: str | None) -> str | None:
    """Return the plain-text form of a JSON-array-encoded answer, else None."""

    raw = (value or "").strip()
    if not (raw.startswith("[") and raw.endswith("]")):
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, list) or not parsed:
        return None
    if not all(isinstance(item, str) for item in parsed):
        return None
    return ", ".join(item for item in parsed if item)


def connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("MYSQL_PORT", 3306)),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["BACKEND_HANDOFF_MYSQL_DATABASE"],
        charset="utf8mb4",
        autocommit=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="persist the changes")
    args = parser.parse_args()

    connection = connect()
    backup: list[dict[str, object]] = []
    try:
        cursor = connection.cursor()
        for table in TABLES:
            cursor.execute(f"SELECT id, answer FROM {table}")
            changes = []
            for row_id, answer in cursor.fetchall():
                fixed = plain_answer(answer)
                if fixed is None:
                    continue
                changes.append((row_id, answer, fixed))
            print(f"[{table}] rows needing normalization: {len(changes)}")
            for row_id, before, after in changes[:8]:
                print(f"   id={row_id}  {before!r} -> {after!r}")
            if len(changes) > 8:
                print(f"   ... and {len(changes) - 8} more")
            backup.append({
                "table": table,
                "rows": [{"id": r, "answer": b} for r, b, _ in changes],
            })
            if args.apply:
                cursor.executemany(
                    f"UPDATE {table} SET answer=%s WHERE id=%s",
                    [(after, row_id) for row_id, _, after in changes],
                )

        if args.apply:
            connection.commit()
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            out = Path(f"/tmp/question-answer-format-backup-{stamp}.json")
            out.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"applied; backup written to {out}")
        else:
            connection.rollback()
            print("dry run only; nothing written (pass --apply to persist)")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

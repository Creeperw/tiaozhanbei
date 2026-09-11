"""Rewrite the onboarding survey memory as readable natural-language text.

``submit_onboarding_survey`` used to store the raw survey JSON as the content of
the ``personalization_memories`` row and title it 「Onboarding Survey」.  That row
is shown in the 学习记忆 list *and* is rendered into the agents'
「长期偏好与背景」 context by ``memory_agent_service._render_memory_brief``, so a
learner saw a JSON blob and the agents were fed one too.

The writer now renders natural language (``diagnosis_agent_service``); this script
brings existing rows in line.  ``survey_answers`` inside the stored payload is
already normalised, which is exactly the flat shape the renderer expects, so no
re-normalisation is involved.

Usage:
    python migrate_onboarding_memory_text.py            # dry run
    python migrate_onboarding_memory_text.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pymysql

# The memory tables live in the mounted business database, not in the main one.
# ``config.SQLALCHEMY_DATABASE_URL`` prefers ``DATABASE_URL`` over the individual
# ``MYSQL_*`` variables, so drop it before importing the app modules or the
# override below would be silently ignored.
os.environ.pop("DATABASE_URL", None)
os.environ["MYSQL_DATABASE"] = os.environ.get(
    "BACKEND_HANDOFF_MYSQL_DATABASE", "competition_frontend"
)
os.environ.setdefault("USE_SQLITE", "false")

from APP.backend.diagnosis_agent_service import (  # noqa: E402
    ONBOARDING_MEMORY_SOURCE,
    ONBOARDING_MEMORY_TITLE,
    _render_onboarding_memory_text,
)

TABLE = "personalization_memories"
TITLE = ONBOARDING_MEMORY_TITLE
SOURCE = ONBOARDING_MEMORY_SOURCE


def connect():
    return pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("MYSQL_PORT", 3306)),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        charset="utf8mb4",
        autocommit=False,
    )


def render(content: str) -> str | None:
    """Return the readable text for a stored payload, or None if it is not one."""
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    answers = payload.get("survey_answers")
    if not isinstance(answers, dict):
        return None
    text = _render_onboarding_memory_text(answers)
    return text or None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the changes")
    args = parser.parse_args()

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, title, content FROM {TABLE} WHERE source = %s ORDER BY id",
                (SOURCE,),
            )
            rows = cur.fetchall()

            if not rows:
                print(f"no {SOURCE} memories found")
                return 0

            print(f"found {len(rows)} {SOURCE} memory row(s)")
            updates: list[tuple[str, str, int]] = []
            for memory_id, title, content in rows:
                text = render(content or "")
                if text is None:
                    print(f"  id={memory_id} skipped (content is not a survey payload)")
                    continue
                if (title or "") == TITLE and (content or "") == text:
                    print(f"  id={memory_id} already up to date")
                    continue
                print(f"  id={memory_id} title {title!r} -> {TITLE!r}")
                print(f"    content {len(content or '')} chars -> {len(text)} chars")
                updates.append((TITLE, text, memory_id))

            if not args.apply:
                print("\ndry run; pass --apply to write")
                return 0

            backup = Path(
                "/tmp/onboarding-memory-backup-"
                + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                + ".json"
            )
            backup.write_text(
                json.dumps(
                    [{"id": i, "title": t, "content": c} for i, t, c in rows],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"\nbackup written to {backup}")

            if updates:
                cur.executemany(
                    f"UPDATE {TABLE} SET title = %s, content = %s, updated_at = NOW() WHERE id = %s",
                    updates,
                )
            conn.commit()
            print(f"updated {len(updates)} row(s)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

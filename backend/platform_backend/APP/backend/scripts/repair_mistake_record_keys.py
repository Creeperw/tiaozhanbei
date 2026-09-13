"""Repair the identity columns on ``mistake_records``.

Two defects are repaired, and both repairs are idempotent:

1. ``question_id`` held the question *version* id for rows written by the
   grading writeback path. Knowledge-atlas versions are named
   ``<question_id>:atlas:<hash>``, so the mistake detail lookup — which
   resolves a question by its question id — missed, and the mistake drawer
   showed neither the options nor the standard answer. The authoritative
   question id is read from ``question_version_records``; nothing is inferred
   from the shape of the id.

2. ``first_attempt_item_id`` did not exist, so every later wrong attempt
   overwrote the attempt pointer while the interface kept labelling it
   "首次作答". The earliest attempt per (learner, question) is restored, so
   the learner once again sees the answer they originally gave.

The script is read-only unless ``--apply`` is passed, and it reports what it
would change before touching anything.

Usage (inside the backend package root, with the application environment
loaded)::

    python -m APP.backend.scripts.repair_mistake_record_keys
    python -m APP.backend.scripts.repair_mistake_record_keys --apply
"""

from __future__ import annotations

import argparse
import os

from sqlalchemy import inspect
from sqlalchemy.orm import Session


def _resolve_target_database() -> str:
    """Align the repair with the database the learner workspace is served from.

    The workspace reaches its tables through the handoff layer, which keeps its
    data in ``BACKEND_HANDOFF_MYSQL_DATABASE`` while ``MYSQL_DATABASE`` names the
    main application database. Importing the models without aligning the two
    silently connects to the empty schema, and the run then reports "nothing to
    repair" instead of failing. An explicit ``DATABASE_URL`` always wins.
    """

    if (os.getenv("DATABASE_URL") or "").strip():
        return "DATABASE_URL"
    handoff = (os.getenv("BACKEND_HANDOFF_MYSQL_DATABASE") or "").strip()
    current = (os.getenv("MYSQL_DATABASE") or "").strip()
    if handoff and handoff != current:
        os.environ["MYSQL_DATABASE"] = handoff
    return os.environ.get("MYSQL_DATABASE", "")


TARGET_DATABASE = _resolve_target_database()

from APP.backend.database import (  # noqa: E402  (import after the env is aligned)
    LearningAttemptItemRecord,
    LearningAttemptRecord,
    MistakeRecord,
    QuestionVersionRecord,
    SessionLocal,
)

BATCH_SIZE = 200


def _require_snapshot_column(db: Session) -> None:
    """Fail loudly when the additive schema migration has not run yet.

    The repair reads ``first_attempt_item_id``; without the column the failure
    would otherwise surface as an opaque SQL error.
    """

    columns = {
        column["name"] for column in inspect(db.get_bind()).get_columns("mistake_records")
    }
    if "first_attempt_item_id" not in columns:
        raise SystemExit(
            "mistake_records.first_attempt_item_id is missing; start the backend "
            "once so the additive schema migration adds it, then re-run."
        )


def _authoritative_question_ids(db: Session) -> dict[str, str]:
    """Map question version id -> authoritative question id."""

    return {
        str(version_id): str(question_id)
        for version_id, question_id in db.query(
            QuestionVersionRecord.question_version_id,
            QuestionVersionRecord.question_id,
        ).all()
        if version_id and question_id
    }


def repair_question_ids(db: Session, *, apply: bool) -> dict[str, int]:
    """Point ``question_id`` at the question instead of the version."""

    version_to_question = _authoritative_question_ids(db)
    scanned = 0
    repairable = 0
    skipped = 0
    samples: list[str] = []

    for mistake in db.query(MistakeRecord).order_by(MistakeRecord.id.asc()).all():
        scanned += 1
        version_id = str(mistake.question_version_id or "")
        authoritative = version_to_question.get(version_id)
        if not authoritative:
            # The version is unknown, so the question id cannot be derived from
            # an authoritative source. Leave the row untouched rather than
            # guessing from the id format.
            skipped += 1
            continue
        if str(mistake.question_id or "") == authoritative:
            continue
        repairable += 1
        if len(samples) < 5:
            samples.append(f"  id={mistake.id}: {mistake.question_id} -> {authoritative}")
        if apply:
            mistake.question_id = authoritative

    if apply and repairable:
        db.commit()
    else:
        db.rollback()

    for line in samples:
        print(line)
    print(
        f"question_id: scanned={scanned} repairable={repairable} "
        f"skipped_no_version={skipped}"
    )
    return {"scanned": scanned, "repaired": repairable, "skipped": skipped}


def _earliest_attempt_item_id(
    db: Session, *, learner_id: int, question_id: str
) -> str | None:
    """Earliest attempt the learner made on the question, across versions."""

    row = (
        db.query(LearningAttemptItemRecord.attempt_item_id)
        .join(
            LearningAttemptRecord,
            LearningAttemptRecord.attempt_id == LearningAttemptItemRecord.attempt_id,
        )
        .join(
            QuestionVersionRecord,
            QuestionVersionRecord.question_version_id
            == LearningAttemptItemRecord.question_version_id,
        )
        .filter(
            LearningAttemptRecord.learner_id == learner_id,
            QuestionVersionRecord.question_id == question_id,
        )
        .order_by(
            LearningAttemptItemRecord.created_at.asc(),
            LearningAttemptItemRecord.attempt_item_id.asc(),
        )
        .first()
    )
    return str(row[0]) if row is not None else None


def repair_first_attempts(db: Session, *, apply: bool) -> dict[str, int]:
    """Restore the immutable first-attempt snapshot on rows that lack it."""

    pending = (
        db.query(MistakeRecord)
        .filter(MistakeRecord.first_attempt_item_id.is_(None))
        .order_by(MistakeRecord.id.asc())
        .all()
    )
    repairable = 0
    unresolved = 0
    samples: list[str] = []

    for index, mistake in enumerate(pending, start=1):
        earliest = _earliest_attempt_item_id(
            db,
            learner_id=int(mistake.user_id),
            question_id=str(mistake.question_id or ""),
        )
        if not earliest:
            unresolved += 1
            continue
        repairable += 1
        if len(samples) < 5:
            samples.append(
                f"  id={mistake.id}: first_attempt_item_id -> {earliest}"
            )
        if apply:
            mistake.first_attempt_item_id = earliest
            if index % BATCH_SIZE == 0:
                db.commit()

    if apply and repairable:
        db.commit()
    else:
        db.rollback()

    for line in samples:
        print(line)
    print(
        f"first_attempt_item_id: pending={len(pending)} repairable={repairable} "
        f"unresolved={unresolved}"
    )
    return {
        "pending": len(pending),
        "repaired": repairable,
        "unresolved": unresolved,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the repairs; without it the script only reports",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        _require_snapshot_column(db)
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"[{mode}] repairing mistake_records on {TARGET_DATABASE or '<default>'}")
        repair_question_ids(db, apply=args.apply)
        repair_first_attempts(db, apply=args.apply)
        print(f"[{mode}] done")
    finally:
        db.close()


if __name__ == "__main__":
    main()

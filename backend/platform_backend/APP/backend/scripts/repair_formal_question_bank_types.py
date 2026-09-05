from __future__ import annotations

import argparse
import json
from pathlib import Path

from APP.backend.database import QuestionBankItem, SessionLocal
from APP.backend.question_bank_import_service import _question_type


DEFAULT_METADATA_PATH = Path(__file__).resolve().parents[1] / "vdb_store" / "indexes" / "题库" / "metadata.jsonl"


def repair_question_types(db, metadata_path: str | Path, *, dry_run: bool) -> dict[str, int | bool]:
    source_types = {}
    with Path(metadata_path).open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            record = json.loads(line)
            original = record.get("original") if isinstance(record, dict) else None
            if not isinstance(original, dict):
                continue
            question_id = str(original.get("题目id") or "").strip()
            if question_id:
                source_types[question_id] = _question_type(original.get("题型"))

    updated_count = 0
    question_ids = sorted(source_types)
    for start in range(0, len(question_ids), 500):
        batch = question_ids[start:start + 500]
        rows = db.query(QuestionBankItem).filter(
            QuestionBankItem.question_id.in_(batch),
            QuestionBankItem.status == "pending_link",
        ).all()
        for row in rows:
            question_type = source_types[row.question_id]
            if row.question_type != question_type:
                updated_count += 1
                if not dry_run:
                    row.question_type = question_type
    if not dry_run:
        db.flush()
    return {"updated_count": updated_count, "dry_run": dry_run}


def main() -> int:
    parser = argparse.ArgumentParser(description="修复正式向量题库的题型映射")
    parser.add_argument("--metadata-path", default=str(DEFAULT_METADATA_PATH))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        report = repair_question_types(db, args.metadata_path, dry_run=args.dry_run)
        if args.apply:
            db.commit()
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

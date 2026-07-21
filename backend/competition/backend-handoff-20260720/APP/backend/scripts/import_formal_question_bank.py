from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from APP.backend.database import FormalContentImportBatch, QuestionBankItem, SessionLocal
from APP.backend.question_bank_import_service import import_question_bank_metadata


DEFAULT_METADATA_PATH = Path(__file__).resolve().parents[1] / "vdb_store" / "indexes" / "题库" / "metadata.jsonl"


def run_import(db, metadata_path: str | Path, *, dry_run: bool) -> dict[str, int | bool]:
    path = Path(metadata_path)
    if not dry_run:
        summary = import_question_bank_metadata(db, metadata_path=path)
        return {
            "created_count": summary.created_count,
            "skipped_count": summary.skipped_count,
            "invalid_count": summary.invalid_count,
            "unlinked_count": summary.unlinked_count,
            "dry_run": False,
        }
    existing_ids = {question_id for question_id, in db.query(QuestionBankItem.question_id).all()}
    valid_ids = set()
    invalid_count = 0
    with path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            record = json.loads(line)
            original = record.get("original") if isinstance(record, dict) else None
            question_id = str(original.get("题目id") or "").strip() if isinstance(original, dict) else ""
            stem = str(original.get("题目内容") or "").strip() if isinstance(original, dict) else ""
            if not question_id or not stem:
                invalid_count += 1
            else:
                valid_ids.add(question_id)
    created_count = len(valid_ids - existing_ids)
    skipped_count = len(valid_ids & existing_ids)
    report = {
        "created_count": created_count,
        "skipped_count": skipped_count,
        "invalid_count": invalid_count,
        "unlinked_count": created_count,
        "dry_run": dry_run,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="导入正式题库向量元数据")
    parser.add_argument("--metadata-path", default=str(DEFAULT_METADATA_PATH))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        report = run_import(db, args.metadata_path, dry_run=args.dry_run)
        if args.apply:
            db.commit()
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

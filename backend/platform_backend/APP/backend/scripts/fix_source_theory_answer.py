"""Correct the wrong reference answer in the atlas question source file.

``question_bank_items`` was corrected in the database, but the atlas importer
rebuilds that table from ``formatted_questions.json`` on the next bundle sync.
The source file has to carry the same correction or the fix silently reverts.

The file is 98 MiB of pretty-printed JSON and the wrong answer occurs exactly
once, so the correction is a literal text replacement that leaves the rest of
the document byte-identical.

Usage:
    python fix_source_theory_answer.py            # dry run
    python fix_source_theory_answer.py --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

SOURCE = Path(
    "/srv/tiaozhanbei/data/tiaozhanbei_data_2026-07-29/"
    "知识库管理组件/data/backend_delivery/01_question_bank/formatted_questions.json"
)
QUESTION_ID = "generated_临床案例问答__02841e231d38"
OLD = "中医学理论体系形成的标志是《黄帝内经》。"
NEW = (
    "中医学理论体系形成的标志是《黄帝内经》《黄帝八十一难经》"
    "《伤寒杂病论》《神农本草经》四部经典著作的相继问世。"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    text = SOURCE.read_text(encoding="utf-8")
    occurrences = text.count(OLD)
    print(f"occurrences of the wrong answer: {occurrences}")
    if occurrences != 1:
        print("refusing to edit: expected exactly one occurrence")
        return 1

    updated = text.replace(OLD, NEW)
    payload = json.loads(updated)
    match = next(
        (row for row in payload if row.get("question_id") == QUESTION_ID), None
    )
    print("question_id :", QUESTION_ID)
    print("answer now  :", json.dumps(match.get("answer"), ensure_ascii=False))
    print("total rows  :", len(payload))

    if not args.apply:
        print("dry run only; nothing written (pass --apply to persist)")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = Path(f"/tmp/formatted-questions-backup-{stamp}.json")
    shutil.copy2(SOURCE, backup)
    SOURCE.write_text(updated, encoding="utf-8")
    print(f"applied; backup written to {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

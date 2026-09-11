"""Correct the wrong reference answer for the theory-system-origin question.

The stored answer named only 《黄帝内经》.  The textbook states the marker is the
four classics published in succession:

    医家通过对医药经验的总结提升，形成了中医学的理论体系，其标志是《黄帝内经》
    《黄帝八十一难经》《伤寒杂病论》《神农本草经》四部经典著作的相继问世。

The question is also imported into ``question_version_records``, so both plain
text columns are corrected together.

Usage:
    python fix_theory_system_answer.py            # dry run
    python fix_theory_system_answer.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pymysql

QUESTION_ID = "generated_临床案例问答__02841e231d38"
CORRECTED = (
    "中医学理论体系形成的标志是《黄帝内经》《黄帝八十一难经》"
    "《伤寒杂病论》《神农本草经》四部经典著作的相继问世。"
)
TABLES = ("question_bank_items", "question_version_records")


def connect():
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
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    connection = connect()
    backup = []
    try:
        cursor = connection.cursor()
        for table in TABLES:
            cursor.execute(
                f"SELECT id, answer FROM {table} WHERE question_id=%s", (QUESTION_ID,)
            )
            rows = cursor.fetchall()
            print(f"[{table}] matched rows: {len(rows)}")
            for row_id, answer in rows:
                print(f"   id={row_id}  before={answer!r}")
                print(f"   id={row_id}  after ={CORRECTED!r}")
                backup.append({"table": table, "id": row_id, "answer": answer})
            if args.apply and rows:
                cursor.executemany(
                    f"UPDATE {table} SET answer=%s WHERE question_id=%s",
                    [(CORRECTED, QUESTION_ID)],
                )
        if args.apply:
            connection.commit()
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            out = Path(f"/tmp/theory-system-answer-backup-{stamp}.json")
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

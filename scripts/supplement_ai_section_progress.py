"""Append only the six explicitly authorized demo completions for uid 4."""
import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import MetaData, Table, select, text

SECTIONS = {
    "SEC_1abda7f6206af937": "第一节 阴阳学说",
    "SEC_2b1e350528db47b6": "第二节 五行学说",
    "SEC_39bd7e32376652d2": "第一节 藏象概述",
    "SEC_3e17fe054b47eff4": "第二节 五脏",
    "SEC_a47fca84665d43e6": "第三节 六腑",
    "SEC_d2a7a378a70ba83c": "第四节 奇恒之腑",
}
BATCH = "ai_section_demo_20260906_v1"
EXAM = "EXAM_2025_TCM_PHYSICIAN"


def save(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, default=str, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--backup-dir")
    args = parser.parse_args()
    from inspect_practice_dates import connect, emit
    engine, settings = connect(args.pid)
    assert settings.mode == "live"
    table = Table("learning_activity_records", MetaData(), autoload_with=engine)
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            assert conn.execute(text("SELECT username FROM users WHERE id=4 FOR UPDATE")).scalar_one() == "judge_nupt_ai"
            assert conn.execute(text("SELECT exam_track_id FROM user_learning_targets WHERE user_id=4 AND is_active=1")).scalar_one() == EXAM
            before = [dict(row) for row in conn.execute(select(table).where(table.c.user_id == 4)).mappings()]
            existing = set()
            for row in before:
                payload = json.loads(row.get("payload_json") or "{}")
                if (row["activity_type"] == "textbook_section_completed"
                        and row["completion_status"] == "completed"
                        and payload.get("book") == "中医学基础"
                        and payload.get("exam_track_id") == EXAM):
                    existing.add(row["resource_id"])
            source = {}
            for row in conn.execute(select(table).where(table.c.user_id == 3, table.c.activity_type == "textbook_section_completed")).mappings():
                payload = json.loads(row["payload_json"] or "{}")
                sid = row["resource_id"]
                if sid in SECTIONS and payload.get("book") == "中医学基础":
                    assert payload["section_name"] == SECTIONS[sid]
                    source[sid] = payload
            assert set(source) == set(SECTIONS)
            planned = []
            for sid, name in SECTIONS.items():
                if sid in existing:
                    continue
                payload = {key: source[sid][key] for key in ("book", "route", "chapter_id", "chapter_name", "section_id", "section_name")}
                payload.update(exam_track_id=EXAM, source="authorized_demo_progress",
                               batch_id=BATCH, is_demo=True,
                               note="用户明确授权补记的演示教材进度；不代表测验成绩、掌握度或今日任务完成。")
                planned.append(dict(user_id=4, activity_type="textbook_section_completed",
                                    resource_id=sid, resource_type="textbook_section",
                                    completion_status="completed", score=None, duration_minutes=None,
                                    created_at=datetime.utcnow(), payload_json=json.dumps(payload, ensure_ascii=False)))
            emit({"account": "judge_nupt_ai", "exam": EXAM, "planned": len(planned),
                  "sections": [SECTIONS[row["resource_id"]] for row in planned], "commit": args.commit})
            if not args.commit or not planned:
                transaction.rollback()
                return
            assert args.backup_dir
            backup = Path(args.backup_dir)
            backup.mkdir(mode=0o700, parents=True, exist_ok=False)
            save(backup / "before-and-plan.json", {"before": before, "planned": planned})
            ids = []
            for row in planned:
                ids.append(conn.execute(table.insert().values(**row)).inserted_primary_key[0])
            after = [dict(row) for row in conn.execute(select(table).where(table.c.user_id == 4)).mappings()]
            after_by_id = {row["id"]: row for row in after}
            assert all(after_by_id[row["id"]] == row for row in before)
            assert len(after) == len(before) + len(planned)
            save(backup / "inserted-precommit.json", {"batch": BATCH, "ids": ids})
            transaction.commit()
            save(backup / "committed.json", {"batch": BATCH, "ids": ids, "count": len(ids)})
            emit({"committed_ids": ids, "backup": str(backup)})
        except BaseException:
            if transaction.is_active:
                transaction.rollback()
            raise
    engine.dispose()


if __name__ == "__main__":
    main()
"""Record an authorized whole-book completion, without inventing assessment data."""
import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import MetaData, Table, select, text

USER_ID = 5
USERNAME = "judge_njucm_pharmacy"
EXAM = "EXAM_TCM_LICENSED_PHARMACIST"
BOOK = "中医学基础"
BATCH = "pharmacy_confirmed_book_20260907_v1"
# IDs and names read from the authenticated production textbook catalogue.
CATALOG = [
    ("CH_161a46b6b9ff66c9", "绪论", [("SEC_ef60a7aed09b862f", "绪论")]),
    ("CH_a16f8e8a38cb869b", "第一章 中医学的哲学基础", [
        ("SEC_1abda7f6206af937", "第一节 阴阳学说"), ("SEC_2b1e350528db47b6", "第二节 五行学说")]),
    ("CH_b7175d6957350df6", "第二章 藏象", [
        ("SEC_39bd7e32376652d2", "第一节 藏象概述"), ("SEC_3e17fe054b47eff4", "第二节 五脏"),
        ("SEC_a47fca84665d43e6", "第三节 六腑"), ("SEC_d2a7a378a70ba83c", "第四节 奇恒之腑"),
        ("SEC_726236775c95757d", "第五节 脏腑之间的关系")]),
    ("CH_ed74b0f4a34d4cd0", "第三章 气血津液", [
        ("SEC_2a9118ae70776f1a", "第一节 气"), ("SEC_474c4ff2ea3ab5cb", "第二节 血"),
        ("SEC_7acfc9290feb8652", "第三节 津液"), ("SEC_78209b0a99f018f2", "第四节 气血津液的关系")]),
    ("CH_d21dc1dbd43189f2", "第四章 经络", [
        ("SEC_0d16da66cf8f19d8", "第一节 经络系统的概述"), ("SEC_879b092648b0e335", "第二节 十二经脉"),
        ("SEC_95a1989f89be7422", "第三节 奇经八脉"), ("SEC_677b80a803b400ee", "第四节 经络学说的应用")]),
    ("CH_9ca4d475a8492178", "第五章 体质", [
        ("SEC_9929ec5827277447", "第一节 体质学说的基本内容"), ("SEC_8d1ffcc6391eecf1", "第二节 体质学说的应用")]),
    ("CH_5a007802580c0934", "第六章 病因", [
        ("SEC_b1b09b5ac91a211d", "第一节 外感病因"), ("SEC_49070e714fbb9a15", "第二节 内伤病因"),
        ("SEC_7cdb06947d5cb343", "第三节 病理产物性病因"), ("SEC_779499c14ddc20b5", "第四节 其他病因")]),
    ("CH_de968fcb454fb70f", "第七章 病机", [
        ("SEC_6fc720b5c10062d8", "第一节 发病原理"), ("SEC_7299b2a23e73feaf", "第二节 基本病机")]),
    ("CH_94fd8ad5744fb5cc", "第八章 诊法", [
        ("SEC_592dc6a0495fa809", "第一节 望诊"), ("SEC_2604e983b6d4be8d", "第二节 闻诊"),
        ("SEC_d5ed9d01b5dece9a", "第三节 问诊"), ("SEC_e01ca4e73e2db5e3", "第四节 切诊")]),
    ("CH_5c08710c16e4e4cd", "第九章 辨证", [
        ("SEC_3e06623b6b6236d1", "第一节 八纲辨证"), ("SEC_aec1447fb4768dc1", "第二节 气血津液辨证"),
        ("SEC_bcaa769ae40b68de", "第三节 脏腑辨证"), ("SEC_89d93710fafa39a9", "第四节 外感病辨证")]),
    ("CH_8bf4dfde210b925a", "第十章 预防、治则、养生、康复", [
        ("SEC_cba6de50801936b1", "第一节 预防"), ("SEC_c4d508da9bfe4b16", "第二节 治则"),
        ("SEC_6c3e8447b338d520", "第三节 养生"), ("SEC_779a65379ef0f6a7", "第四节 康复")]),
]


def planned_rows(before, now):
    existing = set()
    for row in before:
        if row["user_id"] != USER_ID or row["activity_type"] != "textbook_section_completed":
            continue
        payload = json.loads(row.get("payload_json") or "{}")
        if (row["completion_status"] == "completed" and payload.get("book") == BOOK
                and payload.get("exam_track_id") == EXAM):
            existing.add(row["resource_id"])
    result = []
    for chapter_id, chapter_name, sections in CATALOG:
        for section_id, section_name in sections:
            if section_id in existing:
                continue
            payload = dict(book=BOOK, route="textbook_14_5", exam_track_id=EXAM,
                           chapter_id=chapter_id, chapter_name=chapter_name,
                           section_id=section_id, section_name=section_name,
                           source="authorized_user_reported_completion",
                           basis="user_confirmed_whole_book_completion", batch_id=BATCH,
                           is_demo=False, verified_assessment=False,
                           note="用户明确确认整本已学完后补记；实际学习日期未知，当前时间为补记时间；不代表测评通过、掌握度或今日任务完成。")
            result.append(dict(user_id=USER_ID, activity_type="textbook_section_completed",
                               resource_id=section_id, resource_type="textbook_section",
                               completion_status="completed", score=None, duration_minutes=None,
                               created_at=now, payload_json=json.dumps(payload, ensure_ascii=False)))
    return result


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
    assert len({sid for _, _, sections in CATALOG for sid, _ in sections}) == 36
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                assert conn.execute(text("SELECT username FROM users WHERE id=5 FOR UPDATE")).scalar_one() == USERNAME
                assert conn.execute(text("SELECT exam_track_id FROM user_learning_targets WHERE user_id=5 AND is_active=1 FOR UPDATE")).scalar_one() == EXAM
                before = [dict(row) for row in conn.execute(select(table).where(table.c.user_id == USER_ID)).mappings()]
                planned = planned_rows(before, datetime.utcnow())
                emit(dict(account=USERNAME, exam=EXAM, planned=len(planned), commit=args.commit))
                if not args.commit or not planned:
                    transaction.rollback()
                    return
                assert args.backup_dir
                backup = Path(args.backup_dir)
                backup.mkdir(mode=0o700, parents=True, exist_ok=False)
                save(backup / "before-and-plan.json", dict(batch=BATCH, before=before, planned=planned))
                ids = [conn.execute(table.insert().values(**row)).inserted_primary_key[0] for row in planned]
                after = [dict(row) for row in conn.execute(select(table).where(table.c.user_id == USER_ID)).mappings()]
                by_id = {row["id"]: row for row in after}
                assert all(by_id[row["id"]] == row for row in before)
                assert len(after) == len(before) + len(planned)
                assert planned_rows(after, datetime.utcnow()) == []
                save(backup / "inserted-precommit.json", dict(batch=BATCH, ids=ids))
                transaction.commit()
                save(backup / "committed.json", dict(batch=BATCH, ids=ids, count=len(ids)))
                emit(dict(committed=len(ids), ids=ids, backup=str(backup)))
            except BaseException:
                if transaction.is_active:
                    transaction.rollback()
                raise
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""把知识图谱的知识点同步进平台的 ``knowledge_points`` 与 ``kp`` 表。

平台用 ``knowledge_points`` 判定一个知识点是否"已知"：表里没有的 id 会被记成
待审候选，永远进不了掌握度。知识图谱有七万多个知识点，而这张表此前只积累了日常
任务路径顺手写入的几十个，于是试卷的知识点被整批判定未知，掌握度与复习闭环静默
停摆——做题、批改都正常，个人数据却再也不更新。

本脚本把图谱镜像进数据库，作为可重复执行的同步手段（而不是一次性灌数据）：

* 幂等——图谱更新后重跑即可刷新，不产生重复行；
* 不越权——题库、种子目录、智能体试卷各自拥有自己的知识点身份，图谱不改写它们；
* 流式——逐条解析 72 MB 图谱文件，内存占用与知识点数量无关。

用法::

    USE_SQLITE=false MYSQL_DATABASE=competition_frontend \\
    DATABASE_URL='mysql+pymysql://user:pass@host:3306/competition_frontend?charset=utf8mb4' \\
        /srv/tiaozhanbei/.venv/bin/python \\
        APP/backend/scripts/sync_knowledge_atlas_points.py \\
        --atlas <backend_delivery>/04_knowledge_points/final_knowledge_points.json

先加 ``--dry-run`` 看统计，确认无误后去掉即可真正写入。

体检（不写入）::

    ... --verify --verify-days 7

体检会报告最近若干天试卷里出现过的知识点有多大比例能通过准入判定——这是判断
"掌握度与复习闭环是否真的恢复"最直接的指标。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator

# scripts/ 位于 APP/backend/ 下，平台后端根目录是 platform_backend/，需要它才能
# ``import APP``。
_PLATFORM_BACKEND_ROOT = Path(__file__).resolve().parents[3]
if str(_PLATFORM_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_BACKEND_ROOT))

from APP.backend.database import (  # noqa: E402
    KnowledgePoint,
    LearningKnowledgePoint,
    SessionLocal,
)
from sqlalchemy import func  # noqa: E402

# 与 daily_task_progress_service.KNOWLEDGE_ATLAS_SOURCE 保持同一取值：来源前缀
# 必须落在 knowledge_point_identity_service.FORMAL_SOURCE_PREFIXES 内，否则图谱
# 知识点会被当作智能体自造内容而拒绝准入。
ATLAS_SOURCE = "formal-content:knowledge-atlas-2026-07-18"
# 与 daily_task_progress_service.ensure_executable_knowledge_bundle 一致：图谱的
# other_name 用中文分号、顿号分隔别名。
_ALIAS_SEPARATOR = re.compile(r"[；;、]")
# ``knowledge_points.name`` 的列宽。
_NAME_LIMIT = 200
# 每批写入的行数：部署机内存偏紧，分批提交把峰值压在可控范围内。
BATCH_SIZE = 500


def iter_atlas_records(
    path: Path,
    *,
    chunk_size: int = 1 << 20,
) -> Iterator[dict[str, Any]]:
    """逐条产出图谱数组里的 ``kp`` 载荷。

    图谱是 72 MB 的 JSON 数组，整文件 ``json.load`` 会让常驻内存涨到数百 MB。
    这里用 ``JSONDecoder.raw_decode`` 增量解码：只在当前缓冲区里解一个对象，
    数据不够时再读下一块，内存占用与记录数无关。
    """

    decoder = json.JSONDecoder()
    with path.open(encoding="utf-8") as handle:
        buffer = ""
        index = 0
        # 先把数组起始的 ``[`` 读进来，之后 index 始终指向待解析位置。
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return
            buffer += chunk
            offset = len(buffer) - len(buffer.lstrip())
            if buffer[offset : offset + 1] == "[":
                index = offset + 1
                break
            if buffer.strip():
                raise ValueError(f"知识图谱文件不是 JSON 数组：{path}")
        while True:
            # 跳过元素之间的空白与逗号。
            while index < len(buffer) and buffer[index] in " \t\r\n,":
                index += 1
            if index < len(buffer) and buffer[index] == "]":
                return
            if index >= len(buffer):
                chunk = handle.read(chunk_size)
                if not chunk:
                    return
                buffer = buffer[index:] + chunk
                index = 0
                continue
            try:
                record, index = decoder.raw_decode(buffer, index)
            except ValueError:
                # 缓冲区在对象中间被截断，补齐数据后重试；读到文件尾仍失败才
                # 说明文件本身有问题。
                chunk = handle.read(chunk_size)
                if not chunk:
                    raise
                buffer = buffer[index:] + chunk
                index = 0
                continue
            if isinstance(record, dict):
                payload = record.get("kp", record)
                if isinstance(payload, dict):
                    yield payload


def _aliases(payload: dict[str, Any]) -> list[str]:
    """把图谱的 ``other_name`` 拆成别名列表。"""

    return [
        value.strip()
        for value in _ALIAS_SEPARATOR.split(str(payload.get("other_name") or ""))
        if value.strip()
    ]


def _description(payload: dict[str, Any]) -> str:
    """用一级、二级分类描述知识点的归属，与日常任务路径写法一致。"""

    return " / ".join(
        filter(
            None,
            (
                str(payload.get("kp_lv1") or "").strip(),
                str(payload.get("kp_lv2") or "").strip(),
            ),
        )
    )


def _is_atlas_source(source: str) -> bool:
    return source.startswith("formal-content:knowledge-atlas")


def sync_atlas_points(
    db,
    atlas_path: Path,
    *,
    limit: int = 0,
    batch_size: int = BATCH_SIZE,
    dry_run: bool = False,
) -> dict[str, int]:
    """把图谱知识点镜像进 ``knowledge_points`` 与 ``kp`` 表。"""

    stats = {
        "scanned": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "invalid": 0,
    }
    # 一次取出两张表的现状，避免逐条查询；写入过程中同步维护，保证同一次运行内
    # 不会对同一 id 重复插入。
    point_sources = {
        str(kp_id): str(source or "")
        for kp_id, source in db.query(
            KnowledgePoint.kp_id, KnowledgePoint.source
        ).all()
    }
    mirror_ids = {
        str(kp_id) for (kp_id,) in db.query(LearningKnowledgePoint.kp_id).all()
    }

    pending = 0
    for payload in iter_atlas_records(atlas_path):
        if limit and stats["scanned"] >= limit:
            break
        stats["scanned"] += 1

        kp_id = str(payload.get("kp_id") or "").strip()
        name = str(payload.get("kp_lv3") or "").strip()
        if not kp_id or not name:
            stats["invalid"] += 1
            continue

        existing_source = point_sources.get(kp_id)
        if existing_source is not None and not _is_atlas_source(existing_source):
            # 题库、种子目录、智能体试卷各自拥有自己的知识点身份，图谱不改写。
            stats["skipped"] += 1
            continue

        aliases = _aliases(payload)
        description = _description(payload)
        if existing_source is None:
            db.add(
                KnowledgePoint(
                    kp_id=kp_id,
                    name=name[:_NAME_LIMIT],
                    aliases_json=json.dumps(aliases, ensure_ascii=False),
                    description=description,
                    source=ATLAS_SOURCE,
                    status="active",
                )
            )
            point_sources[kp_id] = ATLAS_SOURCE
            stats["created"] += 1
        else:
            # 图谱是这些行的唯一真相来源，重跑即刷新。
            db.query(KnowledgePoint).filter_by(kp_id=kp_id).update(
                {
                    "name": name[:_NAME_LIMIT],
                    "aliases_json": json.dumps(aliases, ensure_ascii=False),
                    "description": description,
                    "status": "active",
                },
                synchronize_session=False,
            )
            stats["updated"] += 1

        if kp_id not in mirror_ids:
            db.add(
                LearningKnowledgePoint(
                    kp_id=kp_id,
                    kp_lv1=str(payload.get("kp_lv1") or ""),
                    kp_lv2=str(payload.get("kp_lv2") or ""),
                    kp_lv3=name,
                    raw_content=json.dumps(
                        payload.get("raw_content") or [], ensure_ascii=False
                    ),
                    other_name_json=json.dumps(aliases, ensure_ascii=False),
                    order_json=json.dumps(
                        {"order_code": payload.get("order")}, ensure_ascii=False
                    ),
                )
            )
            mirror_ids.add(kp_id)

        pending += 1
        if pending >= batch_size:
            if not dry_run:
                db.commit()
            else:
                db.rollback()
            pending = 0

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return stats


def verify_recent_admission(db, *, days: int = 7, max_ids: int = 0) -> int:
    """复核最近试卷的知识点准入情况。

    这是"闭环到底通没通"的直接体检：把最近 ``days`` 天试卷里记下的知识点 id
    逐个走一遍准入判定，报告通过率。主数据缺失时这个比例接近 0，同步之后应
    该接近 100%。

    ``max_ids`` 为 0 表示全部判定。命中的知识点走的是主键直查，逐个判定很快；
    只有库里确实没有的 id 才会触发全表比对，因此不必担心数量。

    纯只读：判定过程可能顺带写入映射或候选，结束时统一回滚。
    """

    from datetime import datetime, timedelta

    from APP.backend.database import PaperItemRecord
    from APP.backend.knowledge_point_identity_service import (
        resolve_agent_knowledge_point,
    )

    since = datetime.now() - timedelta(days=days)
    rows = (
        db.query(PaperItemRecord.evidence_refs_json)
        .filter(PaperItemRecord.created_at >= since)
        .all()
    )

    # 同一份试卷会反复出现同一个知识点，按 id 去重后再判定，避免重复开销。
    hints: dict[str, str] = {}
    for (evidence,) in rows:
        try:
            payload = json.loads(evidence or "{}")
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        names = payload.get("kp_name_hints")
        names = names if isinstance(names, dict) else {}
        for kp_id in payload.get("source_kp_ids") or ():
            key = str(kp_id)
            if key not in hints:
                hints[key] = str(names.get(key) or "")

    if not hints:
        print(f"最近 {days} 天没有可复核的试卷知识点记录。")
        return 0

    targets = list(hints.items())
    if max_ids:
        targets = targets[:max_ids]
    admitted = 0
    rejected: list[tuple[str, str]] = []
    try:
        for kp_id, name in targets:
            try:
                resolution = resolve_agent_knowledge_point(
                    db,
                    source_kp_id=kp_id,
                    name=name or kp_id,
                    user_id=None,
                )
            except Exception as exc:  # noqa: BLE001 - 体检要报告异常而不是中断
                rejected.append((kp_id, f"判定异常：{exc}"))
                continue
            if resolution.admitted:
                admitted += 1
            else:
                rejected.append((kp_id, str(resolution.status)))
    finally:
        db.rollback()

    total = len(targets)
    rate = admitted / total * 100 if total else 0.0
    print(
        f"最近 {days} 天试卷知识点准入：{admitted}/{total} 通过（{rate:.1f}%），"
        f"共出现 {len(hints)} 个不同知识点。"
    )
    for kp_id, reason in rejected[:10]:
        print(f"  未通过：{kp_id}（{reason}）")
    if len(rejected) > 10:
        print(f"  …… 另有 {len(rejected) - 10} 个未通过。")
    return 0 if admitted == total else 1


def verify_atlas_coverage(db, atlas_path: Path, *, sample: int = 2000) -> int:
    """核对图谱知识点是否都已入库，并给出知识库的来源构成。

    抽样比对而非全量比对：全量要读完 72 MB 图谱，体检没必要那么慢。
    """

    stored = {
        str(kp_id) for (kp_id,) in db.query(KnowledgePoint.kp_id).all()
    }
    missing: list[str] = []
    checked = 0
    for record in iter_atlas_records(atlas_path):
        kp_id = str(record.get("kp_id") or "").strip()
        if not kp_id:
            continue
        checked += 1
        if kp_id not in stored and len(missing) < 10:
            missing.append(kp_id)
        if checked >= sample:
            break

    print(f"知识库知识点总数：{len(stored)}")
    print(f"图谱抽样核对：{checked} 条中缺失 {len(missing)} 条")
    for kp_id in missing:
        print(f"  缺失：{kp_id}")

    sources = (
        db.query(KnowledgePoint.source, func.count(KnowledgePoint.id))
        .group_by(KnowledgePoint.source)
        .order_by(func.count(KnowledgePoint.id).desc())
        .all()
    )
    print("来源构成：")
    for source, count in sources:
        print(f"  {count:>7}  {source or '(空)'}")
    return 0 if not missing else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把知识图谱知识点同步进平台数据库"
    )
    parser.add_argument(
        "--atlas",
        type=Path,
        default=None,
        help="final_knowledge_points.json 的路径；配合 --verify 时可选",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="只体检不写入：报告最近试卷的知识点准入率与知识库来源构成",
    )
    parser.add_argument(
        "--verify-days",
        type=int,
        default=7,
        help="体检时回看多少天的试卷，默认 7 天",
    )
    parser.add_argument(
        "--verify-sample",
        type=int,
        default=2000,
        help="体检时抽样核对多少条图谱知识点，默认 2000",
    )
    parser.add_argument(
        "--verify-max-ids",
        type=int,
        default=0,
        help="体检最多判定多少个知识点，0（默认）表示全部",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多处理多少条（0 表示全部），用于小规模试跑",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"每批提交行数，默认 {BATCH_SIZE}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计不写入，用于上线前核对",
    )
    parser.add_argument(
        "--expect-database",
        default="",
        help="期望连接的库名；不一致时直接失败，防止写错库",
    )
    args = parser.parse_args()

    if not args.verify and args.atlas is None:
        parser.error("同步模式必须提供 --atlas；只做体检请加 --verify")
    if args.atlas is not None and not args.atlas.is_file():
        print(f"知识图谱文件不存在：{args.atlas}", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        database_name = str(db.get_bind().url.database or "")
        print(f"目标数据库：{database_name}")
        if args.expect_database and database_name != args.expect_database:
            print(
                f"目标数据库是 {database_name!r}，"
                f"与期望的 {args.expect_database!r} 不一致，已中止。",
                file=sys.stderr,
            )
            return 2

        if args.verify:
            coverage = 0
            if args.atlas is not None:
                coverage = verify_atlas_coverage(
                    db, args.atlas, sample=args.verify_sample
                )
            print()
            admission = verify_recent_admission(
                db, days=args.verify_days, max_ids=args.verify_max_ids
            )
            return admission or coverage

        stats = sync_atlas_points(
            db,
            args.atlas,
            limit=args.limit,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )
    finally:
        db.close()

    prefix = "[试运行] " if args.dry_run else ""
    print(
        f"{prefix}扫描 {stats['scanned']} 条，新增 {stats['created']} 条，"
        f"刷新 {stats['updated']} 条，"
        f"跳过异源 {stats['skipped']} 条，无效 {stats['invalid']} 条"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

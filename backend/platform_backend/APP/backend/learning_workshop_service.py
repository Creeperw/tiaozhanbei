from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from APP.backend.database import (
    KnowledgeCardRecord,
    PaperInstanceRecord,
    PaperItemRecord,
    QuestionKPLinkRecord,
    QuestionVersionRecord,
)
from APP.backend.knowledge_point_identity_service import resolve_agent_knowledge_point
from APP.backend.time_utils import utc_now
from competition_app.contracts.knowledge import SCOPE_BRIDGE_MATCH_METHOD


logger = logging.getLogger(__name__)


WORKSHOP_MODULES = [
    {
        "key": "question_training",
        "label": "题目训练",
        "description": "集中完成练习批改、案例训练和错题变式。",
        "enabled": True,
        "recommended": True,
        "capabilities": ["practice_grading", "case_training", "mistake_variation"],
    },
    {
        "key": "knowledge_cards",
        "label": "知识卡片",
        "description": "沉淀已学习知识点的讲解、教材、视频与配套题目。",
        "enabled": True,
        "recommended": False,
        "capabilities": ["resource_bundle", "card_library"],
    },
    {
        "key": "paper_workspace",
        "label": "试卷生成",
        "description": "按要求组卷，并在计时答题界面完成保存、交卷和评分。",
        "enabled": True,
        "recommended": False,
        "capabilities": ["paper_generation", "timed_session", "paper_submission"],
    },
]


def workshop_overview() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "modules": WORKSHOP_MODULES,
        "default_module": "question_training",
        "endpoints": {
            "knowledge_cards": "/api/v1/workshop/knowledge-cards",
            "resolve_knowledge_card": "/api/v1/workshop/knowledge-cards/resolve",
            "papers": "/api/v1/workshop/papers",
            "paper": "/api/v1/workshop/papers/{paper_id}",
        },
    }


def upsert_knowledge_card(
    db: Session,
    *,
    user_id: int,
    kp_id: str,
    title: str,
    resource_bundle: dict[str, Any],
    source_execution_id: str = "",
) -> dict[str, Any]:
    row = db.query(KnowledgeCardRecord).filter_by(user_id=user_id, kp_id=kp_id).one_or_none()
    if row is None:
        row = KnowledgeCardRecord(
            card_id=f"KC_{uuid4().hex}",
            user_id=user_id,
            kp_id=kp_id,
        )
        db.add(row)
    row.title = title[:200]
    row.learning_status = "learned"
    row.resource_bundle_json = json.dumps(resource_bundle, ensure_ascii=False)
    row.source_execution_id = source_execution_id[:120]
    row.updated_at = utc_now()
    db.commit()
    db.refresh(row)
    return serialize_knowledge_card(row, include_bundle=True)


def serialize_knowledge_card(row: KnowledgeCardRecord, *, include_bundle: bool) -> dict[str, Any]:
    value = {
        "card_id": row.card_id,
        "kp_id": row.kp_id,
        "title": row.title,
        "learning_status": "learned",
        "source_execution_id": row.source_execution_id or "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if include_bundle:
        try:
            bundle = json.loads(row.resource_bundle_json or "{}")
        except (TypeError, ValueError):
            bundle = {}
        value.update({"schema_version": "1.0", "resource_bundle": bundle})
    return value


def list_knowledge_cards(db: Session, *, user_id: int, offset: int, limit: int) -> dict[str, Any]:
    query = db.query(KnowledgeCardRecord).filter_by(user_id=user_id, learning_status="learned")
    total = query.count()
    rows = query.order_by(KnowledgeCardRecord.updated_at.desc(), KnowledgeCardRecord.id.desc()).offset(offset).limit(limit).all()
    return {
        "schema_version": "1.0",
        "items": [serialize_knowledge_card(row, include_bundle=False) for row in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def get_knowledge_card(db: Session, *, user_id: int, card_id: str) -> dict[str, Any] | None:
    row = db.query(KnowledgeCardRecord).filter_by(user_id=user_id, card_id=card_id).one_or_none()
    return serialize_knowledge_card(row, include_bundle=True) if row is not None else None


def _learner_notices_payload(paper: dict[str, Any]) -> dict[str, str]:
    """从试卷载荷里取出面向学习者的卷面说明。

    说明由组卷侧确定性生成（难度与来源说明、题目来源说明、审核说明），随
    ``paper`` 载荷一起送达。这里只做形状与空值校验，不改写文案。
    """
    notices = paper.get("learner_notices")
    if not isinstance(notices, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in notices.items()
        if str(value or "").strip()
    }


def publish_agent_paper(
    db: Session,
    *,
    user_id: int,
    execution_id: str,
    paper: dict[str, Any],
    blueprint: dict[str, Any],
    evidence_pack: dict[str, Any],
    daily_task_item_id: str | None = None,
) -> dict[str, Any]:
    _validate_agent_paper_constraints(paper, blueprint)
    if daily_task_item_id is not None and (
        not isinstance(daily_task_item_id, str) or not daily_task_item_id.strip()
    ):
        raise ValueError("daily task item id is invalid")
    bound_item_id = daily_task_item_id.strip() if isinstance(daily_task_item_id, str) else None
    snapshots = None
    if bound_item_id:
        # Keep the Task 5 frozen-snapshot ownership and set-validation rules as
        # the single authority for bound papers.
        from APP.backend.paper_generation_service import (
            _bound_paper_matches_snapshots,
            _bound_snapshots,
        )

        snapshots = _bound_snapshots(
            db,
            user_id=user_id,
            daily_task_item_id=bound_item_id,
        )
        existing_bound = db.query(PaperInstanceRecord).filter_by(
            learner_id=user_id,
            daily_task_item_id=bound_item_id,
        ).one_or_none()
        if existing_bound is not None:
            if not _bound_paper_matches_snapshots(db, existing_bound, snapshots):
                raise ValueError("bound paper does not match frozen daily task questions")
            return {"paper_id": existing_bound.paper_id, "status": existing_bound.status}

    existing = db.query(PaperInstanceRecord).filter_by(task_id=execution_id, learner_id=user_id).one_or_none()
    if existing is not None:
        return {"paper_id": existing.paper_id, "status": existing.status}

    # 卷面说明（难度与来源、题目来源、审核结论）随试卷载荷一起送达，但它不是
    # 蓝图内容。存进同一列的下划线子键，答题页从试卷详情接口读出后显示给学习
    # 者——看不到这些说明，学习者就无法判断卷子为什么掺入了别的知识点的题、
    # 内容审核又指出了什么。
    learner_notices = _learner_notices_payload(paper)
    paper_id = f"PAPER_{uuid4().hex}"
    duration = max(1, min(24 * 60, int(paper.get("duration_minutes") or 60)))
    paper_items = list(paper.get("items") or []) if snapshots is None else [{} for _ in snapshots]
    item_scores = _normalized_item_scores(paper_items, paper, blueprint)
    db.add(
        PaperInstanceRecord(
            paper_id=paper_id,
            task_id=execution_id,
            daily_task_item_id=bound_item_id,
            orchestration_run_id=execution_id,
            learner_id=user_id,
            title=str(paper.get("title") or "训练试卷")[:200],
            status="published",
            duration_minutes=duration,
            blueprint_json=json.dumps(
                {**blueprint, "_learner_notices": learner_notices}
                if learner_notices
                else blueprint,
                ensure_ascii=False,
            ),
            evidence_pack_json=json.dumps(evidence_pack, ensure_ascii=False),
        )
    )
    if snapshots is not None:
        for position, (snapshot, item_score) in enumerate(zip(snapshots, item_scores), start=1):
            db.add(PaperItemRecord(
                paper_item_id=f"PI_{uuid4().hex}",
                paper_id=paper_id,
                position=position,
                question_id=snapshot.question_id,
                question_version_id=snapshot.question_version_id,
                question_type=snapshot.question_type,
                stem_snapshot=snapshot.stem_snapshot,
                options_snapshot_json=snapshot.options_snapshot_json,
                standard_answer_snapshot=snapshot.answer_snapshot,
                kp_snapshot_json=snapshot.kp_snapshot_json,
                evidence_refs_json="[]",
                source_kind=snapshot.source_kind,
                standard_difficulty=None,
                max_score_snapshot=item_score,
            ))
    # 知识点准入统计：未准入的知识点不会进入掌握度与复习闭环。整卷被拒时
    # 学习者做完题却看不到任何个人数据变化，且此前没有任何日志可循——这里把
    # 结果汇总成一条日志，让静默失效至少留下痕迹。
    kp_total = 0
    kp_admitted = 0
    kp_rejected_samples: list[str] = []
    for position, (item, item_score) in enumerate(zip(paper_items, item_scores), start=1):
        if snapshots is not None:
            break
        question = item.get("question") or {}
        bridges = question.get("bridges") or []
        # 只认题目自身的知识点桥接。组卷在填空缺口上补题时，会把整个蓝图单元的
        # 宽召回范围挂成 ``resolved_blueprint_unit`` 桥接——那是这道题从哪个范围
        # 里找出来，不是这道题考了哪些知识点。把它当题目知识点落库，一份卷子就
        # 会带出几百个学习者没学过的知识点，并顺着卷面快照写进掌握度与复习排期。
        kp_ids = list(dict.fromkeys(
            str(bridge.get("kp_id"))
            for bridge in bridges
            if isinstance(bridge, dict)
            and str(bridge.get("kp_id") or "").strip()
            and str(bridge.get("match_method") or "") != SCOPE_BRIDGE_MATCH_METHOD
        ))
        source_metadata = question.get("source_metadata") or {}
        kp_name_hints = (
            source_metadata.get("kp_names")
            if isinstance(source_metadata, dict)
            and isinstance(source_metadata.get("kp_names"), dict)
            else {}
        )
        tags = [
            str(value).strip()
            for value in question.get("tags") or []
            if str(value).strip()
        ]
        canonical_kp_ids = []
        rejected: list[str] = []
        for index, kp_id in enumerate(kp_ids):
            name = str(kp_name_hints.get(kp_id) or "").strip()
            if not name and tags:
                name = tags[index] if index < len(tags) else tags[0]
            resolution = resolve_agent_knowledge_point(
                db,
                source_kp_id=kp_id,
                name=name or kp_id,
            )
            if resolution.admitted:
                canonical_kp_ids.append(str(resolution.canonical_kp_id))
            else:
                rejected.append(f"{kp_id}（{name or kp_id}）")
        canonical_kp_ids = list(dict.fromkeys(canonical_kp_ids))
        kp_total += len(kp_ids)
        kp_admitted += len(canonical_kp_ids)
        # 未准入样本带上名称：候选队列已拆除，这条日志是它们的唯一记录。
        if rejected and len(kp_rejected_samples) < 5:
            kp_rejected_samples.extend(rejected[: 5 - len(kp_rejected_samples)])
        question_id = str(question.get("question_id") or f"AGENT_Q_{uuid4().hex}")
        paper_item = PaperItemRecord(
                paper_item_id=f"PI_{uuid4().hex}",
                paper_id=paper_id,
                position=int(item.get("sequence") or position),
                question_id=question_id[:120],
                question_version_id=f"{question_id}:agent"[:120],
                question_type=str(question.get("question_type") or "short_answer")[:50],
                stem_snapshot=str(question.get("stem") or ""),
                options_snapshot_json=json.dumps(question.get("options") or [], ensure_ascii=False),
                standard_answer_snapshot=str(question.get("reference_answer") or ""),
                kp_snapshot_json=json.dumps(canonical_kp_ids, ensure_ascii=False),
                evidence_refs_json=json.dumps(
                    {"source_kp_ids": kp_ids},
                    ensure_ascii=False,
                ),
                source_kind="agent_audited",
                standard_difficulty=None,
                max_score_snapshot=item_score,
            )
        db.add(paper_item)
        ensure_paper_question_authority(
            db,
            paper_item,
            analysis=str(question.get("analysis") or ""),
        )
    if snapshots is not None:
        paper_instance = db.query(PaperInstanceRecord).filter_by(paper_id=paper_id).one()
        if not _bound_paper_matches_snapshots(db, paper_instance, snapshots):
            raise ValueError("bound paper does not match frozen daily task questions")
    db.commit()
    if kp_total and not kp_admitted:
        logger.error(
            "试卷 %s 的知识点全部未准入（0/%d）：掌握度与复习闭环不会更新。"
            "被拒样本 %s；请检查 knowledge_points 是否已同步知识图谱。",
            paper_id,
            kp_total,
            kp_rejected_samples,
        )
    elif kp_total and kp_admitted < kp_total:
        logger.warning(
            "试卷 %s 有知识点未准入：%d/%d 通过，被拒样本 %s。",
            paper_id,
            kp_admitted,
            kp_total,
            kp_rejected_samples,
        )
    elif kp_total:
        logger.info(
            "试卷 %s 知识点准入：%d/%d 通过。", paper_id, kp_admitted, kp_total
        )
    return {"paper_id": paper_id, "status": "published", "duration_minutes": duration}


def _validate_agent_paper_constraints(
    paper: dict[str, Any], blueprint: dict[str, Any]
) -> None:
    items = list(paper.get("items") or [])
    required_total = blueprint.get("required_total_question_count")
    if blueprint.get("question_count_is_hard_constraint") and required_total is not None:
        if len(items) != int(required_total):
            raise ValueError("agent paper does not match required question count")
    raw_distribution = blueprint.get("required_question_type_distribution") or {}
    if not isinstance(raw_distribution, dict) or not raw_distribution:
        return

    def normalize_question_type(value: Any) -> str:
        normalized = str(value or "").strip().replace(" ", "").replace("_", "")
        if "案例" in normalized or "病例" in normalized:
            return "简答题"
        return {
            "单选题": "单项选择题",
            "单项选择": "单项选择题",
            "多选题": "多项选择题",
            "多项选择": "多项选择题",
            "简答": "简答题",
            "问答": "简答题",
            "问答题": "简答题",
        }.get(normalized, normalized)

    expected = {
        normalize_question_type(question_type): int(count)
        for question_type, count in raw_distribution.items()
        if int(count) > 0
    }
    actual: dict[str, int] = {}
    for item in items:
        question = item.get("question") or {}
        question_type = normalize_question_type(question.get("question_type"))
        actual[question_type] = actual.get(question_type, 0) + 1
    if actual != expected:
        raise ValueError("agent paper does not match required question type distribution")


def _normalized_item_scores(
    items: list[dict[str, Any]],
    paper: dict[str, Any],
    blueprint: dict[str, Any],
) -> list[float]:
    """Allocate integer item scores while keeping the system total authoritative."""

    if not items:
        return []
    raw_scores: list[float | None] = []
    for item in items:
        try:
            score = float(item.get("score")) if item.get("score") is not None else None
        except (TypeError, ValueError):
            score = None
        raw_scores.append(score if score is not None and score > 0 else None)
    try:
        configured_total = float(
            paper.get("total_score") or blueprint.get("total_score") or 0
        )
    except (TypeError, ValueError):
        configured_total = 0.0
    if configured_total <= 0 and all(score is not None for score in raw_scores):
        configured_total = sum(float(score) for score in raw_scores if score is not None)
    total_score = round(configured_total) if configured_total > 0 else 100

    explicit_total = sum(score or 0.0 for score in raw_scores)
    missing_count = sum(score is None for score in raw_scores)
    if missing_count and explicit_total < total_score:
        remainder = (total_score - explicit_total) / missing_count
        provisional = [score if score is not None else remainder for score in raw_scores]
    else:
        provisional = [score if score is not None else 1.0 for score in raw_scores]

    provisional_total = sum(provisional)
    if provisional_total <= 0:
        provisional = [1.0] * len(items)
        provisional_total = float(len(items))
    exact = [total_score * score / provisional_total for score in provisional]
    allocated = [int(score) for score in exact]
    remaining = total_score - sum(allocated)
    remainder_order = sorted(
        range(len(exact)),
        key=lambda index: (-(exact[index] - allocated[index]), index),
    )
    for index in remainder_order[:remaining]:
        allocated[index] += 1
    return [float(score) for score in allocated]


def ensure_paper_question_authority(
    db: Session,
    item: PaperItemRecord,
    *,
    analysis: str = "",
) -> QuestionVersionRecord:
    """Backfill the immutable question authority required by grading and variations."""

    version = db.query(QuestionVersionRecord).filter_by(
        question_version_id=item.question_version_id,
    ).one_or_none()
    if version is None:
        latest = db.query(func.max(QuestionVersionRecord.version)).filter_by(
            question_id=item.question_id,
        ).scalar()
        version = QuestionVersionRecord(
            question_version_id=item.question_version_id,
            question_id=item.question_id,
            version=int(latest or 0) + 1,
        )
        db.add(version)
    version.question_type = item.question_type
    version.stem = item.stem_snapshot
    version.answer = item.standard_answer_snapshot
    if analysis.strip() and not str(version.analysis or "").strip():
        version.analysis = analysis.strip()
    version.standard_difficulty = None
    version.source_kind = item.source_kind or "paper_snapshot"
    version.status = "active"
    db.flush()

    kp_ids = list(dict.fromkeys(_paper_kp_ids(item.kp_snapshot_json)))
    existing = {
        row.kp_id: row
        for row in db.query(QuestionKPLinkRecord).filter_by(
            question_version_id=item.question_version_id,
        ).all()
    }
    for index, kp_id in enumerate(kp_ids):
        link = existing.get(kp_id)
        if link is None:
            db.add(QuestionKPLinkRecord(
                question_version_id=item.question_version_id,
                kp_id=kp_id,
                is_primary=index == 0,
                status="active",
            ))
        else:
            link.is_primary = index == 0
            link.status = "active"
    db.flush()
    return version


def _paper_kp_ids(value: str | None) -> list[str]:
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(decoded, list):
        return []
    return [str(item).strip() for item in decoded if str(item).strip()]


def ensure_paper_started(db: Session, paper: PaperInstanceRecord) -> None:
    if paper.started_at is None and paper.status == "published":
        paper.started_at = utc_now()
        paper.expires_at = paper.started_at + timedelta(minutes=max(1, int(paper.duration_minutes or 60)))
        db.commit()
        db.refresh(paper)

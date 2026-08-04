from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from APP.backend.database import (
    KnowledgeCardRecord,
    KnowledgePoint,
    PaperInstanceRecord,
    PaperItemRecord,
    QuestionKPLinkRecord,
    QuestionVersionRecord,
)
from APP.backend.time_utils import utc_now


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
            blueprint_json=json.dumps(blueprint, ensure_ascii=False),
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
    for position, (item, item_score) in enumerate(zip(paper_items, item_scores), start=1):
        if snapshots is not None:
            break
        question = item.get("question") or {}
        bridges = question.get("bridges") or []
        kp_ids = list(dict.fromkeys(
            str(bridge.get("kp_id"))
            for bridge in bridges
            if isinstance(bridge, dict) and str(bridge.get("kp_id") or "").strip()
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
        for index, kp_id in enumerate(kp_ids):
            name = str(kp_name_hints.get(kp_id) or "").strip()
            if not name and tags:
                name = tags[index] if index < len(tags) else tags[0]
            row = db.query(KnowledgePoint).filter_by(kp_id=kp_id).one_or_none()
            if row is None:
                db.add(
                    KnowledgePoint(
                        kp_id=kp_id,
                        name=name or kp_id,
                        source="agent_audited_paper",
                        status="active",
                    )
                )
            elif name and (not str(row.name or "").strip() or row.name == kp_id):
                row.name = name
                row.status = "active"
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
                kp_snapshot_json=json.dumps(kp_ids, ensure_ascii=False),
                evidence_refs_json="[]",
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
    """Complete optional model scores while keeping the system total authoritative."""

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
        total_score = float(paper.get("total_score") or blueprint.get("total_score") or 0)
    except (TypeError, ValueError):
        total_score = 0.0
    if total_score <= 0 and all(score is not None for score in raw_scores):
        return [round(float(score), 2) for score in raw_scores]
    if total_score <= 0:
        total_score = 100.0

    explicit_total = sum(score or 0.0 for score in raw_scores)
    missing_count = sum(score is None for score in raw_scores)
    if missing_count and explicit_total < total_score:
        remainder = (total_score - explicit_total) / missing_count
        return [round(score if score is not None else remainder, 2) for score in raw_scores]

    provisional = [score if score is not None else 1.0 for score in raw_scores]
    provisional_total = sum(provisional)
    if provisional_total <= 0:
        provisional = [1.0] * len(items)
        provisional_total = float(len(items))
    scaled = [round(total_score * score / provisional_total, 2) for score in provisional]
    scaled[-1] = round(scaled[-1] + total_score - sum(scaled), 2)
    return scaled


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

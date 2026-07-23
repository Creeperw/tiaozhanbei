from __future__ import annotations

import json
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
) -> dict[str, Any]:
    existing = db.query(PaperInstanceRecord).filter_by(task_id=execution_id, learner_id=user_id).one_or_none()
    if existing is not None:
        return {"paper_id": existing.paper_id, "status": existing.status}

    paper_id = f"PAPER_{uuid4().hex}"
    duration = max(1, min(24 * 60, int(paper.get("duration_minutes") or 60)))
    paper_items = list(paper.get("items") or [])
    item_scores = _normalized_item_scores(paper_items, paper, blueprint)
    db.add(
        PaperInstanceRecord(
            paper_id=paper_id,
            task_id=execution_id,
            orchestration_run_id=execution_id,
            learner_id=user_id,
            title=str(paper.get("title") or "训练试卷")[:200],
            status="published",
            duration_minutes=duration,
            blueprint_json=json.dumps(blueprint, ensure_ascii=False),
            evidence_pack_json=json.dumps(evidence_pack, ensure_ascii=False),
        )
    )
    for position, (item, item_score) in enumerate(zip(paper_items, item_scores), start=1):
        question = item.get("question") or {}
        try:
            item_difficulty = max(1, min(5, int(float(
                question.get("difficulty") or paper.get("difficulty") or blueprint.get("difficulty") or 2
            ))))
        except (TypeError, ValueError):
            item_difficulty = 2
        bridges = question.get("bridges") or []
        kp_ids = list(dict.fromkeys(
            str(bridge.get("kp_id"))
            for bridge in bridges
            if isinstance(bridge, dict) and str(bridge.get("kp_id") or "").strip()
        ))
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
                standard_difficulty=item_difficulty,
                max_score_snapshot=item_score,
            )
        db.add(paper_item)
        ensure_paper_question_authority(
            db,
            paper_item,
            analysis=str(question.get("analysis") or ""),
        )
    db.commit()
    return {"paper_id": paper_id, "status": "published", "duration_minutes": duration}


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
    version.standard_difficulty = max(1, min(5, int(item.standard_difficulty or 2)))
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

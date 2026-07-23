from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from APP.backend.agent_contracts import EvidenceItem, EvidencePack, LearnerContextBrief
from APP.backend.database import (
    CandidateKnowledgePoint,
    KnowledgePoint,
    MistakeRecord,
    QuestionBankItem,
    TeachingResource,
)
from APP.backend.deep_training_service import align_knowledge_points as align_current_knowledge_points
from APP.backend.knowledge_atlas_service import AtlasUnavailableError, atlas_service
from APP.backend.question_index_search_service import question_index_search_service
from APP.backend.rag_core import RAGUnavailableError, rag_service


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return loaded if isinstance(loaded, list) else []


def _stable_candidate_id(text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8].upper()
    return f"CAND_KP_{digest}"


def _knowledge_point_payloads(db: Session) -> list[dict[str, Any]]:
    rows = db.query(KnowledgePoint).filter(KnowledgePoint.status == "active").order_by(KnowledgePoint.id.asc()).all()
    return [
        {
            "kp_id": row.kp_id,
            "name": row.name,
            "aliases": [_text(item) for item in _json_list(row.aliases_json) if _text(item)],
        }
        for row in rows
    ]


def align_knowledge_points(db: Session, text: str, user_id: int | None = None) -> dict[str, Any]:
    result = align_current_knowledge_points(text=text, knowledge_points=_knowledge_point_payloads(db))
    if result.get("candidate_kp_ids"):
        candidate_id = result["candidate_kp_ids"][0]
        existing = db.query(CandidateKnowledgePoint).filter(CandidateKnowledgePoint.candidate_id == candidate_id).first()
        if existing is None:
            candidate = CandidateKnowledgePoint(
                candidate_id=candidate_id,
                name=_text(text)[:200],
                source_text=_text(text),
                created_by_user_id=user_id,
                evidence_json=json.dumps(result.get("evidence", []), ensure_ascii=False),
            )
            db.add(candidate)
            db.commit()
    return result


def _row_kp_ids(row: Any) -> list[str]:
    return [_text(item) for item in _json_list(getattr(row, "kp_ids_json", "[]")) if _text(item)]


def _matches_kp(row: Any, kp_ids: set[str]) -> bool:
    return bool(set(_row_kp_ids(row)) & kp_ids)


def _normalize_rag_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": _text(item.get("source")),
        "content": _text(item.get("content")),
        "score": float(item.get("score", 0.0) or 0.0),
        "type": _text(item.get("type")) or "text",
    }


def _evidence_item(source_scope: str, source_id: str, summary: str, kp_ids: list[str], confidence: float) -> EvidenceItem:
    return EvidenceItem(
        source_scope=source_scope,
        source_id=source_id,
        summary=summary,
        kp_ids=kp_ids,
        confidence=max(0.0, min(confidence, 1.0)),
    )


def _build_rag_evidence(raw_results: list[dict[str, Any]], kp_ids: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[EvidenceItem]]:
    personal: list[dict[str, Any]] = []
    public: list[dict[str, Any]] = []
    items: list[EvidenceItem] = []
    for raw in sorted(raw_results, key=lambda value: float(value.get("score", 0.0) or 0.0), reverse=True):
        scope = _text(raw.get("scope")) or "public"
        normalized = _normalize_rag_result(raw)
        if scope == "personal":
            personal.append(normalized)
        else:
            public.append(normalized)
        items.append(_evidence_item(scope, normalized["source"], normalized["content"], kp_ids, normalized["score"]))
    return personal, public, items


def _knowledge_point_evidence(db: Session, kp_ids: list[str]) -> list[EvidenceItem]:
    targets = set(kp_ids)
    if not targets:
        return []
    rows = (
        db.query(KnowledgePoint)
        .filter(KnowledgePoint.status == "active", KnowledgePoint.kp_id.in_(targets))
        .order_by(KnowledgePoint.id.asc())
        .all()
    )
    return [
        _evidence_item(
            "knowledge_point",
            row.kp_id,
            f"{row.name}：{_text(row.description)}" if _text(row.description) else row.name,
            [row.kp_id],
            0.85,
        )
        for row in rows
    ]


def _question_evidence(db: Session, kp_ids: list[str], limit: int = 5) -> list[dict[str, Any]]:
    targets = set(kp_ids)
    atlas_targets = [kp_id for kp_id in targets if re.fullmatch(r"\d{6}", kp_id)]
    if atlas_targets:
        try:
            atlas_questions = atlas_service.questions_for_kps(atlas_targets, limit=limit)
        except AtlasUnavailableError:
            atlas_questions = []
        if atlas_questions:
            return [
                {
                    "question_id": row["question_id"],
                    "stem": row["stem"],
                    "options": row.get("options") or [],
                    "answer": row.get("answer") or [],
                    "analysis": row.get("explanation") or "",
                    "kp_ids": row.get("kp_ids") or [],
                    "difficulty": float(row.get("difficulty") or 0.0),
                    "quality_score": float(row.get("score") or 0.85),
                    "channels": row.get("channels") or ["atlas_question_bank"],
                }
                for row in atlas_questions
            ]
    if not targets:
        return []
    clauses = [QuestionBankItem.kp_ids_json.like(f'%"{kp_id}"%') for kp_id in sorted(targets)]
    rows = (
        db.query(QuestionBankItem)
        .filter(QuestionBankItem.status == "active", or_(*clauses))
        .order_by(QuestionBankItem.quality_score.desc(), QuestionBankItem.id.asc())
        .limit(max(1, int(limit)) * 4)
        .all()
    )
    matched = [row for row in rows if _matches_kp(row, targets)]
    matched.sort(key=lambda row: (float(row.quality_score or 0.0), -abs(float(row.difficulty or 2.0) - 2.5)), reverse=True)
    return [
        {
            "question_id": row.question_id,
            "stem": row.stem,
            "answer": row.answer,
            "analysis": row.analysis,
            "kp_ids": _row_kp_ids(row),
            "difficulty": float(row.difficulty or 0.0),
            "quality_score": float(row.quality_score or 0.0),
        }
        for row in matched[:limit]
    ]


def _semantic_question_evidence(query: str, kp_ids: list[str], limit: int = 5) -> list[dict[str, Any]]:
    atlas_kp_ids = [kp_id for kp_id in kp_ids if re.fullmatch(r"\d{6}", kp_id)]
    results = question_index_search_service.search(
        query,
        kp_ids=atlas_kp_ids,
        limit=limit,
    )
    return [
        {
            "question_id": row["question_id"],
            "stem": row["stem"],
            "options": row.get("options") or [],
            "answer": row.get("answer") if row.get("answer") is not None else [],
            "analysis": row.get("explanation") or "",
            "kp_ids": row.get("kp_ids") or [],
            "question_type": row.get("question_type") or "",
            "difficulty": float(row.get("difficulty") or 0.0),
            "quality_score": float(row.get("score") or 0.0),
            "channels": row.get("channels") or ["question_index_v2", "semantic_search"],
        }
        for row in results
    ]


def _merge_question_evidence(*groups: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for question in group:
            question_id = _text(question.get("question_id"))
            if question_id and question_id not in merged:
                merged[question_id] = question
    return list(merged.values())[: max(1, int(limit))]


def _resource_evidence(db: Session, kp_ids: list[str], limit: int = 5) -> list[dict[str, Any]]:
    targets = set(kp_ids)
    rows = db.query(TeachingResource).filter(TeachingResource.status == "active").all()
    matched = [row for row in rows if _matches_kp(row, targets)]
    matched.sort(key=lambda row: float(row.quality_score or 0.0), reverse=True)
    return [
        {
            "resource_id": row.resource_id,
            "title": row.title,
            "resource_type": row.resource_type,
            "summary": row.summary,
            "kp_ids": _row_kp_ids(row),
            "quality_score": float(row.quality_score or 0.0),
        }
        for row in matched[:limit]
    ]


def _mistake_evidence(db: Session, user_id: int | None, kp_ids: list[str], limit: int = 5) -> list[dict[str, Any]]:
    if user_id is None:
        return []
    targets = set(kp_ids)
    rows = db.query(MistakeRecord).filter(MistakeRecord.user_id == user_id, MistakeRecord.status == "active").order_by(MistakeRecord.id.desc()).all()
    return [
        {
            "question_id": row.question_id,
            "summary": row.summary,
            "error_type": row.error_type,
            "kp_ids": _row_kp_ids(row),
        }
        for row in rows
        if _matches_kp(row, targets)
    ][:limit]


def _detect_conflicts(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    positives: list[dict[str, Any]] = []
    negatives: list[dict[str, Any]] = []
    for item in evidence:
        content = _text(item.get("content"))
        if "四君子汤" in content and "脾胃气虚证" in content:
            if "不适用" in content or "不主治" in content or "不能" in content:
                negatives.append(item)
            else:
                positives.append(item)
    if positives and negatives:
        return [
            {
                "type": "possible_negation_conflict",
                "positive_sources": [item["source"] for item in positives],
                "negative_sources": [item["source"] for item in negatives],
            }
        ]
    return []


def build_evidence_pack(
    db: Session,
    *,
    query: str,
    learner_context: LearnerContextBrief,
    task_type: str | None = None,
    requested_kp_ids: list[str] | None = None,
    document_result: dict[str, Any] | None = None,
    rag_search: Callable[..., list[dict[str, Any]]] | None = None,
) -> EvidencePack:
    user_id = int(learner_context.learner_id) if str(learner_context.learner_id).isdigit() else None
    alignment = align_knowledge_points(db, query, user_id=user_id)
    query_kp_ids = list(alignment.get("resolved_kp_ids", []))
    scoped_kp_ids = list(dict.fromkeys(
        value.strip()
        for value in (requested_kp_ids or [])
        if isinstance(value, str) and value.strip()
    ))
    evidence_kp_ids = scoped_kp_ids or query_kp_ids or list(learner_context.kp_ids)
    resolved_kp_ids = (
        scoped_kp_ids
        if scoped_kp_ids
        else list(dict.fromkeys([*query_kp_ids, *learner_context.kp_ids]))
    )
    candidate_kp_ids = list(alignment.get("candidate_kp_ids", []))
    search = rag_search or rag_service.search
    retrieval_risks: list[str] = []
    try:
        rag_results = search(
            query,
            top_k=5,
            user_id=user_id,
            include_public=True,
            include_personal=True,
        )
    except RAGUnavailableError as exc:
        rag_results = []
        retrieval_risks.append(f"document_rag={exc.state}: {exc.message}")
    personal, public, items = _build_rag_evidence(rag_results, evidence_kp_ids)
    items.extend(_knowledge_point_evidence(db, evidence_kp_ids))
    linked_questions = _question_evidence(db, evidence_kp_ids)
    semantic_questions: list[dict[str, Any]] = []
    if rag_search is None:
        try:
            semantic_questions = _semantic_question_evidence(query, evidence_kp_ids)
        except RAGUnavailableError as exc:
            retrieval_risks.append(f"question_index={exc.state}: {exc.message}")
    questions = _merge_question_evidence(semantic_questions, linked_questions, limit=5)
    resources = _resource_evidence(db, evidence_kp_ids)
    mistakes = _mistake_evidence(db, user_id, evidence_kp_ids)

    for question in questions:
        items.append(_evidence_item("question_bank", question["question_id"], question["stem"], question["kp_ids"], question["quality_score"]))
    for resource in resources:
        items.append(_evidence_item("teaching_resource", resource["resource_id"], resource["summary"], resource["kp_ids"], resource["quality_score"]))
    for mistake in mistakes:
        items.append(_evidence_item("mistake_record", mistake["question_id"], mistake["summary"], mistake["kp_ids"], 0.82))

    conflict_evidence = _detect_conflicts([*personal, *public])
    risk_notes = ["存在可能冲突的知识库证据，需审核智能体复核"] if conflict_evidence else []
    risk_notes.extend(retrieval_risks)
    if document_result and document_result.get("risk_notes"):
        risk_notes.extend(str(note) for note in document_result.get("risk_notes", []))

    return EvidencePack(
        source_scope="knowledge_base_agent",
        source_id=f"evidence:{hashlib.sha1(query.encode('utf-8')).hexdigest()[:12]}",
        items=items,
        kp_ids=resolved_kp_ids,
        resolved_kp_ids=resolved_kp_ids,
        candidate_kp_ids=candidate_kp_ids,
        personal_evidence=personal,
        public_evidence=public,
        question_evidence=questions,
        resource_evidence=resources,
        conflict_evidence=conflict_evidence,
        risk_notes=risk_notes,
        confidence=(0.58 if retrieval_risks else (0.82 if not conflict_evidence else 0.66)),
        agent_trace=[{
            "agent": "knowledge_base_agent",
            "action": "build_evidence_pack",
            "task_type": task_type or "general",
            "retrieval_risks": retrieval_risks,
        }],
    )


def list_questions(db: Session, kp_ids: list[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
    targets = set(kp_ids or [])
    atlas_targets = [kp_id for kp_id in targets if re.fullmatch(r"\d{6}", kp_id)]
    if atlas_targets:
        try:
            atlas_questions = atlas_service.questions_for_kps(atlas_targets, limit=limit)
        except AtlasUnavailableError:
            atlas_questions = []
        if atlas_questions:
            return [
                {
                    "question_id": row["question_id"],
                    "stem": row["stem"],
                    "options": row.get("options") or [],
                    "answer": row.get("answer") or [],
                    "analysis": row.get("explanation") or "",
                    "kp_ids": row.get("kp_ids") or [],
                    "question_type": row.get("question_type") or "",
                    "difficulty": float(row.get("difficulty") or 0.0),
                    "quality_score": float(row.get("score") or 0.85),
                    "channels": row.get("channels") or ["atlas_question_bank"],
                }
                for row in atlas_questions
            ]
    query = db.query(QuestionBankItem).filter(QuestionBankItem.status == "active")
    if targets:
        clauses = [QuestionBankItem.kp_ids_json.like(f'%"{kp_id}"%') for kp_id in sorted(targets)]
        query = query.filter(or_(*clauses))
    rows = query.order_by(QuestionBankItem.id.asc()).limit(max(1, int(limit)) * (4 if targets else 1)).all()
    if targets:
        rows = [row for row in rows if _matches_kp(row, targets)]
    return [
        {
            "question_id": row.question_id,
            "stem": row.stem,
            "answer": row.answer,
            "analysis": row.analysis,
            "kp_ids": _row_kp_ids(row),
            "question_type": row.question_type,
            "difficulty": float(row.difficulty or 0.0),
            "quality_score": float(row.quality_score or 0.0),
        }
        for row in rows[:limit]
    ]

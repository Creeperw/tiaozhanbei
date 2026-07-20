from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from APP.backend.agent_contracts import LearnerContextBrief
from APP.backend.database import (
    AgentEvent,
    FeedbackRecord,
    LearnerKnowledgeMastery,
    LearningActivityRecord,
    LearningInterventionRecord,
    LearningPlanRecord,
    MemoryCandidate,
    MemorySummary,
    PersonalizationMemory,
    UserProfile,
)
from APP.backend.learner_profile_service import build_learner_profile_payload


def _parse_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _memory_item(row: PersonalizationMemory) -> dict[str, Any]:
    return {
        "id": row.id,
        "category": row.category,
        "title": row.title or "",
        "content": row.content or "",
        "source": row.source or "",
        "importance": row.importance or "normal",
        "confidence": row.confidence,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _plan_item(row: LearningPlanRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "plan_type": row.plan_type or "",
        "title": row.title or "",
        "summary": row.summary or "",
        "status": row.status or "",
        "payload": _parse_json(row.payload_json),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _mastery_item(row: LearnerKnowledgeMastery) -> dict[str, Any]:
    return {
        "kp_id": row.kp_id,
        "mastery": row.mastery,
        "confidence": row.confidence,
        "wrong_count": row.wrong_count,
        "review_count": row.review_count,
        "last_review_at": _iso(row.last_review_at),
        "next_review_at": _iso(row.next_review_at),
        "mastery_status": row.mastery_status or "unknown",
    }


def _activity_item(row: LearningActivityRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "activity_type": row.activity_type or "",
        "resource_id": row.resource_id or "",
        "resource_type": row.resource_type or "",
        "duration_minutes": row.duration_minutes or 0,
        "completion_status": row.completion_status or "unknown",
        "score": row.score,
        "payload": _parse_json(row.payload_json),
        "created_at": _iso(row.created_at),
    }


def _intervention_item(row: LearningInterventionRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "t_stage": row.t_stage or "",
        "action": row.action or "",
        "reason": row.reason or "",
        "cooldown_hours": row.cooldown_hours or 0,
        "feedback": row.feedback or "",
        "effect_status": row.effect_status or "pending",
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _candidate_item(row: MemoryCandidate) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title or "",
        "content": row.content or "",
        "importance": row.importance or "normal",
        "reason": row.reason or "",
        "source": row.source or "",
        "status": row.status or "pending",
        "confidence": row.confidence,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _summary_item(row: MemorySummary) -> dict[str, Any]:
    return {
        "id": row.id,
        "description": row.description or "",
        "key_facts": _parse_json(row.key_facts),
        "compression_reason": row.compression_reason or "",
        "confidence": row.confidence,
        "created_at": _iso(row.created_at),
    }


def _feedback_item(row: FeedbackRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "feedback_type": row.feedback_type or "",
        "rating": row.rating or "",
        "reason": row.reason or "",
        "user_feedback": row.user_feedback or "",
        "created_at": _iso(row.created_at),
    }


def _agent_trace_item(row: AgentEvent) -> dict[str, Any]:
    return {
        "agent": row.agent_name,
        "event_type": row.event_type,
        "input_summary": row.input_summary or "",
        "output_summary": row.output_summary or "",
        "created_at": _iso(row.created_at),
    }


def build_learner_context_brief(db: Session, user_id: int) -> LearnerContextBrief:
    profile_row = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    profile = build_learner_profile_payload(profile_row or {})

    memories = (
        db.query(PersonalizationMemory)
        .filter(PersonalizationMemory.user_id == user_id, PersonalizationMemory.is_active.is_(True))
        .order_by(PersonalizationMemory.updated_at.desc(), PersonalizationMemory.id.desc())
        .all()
    )
    memory_items = [_memory_item(row) for row in memories]
    short_term_items = [item for item in memory_items if item["category"] in {"short_term", "feedback", "mistake"}]
    long_term_items = [item for item in memory_items if item["category"] in {"long_term", "preference", "note"}]

    plans = (
        db.query(LearningPlanRecord)
        .filter(LearningPlanRecord.user_id == user_id)
        .order_by(LearningPlanRecord.updated_at.desc(), LearningPlanRecord.id.desc())
        .all()
    )
    plan_items = [_plan_item(row) for row in plans]

    mastery_rows = (
        db.query(LearnerKnowledgeMastery)
        .filter(LearnerKnowledgeMastery.user_id == user_id)
        .order_by(LearnerKnowledgeMastery.updated_at.desc(), LearnerKnowledgeMastery.id.desc())
        .all()
    )
    mastery_items = [_mastery_item(row) for row in mastery_rows]

    activities = (
        db.query(LearningActivityRecord)
        .filter(LearningActivityRecord.user_id == user_id)
        .order_by(LearningActivityRecord.created_at.desc(), LearningActivityRecord.id.desc())
        .limit(20)
        .all()
    )
    interventions = (
        db.query(LearningInterventionRecord)
        .filter(LearningInterventionRecord.user_id == user_id)
        .order_by(LearningInterventionRecord.created_at.desc(), LearningInterventionRecord.id.desc())
        .limit(20)
        .all()
    )
    candidates = (
        db.query(MemoryCandidate)
        .filter(MemoryCandidate.user_id == user_id, MemoryCandidate.status == "pending")
        .order_by(MemoryCandidate.created_at.desc(), MemoryCandidate.id.desc())
        .limit(20)
        .all()
    )
    summaries = (
        db.query(MemorySummary)
        .filter(MemorySummary.user_id == user_id)
        .order_by(MemorySummary.created_at.desc(), MemorySummary.id.desc())
        .limit(10)
        .all()
    )
    feedback = (
        db.query(FeedbackRecord)
        .filter(FeedbackRecord.user_id == user_id)
        .order_by(FeedbackRecord.created_at.desc(), FeedbackRecord.id.desc())
        .limit(10)
        .all()
    )
    events = (
        db.query(AgentEvent)
        .filter(AgentEvent.user_id == user_id)
        .order_by(AgentEvent.created_at.desc(), AgentEvent.id.desc())
        .limit(10)
        .all()
    )

    weak_kp_ids = [item["kp_id"] for item in mastery_items if item["mastery_status"] == "weak" or (item["mastery"] or 0) < 0.6]
    next_reviews = [item for item in mastery_items if item["next_review_at"]]
    active_plans = [item for item in plan_items if item["status"] == "active"]

    return LearnerContextBrief(
        learner_id=str(user_id),
        learner_group=profile.get("learner_group") or "未选择用户群体",
        goal=profile.get("learning_goal") or "未填写学习目标",
        source_scope="memory_agent",
        source_id=f"learner:{user_id}",
        kp_ids=weak_kp_ids,
        risk_notes=[],
        confidence=0.8,
        agent_trace=[_agent_trace_item(row) for row in events],
        profile=profile,
        short_term_memory={
            "active_items": short_term_items,
            "pending_candidates": [_candidate_item(row) for row in candidates],
            "summaries": [_summary_item(row) for row in summaries],
        },
        long_term_memory={"stable_items": long_term_items, "mastery": mastery_items, "next_reviews": next_reviews},
        planning_memory={"active_plans": active_plans, "plans": plan_items},
        learning_state={
            "weak_kp_ids": weak_kp_ids,
            "mastery_by_kp": {item["kp_id"]: item for item in mastery_items},
            "recent_activities": [_activity_item(row) for row in activities],
            "interventions": [_intervention_item(row) for row in interventions],
            "feedback": [_feedback_item(row) for row in feedback],
        },
    )

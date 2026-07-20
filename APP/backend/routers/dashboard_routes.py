from __future__ import annotations

import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from APP.backend.auth import get_current_user
from APP.backend.checkin_service import build_checkin_status
from APP.backend.dashboard_service import build_dashboard_payload
from APP.backend.database import (
    AgentEvent,
    DbMessage,
    DbSession,
    LearningActivityRecord,
    LearningInterventionRecord,
    PersonalizationMemory,
    UserModel,
    get_db,
)
from APP.backend.health_memory import get_or_create_profile
from APP.backend.learning_target_service import (
    get_active_learning_target,
    serialize_learning_target,
)
from APP.backend.system_data_service import (
    record_dashboard_recommendation_click,
    record_dashboard_recommendations_view,
    rebuild_system_data,
    system_data_payload,
)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


class RecommendationClickRequest(BaseModel):
    recommendation_key: str = Field(min_length=1, max_length=120)
    recommendation_view_id: str = Field(min_length=1, max_length=160)


def _json_payload(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}



def _build_difficulty_notice(db: Session, user_id: int) -> dict | None:
    record = db.query(LearningInterventionRecord).filter(
        LearningInterventionRecord.user_id == user_id,
        LearningInterventionRecord.effect_status == "pending",
    ).order_by(LearningInterventionRecord.created_at.desc(), LearningInterventionRecord.id.desc()).first()
    if not record:
        return None
    return {
        "notice_id": f"NOTICE_DIFFICULTY_DROP_{record.id}",
        "type": "difficulty_adjustment",
        "title": "是否调整今日任务难度？",
        "message": record.reason or "系统发现近期学习状态变化，建议确认是否调整任务难度。",
        "actions": ["accept", "too_easy", "too_hard", "not_relevant"],
        "intervention_id": record.id,
        "current_difficulty": "",
        "suggested_difficulty": record.action,
    }


def _build_announcements(db: Session, user_id: int, profile) -> list[dict]:
    recent = db.query(LearningActivityRecord).filter(
        LearningActivityRecord.user_id == user_id,
    ).order_by(LearningActivityRecord.created_at.desc(), LearningActivityRecord.id.desc()).limit(20).all()
    configured_time = profile.diet_restrictions or ""
    for item in recent:
        observed_time = _json_payload(item.payload_json).get("observed_time_slot")
        if observed_time and configured_time and observed_time not in configured_time:
            return [{
                "notice_id": f"NOTICE_PROFILE_CONFLICT_{user_id}_{item.id}",
                "type": "profile_conflict",
                "severity": "medium",
                "title": "学习时段可能有变化",
                "message": f"你设置的学习时段是“{configured_time}”，但最近学习行为更多出现在“{observed_time}”。是否需要更新？",
                "actions": ["accept_new", "keep_original", "postpone"],
                "source": "dashboard_service",
            }]
    return []


@router.get("/home")
def get_dashboard_home(current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = get_or_create_profile(db, current_user.id)
    learning_target = serialize_learning_target(
        get_active_learning_target(db, current_user.id)
    )
    active_memories = db.query(PersonalizationMemory).filter(
        PersonalizationMemory.user_id == current_user.id,
        PersonalizationMemory.is_active == True,
    ).order_by(PersonalizationMemory.updated_at.desc()).limit(6).all()
    recent_events = db.query(AgentEvent).filter(
        AgentEvent.user_id == current_user.id,
    ).order_by(AgentEvent.created_at.desc()).limit(6).all()
    recent_sessions = db.query(DbSession).filter(
        DbSession.user_id == current_user.id,
    ).order_by(DbSession.created_at.desc()).limit(6).all()

    profile_payload = {
        "display_name": profile.display_name,
        "constitution": profile.constitution,
        "health_goals": profile.health_goals,
        "diet_restrictions": profile.diet_restrictions,
        "exercise_preferences": profile.exercise_preferences,
        "medical_history": profile.medical_history,
        "custom_needs": profile.custom_needs,
    }
    memory_payload = [
        {
            "category": item.category,
            "source": item.source,
            "title": item.title,
            "content": item.content,
        }
        for item in active_memories
    ]
    event_payload = [
        {
            "agent_name": item.agent_name,
            "event_type": item.event_type,
            "output_summary": item.output_summary,
        }
        for item in recent_events
    ]

    session_payload = []
    for item in recent_sessions:
        latest_message = db.query(DbMessage).filter(
            DbMessage.session_id == item.id,
        ).order_by(DbMessage.created_at.desc()).first()
        updated_at = latest_message.created_at if latest_message and latest_message.created_at else item.created_at
        session_payload.append(
            {
                "id": item.id,
                "title": item.title,
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "updated_at": updated_at.isoformat() if updated_at else None,
            }
        )

    session_payload.sort(key=lambda session: session.get("updated_at") or session.get("created_at") or "", reverse=True)

    payload = build_dashboard_payload(
        user={"username": current_user.username, "role": current_user.role},
        profile=profile_payload,
        active_memories=memory_payload,
        recent_events=event_payload,
        recent_sessions=session_payload,
        learning_target=learning_target,
        announcements=_build_announcements(db, current_user.id, profile),
        checkin_status=build_checkin_status(db, current_user.id),
        difficulty_notice=_build_difficulty_notice(db, current_user.id),
    )
    recommendation_view = record_dashboard_recommendations_view(
        db,
        user_id=current_user.id,
        recommendation_keys=tuple(item["key"] for item in payload["recommendations"]),
    )
    snapshot = rebuild_system_data(db, user_id=current_user.id)
    db.commit()
    return {
        **payload,
        "recommendation_view_id": recommendation_view.resource_id,
        "system_data": system_data_payload(snapshot),
    }


@router.post("/recommendations/click")
def record_recommendation_click(
    request: RecommendationClickRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        snapshot = record_dashboard_recommendation_click(
            db,
            user_id=current_user.id,
            recommendation_key=request.recommendation_key,
            recommendation_view_id=request.recommendation_view_id,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"system_data": system_data_payload(snapshot)}

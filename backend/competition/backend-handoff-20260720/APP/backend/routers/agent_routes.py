from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from APP.backend.agent_contracts import EvidencePack, ExpertArtifact, LearnerContextBrief
from APP.backend.agent_orchestrator_service import OrchestrationRequest, PlanValidationError, run_agent_orchestration
from APP.backend.auth import get_current_user
from APP.backend.cross_validation_service import cross_validate_output
from APP.backend.database import AgentEvent, DbSession, LearningPlanRecord, UserModel, get_db
from APP.backend.diagnosis_agent_service import (
    build_diagnosis_snapshot,
    build_learning_profile,
    build_report_summary,
    get_onboarding_status,
)
from APP.backend.learning_plan_service import create_or_update_learning_plan_record, generate_learning_plan
from APP.backend.memory_agent_service import build_learner_context_brief

router = APIRouter(prefix="/agent", tags=["Agent"])
logger = logging.getLogger(__name__)


class PlanGenerateRequest(BaseModel):
    persist: bool = True


class CrossValidateRequest(BaseModel):
    artifact: ExpertArtifact
    evidence_pack: EvidencePack
    session_id: str | None = None

def _extract_daily_minutes(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        minutes = int(value)
        return minutes if minutes > 0 else None
    text = str(value)
    daily_minutes_match = re.search(r"(?:每天|每日)\D*(\d+(?:\.\d+)?)\s*分钟", text)
    if daily_minutes_match:
        minutes = int(float(daily_minutes_match.group(1)))
        return minutes if minutes > 0 else None
    daily_hours_match = re.search(r"(?:每天|每日)\D*(\d+(?:\.\d+)?)\s*小时", text)
    if daily_hours_match:
        minutes = int(float(daily_hours_match.group(1)) * 60)
        return minutes if minutes > 0 else None
    first_number_match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", text)
    if not first_number_match:
        return None
    minutes = int(float(first_number_match.group(1)))
    return minutes if minutes > 0 else None



def _stabilize_plan_payload(payload: dict[str, Any], brief: LearnerContextBrief) -> dict[str, Any]:
    stable_payload = dict(payload)
    plan_summary = dict(stable_payload.get("plan_summary") or {})
    preferred_goal = (
        (brief.profile or {}).get("learning_goal")
        or brief.goal
        or plan_summary.get("goal")
        or "未填写学习目标"
    )
    current_goal = str(plan_summary.get("goal") or "").strip()
    if not current_goal or current_goal.startswith("优先补强 "):
        plan_summary["goal"] = preferred_goal
    if not plan_summary.get("learner_group"):
        plan_summary["learner_group"] = brief.learner_group
    constraints = dict(stable_payload.get("constraints") or {})
    preferred_minutes = _extract_daily_minutes((brief.profile or {}).get("time_constraints"))
    if preferred_minutes is not None:
        current_minutes = _extract_daily_minutes(constraints.get("daily_available_minutes"))
        if current_minutes is None or current_minutes == 30:
            constraints["daily_available_minutes"] = preferred_minutes
    if constraints:
        stable_payload["constraints"] = constraints
    stable_payload["plan_summary"] = plan_summary
    return stable_payload



def _build_plan_payload(db: Session, user_id: int, *, persist: bool) -> dict[str, Any]:
    onboarding = get_onboarding_status(db, user_id)
    diagnosis = build_diagnosis_snapshot(db, user_id, persist=False)
    learning_profile = build_learning_profile(db, user_id)
    brief = build_learner_context_brief(db, user_id)
    if persist:
        payload = create_or_update_learning_plan_record(
            db,
            user_id=user_id,
            learner_group=onboarding["learner_group"],
            onboarding_answers=onboarding.get("survey_answers", {}),
            diagnosis_report=diagnosis,
            learning_profile=learning_profile,
        )
    else:
        payload = generate_learning_plan(
            learner_id=str(user_id),
            learner_group=onboarding["learner_group"],
            onboarding_answers=onboarding.get("survey_answers", {}),
            diagnosis_report=diagnosis,
            learning_profile=learning_profile,
        )
    return _stabilize_plan_payload(payload, brief)


@router.get("/context/brief")
def get_context_brief(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    brief = build_learner_context_brief(db, current_user.id)
    return brief.model_dump()


@router.post("/plan/generate")
def generate_plan(
    req: PlanGenerateRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payload = _build_plan_payload(db, current_user.id, persist=req.persist)
    return payload


@router.get("/plan/summary")
def get_plan_summary(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = (
        db.query(LearningPlanRecord)
        .filter(LearningPlanRecord.user_id == current_user.id, LearningPlanRecord.plan_type == "diagnosis_driven")
        .first()
    )
    if record is not None:
        payload = record.payload_json
        try:
            parsed = json.loads(payload or "{}")
        except ValueError:
            parsed = {}
        if isinstance(parsed, dict):
            parsed["record_id"] = record.id
            return parsed

    payload = _build_plan_payload(db, current_user.id, persist=False)
    return payload


@router.get("/diagnosis/report")
def get_diagnosis_report(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return build_report_summary(db, current_user.id)


@router.get("/trace/recent")
def get_recent_trace(
    limit: int = 10,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    capped_limit = max(1, min(limit, 50))
    rows = (
        db.query(AgentEvent)
        .filter(AgentEvent.user_id == current_user.id)
        .order_by(AgentEvent.created_at.desc(), AgentEvent.id.desc())
        .limit(capped_limit)
        .all()
    )
    return {
        "items": [
            {
                "agent_name": row.agent_name,
                "event_type": row.event_type,
                "input_summary": row.input_summary,
                "output_summary": row.output_summary,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    }


@router.post("/orchestrate")
def orchestrate_agent_run(
    req: OrchestrationRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        result = run_agent_orchestration(
            db,
            user_id=current_user.id,
            request=req,
            raise_on_validation_error=True,
        )
    except PlanValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception as exc:
        logger.exception("Agent orchestration failed for user_id=%s", current_user.id)
        raise HTTPException(
            status_code=500,
            detail={"code": "orchestration_failed", "message": "Agent orchestration failed"},
        ) from exc
    if result.get("status") == "failed":
        raise HTTPException(
            status_code=500,
            detail={
                "code": result.get("error_code") or "orchestration_failed",
                "message": result.get("error") or "Agent orchestration failed",
            },
        )
    return result


@router.post("/cross-validate")
def cross_validate(
    req: CrossValidateRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not req.session_id:
        raise HTTPException(status_code=400, detail="session_id is required for platform artifact validation")
    session = (
        db.query(DbSession)
        .filter(DbSession.id == req.session_id, DbSession.user_id == current_user.id)
        .first()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    expected_artifact = req.artifact.model_dump(mode="json")
    expected_evidence_pack = req.evidence_pack.model_dump(mode="json")
    source_marker = None
    candidate_events = (
        db.query(AgentEvent)
        .filter(
            AgentEvent.user_id == current_user.id,
            AgentEvent.session_id == req.session_id,
        )
        .all()
    )
    for event in candidate_events:
        try:
            payload = json.loads(event.payload or "{}")
        except ValueError:
            continue
        try:
            saved_artifact = ExpertArtifact(**payload.get("artifact", {})).model_dump(mode="json")
            saved_evidence_pack = EvidencePack(**payload.get("evidence_pack", {})).model_dump(mode="json")
        except ValueError:
            continue
        if saved_artifact == expected_artifact and saved_evidence_pack == expected_evidence_pack:
            source_marker = event
            break
    if source_marker is None:
        raise HTTPException(status_code=400, detail="session artifact source not found or artifact was modified")
    learner_context = build_learner_context_brief(db, current_user.id)
    diagnosis_report = build_diagnosis_snapshot(db, current_user.id, persist=False)
    review, summary = cross_validate_output(
        artifact=req.artifact,
        evidence_pack=req.evidence_pack,
        learner_context=learner_context,
        diagnosis_report=diagnosis_report,
        db=db,
        user_id=current_user.id,
        session_id=req.session_id,
    )
    return {
        "review": review.model_dump(),
        "summary": summary,
    }

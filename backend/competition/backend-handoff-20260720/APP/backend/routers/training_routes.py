from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from APP.backend.auth import get_current_user
from APP.backend.database import (
    AgentEvent,
    CorePracticeSubmissionClaim,
    LearningActivityRecord,
    LearningAttemptRecord,
    LearningInterventionRecord,
    LearningQuestion,
    LearningQuestionAttempt,
    MistakeRecord,
    PersonalizationMemory,
    KnowledgePoint,
    QuestionAttempt,
    QuestionBankItem,
    UserQuestionItem,
    UserQuestionPracticeClaim,
    UserModel,
    get_db,
)
from APP.backend.diagnosis_agent_service import (
    build_diagnosis_snapshot,
    build_learning_profile,
    build_report_summary,
    get_onboarding_status,
    submit_onboarding_survey,
)
from APP.backend.health_memory import get_or_create_profile
from APP.backend import exam_learning_service
from APP.backend.learning_target_service import (
    LearningTargetLockedError,
    LearningTargetValidationError,
    requires_official_exam_repository,
    serialize_learning_target,
    set_active_learning_target,
)
from APP.backend.learning_plan_service import create_or_update_learning_plan_record
from APP.backend.onboarding_template_service import (
    OnboardingTemplateError,
    apply_onboarding_defaults,
    get_group_templates,
)
from APP.backend.checkin_service import record_daily_checkin
from APP.backend.grading_application_service import (
    apply_practice_grading,
    from_legacy_route_request,
)
from APP.backend.core_learning_service import (
    record_practice_outcome,
    resolve_controlled_practice_submission,
)
from APP.backend.system_data_service import rebuild_system_data
from APP.backend.training_service import grade_practice_submission
from APP.backend.expert_agent_service import generate_question_explanation

router = APIRouter(prefix="/training", tags=["Training"])
stable_practice_router = APIRouter(prefix="/v1/workshop/practice", tags=["Workshop Practice"])

OBJECTIVE_QUESTION_TYPES = {
    "single_choice", "multiple_choice", "fill_blank", "true_false",
}
CASE_QUESTION_TYPES = {"short_answer", "case_quiz"}
QUESTION_TYPE_ALIASES = {
    "单选题": "single_choice",
    "单项选择题": "single_choice",
    "多选题": "multiple_choice",
    "多项选择题": "multiple_choice",
    "填空题": "fill_blank",
    "判断题": "true_false",
    "简答题": "short_answer",
    "案例题": "case_quiz",
}

# Module-level seam lets route tests control only the application runner.
practice_grading_runner = grade_practice_submission


class PracticeGradeRequest(BaseModel):
    question_id: str = "manual-question"
    question_type: str = "short_answer"
    stem: str
    student_answer: str = ""
    standard_answer: str = ""
    rubric: str = ""
    knowledge_points: list[str] = Field(default_factory=list)
    knowledge_point_names: list[str] = Field(default_factory=list)
    difficulty: int = 2
    request_id: str | None = Field(default=None, max_length=120)


class OnboardingSurveyRequest(BaseModel):
    learner_group: str = ""
    background: dict[str, Any] = Field(default_factory=dict)
    goals: dict[str, Any] = Field(default_factory=dict)
    preferences: dict[str, Any] = Field(default_factory=dict)
    special_requirements: dict[str, Any] = Field(default_factory=dict)
    locked_fields: list[str] = Field(default_factory=list)
    daily_available_minutes: int | None = None
    preferred_time_slot: str = ""
    resource_preference: list[str] = Field(default_factory=list)
    learning_mode: str = ""
    difficulty_preference: str = ""
    current_difficulties: list[str] | str | None = None
    difficulty_notes: str = ""
    long_term_goal: str = ""
    short_term_goal: str = ""
    target_exam_or_course: str = ""
    target_type: str | None = None
    exam_track_id: str | None = None
    exam_date: datetime | None = None
    is_locked: bool = True
    lock_reason: str = "用户手动选择"


class DifficultyFeedbackRequest(BaseModel):
    notice_id: str
    action: str
    reason: str = ""
    current_difficulty: str = ""
    suggested_difficulty: str = ""


class InterventionFeedbackRequest(BaseModel):
    action: str
    reason: str = ""


def _profile_payload(profile) -> dict[str, Any]:
    return {
        "display_name": profile.display_name,
        "constitution": profile.constitution,
        "health_goals": profile.health_goals,
        "diet_restrictions": profile.diet_restrictions,
        "exercise_preferences": profile.exercise_preferences,
        "medical_history": profile.medical_history,
        "custom_needs": profile.custom_needs,
    }


def _active_memory_payload(db: Session, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    memories = db.query(PersonalizationMemory).filter(
        PersonalizationMemory.user_id == user_id,
        PersonalizationMemory.is_active == True,
    ).order_by(PersonalizationMemory.updated_at.desc()).limit(limit).all()
    return [
        {
            "category": item.category,
            "title": item.title,
            "content": item.content,
            "importance": item.importance,
        }
        for item in memories
    ]


def _recent_event_payload(db: Session, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    events = db.query(AgentEvent).filter(
        AgentEvent.user_id == user_id,
    ).order_by(AgentEvent.created_at.desc()).limit(limit).all()
    return [
        {
            "agent_name": item.agent_name,
            "event_type": item.event_type,
            "output_summary": item.output_summary,
        }
        for item in events
    ]


def _record_training_event(db: Session, user_id: int, event_type: str, output_summary: str, payload: dict[str, Any]) -> None:
    db.add(AgentEvent(
        user_id=user_id,
        session_id=None,
        agent_name="training_orchestrator",
        event_type=event_type,
        input_summary="Phase 4 training loop",
        output_summary=output_summary,
        payload=json.dumps(payload, ensure_ascii=False),
    ))


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _record_practice_outcome(db: Session, user_id: int, submission: dict[str, Any], payload: dict[str, Any]) -> None:
    grading = payload.get("grading", {})
    question_id = grading.get("question_id") or submission.get("question_id") or "manual-question"
    kp_ids = submission.get("knowledge_points") or []
    score = float(grading.get("score") or 0)
    is_correct = bool(grading.get("is_correct"))
    timestamp = _now()

    db.add(QuestionAttempt(
        user_id=user_id,
        question_id=question_id,
        answer=submission.get("student_answer", ""),
        is_correct=is_correct,
        score=score,
        kp_ids_json=json.dumps(kp_ids, ensure_ascii=False),
        feedback=grading.get("analysis", ""),
        created_at=timestamp,
    ))
    db.add(LearningActivityRecord(
        user_id=user_id,
        activity_type="question_attempt",
        resource_id=question_id,
        resource_type="question",
        duration_minutes=10,
        completion_status="completed" if is_correct else "needs_review",
        score=score,
        payload_json=json.dumps({**submission, "grading": grading}, ensure_ascii=False),
        created_at=timestamp,
    ))
    if is_correct:
        return

    mistake = payload.get("mistake_record") or {}
    db.add(MistakeRecord(
        user_id=user_id,
        question_id=question_id,
        kp_ids_json=json.dumps(kp_ids, ensure_ascii=False),
        error_type=mistake.get("error_type") or "练习错因",
        summary=mistake.get("content") or grading.get("analysis", ""),
        status="active",
        created_at=timestamp,
        updated_at=timestamp,
    ))


@router.post("/checkin")
def daily_checkin(current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    return record_daily_checkin(db, current_user.id)


@router.post("/difficulty-feedback")
def submit_difficulty_feedback(req: DifficultyFeedbackRequest, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    db.add(LearningActivityRecord(
        user_id=current_user.id,
        activity_type="difficulty_feedback",
        resource_id=req.notice_id,
        resource_type="intervention_notice",
        duration_minutes=0,
        completion_status=req.action,
        score=None,
        payload_json=json.dumps(payload, ensure_ascii=False),
        created_at=_now(),
    ))
    db.commit()
    return {"success": True, "feedback_type": "difficulty_adjustment", "notice_id": req.notice_id}


@router.post("/interventions/{intervention_id}/feedback")
def submit_intervention_feedback(intervention_id: int, req: InterventionFeedbackRequest, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    record = db.query(LearningInterventionRecord).filter(
        LearningInterventionRecord.id == intervention_id,
        LearningInterventionRecord.user_id == current_user.id,
    ).first()
    if not record:
        return {"success": False, "detail": "intervention_not_found"}
    record.feedback = json.dumps({"action": req.action, "reason": req.reason}, ensure_ascii=False)
    record.effect_status = "user_feedback_received"
    db.commit()
    return {"success": True, "intervention_id": intervention_id, "effect_status": record.effect_status}


def _user_question_payload(question: UserQuestionItem, submission: dict[str, Any]) -> dict[str, Any]:
    return {
        **submission,
        "question_id": question.question_id,
        "stem": question.stem,
        "standard_answer": question.answer,
        "rubric": question.analysis,
        "question_type": question.question_type,
        "knowledge_points": json.loads(question.kp_ids_json or "[]"),
        "difficulty": 2,
    }


def _question_kp_ids(question: QuestionBankItem) -> list[str]:
    try:
        value = json.loads(question.kp_ids_json or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        str(item).strip()
        for item in value
        if isinstance(item, str) and item.strip()
    ))


def _knowledge_point_names(db: Session, kp_ids: list[str]) -> list[str]:
    rows = db.query(KnowledgePoint).filter(KnowledgePoint.kp_id.in_(kp_ids)).all() if kp_ids else []
    names = {
        row.kp_id: row.name
        for row in rows
        if str(row.name or "").strip() and str(row.name).strip() != str(row.kp_id)
    }
    return [names[kp_id] for kp_id in kp_ids if kp_id in names]


def _normalized_question_type(value: str | None) -> str:
    text = str(value or "").strip()
    return QUESTION_TYPE_ALIASES.get(text, text)


def _matches_practice_mode(question_type: str | None, mode: str) -> bool:
    normalized = _normalized_question_type(question_type)
    if mode == "objective":
        return normalized in OBJECTIVE_QUESTION_TYPES
    if mode == "case":
        return normalized in CASE_QUESTION_TYPES
    return True


def _decode_options(value: str | None) -> list[Any]:
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    if isinstance(decoded, dict):
        return [{"key": str(key), "value": item} for key, item in decoded.items()]
    return decoded if isinstance(decoded, list) else []


def _public_question_options(db: Session, question_id: str) -> list[Any]:
    row = db.query(LearningQuestion).filter(
        LearningQuestion.question_id == question_id,
    ).one_or_none()
    return _decode_options(row.options_json) if row is not None else []


@router.get("/practice/next")
@stable_practice_router.get("/next")
def next_practice_question(
    kp_id: str | None = Query(default=None, min_length=1, max_length=120),
    scope: str = Query(default="public", pattern="^(public|user|all)$"),
    mode: str = Query(default="all", pattern="^(all|objective|case)$"),
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if scope in {"user", "all"}:
        attempted_user_question_ids = {
            question_id
            for question_id, in db.query(QuestionAttempt.question_id).filter(
                QuestionAttempt.user_id == current_user.id,
            ).all()
        }
        user_candidates = []
        for question in db.query(UserQuestionItem).filter_by(
            owner_user_id=current_user.id,
            status="active",
        ).all():
            question_kp_ids = json.loads(question.kp_ids_json or "[]")
            if (not kp_id or kp_id in question_kp_ids) and _matches_practice_mode(
                question.question_type, mode
            ):
                user_candidates.append((question, question_kp_ids))
        user_candidates.sort(key=lambda item: (
            item[0].question_id in attempted_user_question_ids,
            item[0].question_id,
        ))
        if user_candidates:
            question, question_kp_ids = user_candidates[0]
            request_id = str(uuid.uuid4())
            db.add(UserQuestionPracticeClaim(
                user_id=current_user.id,
                request_id=request_id,
                question_id=question.question_id,
            ))
            db.commit()
            return {
                "available": True,
                "kp_id": kp_id,
                "question": {
                    "question_id": question.question_id,
                    "question_type": _normalized_question_type(question.question_type),
                    "stem": question.stem,
                    "options": _decode_options(question.options_json),
                    "kp_ids": question_kp_ids,
                    "kp_names": _knowledge_point_names(db, question_kp_ids),
                    "difficulty": 2,
                    "difficulty_source": "system_default",
                    "request_id": request_id,
                    "source_scope": "user",
                },
            }
        if scope == "user":
            return {"available": False, "kp_id": kp_id, "question": None}
    registered_kp_ids = {
        row.kp_id
        for row in db.query(KnowledgePoint).filter(
            KnowledgePoint.status == "active"
        ).all()
    }
    candidates = []
    for question in db.query(QuestionBankItem).filter(
        QuestionBankItem.status == "active"
    ).all():
        question_kp_ids = _question_kp_ids(question)
        if kp_id and kp_id not in question_kp_ids:
            continue
        if not _matches_practice_mode(question.question_type, mode):
            continue
        if not question_kp_ids or not set(question_kp_ids) <= registered_kp_ids:
            continue
        candidates.append((question, question_kp_ids))
    attempted_question_ids = {
        question_id for question_id, in db.query(QuestionAttempt.question_id).filter(
            QuestionAttempt.user_id == current_user.id,
        ).all()
    }
    attempted_question_ids.update(
        question_id
        for question_id, in db.query(LearningQuestionAttempt.question_id).filter(
            LearningQuestionAttempt.user_id == current_user.id,
        ).all()
    )
    candidates.sort(key=lambda item: (
        item[0].question_id in attempted_question_ids,
        -float(item[0].quality_score or 0),
        abs(float(item[0].difficulty or 2) - 2),
        item[0].question_id,
    ))
    if not candidates:
        return {"available": False, "kp_id": kp_id, "question": None}

    question, question_kp_ids = candidates[0]
    request_id = str(uuid.uuid4())
    controlled = resolve_controlled_practice_submission(
        db,
        {"question_id": question.question_id},
    )
    if controlled is None:
        raise HTTPException(status_code=409, detail="question is not available for controlled practice")
    db.add(CorePracticeSubmissionClaim(
        user_id=current_user.id,
        request_id=request_id,
        question_id=question.question_id,
    ))
    db.commit()
    return {
        "available": True,
        "kp_id": kp_id,
        "question": {
            "question_id": question.question_id,
            "question_type": _normalized_question_type(question.question_type),
            "stem": question.stem,
            "options": _public_question_options(db, question.question_id),
            "kp_ids": question_kp_ids,
            "kp_names": _knowledge_point_names(db, question_kp_ids),
            "difficulty": int(question.difficulty or 2),
            "difficulty_source": "question_bank_snapshot" if question.difficulty else "system_default",
            "request_id": request_id,
            "source_scope": "public",
        },
    }


@router.post("/practice/grade")
@stable_practice_router.post("/grade")
def grade_practice(
    req: PracticeGradeRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    submission = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    if submission.get("request_id") and not str(submission.get("student_answer") or "").strip():
        raise HTTPException(status_code=422, detail="student_answer is required")
    controlled_submission = resolve_controlled_practice_submission(db, submission)
    private_question = db.query(UserQuestionItem).filter_by(
        question_id=submission.get("question_id"),
        owner_user_id=current_user.id,
        status="active",
    ).one_or_none()
    private_claim = None
    private_controlled = private_question is not None
    if (controlled_submission is not None or private_controlled) and not submission.get("request_id"):
        raise HTTPException(
            status_code=400,
            detail="controlled practice requires an issued request_id",
        )
    if private_controlled:
        private_claim = db.query(UserQuestionPracticeClaim).filter_by(
            user_id=current_user.id,
            request_id=submission["request_id"],
            question_id=private_question.question_id,
        ).one_or_none()
        controlled_submission = _user_question_payload(private_question, submission)
    if submission.get("request_id") and controlled_submission is None:
        raise HTTPException(status_code=400, detail="request_id requires an active registered question")
    controlled_request_id = submission.get("request_id") if controlled_submission is not None else None
    if controlled_request_id:
        cutoff = _now() - timedelta(minutes=30)
        try:
            claim_model = UserQuestionPracticeClaim if private_controlled else CorePracticeSubmissionClaim
            consumed = db.query(claim_model).filter(
                claim_model.user_id == current_user.id,
                claim_model.request_id == controlled_request_id,
                claim_model.question_id == controlled_submission["question_id"],
                claim_model.created_at >= cutoff,
            ).delete(synchronize_session=False)
            db.flush()
        except OperationalError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="practice submission is already being processed") from exc
        if consumed != 1:
            replayed = db.query(LearningAttemptRecord).filter_by(
                learner_id=current_user.id,
                request_id=controlled_request_id,
            ).one_or_none() if private_controlled else db.query(LearningQuestionAttempt).filter_by(
                user_id=current_user.id,
                request_id=controlled_request_id,
            ).one_or_none()
            expired = db.query(claim_model).filter_by(
                user_id=current_user.id,
                request_id=controlled_request_id,
                question_id=controlled_submission["question_id"],
            ).one_or_none()
            if expired is not None:
                db.delete(expired)
                db.commit()
                raise HTTPException(status_code=410, detail="practice request expired")
            detail = (
                "practice submission already processed"
                if replayed is not None
                else "practice request was not issued to this user"
            )
            raise HTTPException(status_code=409 if replayed else 400, detail=detail)
    memories = _active_memory_payload(db, current_user.id)
    profile = get_or_create_profile(
        db,
        current_user.id,
        commit=controlled_request_id is None,
    )
    grading_submission = controlled_submission or submission
    grading_submission = {
        **grading_submission,
        "knowledge_point_names": _knowledge_point_names(
            db, list(grading_submission.get("knowledge_points") or ())
        ) or list(submission.get("knowledge_point_names") or ()),
    }
    if private_controlled:
        runner_payload = practice_grading_runner(
            profile=_profile_payload(profile),
            memories=memories,
            submission={
                "question_id": grading_submission["question_id"],
                "question_type": grading_submission["question_type"],
                "stem": grading_submission["stem"],
                "student_answer": grading_submission["student_answer"],
                "standard_answer": grading_submission["standard_answer"],
                "rubric": grading_submission["rubric"],
                "knowledge_points": grading_submission["knowledge_points"],
                "knowledge_point_names": grading_submission["knowledge_point_names"],
                "difficulty": grading_submission["difficulty"],
            },
        )
        raw_grading = runner_payload.get("grading", runner_payload)
        audit_payload = runner_payload.get("audit") if isinstance(runner_payload, dict) else None
        grading = {
            "question_id": submission["question_id"],
            "question_type": grading_submission["question_type"],
            "score": raw_grading.get("score"),
            "is_correct": raw_grading.get("is_correct"),
            "analysis": raw_grading.get("analysis", raw_grading.get("feedback", "")),
            "error_type": raw_grading.get("error_type", "none"),
            "grading_source": raw_grading.get("grading_source", "unknown"),
            "confidence": raw_grading.get("confidence"),
            "dimension_scores": raw_grading.get("dimension_scores", {}),
        }
        cached_explanation = str(private_question.analysis or "").strip()
        generated_explanation = ""
        if not cached_explanation:
            generated_explanation = generate_question_explanation(
                submission={
                    "question_id": grading_submission["question_id"],
                    "question_type": grading_submission["question_type"],
                    "stem": grading_submission["stem"],
                    "standard_answer": grading_submission["standard_answer"],
                    "rubric": grading_submission["rubric"],
                    "knowledge_points": grading_submission["knowledge_points"],
                    "knowledge_point_names": grading_submission["knowledge_point_names"],
                    "difficulty": grading_submission["difficulty"],
                }
            )
        if cached_explanation:
            grading["question_explanation"] = cached_explanation
            grading["explanation_source"] = "user_question_cache"
        elif generated_explanation:
            private_question.analysis = generated_explanation
            grading["question_explanation"] = generated_explanation
            grading["explanation_source"] = "generated_on_first_attempt"
        if not isinstance(audit_payload, dict) or audit_payload.get("decision") != "pass":
            db.commit()
            return {
                "grading": grading,
                "audit": audit_payload or {
                    "decision": "needs_human_review",
                    "reason": "主观题审核结果缺失。",
                    "confidence": 0.0,
                },
                "mistake_record": None,
                "attempt_id": None,
                "attempt_item_id": None,
                "grading_artifact_id": None,
                "audit_id": None,
                "writeback": {
                    "status": "withheld_pending_audit",
                    "receipt_id": None,
                    "mistake_ids": [],
                    "review_task_ids": [],
                },
                "agent_trace": runner_payload.get("agent_trace", []),
            }
        attempt_id = str(uuid.uuid4())
        is_correct = bool(grading.get("is_correct"))
        score = (
            float(grading["score"])
            if isinstance(grading.get("score"), (int, float))
            else None
        )
        kp_ids = list(grading_submission.get("knowledge_points") or ())
        db.add(LearningAttemptRecord(
            attempt_id=attempt_id,
            learner_id=current_user.id,
            attempt_type="practice_grading",
            request_id=submission["request_id"],
            status="completed",
            submitted_at=_now(),
            source_kind="user_question",
        ))
        db.add(QuestionAttempt(
            user_id=current_user.id,
            question_id=controlled_submission["question_id"],
            answer=grading_submission["student_answer"],
            is_correct=is_correct,
            score=score,
            kp_ids_json=json.dumps(kp_ids, ensure_ascii=False),
            feedback=str(grading.get("analysis") or ""),
            created_at=_now(),
        ))
        mistake_ids: list[str] = []
        if not is_correct:
            mistake = db.query(MistakeRecord).filter_by(
                user_id=current_user.id,
                question_id=controlled_submission["question_id"],
                status="active",
            ).one_or_none()
            if mistake is None:
                mistake = MistakeRecord(
                    user_id=current_user.id,
                    question_id=controlled_submission["question_id"],
                    status="active",
                )
                db.add(mistake)
            mistake.kp_ids_json = json.dumps(kp_ids, ensure_ascii=False)
            mistake.error_type = str(grading.get("error_type") or "练习错因")
            mistake.summary = str(grading.get("analysis") or "")
            db.flush()
            mistake_ids.append(str(mistake.id))
        db.add(LearningActivityRecord(
            user_id=current_user.id,
            activity_type="question_attempt",
            resource_id=controlled_submission["question_id"],
            resource_type="user_question",
            duration_minutes=0,
            completion_status="completed",
            score=score,
            payload_json=json.dumps({
                "request_id": submission["request_id"],
                "is_correct": is_correct,
                "question_type": grading_submission["question_type"],
            }, ensure_ascii=False),
            created_at=_now(),
        ))
        rebuild_system_data(db, user_id=current_user.id)
        db.commit()
        return {
            "grading": grading,
            "audit": audit_payload,
            "mistake_record": runner_payload.get("mistake_record"),
            "attempt_id": attempt_id,
            "attempt_item_id": None,
            "grading_artifact_id": None,
            "audit_id": None,
            "writeback": {
                "status": "recorded",
                "receipt_id": None,
                "mistake_ids": mistake_ids,
                "review_task_ids": [],
            },
            "agent_trace": runner_payload.get("agent_trace", []),
        }
    command = from_legacy_route_request(
        current_user.id,
        grading_submission,
        profile=_profile_payload(profile),
        memories=memories,
        request_id=submission.get("request_id") or f"legacy-route:{submission['question_id']}",
    )
    result = apply_practice_grading(
        db,
        command,
        runner=practice_grading_runner,
        explanation_runner=generate_question_explanation,
        atomic=controlled_submission is not None and bool(submission.get("request_id")),
    )
    grading = dict(result.grading_payload or {})
    grading["question_id"] = submission["question_id"]
    grading["question_type"] = grading_submission["question_type"]
    grading["analysis"] = grading.get("feedback", grading.get("error_reason", ""))
    grading["error_type"] = (
        grading.get("error_types") or ["none" if grading.get("is_correct") else "练习错因"]
    )[0]
    grading.setdefault("standard_answer", grading_submission["standard_answer"])
    audit_passed = isinstance(result.audit, dict) and result.audit.get("decision") == "pass"
    if controlled_submission is not None and submission.get("request_id") and audit_passed:
        record_practice_outcome(
            db,
            user_id=current_user.id,
            task_id=None,
            request_id=submission["request_id"],
            submission=controlled_submission,
            grading=grading,
        )
        db.add(LearningActivityRecord(
            user_id=current_user.id,
            activity_type="question_attempt",
            resource_id=controlled_submission["question_id"],
            resource_type="question",
            duration_minutes=0,
            completion_status="completed",
            score=float(grading["score"]) if isinstance(grading.get("score"), (int, float)) else None,
            payload_json=json.dumps({"request_id": submission["request_id"]}, ensure_ascii=False),
            created_at=_now(),
        ))
        rebuild_system_data(db, user_id=current_user.id)
        db.commit()
    if controlled_submission is not None:
        grading.pop("standard_answer", None)
    writeback = result.writeback
    response = {
        "grading": grading,
        "audit": result.audit,
        "mistake_record": result.presentation.get("mistake_record"),
        "attempt_id": result.attempt_id,
        "attempt_item_id": result.attempt_item_id,
        "grading_artifact_id": result.grading_artifact_id,
        "audit_id": result.audit_id,
        "writeback": {
            "status": writeback.status if writeback else "skipped",
            "receipt_id": writeback.receipt_id if writeback else None,
            "mistake_ids": list(writeback.mistake_ids) if writeback else [],
            "review_task_ids": list(writeback.review_task_ids) if writeback else [],
        },
    }
    for key in ("remediation", "agent_trace"):
        if key in result.presentation:
            response[key] = result.presentation[key]
    return response


@router.get("/onboarding/group-templates")
def get_onboarding_group_templates(current_user: UserModel = Depends(get_current_user)):
    return get_group_templates()


@router.post("/onboarding/survey")
def submit_training_onboarding_survey(
    req: OnboardingSurveyRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payload = (
        req.model_dump(exclude_unset=True)
        if hasattr(req, "model_dump")
        else req.dict(exclude_unset=True)
    )
    target_payload = {
        key: payload.pop(key)
        for key in (
            "target_type",
            "exam_track_id",
            "exam_date",
            "is_locked",
            "lock_reason",
        )
        if key in payload
    }
    target_requested = bool(
        target_payload.get("target_type") or target_payload.get("exam_track_id")
    )
    if target_requested and not all(
        target_payload.get(key) for key in ("target_type", "exam_track_id")
    ):
        raise HTTPException(
            status_code=422,
            detail="target_type and exam_track_id must be provided together",
        )
    try:
        target = None
        if target_requested:
            target_repository = (
                exam_learning_service.get_official_exam_repository()
                if requires_official_exam_repository(
                    target_payload["target_type"],
                    target_payload["exam_track_id"],
                )
                else None
            )
            target = set_active_learning_target(
                db,
                user_id=current_user.id,
                target_type=target_payload["target_type"],
                exam_track_id=target_payload["exam_track_id"],
                exam_date=target_payload.get("exam_date"),
                is_locked=target_payload.get("is_locked", True),
                lock_reason=target_payload.get("lock_reason", "用户手动选择"),
                source="manual",
                repository=target_repository,
                commit=False,
            )
            goals = dict(payload.get("goals") or {})
            goals.setdefault("target_exam_or_course", target.exam_name_snapshot)
            payload["goals"] = goals
            payload.setdefault("target_exam_or_course", target.exam_name_snapshot)

        try:
            defaulted_payload = apply_onboarding_defaults(payload)
        except OnboardingTemplateError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        learner_group = defaulted_payload["learner_group"]
        locked_fields = defaulted_payload.get("profile_locked_fields") if "locked_fields" in payload else None
        result = submit_onboarding_survey(
            db,
            user_id=current_user.id,
            survey_answers=defaulted_payload,
            learner_group=learner_group,
            locked_fields=locked_fields,
            commit=False,
        )
        if target is not None:
            result["learning_target"] = serialize_learning_target(target)
        db.commit()
        return result
    except LearningTargetValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LearningTargetLockedError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.post("/onboarding/dismiss")
def dismiss_training_onboarding(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = get_or_create_profile(db, current_user.id)
    existing = {}
    try:
        existing = json.loads(profile.survey_json or "{}")
    except (TypeError, ValueError):
        existing = {}
    if not isinstance(existing, dict):
        existing = {}
    existing["onboarding_dismissed"] = True
    profile.survey_json = json.dumps(existing, ensure_ascii=False)
    db.commit()
    return {"status": "dismissed", "needs_survey_popup": False}


@router.get("/onboarding/status")
def training_onboarding_status(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_onboarding_status(db, current_user.id)


@router.get("/diagnosis/summary")
def get_training_diagnosis_summary(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    report = build_diagnosis_snapshot(db, current_user.id, persist=False)
    learning_profile = build_learning_profile(db, current_user.id)
    return {
        "diagnosis": report.model_dump(),
        "learning_profile": learning_profile,
    }


@router.get("/plan/summary")
def get_learning_plan_summary(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    onboarding = get_onboarding_status(db, current_user.id)
    diagnosis = build_diagnosis_snapshot(db, current_user.id, persist=False)
    learning_profile = build_learning_profile(db, current_user.id)
    return create_or_update_learning_plan_record(
        db,
        user_id=current_user.id,
        learner_group=onboarding["learner_group"],
        onboarding_answers=onboarding.get("survey_answers", {}),
        diagnosis_report=diagnosis,
        learning_profile=learning_profile,
    )


@router.get("/report")
def get_learning_report(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return build_report_summary(db, current_user.id)

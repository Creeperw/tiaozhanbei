from __future__ import annotations

import json
import math
import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from APP.backend.auth import get_current_user
from APP.backend.database import (
    GradingResultRecord,
    LearningAttemptItemRecord,
    LearningAttemptRecord,
    LearningQuestion,
    MistakeRecord,
    QuestionAttempt,
    QuestionBankItem,
    QuestionVersionRecord,
    SystemData,
    UserModel,
    UserQuestionItem,
    get_db,
)
from APP.backend.case_training_models import CaseDefinitionRecord, CaseSessionRecord, CaseVersionRecord
from APP.backend.mistake_variation_service import (
    MistakeVariationNotFound,
    available_variation_source_ids,
    list_available_variation_sources,
)
from APP.backend.mistake_context_service import (
    latest_mistake_context,
    mistake_context_required,
    record_mistake_context,
)
from APP.backend.paper_submission_service import PaperSubmissionInvalid, PaperSubmissionNotFound, get_owned_paper, pause_paper_timer, resume_paper_timer, save_paper_answers, submit_paper
from APP.backend.system_data_service import system_data_payload
from APP.backend.training_workspace_service import (
    TRAINING_TASK_MAX_JSON_BYTES,
    InvalidTrainingTaskRequest,
    create_training_task,
    get_training_task_result,
    get_training_workspace_modules,
)

router = APIRouter(prefix="/training/workspace", tags=["Training Workspace"])
stable_practice_router = APIRouter(prefix="/v1/workshop/practice", tags=["Workshop Practice"])


@dataclass(frozen=True)
class LimiterDecision:
    allowed: bool
    retry_after: int = 0


class TrainingTaskLimiter:
    def __init__(
        self,
        *,
        max_requests: int = 10,
        window_seconds: int = 60,
        max_tracked_users: int = 10000,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._max_tracked_users = max_tracked_users
        self._clock = clock
        self._lock = Lock()
        self._active_users: set[int] = set()
        self._requests: dict[int, deque[float]] = {}

    def _prune(self, now: float) -> None:
        cutoff = now - self._window_seconds
        for user_id, timestamps in list(self._requests.items()):
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if not timestamps and user_id not in self._active_users:
                del self._requests[user_id]

    def acquire(self, user_id: int) -> LimiterDecision:
        now = self._clock()
        with self._lock:
            self._prune(now)
            timestamps = self._requests.get(user_id)
            if user_id in self._active_users:
                return LimiterDecision(False, 1)
            if timestamps is None:
                if len(self._requests) >= self._max_tracked_users:
                    return LimiterDecision(False, max(1, self._window_seconds))
                timestamps = deque()
                self._requests[user_id] = timestamps
            if len(timestamps) >= self._max_requests:
                retry_after = max(1, math.ceil(timestamps[0] + self._window_seconds - now))
                return LimiterDecision(False, retry_after)
            timestamps.append(now)
            self._active_users.add(user_id)
            return LimiterDecision(True)

    def release(self, user_id: int) -> None:
        with self._lock:
            self._active_users.discard(user_id)
            timestamps = self._requests.get(user_id)
            if timestamps is not None and not timestamps:
                del self._requests[user_id]

    @property
    def tracked_user_count(self) -> int:
        with self._lock:
            return len(self._requests)

    def reset(self) -> None:
        with self._lock:
            self._active_users.clear()
            self._requests.clear()


training_task_limiter = TrainingTaskLimiter()


class TrainingTaskRequest(BaseModel):
    task_type: str = Field(max_length=80)
    title: str = Field(default="", max_length=200)
    query: str = Field(default="", max_length=8000)
    inputs: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class PaperAnswersRequest(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)


class PaperSubmitRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=120)


class MistakeAnswerContextRequest(BaseModel):
    answer_state: str = Field(min_length=1, max_length=40)
    reason: str = Field(min_length=1, max_length=80)
    notes: str = Field(default="", max_length=1000)


TRAINING_TASK_REQUEST_OPENAPI_SCHEMA = (
    TrainingTaskRequest.model_json_schema()
    if hasattr(TrainingTaskRequest, "model_json_schema")
    else TrainingTaskRequest.schema()
)


async def _read_training_task_request(request: Request) -> TrainingTaskRequest:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > TRAINING_TASK_MAX_JSON_BYTES:
                raise HTTPException(status_code=413, detail="Request body too large")
        except ValueError:
            pass

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > TRAINING_TASK_MAX_JSON_BYTES:
            raise HTTPException(status_code=413, detail="Request body too large")
        body.extend(chunk)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    try:
        if hasattr(TrainingTaskRequest, "model_validate"):
            return TrainingTaskRequest.model_validate(payload)
        return TrainingTaskRequest.parse_obj(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


@router.get("/modules")
def get_workspace_modules(current_user: UserModel = Depends(get_current_user)):
    return get_training_workspace_modules()


@router.get("/mistake-variations/sources")
def get_mistake_variation_sources(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return {"items": list_available_variation_sources(db, current_user.id)}


def _json_list(value: str | None) -> list[Any]:
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


def _mistake_question(db: Session, mistake: MistakeRecord, user_id: int) -> dict[str, Any]:
    if mistake.question_version_id:
        row = db.query(QuestionVersionRecord).filter_by(
            question_version_id=mistake.question_version_id,
        ).one_or_none()
        if row is not None:
            return {
                "stem": row.stem,
                "question_type": row.question_type,
                "difficulty": row.standard_difficulty,
            }
    private = db.query(UserQuestionItem).filter_by(
        question_id=mistake.question_id,
        owner_user_id=user_id,
    ).one_or_none()
    if private is not None:
        return {
            "stem": private.stem,
            "question_type": private.question_type,
            "difficulty": 2,
        }
    public = db.query(QuestionBankItem).filter_by(question_id=mistake.question_id).one_or_none()
    if public is not None:
        return {
            "stem": public.stem,
            "question_type": public.question_type,
            "difficulty": int(public.difficulty or 2),
        }
    delivered = db.query(LearningQuestion).filter_by(question_id=mistake.question_id).one_or_none()
    if delivered is not None:
        return {
            "stem": delivered.question_content,
            "question_type": delivered.question_type,
            "difficulty": int(delivered.difficulty or 2),
        }
    if mistake.attempt_item_id and str(mistake.question_id or "").startswith("case:"):
        case_row = db.query(CaseDefinitionRecord, CaseSessionRecord).join(
            CaseVersionRecord,
            CaseVersionRecord.case_definition_id == CaseDefinitionRecord.case_definition_id,
        ).join(
            CaseSessionRecord,
            CaseSessionRecord.case_version_id == CaseVersionRecord.case_version_id,
        ).join(
            LearningAttemptRecord,
            LearningAttemptRecord.source_task_id == CaseSessionRecord.session_id,
        ).join(
            LearningAttemptItemRecord,
            LearningAttemptItemRecord.attempt_id == LearningAttemptRecord.attempt_id,
        ).filter(
            LearningAttemptItemRecord.attempt_item_id == mistake.attempt_item_id,
            CaseSessionRecord.owner_user_id == user_id,
        ).one_or_none()
        if case_row is not None:
            definition, session = case_row
            return {
                "stem": definition.title,
                "question_type": f"case_{session.mode}",
                "difficulty": 2,
            }
    return {"stem": "题目内容暂不可用", "question_type": "", "difficulty": None}


def _mistake_attempt(db: Session, mistake: MistakeRecord, user_id: int) -> dict[str, Any]:
    if mistake.attempt_item_id:
        item = db.query(LearningAttemptItemRecord).filter_by(
            attempt_item_id=mistake.attempt_item_id,
        ).one_or_none()
        grading = db.query(GradingResultRecord).filter_by(
            attempt_item_id=mistake.attempt_item_id,
        ).order_by(GradingResultRecord.id.desc()).first()
        if item is not None:
            return {
                "student_answer": item.submitted_answer,
                "score": float(grading.score) if grading and grading.score is not None else None,
                "max_score": float(grading.max_score) if grading and grading.max_score is not None else None,
                "feedback": grading.error_reason if grading else "",
            }
    attempt = db.query(QuestionAttempt).filter(
        QuestionAttempt.user_id == user_id,
        QuestionAttempt.question_id == mistake.question_id,
        QuestionAttempt.is_correct.is_(False),
    ).order_by(QuestionAttempt.created_at.desc(), QuestionAttempt.id.desc()).first()
    return {
        "student_answer": attempt.answer if attempt else "",
        "score": float(attempt.score) if attempt and attempt.score is not None else None,
        "max_score": 100.0 if attempt and attempt.score is not None else None,
        "feedback": attempt.feedback if attempt else "",
    }


def _mistake_payload(
    db: Session,
    mistake: MistakeRecord,
    user_id: int,
    variation_ids: set[int],
) -> dict[str, Any]:
    question = _mistake_question(db, mistake, user_id)
    attempt = _mistake_attempt(db, mistake, user_id)
    context_required = mistake_context_required(question["question_type"])
    answer_context = latest_mistake_context(db, user_id, mistake.id)
    variation_available = (
        mistake.id in variation_ids
        and (not context_required or answer_context is not None)
    )
    return {
        "mistake_id": mistake.id,
        "status": mistake.status,
        "question_id": mistake.question_id,
        "question_version_id": mistake.question_version_id,
        "attempt_item_id": mistake.attempt_item_id,
        "stem": question["stem"],
        "question_type": question["question_type"],
        "difficulty": question["difficulty"],
        "kp_ids": _json_list(mistake.kp_ids_json),
        "error_type": mistake.error_type,
        "summary": mistake.summary,
        "answer_context_required": context_required,
        "answer_context_completed": not context_required or answer_context is not None,
        "answer_context": answer_context,
        **attempt,
        "variation_available": variation_available,
        "variation_reason": (
            ""
            if variation_available
            else "错题已归档" if mistake.status != "active"
            else "请先补充当时的作答把握和判断过程" if context_required and answer_context is None
            else "该错题尚缺已审核题目版本或知识点证据"
        ),
        "created_at": mistake.created_at.isoformat() if mistake.created_at else None,
        "updated_at": mistake.updated_at.isoformat() if mistake.updated_at else None,
    }


@router.get("/mistakes")
@stable_practice_router.get("/mistakes")
def list_mistakes(
    status: str = Query(default="all", max_length=50),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(MistakeRecord).filter(MistakeRecord.user_id == current_user.id)
    if status != "all":
        query = query.filter(MistakeRecord.status == status)
    total = query.count()
    mistakes = query.order_by(
        MistakeRecord.created_at.desc(), MistakeRecord.id.desc()
    ).offset(offset).limit(limit).all()
    variation_ids = available_variation_source_ids(
        db,
        current_user.id,
        [mistake.id for mistake in mistakes],
    )
    return {
        "schema_version": "1.0",
        "items": [
            _mistake_payload(db, mistake, current_user.id, variation_ids)
            for mistake in mistakes
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(mistakes) < total,
    }


@router.get("/mistakes/{mistake_id}")
@stable_practice_router.get("/mistakes/{mistake_id}")
def get_mistake(
    mistake_id: int,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mistake = db.query(MistakeRecord).filter_by(
        id=mistake_id,
        user_id=current_user.id,
    ).one_or_none()
    if mistake is None:
        raise HTTPException(status_code=404, detail="Mistake was not found")
    variation_ids = available_variation_source_ids(db, current_user.id, [mistake.id])
    return {
        "schema_version": "1.0",
        "mistake": _mistake_payload(db, mistake, current_user.id, variation_ids),
    }


@router.post("/mistakes/{mistake_id}/answer-context")
@stable_practice_router.post("/mistakes/{mistake_id}/answer-context")
def submit_mistake_answer_context(
    mistake_id: int,
    req: MistakeAnswerContextRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mistake = db.query(MistakeRecord).filter_by(
        id=mistake_id,
        user_id=current_user.id,
        status="active",
    ).one_or_none()
    if mistake is None:
        raise HTTPException(status_code=404, detail="Mistake was not found")
    question = _mistake_question(db, mistake, current_user.id)
    if not mistake_context_required(question["question_type"]):
        raise HTTPException(status_code=409, detail="主观题由专家批改结果直接归因，无需补充作答情况")
    answer_states = {"确定后作答", "犹豫后作答", "排除后猜测", "完全猜测", "误读题意"}
    reasons = {"概念混淆", "审题遗漏", "记忆不清", "选项辨析困难", "操作失误", "其他"}
    if req.answer_state not in answer_states or req.reason not in reasons:
        raise HTTPException(status_code=422, detail="作答情况或错因选项无效")
    context = record_mistake_context(
        db,
        user_id=current_user.id,
        mistake=mistake,
        answer_state=req.answer_state,
        reason=req.reason,
        notes=req.notes,
    )
    variation_ids = available_variation_source_ids(db, current_user.id, [mistake.id])
    return {
        "schema_version": "1.0",
        "mistake": _mistake_payload(db, mistake, current_user.id, variation_ids),
        "answer_context": context,
    }


@router.get("/papers/{paper_id}")
def get_paper(
    paper_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return get_owned_paper(db, current_user.id, paper_id)
    except PaperSubmissionNotFound as exc:
        raise HTTPException(status_code=404, detail="Paper was not found") from exc


@router.put("/papers/{paper_id}/answers")
def save_paper_answer_draft(
    paper_id: str,
    request: PaperAnswersRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return save_paper_answers(db, current_user.id, paper_id, request.answers)
    except PaperSubmissionNotFound as exc:
        raise HTTPException(status_code=404, detail="Paper was not found") from exc
    except PaperSubmissionInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/papers/{paper_id}/timer/pause")
def pause_paper(
    paper_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return pause_paper_timer(db, current_user.id, paper_id)
    except PaperSubmissionNotFound as exc:
        raise HTTPException(status_code=404, detail="Paper was not found") from exc
    except PaperSubmissionInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/papers/{paper_id}/timer/resume")
def resume_paper(
    paper_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return resume_paper_timer(db, current_user.id, paper_id)
    except PaperSubmissionNotFound as exc:
        raise HTTPException(status_code=404, detail="Paper was not found") from exc
    except PaperSubmissionInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/papers/{paper_id}/submit")
def submit_paper_answers(
    paper_id: str,
    request: PaperSubmitRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return submit_paper(db, current_user.id, paper_id, request.request_id)
    except PaperSubmissionNotFound as exc:
        raise HTTPException(status_code=404, detail="Paper was not found") from exc
    except PaperSubmissionInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/tasks",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": TRAINING_TASK_REQUEST_OPENAPI_SCHEMA
                }
            },
        }
    },
    responses={
        400: {"description": "Invalid JSON or training task service validation failure"},
        413: {"description": "Training task request body exceeds 64 KiB"},
        422: {"description": "Training task request schema validation failure"},
        429: {
            "description": "Training task request limit exceeded",
            "headers": {
                "Retry-After": {
                    "description": "Seconds until another request may be attempted",
                    "schema": {"type": "integer"},
                }
            },
        },
    },
)
async def create_workspace_task(
    request: Request,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    decision = training_task_limiter.acquire(current_user.id)
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many training task requests",
            headers={"Retry-After": str(decision.retry_after)},
        )
    try:
        req = await _read_training_task_request(request)
        payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
        if payload.get("task_type") == "mistake_variation":
            inputs = payload.get("inputs") or {}
            if inputs.get("action", "generate") == "generate":
                mistake_id = inputs.get("mistake_id")
                mistake = db.query(MistakeRecord).filter_by(
                    id=mistake_id,
                    user_id=current_user.id,
                    status="active",
                ).one_or_none()
                if mistake is not None:
                    question = _mistake_question(db, mistake, current_user.id)
                    if mistake_context_required(question["question_type"]) and latest_mistake_context(
                        db, current_user.id, mistake.id
                    ) is None:
                        raise HTTPException(status_code=409, detail="请先补充本题当时的作答情况，再生成错题变式")
        try:
            result = create_training_task(db, current_user.id, payload)
            snapshot = db.query(SystemData).filter_by(user_id=current_user.id).one_or_none()
            return {**result, "system_data": system_data_payload(snapshot)}
        except MistakeVariationNotFound as exc:
            raise HTTPException(status_code=404, detail="Training source was not found") from exc
        except InvalidTrainingTaskRequest as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        training_task_limiter.release(current_user.id)


@router.get(
    "/tasks/{task_id}",
    responses={404: {"description": "Training task was not found"}},
)
def get_workspace_task(
    task_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = get_training_task_result(db, current_user.id, task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Training task was not found")
    return result

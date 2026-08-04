from __future__ import annotations

import json
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from APP.backend.auth import get_current_user
from APP.backend.case_training_models import CaseDefinitionRecord, CaseSessionRecord, CaseVersionRecord
from APP.backend.case_training_service import CaseTrainingService, CaseTrainingStateError
from APP.backend.database import SessionLocal, UserModel, get_db
from APP.backend.tool_runtime import build_default_tool_runtime
from APP.backend.training_service import grade_practice_submission


MAX_CASE_SESSIONS_PER_LEARNER = 3
MAX_CASE_MESSAGE_BYTES = 8192


@dataclass(frozen=True)
class LimiterDecision:
    allowed: bool
    retry_after: int = 0


class CaseTrainingLimiter:
    def __init__(
        self,
        *,
        max_requests: int = 20,
        window_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._clock = clock
        self._lock = Lock()
        self._requests: dict[int, deque[float]] = defaultdict(deque)

    def acquire(self, user_id: int) -> LimiterDecision:
        now = self._clock()
        cutoff = now - self._window_seconds
        with self._lock:
            requests = self._requests[user_id]
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= self._max_requests:
                return LimiterDecision(False, max(1, math.ceil(requests[0] + self._window_seconds - now)))
            requests.append(now)
            return LimiterDecision(True)

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()


class StartCaseSessionRequest(BaseModel):
    selection: str = Field(default="random", pattern="^(random|by_type|by_version)$")
    case_version_id: str | None = Field(default=None, max_length=120)
    case_type: str | None = Field(default=None, max_length=80)
    mode: str = Field(default="full", pattern="^(full|diagnosis_only)$")


class CaseMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_CASE_MESSAGE_BYTES)


class CaseHelpRequest(BaseModel):
    help_type: str = Field(pattern="^(hint|answer)$")


class CaseSubmitRequest(BaseModel):
    answer: dict = Field(default_factory=dict)


router = APIRouter(prefix="/training", tags=["Case Training"])
case_training_limiter = CaseTrainingLimiter()
case_session_start_lock = Lock()


def _patient_runner(**kwargs):
    invocation = build_default_tool_runtime().execute(
        "generate_simulated_patient_reply",
        "patient_simulation_expert",
        **kwargs,
    )
    return invocation.result if invocation.status == "success" else {}


def _patient_auditor(**kwargs):
    invocation = build_default_tool_runtime().execute(
        "audit_simulated_patient_reply",
        "patient_audit_agent",
        **kwargs,
    )
    return invocation.result if invocation.status == "success" else {}


def case_training_service_factory(session_factory: sessionmaker) -> CaseTrainingService:
    return CaseTrainingService(
        session_factory,
        patient_runner=_patient_runner,
        patient_auditor=_patient_auditor,
        grading_runner=grade_practice_submission,
    )


def build_case_training_service() -> CaseTrainingService:
    return case_training_service_factory(SessionLocal)


def _active_session_count(db: Session, learner_id: int) -> int:
    return db.query(CaseSessionRecord).filter(
        CaseSessionRecord.owner_user_id == learner_id,
        CaseSessionRecord.status.in_(("active", "help_available", "submitted", "grading", "needs_human_review")),
    ).count()


def _limit(current_user: UserModel) -> None:
    decision = case_training_limiter.acquire(current_user.id)
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many case training requests",
            headers={"Retry-After": str(decision.retry_after)},
        )


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Case session was not found")


def _service_error(error: CaseTrainingStateError) -> HTTPException:
    if str(error) == "case session unavailable":
        return _not_found()
    if str(error) == "case unavailable":
        return HTTPException(status_code=409, detail="No training cases are currently available")
    if str(error) in {"invalid case selection", "invalid case training mode"}:
        return HTTPException(status_code=400, detail="Invalid case training request")
    return HTTPException(status_code=409, detail="Case session is not available for this operation")


@router.get("/cases/types")
def get_case_types(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    case_types = set()
    records = db.query(CaseDefinitionRecord.visible_context_json).join(
        CaseVersionRecord,
        CaseVersionRecord.case_definition_id == CaseDefinitionRecord.case_definition_id,
    ).all()
    for visible_context_json, in records:
        context = json.loads(visible_context_json or "{}")
        case_type = context.get("case_type") if isinstance(context, dict) else None
        if isinstance(case_type, str) and case_type.strip():
            case_types.add(case_type.strip())
    return {"types": sorted(case_types), "modes": ["full", "diagnosis_only"]}


@router.post("/case-sessions")
def start_case_session(
    request: StartCaseSessionRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _limit(current_user)
    with case_session_start_lock:
        if _active_session_count(db, current_user.id) >= MAX_CASE_SESSIONS_PER_LEARNER:
            raise HTTPException(status_code=409, detail="Active case session limit reached")
        try:
            return build_case_training_service().start_session(
                current_user.id,
                selection=request.selection,
                case_version_id=request.case_version_id,
                case_type=request.case_type,
                mode=request.mode,
            )
        except CaseTrainingStateError as error:
            raise _service_error(error) from error


@router.get("/case-sessions/{session_id}")
def get_case_session(session_id: str, current_user: UserModel = Depends(get_current_user)):
    result = build_case_training_service().get_session(current_user.id, session_id)
    if result is None:
        raise _not_found()
    return result


@router.post("/case-sessions/{session_id}/messages")
def add_case_message(
    session_id: str,
    request: CaseMessageRequest,
    current_user: UserModel = Depends(get_current_user),
):
    _limit(current_user)
    if len(request.message.encode("utf-8")) > MAX_CASE_MESSAGE_BYTES:
        raise HTTPException(status_code=422, detail="Case message is too large")
    try:
        return build_case_training_service().ask(current_user.id, session_id, request.message)
    except CaseTrainingStateError as error:
        raise _service_error(error) from error


@router.post("/case-sessions/{session_id}/help")
def request_case_help(
    session_id: str,
    request: CaseHelpRequest,
    current_user: UserModel = Depends(get_current_user),
):
    _limit(current_user)
    try:
        return build_case_training_service().request_help(current_user.id, session_id, help_type=request.help_type)
    except CaseTrainingStateError as error:
        raise _service_error(error) from error


@router.post("/case-sessions/{session_id}/submit")
def submit_case_session(
    session_id: str,
    request: CaseSubmitRequest,
    current_user: UserModel = Depends(get_current_user),
):
    _limit(current_user)
    try:
        return build_case_training_service().submit(current_user.id, session_id, request.answer)
    except CaseTrainingStateError as error:
        raise _service_error(error) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail="Case training is temporarily unavailable") from error

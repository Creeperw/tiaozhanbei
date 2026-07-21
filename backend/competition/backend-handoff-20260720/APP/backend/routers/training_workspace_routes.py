from __future__ import annotations

import json
import math
import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from APP.backend.auth import get_current_user
from APP.backend.database import SystemData, UserModel, get_db
from APP.backend.mistake_variation_service import MistakeVariationNotFound, list_available_variation_sources
from APP.backend.paper_submission_service import PaperSubmissionInvalid, PaperSubmissionNotFound, get_owned_paper, save_paper_answers, submit_paper
from APP.backend.system_data_service import system_data_payload
from APP.backend.training_workspace_service import (
    TRAINING_TASK_MAX_JSON_BYTES,
    InvalidTrainingTaskRequest,
    create_training_task,
    get_training_task_result,
    get_training_workspace_modules,
)

router = APIRouter(prefix="/training/workspace", tags=["Training Workspace"])


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

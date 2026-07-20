from __future__ import annotations
from APP.backend.time_utils import utc_now

import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from APP.backend.database import LearningFocusSession, LearningTask

_IDLE_SECONDS = 300


def begin_learning_task(
    db: Session,
    *,
    user_id: int,
    task_type: str,
    resource_type: str,
    resource_id: str,
    now: datetime | None = None,
) -> LearningTask:
    timestamp = now or utc_now()
    task = LearningTask(
        task_id=f"LT_{uuid4().hex}",
        user_id=user_id,
        task_type=task_type,
        resource_ids_json=json.dumps([resource_id] if resource_id else [], ensure_ascii=False),
        task_content=resource_type,
        status="in_progress",
        created_at=timestamp,
    )
    db.add(task)
    db.flush()
    return task


def complete_learning_task(
    db: Session,
    *,
    user_id: int,
    task_id: str,
    now: datetime | None = None,
) -> LearningTask:
    task = db.query(LearningTask).filter_by(task_id=task_id, user_id=user_id).one()
    if task.status != "completed":
        task.status = "completed"
        task.completed_at = now or utc_now()
        task.version += 1
        db.flush()
    return task


def start_focus_session(
    db: Session,
    *,
    user_id: int,
    task_id: str | None,
    resource_type: str,
    resource_id: str,
    now: datetime | None = None,
) -> LearningFocusSession:
    timestamp = now or utc_now()
    focus = LearningFocusSession(
        focus_session_id=f"FOCUS_{uuid4().hex}",
        user_id=user_id,
        task_id=task_id,
        resource_type=resource_type,
        resource_id=resource_id,
        status="active",
        is_visible=True,
        started_at=timestamp,
        updated_at=timestamp,
    )
    db.add(focus)
    db.flush()
    return focus


def record_focus_heartbeat(
    db: Session,
    *,
    user_id: int,
    focus_session_id: str,
    visible: bool,
    interacted: bool,
    now: datetime | None = None,
) -> LearningFocusSession:
    timestamp = now or utc_now()
    focus = db.query(LearningFocusSession).filter_by(
        focus_session_id=focus_session_id,
        user_id=user_id,
        status="active",
    ).one()
    elapsed = max(0, int((timestamp - focus.updated_at).total_seconds()))
    interaction_age = (
        max(0, int((timestamp - focus.last_interaction_at).total_seconds()))
        if focus.last_interaction_at is not None
        else None
    )
    if (
        focus.is_visible
        and interaction_age is not None
        and interaction_age <= _IDLE_SECONDS
        and elapsed <= _IDLE_SECONDS
    ):
        focus.active_seconds += elapsed
    focus.is_visible = visible
    if not visible:
        focus.last_interaction_at = None
    elif interacted:
        focus.last_interaction_at = timestamp
    focus.updated_at = timestamp
    db.flush()
    return focus


def end_focus_session(
    db: Session,
    *,
    user_id: int,
    focus_session_id: str,
    now: datetime | None = None,
) -> LearningFocusSession:
    timestamp = now or utc_now()
    focus = db.query(LearningFocusSession).filter_by(
        focus_session_id=focus_session_id,
        user_id=user_id,
        status="active",
    ).one()
    focus.status = "completed"
    focus.ended_at = timestamp
    focus.updated_at = timestamp
    db.flush()
    return focus

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import Session

from APP.backend.auth import get_current_user
from APP.backend.database import LearningActivityRecord, LearningTask, UserModel, get_db
from APP.backend.learning_task_activity_service import (
    begin_learning_task,
    complete_learning_task,
    end_focus_session,
    record_focus_heartbeat,
    start_focus_session,
)
from APP.backend.system_data_service import rebuild_system_data, system_data_payload

router = APIRouter(prefix="/learning-activity", tags=["Learning Activity"])


class TaskStartRequest(BaseModel):
    task_type: str = Field(min_length=1, max_length=80)
    resource_type: str = Field(min_length=1, max_length=80)
    resource_id: str = Field(default="", max_length=120)


class FocusStartRequest(BaseModel):
    task_id: str | None = Field(default=None, max_length=120)
    resource_type: str = Field(min_length=1, max_length=80)
    resource_id: str = Field(default="", max_length=120)


class FocusHeartbeatRequest(BaseModel):
    visible: bool
    interacted: bool


class TextbookProgressRequest(BaseModel):
    book: str = Field(min_length=1, max_length=200)
    route: str = Field(default="textbook_14_5", max_length=120)
    chapter_id: str = Field(min_length=1, max_length=120)
    chapter_name: str = Field(default="", max_length=300)
    section_id: str = Field(min_length=1, max_length=120)
    section_name: str = Field(default="", max_length=300)


def _textbook_history_payload(record: LearningActivityRecord) -> dict:
    try:
        payload = json.loads(record.payload_json or "{}")
    except (TypeError, ValueError):
        payload = {}
    return {
        "id": record.id,
        "section_id": record.resource_id,
        "chapter_id": payload.get("chapter_id", ""),
        "section_name": payload.get("section_name", ""),
        "chapter_name": payload.get("chapter_name", ""),
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


@router.get("/textbook-progress")
def get_textbook_progress(
    book: str = Query(min_length=1, max_length=200),
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    records = db.query(LearningActivityRecord).filter(
        LearningActivityRecord.user_id == current_user.id,
        LearningActivityRecord.activity_type == "textbook_section_completed",
        LearningActivityRecord.resource_type == "textbook_section",
    ).order_by(LearningActivityRecord.created_at.asc(), LearningActivityRecord.id.asc()).all()
    book_records = []
    for record in records:
        try:
            payload = json.loads(record.payload_json or "{}")
        except (TypeError, ValueError):
            continue
        if payload.get("book") == book:
            book_records.append(record)
    completed_section_ids = list(dict.fromkeys(record.resource_id for record in book_records if record.resource_id))
    return {
        "book": book,
        "completed_section_ids": completed_section_ids,
        "last_section_id": book_records[-1].resource_id if book_records else "",
        "history": [_textbook_history_payload(record) for record in book_records],
    }


@router.post("/textbook-progress", status_code=status.HTTP_201_CREATED)
def record_textbook_progress(
    request: TextbookProgressRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payload = request.model_dump()
    record = LearningActivityRecord(
        user_id=current_user.id,
        activity_type="textbook_section_completed",
        resource_id=request.section_id,
        resource_type="textbook_section",
        completion_status="completed",
        payload_json=json.dumps(payload, ensure_ascii=False),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"ok": True, "record": _textbook_history_payload(record)}


def _task_payload(task: LearningTask) -> dict:
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "status": task.status,
        "version": task.version,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
def create_task(
    request: TaskStartRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = begin_learning_task(
        db,
        user_id=current_user.id,
        task_type=request.task_type,
        resource_type=request.resource_type,
        resource_id=request.resource_id,
    )
    snapshot = rebuild_system_data(db, user_id=current_user.id)
    db.commit()
    return {**_task_payload(task), "system_data": system_data_payload(snapshot)}


@router.post("/tasks/{task_id}/complete")
def complete_task(
    task_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        task = complete_learning_task(db, user_id=current_user.id, task_id=task_id)
    except NoResultFound:
        db.rollback()
        raise HTTPException(status_code=404, detail="Learning task was not found") from None
    snapshot = rebuild_system_data(db, user_id=current_user.id)
    db.commit()
    return {**_task_payload(task), "system_data": system_data_payload(snapshot)}


@router.post("/focus-sessions", status_code=status.HTTP_201_CREATED)
def create_focus_session(
    request: FocusStartRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if request.task_id and db.query(LearningTask).filter_by(
        task_id=request.task_id,
        user_id=current_user.id,
    ).one_or_none() is None:
        raise HTTPException(status_code=404, detail="Learning task was not found")
    focus = start_focus_session(
        db,
        user_id=current_user.id,
        task_id=request.task_id,
        resource_type=request.resource_type,
        resource_id=request.resource_id,
    )
    db.commit()
    return {"focus_session_id": focus.focus_session_id, "status": focus.status}


@router.post("/focus-sessions/{focus_session_id}/heartbeat")
def heartbeat_focus_session(
    focus_session_id: str,
    request: FocusHeartbeatRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        focus = record_focus_heartbeat(
            db,
            user_id=current_user.id,
            focus_session_id=focus_session_id,
            visible=request.visible,
            interacted=request.interacted,
        )
    except NoResultFound:
        db.rollback()
        raise HTTPException(status_code=404, detail="Focus session was not found") from None
    db.commit()
    return {"focus_session_id": focus.focus_session_id, "active_seconds": focus.active_seconds}


@router.post("/focus-sessions/{focus_session_id}/end")
def close_focus_session(
    focus_session_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        focus = end_focus_session(db, user_id=current_user.id, focus_session_id=focus_session_id)
    except NoResultFound:
        db.rollback()
        raise HTTPException(status_code=404, detail="Focus session was not found") from None
    snapshot = rebuild_system_data(db, user_id=current_user.id)
    db.commit()
    return {
        "focus_session_id": focus.focus_session_id,
        "status": focus.status,
        "active_seconds": focus.active_seconds,
        "system_data": system_data_payload(snapshot),
    }

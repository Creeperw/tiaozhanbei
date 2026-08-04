from __future__ import annotations

import json
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from APP.backend.auth import get_current_user
from APP.backend.database import (
    CorePracticeSubmissionClaim,
    DailyTaskItemRecord,
    DailyTaskQuestionSnapshotRecord,
    UserModel,
    get_db,
)
from APP.backend.daily_task_progress_service import (
    TERMINAL_AUDIT_DECISIONS,
    DailyTaskProgressError,
    confirm_iframe_video,
    record_video_evidence,
)
from APP.backend.time_utils import utc_now


router = APIRouter(prefix="/daily-task-items", tags=["Daily Tasks"])


def _service_response(callable_, db: Session, user_id: int, payload: dict):
    try:
        result = callable_(db, user_id, payload)
    except DailyTaskProgressError as exc:
        raise HTTPException(status_code=exc.code, detail=str(exc)) from exc
    code = int(result.pop("code", 200))
    if code >= 400:
        raise HTTPException(status_code=code, detail="video evidence requirements were not met")
    return result


@router.post("/{task_item_id}/video-evidence")
def post_daily_task_video_evidence(
    task_item_id: str,
    payload: dict,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    trusted_payload = {**payload, "task_item_id": task_item_id}
    return _service_response(record_video_evidence, db, current_user.id, trusted_payload)


@router.post("/{task_item_id}/video-evidence/confirm")
def confirm_daily_task_iframe_video(
    task_item_id: str,
    payload: dict,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    trusted_payload = {**payload, "task_item_id": task_item_id}
    return _service_response(confirm_iframe_video, db, current_user.id, trusted_payload)


def _json_list(value: str | None) -> list:
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


@router.get("/{task_item_id}/practice/next")
def next_daily_task_practice_question(
    task_item_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = db.query(DailyTaskItemRecord).filter_by(
        task_item_id=task_item_id,
        user_id=current_user.id,
        item_kind="knowledge_practice",
    ).one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="daily task item was not found")

    snapshots = db.query(DailyTaskQuestionSnapshotRecord).filter_by(
        task_item_id=task_item_id,
        user_id=current_user.id,
    ).order_by(
        DailyTaskQuestionSnapshotRecord.id.asc()
    ).all()
    reviewed = sum(
        snapshot.audit_decision in TERMINAL_AUDIT_DECISIONS
        for snapshot in snapshots
    )
    snapshot = next(
        (
            candidate
            for candidate in snapshots
            if candidate.audit_decision not in TERMINAL_AUDIT_DECISIONS
        ),
        None,
    )
    progress = {
        "reviewed": reviewed,
        "required": item.required_question_count,
    }
    if snapshot is None:
        return {
            "available": False,
            "reason": "daily_task_item_completed",
            "progress": progress,
        }

    claim_cutoff = utc_now() - timedelta(minutes=30)
    existing_claim = (
        db.query(CorePracticeSubmissionClaim)
        .filter(
            CorePracticeSubmissionClaim.user_id == current_user.id,
            CorePracticeSubmissionClaim.daily_task_snapshot_id == snapshot.id,
            CorePracticeSubmissionClaim.created_at >= claim_cutoff,
        )
        .one_or_none()
    )
    if existing_claim is not None:
        request_id = existing_claim.request_id
    else:
        db.query(CorePracticeSubmissionClaim).filter(
            CorePracticeSubmissionClaim.user_id == current_user.id,
            CorePracticeSubmissionClaim.daily_task_snapshot_id == snapshot.id,
            CorePracticeSubmissionClaim.created_at < claim_cutoff,
        ).delete(synchronize_session=False)
        request_id = str(uuid.uuid4())
        db.add(CorePracticeSubmissionClaim(
            user_id=current_user.id,
            request_id=request_id,
            question_id=snapshot.question_id,
            daily_task_item_id=item.task_item_id,
            question_version_id=snapshot.question_version_id,
            daily_task_snapshot_id=snapshot.id,
        ))
        try:
            db.commit()
        except IntegrityError:
            # A simultaneous /next call may have issued this same frozen
            # snapshot. The unique constraint makes that race deterministic.
            db.rollback()
            existing_claim = db.query(CorePracticeSubmissionClaim).filter_by(
                user_id=current_user.id,
                daily_task_snapshot_id=snapshot.id,
            ).one_or_none()
            if existing_claim is None:
                raise HTTPException(status_code=409, detail="daily task practice claim could not be issued")
            request_id = existing_claim.request_id

    return {
        "available": True,
        "progress": progress,
        "question": {
            "question_id": snapshot.question_id,
            "question_version_id": snapshot.question_version_id,
            "question_type": snapshot.question_type,
            "stem": snapshot.stem_snapshot,
            "options": _json_list(snapshot.options_snapshot_json),
            "kp_ids": _json_list(snapshot.kp_snapshot_json),
            "request_id": request_id,
            "source_scope": "daily_task",
        },
    }

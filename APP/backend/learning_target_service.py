from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from APP.backend.database import UserLearningTarget
from APP.backend.time_utils import utc_now


VALID_TARGET_TYPES = {"graduate_entrance_exam", "certification"}


class LearningTargetValidationError(ValueError):
    pass


class LearningTargetLockedError(RuntimeError):
    pass


def get_active_learning_target(db: Session, user_id: int) -> UserLearningTarget | None:
    return (
        db.query(UserLearningTarget)
        .filter(UserLearningTarget.user_id == user_id, UserLearningTarget.is_active.is_(True))
        .order_by(UserLearningTarget.updated_at.desc(), UserLearningTarget.id.desc())
        .first()
    )


def set_active_learning_target(
    db: Session,
    *,
    user_id: int,
    target_type: str,
    exam_track_id: str,
    repository,
    exam_date: date | datetime | None = None,
    is_locked: bool = True,
    lock_reason: str = "用户手动选择",
    source: str = "manual",
    commit: bool = True,
) -> UserLearningTarget:
    if target_type not in VALID_TARGET_TYPES:
        raise LearningTargetValidationError("invalid target_type")
    try:
        track = repository.get_track(exam_track_id)
    except KeyError as exc:
        raise LearningTargetValidationError("unknown exam_track_id") from exc

    active = get_active_learning_target(db, user_id)
    if active is not None and active.is_locked and source != "manual":
        raise LearningTargetLockedError("active learning target is locked")
    if active is not None and active.exam_track_id == exam_track_id:
        active.target_type = target_type
        active.exam_date = _as_datetime(exam_date)
        active.is_locked = is_locked
        active.lock_reason = lock_reason
        active.source = source
        active.active_slot = 1
        active.updated_at = utc_now()
        if commit:
            db.commit()
            db.refresh(active)
        else:
            db.flush()
        return active

    now = utc_now()
    if active is not None:
        active.is_active = False
        active.active_slot = None
        active.archived_at = now
        active.updated_at = now

    target = UserLearningTarget(
        user_id=user_id,
        target_type=target_type,
        exam_track_id=exam_track_id,
        exam_name_snapshot=str(track.get("title_normalized") or track.get("title") or exam_track_id),
        syllabus_version=str(track.get("schema_version") or track.get("year") or ""),
        exam_date=_as_datetime(exam_date),
        is_active=True,
        active_slot=1,
        is_locked=is_locked,
        lock_reason=lock_reason,
        source=source,
    )
    db.add(target)
    try:
        if commit:
            db.commit()
            db.refresh(target)
        else:
            db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise LearningTargetLockedError(
            "learning target changed concurrently; retry the request"
        ) from exc
    return target


def serialize_learning_target(target: UserLearningTarget | None) -> dict | None:
    if target is None:
        return None
    return {
        "id": target.id,
        "target_type": target.target_type,
        "exam_track_id": target.exam_track_id,
        "exam_name": target.exam_name_snapshot,
        "syllabus_version": target.syllabus_version,
        "exam_date": target.exam_date.date().isoformat() if target.exam_date else None,
        "is_active": target.is_active,
        "is_locked": target.is_locked,
        "lock_reason": target.lock_reason,
        "source": target.source,
        "created_at": target.created_at.isoformat() if target.created_at else None,
        "updated_at": target.updated_at.isoformat() if target.updated_at else None,
    }


def _as_datetime(value: date | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min)

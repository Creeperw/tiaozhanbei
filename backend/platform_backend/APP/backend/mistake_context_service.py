from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from APP.backend.database import LearningActivityRecord, MistakeRecord
from APP.backend.time_utils import utc_now


SUBJECTIVE_TYPES = {"short_answer", "case_quiz", "简答题", "案例题"}


def mistake_context_required(question_type: str | None) -> bool:
    normalized = str(question_type or "").strip()
    return not (
        normalized in SUBJECTIVE_TYPES
        or normalized.startswith("case_")
        or any(marker in normalized for marker in ("简答", "案例", "主观"))
    )


def latest_mistake_context(db: Session, user_id: int, mistake_id: int) -> dict[str, Any] | None:
    row = db.query(LearningActivityRecord).filter_by(
        user_id=user_id,
        activity_type="mistake_answer_context",
        resource_id=str(mistake_id),
        resource_type="mistake",
    ).order_by(LearningActivityRecord.created_at.desc(), LearningActivityRecord.id.desc()).first()
    if row is None:
        return None
    try:
        value = json.loads(row.payload_json or "{}")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def record_mistake_context(
    db: Session,
    *,
    user_id: int,
    mistake: MistakeRecord,
    answer_state: str,
    reason: str,
    notes: str = "",
) -> dict[str, Any]:
    payload = {
        "mistake_id": mistake.id,
        "answer_state": answer_state,
        "reason": reason,
        "notes": notes.strip(),
        "recorded_at": utc_now().isoformat(),
    }
    db.add(LearningActivityRecord(
        user_id=user_id,
        activity_type="mistake_answer_context",
        resource_id=str(mistake.id),
        resource_type="mistake",
        duration_minutes=0,
        completion_status="completed",
        score=None,
        payload_json=json.dumps(payload, ensure_ascii=False),
        created_at=utc_now(),
    ))
    mistake.error_type = reason
    mistake.updated_at = utc_now()
    db.commit()
    return payload

from __future__ import annotations
from APP.backend.time_utils import as_beijing, utc_now

import json
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from APP.backend.database import LearningActivityRecord

CHECKIN_ACTIVITY_TYPE = "daily_checkin"
CHECKIN_RESOURCE_TYPE = "checkin"


def _today() -> date:
    return as_beijing(utc_now()).date()


def _date_key(value: date) -> str:
    return value.isoformat()


def _checkin_resource_id(value: date) -> str:
    return f"checkin:{_date_key(value)}"


def _checkin_records(db: Session, user_id: int) -> list[LearningActivityRecord]:
    return db.query(LearningActivityRecord).filter(
        LearningActivityRecord.user_id == user_id,
        LearningActivityRecord.activity_type == CHECKIN_ACTIVITY_TYPE,
    ).order_by(LearningActivityRecord.created_at.desc()).all()


def build_checkin_status(db: Session, user_id: int, days: int = 7) -> dict[str, Any]:
    today = _today()
    records = _checkin_records(db, user_id)
    checked_dates = {
        (record.resource_id or "").replace("checkin:", "")
        for record in records
        if (record.resource_id or "").startswith("checkin:")
    }
    checked_in_today = _date_key(today) in checked_dates

    streak = 0
    cursor = today
    while _date_key(cursor) in checked_dates:
        streak += 1
        cursor -= timedelta(days=1)

    calendar_days = []
    for offset in range(days - 1, -1, -1):
        current = today - timedelta(days=offset)
        key = _date_key(current)
        calendar_days.append({
            "date": key,
            "checked_in": key in checked_dates,
            "is_today": current == today,
        })

    return {
        "today": _date_key(today),
        "checked_in_today": checked_in_today,
        "streak": streak,
        "total_checkins": len(checked_dates),
        "calendar_days": calendar_days,
    }


def record_daily_checkin(db: Session, user_id: int) -> dict[str, Any]:
    today = _today()
    resource_id = _checkin_resource_id(today)
    existing = db.query(LearningActivityRecord).filter(
        LearningActivityRecord.user_id == user_id,
        LearningActivityRecord.activity_type == CHECKIN_ACTIVITY_TYPE,
        LearningActivityRecord.resource_id == resource_id,
    ).first()

    already_checked_in = existing is not None
    if not already_checked_in:
        payload = {"date": _date_key(today), "checked_at": utc_now().isoformat()}
        db.add(LearningActivityRecord(
            user_id=user_id,
            activity_type=CHECKIN_ACTIVITY_TYPE,
            resource_id=resource_id,
            resource_type=CHECKIN_RESOURCE_TYPE,
            duration_minutes=0,
            completion_status="completed",
            score=100.0,
            payload_json=json.dumps(payload, ensure_ascii=False),
            created_at=utc_now(),
        ))
        db.commit()

    return {
        "checked_in": True,
        "resource_id": resource_id,
        "resource_type": CHECKIN_RESOURCE_TYPE,
        "date": _date_key(today),
        "already_checked_in": already_checked_in,
        "message": "今日已签到" if already_checked_in else "今日签到成功",
        "status": build_checkin_status(db, user_id),
    }

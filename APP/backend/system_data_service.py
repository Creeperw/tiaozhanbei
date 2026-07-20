from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta

from APP.backend.time_utils import BEIJING_TZ, as_beijing, utc_now
from typing import Any

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from APP.backend.database import LearningActivityRecord, LearningFocusSession, LearningTask, SystemData

_ACTIVITY_WINDOW_DAYS = 30
_CALCULATION_VERSION = "system-data-v2"
_RECOMMENDATION_VIEW_ACTIVITY = "dashboard_recommendations_view"
_RESOURCE_CLICK_ACTIVITY = "resource_click"
_LEGACY_TASK_ACTIVITY_TYPES = {
    "question_attempt",
    "paper_submission",
    "case_training",
    "training_workspace_task",
}


def system_data_payload(snapshot: SystemData | None) -> dict[str, Any]:
    if snapshot is None:
        return {}
    return {
        "time_data": _json_object(snapshot.time_data_json),
        "task_completion_rate": _json_object(snapshot.task_completion_rate_json),
        "resource_click_rate": _json_object(snapshot.resource_click_rate_json),
        "calculation_version": snapshot.calculation_version,
        "calculated_at": _beijing_iso(snapshot.calculated_at),
    }


def record_login_activity(db: Session, *, user_id: int, now: datetime | None = None) -> SystemData:
    timestamp = now or utc_now()
    db.add(LearningActivityRecord(
        user_id=user_id,
        activity_type="login",
        resource_type="session",
        completion_status="completed",
        created_at=timestamp,
    ))
    return rebuild_system_data(db, user_id=user_id, now=timestamp)


def record_dashboard_recommendations_view(
    db: Session,
    *,
    user_id: int,
    recommendation_keys: tuple[str, ...],
    now: datetime | None = None,
) -> LearningActivityRecord:
    if not recommendation_keys or any(not key.strip() for key in recommendation_keys):
        raise ValueError("recommendation_keys are required")
    timestamp = now or utc_now()
    view = LearningActivityRecord(
        user_id=user_id,
        activity_type=_RECOMMENDATION_VIEW_ACTIVITY,
        resource_id=f"recommendation-view:{uuid.uuid4()}",
        resource_type="dashboard_recommendations",
        completion_status="viewed",
        payload_json=json.dumps({"recommendation_keys": list(dict.fromkeys(recommendation_keys))}, ensure_ascii=False),
        created_at=timestamp,
    )
    db.add(view)
    db.flush()
    return view


def record_dashboard_recommendation_click(
    db: Session,
    *,
    user_id: int,
    recommendation_key: str,
    recommendation_view_id: str,
    now: datetime | None = None,
) -> SystemData:
    if not recommendation_key.strip() or not recommendation_view_id.strip():
        raise ValueError("recommendation click requires displayed recommendation")
    view = db.query(LearningActivityRecord).filter_by(
        user_id=user_id,
        activity_type=_RECOMMENDATION_VIEW_ACTIVITY,
        resource_id=recommendation_view_id,
        completion_status="viewed",
    ).one_or_none()
    if view is None or recommendation_key not in _json_list(view.payload_json, "recommendation_keys"):
        raise ValueError("recommendation was not displayed to current user")

    timestamp = now or utc_now()
    db.add(LearningActivityRecord(
        user_id=user_id,
        activity_type=_RESOURCE_CLICK_ACTIVITY,
        resource_id=recommendation_key,
        resource_type="dashboard_recommendation",
        completion_status="clicked",
        payload_json=json.dumps({"recommendation_view_id": recommendation_view_id}, ensure_ascii=False),
        created_at=timestamp,
    ))
    return rebuild_system_data(db, user_id=user_id, now=timestamp)


def rebuild_system_data(
    db: Session,
    *,
    user_id: int,
    now: datetime | None = None,
) -> SystemData:
    db.flush()
    calculated_at = now or utc_now()
    window_start = calculated_at - timedelta(days=_ACTIVITY_WINDOW_DAYS)
    snapshot = db.query(SystemData).filter_by(user_id=user_id).with_for_update().one_or_none()
    if snapshot is None:
        try:
            with db.begin_nested():
                snapshot = SystemData(user_id=user_id)
                db.add(snapshot)
                db.flush()
        except IntegrityError:
            snapshot = db.query(SystemData).filter_by(user_id=user_id).with_for_update().one()

    activities = db.query(LearningActivityRecord).filter(
        LearningActivityRecord.user_id == user_id,
        LearningActivityRecord.created_at >= window_start,
        LearningActivityRecord.created_at <= calculated_at,
    ).all()
    _migrate_legacy_task_activities(db, user_id=user_id, activities=activities)
    db.flush()
    tasks = db.query(LearningTask).filter(
        LearningTask.user_id == user_id,
        LearningTask.created_at >= window_start,
        LearningTask.created_at <= calculated_at,
    ).all()
    focus_sessions = db.query(LearningFocusSession).filter(
        LearningFocusSession.user_id == user_id,
        LearningFocusSession.started_at >= window_start,
        LearningFocusSession.started_at <= calculated_at,
    ).all()

    snapshot.time_data_json = json.dumps(
        _time_data(activities, focus_sessions, window_start, calculated_at),
        ensure_ascii=False,
    )
    snapshot.task_completion_rate_json = json.dumps(
        _task_completion_rate(tasks, window_start, calculated_at),
        ensure_ascii=False,
    )
    snapshot.resource_click_rate_json = json.dumps(_resource_click_rate(activities, window_start, calculated_at), ensure_ascii=False)
    snapshot.data_source = "learning_tasks,learning_focus_sessions,learning_activity_records"
    snapshot.calculation_version = _CALCULATION_VERSION
    snapshot.calculated_at = calculated_at
    db.flush()
    return snapshot


def build_learning_trends(
    db: Session,
    *,
    user_id: int,
    days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    if days not in {7, 30, 90}:
        raise ValueError("days must be one of: 7, 30, 90")

    calculated_at = now or utc_now()
    today = as_beijing(calculated_at).date()
    start_date = today - timedelta(days=days - 1)
    window_start = (datetime.combine(start_date, datetime.min.time())
                    .replace(tzinfo=BEIJING_TZ)
                    .astimezone(UTC)
                    .replace(tzinfo=None))
    activities = db.query(LearningActivityRecord).filter(
        LearningActivityRecord.user_id == user_id,
        LearningActivityRecord.created_at >= window_start,
        LearningActivityRecord.created_at <= calculated_at,
    ).all()
    tasks = db.query(LearningTask).filter(
        LearningTask.user_id == user_id,
        LearningTask.created_at >= window_start,
        LearningTask.created_at <= calculated_at,
    ).all()
    focus_sessions = db.query(LearningFocusSession).filter(
        LearningFocusSession.user_id == user_id,
        LearningFocusSession.started_at <= calculated_at,
        or_(
            LearningFocusSession.ended_at.is_(None),
            LearningFocusSession.ended_at >= window_start,
        ),
    ).all()

    login_dates = {
        as_beijing(activity.created_at).date()
        for activity in activities
        if activity.activity_type == "login"
    }
    focus_seconds_by_date = _focus_seconds_by_beijing_date(
        focus_sessions,
        window_start=window_start,
        window_end=calculated_at,
    )
    tasks_by_date: dict[date, list[LearningTask]] = {}
    for task in tasks:
        task_date = as_beijing(task.created_at).date()
        tasks_by_date.setdefault(task_date, []).append(task)

    series = []
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        daily_tasks = [task for task in tasks_by_date.get(day, []) if task.status != "cancelled"]
        completed = sum(task.status == "completed" for task in daily_tasks)
        series.append({
            "date": day.isoformat(),
            "login_days": int(day in login_dates),
            "focus_minutes": round(focus_seconds_by_date.get(day, 0) / 60),
            "task_completion_rate": completed / len(daily_tasks) if daily_tasks else 0.0,
        })
    return {
        "days": days,
        "series": series,
        "calculated_at": _beijing_iso(calculated_at),
    }


def _focus_seconds_by_beijing_date(
    focus_sessions: list[LearningFocusSession],
    *,
    window_start: datetime,
    window_end: datetime,
) -> dict[date, int]:
    seconds_by_date: dict[date, int] = {}
    for focus in focus_sessions:
        if focus.status not in {"active", "completed"} or focus.active_seconds <= 0:
            continue
        observed_end = min(focus.ended_at or focus.updated_at or window_end, window_end)
        session_start = max(focus.started_at, window_start)
        if session_start >= observed_end:
            continue
        observed_seconds = max(0, int((observed_end - focus.started_at).total_seconds()))
        if observed_seconds == 0:
            continue
        elapsed_processed = max(0, int((session_start - focus.started_at).total_seconds()))
        cursor = session_start
        while cursor < observed_end:
            beijing_cursor = as_beijing(cursor)
            next_midnight = datetime.combine(
                beijing_cursor.date() + timedelta(days=1),
                datetime.min.time(),
                tzinfo=BEIJING_TZ,
            ).astimezone(UTC).replace(tzinfo=None)
            segment_end = min(observed_end, next_midnight)
            segment_seconds = int((segment_end - cursor).total_seconds())
            allocated_seconds = (
                focus.active_seconds
                if segment_end == observed_end
                else round(focus.active_seconds * (elapsed_processed + segment_seconds) / observed_seconds)
            ) - round(focus.active_seconds * elapsed_processed / observed_seconds)
            focus_date = beijing_cursor.date()
            seconds_by_date[focus_date] = seconds_by_date.get(focus_date, 0) + allocated_seconds
            elapsed_processed += segment_seconds
            cursor = segment_end
    return seconds_by_date


def _migrate_legacy_task_activities(
    db: Session,
    *,
    user_id: int,
    activities: list[LearningActivityRecord],
) -> None:
    existing_task_ids = {
        task_id for task_id, in db.query(LearningTask.task_id).filter(
            LearningTask.user_id == user_id,
        ).all()
    }
    for activity in activities:
        if activity.activity_type not in _LEGACY_TASK_ACTIVITY_TYPES:
            continue
        source_task_id = _json_value(activity.payload_json, "task_id") or activity.resource_id
        if source_task_id and source_task_id in existing_task_ids:
            continue
        task_id = f"LEGACY_ACTIVITY_{activity.id}"
        if task_id in existing_task_ids:
            continue
        task_type = _json_value(activity.payload_json, "task_type") or activity.activity_type
        status = (
            activity.completion_status
            if activity.activity_type == "training_workspace_task"
            else "completed"
        )
        resource_ids = [activity.resource_id] if activity.resource_id else []
        db.add(LearningTask(
            task_id=task_id,
            user_id=user_id,
            task_type=task_type,
            resource_ids_json=json.dumps(resource_ids, ensure_ascii=False),
            task_content=activity.resource_type or activity.activity_type,
            status=status,
            created_at=activity.created_at,
            completed_at=activity.created_at if status == "completed" else None,
        ))
        existing_task_ids.add(task_id)


def _time_data(
    activities: list[LearningActivityRecord],
    focus_sessions: list[LearningFocusSession],
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    login_dates = {
        as_beijing(activity.created_at).date().isoformat()
        for activity in activities
        if activity.activity_type == "login"
    }
    focus_slot = ""
    completed_focus_sessions = [
        focus for focus in focus_sessions
        if focus.status == "completed" and focus.active_seconds > 0
    ]
    if completed_focus_sessions:
        seconds_by_hour: dict[int, int] = {}
        for focus in completed_focus_sessions:
            hour = as_beijing(focus.started_at).hour
            seconds_by_hour[hour] = seconds_by_hour.get(hour, 0) + focus.active_seconds
        focus_slot = _hour_slot(max(seconds_by_hour, key=lambda hour: (seconds_by_hour[hour], -hour)))
    return {
        "login_frequency": _metric(len(login_dates), "days", window_start, window_end),
        "focus_time_period": _metric(focus_slot, "hour_slot", window_start, window_end),
    }


def _task_completion_rate(
    tasks: list[LearningTask],
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    candidates = [task for task in tasks if task.status != "cancelled"]
    completed = sum(task.status == "completed" for task in candidates)
    value = completed / len(candidates) if candidates else 0.0
    return _metric(value, "ratio", window_start, window_end)


def _resource_click_rate(
    activities: list[LearningActivityRecord],
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    views = [activity for activity in activities if activity.activity_type == _RECOMMENDATION_VIEW_ACTIVITY]
    displayed_resources = {
        (view.resource_id, recommendation_key)
        for view in views
        for recommendation_key in _json_list(view.payload_json, "recommendation_keys")
    }
    clicked_resources = {
        (_json_value(activity.payload_json, "recommendation_view_id"), activity.resource_id)
        for activity in activities
        if activity.activity_type == _RESOURCE_CLICK_ACTIVITY
    }
    clicked_count = len(displayed_resources & clicked_resources)
    rate = clicked_count / len(displayed_resources) if displayed_resources else 0.0
    return _metric(rate, "ratio", window_start, window_end)


def _metric(value: Any, unit: str, window_start: datetime, window_end: datetime) -> dict[str, Any]:
    return {
        "value": value,
        "unit": unit,
        "window_start": _beijing_iso(window_start),
        "window_end": _beijing_iso(window_end),
    }


def _beijing_iso(value: datetime | None) -> str | None:
    return as_beijing(value).isoformat() if value else None


def _hour_slot(hour: int) -> str:
    return f"{hour:02d}:00-{hour:02d}:59"


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_value(value: str, key: str) -> str:
    try:
        payload = json.loads(value or "{}")
    except (TypeError, ValueError):
        return ""
    return str(payload.get(key) or "") if isinstance(payload, dict) else ""


def _json_list(value: str, key: str) -> list[str]:
    try:
        payload = json.loads(value or "{}")
    except (TypeError, ValueError):
        return []
    candidate = payload.get(key) if isinstance(payload, dict) else []
    if isinstance(candidate, str):
        return [candidate]
    return [str(item) for item in candidate] if isinstance(candidate, list) else []

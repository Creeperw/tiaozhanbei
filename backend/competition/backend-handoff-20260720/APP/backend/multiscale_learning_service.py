from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.orm import Session

from APP.backend.database import (
    KnowledgeMasteryState,
    KnowledgePoint,
    LearnerKPReviewState,
    LearningFocusSession,
    LearningQuestion,
    LearningQuestionAttempt,
    LearningTask,
    LearningUserProfile,
    LongTermPlan,
    MistakeRecord,
    QuestionBankItem,
    ShortTermPlan,
    TeachingResource,
    UserProfile,
)
from competition_app.contracts.multiscale_learning import (
    MetricValue,
    MultiScaleLearningState,
    PathCandidate,
)


SCHEMA_VERSION = "1.0"
ALLOWED_WINDOWS = (7, 30, 90)
HARD_CONSTRAINT_ORDER = (
    "goal_route_alignment",
    "parent_plan_exists",
    "prerequisite_satisfied",
    "time_budget",
    "due_review_priority",
    "trusted_source",
    "low_data_protection",
    "approved_stage_mapping",
)
POSITIVE_WEIGHTS = {
    "learning_gain": 0.30,
    "retention_benefit": 0.20,
    "knowledge_coverage": 0.20,
    "time_fit": 0.10,
    "difficulty_fit": 0.10,
    "autonomy_support": 0.10,
}
REPETITION_WEIGHT = 0.10
UNCERTAINTY_WEIGHT = 0.15
_ACTIVE_PLAN_STATUSES = {"active", "approved", "current", "pending"}
_TRUSTED_SOURCE_PREFIXES = (
    "approved_",
    "audited_",
    "curated_",
    "formal_",
    "official_",
)
_TRUSTED_SOURCE_VALUES = {
    "approved",
    "audited",
    "curated",
    "formal",
    "official",
    "textbook",
    "approved_textbook",
    "curated_question_bank",
}
_UNTRUSTED_SOURCE_PREFIXES = (
    "unapproved",
    "not_",
    "unverified",
    "unknown",
    "draft",
    "retired",
)


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return fallback
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return fallback
    return parsed


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if str(value).strip()))


def _metric(
    value: float | int | None,
    *,
    unit: str,
    source_refs: list[str],
    unavailable_reason: str,
) -> dict[str, Any]:
    return MetricValue(
        available=value is not None,
        value=value,
        unit=unit,
        source_refs=_unique(source_refs),
        unavailable_reason=None if value is not None else unavailable_reason,
    ).model_dump(mode="json")


def _source(
    table: str,
    source_id: str,
    *,
    window_days: int | None = None,
    time_field: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source_id": f"{table}:{source_id}",
        "source_type": "database_row",
        "table": table,
        "record_id": str(source_id),
        "window_days": window_days,
    }
    if time_field:
        result["time_field"] = time_field
    return result


def _current_row(db: Session, model: Any, user_id: int) -> Any | None:
    user_column = getattr(model, "user_id")
    query = db.query(model).filter(user_column == user_id)
    if hasattr(model, "status"):
        query = query.filter(model.status.in_(_ACTIVE_PLAN_STATUSES))
    order_column = getattr(model, "updated_at", getattr(model, "created_at"))
    return query.order_by(order_column.desc(), model.id.desc()).first()


def _row_plan(row: Any | None, layer: str) -> dict[str, Any]:
    if row is None:
        return {}
    payload = _json(getattr(row, "content", ""), {})
    if not isinstance(payload, dict):
        payload = {}
    result = {
        **payload,
        "plan_id": str(row.plan_id),
        "content": getattr(row, "content", ""),
        "status": str(getattr(row, "status", "")),
        "created_at": _iso(getattr(row, "created_at", None)),
        "updated_at": _iso(getattr(row, "updated_at", None)),
    }
    if layer == "short_term_plan":
        result["long_term_plan_id"] = str(row.long_term_plan_id or "")
    return result


def _plan_layer(
    plan_context: dict[str, Any],
    key: str,
    row: Any | None,
) -> dict[str, Any]:
    if key in plan_context:
        value = plan_context.get(key)
        return dict(value) if isinstance(value, dict) else {}
    return _row_plan(row, key)


def _profile_payload(db: Session, user_id: int) -> tuple[dict[str, Any], list[str]]:
    learning = (
        db.query(LearningUserProfile)
        .filter(LearningUserProfile.user_id == user_id)
        .one_or_none()
    )
    legacy = db.query(UserProfile).filter(UserProfile.user_id == user_id).one_or_none()
    payload: dict[str, Any] = {}
    refs: list[str] = []
    if learning is not None:
        payload.update(
            {
                "goals": _json(learning.goals_json, {}),
                "preferences": _json(learning.user_preference_json, {}),
                "daily_available_minutes": learning.daily_available_minutes,
                "completed_courses": _json(learning.completed_courses_json, []),
            }
        )
        refs.append(f"user_profile:{learning.id}")
    if legacy is not None:
        survey = _json(legacy.survey_json, {})
        payload.setdefault("goals", {})
        if legacy.health_goals and not payload["goals"]:
            payload["goals"] = {"target_exam_or_course": legacy.health_goals}
        payload["legacy_preferences"] = {
            "exercise_preferences": legacy.exercise_preferences or "",
            "custom_needs": legacy.custom_needs or "",
            "survey": survey,
        }
        refs.append(f"user_profiles:{legacy.id}")
    return payload, refs


def _route_and_stage(long_plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    route = long_plan.get("planning_route")
    route = dict(route) if isinstance(route, dict) else {}
    selection = long_plan.get("textbook_selection")
    selection = dict(selection) if isinstance(selection, dict) else {}
    stage_id = str(
        selection.get("stage_id")
        or long_plan.get("current_stage_id")
        or ""
    ).strip()
    phases = route.get("phases") if isinstance(route.get("phases"), list) else []
    stage = next(
        (
            dict(item)
            for item in phases
            if isinstance(item, dict) and str(item.get("phase_id") or "") == stage_id
        ),
        {},
    )
    if not stage and phases:
        first = phases[0]
        stage = dict(first) if isinstance(first, dict) else {}
    if selection:
        stage = {
            **stage,
            "phase_id": selection.get("stage_id") or stage.get("phase_id"),
            "name": selection.get("stage_name") or stage.get("name"),
            "books": selection.get("books") or stage.get("books") or [],
        }
    return route, stage


def _named_kps(db: Session, kp_ids: set[str]) -> dict[str, dict[str, str]]:
    if not kp_ids:
        return {}
    rows = db.query(KnowledgePoint).filter(KnowledgePoint.kp_id.in_(kp_ids)).all()
    return {
        str(row.kp_id): {
            "kp_id": str(row.kp_id),
            "name": str(row.name),
            "source": str(row.source or "unknown"),
        }
        for row in rows
        if str(row.name or "").strip()
    }


def _mastery_ratio(row: KnowledgeMasteryState) -> float | None:
    if row.mastery_score is None:
        return None
    return _clamp(float(row.mastery_score) / 100.0)


def _canonical_digest(payload: dict[str, Any]) -> str:
    normalized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _digest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: value
        for key, value in payload.items()
        if key not in {"state_digest", "state_id", "generated_at"}
    }
    data_quality = result.get("data_quality")
    if isinstance(data_quality, dict):
        result["data_quality"] = {
            key: value
            for key, value in data_quality.items()
            if key not in {"window_start", "window_end"}
        }
    return result


def _validated_state(
    state: dict[str, Any],
    *,
    user_id: int,
) -> dict[str, Any]:
    try:
        normalized = MultiScaleLearningState.model_validate(state).model_dump(
            mode="json"
        )
    except Exception as exc:
        raise ValueError(f"invalid multiscale learning state: {exc}") from exc
    if normalized["learner_id"] != str(user_id):
        raise ValueError("supplied state learner does not match requested learner")
    expected = _canonical_digest(_digest_payload(normalized))
    if normalized["state_digest"] != expected:
        raise ValueError("supplied state digest does not match its payload")
    return normalized


def _clipped_focus_seconds(
    row: LearningFocusSession,
    *,
    window_start: datetime,
    window_end: datetime,
) -> float:
    session_start = max(row.started_at, window_start)
    session_end = min(row.ended_at or window_end, window_end)
    total_end = row.ended_at or window_end
    total_wall = max(0.0, (total_end - row.started_at).total_seconds())
    observed_active = max(0.0, float(row.active_seconds or 0))
    if total_wall <= 0:
        return (
            observed_active
            if window_start <= row.started_at <= window_end
            else 0.0
        )
    overlap = max(0.0, (session_end - session_start).total_seconds())
    if overlap <= 0:
        return 0.0
    return min(overlap, observed_active * overlap / total_wall)


# ---------------------------------------------------------------------------
# Read-only multiscale state derivation
# ---------------------------------------------------------------------------


def build_multiscale_state(
    db: Session,
    user_id: int,
    plan_context: dict[str, Any] | None = None,
    window_days: int = 30,
) -> dict[str, Any]:
    """Derive a read-only learning-state snapshot from authoritative source rows."""

    if window_days not in ALLOWED_WINDOWS:
        raise ValueError("window_days must be one of: 7, 30, 90")
    plan_context = dict(plan_context or {})
    now_db = datetime.utcnow()
    generated_at = datetime.now(timezone.utc)
    window_start = now_db - timedelta(days=window_days)

    long_row = _current_row(db, LongTermPlan, user_id)
    short_row = _current_row(db, ShortTermPlan, user_id)
    long_plan = _plan_layer(plan_context, "long_term_plan", long_row)
    short_plan = _plan_layer(plan_context, "short_term_plan", short_row)
    route, stage = _route_and_stage(long_plan)
    profile, profile_refs = _profile_payload(db, user_id)

    completion_tasks = (
        db.query(LearningTask)
        .filter(
            LearningTask.user_id == user_id,
            LearningTask.status != "cancelled",
            or_(
                (
                    (LearningTask.completed_at.is_not(None))
                    & (LearningTask.completed_at >= window_start)
                    & (LearningTask.completed_at <= now_db)
                ),
                (
                    (LearningTask.due_at.is_not(None))
                    & (LearningTask.due_at >= window_start)
                    & (LearningTask.due_at <= now_db)
                ),
            ),
        )
        .order_by(
            LearningTask.completed_at.desc(),
            LearningTask.due_at.desc(),
            LearningTask.id.desc(),
        )
        .all()
    )
    pending_tasks = (
        db.query(LearningTask)
        .filter(
            LearningTask.user_id == user_id,
            LearningTask.status.in_(["pending", "active"]),
        )
        .order_by(LearningTask.created_at.desc(), LearningTask.id.desc())
        .all()
    )
    tasks_by_id = {
        str(row.task_id): row for row in completion_tasks + pending_tasks
    }
    tasks = list(tasks_by_id.values())
    attempts = (
        db.query(LearningQuestionAttempt)
        .filter(
            LearningQuestionAttempt.user_id == user_id,
            LearningQuestionAttempt.answered_at >= window_start,
            LearningQuestionAttempt.answered_at <= now_db,
        )
        .order_by(
            LearningQuestionAttempt.answered_at.desc(),
            LearningQuestionAttempt.id.desc(),
        )
        .all()
    )
    mastery_rows = (
        db.query(KnowledgeMasteryState)
        .filter(KnowledgeMasteryState.learner_id == user_id)
        .order_by(
            KnowledgeMasteryState.updated_at.desc(),
            KnowledgeMasteryState.id.desc(),
        )
        .all()
    )
    review_rows = (
        db.query(LearnerKPReviewState)
        .filter(LearnerKPReviewState.learner_id == user_id)
        .order_by(
            LearnerKPReviewState.next_review_at.asc(),
            LearnerKPReviewState.id.asc(),
        )
        .all()
    )
    mistake_rows = (
        db.query(MistakeRecord)
        .filter(
            MistakeRecord.user_id == user_id,
            MistakeRecord.created_at >= window_start,
            MistakeRecord.created_at <= now_db,
        )
        .order_by(MistakeRecord.created_at.desc(), MistakeRecord.id.desc())
        .all()
    )
    focus_rows = (
        db.query(LearningFocusSession)
        .filter(
            LearningFocusSession.user_id == user_id,
            LearningFocusSession.started_at <= now_db,
            (
                (LearningFocusSession.ended_at.is_(None))
                | (LearningFocusSession.ended_at >= window_start)
            ),
        )
        .order_by(
            LearningFocusSession.started_at.desc(),
            LearningFocusSession.id.desc(),
        )
        .all()
    )
    due_rows = [
        row
        for row in review_rows
        if row.status == "active"
        and row.next_review_at is not None
        and row.next_review_at <= now_db
    ]
    attempted_question_ids = {
        str(row.question_id) for row in attempts if str(row.question_id or "")
    }
    attempted_questions = (
        db.query(LearningQuestion)
        .filter(LearningQuestion.question_id.in_(attempted_question_ids))
        .order_by(LearningQuestion.question_id.asc())
        .all()
        if attempted_question_ids else []
    )
    recent_kp_ids = _unique(
        [
            str(kp_id)
            for row in attempted_questions
            for kp_id in _json(row.kp_ids_json, [])
            if str(kp_id).strip()
        ]
    )

    kp_ids: set[str] = {str(row.kp_id) for row in mastery_rows}
    kp_ids.update(str(row.kp_id) for row in review_rows)
    for row in tasks:
        kp_ids.update(str(item) for item in _json(row.kp_ids_json, []) if str(item))
    for row in mistake_rows:
        kp_ids.update(str(item) for item in _json(row.kp_ids_json, []) if str(item))
    kp_names = _named_kps(db, kp_ids)
    mastery_items = []
    for row in mastery_rows:
        if str(row.kp_id) not in kp_names:
            continue
        mastery_items.append({
            "kp_id": str(row.kp_id),
            "name": kp_names.get(str(row.kp_id), {}).get("name"),
            "mastery": _mastery_ratio(row),
            "confidence": (
                _clamp(float(row.mastery_confidence))
                if row.mastery_confidence is not None else None
            ),
            "attempt_count": int(row.attempt_count or 0),
            "source_ref": f"knowledge_mastery_states:{row.mastery_state_id}",
        })
    weak_points = [
        item
        for item in sorted(
            mastery_items,
            key=lambda item: (
                item["mastery"] is None,
                item["mastery"] if item["mastery"] is not None else 1.0,
            ),
        )
        if item["mastery"] is not None and item["mastery"] < 0.7
    ]
    due_points = [
        {
            "kp_id": str(row.kp_id),
            "name": kp_names[str(row.kp_id)]["name"],
            "next_review_at": _iso(row.next_review_at),
            "retention_estimate": (
                _clamp(float(row.retention_estimate))
                if row.retention_estimate is not None else None
            ),
            "source_ref": f"learner_kp_review_states:{row.review_state_id}",
        }
        for row in due_rows
        if str(row.kp_id) in kp_names
    ]
    planned_ids = _unique(
        [
            str(item)
            for row in tasks
            for item in _json(row.kp_ids_json, [])
            if str(item).strip()
        ]
        + [
            str(item)
            for item in (
                (short_plan.get("short_term_focus") or {}).get(
                    "knowledge_point_ids", []
                )
                if isinstance(short_plan.get("short_term_focus"), dict)
                else []
            )
            if str(item).strip()
        ]
    )
    planned_points = [
        {"kp_id": kp_id, "name": kp_names[kp_id]["name"]}
        for kp_id in planned_ids
        if kp_id in kp_names
    ]

    completed_tasks = sum(
        row.status == "completed"
        for row in completion_tasks
    )
    task_completion = (
        completed_tasks / len(completion_tasks) if completion_tasks else None
    )
    active_days = {
        value.date()
        for value in (
            [row.answered_at for row in attempts]
            + [row.started_at for row in focus_rows]
            + [
                row.completed_at or row.due_at
                for row in completion_tasks
            ]
        )
        if value is not None and window_start <= value <= now_db
    }
    regularity = len(active_days) / window_days if active_days else None
    accuracy = (
        sum(bool(row.is_correct) for row in attempts) / len(attempts)
        if attempts else None
    )
    response_times = [
        int(row.response_time_seconds)
        for row in attempts
        if row.response_time_seconds is not None and row.response_time_seconds >= 0
    ]
    average_response = (
        sum(response_times) / len(response_times) if response_times else None
    )
    complete_mastery = [
        item
        for item in mastery_items
        if item["mastery"] is not None and item["confidence"] is not None
    ]
    average_mastery = (
        sum(float(item["mastery"]) for item in complete_mastery)
        / len(complete_mastery)
        if complete_mastery and len(complete_mastery) == len(mastery_items)
        else None
    )
    focus_minutes = (
        sum(
            _clipped_focus_seconds(
                row,
                window_start=window_start,
                window_end=now_db,
            )
            for row in focus_rows
        )
        / 60
        if focus_rows else None
    )
    known_task_minutes = [
        int(row.estimated_minutes)
        for row in pending_tasks
        if row.estimated_minutes is not None
    ]
    current_load = (
        sum(known_task_minutes)
        if pending_tasks
        and len(known_task_minutes) == len(pending_tasks)
        else None
    )

    task_refs = [f"learning_task:{row.task_id}" for row in completion_tasks]
    attempt_refs = [f"question_attempt:{row.attempt_id}" for row in attempts]
    mastery_refs = [
        f"knowledge_mastery_states:{row.mastery_state_id}" for row in mastery_rows
    ]
    review_refs = [
        f"learner_kp_review_states:{row.review_state_id}" for row in review_rows
    ]
    focus_refs = [
        f"learning_focus_sessions:{row.focus_session_id}" for row in focus_rows
    ]

    goal = profile.get("goals") if isinstance(profile.get("goals"), dict) else {}
    route_sources = route.get("sources") if isinstance(route.get("sources"), list) else []
    macro = {
        "qualification_goal": goal or {},
        "approved_route": route if route.get("planning_status") == "approved_route" else {},
        "current_stage": stage,
        "stage_books": list(stage.get("books") or []),
        "prerequisites": list(stage.get("prerequisites") or []),
        "acceptance_evidence": list(stage.get("exit_evidence") or []),
    }
    meso = {
        "current_short_term_plan": short_plan,
        "current_daily_tasks": [
            {
                "task_id": str(row.task_id),
                "task_type": str(row.task_type),
                "content": str(row.task_content or ""),
                "estimated_minutes": row.estimated_minutes,
                "status": str(row.status),
                "kp_ids": _json(row.kp_ids_json, []),
                "source_ref": f"learning_task:{row.task_id}",
            }
            for row in tasks[:30]
        ],
        "planned_knowledge_points": planned_points,
        "weak_knowledge_points": weak_points,
        "due_review_knowledge_points": due_points,
        "task_completion_rate": _metric(
            round(task_completion, 4) if task_completion is not None else None,
            unit="ratio_0_1",
            source_refs=task_refs,
            unavailable_reason="no_tasks_in_window",
        ),
        "learning_regularity": _metric(
            round(regularity, 4) if regularity is not None else None,
            unit="ratio_0_1",
            source_refs=_unique(task_refs + attempt_refs + focus_refs),
            unavailable_reason="no_learning_activity_in_window",
        ),
    }
    micro = {
        "recent_attempts": [
            {
                "attempt_id": str(row.attempt_id),
                "question_id": str(row.question_id),
                "is_correct": bool(row.is_correct),
                "score": row.score,
                "response_time_seconds": row.response_time_seconds,
                "answered_at": _iso(row.answered_at),
                "source_ref": f"question_attempt:{row.attempt_id}",
            }
            for row in attempts[:30]
        ],
        "question_accuracy": _metric(
            round(accuracy, 4) if accuracy is not None else None,
            unit="ratio_0_1",
            source_refs=attempt_refs,
            unavailable_reason="no_question_attempts",
        ),
        "average_response_time": _metric(
            round(average_response, 2) if average_response is not None else None,
            unit="seconds",
            source_refs=attempt_refs,
            unavailable_reason=(
                "no_question_attempts"
                if not attempts
                else "no_response_time_observations"
            ),
        ),
        "average_mastery": _metric(
            round(average_mastery, 4) if average_mastery is not None else None,
            unit="ratio_0_1",
            source_refs=mastery_refs,
            unavailable_reason=(
                "no_mastery_observations"
                if not mastery_rows
                else "mastery_or_confidence_missing"
            ),
        ),
        "mastery_by_knowledge_point": mastery_items,
        "confirmed_mistake_reasons": [
            {
                "mistake_id": str(row.id),
                "reason": str(row.error_type or row.summary),
                "kp_ids": _json(row.kp_ids_json, []),
                "source_ref": f"mistake_records:{row.id}",
            }
            for row in mistake_rows
            if str(row.error_type or row.summary or "").strip()
        ],
        "recent_focus_minutes": _metric(
            round(focus_minutes, 2) if focus_minutes is not None else None,
            unit="minutes",
            source_refs=focus_refs,
            unavailable_reason="no_focus_sessions",
        ),
        "current_task_load": _metric(
            current_load,
            unit="minutes",
            source_refs=[
                f"learning_task:{row.task_id}"
                for row in pending_tasks
                if row.estimated_minutes is not None
            ],
            unavailable_reason=(
                "no_pending_tasks"
                if not pending_tasks
                else "pending_task_duration_missing_or_incomplete"
            ),
        ),
        "recent_question_ids": _unique([str(row.question_id) for row in attempts]),
        "recent_knowledge_point_ids": recent_kp_ids,
        "recent_resource_ids": _unique(
            [str(row.resource_id) for row in focus_rows if str(row.resource_id or "")]
        ),
    }

    available_metric_count = sum(
        metric["available"]
        for metric in (
            meso["task_completion_rate"],
            meso["learning_regularity"],
            micro["question_accuracy"],
            micro["average_response_time"],
            micro["average_mastery"],
            micro["recent_focus_minutes"],
            micro["current_task_load"],
        )
    )
    total_metric_count = 7
    coverage = available_metric_count / total_metric_count
    data_quality = {
        "window_days": window_days,
        "window_start": _iso(window_start),
        "window_end": _iso(now_db),
        "coverage": round(coverage, 4),
        "sample_counts": {
            "tasks": len(tasks),
            "question_attempts": len(attempts),
            "mastery_points": len(mastery_rows),
            "review_states": len(review_rows),
            "mistakes": len(mistake_rows),
            "focus_sessions": len(focus_rows),
        },
        "available_metrics": available_metric_count,
        "unavailable_metrics": total_metric_count - available_metric_count,
        "allow_cautious_path_adjustment": (
            coverage >= 0.5 and len(attempts) >= 3 and bool(mastery_rows)
        ),
        "limitations": [
            "缺失指标保持不可用，不参与正向评分。",
            "掌握度是当前学习状态估计，不是标准化考试成绩。",
        ],
    }
    global_constraints = [
        {
            "key": "approved_route_available",
            "passed": bool(
                route.get("planning_status") == "approved_route"
                and route.get("route_status") == "approved"
            ),
            "reason": (
                "approved_route_present"
                if route.get("planning_status") == "approved_route"
                and route.get("route_status") == "approved"
                else "approved_route_missing"
            ),
            "source_refs": [
                f"route:{route.get('route_id')}"
            ] if route.get("route_id") else [],
        },
        {
            "key": "low_data_protection",
            "passed": bool(data_quality["allow_cautious_path_adjustment"]),
            "reason": (
                "sufficient_for_cautious_adjustment"
                if data_quality["allow_cautious_path_adjustment"]
                else "insufficient_data_for_high_risk_adjustment"
            ),
            "source_refs": _unique(attempt_refs + mastery_refs),
        },
    ]
    source_refs: list[dict[str, Any]] = []
    if "long_term_plan" in plan_context:
        source_refs.append(
            {
                "source_id": (
                    "plan_context:long_term_plan:"
                    f"{long_plan.get('plan_id') or 'anonymous'}"
                ),
                "source_type": "request_context",
                "table": "plan_context",
                "record_id": str(long_plan.get("plan_id") or "anonymous"),
                "window_days": None,
            }
        )
    elif long_row is not None:
        source_refs.append(_source("long_term_plan", long_row.plan_id))
    if "short_term_plan" in plan_context:
        source_refs.append(
            {
                "source_id": (
                    "plan_context:short_term_plan:"
                    f"{short_plan.get('plan_id') or 'anonymous'}"
                ),
                "source_type": "request_context",
                "table": "plan_context",
                "record_id": str(short_plan.get("plan_id") or "anonymous"),
                "window_days": None,
            }
        )
    elif short_row is not None:
        source_refs.append(_source("short_term_plan", short_row.plan_id))
    for row in tasks:
        task_time_field = None
        if (
            row.completed_at is not None
            and window_start <= row.completed_at <= now_db
        ):
            task_time_field = "completed_at"
        elif (
            row.due_at is not None
            and window_start <= row.due_at <= now_db
        ):
            task_time_field = "due_at"
        source_refs.append(
            _source(
                "learning_task",
                row.task_id,
                window_days=(window_days if task_time_field else None),
                time_field=task_time_field,
            )
        )
    source_refs.extend(
        _source(
            "question_attempt", row.attempt_id,
            window_days=window_days, time_field="answered_at",
        )
        for row in attempts
    )
    source_refs.extend(
        _source("knowledge_mastery_states", row.mastery_state_id)
        for row in mastery_rows
    )
    source_refs.extend(
        _source("learner_kp_review_states", row.review_state_id)
        for row in review_rows
    )
    source_refs.extend(
        _source(
            "mistake_records", str(row.id),
            window_days=window_days, time_field="created_at",
        )
        for row in mistake_rows
    )
    source_refs.extend(
        _source(
            "learning_focus_sessions", row.focus_session_id,
            window_days=window_days, time_field="started_at",
        )
        for row in focus_rows
    )
    for ref in profile_refs:
        table, source_id = ref.split(":", 1)
        source_refs.append(_source(table, source_id))
    for item in route_sources:
        if isinstance(item, dict) and item.get("source_id"):
            source_refs.append(
                {
                    "source_id": f"route_source:{item['source_id']}",
                    "source_type": str(item.get("source_type") or "route_source"),
                    "table": "plan_context",
                    "record_id": str(item["source_id"]),
                    "window_days": None,
                }
            )

    without_digest = {
        "schema_version": SCHEMA_VERSION,
        "state_id": f"MSLS_{uuid4().hex}",
        "learner_id": str(user_id),
        "generated_at": generated_at.isoformat(),
        "macro": macro,
        "meso": meso,
        "micro": micro,
        "data_quality": data_quality,
        "hard_constraints": global_constraints,
        "source_refs": source_refs,
    }
    normalized = MultiScaleLearningState.model_validate(
        {**without_digest, "state_digest": "0" * 24}
    ).model_dump(mode="json")
    normalized_without_digest = {
        key: value for key, value in normalized.items() if key != "state_digest"
    }
    return MultiScaleLearningState.model_validate(
        {
            **normalized_without_digest,
            "state_digest": _canonical_digest(
                _digest_payload(normalized_without_digest)
            ),
        }
    ).model_dump(mode="json")


def _available_minutes(
    db: Session,
    user_id: int,
    plan_context: dict[str, Any],
    explicit: int | None,
) -> tuple[int | None, list[str]]:
    if explicit is not None:
        return max(0, min(1440, int(explicit))), ["request:available_minutes"]
    for key in ("daily_available_minutes", "available_minutes"):
        value = plan_context.get(key)
        if isinstance(value, (int, float)):
            return max(0, min(1440, int(value))), [f"plan_context:{key}"]
    row = (
        db.query(LearningUserProfile)
        .filter(LearningUserProfile.user_id == user_id)
        .one_or_none()
    )
    if row is not None and row.daily_available_minutes is not None:
        return (
            max(0, min(1440, int(row.daily_available_minutes))),
            [f"user_profile:{row.id}"],
        )
    return None, []


# ---------------------------------------------------------------------------
# Candidate repository and descriptor helpers
# ---------------------------------------------------------------------------


def _is_trusted(value: str) -> bool:
    lowered = str(value or "").lower()
    if not lowered or lowered.startswith(_UNTRUSTED_SOURCE_PREFIXES):
        return False
    return (
        lowered in _TRUSTED_SOURCE_VALUES
        or lowered.startswith(_TRUSTED_SOURCE_PREFIXES)
    )


def _score_metric(
    value: float | None,
    *,
    sources: list[str],
    reason: str,
) -> dict[str, Any]:
    return _metric(
        round(_clamp(value), 4) if value is not None else None,
        unit="ratio_0_1",
        source_refs=sources,
        unavailable_reason=reason,
    )


def _score(components: dict[str, dict[str, Any]]) -> float:
    available_positive = [
        (POSITIVE_WEIGHTS[key], float(components[key]["value"]))
        for key in POSITIVE_WEIGHTS
        if components[key]["available"]
    ]
    denominator = sum(weight for weight, _value in available_positive)
    positive = (
        sum(weight * value for weight, value in available_positive) / denominator
        if denominator else 0.0
    )
    repetition = (
        float(components["repetition_penalty"]["value"])
        if components["repetition_penalty"]["available"]
        else 0.0
    )
    uncertainty = (
        float(components["uncertainty_risk"]["value"])
        if components["uncertainty_risk"]["available"]
        else 0.0
    )
    return round(
        _clamp(
            positive
            - REPETITION_WEIGHT * repetition
            - UNCERTAINTY_WEIGHT * uncertainty
        ),
        4,
    )


def _candidate_id(scope: str, kind: str, source_refs: list[str]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            [scope, kind, sorted(source_refs)],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"PATH_{digest}"


def _route_evidence(route: dict[str, Any], stage: dict[str, Any]) -> list[str]:
    evidence = [
        f"evidence:{item.get('source_id')}"
        for item in route.get("sources", [])
        if isinstance(item, dict)
        and item.get("source_id")
        and _is_trusted(str(item.get("source_type") or ""))
        and str(item.get("status") or "approved") == "approved"
    ]
    trusted_source_ids = {
        str(item.get("source_id"))
        for item in route.get("sources", [])
        if isinstance(item, dict)
        and item.get("source_id")
        and _is_trusted(str(item.get("source_type") or ""))
        and str(item.get("status") or "approved") == "approved"
    }
    evidence.extend(
        f"evidence:{item}"
        for item in stage.get("source_refs", [])
        if str(item) in trusted_source_ids
    )
    return _unique(evidence)


# ---------------------------------------------------------------------------
# Hard-constraint gate and transparent fixed-weight scorer
# ---------------------------------------------------------------------------


def _evaluate_hard_constraints(
    *,
    descriptor: dict[str, Any],
    scope: str,
    long_plan: dict[str, Any],
    short_plan: dict[str, Any],
    approved_route: bool,
    planned_stage_ids: set[str],
    allowed_stage_kp_ids: set[str],
    allowed_stage_kp_names: set[str],
    allowed_stage_books: set[str],
    missing_prerequisites: list[str],
    prerequisite_evidence_refs: list[str],
    goal_aligned: bool,
    goal_reason: str,
    route_refs: list[str],
    profile_refs: list[str],
    budget: int | None,
    budget_refs: list[str],
    due_ids: set[str],
    due_rows: list[LearnerKPReviewState],
    low_data: bool,
    state_digest: str,
    long_plan_verified: bool,
    short_plan_verified: bool,
    parent_link_verified: bool,
) -> tuple[list[dict[str, Any]], bool, bool, int]:
    """Evaluate the fixed safety gate without consulting score components."""

    kp_set = set(descriptor["kp_ids"])
    raw_estimated = descriptor["estimated_minutes"]
    estimated = (
        max(0, min(1440, int(raw_estimated)))
        if raw_estimated is not None else 0
    )
    duration_known = raw_estimated is not None
    duration_below_day = duration_known and int(raw_estimated) < 1440
    time_ok = (
        True
        if scope == "long_term"
        else (
            budget is not None
            and duration_below_day
            and int(raw_estimated) <= budget
        )
    )

    if scope == "long_term":
        parent_ok = long_plan_verified
        parent_reason = (
            "verified_long_term_root_candidate"
            if parent_ok else "long_term_plan_required_or_inactive"
        )
    elif scope == "short_term":
        parent_ok = long_plan_verified
        parent_reason = (
            "verified_long_term_plan_present"
            if parent_ok else "long_term_plan_unverified"
        )
    else:
        parent_ok = (
            long_plan_verified
            and short_plan_verified
            and parent_link_verified
        )
        if not short_plan:
            parent_reason = "short_term_plan_required"
        elif not long_plan_verified:
            parent_reason = "long_term_plan_unverified"
        elif not short_plan_verified and not parent_link_verified:
            parent_reason = "short_term_plan_inactive_parent_mismatch"
        elif not short_plan_verified:
            parent_reason = "short_term_plan_unverified"
        elif not parent_link_verified:
            parent_reason = "short_term_parent_mismatch"
        else:
            parent_reason = "parent_plans_present"

    prerequisite_ok = not missing_prerequisites
    covers_due = bool(kp_set.intersection(due_ids))
    due_ok = (
        scope != "daily_task"
        or not due_ids
        or descriptor["recommended_action"] == "review"
        or covers_due
    )
    safe_under_low_data = (
        descriptor["kind"] == "due_review"
        or (
            scope == "daily_task"
            and approved_route
            and isinstance(descriptor["difficulty"], (int, float))
            and float(descriptor["difficulty"]) <= 3
        )
    )
    low_data_ok = not low_data or safe_under_low_data

    candidate_stage_id = str(
        descriptor["stage"].get("phase_id")
        or descriptor["stage"].get("stage_id")
        or ""
    )
    candidate_kp_names = {
        str(item.get("name") or "").strip()
        for item in descriptor["knowledge_points"]
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    kps_mapped = (
        kp_set.issubset(allowed_stage_kp_ids)
        if kp_set
        else candidate_kp_names.issubset(allowed_stage_kp_names)
    )
    candidate_books = {
        str(item.get("name") or "").strip()
        for item in descriptor["books"]
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    books_mapped = (
        not candidate_books
        or candidate_books.issubset(allowed_stage_books)
    )
    stage_mapping_ok = bool(
        approved_route
        and candidate_stage_id
        and candidate_stage_id in planned_stage_ids
        and kps_mapped
        and books_mapped
    )
    values = {
        "goal_route_alignment": (
            goal_aligned,
            goal_reason,
            _unique(route_refs + profile_refs),
        ),
        "parent_plan_exists": (
            parent_ok,
            parent_reason,
            _unique(
                (
                    [f"long_term_plan:{long_plan.get('plan_id')}"]
                    if long_plan.get("plan_id") else []
                )
                + (
                    [f"short_term_plan:{short_plan.get('plan_id')}"]
                    if short_plan.get("plan_id") else []
                )
            ),
        ),
        "prerequisite_satisfied": (
            prerequisite_ok,
            (
                "prerequisite_satisfied"
                if prerequisite_ok
                else "prerequisite_not_satisfied:"
                + ",".join(missing_prerequisites)
            ),
            _unique(route_refs + prerequisite_evidence_refs),
        ),
        "time_budget": (
            time_ok,
            (
                "not_applicable_for_long_term_scope"
                if scope == "long_term"
                else (
                    "within_time_budget"
                    if time_ok
                    else (
                        "estimated_minutes_missing"
                        if not duration_known
                        else (
                            "candidate_duration_must_be_less_than_1440"
                            if not duration_below_day
                            else (
                                "time_budget_missing"
                                if budget is None else "time_budget_exceeded"
                            )
                        )
                    )
                )
            ),
            budget_refs,
        ),
        "due_review_priority": (
            due_ok,
            (
                "due_review_priority_satisfied"
                if due_ok else "due_review_priority_required"
            ),
            [f"review:{row.review_state_id}" for row in due_rows],
        ),
        "trusted_source": (
            bool(descriptor["trusted"] and descriptor["evidence_refs"]),
            (
                "trusted_source_present"
                if descriptor["trusted"] and descriptor["evidence_refs"]
                else "trusted_source_required"
            ),
            descriptor["evidence_refs"],
        ),
        "low_data_protection": (
            low_data_ok,
            (
                "low_data_safe_candidate"
                if low_data and low_data_ok
                else (
                    "sufficient_data"
                    if not low_data
                    else (
                        "insufficient_data_difficulty_unknown"
                        if descriptor["difficulty"] is None
                        else "insufficient_data_for_high_risk_candidate"
                    )
                )
            ),
            [f"state:{state_digest}"],
        ),
        "approved_stage_mapping": (
            stage_mapping_ok,
            (
                "approved_stage_mapping_present"
                if stage_mapping_ok else "approved_stage_mapping_required"
            ),
            route_refs,
        ),
    }
    return (
        [
            {
                "key": key,
                "passed": values[key][0],
                "reason": values[key][1],
                "source_refs": values[key][2],
            }
            for key in HARD_CONSTRAINT_ORDER
        ],
        time_ok,
        duration_known,
        estimated,
    )


def _build_score_components(
    *,
    descriptor: dict[str, Any],
    scope: str,
    target_ids: set[str],
    mastery_by_kp: dict[str, float],
    mastery_ref_by_kp: dict[str, str],
    recent_kp_ids: set[str],
    budget: int | None,
    budget_refs: list[str],
    profile: dict[str, Any],
    profile_refs: list[str],
    coverage: float | int | None,
    state_digest: str,
    time_ok: bool,
    duration_known: bool,
    now: datetime,
) -> dict[str, dict[str, Any]]:
    """Calculate transparent fixed-weight inputs after the hard gate."""

    kp_set = set(descriptor["kp_ids"])
    mastery_values = [
        mastery_by_kp[item] for item in kp_set if item in mastery_by_kp
    ]
    learning_gain = (
        1 - sum(mastery_values) / len(mastery_values)
        if mastery_values else None
    )
    retention = descriptor.get("retention")
    retention_benefit = (
        1 - float(retention)
        if isinstance(retention, (int, float)) else None
    )
    if descriptor.get("next_review_at") is not None:
        overdue_days = max(
            0.0, (now - descriptor["next_review_at"]).total_seconds() / 86400
        )
        retention_benefit = max(
            retention_benefit or 0.0,
            min(1.0, 0.5 + overdue_days / 30),
        )
    knowledge_coverage = (
        len(kp_set.intersection(target_ids)) / len(target_ids)
        if target_ids and kp_set else None
    )
    time_fit = 1.0 if time_ok and scope != "long_term" else None
    difficulty = descriptor.get("difficulty")
    if isinstance(difficulty, (int, float)) and mastery_values:
        learner_level = 1 + 4 * (
            sum(mastery_values) / len(mastery_values)
        )
        difficulty_fit = 1 - abs(float(difficulty) - learner_level) / 4
    else:
        difficulty_fit = None
    preferences = profile.get("preferences")
    preferences = preferences if isinstance(preferences, dict) else {}
    preferred_types = preferences.get("resource_preference")
    if isinstance(preferred_types, list) and preferred_types:
        autonomy_support = (
            1.0
            if descriptor["resource_type"] in {
                str(item) for item in preferred_types
            }
            else 0.5
        )
        autonomy_sources = profile_refs
    else:
        autonomy_support = 0.5
        autonomy_sources = ["rule:neutral_no_confirmed_preference"]
    repetition_ratio = (
        len(kp_set.intersection(recent_kp_ids)) / len(kp_set)
        if kp_set and recent_kp_ids else None
    )
    missing_positive = sum(
        value is None
        for value in (
            learning_gain,
            retention_benefit,
            knowledge_coverage,
            time_fit,
            difficulty_fit,
        )
    )
    uncertainty = _clamp(
        0.6 * (missing_positive / 5)
        + 0.4 * (1 - float(coverage or 0.0))
    )
    return {
        "learning_gain": _score_metric(
            learning_gain,
            sources=[
                mastery_ref_by_kp[item]
                for item in kp_set
                if item in mastery_by_kp and item in mastery_ref_by_kp
            ],
            reason="mastery_missing_for_candidate",
        ),
        "retention_benefit": _score_metric(
            retention_benefit,
            sources=[
                ref
                for ref in descriptor["source_refs"]
                if ref.startswith("review:")
            ],
            reason="review_state_missing_for_candidate",
        ),
        "knowledge_coverage": _score_metric(
            knowledge_coverage,
            sources=_unique(
                [
                    f"knowledge_point:{item}"
                    for item in kp_set.intersection(target_ids)
                ]
            ),
            reason="candidate_or_target_knowledge_points_missing",
        ),
        "time_fit": _score_metric(
            time_fit,
            sources=budget_refs,
            reason=(
                "time_fit_not_applicable_for_long_term_scope"
                if scope == "long_term"
                else (
                    "estimated_minutes_missing"
                    if not duration_known
                    else (
                        "time_budget_missing"
                        if budget is None else "candidate_exceeds_time_budget"
                    )
                )
            ),
        ),
        "difficulty_fit": _score_metric(
            difficulty_fit,
            sources=descriptor["source_refs"],
            reason=(
                "difficulty_not_applicable_for_review"
                if descriptor["kind"] == "due_review"
                else "learner_mastery_missing_for_difficulty_fit"
                if difficulty is not None
                else "resource_difficulty_missing"
            ),
        ),
        "autonomy_support": _score_metric(
            autonomy_support,
            sources=autonomy_sources,
            reason="user_preference_missing",
        ),
        "repetition_penalty": _score_metric(
            repetition_ratio,
            sources=(
                [f"state:{state_digest}"]
                if repetition_ratio is not None else []
            ),
            reason="no_recent_knowledge_point_history",
        ),
        "uncertainty_risk": _score_metric(
            uncertainty,
            sources=[f"state:{state_digest}"],
            reason="state_data_quality_missing",
        ),
    }


# ---------------------------------------------------------------------------
# Candidate orchestration
# ---------------------------------------------------------------------------


def build_path_candidates(
    db: Session,
    user_id: int,
    *,
    state: dict[str, Any] | None = None,
    scope: Literal["long_term", "short_term", "daily_task"] = "daily_task",
    plan_context: dict[str, Any] | None = None,
    limit: int = 10,
    include_blocked: bool = True,
    daily_capacity: int | None = None,
    available_minutes: int | None = None,
) -> dict[str, Any]:
    """Build traceable candidates, applying every hard gate before scoring."""

    if scope not in {"long_term", "short_term", "daily_task"}:
        raise ValueError("scope must be one of: long_term, short_term, daily_task")
    if not 1 <= int(limit) <= 30:
        raise ValueError("limit must be between 1 and 30")
    if daily_capacity is not None and daily_capacity < 0:
        raise ValueError("daily_capacity must be non-negative")
    context_was_omitted = plan_context is None
    plan_context = dict(plan_context or {})
    if state is None:
        state = build_multiscale_state(
            db, user_id, plan_context=plan_context, window_days=30
        )
    state = _validated_state(state, user_id=user_id)
    if context_was_omitted:
        state_macro = (
            state.get("macro") if isinstance(state.get("macro"), dict) else {}
        )
        state_meso = (
            state.get("meso") if isinstance(state.get("meso"), dict) else {}
        )
        state_route = state_macro.get("approved_route")
        state_route = dict(state_route) if isinstance(state_route, dict) else {}
        state_stage = state_macro.get("current_stage")
        state_stage = dict(state_stage) if isinstance(state_stage, dict) else {}
        state_short_plan = (
            dict(state_meso.get("current_short_term_plan"))
            if isinstance(state_meso.get("current_short_term_plan"), dict)
            else {}
        )
        if state_route:
            plan_context["long_term_plan"] = {
                "plan_id": (
                    state_short_plan.get("long_term_plan_id")
                    or "state_snapshot"
                ),
                "planning_route": state_route,
                "textbook_selection": {
                    "stage_id": (
                        state_stage.get("phase_id")
                        or state_stage.get("stage_id")
                    ),
                    "stage_name": state_stage.get("name"),
                    "books": state_stage.get("books") or [],
                },
            }
        plan_context["short_term_plan"] = state_short_plan
    now = datetime.utcnow()
    long_row = _current_row(db, LongTermPlan, user_id)
    short_row = _current_row(db, ShortTermPlan, user_id)
    long_plan = _plan_layer(plan_context, "long_term_plan", long_row)
    short_plan = _plan_layer(plan_context, "short_term_plan", short_row)
    route, stage = _route_and_stage(long_plan)
    approved_route = bool(
        route.get("planning_status") == "approved_route"
        and route.get("route_status") == "approved"
    )
    route_refs = (
        [f"route:{route.get('route_id')}"] if route.get("route_id") else []
    )
    route_evidence = _route_evidence(route, stage)
    if scope == "long_term":
        budget, budget_refs = None, []
    elif scope == "short_term":
        short_budget = (
            available_minutes
            if available_minutes is not None
            else plan_context.get("short_term_available_minutes")
        )
        if short_budget is None:
            short_budget = short_plan.get("available_minutes")
        budget = (
            max(0, min(1440, int(short_budget)))
            if isinstance(short_budget, (int, float))
            else None
        )
        budget_refs = (
            ["plan_context:short_term_available_minutes"]
            if budget is not None else []
        )
    else:
        budget, budget_refs = _available_minutes(
            db, user_id, plan_context, available_minutes
        )

    review_capacity = (
        int(daily_capacity) if daily_capacity is not None else int(limit)
    )
    due_rows = (
        db.query(LearnerKPReviewState)
        .filter(
            LearnerKPReviewState.learner_id == user_id,
            LearnerKPReviewState.status == "active",
            LearnerKPReviewState.next_review_at.is_not(None),
            LearnerKPReviewState.next_review_at <= now,
        )
        .order_by(
            LearnerKPReviewState.next_review_at.asc(),
            LearnerKPReviewState.id.asc(),
        )
        .limit(review_capacity)
        .all()
    )
    due_ids = {
        str(item.get("kp_id"))
        for item in state.get("meso", {}).get("due_review_knowledge_points", [])
        if isinstance(item, dict) and item.get("kp_id")
    }
    weak_ids = {
        str(item.get("kp_id"))
        for item in state.get("meso", {}).get("weak_knowledge_points", [])
        if isinstance(item, dict) and item.get("kp_id")
    }
    planned_ids = {
        str(item.get("kp_id"))
        for item in state.get("meso", {}).get("planned_knowledge_points", [])
        if isinstance(item, dict) and item.get("kp_id")
    }
    target_ids = due_ids | weak_ids | planned_ids
    kp_names = _named_kps(db, target_ids)
    mastery_by_kp = {
        str(item.get("kp_id")): float(item["mastery"])
        for item in state.get("micro", {}).get("mastery_by_knowledge_point", [])
        if isinstance(item, dict)
        and item.get("kp_id")
        and isinstance(item.get("mastery"), (int, float))
    }
    mastery_ref_by_kp = {
        str(item.get("kp_id")): str(item.get("source_ref"))
        for item in state.get("micro", {}).get(
            "mastery_by_knowledge_point", []
        )
        if isinstance(item, dict)
        and item.get("kp_id")
        and str(item.get("source_ref") or "").startswith(
            "knowledge_mastery_states:"
        )
    }
    recent_kp_ids = set(
        state.get("micro", {}).get("recent_knowledge_point_ids", [])
    )
    descriptors: list[dict[str, Any]] = []

    if scope == "daily_task":
        for row in due_rows:
            kp = kp_names.get(str(row.kp_id))
            if not kp:
                continue
            descriptors.append(
                {
                    "kind": "due_review",
                    "stage": stage,
                    "books": [
                        {"name": str(book)}
                        for book in stage.get("books", [])
                        if str(book).strip()
                    ],
                    "knowledge_points": [
                        {"kp_id": kp["kp_id"], "name": kp["name"]}
                    ],
                    "kp_ids": {kp["kp_id"]},
                    "estimated_minutes": 10,
                    "difficulty": None,
                    "recommended_action": "review",
                    "source_refs": [
                        f"review:{row.review_state_id}",
                        f"knowledge_point:{kp['kp_id']}",
                    ],
                    "evidence_refs": _unique(
                        route_evidence + [f"review_evidence:{row.review_state_id}"]
                    ),
                    "trusted": _is_trusted(kp["source"]),
                    "retention": (
                        _clamp(float(row.retention_estimate))
                        if row.retention_estimate is not None else None
                    ),
                    "next_review_at": row.next_review_at,
                    "resource_type": "review",
                    "resource_id": row.review_state_id,
                }
            )

        task_rows = (
            db.query(LearningTask)
            .filter(
                LearningTask.user_id == user_id,
                LearningTask.status.in_(["pending", "active"]),
            )
            .order_by(LearningTask.created_at.desc())
            .limit(50)
            .all()
        )
        task_question_ids = {
            str(question_id)
            for row in task_rows
            for question_id in _json(row.question_ids_json, [])
            if str(question_id).strip()
        }
        task_question_rows = (
            db.query(LearningQuestion)
            .filter(LearningQuestion.question_id.in_(task_question_ids))
            .order_by(LearningQuestion.question_id.asc())
            .all()
            if task_question_ids else []
        )
        task_difficulty_by_id = {
            str(row.question_id): (
                float(row.difficulty) if row.difficulty is not None else None
            )
            for row in task_question_rows
        }
        for row in task_rows:
            ids = {
                str(item)
                for item in _json(row.kp_ids_json, [])
                if str(item) in kp_names
            }
            if not ids:
                continue
            question_ids = [
                str(item) for item in _json(row.question_ids_json, []) if str(item)
            ]
            difficulty_values = [
                float(task_difficulty_by_id[question_id])
                for question_id in question_ids
                if task_difficulty_by_id.get(question_id) is not None
            ]
            descriptors.append(
                {
                    "kind": "task",
                    "stage": stage,
                    "books": [
                        {"name": str(book)}
                        for book in stage.get("books", [])
                        if str(book).strip()
                    ],
                    "knowledge_points": [
                        {"kp_id": item, "name": kp_names[item]["name"]}
                        for item in sorted(ids)
                    ],
                    "kp_ids": ids,
                    "estimated_minutes": (
                        int(row.estimated_minutes)
                        if row.estimated_minutes is not None else None
                    ),
                    "difficulty": (
                        sum(difficulty_values) / len(difficulty_values)
                        if difficulty_values else None
                    ),
                    "recommended_action": (
                        "review" if row.task_type == "review" else "learn"
                    ),
                    "source_refs": [f"task:{row.task_id}"],
                    "evidence_refs": route_evidence,
                    "trusted": bool(route_evidence),
                    "retention": None,
                    "resource_type": str(row.task_type),
                    "resource_id": row.task_id,
                }
            )

        resource_query = db.query(TeachingResource).filter(
            TeachingResource.status == "active"
        )
        if target_ids:
            resource_query = resource_query.filter(
                or_(
                    *[
                        TeachingResource.kp_ids_json.contains(
                            f'"{kp_id}"'
                        )
                        for kp_id in sorted(target_ids)
                    ]
                )
            )
        else:
            resource_query = resource_query.filter(False)
        resources = (
            resource_query.order_by(TeachingResource.resource_id.asc())
            .limit(500)
            .all()
        )
        for row in resources:
            ids = {
                str(item)
                for item in _json(row.kp_ids_json, [])
                if str(item) in kp_names
            }
            if not ids or (target_ids and not ids.intersection(target_ids)):
                continue
            descriptors.append(
                {
                    "kind": "resource",
                    "stage": stage,
                    "books": [
                        {"name": str(book)}
                        for book in stage.get("books", [])
                        if str(book).strip()
                    ],
                    "knowledge_points": [
                        {"kp_id": item, "name": kp_names[item]["name"]}
                        for item in sorted(ids)
                    ],
                    "kp_ids": ids,
                    "estimated_minutes": 15 if row.resource_type == "video" else 10,
                    "difficulty": None,
                    "recommended_action": "learn",
                    "source_refs": [f"resource:{row.resource_id}"],
                    "evidence_refs": _unique(
                        route_evidence
                        + (
                            [f"evidence:resource:{row.resource_id}"]
                            if _is_trusted(row.source)
                            else []
                        )
                    ),
                    "trusted": _is_trusted(row.source),
                    "retention": None,
                    "resource_type": str(row.resource_type),
                    "resource_id": str(row.resource_id),
                }
            )

        question_query = db.query(QuestionBankItem).filter(
            QuestionBankItem.status == "active"
        )
        if target_ids:
            question_query = question_query.filter(
                or_(
                    *[
                        QuestionBankItem.kp_ids_json.contains(f'"{kp_id}"')
                        for kp_id in sorted(target_ids)
                    ]
                )
            )
        else:
            question_query = question_query.filter(False)
        questions = (
            question_query.order_by(QuestionBankItem.question_id.asc())
            .limit(500)
            .all()
        )
        for row in questions:
            ids = {
                str(item)
                for item in _json(row.kp_ids_json, [])
                if str(item) in kp_names
            }
            if not ids or (target_ids and not ids.intersection(target_ids)):
                continue
            descriptors.append(
                {
                    "kind": "question",
                    "stage": stage,
                    "books": [
                        {"name": str(book)}
                        for book in stage.get("books", [])
                        if str(book).strip()
                    ],
                    "knowledge_points": [
                        {"kp_id": item, "name": kp_names[item]["name"]}
                        for item in sorted(ids)
                    ],
                    "kp_ids": ids,
                    "estimated_minutes": 5,
                    "difficulty": (
                        float(row.difficulty)
                        if row.difficulty is not None else None
                    ),
                    "recommended_action": "practice",
                    "source_refs": [f"question:{row.question_id}"],
                    "evidence_refs": (
                        [f"evidence:question:{row.question_id}"]
                        if _is_trusted(row.source) else []
                    ),
                    "trusted": _is_trusted(row.source),
                    "retention": None,
                    "resource_type": "question",
                    "resource_id": str(row.question_id),
                }
            )
    else:
        phases = route.get("phases") if isinstance(route.get("phases"), list) else []
        selected_phases = phases if scope == "long_term" else ([stage] if stage else [])
        for item in selected_phases:
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                continue
            descriptors.append(
                {
                    "kind": "route_stage",
                    "stage": dict(item),
                    "books": [
                        {"name": str(book)}
                        for book in item.get("books", [])
                        if str(book).strip()
                    ],
                    "knowledge_points": [
                        {"name": str(name)}
                        for name in item.get("learning_focus", [])
                        if str(name).strip()
                    ],
                    "kp_ids": set(),
                    "estimated_minutes": (
                        int(item["estimated_minutes"])
                        if isinstance(item.get("estimated_minutes"), (int, float))
                        else None
                    ),
                    "difficulty": None,
                    "recommended_action": "continue_stage",
                    "source_refs": _unique(
                        route_refs
                        + [f"route_stage:{item.get('phase_id')}"]
                    ),
                    "evidence_refs": _route_evidence(route, item),
                    "trusted": bool(_route_evidence(route, item)),
                    "retention": None,
                    "resource_type": "route_stage",
                    "resource_id": str(item.get("phase_id") or ""),
                }
            )

    if not descriptors and scope == "daily_task" and stage:
        focus = next(
            (
                str(item)
                for item in stage.get("learning_focus", [])
                if str(item).strip()
            ),
            "",
        )
        descriptors.append(
            {
                "kind": "route_focus",
                "stage": stage,
                "books": [
                    {"name": str(book)}
                    for book in stage.get("books", [])
                    if str(book).strip()
                ],
                "knowledge_points": [{"name": focus}] if focus else [],
                "kp_ids": set(),
                "estimated_minutes": (
                    int(stage["daily_estimated_minutes"])
                    if isinstance(
                        stage.get("daily_estimated_minutes"), (int, float)
                    )
                    else None
                ),
                "difficulty": None,
                "recommended_action": "learn",
                "source_refs": _unique(route_refs + ["plan_context:route_focus"]),
                "evidence_refs": route_evidence,
                "trusted": bool(route_evidence),
                "retention": None,
                "resource_type": "route_focus",
                "resource_id": str(stage.get("phase_id") or ""),
            }
        )

    profile, profile_refs = _profile_payload(db, user_id)
    profile_goal = profile.get("goals") if isinstance(profile.get("goals"), dict) else {}
    goal_text = str(
        profile_goal.get("target_exam_or_course")
        or profile_goal.get("long_term_goal")
        or ""
    ).strip()
    route_goal = str(route.get("goal_name") or "").strip()
    if not approved_route:
        goal_aligned = False
    elif not goal_text:
        goal_aligned = False
    else:
        goal_aligned = goal_text in route_goal or route_goal in goal_text
    goal_reason = (
        "learner_goal_missing"
        if not goal_text
        else (
            "goal_route_aligned"
            if goal_aligned else "goal_route_mismatch"
        )
    )

    coverage = state.get("data_quality", {}).get("coverage")
    low_data = not isinstance(coverage, (int, float)) or float(coverage) < 0.5
    planned_stage_ids = {
        str(item.get("phase_id") or "")
        for item in route.get("phases", [])
        if isinstance(item, dict)
    }
    completed_prerequisites = {
        str(item).strip()
        for item in list(profile.get("completed_courses") or [])
        if str(item).strip()
    }
    required_prerequisites = {
        str(item).strip()
        for item in stage.get("prerequisites", [])
        if str(item).strip()
    }
    missing_prerequisites = sorted(
        required_prerequisites - completed_prerequisites
    )
    prerequisite_evidence_refs = (
        profile_refs if required_prerequisites and not missing_prerequisites else []
    )
    long_plan_id = str(long_plan.get("plan_id") or "")
    short_plan_id = str(short_plan.get("plan_id") or "")
    claimed_long_status = long_plan.get("status")
    claimed_short_status = short_plan.get("status")
    long_plan_verified = bool(
        long_row is not None
        and str(long_row.plan_id) == long_plan_id
        and str(long_row.status or "") in _ACTIVE_PLAN_STATUSES
        and (
            claimed_long_status is None
            or str(claimed_long_status) == str(long_row.status)
        )
    )
    short_plan_verified = bool(
        short_row is not None
        and str(short_row.plan_id) == short_plan_id
        and str(short_row.status or "") in _ACTIVE_PLAN_STATUSES
        and (
            claimed_short_status is None
            or str(claimed_short_status) == str(short_row.status)
        )
    )
    parent_link_verified = bool(
        long_plan_verified
        and short_plan_verified
        and str(short_row.long_term_plan_id or "") == long_plan_id
        and str(short_plan.get("long_term_plan_id") or "") == long_plan_id
    )
    current_stage_id = str(
        stage.get("phase_id") or stage.get("stage_id") or ""
    )
    approved_stage = next(
        (
            item
            for item in route.get("phases", [])
            if isinstance(item, dict)
            and str(item.get("phase_id") or "") == current_stage_id
        ),
        {},
    )
    allowed_stage_kp_ids = {
        str(item).strip()
        for item in approved_stage.get("knowledge_point_ids", [])
        if str(item).strip()
    }
    allowed_stage_kp_names = {
        str(item).strip()
        for item in approved_stage.get("learning_focus", [])
        if str(item).strip()
    }
    allowed_stage_books = {
        str(item).strip()
        for item in approved_stage.get("books", [])
        if str(item).strip()
    }
    results: list[dict[str, Any]] = []
    for descriptor in descriptors:
        hard_results, time_ok, duration_known, estimated = (
            _evaluate_hard_constraints(
                descriptor=descriptor,
                scope=scope,
                long_plan=long_plan,
                short_plan=short_plan,
                approved_route=approved_route,
                planned_stage_ids=planned_stage_ids,
                allowed_stage_kp_ids=allowed_stage_kp_ids,
                allowed_stage_kp_names=allowed_stage_kp_names,
                allowed_stage_books=allowed_stage_books,
                missing_prerequisites=missing_prerequisites,
                prerequisite_evidence_refs=prerequisite_evidence_refs,
                goal_aligned=goal_aligned,
                goal_reason=goal_reason,
                route_refs=route_refs,
                profile_refs=profile_refs,
                budget=budget,
                budget_refs=budget_refs,
                due_ids=due_ids,
                due_rows=due_rows,
                low_data=low_data,
                state_digest=str(state["state_digest"]),
                long_plan_verified=long_plan_verified,
                short_plan_verified=short_plan_verified,
                parent_link_verified=parent_link_verified,
            )
        )
        components = _build_score_components(
            descriptor=descriptor,
            scope=scope,
            target_ids=target_ids,
            mastery_by_kp=mastery_by_kp,
            mastery_ref_by_kp=mastery_ref_by_kp,
            recent_kp_ids=recent_kp_ids,
            budget=budget,
            budget_refs=budget_refs,
            profile=profile,
            profile_refs=profile_refs,
            coverage=coverage,
            state_digest=str(state["state_digest"]),
            time_ok=time_ok,
            duration_known=duration_known,
            now=now,
        )
        eligible = all(item["passed"] for item in hard_results)
        payload = {
            "candidate_id": _candidate_id(
                scope, descriptor["kind"], descriptor["source_refs"]
            ),
            "scope": scope,
            "stage": descriptor["stage"],
            "books": descriptor["books"],
            "knowledge_points": descriptor["knowledge_points"],
            "estimated_minutes": estimated,
            "eligible": eligible,
            "blocked_reasons": [
                item["reason"] for item in hard_results if not item["passed"]
            ],
            "hard_constraint_results": hard_results,
            "score": _score(components),
            "score_components": components,
            "evidence_refs": descriptor["evidence_refs"],
            "source_refs": descriptor["source_refs"],
            "recommended_action": descriptor["recommended_action"],
        }
        results.append(
            PathCandidate.model_validate(payload).model_dump(mode="json")
        )

    results.sort(
        key=lambda item: (
            not item["eligible"],
            item["recommended_action"] != "review",
            -float(item["score"]),
            item["candidate_id"],
        )
    )
    if not include_blocked:
        results = [item for item in results if item["eligible"]]
    results = results[: int(limit)]
    return {
        "schema_version": SCHEMA_VERSION,
        "learner_id": str(user_id),
        "scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "state_digest": state.get("state_digest"),
        "items": results,
        "counts": {
            "returned": len(results),
            "eligible": sum(item["eligible"] for item in results),
            "blocked": sum(not item["eligible"] for item in results),
            "due_reviews_considered": len(due_rows),
        },
        "scoring_policy": {
            "hard_constraint_order": list(HARD_CONSTRAINT_ORDER),
            "positive_weights": POSITIVE_WEIGHTS,
            "repetition_weight": REPETITION_WEIGHT,
            "uncertainty_weight": UNCERTAINTY_WEIGHT,
            "missing_positive_components": "renormalize_available_only",
            "risk_components": "independent_deductions",
        },
    }

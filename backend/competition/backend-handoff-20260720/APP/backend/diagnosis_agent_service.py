from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


UTC = timezone.utc

from sqlalchemy.orm import Session

from APP.backend.agent_contracts import DiagnosisReport, LearnerContextBrief
from APP.backend.deep_training_service import diagnose_learning_state
from APP.backend.health_memory import get_or_create_profile
from APP.backend.memory_agent_service import build_learner_context_brief
from APP.backend.learner_profile_service import (
    apply_learner_profile_update,
    get_locked_profile_fields,
    parse_json_field,
    serialize_json_field,
)
from APP.backend.database import (
    AgentEvent,
    LearnerKnowledgeMastery,
    LearningActivityRecord,
    MistakeRecord,
    PersonalizationMemory,
    QuestionAttempt,
)


ONBOARDING_ACTIVITY_TYPE = "onboarding_survey"
DIAGNOSIS_ACTIVITY_TYPE = "diagnosis_summary"


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip() or default


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    text = _text(value)
    return [text] if text else []


def _choice_text(value: Any, default: str = "") -> str:
    if isinstance(value, list):
        items = [_text(item) for item in value if _text(item)]
        return "、".join(items) if items else default
    return _text(value, default)


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _parse_daily_minutes(value: Any) -> int:
    if isinstance(value, bool):
        return 30
    if isinstance(value, (int, float)):
        return int(value)
    text = _text(value)
    duration_aliases = {
        "15 分钟以内": 15,
        "15-30 分钟": 25,
        "30-60 分钟": 45,
        "60 分钟以上": 75,
    }
    if text in duration_aliases:
        return duration_aliases[text]
    try:
        return int(text)
    except ValueError:
        return 30


def _minutes_to_tasks(minutes: int) -> int:
    if minutes <= 20:
        return 1
    if minutes <= 45:
        return 2
    if minutes <= 75:
        return 3
    return 4


def _difficulty_from_foundation(foundation: str, learner_group: str) -> str:
    normalized = foundation.lower()
    if normalized in {"none", "weak"}:
        return "D1"
    if normalized in {"basic", "beginner"}:
        return "D2"
    if normalized in {"intermediate", "mid"}:
        return "D3"
    if normalized in {"advanced", "strong"}:
        return "D4"
    if learner_group == "学历教育":
        return "D2"
    return "D1"


def normalize_onboarding_answers(survey_answers: dict[str, Any], learner_group: str = "") -> dict[str, Any]:
    background = survey_answers.get("background") if isinstance(survey_answers.get("background"), dict) else {}
    goals = survey_answers.get("goals") if isinstance(survey_answers.get("goals"), dict) else {}
    preferences = survey_answers.get("preferences") if isinstance(survey_answers.get("preferences"), dict) else {}
    special = survey_answers.get("special_requirements") if isinstance(survey_answers.get("special_requirements"), dict) else {}

    direct_group = _text(survey_answers.get("user_group"))
    if not direct_group:
        direct_group = _text(survey_answers.get("learner_group_title")).replace("群体", "")
    if not direct_group:
        direct_group = _text(survey_answers.get("learner_group")) or learner_group
    daily_minutes = _parse_daily_minutes(
        preferences.get("daily_available_minutes")
        or survey_answers.get("daily_available_minutes")
        or 30
    )
    foundation = _text(
        background.get("tcm_foundation")
        or background.get("foundation_level")
        or survey_answers.get("tcm_foundation"),
        "",
    )
    normalized = {
        "user_group": direct_group or "未选择用户群体",
        "learner_group": _text(survey_answers.get("learner_group")),
        "learner_group_title": _text(survey_answers.get("learner_group_title")),
        "education": _text(background.get("education") or survey_answers.get("education"), "未填写"),
        "major_or_role": _text(
            background.get("major_or_role")
            or background.get("education_major")
            or survey_answers.get("major_or_role"),
            "未填写",
        ),
        "year": _text(background.get("year") or survey_answers.get("year")),
        "tcm_foundation": foundation,
        "learned_courses": _listify(background.get("learned_courses") or survey_answers.get("learned_courses")),
        "long_term_goal": _text(goals.get("long_term_goal") or survey_answers.get("long_term_goal")),
        "short_term_goal": _text(goals.get("short_term_goal") or survey_answers.get("short_term_goals") or survey_answers.get("short_term_goal")),
        "target_exam_or_course": _text(goals.get("target_exam_or_course") or survey_answers.get("target_exam_or_course")),
        "current_difficulties": _choice_text(
            goals.get("current_difficulties")
            or background.get("weak_area")
            or survey_answers.get("current_difficulties")
            or survey_answers.get("difficulty_notes")
        ),
        "daily_available_minutes": max(10, min(daily_minutes, 180)),
        "preferred_time_slot": _text(preferences.get("preferred_time_slot") or survey_answers.get("preferred_time_slot"), "未填写"),
        "resource_preference": _listify(preferences.get("resource_preference") or survey_answers.get("resource_preference")),
        "learning_mode": _text(preferences.get("learning_mode") or survey_answers.get("learning_mode")),
        "difficulty_preference": _text(
            preferences.get("difficulty_preference")
            or preferences.get("default_difficulty")
            or survey_answers.get("difficulty_preference")
        ),
        "device_environment": _text(preferences.get("device_environment") or survey_answers.get("device_environment")),
        "notification_quiet_hours": _text(preferences.get("notification_quiet_hours") or survey_answers.get("notification_quiet_hours")),
        "special_requirement": _text(special.get("description") or survey_answers.get("special_requirement")),
    }
    if "locked_fields" in survey_answers:
        normalized["locked_fields"] = survey_answers.get("locked_fields") or []
    if "profile_locked_fields" in survey_answers:
        normalized["profile_locked_fields"] = survey_answers.get("profile_locked_fields") or []
    if "field_sources" in survey_answers:
        normalized["field_sources"] = survey_answers.get("field_sources") or {}
    return normalized


def build_l0_baseline(onboarding_answers: dict[str, Any], learner_group: str = "") -> dict[str, Any]:
    normalized = normalize_onboarding_answers(onboarding_answers, learner_group)
    preferred_difficulty = normalized["difficulty_preference"] or _difficulty_from_foundation(
        normalized["tcm_foundation"],
        normalized["user_group"],
    )
    return {
        "stage_id": "L0",
        "learner_group": normalized["user_group"],
        "education": normalized["education"],
        "major_or_role": normalized["major_or_role"],
        "year": normalized["year"],
        "long_term_goal": normalized["long_term_goal"],
        "short_term_goal": normalized["short_term_goal"],
        "target_exam_or_course": normalized["target_exam_or_course"],
        "current_difficulties": normalized["current_difficulties"],
        "daily_available_minutes": normalized["daily_available_minutes"],
        "preferred_time_slot": normalized["preferred_time_slot"],
        "resource_preference": normalized["resource_preference"],
        "preferred_difficulty": preferred_difficulty,
        "device_environment": normalized["device_environment"],
        "notification_quiet_hours": normalized["notification_quiet_hours"],
        "default_daily_tasks": _minutes_to_tasks(normalized["daily_available_minutes"]),
    }


def _upsert_onboarding_memory(db: Session, user_id: int, title: str, content: str, source: str) -> None:
    existing = (
        db.query(PersonalizationMemory)
        .filter(
            PersonalizationMemory.user_id == user_id,
            PersonalizationMemory.title == title,
            PersonalizationMemory.source == source,
            PersonalizationMemory.is_active.is_(True),
        )
        .first()
    )
    if existing:
        existing.content = content
        existing.updated_at = _now()
        return
    db.add(
        PersonalizationMemory(
            user_id=user_id,
            category="note",
            importance="normal",
            title=title,
            content=content,
            source=source,
            is_active=True,
            confidence=0.82,
        )
    )


def submit_onboarding_survey(
    db: Session,
    user_id: int,
    survey_answers: dict[str, Any],
    learner_group: str = "",
    locked_fields: list[str] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    normalized = normalize_onboarding_answers(survey_answers, learner_group)
    l0_baseline = build_l0_baseline(survey_answers, learner_group)
    profile = get_or_create_profile(db, user_id, commit=commit)

    existing_locked_fields = list(get_locked_profile_fields(profile))
    existing_lock_reason = parse_json_field(getattr(profile, "lock_reason_json", "{}"), {})
    survey_locked_fields = survey_answers.get("locked_fields") if isinstance(survey_answers, dict) else None
    profile_locked_fields = locked_fields or survey_answers.get("profile_locked_fields") or []

    stored_survey = dict(survey_answers)
    if survey_locked_fields is not None:
        stored_survey["locked_fields"] = survey_locked_fields
    if profile_locked_fields:
        stored_survey["profile_locked_fields"] = profile_locked_fields
    profile.survey_json = json.dumps(stored_survey, ensure_ascii=False)

    profile_update = {
        "learner_group": normalized["user_group"],
        "learning_goal": normalized["long_term_goal"] or normalized["short_term_goal"] or normalized["target_exam_or_course"],
        "time_constraints": f"每天 {l0_baseline['daily_available_minutes']} 分钟；偏好时段 {l0_baseline['preferred_time_slot']}",
        "resource_preferences": "；".join(
            part
            for part in [
                "、".join(l0_baseline["resource_preference"]) if l0_baseline["resource_preference"] else "",
                normalized["learning_mode"],
                f"难度偏好 {l0_baseline['preferred_difficulty']}",
            ]
            if part
        ),
        "current_difficulties": normalized["current_difficulties"] or normalized["tcm_foundation"] or "待补充当前困难",
        "learning_needs": "；".join(
            part
            for part in [
                normalized["device_environment"],
                normalized["notification_quiet_hours"],
                normalized["special_requirement"],
            ]
            if part
        ),
    }
    apply_learner_profile_update(profile, profile_update, source="diagnosis_agent")

    if profile_locked_fields:
        merged_locked_fields = sorted(set(existing_locked_fields).union(profile_locked_fields))
        merged_lock_reason = dict(existing_lock_reason) if isinstance(existing_lock_reason, dict) else {}
        for field in profile_locked_fields:
            merged_lock_reason.setdefault(field, "用户在学情调查中确认")
        profile.locked_fields_json = serialize_json_field(merged_locked_fields)
        profile.lock_reason_json = serialize_json_field(merged_lock_reason)

    payload = {
        "status": "onboarding_completed",
        "learner_group": normalized["user_group"],
        "survey_answers": normalized,
        "field_sources": survey_answers.get("field_sources", {}),
        "l0_baseline": l0_baseline,
        "submitted_at": _now().isoformat(),
    }
    db.add(
        LearningActivityRecord(
            user_id=user_id,
            activity_type=ONBOARDING_ACTIVITY_TYPE,
            resource_id=f"onboarding:{user_id}",
            resource_type="survey",
            duration_minutes=0,
            completion_status="completed",
            score=100.0,
            payload_json=json.dumps(payload, ensure_ascii=False),
            created_at=_now(),
        )
    )
    _upsert_onboarding_memory(
        db,
        user_id,
        "Onboarding Survey",
        json.dumps(payload, ensure_ascii=False),
        "onboarding_survey",
    )
    if commit:
        db.commit()
    else:
        db.flush()

    return {
        "status": payload["status"],
        "learner_group": payload["learner_group"],
        "field_sources": payload["field_sources"],
        "l0_baseline": l0_baseline,
        "needs_survey_popup": False,
    }


def _latest_activity_payload(db: Session, user_id: int, activity_type: str) -> dict[str, Any]:
    row = (
        db.query(LearningActivityRecord)
        .filter(LearningActivityRecord.user_id == user_id, LearningActivityRecord.activity_type == activity_type)
        .order_by(LearningActivityRecord.created_at.desc(), LearningActivityRecord.id.desc())
        .first()
    )
    return _json_dict(row.payload_json) if row else {}


def _baseline_from_profile(user_id: int, learner_group: str, profile: Any) -> dict[str, Any]:
    minutes = 30
    time_budget = _text(getattr(profile, "diet_restrictions", ""))
    for token in time_budget.replace("；", " ").split():
        digits = "".join(ch for ch in token if ch.isdigit())
        if digits:
            minutes = max(10, min(int(digits), 180))
            break
    return {
        "stage_id": "L0",
        "learner_group": learner_group or _text(getattr(profile, "constitution", ""), "未选择用户群体"),
        "daily_available_minutes": minutes,
        "preferred_time_slot": "未填写",
        "resource_preference": _listify(_text(getattr(profile, "exercise_preferences", ""))),
        "preferred_difficulty": "D2",
        "default_daily_tasks": _minutes_to_tasks(minutes),
    }


def get_onboarding_status(db: Session, user_id: int) -> dict[str, Any]:
    payload = _latest_activity_payload(db, user_id, ONBOARDING_ACTIVITY_TYPE)
    if payload:
        return {
            "status": payload.get("status", "onboarding_completed"),
            "learner_group": payload.get("learner_group", "未选择用户群体"),
            "survey_answers": payload.get("survey_answers", {}),
            "field_sources": payload.get("field_sources", {}),
            "l0_baseline": payload.get("l0_baseline", {}),
            "needs_survey_popup": False,
        }

    profile = get_or_create_profile(db, user_id)
    baseline = _baseline_from_profile(user_id, _text(profile.constitution), profile)
    profile_survey = _json_dict(getattr(profile, "survey_json", None))
    dismissed = bool(profile_survey.get("onboarding_dismissed"))
    return {
        "status": "pending",
        "learner_group": baseline["learner_group"],
        "survey_answers": {},
        "field_sources": {},
        "l0_baseline": baseline,
        "needs_survey_popup": not dismissed,
    }


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _update_mastery(row: LearnerKnowledgeMastery | None, is_correct: bool, score: float) -> tuple[float, int, int, str]:
    previous_mastery = row.mastery if row else 0.6
    previous_wrong = row.wrong_count if row else 0
    previous_review = row.review_count if row else 0
    performance = _clamp(score / 100.0)
    delta = 0.12 if is_correct else -0.18
    mastery = _clamp(previous_mastery * 0.75 + performance * 0.25 + delta)
    wrong_count = previous_wrong + (0 if is_correct else 1)
    review_count = previous_review + 1
    if mastery >= 0.8 and wrong_count == 0:
        status = "strong"
    elif mastery < 0.6 or wrong_count >= 1:
        status = "weak"
    else:
        status = "developing"
    return mastery, wrong_count, review_count, status


def record_question_attempts(db: Session, user_id: int, attempts: list[dict[str, Any]]) -> dict[str, Any]:
    now = _now()
    error_patterns: dict[str, int] = {}
    for attempt in attempts:
        kp_ids = [kp_id for kp_id in _listify(attempt.get("kp_ids")) if kp_id]
        is_correct = bool(attempt.get("is_correct"))
        score = float(attempt.get("score") or (100 if is_correct else 0))
        question_id = _text(attempt.get("question_id"), str(uuid.uuid4()))
        db.add(
            QuestionAttempt(
                user_id=user_id,
                question_id=question_id,
                answer=_text(attempt.get("answer")),
                is_correct=is_correct,
                score=score,
                kp_ids_json=json.dumps(kp_ids, ensure_ascii=False),
                feedback=_text(attempt.get("feedback")),
                created_at=now,
            )
        )
        db.add(
            LearningActivityRecord(
                user_id=user_id,
                activity_type="question_attempt",
                resource_id=question_id,
                resource_type="question",
                duration_minutes=int(attempt.get("duration_minutes") or 10),
                completion_status="completed" if is_correct else "needs_review",
                score=score,
                payload_json=json.dumps(attempt, ensure_ascii=False),
                created_at=now,
            )
        )
        if not is_correct:
            error_type = _text(attempt.get("error_type"), "知识点掌握不牢")
            error_patterns[error_type] = error_patterns.get(error_type, 0) + 1
            db.add(
                MistakeRecord(
                    user_id=user_id,
                    question_id=question_id,
                    kp_ids_json=json.dumps(kp_ids, ensure_ascii=False),
                    error_type=error_type,
                    summary=_text(attempt.get("summary") or attempt.get("feedback") or error_type),
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )

        for kp_id in kp_ids:
            mastery_row = (
                db.query(LearnerKnowledgeMastery)
                .filter(LearnerKnowledgeMastery.user_id == user_id, LearnerKnowledgeMastery.kp_id == kp_id)
                .first()
            )
            mastery, wrong_count, review_count, status = _update_mastery(mastery_row, is_correct, score)
            next_review_at = now + timedelta(days=1 if status == "weak" else 3)
            if mastery_row:
                mastery_row.mastery = mastery
                mastery_row.wrong_count = wrong_count
                mastery_row.review_count = review_count
                mastery_row.mastery_status = status
                mastery_row.confidence = _clamp((mastery_row.confidence or 0.8) * 0.9 + 0.1)
                mastery_row.last_review_at = now
                mastery_row.next_review_at = next_review_at
                mastery_row.updated_at = now
            else:
                db.add(
                    LearnerKnowledgeMastery(
                        user_id=user_id,
                        kp_id=kp_id,
                        mastery=mastery,
                        confidence=0.8,
                        wrong_count=wrong_count,
                        review_count=review_count,
                        last_review_at=now,
                        next_review_at=next_review_at,
                        mastery_status=status,
                        created_at=now,
                        updated_at=now,
                    )
                )
    db.commit()
    return build_learning_profile(db, user_id)


def build_learning_profile(db: Session, user_id: int) -> dict[str, Any]:
    mastery_rows = (
        db.query(LearnerKnowledgeMastery)
        .filter(LearnerKnowledgeMastery.user_id == user_id)
        .order_by(LearnerKnowledgeMastery.updated_at.desc(), LearnerKnowledgeMastery.id.desc())
        .all()
    )
    attempt_rows = (
        db.query(QuestionAttempt)
        .filter(QuestionAttempt.user_id == user_id)
        .order_by(QuestionAttempt.created_at.desc(), QuestionAttempt.id.desc())
        .limit(20)
        .all()
    )
    mistake_rows = (
        db.query(MistakeRecord)
        .filter(MistakeRecord.user_id == user_id, MistakeRecord.status == "active")
        .order_by(MistakeRecord.updated_at.desc(), MistakeRecord.id.desc())
        .all()
    )
    mastery_by_kp = {row.kp_id: round(float(row.mastery or 0.0), 4) for row in mastery_rows if row.kp_id}
    weak_kp_ids = sorted(
        row.kp_id for row in mastery_rows if row.kp_id and ((row.mastery_status == "weak") or (float(row.mastery or 0.0) < 0.6))
    )
    strong_kp_ids = sorted(
        row.kp_id for row in mastery_rows if row.kp_id and float(row.mastery or 0.0) >= 0.8 and row.wrong_count == 0
    )
    error_patterns: dict[str, int] = {}
    for row in mistake_rows:
        key = _text(row.error_type, "知识点掌握不牢")
        error_patterns[key] = error_patterns.get(key, 0) + 1
    correct_count = sum(1 for row in attempt_rows if row.is_correct)
    question_accuracy = round(correct_count / len(attempt_rows), 4) if attempt_rows else 1.0
    review_stability = round(
        sum(1 for row in mastery_rows if float(row.mastery or 0.0) >= 0.6) / len(mastery_rows),
        4,
    ) if mastery_rows else 1.0
    if question_accuracy >= 0.8:
        case_reasoning_level = "stable"
    elif question_accuracy >= 0.55:
        case_reasoning_level = "developing"
    else:
        case_reasoning_level = "emerging"

    profile = get_or_create_profile(db, user_id)
    preferred_difficulty = get_onboarding_status(db, user_id)["l0_baseline"].get("preferred_difficulty") or _difficulty_from_foundation(
        _text(profile.medical_history),
        _text(profile.constitution),
    )
    return {
        "mastery_by_kp": mastery_by_kp,
        "weak_kp_ids": weak_kp_ids,
        "strong_kp_ids": strong_kp_ids,
        "error_patterns": error_patterns,
        "case_reasoning_level": case_reasoning_level,
        "question_accuracy": question_accuracy,
        "review_stability": review_stability,
        "preferred_difficulty": preferred_difficulty or "D2",
    }


def _recent_activities(db: Session, user_id: int, start: datetime, end: datetime) -> list[LearningActivityRecord]:
    return (
        db.query(LearningActivityRecord)
        .filter(
            LearningActivityRecord.user_id == user_id,
            LearningActivityRecord.created_at >= start,
            LearningActivityRecord.created_at < end,
            LearningActivityRecord.activity_type.in_(["question_attempt", "plan_generation"]),
        )
        .all()
    )


def build_l3_behavior_window(db: Session, user_id: int) -> dict[str, Any]:
    end = _now() + timedelta(seconds=1)
    start = end - timedelta(days=7)
    previous_start = start - timedelta(days=7)
    last_week = _recent_activities(db, user_id, start, end)
    previous_week = _recent_activities(db, user_id, previous_start, start)

    last_count = len(last_week)
    prev_count = len(previous_week)
    completion_rate = round(
        sum(1 for row in last_week if row.completion_status == "completed") / last_count,
        4,
    ) if last_count else 1.0
    last_focus = sum(row.duration_minutes or 0 for row in last_week)
    prev_focus = sum(row.duration_minutes or 0 for row in previous_week)
    login_change = round((last_count - prev_count) / prev_count, 4) if prev_count else (0.0 if last_count else -1.0)
    focus_change = round((last_focus - prev_focus) / prev_focus, 4) if prev_focus else (0.0 if last_focus else -1.0)

    attempts = (
        db.query(QuestionAttempt)
        .filter(QuestionAttempt.user_id == user_id, QuestionAttempt.created_at >= start)
        .all()
    )
    counts_by_question: dict[str, int] = {}
    for row in attempts:
        counts_by_question[row.question_id] = counts_by_question.get(row.question_id, 0) + 1
    retry_count = sum(max(0, count - 1) for count in counts_by_question.values())

    return {
        "task_completion_rate": completion_rate,
        "login_weekly_change": login_change,
        "focus_time_change": focus_change,
        "retry_count": retry_count,
        "path_deviation": 0.0,
    }


def _mistake_payloads(db: Session, user_id: int) -> list[dict[str, Any]]:
    rows = (
        db.query(MistakeRecord)
        .filter(MistakeRecord.user_id == user_id, MistakeRecord.status == "active")
        .order_by(MistakeRecord.updated_at.desc(), MistakeRecord.id.desc())
        .all()
    )
    payloads = []
    for row in rows:
        kp_ids = []
        try:
            raw = json.loads(row.kp_ids_json or "[]")
            if isinstance(raw, list):
                kp_ids = [_text(item) for item in raw if _text(item)]
        except json.JSONDecodeError:
            kp_ids = []
        payloads.append({
            "kp_ids": kp_ids,
            "error_type": _text(row.error_type),
            "summary": _text(row.summary),
        })
    return payloads


def generate_diagnosis_report(
    *,
    learner_context: dict[str, Any] | LearnerContextBrief,
    l0_baseline: dict[str, Any],
    l3_behavior: dict[str, Any],
    learning_profile: dict[str, Any],
    mistakes: list[dict[str, Any]],
) -> DiagnosisReport:
    if isinstance(learner_context, LearnerContextBrief):
        context_payload = learner_context.model_dump()
    else:
        context_payload = dict(learner_context)

    diagnosis_payload = diagnose_learning_state(
        l0_baseline=l0_baseline,
        l3_behavior=l3_behavior,
        mistakes=mistakes,
    )
    stage = diagnosis_payload["t_stage"]
    weak_count = len(learning_profile.get("weak_kp_ids", []))
    error_patterns = learning_profile.get("error_patterns", {})
    primary_error = next(iter(error_patterns.keys()), "暂无明显错因")
    interventions = [stage.get("suggested_action", "keep_current_plan")]
    if weak_count:
        interventions.append(f"优先复盘 {weak_count} 个薄弱知识点")
    summary = (
        f"当前处于{stage['stage_name']}，共识别 {weak_count} 个薄弱知识点，"
        f"主要错因是{primary_error}。"
    )
    return DiagnosisReport(
        diagnosis_id=f"diag-{uuid.uuid4().hex[:10]}",
        stage_id=stage["stage_id"],
        stage_name=stage["stage_name"],
        summary=summary,
        source_scope="diagnosis_agent",
        source_id=_text(context_payload.get("learner_id"), "unknown-learner"),
        kp_ids=list(learning_profile.get("weak_kp_ids", [])),
        risk_notes=[],
        confidence=0.86,
        interventions=interventions,
        t_stage=stage,
        l0_baseline=l0_baseline,
        l3_window=l3_behavior,
        attribution=diagnosis_payload["attribution"],
    )


def build_diagnosis_snapshot(db: Session, user_id: int, persist: bool = False) -> DiagnosisReport:
    onboarding_status = get_onboarding_status(db, user_id)
    learner_context = build_learner_context_brief(db, user_id)
    learning_profile = build_learning_profile(db, user_id)
    l3_behavior = build_l3_behavior_window(db, user_id)
    mistakes = _mistake_payloads(db, user_id)
    report = generate_diagnosis_report(
        learner_context=learner_context,
        l0_baseline=onboarding_status["l0_baseline"],
        l3_behavior=l3_behavior,
        learning_profile=learning_profile,
        mistakes=mistakes,
    )
    if persist:
        db.add(
            LearningActivityRecord(
                user_id=user_id,
                activity_type=DIAGNOSIS_ACTIVITY_TYPE,
                resource_id=report.diagnosis_id or f"diagnosis:{user_id}",
                resource_type="diagnosis_report",
                duration_minutes=0,
                completion_status="completed",
                score=round(float(report.confidence or 0.0) * 100, 2),
                payload_json=json.dumps(report.model_dump(), ensure_ascii=False),
                created_at=_now(),
            )
        )
        db.commit()
    return report


def _legacy_report_payload(db: Session, user_id: int, diagnosis: DiagnosisReport, learning_profile: dict[str, Any]) -> dict[str, Any]:
    profile = get_or_create_profile(db, user_id)
    learner_group = _text(profile.constitution, "普通学习者")
    goal = _text(profile.health_goals, "未填写")
    focus = learning_profile.get("weak_kp_ids", [goal])[0] if learning_profile.get("weak_kp_ids") else goal
    weak_points = [
        {
            "title": kp_id,
            "evidence": f"掌握度 {mastery:.0%}",
        }
        for kp_id, mastery in list(learning_profile.get("mastery_by_kp", {}).items())[:5]
        if kp_id in learning_profile.get("weak_kp_ids", [])
    ]
    if not weak_points and focus:
        weak_points.append({"title": "待观察薄弱点", "evidence": str(focus)})

    mastery_values = learning_profile.get("mastery_by_kp", {})
    base_mastery = 0.72 if not mastery_values else sum(mastery_values.values()) / max(len(mastery_values), 1)
    mastery_radar = [
        {"name": "中医基础", "value": round(max(0.35, base_mastery), 2)},
        {"name": "中医诊断", "value": round(max(0.35, base_mastery - 0.06), 2)},
        {"name": "方剂学", "value": round(max(0.35, base_mastery - 0.1), 2)},
        {"name": "辨证推理", "value": round(max(0.35, base_mastery - 0.08), 2)},
    ]
    next_actions = [
        "完成今日任务卡中的短练与复盘",
        "优先处理薄弱点 Top1 对应的知识卡",
        "答错后查看解析并完成 1-2 道变式题",
    ]
    return {
        "learner_overview": {
            "learner_group": learner_group,
            "goal": goal,
            "current_focus": str(focus or goal),
        },
        "mastery_radar": mastery_radar,
        "weak_points": weak_points,
        "mistake_summary": {
            "total_mistakes": sum(learning_profile.get("error_patterns", {}).values()),
            "top_error_type": next(iter(learning_profile.get("error_patterns", {})), "暂无明显错因"),
        },
        "resource_match": {
            "difficulty_match": 0.9,
            "recommended_difficulty": learning_profile.get("preferred_difficulty", diagnosis.l0_baseline.get("preferred_difficulty") if diagnosis.l0_baseline else "D2"),
            "reason": "根据学习者群体、近期掌握度与诊断阶段映射推荐难度。",
        },
        "t_stage": diagnosis.t_stage or {"stage_id": diagnosis.stage_id, "stage_name": diagnosis.stage_name, "evidence": []},
        "next_actions": next_actions,
    }


# Backward compatible report payload for ReportsPage while exposing task 9 diagnosis data.
def build_report_summary(db: Session, user_id: int) -> dict[str, Any]:
    onboarding_status = get_onboarding_status(db, user_id)
    learning_profile = build_learning_profile(db, user_id)
    diagnosis = build_diagnosis_snapshot(db, user_id, persist=False)
    legacy = _legacy_report_payload(db, user_id, diagnosis, learning_profile)
    recent_events = (
        db.query(AgentEvent)
        .filter(AgentEvent.user_id == user_id)
        .order_by(AgentEvent.created_at.desc(), AgentEvent.id.desc())
        .limit(10)
        .all()
    )
    return {
        **legacy,
        "onboarding_status": onboarding_status,
        "learning_profile": learning_profile,
        "diagnosis": diagnosis.model_dump(),
        "agent_trace": [
            {
                "agent_name": row.agent_name,
                "event_type": row.event_type,
                "output_summary": row.output_summary,
            }
            for row in recent_events
        ],
    }

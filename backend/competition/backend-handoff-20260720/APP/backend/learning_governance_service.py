from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from APP.backend import diagnosis_agent_service, system_data_service
from APP.backend.database import (
    KnowledgeCardRecord,
    KnowledgeMasteryState,
    KnowledgePoint,
    LearnerKPReviewState,
    LearnerKnowledgeMastery,
    LearningInterventionLifecycle,
    LearningInterventionRecord,
    LearningQuestionAttempt,
    LearningTask,
    MistakeRecord,
    NotificationPreference,
    NotificationRecord,
    PlanReviewRecord,
    QuestionBankItem,
    TeachingResource,
    UserProfile,
)
from APP.backend.time_utils import utc_now


SCHEMA_VERSION = "1.0"
METHODOLOGY_VERSION = "learning-monitoring-v2"
REFERENCE_LINKS = [
    {
        "reference_id": "caliper-1edtech-1.2",
        "title": "1EdTech Caliper Analytics 1.2",
        "url": "https://www.imsglobal.org/spec/caliper/v1p2/",
        "applies_to": ["learning_event_provenance", "assessment_and_resource_events"],
        "note": "用于学习事件语义与来源追踪；不规定本系统的指标权重。",
    },
    {
        "reference_id": "bkt-properties-2013",
        "title": "Properties of the Bayesian Knowledge Tracing Model",
        "url": "https://jedm.educationaldatamining.org/index.php/JEDM/article/view/35",
        "applies_to": ["mastery_interpretation", "attempt_based_update"],
        "note": "支持按知识组件和作答证据更新掌握状态的建模方向；当前工程公式不是 BKT。",
    },
    {
        "reference_id": "edm-knowledge-tracing-cold-start-2021",
        "title": "The Cold Start Problem and Interpretation of Knowledge Tracing Models' Predictive Performance",
        "url": "https://educationaldatamining.org/EDM2021/virtual/poster_paper126.html",
        "applies_to": ["data_sufficiency", "cold_start_warning"],
        "note": "支持少量首次作答时降低结论强度；本系统的数据覆盖阈值仍是可审计的工程策略。",
    },
    {
        "reference_id": "educational-recommender-review-2022",
        "title": "A systematic literature review on educational recommender systems",
        "url": "https://pubmed.ncbi.nlm.nih.gov/36124004/",
        "applies_to": ["resource_matching", "recommendation_evaluation"],
        "note": "支持多维资源推荐与效果验证方向；本系统权重尚需用真实反馈校准。",
    },
]
_ACTION_BY_STAGE = {
    "T1": ("降低单次任务难度", "先缩小任务范围，并用对比卡补齐关键概念。"),
    "T2": ("恢复学习节奏", "减少今日任务数量，保留一个能够完成的核心任务。"),
    "T4": ("回到当前学习主线", "优先处理当前阶段和短期计划覆盖的知识点。"),
    "T5": ("安排错题复盘", "先完成薄弱知识点的错题复盘，再增加新内容。"),
}


def _json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return fallback
    return parsed


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _percent_score_to_ratio(value: float | None) -> float:
    """Normalize the authoritative mastery contract (0..100) to the UI ratio."""

    return _clamp(float(value or 0.0) / 100.0)


def _retention_value(row: LearnerKPReviewState, now: datetime) -> tuple[float | None, str]:
    if row.last_review_at is not None and float(row.stability_seconds or 0.0) > 0:
        elapsed = max(0.0, (now - row.last_review_at).total_seconds())
        return _clamp(math.exp(-elapsed / float(row.stability_seconds))), "dynamic_exponential"
    persisted = row.retention_estimate
    if persisted is not None and float(persisted) > 0:
        return _clamp(float(persisted)), "persisted_legacy_estimate"
    return None, "insufficient_review_evidence"


def _dimension(
    key: str,
    label: str,
    value: float,
    *,
    source_ids: list[str],
    formula: str,
    evidence_count: int,
    window_days: int | None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": round(_clamp(value), 4),
        "source_ids": source_ids,
        "formula": formula,
        "evidence_count": max(0, int(evidence_count)),
        "status": "observed" if evidence_count > 0 else "insufficient_evidence",
        "window_days": window_days,
    }


def _preferred_difficulty(profile: UserProfile | None) -> tuple[float, str]:
    survey = _json(getattr(profile, "survey_json", "{}"), {}) if profile is not None else {}
    values = [
        survey.get("preferred_difficulty"),
        survey.get("difficulty_preference"),
        (survey.get("preferences") or {}).get("difficulty_preference")
        if isinstance(survey.get("preferences"), dict) else None,
    ]
    mapping = {"D1": 1.0, "D2": 2.0, "D3": 3.0, "D4": 4.0, "D5": 5.0}
    for value in values:
        if isinstance(value, (int, float)) and 1 <= float(value) <= 5:
            return float(value), "user_profile_survey"
        label = str(value or "").strip().upper()
        if label in mapping:
            return mapping[label], "user_profile_survey"
    return 2.0, "transparent_default_D2"


def _weighted_match_score(components: dict[str, float | None]) -> float:
    weights = {
        "knowledge_fit": 0.40,
        "quality": 0.20,
        "format_fit": 0.15,
        "time_fit": 0.15,
        "difficulty_fit": 0.10,
    }
    available = [(weights[key], value) for key, value in components.items() if value is not None]
    denominator = sum(weight for weight, _value in available)
    if denominator <= 0:
        return 0.0
    return _clamp(sum(weight * float(value) for weight, value in available) / denominator)


def _metric_value(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key, {}).get("value") if isinstance(payload.get(key), dict) else None
    return float(value) if isinstance(value, (int, float)) else 0.0


def _kp_names(db: Session, kp_ids: set[str]) -> dict[str, str]:
    if not kp_ids:
        return {}
    return {
        str(row.kp_id): str(row.name or row.kp_id)
        for row in db.query(KnowledgePoint).filter(KnowledgePoint.kp_id.in_(kp_ids)).all()
    }


def _mastery_rows(db: Session, user_id: int) -> list[dict[str, Any]]:
    authoritative = (
        db.query(KnowledgeMasteryState)
        .filter(KnowledgeMasteryState.learner_id == user_id)
        .order_by(KnowledgeMasteryState.updated_at.desc())
        .all()
    )
    if authoritative:
        return [
            {
                "kp_id": str(row.kp_id),
                "score": _percent_score_to_ratio(row.mastery_score),
                "score_raw": float(row.mastery_score or 0.0),
                "score_unit": "percent_0_100",
                "confidence": _clamp(row.mastery_confidence or 0.0),
                "attempt_count": int(row.attempt_count or 0),
                "updated_at": _iso(row.updated_at),
            }
            for row in authoritative
        ]
    legacy = (
        db.query(LearnerKnowledgeMastery)
        .filter(LearnerKnowledgeMastery.user_id == user_id)
        .order_by(LearnerKnowledgeMastery.updated_at.desc())
        .all()
    )
    return [
        {
            "kp_id": str(row.kp_id),
            "score": _clamp(row.mastery or 0.0),
            "score_raw": float(row.mastery or 0.0),
            "score_unit": "ratio_0_1",
            "confidence": _clamp(row.confidence or 0.0),
            "attempt_count": int((row.wrong_count or 0) + (row.review_count or 0)),
            "updated_at": _iso(row.updated_at),
        }
        for row in legacy
    ]


def build_learning_insights(db: Session, user_id: int, *, days: int = 30) -> dict[str, Any]:
    if days not in {7, 30, 90}:
        raise ValueError("days must be one of: 7, 30, 90")
    now = utc_now()
    window_start = now - timedelta(days=days)
    window_metrics = system_data_service.build_learning_window_metrics(
        db, user_id=user_id, days=days, now=now
    )
    trends = system_data_service.build_learning_trends(db, user_id=user_id, days=days, now=now)
    mastery = _mastery_rows(db, user_id)
    kp_ids = {item["kp_id"] for item in mastery}
    names = _kp_names(db, kp_ids)
    review_rows = (
        db.query(LearnerKPReviewState)
        .filter(LearnerKPReviewState.learner_id == user_id)
        .all()
    )
    review_by_kp = {str(row.kp_id): row for row in review_rows}
    retention_by_kp = {
        str(row.kp_id): _retention_value(row, now)
        for row in review_rows
    }
    mastery_heatmap = [
        {
            **item,
            "kp_name": names.get(item["kp_id"], item["kp_id"]),
            "retention": retention_by_kp[item["kp_id"]][0]
            if item["kp_id"] in retention_by_kp else None,
            "retention_source": retention_by_kp[item["kp_id"]][1]
            if item["kp_id"] in retention_by_kp else "no_review_state",
            "next_review_at": _iso(review_by_kp[item["kp_id"]].next_review_at)
            if item["kp_id"] in review_by_kp else None,
        }
        for item in sorted(mastery, key=lambda value: (value["score"], -value["attempt_count"]))
    ]
    mistakes = db.query(MistakeRecord).filter(
        MistakeRecord.user_id == user_id,
        MistakeRecord.created_at >= window_start,
        MistakeRecord.created_at <= now,
    ).all()
    mistake_counts = Counter(str(row.error_type or "待调研错因") for row in mistakes)
    attempts = db.query(LearningQuestionAttempt).filter(
        LearningQuestionAttempt.user_id == user_id,
        LearningQuestionAttempt.answered_at >= window_start,
        LearningQuestionAttempt.answered_at <= now,
    ).all()
    accuracy = sum(bool(row.is_correct) for row in attempts) / len(attempts) if attempts else 0.0
    average_mastery = sum(item["score"] for item in mastery) / len(mastery) if mastery else 0.0
    retention_values = [
        value for value, _source in retention_by_kp.values()
        if value is not None
    ]
    retention = sum(retention_values) / len(retention_values) if retention_values else 0.0
    completion = _metric_value(window_metrics, "task_completion_rate")
    resource_engagement = _metric_value(window_metrics, "resource_click_rate")
    login_days = sum(int(item.get("login_days") or 0) for item in trends.get("series", []))
    consistency = login_days / days
    report = diagnosis_agent_service.build_diagnosis_snapshot(db, user_id, persist=False)
    report_payload = report.model_dump(mode="json")
    stage_id = str(report_payload.get("stage_id") or "T0")
    stage_name = str(report_payload.get("stage_name") or "稳定学习")
    counts = window_metrics.get("counts", {})
    task_count = int(counts.get("tasks") or 0)
    focus_session_count = int(counts.get("focus_sessions") or 0)
    sample_count = len(attempts) + task_count + focus_session_count + login_days
    evidence_coverage = _clamp(
        min(1.0, len(attempts) / 5) * 0.5
        + min(1.0, (login_days + focus_session_count) / 4) * 0.3
        + min(1.0, len(mastery) / 3) * 0.2
    )
    sufficient_for_intervention = (
        evidence_coverage >= 0.6
        and len(attempts) >= 3
        and len(mastery) >= 1
    )
    due_count = sum(
        row.status == "active" and row.next_review_at is not None and row.next_review_at <= now
        for row in review_rows
    )
    weak_points = [
        {
            "kp_id": item["kp_id"],
            "kp_name": item["kp_name"],
            "mastery_score": item["score"],
            "confidence": item["confidence"],
            "reason": "当前掌握度较低，建议优先补强。",
        }
        for item in mastery_heatmap[:5]
        if item["score"] < 0.7
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(now),
        "window": window_metrics["window"],
        "overview": {
            "stage_id": stage_id,
            "stage_name": stage_name,
            "summary": report_payload.get("summary") or "学习数据正在持续积累。",
            "confidence": evidence_coverage,
            "confidence_interpretation": "data_coverage_score_not_statistical_confidence",
            "due_review_count": due_count,
        },
        "dimensions": [
            _dimension("mastery", "知识掌握", average_mastery,
                       source_ids=["knowledge_mastery_states"],
                       formula="mean(normalized_current_mastery_by_kp)",
                       evidence_count=len(mastery), window_days=None),
            _dimension("retention", "复习保持", retention,
                       source_ids=["learner_kp_review_states"],
                       formula="mean(exp(-elapsed_seconds/stability_seconds))",
                       evidence_count=len(retention_values), window_days=None),
            _dimension("execution", "任务执行", completion,
                       source_ids=["learning_tasks"],
                       formula="completed_non_cancelled_tasks/non_cancelled_tasks",
                       evidence_count=task_count, window_days=days),
            _dimension("accuracy", "练习正确", accuracy,
                       source_ids=["learning_question_attempts"],
                       formula="correct_attempts/attempts",
                       evidence_count=len(attempts), window_days=days),
            _dimension("consistency", "学习规律", consistency,
                       source_ids=["learning_activity_records"],
                       formula="distinct_login_or_checkin_days/window_days",
                       evidence_count=login_days, window_days=days),
            _dimension("engagement", "资源使用", resource_engagement,
                       source_ids=["learning_activity_records"],
                       formula="clicked_displayed_recommendations/displayed_recommendations",
                       evidence_count=int(counts.get("recommendation_views") or 0), window_days=days),
        ],
        "activity_trends": trends,
        "mastery_heatmap": mastery_heatmap,
        "weak_points": weak_points,
        "mistake_distribution": [
            {"error_type": name, "count": count}
            for name, count in mistake_counts.most_common()
        ],
        "data_quality": {
            "confidence": evidence_coverage,
            "confidence_interpretation": "data_coverage_score_not_statistical_confidence",
            "formula": "0.5*min(attempts/5,1)+0.3*min((login_days+focus_sessions)/4,1)+0.2*min(mastery_points/3,1)",
            "sample_count": sample_count,
            "attempt_count": len(attempts),
            "mastery_point_count": len(mastery),
            "login_days": login_days,
            "task_count": task_count,
            "focus_session_count": focus_session_count,
            "sources": [
                "learning_activity_records",
                "learning_tasks",
                "learning_focus_sessions",
                "learning_question_attempts",
                "knowledge_mastery_states",
                "learner_kp_review_states",
                "mistake_records",
            ],
            "is_sufficient_for_intervention": sufficient_for_intervention,
            "intervention_gate": "coverage>=0.6 and attempts>=3 and mastery_points>=1",
        },
        "data_sources": [
            {"source_id": "learning_activity_records", "table": "learning_activity_records", "events": ["login", "daily_checkin", "dashboard_recommendations_view", "resource_click"], "time_field": "created_at", "window_days": days},
            {"source_id": "learning_tasks", "table": "learning_task", "fields": ["status", "created_at", "completed_at", "kp_ids_json"], "time_field": "created_at", "window_days": days},
            {"source_id": "learning_focus_sessions", "table": "learning_focus_sessions", "fields": ["active_seconds", "status", "started_at", "ended_at"], "time_field": "started_at", "window_days": days},
            {"source_id": "learning_question_attempts", "table": "question_attempt", "fields": ["is_correct", "score", "response_time_seconds", "answered_at"], "time_field": "answered_at", "window_days": days},
            {"source_id": "knowledge_mastery_states", "table": "knowledge_mastery_states", "fields": ["mastery_score", "mastery_confidence", "attempt_count", "calculation_version"], "unit": "percent_0_100", "window_days": None},
            {"source_id": "learner_kp_review_states", "table": "learner_kp_review_states", "fields": ["last_review_at", "stability_seconds", "next_review_at", "formula_version"], "window_days": None},
            {"source_id": "mistake_records", "table": "mistake_records", "fields": ["error_type", "kp_ids_json", "created_at"], "time_field": "created_at", "window_days": days},
        ],
        "methodology": {
            "version": METHODOLOGY_VERSION,
            "status": "engineering_metrics_with_explicit_provenance",
            "limitations": [
                "掌握度是当前状态估计，不是标准化考试成绩。",
                "数据覆盖度不是统计置信区间。",
                "冷启动阶段只展示观察结果，不自动触发干预。",
            ],
            "references": REFERENCE_LINKS,
        },
    }


def build_resource_match_report(
    db: Session,
    user_id: int,
    *,
    insights: dict[str, Any] | None = None,
    plan_context: dict[str, Any] | None = None,
    limit: int = 12,
) -> dict[str, Any]:
    insights = insights or build_learning_insights(db, user_id, days=30)
    plan_context = plan_context or {}
    weak = insights.get("weak_points") or []
    target_kps = [str(item.get("kp_id")) for item in weak if str(item.get("kp_id") or "").strip()]
    task = plan_context.get("learning_task") if isinstance(plan_context, dict) else {}
    if isinstance(task, dict):
        target_kps.extend(str(item) for item in task.get("kp_ids", []) if str(item).strip())
    target_kps = list(dict.fromkeys(target_kps))
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).one_or_none()
    preferences = " ".join(
        str(value or "") for value in (
            getattr(profile, "exercise_preferences", ""),
            getattr(profile, "custom_needs", ""),
        )
    ).lower()
    target_difficulty, target_difficulty_basis = _preferred_difficulty(profile)
    response_rows = db.query(LearningQuestionAttempt).filter(
        LearningQuestionAttempt.user_id == user_id,
        LearningQuestionAttempt.answered_at >= utc_now() - timedelta(days=30),
    ).all()
    response_seconds: dict[str, list[int]] = {}
    for attempt in response_rows:
        if attempt.response_time_seconds is not None and attempt.response_time_seconds > 0:
            response_seconds.setdefault(str(attempt.question_id), []).append(int(attempt.response_time_seconds))
    candidates: list[dict[str, Any]] = []
    cards = (
        db.query(KnowledgeCardRecord)
        .filter(KnowledgeCardRecord.user_id == user_id)
        .order_by(KnowledgeCardRecord.updated_at.desc())
        .limit(100)
        .all()
    )
    for row in cards:
        bundle = _json(row.resource_bundle_json, {})
        bundle_quality = bundle.get("quality_score") if isinstance(bundle, dict) else None
        bundle_minutes = bundle.get("estimated_minutes") if isinstance(bundle, dict) else None
        candidates.append({
            "resource_id": row.card_id,
            "resource_type": "knowledge_card",
            "title": row.title,
            "kp_ids": [row.kp_id],
            "quality": _clamp(bundle_quality) if isinstance(bundle_quality, (int, float)) else 0.5,
            "quality_basis": "knowledge_card_bundle" if isinstance(bundle_quality, (int, float)) else "neutral_default_no_quality_evidence",
            "estimated_minutes": max(1, int(bundle_minutes)) if isinstance(bundle_minutes, (int, float)) else 12,
            "estimated_minutes_basis": "knowledge_card_bundle" if isinstance(bundle_minutes, (int, float)) else "content_type_default",
            "difficulty": None,
            "source": "user_knowledge_card",
            "action": {"type": "navigate", "page": "knowledge", "params": {"kp_id": row.kp_id}},
        })
    resources = db.query(TeachingResource).filter(TeachingResource.status == "active").limit(200).all()
    for row in resources:
        candidates.append({
            "resource_id": row.resource_id,
            "resource_type": row.resource_type,
            "title": row.title,
            "kp_ids": _json(row.kp_ids_json, []),
            "quality": _clamp(row.quality_score or 0.7),
            "quality_basis": "teaching_resources.quality_score",
            "estimated_minutes": 15 if row.resource_type == "video" else 10,
            "estimated_minutes_basis": "content_type_default",
            "difficulty": None,
            "source": row.source or "unknown",
            "action": {"type": "open_resource", "resource_id": row.resource_id},
        })
    questions = db.query(QuestionBankItem).filter(QuestionBankItem.status == "active").limit(100).all()
    for row in questions:
        observed_times = response_seconds.get(str(row.question_id), [])
        observed_minutes = max(1, math.ceil(sum(observed_times) / len(observed_times) / 60)) if observed_times else 5
        candidates.append({
            "resource_id": row.question_id,
            "resource_type": "question",
            "title": str(row.stem or "练习题")[:80],
            "kp_ids": _json(row.kp_ids_json, []),
            "quality": _clamp(row.quality_score or 0.7),
            "quality_basis": "question_bank_items.quality_score",
            "estimated_minutes": observed_minutes,
            "estimated_minutes_basis": "user_response_time_mean_30d" if observed_times else "question_type_default",
            "difficulty": float(row.difficulty) if row.difficulty is not None else None,
            "source": row.source or "unknown",
            "action": {"type": "navigate", "page": "workshop", "params": {"question_id": row.question_id}},
        })
    available_minutes = int(task.get("estimated_minutes") or 30) if isinstance(task, dict) else 30
    preferred_types = {
        kind for kind, aliases in {
            "video": ("video", "视频"),
            "question": ("question", "题"),
            "knowledge_card": ("card", "卡片", "knowledge_card"),
        }.items() if any(alias in preferences for alias in aliases)
    }
    matches = []
    target_set = set(target_kps)
    for candidate in candidates:
        candidate_kps = {str(item) for item in candidate["kp_ids"] if str(item).strip()}
        coverage = len(candidate_kps & target_set) / len(target_set) if target_set else 0.0
        format_fit = 1.0 if not preferred_types or candidate["resource_type"] in preferred_types else 0.45
        time_fit = 1.0 if candidate["estimated_minutes"] <= available_minutes else max(0.2, available_minutes / candidate["estimated_minutes"])
        candidate_difficulty = candidate.get("difficulty")
        difficulty_fit = (
            _clamp(1.0 - abs(float(candidate_difficulty) - target_difficulty) / 4.0)
            if isinstance(candidate_difficulty, (int, float)) else None
        )
        components: dict[str, float | None] = {
            "knowledge_fit": coverage,
            "quality": candidate["quality"],
            "format_fit": format_fit,
            "time_fit": time_fit,
            "difficulty_fit": difficulty_fit,
        }
        total = _weighted_match_score(components) if target_set else 0.0
        reasons = []
        if coverage > 0:
            reasons.append("覆盖当前薄弱或计划知识点")
        if format_fit == 1.0 and preferred_types:
            reasons.append("符合已确认的资源偏好")
        if time_fit == 1.0:
            reasons.append("可在当前任务时间内完成")
        if difficulty_fit is not None and difficulty_fit >= 0.75:
            reasons.append("难度接近已确认偏好")
        matches.append({
            **candidate,
            "score": round(total, 4),
            "components": {
                "knowledge_fit": round(coverage, 4),
                "quality": round(candidate["quality"], 4),
                "format_fit": round(format_fit, 4),
                "time_fit": round(time_fit, 4),
                "difficulty_fit": round(difficulty_fit, 4) if difficulty_fit is not None else None,
            },
            "component_sources": {
                "knowledge_fit": "resource.kp_ids intersect target.kp_ids",
                "quality": candidate["quality_basis"],
                "format_fit": "user_profiles.exercise_preferences/custom_needs",
                "time_fit": candidate["estimated_minutes_basis"],
                "difficulty_fit": "question_bank_items.difficulty vs user_profile_survey" if difficulty_fit is not None else "not_available_excluded_from_weighting",
            },
            "reasons": reasons or ["作为补充资源使用"],
        })
    matches.sort(key=lambda item: (-item["score"], item["estimated_minutes"], item["title"]))
    selected = [
        item for item in matches if item["components"]["knowledge_fit"] > 0
    ][: max(1, min(limit, 30))] if target_set else []
    covered_kps = {
        str(kp_id)
        for item in selected
        for kp_id in item.get("kp_ids", [])
        if str(kp_id) in target_set
    }
    aggregate_coverage = len(covered_kps) / len(target_set) if target_set else 0.0
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": insights.get("generated_at"),
        "target": {
            "kp_ids": target_kps,
            "available_minutes": available_minutes,
            "preferred_resource_types": sorted(preferred_types),
            "preferred_difficulty": target_difficulty,
            "preferred_difficulty_basis": target_difficulty_basis,
        },
        "summary": {
            "candidate_count": len(candidates),
            "recommended_count": len(selected),
            "coverage": round(aggregate_coverage, 4),
            "data_confidence": insights.get("data_quality", {}).get("confidence", 0.0),
            "evaluation_status": "not_yet_calibrated_with_learning_outcomes",
        },
        "matches": selected,
        "no_match_reason": (
            "当前没有薄弱知识点或今日任务知识点，系统不会生成无依据推荐。"
            if not target_set else "当前没有可验证的匹配资源。"
        ) if not selected else "",
        "data_sources": [
            {"source_id": "recommendation_target", "sources": ["learning_insights.weak_points", "current_learning_task.kp_ids"]},
            {"source_id": "resource_candidates", "tables": ["knowledge_card_records", "teaching_resources", "question_bank_items"]},
            {"source_id": "learner_preferences", "table": "user_profiles", "fields": ["exercise_preferences", "custom_needs", "survey_json"]},
            {"source_id": "observed_question_time", "table": "question_attempt", "fields": ["question_id", "response_time_seconds", "answered_at"], "window_days": 30},
        ],
        "methodology": {
            "version": METHODOLOGY_VERSION,
            "formula": "weighted mean of available components: knowledge .40, quality .20, format .15, time .15, difficulty .10",
            "missing_feature_policy": "exclude_missing_component_and_renormalize_weights",
            "limitations": [
                "当前权重是公开的工程基线，尚未通过真实学习增益校准。",
                "没有目标知识点时不生成推荐。",
                "资源缺少难度时不会伪造难度分。",
            ],
            "recommended_validation_metrics": ["Precision@K", "Recall@K", "NDCG@K", "task_completion_rate", "post_test_learning_gain"],
            "references": [REFERENCE_LINKS[-1]],
        },
    }


def get_notification_preferences(db: Session, user_id: int) -> NotificationPreference:
    row = db.query(NotificationPreference).filter(NotificationPreference.user_id == user_id).one_or_none()
    if row is None:
        row = NotificationPreference(user_id=user_id)
        db.add(row)
        db.flush()
    return row


def serialize_notification_preferences(row: NotificationPreference) -> dict[str, Any]:
    return {
        "in_app_enabled": bool(row.in_app_enabled),
        "categories": {
            "review_due": bool(row.review_due_enabled),
            "intervention": bool(row.intervention_enabled),
            "plan_review": bool(row.plan_review_enabled),
        },
        "digest_frequency": row.digest_frequency,
        "quiet_hours": {"start": row.quiet_hours_start, "end": row.quiet_hours_end},
    }


def update_notification_preferences(db: Session, user_id: int, updates: dict[str, Any]) -> dict[str, Any]:
    row = get_notification_preferences(db, user_id)
    categories = updates.get("categories") if isinstance(updates.get("categories"), dict) else {}
    quiet = updates.get("quiet_hours") if isinstance(updates.get("quiet_hours"), dict) else {}
    if "in_app_enabled" in updates:
        row.in_app_enabled = bool(updates["in_app_enabled"])
    for key, attr in {
        "review_due": "review_due_enabled",
        "intervention": "intervention_enabled",
        "plan_review": "plan_review_enabled",
    }.items():
        if key in categories:
            setattr(row, attr, bool(categories[key]))
    if updates.get("digest_frequency") in {"realtime", "daily", "weekly", "paused"}:
        row.digest_frequency = updates["digest_frequency"]
    for key, attr in {"start": "quiet_hours_start", "end": "quiet_hours_end"}.items():
        value = quiet.get(key)
        if isinstance(value, str) and len(value) == 5 and value[2] == ":":
            setattr(row, attr, value)
    db.flush()
    return serialize_notification_preferences(row)


def create_notification(
    db: Session,
    user_id: int,
    *,
    category: str,
    title: str,
    message: str,
    dedupe_key: str,
    severity: str = "info",
    source_type: str = "system",
    source_id: str = "",
    action: dict[str, Any] | None = None,
) -> NotificationRecord | None:
    preferences = get_notification_preferences(db, user_id)
    enabled = {
        "review_due": preferences.review_due_enabled,
        "intervention": preferences.intervention_enabled,
        "plan_review": preferences.plan_review_enabled,
    }.get(category, preferences.in_app_enabled)
    if not preferences.in_app_enabled or not enabled or preferences.digest_frequency == "paused":
        return None
    existing = db.query(NotificationRecord).filter_by(user_id=user_id, dedupe_key=dedupe_key).one_or_none()
    if existing is not None:
        return existing
    row = NotificationRecord(
        notification_id=f"NOTIF_{uuid4().hex}",
        user_id=user_id,
        category=category,
        severity=severity,
        title=title,
        message=message,
        status="unread",
        source_type=source_type,
        source_id=source_id,
        dedupe_key=dedupe_key,
        action_json=json.dumps(action or {}, ensure_ascii=False),
        scheduled_at=utc_now(),
        delivered_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def serialize_notification(row: NotificationRecord) -> dict[str, Any]:
    return {
        "notification_id": row.notification_id,
        "category": row.category,
        "severity": row.severity,
        "title": row.title,
        "message": row.message,
        "status": row.status,
        "source": {"type": row.source_type, "id": row.source_id},
        "action": _json(row.action_json, {}),
        "created_at": _iso(row.created_at),
        "delivered_at": _iso(row.delivered_at),
        "read_at": _iso(row.read_at),
    }


def list_notifications(db: Session, user_id: int, *, status: str = "all", limit: int = 50) -> dict[str, Any]:
    query = db.query(NotificationRecord).filter(NotificationRecord.user_id == user_id)
    if status != "all":
        query = query.filter(NotificationRecord.status == status)
    rows = query.order_by(NotificationRecord.created_at.desc(), NotificationRecord.id.desc()).limit(limit).all()
    unread = db.query(NotificationRecord).filter_by(user_id=user_id, status="unread").count()
    return {"schema_version": SCHEMA_VERSION, "unread_count": unread, "items": [serialize_notification(row) for row in rows]}


def update_notification_status(db: Session, user_id: int, notification_id: str, status: str) -> dict[str, Any]:
    if status not in {"read", "dismissed"}:
        raise ValueError("notification status must be read or dismissed")
    row = db.query(NotificationRecord).filter_by(user_id=user_id, notification_id=notification_id).one_or_none()
    if row is None:
        raise LookupError("notification not found")
    row.status = status
    if status == "read":
        row.read_at = utc_now()
    db.flush()
    return serialize_notification(row)


def evaluate_intervention(db: Session, user_id: int, insights: dict[str, Any]) -> dict[str, Any] | None:
    overview = insights.get("overview") or {}
    stage_id = str(overview.get("stage_id") or "T0")
    if stage_id == "T0" or not insights.get("data_quality", {}).get("is_sufficient_for_intervention"):
        return None
    now = utc_now()
    recent = (
        db.query(LearningInterventionLifecycle)
        .filter(
            LearningInterventionLifecycle.user_id == user_id,
            LearningInterventionLifecycle.created_at >= now - timedelta(hours=24),
        )
        .order_by(LearningInterventionLifecycle.created_at.desc())
        .first()
    )
    if recent is not None:
        return serialize_intervention(db, recent)
    action, message = _ACTION_BY_STAGE.get(stage_id, ("保持当前计划", "继续按当前节奏学习并积累数据。"))
    period = now.date().isoformat()
    intervention_key = hashlib.sha1(f"{user_id}:{stage_id}:{period}".encode()).hexdigest()[:24]
    legacy = LearningInterventionRecord(
        user_id=user_id,
        t_stage=stage_id,
        action=action,
        reason=f"系统根据近期学习监控判断当前处于“{overview.get('stage_name') or stage_id}”。{message}",
        cooldown_hours=24,
        effect_status="pending",
    )
    db.add(legacy)
    db.flush()
    lifecycle = LearningInterventionLifecycle(
        intervention_record_id=legacy.id,
        user_id=user_id,
        intervention_key=intervention_key,
        status="delivered",
        trigger_snapshot_json=json.dumps({"overview": overview, "data_quality": insights.get("data_quality", {})}, ensure_ascii=False),
        baseline_json=json.dumps({item["key"]: item["value"] for item in insights.get("dimensions", [])}, ensure_ascii=False),
        delivered_at=now,
        evaluate_after=now + timedelta(hours=72),
    )
    db.add(lifecycle)
    db.flush()
    create_notification(
        db,
        user_id,
        category="intervention",
        title="学习节奏调整建议",
        message=legacy.reason,
        dedupe_key=f"intervention:{intervention_key}",
        severity="warning",
        source_type="learning_intervention",
        source_id=str(legacy.id),
        action={"type": "open_intervention", "intervention_id": legacy.id},
    )
    return serialize_intervention(db, lifecycle)


def evaluate_due_interventions(
    db: Session,
    user_id: int,
    insights: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compare 72-hour outcomes with the evidence snapshot captured at delivery."""

    now = utc_now()
    rows = (
        db.query(LearningInterventionLifecycle)
        .filter(
            LearningInterventionLifecycle.user_id == user_id,
            LearningInterventionLifecycle.evaluate_after.is_not(None),
            LearningInterventionLifecycle.evaluate_after <= now,
            LearningInterventionLifecycle.evaluated_at.is_(None),
        )
        .all()
    )
    current = {
        item["key"]: float(item["value"])
        for item in insights.get("dimensions", [])
    }
    evaluated = []
    for lifecycle in rows:
        baseline = _json(lifecycle.baseline_json, {})
        deltas = {
            key: round(current.get(key, 0.0) - float(value or 0.0), 4)
            for key, value in baseline.items()
            if key in current
        }
        positive = max(
            deltas.get("execution", 0.0),
            deltas.get("mastery", 0.0),
            deltas.get("consistency", 0.0),
        ) >= 0.05
        effect = "effective" if positive else "needs_review"
        lifecycle.status = "evaluated"
        lifecycle.evaluated_at = now
        lifecycle.result_json = json.dumps(
            {"effect": effect, "dimension_deltas": deltas}, ensure_ascii=False
        )
        legacy = db.get(LearningInterventionRecord, lifecycle.intervention_record_id)
        if legacy is not None:
            legacy.effect_status = effect
        evaluated.append(serialize_intervention(db, lifecycle))
    return evaluated


def serialize_intervention(db: Session, lifecycle: LearningInterventionLifecycle) -> dict[str, Any]:
    row = db.get(LearningInterventionRecord, lifecycle.intervention_record_id)
    return {
        "intervention_id": row.id if row else lifecycle.intervention_record_id,
        "t_stage": row.t_stage if row else "",
        "action": row.action if row else "",
        "reason": row.reason if row else "",
        "cooldown_hours": row.cooldown_hours if row else 24,
        "feedback": _json(row.feedback, {}) if row else {},
        "effect_status": row.effect_status if row else "pending",
        "lifecycle_status": lifecycle.status,
        "trigger_snapshot": _json(lifecycle.trigger_snapshot_json, {}),
        "baseline": _json(lifecycle.baseline_json, {}),
        "result": _json(lifecycle.result_json, {}),
        "created_at": _iso(lifecycle.created_at),
        "evaluate_after": _iso(lifecycle.evaluate_after),
        "evaluated_at": _iso(lifecycle.evaluated_at),
    }


def list_interventions(db: Session, user_id: int, *, limit: int = 30) -> dict[str, Any]:
    rows = (
        db.query(LearningInterventionLifecycle)
        .filter(LearningInterventionLifecycle.user_id == user_id)
        .order_by(LearningInterventionLifecycle.created_at.desc())
        .limit(limit)
        .all()
    )
    return {"schema_version": SCHEMA_VERSION, "items": [serialize_intervention(db, row) for row in rows]}


def record_intervention_feedback(db: Session, user_id: int, intervention_id: int, action: str, reason: str = "") -> dict[str, Any]:
    if action not in {"accept", "postpone", "not_relevant", "too_easy", "too_hard"}:
        raise ValueError("unsupported intervention feedback")
    row = db.query(LearningInterventionRecord).filter_by(id=intervention_id, user_id=user_id).one_or_none()
    if row is None:
        raise LookupError("intervention not found")
    lifecycle = db.query(LearningInterventionLifecycle).filter_by(intervention_record_id=row.id, user_id=user_id).one_or_none()
    row.feedback = json.dumps({"action": action, "reason": reason}, ensure_ascii=False)
    row.effect_status = "user_feedback_received"
    if lifecycle is not None:
        lifecycle.status = {"accept": "accepted", "postpone": "postponed"}.get(action, "dismissed")
    db.flush()
    return serialize_intervention(db, lifecycle) if lifecycle else {"intervention_id": row.id, "effect_status": row.effect_status}


def run_plan_review(
    db: Session,
    user_id: int,
    *,
    insights: dict[str, Any],
    plan_context: dict[str, Any],
    trigger_type: str = "weekly",
) -> dict[str, Any]:
    now = utc_now()
    iso_year, iso_week, _ = now.isocalendar()
    period_key = f"{iso_year}-W{iso_week:02d}" if trigger_type == "weekly" else now.date().isoformat()
    existing = db.query(PlanReviewRecord).filter_by(user_id=user_id, trigger_type=trigger_type, period_key=period_key).one_or_none()
    if existing is not None:
        return serialize_plan_review(existing)
    dimensions = {item["key"]: float(item["value"]) for item in insights.get("dimensions", [])}
    completion = dimensions.get("execution", 0.0)
    mastery = dimensions.get("mastery", 0.0)
    due = int(insights.get("overview", {}).get("due_review_count") or 0)
    evidence = [
        f"任务完成率 {completion:.0%}",
        f"平均掌握度 {mastery:.0%}",
        f"到期复习 {due} 个知识点",
    ]
    if completion < 0.5:
        outcome = "daily_adjustment_suggested"
        summary = "近期任务完成率偏低，建议减少今日任务数量，但不改变长期路径。"
        proposal = {"target_layer": "daily_task", "operation": "reduce_load", "requires_confirmation": False}
    elif due >= 5:
        outcome = "short_replan_suggested"
        summary = "到期复习积压较多，建议在短期计划中增加复习窗口。"
        proposal = {"target_layer": "short_term", "operation": "add_review_window", "requires_confirmation": True}
    elif mastery < 0.45 and insights.get("data_quality", {}).get("sample_count", 0) >= 5:
        outcome = "short_replan_suggested"
        summary = "当前阶段知识掌握度不足，建议放慢短期计划推进速度。"
        proposal = {"target_layer": "short_term", "operation": "slow_progress", "requires_confirmation": True}
    else:
        outcome = "on_track"
        summary = "当前学习节奏与计划基本一致，继续执行现有计划。"
        proposal = {}
    refs = {
        key: value.get("plan_id") or value.get("task_id")
        for key, value in plan_context.items()
        if isinstance(value, dict) and (value.get("plan_id") or value.get("task_id"))
    }
    review = PlanReviewRecord(
        review_id=f"PLAN_REVIEW_{uuid4().hex}",
        user_id=user_id,
        trigger_type=trigger_type,
        period_key=period_key,
        status="completed" if outcome == "on_track" else "proposal_pending",
        outcome=outcome,
        summary=summary,
        evidence_json=json.dumps(evidence, ensure_ascii=False),
        proposal_json=json.dumps(proposal, ensure_ascii=False),
        plan_refs_json=json.dumps(refs, ensure_ascii=False),
        input_snapshot_json=json.dumps({"dimensions": dimensions, "data_quality": insights.get("data_quality", {})}, ensure_ascii=False),
    )
    db.add(review)
    db.flush()
    if outcome != "on_track":
        create_notification(
            db,
            user_id,
            category="plan_review",
            title="学习规划复盘建议",
            message=summary,
            dedupe_key=f"plan-review:{trigger_type}:{period_key}",
            severity="warning",
            source_type="plan_review",
            source_id=review.review_id,
            action={"type": "open_plan_review", "review_id": review.review_id},
        )
    return serialize_plan_review(review)


def serialize_plan_review(row: PlanReviewRecord) -> dict[str, Any]:
    return {
        "review_id": row.review_id,
        "trigger_type": row.trigger_type,
        "period_key": row.period_key,
        "status": row.status,
        "outcome": row.outcome,
        "summary": row.summary,
        "evidence": _json(row.evidence_json, []),
        "proposal": _json(row.proposal_json, {}),
        "plan_refs": _json(row.plan_refs_json, {}),
        "created_at": _iso(row.created_at),
        "decided_at": _iso(row.decided_at),
    }


def list_plan_reviews(db: Session, user_id: int, *, limit: int = 30) -> dict[str, Any]:
    rows = (
        db.query(PlanReviewRecord)
        .filter(PlanReviewRecord.user_id == user_id)
        .order_by(PlanReviewRecord.created_at.desc())
        .limit(limit)
        .all()
    )
    return {"schema_version": SCHEMA_VERSION, "items": [serialize_plan_review(row) for row in rows]}


def decide_plan_review(db: Session, user_id: int, review_id: str, decision: str) -> dict[str, Any]:
    if decision not in {"accept", "reject"}:
        raise ValueError("decision must be accept or reject")
    row = db.query(PlanReviewRecord).filter_by(user_id=user_id, review_id=review_id).one_or_none()
    if row is None:
        raise LookupError("plan review not found")
    if row.status not in {"proposal_pending", "accepted", "rejected"}:
        raise ValueError("plan review has no actionable proposal")
    row.status = "accepted" if decision == "accept" else "rejected"
    row.decided_at = utc_now()
    db.flush()
    return serialize_plan_review(row)


def enqueue_due_review_notification(db: Session, user_id: int, insights: dict[str, Any]) -> None:
    due = int(insights.get("overview", {}).get("due_review_count") or 0)
    if due <= 0:
        return
    create_notification(
        db,
        user_id,
        category="review_due",
        title="有知识点需要复习",
        message=f"当前有 {due} 个已完成练习的知识点进入到期复习窗口。",
        dedupe_key=f"review-due:{utc_now().date().isoformat()}",
        source_type="review_queue",
        source_id=utc_now().date().isoformat(),
        action={"type": "navigate", "page": "personalization", "params": {"view": "review"}},
    )


def run_automation_cycle(
    db: Session,
    user_id: int,
    *,
    plan_context: dict[str, Any] | None = None,
    days: int = 30,
) -> dict[str, Any]:
    insights = build_learning_insights(db, user_id, days=days)
    enqueue_due_review_notification(db, user_id, insights)
    evaluated_interventions = evaluate_due_interventions(db, user_id, insights)
    intervention = evaluate_intervention(db, user_id, insights)
    plan_review = run_plan_review(
        db,
        user_id,
        insights=insights,
        plan_context=plan_context or {},
        trigger_type="weekly",
    )
    return {
        "insights": insights,
        "intervention": intervention,
        "evaluated_interventions": evaluated_interventions,
        "plan_review": plan_review,
    }

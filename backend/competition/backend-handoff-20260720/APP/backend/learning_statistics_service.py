from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from APP.backend.database import (
    AuditResultRecord,
    GradingResultRecord,
    KnowledgeCardRecord,
    KnowledgeMasteryState,
    LearnerKPReviewState,
    LearningAttemptItemRecord,
    LearningAttemptRecord,
    LearningFocusSession,
    MistakeRecord,
    PaperSubmissionRecord,
    QuestionVersionRecord,
    ReviewTaskRecord,
)
from APP.backend.time_utils import BEIJING_TZ, as_beijing, utc_now
from APP.backend.system_data_service import focus_seconds_in_window


SCHEMA_VERSION = "1.0"
SUPPORTED_WINDOWS = {7, 30, 90}
_ACCEPTED_AUDIT_STATUSES = {"completed", "reviewed"}
_ACCEPTED_ATTEMPT_STATUSES = {"submitted", "completed"}


def _iso(value: datetime) -> str:
    return as_beijing(value).isoformat()


def _kp_ids(value: str) -> set[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return set()
    if not isinstance(parsed, list):
        return set()
    result: set[str] = set()
    for item in parsed:
        if isinstance(item, str) and item.strip():
            result.add(item.strip())
        elif isinstance(item, dict):
            kp_id = str(item.get("kp_id") or "").strip()
            if kp_id:
                result.add(kp_id)
    return result


def _accepted_grading_rows(
    db: Session,
    learner_id: int,
) -> list[dict[str, Any]]:
    """Return one authoritative, audited grading row per submitted item."""

    rows = (
        db.query(
            LearningAttemptRecord,
            LearningAttemptItemRecord,
            GradingResultRecord,
            AuditResultRecord,
        )
        .join(
            LearningAttemptItemRecord,
            LearningAttemptItemRecord.attempt_id == LearningAttemptRecord.attempt_id,
        )
        .join(
            GradingResultRecord,
            GradingResultRecord.attempt_item_id
            == LearningAttemptItemRecord.attempt_item_id,
        )
        .join(
            AuditResultRecord,
            (AuditResultRecord.source_artifact_id == GradingResultRecord.artifact_id)
            & (
                AuditResultRecord.source_artifact_version
                == GradingResultRecord.version
            ),
        )
        .filter(
            LearningAttemptRecord.learner_id == learner_id,
            LearningAttemptRecord.status.in_(_ACCEPTED_ATTEMPT_STATUSES),
            GradingResultRecord.status == "reviewed",
            GradingResultRecord.score.is_not(None),
            GradingResultRecord.max_score.is_not(None),
            GradingResultRecord.score >= 0,
            GradingResultRecord.max_score > 0,
            GradingResultRecord.score <= GradingResultRecord.max_score,
            AuditResultRecord.decision == "pass",
            AuditResultRecord.status.in_(_ACCEPTED_AUDIT_STATUSES),
        )
        .all()
    )
    accepted: dict[str, tuple[tuple[int, int, int], dict[str, Any]]] = {}
    for attempt, item, grading, audit in rows:
        submitted_at = attempt.submitted_at or attempt.created_at
        rank = (
            int(grading.version or 0),
            int(grading.id or 0),
            int(audit.id or 0),
        )
        payload = {
            "attempt_id": str(attempt.attempt_id),
            "attempt_item_id": str(item.attempt_item_id),
            "attempt_type": str(attempt.attempt_type or "unknown"),
            "source_kind": str(attempt.source_kind or "unknown"),
            "question_version_id": str(item.question_version_id or ""),
            "submitted_at": submitted_at,
            "score": float(grading.score),
            "max_score": float(grading.max_score),
            "is_correct": grading.is_correct,
            "kp_ids": _kp_ids(grading.kp_ids_json) or _kp_ids(item.kp_snapshot_json),
        }
        previous = accepted.get(str(item.attempt_item_id))
        if previous is None or rank > previous[0]:
            accepted[str(item.attempt_item_id)] = (rank, payload)
    return [entry[1] for entry in accepted.values()]


def _completed_papers(
    db: Session,
    learner_id: int,
    *,
    since: datetime | None = None,
) -> list[PaperSubmissionRecord]:
    query = db.query(PaperSubmissionRecord).filter(
        PaperSubmissionRecord.learner_id == learner_id,
        PaperSubmissionRecord.status == "completed",
    )
    if since is not None:
        query = query.filter(PaperSubmissionRecord.created_at >= since)
    rows = query.order_by(
        PaperSubmissionRecord.created_at.desc(),
        PaperSubmissionRecord.id.desc(),
    ).all()
    latest: dict[str, PaperSubmissionRecord] = {}
    for row in rows:
        latest.setdefault(str(row.paper_id), row)
    return list(latest.values())


def _paper_item_count(submissions: list[PaperSubmissionRecord]) -> int:
    total = 0
    for submission in submissions:
        try:
            result = json.loads(submission.result_json or "{}")
        except (TypeError, ValueError):
            result = {}
        items = result.get("items") if isinstance(result, dict) else None
        if isinstance(items, list):
            total += len(items)
    return total


def _metric_block(
    rows: list[dict[str, Any]],
    *,
    paper_submissions: list[PaperSubmissionRecord],
    mistakes: list[MistakeRecord],
    focus_sessions: list[LearningFocusSession],
    effective_focus_seconds: int | None = None,
) -> dict[str, Any]:
    question_versions = {
        str(row["question_version_id"])
        for row in rows
        if str(row["question_version_id"])
    }
    kp_ids = set().union(*(row["kp_ids"] for row in rows)) if rows else set()
    correct = sum(row["is_correct"] is True for row in rows)
    incorrect = sum(row["is_correct"] is False for row in rows)
    correctness_unknown = len(rows) - correct - incorrect
    score = sum(float(row["score"]) for row in rows)
    max_score = sum(float(row["max_score"]) for row in rows)
    attempt_types = Counter(str(row["attempt_type"]) for row in rows)
    source_kinds = Counter(str(row["source_kind"]) for row in rows)
    question_attempt_counts = Counter(
        str(row["question_version_id"])
        for row in rows
        if str(row["question_version_id"])
    )
    completed_attempt_ids = {
        str(row["attempt_id"]) for row in rows
    }
    canonical_paper_items = sum(row["attempt_type"] == "paper" for row in rows)
    persisted_paper_items = _paper_item_count(paper_submissions)
    paper_questions_completed = max(canonical_paper_items, persisted_paper_items)
    non_paper_items = sum(row["attempt_type"] != "paper" for row in rows)
    completed_questions = non_paper_items + paper_questions_completed
    active_seconds = (
        max(0, int(effective_focus_seconds))
        if effective_focus_seconds is not None
        else sum(
            max(0, int(row.active_seconds or 0))
            for row in focus_sessions
            if row.status in {"active", "completed"}
        )
    )
    return {
        "questions_completed": completed_questions,
        "audited_question_items_completed": len(rows),
        "paper_questions_completed": paper_questions_completed,
        "unique_question_versions_completed": len(question_versions),
        "correct_answers": correct,
        "incorrect_answers": incorrect,
        "correctness_unavailable": max(
            correctness_unknown,
            completed_questions - correct - incorrect,
        ),
        "score_rate": round(score / max_score, 4) if max_score > 0 else None,
        "score_points": round(score, 2),
        "available_points": round(max_score, 2),
        "knowledge_points_practiced": len(kp_ids),
        "practice_sessions_completed": len({
            row["attempt_id"] for row in rows if row["attempt_type"] == "practice"
        }),
        "paper_attempts_completed": len(paper_submissions),
        "case_sessions_completed": len({
            row["attempt_id"] for row in rows if row["attempt_type"] == "case"
        }),
        "accepted_attempts_completed": len(completed_attempt_ids),
        "retry_count": sum(
            max(0, count - 1) for count in question_attempt_counts.values()
        ),
        "mistakes_recorded": len(mistakes),
        "active_mistakes": sum(str(row.status or "") == "active" for row in mistakes),
        "focus_minutes": round(active_seconds / 60),
        "attempts_by_type": dict(sorted(attempt_types.items())),
        "attempts_by_source": dict(sorted(source_kinds.items())),
    }


def build_learning_statistics(
    db: Session,
    learner_id: int,
    *,
    days: int = 30,
    now: datetime | None = None,
) -> dict[str, Any]:
    if days not in SUPPORTED_WINDOWS:
        raise ValueError("days must be one of: 7, 30, 90")

    calculated_at = now or utc_now()
    window_start = calculated_at - timedelta(days=days)
    all_rows = _accepted_grading_rows(db, learner_id)
    window_rows = [
        row for row in all_rows
        if row["submitted_at"] is not None and row["submitted_at"] >= window_start
    ]

    question_version_ids = {
        str(row["question_version_id"])
        for row in all_rows
        if str(row["question_version_id"])
    }
    base_questions = {
        str(row.question_id)
        for row in db.query(QuestionVersionRecord).filter(
            QuestionVersionRecord.question_version_id.in_(question_version_ids)
        ).all()
    } if question_version_ids else set()

    all_mistakes = db.query(MistakeRecord).filter(
        MistakeRecord.user_id == learner_id
    ).all()
    window_mistakes = [
        row for row in all_mistakes
        if row.created_at is not None and row.created_at >= window_start
    ]
    all_focus = db.query(LearningFocusSession).filter(
        LearningFocusSession.user_id == learner_id
    ).all()
    window_focus = [
        row
        for row in all_focus
        if row.started_at is not None
        and row.started_at <= calculated_at
        and (row.ended_at is None or row.ended_at >= window_start)
    ]
    window_focus_seconds = focus_seconds_in_window(
        window_focus,
        window_start=window_start,
        window_end=calculated_at,
    )

    lifetime = _metric_block(
        all_rows,
        paper_submissions=_completed_papers(db, learner_id),
        mistakes=all_mistakes,
        focus_sessions=all_focus,
    )
    lifetime["unique_questions_completed"] = len(base_questions) or lifetime[
        "unique_question_versions_completed"
    ]
    window = _metric_block(
        window_rows,
        paper_submissions=_completed_papers(db, learner_id, since=window_start),
        mistakes=window_mistakes,
        focus_sessions=window_focus,
        effective_focus_seconds=window_focus_seconds,
    )
    window_question_versions = {
        str(row["question_version_id"])
        for row in window_rows
        if str(row["question_version_id"])
    }
    window_base_questions = {
        str(row.question_id)
        for row in db.query(QuestionVersionRecord).filter(
            QuestionVersionRecord.question_version_id.in_(window_question_versions)
        ).all()
    } if window_question_versions else set()
    window["unique_questions_completed"] = len(window_base_questions) or window[
        "unique_question_versions_completed"
    ]

    mastery = db.query(KnowledgeMasteryState).filter(
        KnowledgeMasteryState.learner_id == learner_id
    ).all()
    review_states = db.query(LearnerKPReviewState).filter(
        LearnerKPReviewState.learner_id == learner_id
    ).all()
    review_tasks = db.query(ReviewTaskRecord).filter(
        ReviewTaskRecord.learner_id == learner_id
    ).all()
    lifetime.update({
        "knowledge_points_assessed": len(mastery),
        "knowledge_points_mastered": sum(
            row.mastery_score is not None and float(row.mastery_score) >= 80
            for row in mastery
        ),
        "knowledge_cards_saved": db.query(KnowledgeCardRecord).filter(
            KnowledgeCardRecord.user_id == learner_id
        ).count(),
        "review_queue_total": sum(row.status == "active" for row in review_states),
        "reviews_due": sum(
            row.status == "active"
            and row.next_review_at is not None
            and row.next_review_at <= calculated_at
            for row in review_states
        ),
        "review_tasks_completed": sum(row.status == "completed" for row in review_tasks),
        "review_tasks_pending": sum(row.status == "pending" for row in review_tasks),
    })

    return {
        "schema_version": SCHEMA_VERSION,
        "learner_id": str(learner_id),
        "calculated_at": _iso(calculated_at),
        "window": {
            "days": days,
            "start_at": _iso(window_start),
            "end_at": _iso(calculated_at),
            "timezone": str(BEIJING_TZ),
        },
        "lifetime": lifetime,
        "current_window": window,
        "metric_definitions": {
            "questions_completed": {
                "label": "已完成题目总数",
                "formula": (
                    "accepted non-paper attempt items + max(accepted paper attempt items, "
                    "items in latest completed paper submissions)"
                ),
                "sources": [
                    "learning_attempts",
                    "learning_attempt_items",
                    "grading_result_records",
                    "audit_result_records",
                    "paper_submissions",
                ],
            },
            "audited_question_items_completed": {
                "label": "正式审核题项数",
                "formula": "count(distinct attempt_item_id with reviewed grading and passed audit)",
                "sources": [
                    "learning_attempts",
                    "learning_attempt_items",
                    "grading_result_records",
                    "audit_result_records",
                ],
            },
            "unique_questions_completed": {
                "label": "已练习不同题目数",
                "formula": "count(distinct question_id resolved from accepted question versions)",
                "sources": ["question_version_records"],
            },
            "score_rate": {
                "label": "正式批改得分率",
                "formula": "sum(accepted score) / sum(accepted max_score)",
                "sources": ["grading_result_records", "audit_result_records"],
            },
            "retry_count": {
                "label": "同题重复作答次数",
                "formula": "sum(max(accepted attempts per question_version_id - 1, 0))",
                "sources": [
                    "learning_attempt_items",
                    "grading_result_records",
                    "audit_result_records",
                ],
            },
            "paper_attempts_completed": {
                "label": "已完成试卷数",
                "formula": "count(distinct completed paper_id)",
                "sources": ["paper_submissions"],
            },
            "reviews_due": {
                "label": "当前到期复习数",
                "formula": "count(active review state where next_review_at <= calculated_at)",
                "sources": ["learner_kp_review_states"],
            },
        },
        "counting_policy": {
            "drafts_counted": False,
            "opened_questions_counted": False,
            "rejected_gradings_counted": False,
            "retry_policy": "each accepted submitted attempt item counts once",
            "grading_version_policy": "latest accepted grading per attempt_item_id",
        },
    }

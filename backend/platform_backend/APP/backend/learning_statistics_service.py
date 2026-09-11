from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
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
    LearningActivityRecord,
    LearningQuestionAttempt,
    QuestionAttempt,
    LearningFocusSession,
    MistakeRecord,
    PaperSubmissionRecord,
    PaperItemRecord,
    QuestionVersionRecord,
    QuestionBankItem,
    UserQuestionItem,
    ReviewTaskRecord,
)
from APP.backend.time_utils import BEIJING_TZ, as_beijing, utc_now
from APP.backend.system_data_service import focus_seconds_in_window


SCHEMA_VERSION = "1.0"
SUPPORTED_WINDOWS = {7, 30, 90}
_ACCEPTED_AUDIT_STATUSES = {"completed", "reviewed"}
_ACCEPTED_ATTEMPT_STATUSES = {"submitted", "completed"}


def practice_window_start(now: datetime, days: int) -> datetime:
    if days not in SUPPORTED_WINDOWS:
        raise ValueError('days must be one of: 7, 30, 90')
    day = as_beijing(now).date() - timedelta(days=days - 1)
    return datetime.combine(day, datetime.min.time(), tzinfo=BEIJING_TZ).astimezone(timezone.utc).replace(tzinfo=None)


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
            "source_task_id": str(attempt.source_task_id or ''),
            "request_id": str(attempt.request_id or ""),
            "audited": True,
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


def unified_practice_rows(db: Session, learner_id: int) -> list[dict[str, Any]]:
    """Read-only union; preserve lineage and never fabricate audit results.

    Canonical requests own their result, including rejected/pending results.
    Legacy mirrors without request IDs require exact question/answer/time matches.
    """
    rows = _accepted_grading_rows(db, learner_id)
    versions = {r.question_version_id: str(r.question_id) for r in db.query(QuestionVersionRecord).filter(
        QuestionVersionRecord.question_version_id.in_([r['question_version_id'] for r in rows])
    ).all()}
    for row in rows:
        row['question_id'] = versions.get(row['question_version_id'], row['question_version_id'])
        if row['attempt_type'] == 'case':
            row['question_id'] = row['source_task_id'] or row['question_id']
    canonical = db.query(LearningAttemptRecord, LearningAttemptItemRecord).join(
        LearningAttemptItemRecord, LearningAttemptItemRecord.attempt_id == LearningAttemptRecord.attempt_id
    ).filter(LearningAttemptRecord.learner_id == learner_id).all()
    all_versions = {r.question_version_id: str(r.question_id) for r in db.query(QuestionVersionRecord).filter(
        QuestionVersionRecord.question_version_id.in_([i.question_version_id for _, i in canonical])
    ).all()}
    requests = {a.request_id for a, _ in canonical if a.request_id}
    activity_links = {}
    for activity in db.query(LearningActivityRecord).filter(
        LearningActivityRecord.user_id == learner_id,
        LearningActivityRecord.activity_type == 'question_attempt',
    ).order_by(LearningActivityRecord.id).all():
        try:
            payload = json.loads(activity.payload_json or '{}')
        except (TypeError, ValueError):
            payload = {}
        if isinstance(payload, dict):
            activity_links[(activity.resource_id, activity.created_at)] = (activity, payload)
    legacy_requests = set()
    mirrors = Counter((all_versions.get(i.question_version_id, i.question_version_id),
                       a.submitted_at or a.created_at, str(i.submitted_answer or '')) for a, i in canonical)
    for projection in db.query(LearningQuestionAttempt).filter(LearningQuestionAttempt.user_id == learner_id).all():
        if projection.request_id in requests:
            for attempt, item in canonical:
                if attempt.request_id == projection.request_id and projection.answered_at != (attempt.submitted_at or attempt.created_at):
                    mirrors[(projection.question_id, projection.answered_at, str(item.submitted_answer or ''))] += 1
    for old in db.query(QuestionAttempt).filter(QuestionAttempt.user_id == learner_id).order_by(QuestionAttempt.id).all():
        activity, payload = activity_links.get((old.question_id, old.created_at), (None, {}))
        request = str(payload.get('request_id') or '')
        if activity is not None and activity.completion_status not in {'completed', 'complete', 'done', 'submitted', 'passed', 'needs_review'}:
            continue
        audit = payload.get('audit')
        if isinstance(audit, dict) and audit.get('decision') not in (None, 'pass'):
            continue
        if request and (request in requests or request in legacy_requests):
            continue
        key = (old.question_id, old.created_at, str(old.answer or ''))
        if mirrors[key]:
            mirrors[key] -= 1
            continue
        if request:
            legacy_requests.add(request)
        score = float(old.score) if old.score is not None else None
        if score is not None and (not math.isfinite(score) or not 0 <= score <= 100):
            score = None
        rows.append({
            'attempt_id': f'legacy:{old.id}', 'attempt_item_id': f'legacy:{old.id}',
            'legacy_id': old.id, 'request_id': request, 'audited': False,
            'attempt_type': 'practice', 'source_kind': 'legacy_question_attempt',
            'question_id': str(old.question_id), 'question_version_id': str(old.question_id),
            'submitted_at': old.created_at, 'score': score,
            'max_score': 100.0 if score is not None else None,
            'is_correct': old.is_correct, 'kp_ids': _kp_ids(old.kp_ids_json),
        })
    owned_cases = {str(a.source_task_id) for a, _ in canonical if a.attempt_type == 'case'}
    for activity in db.query(LearningActivityRecord).filter(
        LearningActivityRecord.user_id == learner_id,
        LearningActivityRecord.activity_type == 'case_training',
        LearningActivityRecord.completion_status.in_(['completed', 'complete', 'done', 'passed']),
    ).order_by(LearningActivityRecord.id).all():
        session_id = str(activity.resource_id or '')
        if not session_id or session_id in owned_cases:
            continue
        owned_cases.add(session_id)
        rows.append({
            'attempt_id': f'case:{session_id}', 'attempt_item_id': f'case:{session_id}',
            'request_id': '', 'audited': False, 'attempt_type': 'case',
            'source_kind': 'completed_case_activity', 'source_task_id': session_id,
            'question_id': session_id, 'question_version_id': '',
            'submitted_at': activity.created_at, 'score': None, 'max_score': None,
            'is_correct': None, 'kp_ids': set(),
        })
    return _merge_completed_paper_rows(db, learner_id, rows)


def _merge_completed_paper_rows(db: Session, learner_id: int, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical_requests = {str(a.request_id) for a in db.query(LearningAttemptRecord).filter(
        LearningAttemptRecord.learner_id == learner_id,
        LearningAttemptRecord.attempt_type == 'paper',
    ).all() if a.request_id}
    for submission in _completed_papers(db, learner_id):
        try:
            result = json.loads(submission.result_json or '{}')
        except (ValueError, TypeError):
            continue
        items = result.get('items') if isinstance(result, dict) else None
        if not isinstance(items, list):
            continue
        snapshots = {r.paper_item_id: r for r in db.query(PaperItemRecord).filter(
            PaperItemRecord.paper_id == submission.paper_id).all()}
        seen = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            key = str(item.get('paper_item_id') or index)
            if key in seen:
                continue
            seen.add(key)
            request = f'{submission.request_id}:{key}'
            snapshot = snapshots.get(key)
            if request in canonical_requests and not any(r['request_id'] == request for r in rows):
                continue
            existing = next((r for r in rows if r['attempt_type'] == 'paper' and (
                r['request_id'] == request or (not r['request_id'] and snapshot is not None and r.get('source_task_id') == submission.paper_id
                and r['question_version_id'] == snapshot.question_version_id))), None)
            score, maximum = item.get('score'), item.get('max_score')
            valid = (isinstance(score, (int, float)) and isinstance(maximum, (int, float))
                     and math.isfinite(score) and math.isfinite(maximum) and 0 <= score <= maximum and maximum > 0)
            if existing is not None:
                if valid:
                    existing.update(score=float(score), max_score=float(maximum))
                continue
            rows.append({
                'attempt_id': f'paper:{submission.id}', 'attempt_item_id': f'paper:{submission.id}:{key}',
                'source_task_id': submission.paper_id, 'request_id': request, 'audited': False,
                'attempt_type': 'paper', 'source_kind': 'completed_paper_submission',
                'question_id': str(snapshot.question_id) if snapshot else str(item.get('question_id') or key),
                'question_version_id': str(snapshot.question_version_id) if snapshot else key,
                'submitted_at': submission.created_at, 'score': float(score) if valid else None,
                'max_score': float(maximum) if valid else None,
                'is_correct': item.get('is_correct') if isinstance(item.get('is_correct'), bool) else None,
                'kp_ids': _kp_ids(snapshot.kp_snapshot_json) if snapshot else set(),
            })
    return rows


@lru_cache(maxsize=1)
def _original_import_question_stems() -> dict[str, str]:
    """Read the original catalog used by synthetic_usage_v1; never import data."""
    catalog = Path(__file__).resolve().parent / 'sample_data' / 'shizhen_mvp_seed.json'
    try:
        data = json.loads(catalog.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or not isinstance(data.get('question_bank'), list):
        return {}
    return {str(item['question_id']): item['stem'] for item in data['question_bank']
            if isinstance(item, dict) and item.get('question_id') and isinstance(item.get('stem'), str)}


def build_practice_history(db: Session, learner_id: int, *, days: int = 30,
                           now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    start = practice_window_start(now, days)
    rows = [r for r in unified_practice_rows(db, learner_id)
            if r['submitted_at'] is not None and start <= r['submitted_at'] <= now]
    question_ids = {row['question_id'] for row in rows if row['attempt_type'] != 'case'}
    stems = {str(item.question_id): item.stem for item in db.query(QuestionBankItem).filter(
        QuestionBankItem.question_id.in_(question_ids)).all() if item.stem}
    stems.update({str(item.question_id): item.stem for item in db.query(UserQuestionItem).filter(
        UserQuestionItem.owner_user_id == learner_id,
        UserQuestionItem.question_id.in_(question_ids)).all() if item.stem})
    version_stems = {str(item.question_version_id): item.stem for item in db.query(QuestionVersionRecord).filter(
        QuestionVersionRecord.question_version_id.in_([row['question_version_id'] for row in rows])).all() if item.stem}
    activities = db.query(LearningActivityRecord).filter(
        LearningActivityRecord.user_id == learner_id,
        LearningActivityRecord.activity_type.in_(['question_attempt', 'paper_submission', 'case_training']),
    ).order_by(LearningActivityRecord.id).all()
    by_request = {}
    by_question_time = {}
    for activity in activities:
        try:
            payload = json.loads(activity.payload_json or '{}')
        except (ValueError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if payload.get('request_id'):
            by_request[payload['request_id']] = (activity, payload)
        by_question_time[(activity.resource_id, activity.created_at)] = (activity, payload)
    result = []
    for row in sorted(rows, key=lambda r: (r['submitted_at'], r['attempt_item_id']), reverse=True):
        activity, payload = by_request.get(row['request_id'], by_question_time.get(
            (row['question_id'], row['submitted_at']), (None, {})))
        original_stem = (_original_import_question_stems().get(row['question_id'])
                         if row.get('source_kind') == 'legacy_question_attempt'
                         and payload.get('source') == 'synthetic_usage_v1' else None)
        result.append({
            'activity_id': row['attempt_item_id'],
            'activity_type': 'case_training' if row['attempt_type'] == 'case' else 'question_attempt',
            'resource_type': ('case_session' if row['attempt_type'] == 'case' else
                              getattr(activity, 'resource_type', 'question')),
            'resource_id': row['question_id'],
            'practice_origin': payload.get('practice_origin'),
            'attempt_type': row['attempt_type'],
            'paper_id': row.get('source_task_id') if row['attempt_type'] == 'paper' else None,
            'practice_mode': payload.get('practice_mode'),
            'completion_status': 'completed',
            'score': row['score'] / row['max_score'] if row['score'] is not None and row['max_score'] else None,
            'created_at': _iso(row['submitted_at']),
            'title': (version_stems.get(row['question_version_id']) or stems.get(row['question_id'])
                      or payload.get('title') or original_stem
                      or ('模拟病患训练' if row['attempt_type'] == 'case' else '历史作答（题干暂不可用）')),
            'duration_minutes': int(getattr(activity, 'duration_minutes', 0) or 0),
        })
    result.sort(key=lambda item: (item['created_at'], str(item['activity_id'])), reverse=True)
    return {'window_days': days, 'total': len(result), 'recent_activities': result,
            'counting_policy': 'unified_practice_rows', 'calculated_at': _iso(now)}


def _completed_papers(
    db: Session,
    learner_id: int,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[PaperSubmissionRecord]:
    query = db.query(PaperSubmissionRecord).filter(
        PaperSubmissionRecord.learner_id == learner_id,
        PaperSubmissionRecord.status == "completed",
    )
    if since is not None:
        query = query.filter(PaperSubmissionRecord.created_at >= since)
    if until is not None:
        query = query.filter(PaperSubmissionRecord.created_at <= until)
    rows = query.order_by(
        PaperSubmissionRecord.created_at.desc(),
        PaperSubmissionRecord.id.desc(),
    ).all()
    latest: dict[tuple[str, str], PaperSubmissionRecord] = {}
    canonical_requests = {str(a.request_id) for a in db.query(LearningAttemptRecord).filter(
        LearningAttemptRecord.learner_id == learner_id,
        LearningAttemptRecord.attempt_type == 'paper',
    ).all() if a.request_id}
    accepted_requests = {r['request_id'] for r in _accepted_grading_rows(db, learner_id)}
    for row in rows:
        try:
            result = json.loads(row.result_json or '{}')
        except (TypeError, ValueError):
            result = {}
        items = result.get('items', []) if isinstance(result, dict) else []
        item_requests = {f"{row.request_id}:{item.get('paper_item_id')}"
                         for item in items if isinstance(item, dict) and item.get('paper_item_id')} if isinstance(items, list) else set()
        if (item_requests & canonical_requests) - accepted_requests:
            continue
        latest.setdefault((str(row.paper_id), str(row.request_id)), row)
    return list(latest.values())


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
    question_rows = [row for row in rows if row['attempt_type'] != 'case']
    correct = sum(row["is_correct"] is True for row in question_rows)
    incorrect = sum(row["is_correct"] is False for row in question_rows)
    correctness_unknown = len(question_rows) - correct - incorrect
    scored_rows = [row for row in rows if row['attempt_type'] != 'case' and row['score'] is not None and row['max_score'] is not None]
    score = sum(float(row["score"]) for row in scored_rows)
    max_score = sum(float(row["max_score"]) for row in scored_rows)
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
    paper_questions_completed = sum(row['attempt_type'] == 'paper' for row in rows)
    non_paper_items = sum(row["attempt_type"] not in {'paper', 'case'} for row in rows)
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
        "audited_question_items_completed": sum(row.get('audited', True) for row in question_rows),
        "legacy_question_items_completed": sum(row.get('source_kind') == 'legacy_question_attempt' for row in rows),
        "paper_questions_completed": paper_questions_completed,
        "unique_question_versions_completed": len(question_versions),
        "correct_answers": correct,
        "incorrect_answers": incorrect,
        "correctness_unavailable": max(
            correctness_unknown,
            completed_questions - correct - incorrect,
        ),
        "score_rate": round(score / max_score, 4) if max_score > 0 else None,
        "accuracy": round(correct / (correct + incorrect), 4) if correct + incorrect else None,
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
    window_start = practice_window_start(calculated_at, days)
    all_rows = [r for r in unified_practice_rows(db, learner_id)
                if r['submitted_at'] is not None and r['submitted_at'] <= calculated_at]
    window_rows = [
        row for row in all_rows
        if row["submitted_at"] is not None and row["submitted_at"] >= window_start
    ]

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
        paper_submissions=_completed_papers(db, learner_id, until=calculated_at),
        mistakes=all_mistakes,
        focus_sessions=all_focus,
    )
    lifetime["unique_questions_completed"] = len({r['question_id'] for r in all_rows if r['question_id'] and r['attempt_type'] != 'case'})
    window = _metric_block(
        window_rows,
        paper_submissions=_completed_papers(db, learner_id, since=window_start, until=calculated_at),
        mistakes=window_mistakes,
        focus_sessions=window_focus,
        effective_focus_seconds=window_focus_seconds,
    )
    window["unique_questions_completed"] = len({r['question_id'] for r in window_rows if r['question_id'] and r['attempt_type'] != 'case'})
    today_start = datetime.combine(
        as_beijing(calculated_at).date(),
        datetime.min.time(),
        tzinfo=BEIJING_TZ,
    ).astimezone(timezone.utc).replace(tzinfo=None)
    today_rows = [
        row for row in all_rows
        if row["submitted_at"] is not None
        and today_start <= row["submitted_at"] <= calculated_at
    ]
    today_focus = [
        row
        for row in window_focus
        if row.started_at is not None
        and row.started_at <= calculated_at
        and (row.ended_at is None or row.ended_at >= today_start)
    ]
    today = _metric_block(
        today_rows,
        paper_submissions=_completed_papers(db, learner_id, since=today_start, until=calculated_at),
        mistakes=[
            row for row in all_mistakes
            if row.created_at is not None and today_start <= row.created_at <= calculated_at
        ],
        focus_sessions=today_focus,
        effective_focus_seconds=focus_seconds_in_window(
            today_focus,
            window_start=today_start,
            window_end=calculated_at,
        ),
    )

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
        "today": today,
        "metric_definitions": {
            "questions_completed": {
                "label": "已完成题目总数",
                "formula": (
                    "count(unified completed question items: audited submissions + legacy attempts + completed paper items, deduplicated)"
                ),
                "sources": [
                    "learning_attempts",
                    "learning_attempt_items",
                    "grading_result_records",
                    "audit_result_records",
                    "paper_submissions",
                    "question_attempts",
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
                "formula": "count(distinct question_id from unified completed question items)",
                "sources": ["question_version_records", "question_attempts", "paper_submissions"],
            },
            "score_rate": {
                "label": "练习得分率",
                "formula": "sum(unified scored question points) / sum(corresponding maximum points)",
                "sources": ["grading_result_records", "audit_result_records", "question_attempts", "paper_submissions"],
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
                "formula": "count(distinct completed paper submission requests)",
                "sources": ["paper_submissions"],
            },
            "reviews_due": {
                "label": "当前到期复习数",
                "formula": "count(active review state where next_review_at <= calculated_at)",
                "sources": ["learner_kp_review_states"],
            },
        },
        "counting_policy": {
            "legacy_policy": "all accounts: persisted legacy question_attempts included; canonical mirrors excluded",
            "window_policy": "Beijing calendar days including today; exclude future submissions",
            "drafts_counted": False,
            "opened_questions_counted": False,
            "rejected_gradings_counted": False,
            "retry_policy": "each accepted submitted attempt item counts once",
            "grading_version_policy": "latest accepted grading per attempt_item_id",
        },
    }

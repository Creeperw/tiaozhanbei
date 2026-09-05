from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import hashlib
import json
from typing import Any, Iterable
import uuid

from sqlalchemy.orm import Session

from APP.backend.database import (
    AuditResultRecord,
    GradingResultRecord,
    KnowledgeMasteryState,
    KnowledgePoint,
    KnowledgePointCanonicalMigration,
    LearnerExamAttemptMembership,
    LearnerExamProgressState,
    LearnerKPReviewState,
    LearnerKnowledgeMastery,
    LearningAttemptItemRecord,
    LearningAttemptRecord,
    LearningWritebackReceipt,
    MasteryHistoryRecord,
    ReviewTaskRecord,
)
from APP.backend.knowledge_point_identity_service import register_reviewed_equivalence
from APP.backend.review_formula import (
    FORMULA_VERSION,
    lambda_per_day,
    mastery_after_attempt,
    stability_for_interval,
)
from APP.backend.time_utils import utc_now


REPLAY_VERSION = "canonical_event_replay_v1"


class ReplayEvidenceConflict(ValueError):
    def __init__(self, conflicts: Iterable[str]):
        self.conflicts = tuple(conflicts)
        super().__init__("; ".join(self.conflicts))


@dataclass(frozen=True)
class CanonicalReplayEvent:
    learner_id: int
    attempt_id: str
    attempt_item_id: str
    occurred_at: datetime
    q_t: float
    is_correct: bool
    confidence: float
    exam_track_ids: tuple[str, ...]
    source_kp_ids: tuple[str, ...]
    sequence: int = 0


@dataclass(frozen=True)
class CanonicalReplayProjection:
    learner_id: int
    mastery_score: float
    mastery_confidence: float
    attempt_count: int
    wrong_count: int
    review_count: int
    recent_five_wrong_count: int
    consecutive_independent_correct: int
    consecutive_wrong_count: int
    lambda_per_day: float
    last_assessed_at: datetime
    next_review_at: datetime | None
    stability_seconds: float
    retention_estimate: float
    review_stage: str
    requires_remediation: bool
    exam_track_ids: tuple[str, ...]
    event_history: tuple[dict[str, Any], ...]


def _json_list(raw: str) -> list[Any]:
    value = json.loads(raw or "[]")
    if not isinstance(value, list):
        raise ValueError("persisted knowledge point ids must be a list")
    return value


def _kp_ids(raw: str) -> tuple[str, ...]:
    values = _json_list(raw)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("persisted knowledge point ids are invalid")
    return tuple(dict.fromkeys(value.strip() for value in values))


def _q_t(grading: GradingResultRecord) -> float:
    if grading.score is not None and grading.max_score and grading.max_score > 0:
        return max(0.0, min(1.0, float(grading.score) / float(grading.max_score)))
    return 1.0 if grading.is_correct else 0.0


def _is_correct(grading: GradingResultRecord, q_t: float) -> bool:
    return bool(grading.is_correct) if grading.is_correct is not None else q_t >= 0.6


def _passing_written_gradings(
    db: Session,
    source_kp_ids: set[str],
    learner_id: int | None = None,
    admitted_only: bool = False,
) -> tuple[
    dict[str, tuple[GradingResultRecord, LearningWritebackReceipt]],
    tuple[str, ...],
]:
    passing_pairs = {
        (str(artifact_id), int(version))
        for artifact_id, version in db.query(
            AuditResultRecord.source_artifact_id,
            AuditResultRecord.source_artifact_version,
        )
        .filter(AuditResultRecord.decision == "pass")
        .filter(AuditResultRecord.status.in_(("completed", "reviewed")))
        .all()
    }
    by_item: dict[str, list[tuple[GradingResultRecord, LearningWritebackReceipt]]] = {}
    query = (
        db.query(GradingResultRecord, LearningWritebackReceipt)
        .join(
            LearningWritebackReceipt,
            (LearningWritebackReceipt.attempt_item_id == GradingResultRecord.attempt_item_id)
            & (LearningWritebackReceipt.grading_artifact_id == GradingResultRecord.artifact_id)
            & (LearningWritebackReceipt.grading_artifact_version == GradingResultRecord.version),
        )
        .filter(
            GradingResultRecord.status == "reviewed",
            LearningWritebackReceipt.status == "applied",
        )
    )
    if learner_id is not None:
        query = query.join(
            LearningAttemptItemRecord,
            LearningAttemptItemRecord.attempt_item_id == GradingResultRecord.attempt_item_id,
        ).join(
            LearningAttemptRecord,
            LearningAttemptRecord.attempt_id == LearningAttemptItemRecord.attempt_id,
        ).filter(LearningAttemptRecord.learner_id == learner_id)
    for grading, receipt in query.order_by(
            GradingResultRecord.attempt_item_id,
            LearningWritebackReceipt.created_at.desc(),
            LearningWritebackReceipt.id.desc(),
        ).all():
        if admitted_only:
            refs = json.loads(receipt.effect_refs_json or "{}")
            if not isinstance(refs, dict):
                continue
            if not any(
                str(update.get("kp_id", "")) in source_kp_ids
                for update in refs.get("mastery_updates", [])
            ):
                continue
        if (grading.artifact_id, int(grading.version)) in passing_pairs:
            if not set(_kp_ids(grading.kp_ids_json)) & source_kp_ids:
                continue
            by_item.setdefault(grading.attempt_item_id, []).append((grading, receipt))
    latest: dict[str, tuple[GradingResultRecord, LearningWritebackReceipt]] = {}
    conflicts: list[str] = []
    for attempt_item_id, rows in by_item.items():
        signatures = {
            (_q_t(row), _is_correct(row, _q_t(row)), _kp_ids(row.kp_ids_json))
            for row, _receipt in rows
        }
        if len(signatures) > 1:
            conflicts.append(attempt_item_id)
            continue
        latest[attempt_item_id] = rows[0]
    return latest, tuple(sorted(conflicts))


def collect_canonical_replay_events(
    db: Session,
    *,
    source_kp_ids: Iterable[str],
    learner_id: int | None = None,
    admitted_only: bool = False,
) -> tuple[CanonicalReplayEvent, ...]:
    sources = tuple(dict.fromkeys(str(value).strip() for value in source_kp_ids if str(value).strip()))
    if not sources:
        raise ValueError("source knowledge point ids are required")
    source_set = set(sources)
    written_gradings, grading_conflicts = _passing_written_gradings(
        db, source_set, learner_id=learner_id, admitted_only=admitted_only
    )
    if grading_conflicts:
        raise ReplayEvidenceConflict(
            f"conflicting passing grading versions: {attempt_item_id}"
            for attempt_item_id in grading_conflicts
        )
    if not written_gradings:
        return ()

    items = {
        row.attempt_item_id: row
        for row in db.query(LearningAttemptItemRecord)
        .filter(LearningAttemptItemRecord.attempt_item_id.in_(tuple(written_gradings)))
        .all()
    }
    attempts = {
        row.attempt_id: row
        for row in db.query(LearningAttemptRecord)
        .filter(LearningAttemptRecord.attempt_id.in_(tuple({item.attempt_id for item in items.values()})))
        .all()
    }
    memberships: dict[tuple[int, str], set[str]] = {}
    for membership in db.query(LearnerExamAttemptMembership).all():
        memberships.setdefault((membership.learner_id, membership.attempt_id), set()).add(
            membership.exam_track_id
        )

    events: list[CanonicalReplayEvent] = []
    for attempt_item_id, (grading, receipt) in written_gradings.items():
        grading_kps = _kp_ids(grading.kp_ids_json)
        matching_sources = tuple(kp_id for kp_id in grading_kps if kp_id in source_set)
        if not matching_sources:
            continue
        item = items.get(attempt_item_id)
        attempt = attempts.get(item.attempt_id) if item is not None else None
        if item is None or attempt is None:
            raise ValueError(f"replay evidence is missing attempt data for {attempt_item_id}")
        occurred_at = receipt.created_at or attempt.submitted_at
        if occurred_at is None:
            raise ValueError(f"replay evidence is missing writeback time for {attempt_item_id}")
        ratio = _q_t(grading)
        correct = _is_correct(grading, ratio)
        refs = json.loads(receipt.effect_refs_json or "{}")
        outcome = refs.get("learning_state_outcome") if isinstance(refs, dict) else None
        if isinstance(outcome, dict):
            ratio = float(outcome["q_t"])
            correct = outcome["is_correct"]
            if not 0.0 <= ratio <= 1.0 or not isinstance(correct, bool):
                raise ValueError("invalid persisted learning state outcome")
        events.append(CanonicalReplayEvent(
            learner_id=attempt.learner_id,
            attempt_id=attempt.attempt_id,
            attempt_item_id=attempt_item_id,
            occurred_at=occurred_at,
            q_t=ratio,
            is_correct=correct,
            confidence=float(grading.confidence or 0.0),
            exam_track_ids=tuple(sorted(memberships.get((attempt.learner_id, attempt.attempt_id), ()))),
            source_kp_ids=matching_sources,
            sequence=int(receipt.id),
        ))
    events.sort(key=lambda row: (row.learner_id, row.occurred_at, row.sequence))
    return tuple(events)


def replay_canonical_events(events: Iterable[CanonicalReplayEvent]) -> tuple[CanonicalReplayProjection, ...]:
    grouped: dict[int, list[CanonicalReplayEvent]] = {}
    for event in events:
        grouped.setdefault(event.learner_id, []).append(event)

    projections: list[CanonicalReplayProjection] = []
    for learner_id, learner_events in sorted(grouped.items()):
        score: float | None = None
        confidence = 0.0
        previous_at: datetime | None = None
        correctness: list[bool] = []
        exam_tracks: set[str] = set()
        history: list[dict[str, Any]] = []
        next_review_at: datetime | None = None
        stability_seconds = 0.0
        for event in sorted(learner_events, key=lambda row: (row.occurred_at, row.sequence)):
            recent_wrong = sum(not value for value in correctness[-5:])
            consecutive_correct = 0
            for value in reversed(correctness):
                if not value:
                    break
                consecutive_correct += 1
            rate = lambda_per_day(recent_wrong, consecutive_correct)
            delta_days = (
                max(0.0, (event.occurred_at - previous_at).total_seconds() / 86400.0)
                if previous_at is not None else 0.0
            )
            previous_score = score
            score = mastery_after_attempt(
                previous_score=previous_score,
                q_t=event.q_t,
                lambda_value=rate,
                delta_days=delta_days,
            )
            correctness.append(event.is_correct)
            confidence = event.confidence
            previous_at = event.occurred_at
            exam_tracks.update(event.exam_track_ids)
            if event.is_correct:
                next_review_at = None
                stability_seconds = 0.0
            else:
                next_review_at = event.occurred_at + timedelta(seconds=300)
                stability_seconds = stability_for_interval(300)
            history.append({
                "attempt_id": event.attempt_id,
                "attempt_item_id": event.attempt_item_id,
                "occurred_at": event.occurred_at.isoformat(),
                "source_kp_ids": list(event.source_kp_ids),
                "q_t": event.q_t,
                "previous_score": previous_score,
                "lambda_per_day": rate,
                "delta_days": delta_days,
                "mastery_score": score,
                "is_correct": event.is_correct,
                "confidence": event.confidence,
                "exam_track_ids": list(event.exam_track_ids),
                "source_sequence": event.sequence,
            })
        recent_five_wrong_count = sum(not value for value in correctness[-5:])
        consecutive_correct = 0
        consecutive_wrong = 0
        for value in reversed(correctness):
            if value:
                consecutive_correct += 1
            else:
                break
        for value in reversed(correctness):
            if not value:
                consecutive_wrong += 1
            else:
                break
        final_rate = lambda_per_day(recent_five_wrong_count, consecutive_correct)
        projections.append(CanonicalReplayProjection(
            learner_id=learner_id,
            mastery_score=float(score or 0.0),
            mastery_confidence=confidence,
            attempt_count=len(correctness),
            wrong_count=sum(not value for value in correctness),
            review_count=len(correctness),
            recent_five_wrong_count=recent_five_wrong_count,
            consecutive_independent_correct=consecutive_correct,
            consecutive_wrong_count=consecutive_wrong,
            lambda_per_day=final_rate,
            last_assessed_at=previous_at,
            next_review_at=next_review_at,
            stability_seconds=stability_seconds,
            retention_estimate=0.0,
            review_stage="0",
            requires_remediation=False,
            exam_track_ids=tuple(sorted(exam_tracks)),
            event_history=tuple(history),
        ))
    return tuple(projections)


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value


def _row_snapshot(row: Any) -> dict[str, Any]:
    return {
        column.name: _serialize(getattr(row, column.name))
        for column in row.__table__.columns
    }


def _state_snapshot(db: Session, source_kp_ids: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
    models_and_fields = (
        (KnowledgeMasteryState, KnowledgeMasteryState.kp_id),
        (MasteryHistoryRecord, MasteryHistoryRecord.kp_id),
        (LearnerKPReviewState, LearnerKPReviewState.kp_id),
        (LearnerExamProgressState, LearnerExamProgressState.kp_id),
        (LearnerKnowledgeMastery, LearnerKnowledgeMastery.kp_id),
        (ReviewTaskRecord, ReviewTaskRecord.primary_kp_id),
    )
    return {
        model.__tablename__: [
            _row_snapshot(row)
            for row in db.query(model).filter(field.in_(source_kp_ids)).order_by(model.id).all()
        ]
        for model, field in models_and_fields
    }


def _projection_dict(projection: CanonicalReplayProjection) -> dict[str, Any]:
    return _serialize(asdict(projection))


def _validate_replay(
    db: Session,
    *,
    canonical_kp_id: str,
    source_kp_ids: tuple[str, ...],
    events: tuple[CanonicalReplayEvent, ...],
) -> list[str]:
    conflicts: list[str] = []
    canonical = db.query(KnowledgePoint).filter_by(kp_id=canonical_kp_id).one_or_none()
    if canonical is None or canonical.status != "active":
        conflicts.append("canonical knowledge point is not active")
    missing = [
        kp_id for kp_id in source_kp_ids
        if db.query(KnowledgePoint).filter_by(kp_id=kp_id).one_or_none() is None
    ]
    if missing:
        conflicts.append(f"source knowledge point records are missing: {', '.join(missing)}")
    known_history_ids = {
        row.trigger_attempt_item_id
        for row in db.query(MasteryHistoryRecord)
        .filter(MasteryHistoryRecord.kp_id.in_(source_kp_ids))
        .all()
        if row.trigger_attempt_item_id
    }
    event_ids = {event.attempt_item_id for event in events}
    unexplained = sorted(known_history_ids - event_ids)
    if unexplained:
        conflicts.append(
            "persisted mastery history lacks one unique passing reviewed grading: "
            + ", ".join(unexplained)
        )
    event_learners = {event.learner_id for event in events}
    derived_learners: set[int] = set()
    for model, learner_field in (
        (KnowledgeMasteryState, KnowledgeMasteryState.learner_id),
        (LearnerKPReviewState, LearnerKPReviewState.learner_id),
        (LearnerKnowledgeMastery, LearnerKnowledgeMastery.user_id),
        (LearnerExamProgressState, LearnerExamProgressState.learner_id),
    ):
        derived_learners.update(
            learner_id
            for learner_id, in db.query(learner_field)
            .filter(model.kp_id.in_(source_kp_ids))
            .distinct()
            .all()
        )
    orphaned_learners = sorted(derived_learners - event_learners)
    if orphaned_learners:
        conflicts.append(
            "derived learner states have no replayable evidence: "
            + ", ".join(str(value) for value in orphaned_learners)
        )
    event_exam_scopes = {
        (event.learner_id, exam_track_id)
        for event in events
        for exam_track_id in event.exam_track_ids
    }
    existing_exam_scopes = {
        (learner_id, exam_track_id)
        for learner_id, exam_track_id in db.query(
            LearnerExamProgressState.learner_id,
            LearnerExamProgressState.exam_track_id,
        )
        .filter(LearnerExamProgressState.kp_id.in_(source_kp_ids))
        .distinct()
        .all()
    }
    orphaned_exam_scopes = sorted(existing_exam_scopes - event_exam_scopes)
    if orphaned_exam_scopes:
        conflicts.append(
            "exam progress has no authoritative attempt membership: "
            + ", ".join(f"{learner_id}/{exam_track_id}" for learner_id, exam_track_id in orphaned_exam_scopes)
        )
    duplicates: dict[tuple[int, str], list[CanonicalReplayEvent]] = {}
    for event in events:
        duplicates.setdefault((event.learner_id, event.attempt_item_id), []).append(event)
    repeated = [key for key, rows in duplicates.items() if len(rows) > 1]
    if repeated:
        conflicts.append(f"duplicate replay events detected: {repeated}")
    return conflicts


def build_canonical_replay_report(
    db: Session,
    *,
    canonical_kp_id: str,
    source_kp_ids: Iterable[str],
) -> dict[str, Any]:
    canonical_id = canonical_kp_id.strip()
    sources = tuple(dict.fromkeys(
        [canonical_id, *(str(value).strip() for value in source_kp_ids)]
    ))
    evidence_conflicts: list[str] = []
    try:
        events = collect_canonical_replay_events(db, source_kp_ids=sources)
    except ReplayEvidenceConflict as exc:
        events = ()
        evidence_conflicts.extend(exc.conflicts)
    conflicts = _validate_replay(
        db,
        canonical_kp_id=canonical_id,
        source_kp_ids=sources,
        events=events,
    )
    conflicts = [*evidence_conflicts, *conflicts]
    projections = replay_canonical_events(events) if not conflicts else ()
    before = _state_snapshot(db, sources)
    payload = {
        "version": REPLAY_VERSION,
        "canonical_kp_id": canonical_id,
        "source_kp_ids": list(sources),
        "event_count": len(events),
        "deduplicated_lineage_count": sum(len(event.source_kp_ids) for event in events),
        "learner_count": len(projections),
        "history_policy": "preserve_raw_history_project_canonical_on_read",
        "apply_scope": "derived_learning_state_only",
        "conflicts": conflicts,
        "events": [_serialize(asdict(event)) for event in events],
        "projections": [_projection_dict(projection) for projection in projections],
        "before": before,
    }
    digest_input = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["plan_hash"] = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    payload["status"] = "blocked" if conflicts else "ready"
    return payload


def _upsert_projection(
    db: Session,
    *,
    canonical_kp_id: str,
    source_kp_ids: tuple[str, ...],
    projection: CanonicalReplayProjection,
) -> None:
    learner_id = projection.learner_id
    state_rows = db.query(KnowledgeMasteryState).filter(
        KnowledgeMasteryState.learner_id == learner_id,
        KnowledgeMasteryState.kp_id.in_(source_kp_ids),
    ).order_by(KnowledgeMasteryState.id).all()
    state = next((row for row in state_rows if row.kp_id == canonical_kp_id), None)
    if state is None:
        state = KnowledgeMasteryState(
            mastery_state_id=str(uuid.uuid4()), learner_id=learner_id, kp_id=canonical_kp_id
        )
        db.add(state)
    state.mastery_score = projection.mastery_score
    state.mastery_confidence = projection.mastery_confidence
    state.attempt_count = projection.attempt_count
    state.last_assessed_at = projection.last_assessed_at
    state.calculation_version = FORMULA_VERSION
    state.source_kind = "canonical_event_replay"
    for row in state_rows:
        if row is not state:
            db.delete(row)

    review_rows = db.query(LearnerKPReviewState).filter(
        LearnerKPReviewState.learner_id == learner_id,
        LearnerKPReviewState.kp_id.in_(source_kp_ids),
    ).order_by(LearnerKPReviewState.id).all()
    review = next((row for row in review_rows if row.kp_id == canonical_kp_id), None)
    if review is None:
        review = LearnerKPReviewState(
            review_state_id=str(uuid.uuid4()), learner_id=learner_id, kp_id=canonical_kp_id
        )
        db.add(review)
        db.flush()
    review.lambda_per_day = projection.lambda_per_day
    review.recent_five_wrong_count = projection.recent_five_wrong_count
    review.consecutive_independent_correct = projection.consecutive_independent_correct
    review.consecutive_wrong_count = projection.consecutive_wrong_count
    review.review_stage = projection.review_stage
    review.stability_seconds = projection.stability_seconds
    review.retention_estimate = projection.retention_estimate
    review.last_review_at = projection.last_assessed_at
    review.next_review_at = projection.next_review_at
    review.requires_remediation = projection.requires_remediation
    review.status = "active"
    review.formula_version = FORMULA_VERSION
    tasks = db.query(ReviewTaskRecord).filter(
        ReviewTaskRecord.learner_id == learner_id,
        ReviewTaskRecord.primary_kp_id.in_(source_kp_ids),
    ).all()
    for task in tasks:
        task.primary_kp_id = canonical_kp_id
        task.review_state_id = review.review_state_id
    for row in review_rows:
        if row is not review:
            db.delete(row)

    legacy_rows = db.query(LearnerKnowledgeMastery).filter(
        LearnerKnowledgeMastery.user_id == learner_id,
        LearnerKnowledgeMastery.kp_id.in_(source_kp_ids),
    ).order_by(LearnerKnowledgeMastery.id).all()
    legacy = next((row for row in legacy_rows if row.kp_id == canonical_kp_id), None)
    if legacy is None:
        legacy = LearnerKnowledgeMastery(user_id=learner_id, kp_id=canonical_kp_id)
        db.add(legacy)
    legacy.mastery = projection.mastery_score / 100.0
    legacy.confidence = projection.mastery_confidence
    legacy.wrong_count = projection.wrong_count
    legacy.review_count = projection.review_count
    legacy.last_review_at = projection.last_assessed_at
    legacy.next_review_at = projection.next_review_at
    legacy.mastery_status = "mastered" if projection.mastery_score >= 80 else "learning"
    for row in legacy_rows:
        if row is not legacy:
            db.delete(row)

    for exam_track_id in projection.exam_track_ids:
        exam_rows = db.query(LearnerExamProgressState).filter(
            LearnerExamProgressState.learner_id == learner_id,
            LearnerExamProgressState.exam_track_id == exam_track_id,
            LearnerExamProgressState.kp_id.in_(source_kp_ids),
        ).order_by(LearnerExamProgressState.id).all()
        exam = next((row for row in exam_rows if row.kp_id == canonical_kp_id), None)
        if exam is None:
            exam = LearnerExamProgressState(
                progress_state_id=str(uuid.uuid4()),
                learner_id=learner_id,
                exam_track_id=exam_track_id,
                kp_id=canonical_kp_id,
            )
            db.add(exam)
        exam.mastery_score = projection.mastery_score
        exam.mastery_confidence = projection.mastery_confidence
        exam.attempt_count = projection.attempt_count
        exam.wrong_count = projection.wrong_count
        exam.review_count = projection.review_count
        exam.review_stage = projection.review_stage
        exam.stability_seconds = projection.stability_seconds
        exam.retention_estimate = projection.retention_estimate
        exam.last_assessed_at = projection.last_assessed_at
        exam.last_review_at = projection.last_assessed_at
        exam.next_review_at = projection.next_review_at
        exam.requires_remediation = projection.requires_remediation
        exam.calculation_version = FORMULA_VERSION
        for row in exam_rows:
            if row is not exam:
                db.delete(row)


def apply_canonical_replay(
    db: Session,
    *,
    report: dict[str, Any],
    expected_plan_hash: str,
    requested_by: str,
) -> KnowledgePointCanonicalMigration:
    if report.get("status") != "ready" or report.get("conflicts"):
        raise ValueError("blocked canonical replay cannot be applied")
    if report.get("plan_hash") != expected_plan_hash:
        raise ValueError("canonical replay plan hash mismatch")
    canonical_kp_id = str(report["canonical_kp_id"])
    source_kp_ids = tuple(str(value) for value in report["source_kp_ids"])
    fresh = build_canonical_replay_report(
        db,
        canonical_kp_id=canonical_kp_id,
        source_kp_ids=source_kp_ids,
    )
    if fresh["plan_hash"] != expected_plan_hash:
        raise ValueError("canonical replay evidence changed after dry-run")

    events = collect_canonical_replay_events(db, source_kp_ids=source_kp_ids)
    projections = replay_canonical_events(events)
    before = _state_snapshot(db, source_kp_ids)
    for projection in projections:
        _upsert_projection(
            db,
            canonical_kp_id=canonical_kp_id,
            source_kp_ids=source_kp_ids,
            projection=projection,
        )

    for source_kp_id in source_kp_ids:
        if source_kp_id == canonical_kp_id:
            continue
        register_reviewed_equivalence(
            db,
            source_kp_id=source_kp_id,
            canonical_kp_id=canonical_kp_id,
            decided_by=requested_by,
            decision_basis="reviewed_exact_duplicate_historical_replay",
            evidence={
                "migration_plan_hash": expected_plan_hash,
                "replay_version": REPLAY_VERSION,
                "canonical_label": db.query(KnowledgePoint.name).filter_by(kp_id=canonical_kp_id).scalar(),
            },
        )

    migration = KnowledgePointCanonicalMigration(
        migration_id=str(uuid.uuid4()),
        canonical_kp_id=canonical_kp_id,
        source_kp_ids_json=json.dumps(source_kp_ids, ensure_ascii=False),
        mode="apply",
        status="applied",
        plan_hash=expected_plan_hash,
        report_json=json.dumps(fresh, ensure_ascii=False),
        rollback_json=json.dumps(before, ensure_ascii=False),
        requested_by=requested_by,
        applied_at=utc_now(),
    )
    db.add(migration)
    db.flush()
    return migration

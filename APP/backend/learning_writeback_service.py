from APP.backend.time_utils import utc_now
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
import json
import uuid

from sqlalchemy.orm import Session

from APP.backend.database import (
    AuditResultRecord, EvidencePackRecord, GradingResultRecord,
    KnowledgeMasteryState, LearnerKPReviewState, LearnerKnowledgeMastery,
    LearningAttemptItemRecord, LearningAttemptRecord, LearningWritebackReceipt,
    MasteryHistoryRecord, MistakeRecord, ReviewTaskRecord,
)
from APP.backend.review_formula import (
    FORMULA_VERSION, lambda_per_day, mastery_after_attempt, stability_for_interval,
)


@dataclass(frozen=True)
class GradingWritebackCommand:
    attempt_item_id: str
    grading_artifact_id: str
    grading_artifact_version: int
    audit_id: str


@dataclass(frozen=True)
class LearningWritebackResult:
    status: str
    receipt_id: str | None
    mistake_ids: tuple[str, ...]
    mastery_updates: tuple[dict[str, Any], ...]
    review_task_ids: tuple[str, ...]
    formula_version: str


def _json(value: str, expected: type) -> Any:
    parsed = json.loads(value or ("[]" if expected is list else "{}"))
    if not isinstance(parsed, expected):
        raise ValueError("invalid persisted writeback data")
    return parsed


def _kp_ids(value: str) -> tuple[str, ...]:
    entries = _json(value, list)
    if any(not isinstance(kp_id, str) or not kp_id.strip() for kp_id in entries):
        raise ValueError("invalid persisted knowledge point ids")
    return tuple(entry.strip() for entry in entries)


def _snapshot_kp_ids(value: str) -> tuple[str, ...]:
    entries = _json(value, list)
    kp_ids = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid authoritative knowledge point snapshot")
        kp_id = entry.get("kp_id")
        if not isinstance(kp_id, str) or not kp_id.strip():
            raise ValueError("invalid authoritative knowledge point snapshot")
        kp_ids.append(kp_id.strip())
    return tuple(kp_ids)


def _result_from_receipt(receipt: LearningWritebackReceipt) -> LearningWritebackResult:
    refs = _json(receipt.effect_refs_json, dict)
    return LearningWritebackResult(
        receipt.status,
        receipt.receipt_id,
        tuple(refs.get("mistake_ids", ())),
        tuple(refs.get("mastery_updates", ())),
        tuple(refs.get("review_task_ids", ())),
        receipt.formula_version,
    )


def apply_grading_writeback(db: Session, learner_id: int, command: GradingWritebackCommand) -> LearningWritebackResult:
    item = db.query(LearningAttemptItemRecord).filter_by(attempt_item_id=command.attempt_item_id).one_or_none()
    if item is None:
        raise ValueError("attempt item not found")
    attempt = db.query(LearningAttemptRecord).filter_by(attempt_id=item.attempt_id).one_or_none()
    if attempt is None or attempt.learner_id != learner_id:
        raise ValueError("attempt learner mismatch")
    grading = db.query(GradingResultRecord).filter_by(
        artifact_id=command.grading_artifact_id,
        version=command.grading_artifact_version,
    ).one_or_none()
    if grading is None or grading.attempt_item_id != item.attempt_item_id or grading.status != "reviewed":
        raise ValueError("invalid grading artifact")
    audit = db.query(AuditResultRecord).filter_by(audit_id=command.audit_id).one_or_none()
    if audit is None or audit.source_artifact_id != grading.artifact_id or audit.source_artifact_version != grading.version:
        raise ValueError("audit artifact mismatch")
    decision = "needs_human_review" if audit.decision == "human_review" else audit.decision
    if decision != "pass":
        return LearningWritebackResult("skipped", None, (), (), (), FORMULA_VERSION)
    if audit.status not in {"completed", "reviewed"}:
        raise ValueError("audit is not persisted as complete")

    evidence = db.query(EvidencePackRecord).filter_by(pack_id=grading.evidence_pack_id).one_or_none()
    if evidence is None or evidence.user_id != learner_id:
        raise ValueError("invalid evidence pack")
    evidence_payload = _json(evidence.payload_json, dict)
    grading_payload = _json(grading.payload_json, dict)
    if evidence_payload.get("attempt_item_id") != item.attempt_item_id:
        raise ValueError("evidence association mismatch")
    if evidence_payload.get("question_version_id") != item.question_version_id or grading_payload.get("question_version_id") != item.question_version_id:
        raise ValueError("question version mismatch")
    frozen_kps = _snapshot_kp_ids(item.kp_snapshot_json)
    grading_kps = _kp_ids(grading.kp_ids_json)
    evidence_kps = set(_kp_ids(evidence.resolved_kp_ids_json))
    if not grading_kps:
        return LearningWritebackResult("degraded", None, (), (), (), FORMULA_VERSION)
    if not set(grading_kps) <= set(frozen_kps) or not set(grading_kps) <= evidence_kps:
        raise ValueError("knowledge point scope mismatch")

    key = f"{item.attempt_item_id}:{grading.artifact_id}:v{grading.version}"
    receipt = db.query(LearningWritebackReceipt).filter_by(idempotency_key=key).one_or_none()
    if receipt is not None:
        return _result_from_receipt(receipt)

    now = utc_now()
    q_t = max(0.0, min(1.0, grading.score / grading.max_score)) if grading.score is not None and grading.max_score and grading.max_score > 0 else (1.0 if grading.is_correct else 0.0)
    is_correct = grading.is_correct if grading.is_correct is not None else q_t >= 0.6
    mistake_ids = []
    mastery_updates = []
    task_ids = []
    recovery_review = None
    for kp_id in grading_kps:
        state = db.query(KnowledgeMasteryState).filter_by(learner_id=learner_id, kp_id=kp_id).one_or_none()
        legacy = db.query(LearnerKnowledgeMastery).filter_by(user_id=learner_id, kp_id=kp_id).one_or_none()
        previous = state.mastery_score if state is not None else (legacy.mastery * 100 if legacy is not None else None)
        delta_days = max(0.0, (now - state.last_assessed_at).total_seconds() / 86400) if state is not None and state.last_assessed_at else 0.0
        review = db.query(LearnerKPReviewState).filter_by(learner_id=learner_id, kp_id=kp_id).one_or_none()
        rate = lambda_per_day(
            review.recent_five_wrong_count if review is not None else 0,
            review.consecutive_independent_correct if review is not None else 0,
        )
        score = mastery_after_attempt(previous_score=previous, q_t=q_t, lambda_value=rate, delta_days=delta_days)
        if state is None:
            state = KnowledgeMasteryState(mastery_state_id=str(uuid.uuid4()), learner_id=learner_id, kp_id=kp_id)
            db.add(state)
        state.mastery_score = score
        state.mastery_confidence = grading.confidence
        state.attempt_count = (state.attempt_count or 0) + 1
        state.last_assessed_at = now
        state.calculation_version = FORMULA_VERSION
        db.add(MasteryHistoryRecord(history_id=str(uuid.uuid4()), learner_id=learner_id, kp_id=kp_id, trigger_attempt_item_id=item.attempt_item_id, mastery_score=score, mastery_confidence=grading.confidence, calculation_version=FORMULA_VERSION, formula_input_json=json.dumps({"q_t": q_t, "previous_score": previous, "lambda_per_day": rate, "delta_days": delta_days})))
        if review is None:
            review = LearnerKPReviewState(review_state_id=str(uuid.uuid4()), learner_id=learner_id, kp_id=kp_id, review_stage="0")
            db.add(review)
        review.lambda_per_day = rate
        review.formula_version = FORMULA_VERSION
        review.status = "active"
        if not is_correct:
            scheduled = now + timedelta(seconds=300)
            review.next_review_at = scheduled
            review.stability_seconds = stability_for_interval(300)
            if recovery_review is None:
                recovery_review = review
        if legacy is None:
            legacy = LearnerKnowledgeMastery(user_id=learner_id, kp_id=kp_id)
            db.add(legacy)
        legacy.mastery = score / 100
        legacy.confidence = grading.confidence
        legacy.review_count = (legacy.review_count or 0) + 1
        legacy.next_review_at = review.next_review_at
        legacy.mastery_status = "mastered" if score >= 80 else "learning"
        mastery_updates.append({"kp_id": kp_id, "mastery_score": score})

    if not is_correct:
        task_id = str(uuid.uuid4())
        db.add(ReviewTaskRecord(review_task_id=task_id, learner_id=learner_id, review_state_id=recovery_review.review_state_id, primary_kp_id=recovery_review.kp_id, source_type="practice", review_type="recovery_retry", reason_codes_json=json.dumps(["wrong_answer"]), status="pending", scheduled_at=now + timedelta(seconds=300), source_attempt_item_id=item.attempt_item_id))
        task_ids.append(task_id)
        mistake = db.query(MistakeRecord).filter_by(
            user_id=learner_id,
            question_id=item.question_version_id,
            status="active",
        ).one_or_none()
        if mistake is None:
            mistake = MistakeRecord(user_id=learner_id, question_id=item.question_version_id, status="active")
            db.add(mistake)
        mistake.kp_ids_json = json.dumps(grading_kps)
        mistake.error_type = ",".join(_kp_ids(grading.error_types_json))
        mistake.summary = grading.error_reason
        db.flush()
        mistake_ids.append(str(mistake.id))
    receipt_id = str(uuid.uuid4())
    refs = {"mistake_ids": mistake_ids, "mastery_updates": mastery_updates, "review_task_ids": task_ids}
    db.add(LearningWritebackReceipt(receipt_id=receipt_id, idempotency_key=key, attempt_item_id=item.attempt_item_id, grading_artifact_id=grading.artifact_id, grading_artifact_version=grading.version, audit_id=audit.audit_id, status="applied", effect_refs_json=json.dumps(refs), formula_version=FORMULA_VERSION))
    db.flush()
    return LearningWritebackResult("applied", receipt_id, tuple(mistake_ids), tuple(mastery_updates), tuple(task_ids), FORMULA_VERSION)

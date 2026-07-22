from APP.backend.time_utils import utc_now
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any, Callable
import json
import uuid

from sqlalchemy.orm import Session

from APP.backend.database import (
    AuditResultRecord,
    EvidencePackRecord,
    GradingResultRecord,
    LearningAttemptItemRecord,
    LearningAttemptRecord,
)
from APP.backend.learning_writeback_service import (
    GradingWritebackCommand,
    LearningWritebackResult,
    apply_grading_writeback,
)
from APP.backend.training_service import grade_practice_submission


@dataclass(frozen=True)
class GradePracticeCommand:
    learner_id: int
    source_channel: str
    source_task_id: str | None
    request_id: str
    question_version_id: str
    question_type: str
    stem: str
    submitted_answer: str
    standard_answer: str
    rubric: str
    kp_ids: tuple[str, ...]
    difficulty: int
    duration_sec: int | None
    hint_used: bool
    profile: dict[str, Any]
    memories: tuple[dict[str, Any], ...]
    kp_names: tuple[str, ...] = ()
    attempt_type: str = "practice"


@dataclass(frozen=True)
class GradePracticeResult:
    attempt_id: str
    attempt_item_id: str
    grading_artifact_id: str | None
    grading_artifact_version: int | None
    audit_id: str | None
    grading_payload: dict[str, Any] | None
    audit: dict[str, Any] | None
    writeback: LearningWritebackResult | None
    presentation: dict[str, Any]


def _command(
    learner_id: int,
    source_channel: str,
    source_task_id: str | None,
    request_id: str,
    data: dict[str, Any],
    profile: dict[str, Any],
    memories: list[dict[str, Any]],
) -> GradePracticeCommand:
    return GradePracticeCommand(
        learner_id=learner_id,
        source_channel=source_channel,
        source_task_id=source_task_id,
        request_id=request_id,
        question_version_id=str(data.get("question_version_id") or data.get("question_id") or ""),
        question_type=str(data.get("question_type") or data.get("type") or ""),
        stem=str(data.get("stem") or ""),
        submitted_answer=str(
            data.get("submitted_answer") or data.get("student_answer") or data.get("answer") or ""
        ),
        standard_answer=str(data.get("standard_answer") or ""),
        rubric=str(data.get("rubric") or ""),
        kp_ids=tuple(data.get("kp_ids") or data.get("knowledge_points") or ()),
        kp_names=tuple(data.get("knowledge_point_names") or ()),
        difficulty=int(data.get("difficulty") or 1),
        duration_sec=data.get("duration_sec"),
        hint_used=bool(data.get("hint_used", False)),
        profile=dict(profile),
        memories=tuple(dict(memory) for memory in memories),
    )


def from_legacy_route_request(
    learner_id: int,
    request: dict[str, Any],
    *,
    profile: dict[str, Any],
    memories: list[dict[str, Any]],
    request_id: str,
) -> GradePracticeCommand:
    return _command(learner_id, "legacy_route", None, request_id, request, profile, memories)


def from_workspace_request(
    learner_id: int,
    request: dict[str, Any],
    *,
    profile: dict[str, Any],
    memories: list[dict[str, Any]],
    request_id: str,
) -> GradePracticeCommand:
    return _command(
        learner_id,
        "workspace",
        str(request.get("task_id") or "") or None,
        request_id,
        request.get("inputs") or {},
        profile,
        memories,
    )


def _submission(command: GradePracticeCommand) -> dict[str, Any]:
    return {
        "question_id": command.question_version_id,
        "question_type": command.question_type,
        "stem": command.stem,
        "answer": command.submitted_answer,
        "student_answer": command.submitted_answer,
        "submitted_answer": command.submitted_answer,
        "standard_answer": command.standard_answer,
        "rubric": command.rubric,
        "knowledge_points": list(command.kp_ids),
        "knowledge_point_names": list(command.kp_names),
        "difficulty": command.difficulty,
    }


def _number(value: Any, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"invalid {name}")
    number = float(value)
    if (minimum is not None and number < minimum) or (maximum is not None and number > maximum):
        raise ValueError(f"invalid {name}")
    return number


def _normalize(payload: Any, *, require_audit: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("malformed grading payload")
    supplied_audit = payload.get("audit")
    if isinstance(payload.get("grading"), dict):
        legacy = payload["grading"]
        legacy_required = ("score", "is_correct", "error_type", "analysis", "standard_answer")
        if any(key not in legacy for key in legacy_required):
            raise ValueError("malformed grading payload")
        raw_grading = {
            "score": legacy["score"],
            "max_score": 100,
            "is_correct": legacy["is_correct"],
            "error_types": [] if legacy["is_correct"] is True else [legacy["error_type"]],
            "error_reason": "" if legacy["is_correct"] is True else legacy["analysis"],
            "confidence": 0.91,
            "feedback": legacy["analysis"],
            "standard_answer": legacy["standard_answer"],
        }
    else:
        required = ("score", "max_score", "is_correct", "error_types", "error_reason", "confidence")
        if any(key not in payload for key in required):
            raise ValueError("malformed grading payload")
        raw_grading = {
            key: payload[key]
            for key in (*required, "feedback", "mistake_record", "dimension_scores")
            if key in payload
        }
    if not isinstance(raw_grading["is_correct"], bool) or not isinstance(raw_grading["error_types"], list):
        raise ValueError("malformed grading payload")
    score = _number(raw_grading["score"], "score", minimum=0)
    max_score = _number(raw_grading["max_score"], "max_score", minimum=0)
    if max_score <= 0 or score > max_score:
        raise ValueError("invalid score range")
    grading = {
        "score": score,
        "max_score": max_score,
        "is_correct": raw_grading["is_correct"],
        "error_types": [str(error_type) for error_type in raw_grading["error_types"]],
        "error_reason": str(raw_grading["error_reason"]),
        "confidence": _number(raw_grading["confidence"], "confidence", minimum=0, maximum=1),
    }
    for key in ("feedback", "mistake_record", "standard_answer", "dimension_scores"):
        if key in raw_grading:
            grading[key] = raw_grading[key]
    if supplied_audit is None:
        if require_audit:
            audit = {
                "decision": "needs_human_review",
                "reason": "case grading requires an explicit audit",
                "confidence": grading["confidence"],
            }
        else:
            audit = {
                "decision": "pass",
                "reason": "legacy grading payload structurally complete",
                "confidence": grading["confidence"],
            }
    elif isinstance(supplied_audit, dict) and supplied_audit.get("decision") in {"pass", "revise", "reject", "needs_human_review", "human_review"}:
        decision = "needs_human_review" if supplied_audit["decision"] == "human_review" else supplied_audit["decision"]
        audit = {
            "decision": decision,
            "reason": str(supplied_audit.get("reason") or ""),
            "confidence": _number(supplied_audit.get("confidence", grading["confidence"]), "audit confidence", minimum=0, maximum=1),
        }
    else:
        raise ValueError("malformed audit payload")
    return grading, audit


def _presentation(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in ("remediation", "agent_trace", "mistake_record")
        if key in payload
    }


def _structured_rubric(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def apply_practice_grading(
    db: Session,
    command: GradePracticeCommand,
    *,
    runner: Callable[..., dict[str, Any]] = grade_practice_submission,
    writeback: Callable[..., LearningWritebackResult] = apply_grading_writeback,
    before_commit: Callable[[GradePracticeResult], None] | None = None,
    atomic: bool = False,
    before_atomic_write: Callable[[], None] | None = None,
    require_audit: bool = False,
) -> GradePracticeResult:
    runner_payload = None
    if atomic:
        try:
            runner_payload = runner(
                profile=dict(command.profile),
                memories=[dict(memory) for memory in command.memories],
                submission=_submission(command),
            )
        except Exception:
            db.rollback()
            raise
        grading_payload, audit_payload = _normalize(runner_payload, require_audit=require_audit)
        presentation = _presentation(runner_payload)
        if before_atomic_write is not None:
            before_atomic_write()

    attempt_id = str(uuid.uuid4())
    attempt_item_id = str(uuid.uuid4())
    attempt = LearningAttemptRecord(
        attempt_id=attempt_id,
        learner_id=command.learner_id,
        attempt_type=command.attempt_type,
        source_task_id=command.source_task_id or "",
        request_id=command.request_id,
        status="submitted",
        submitted_at=utc_now(),
        source_kind=command.source_channel,
    )
    item = LearningAttemptItemRecord(
        attempt_item_id=attempt_item_id,
        attempt_id=attempt_id,
        question_version_id=command.question_version_id,
        submitted_answer=command.submitted_answer,
        duration_sec=command.duration_sec or 0,
        hint_used=command.hint_used,
        kp_snapshot_json=json.dumps([
            {"kp_id": kp_id, "relation_type": "primary", "confidence": 1.0}
            for kp_id in command.kp_ids
        ]),
        source_kind=command.source_channel,
    )
    try:
        db.add(attempt)
        db.flush()
        db.add(item)
        if atomic:
            db.flush()
        else:
            db.commit()
    except Exception:
        db.rollback()
        raise

    if not atomic:
        try:
            runner_payload = runner(
                profile=dict(command.profile),
                memories=[dict(memory) for memory in command.memories],
                submission=_submission(command),
            )
        except Exception:
            raise
        grading_payload, audit_payload = _normalize(runner_payload, require_audit=require_audit)
        presentation = _presentation(runner_payload)

    artifact_id = str(uuid.uuid4())
    audit_id = str(uuid.uuid4())
    pack_id = str(uuid.uuid4())
    grading_payload = {**grading_payload, "question_version_id": command.question_version_id}
    structured_rubric = _structured_rubric(command.rubric)
    if structured_rubric is not None:
        grading_payload["rubric"] = structured_rubric
    evidence_payload = {
        "attempt_item_id": attempt_item_id,
        "question_version_id": command.question_version_id,
    }
    evidence = EvidencePackRecord(
        pack_id=pack_id,
        user_id=command.learner_id,
        query=command.stem,
        resolved_kp_ids_json=json.dumps(command.kp_ids),
        candidate_kp_ids_json=json.dumps(command.kp_ids),
        payload_json=json.dumps(evidence_payload),
    )
    grading = GradingResultRecord(
        artifact_id=artifact_id,
        attempt_item_id=attempt_item_id,
        version=1,
        score=grading_payload["score"],
        max_score=grading_payload["max_score"],
        is_correct=grading_payload["is_correct"],
        error_types_json=json.dumps(grading_payload["error_types"]),
        error_reason=str(grading_payload["error_reason"]),
        kp_ids_json=json.dumps(command.kp_ids),
        evidence_pack_id=pack_id,
        confidence=grading_payload["confidence"],
        status="reviewed",
        payload_json=json.dumps(grading_payload),
    )
    audit = AuditResultRecord(
        audit_id=audit_id,
        source_artifact_id=artifact_id,
        source_artifact_version=1,
        decision=audit_payload["decision"],
        reason=str(audit_payload.get("reason") or ""),
        confidence=float(audit_payload.get("confidence", grading_payload["confidence"])),
        status="completed",
        payload_json=json.dumps(audit_payload),
    )
    try:
        db.add_all([evidence, grading])
        db.flush()
        db.add(audit)
        db.flush()
        writeback_result = None
        if audit.decision == "pass":
            writeback_result = writeback(
                db,
                command.learner_id,
                GradingWritebackCommand(attempt_item_id, artifact_id, 1, audit_id),
            )
        result = GradePracticeResult(
            attempt_id,
            attempt_item_id,
            artifact_id,
            1,
            audit_id,
            grading_payload,
            audit_payload,
            writeback_result,
            presentation,
        )
        if before_commit is not None:
            before_commit(result)
        if not atomic:
            db.commit()
    except Exception:
        db.rollback()
        raise

    return result

from __future__ import annotations
from APP.backend.time_utils import utc_now

import json
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from APP.backend.database import (
    KnowledgePoint,
    LearningActivityRecord,
    LearningAttemptItemRecord,
    LearningAttemptRecord,
    GradingResultRecord,
    PaperAnswerRecord,
    PaperInstanceRecord,
    PaperItemRecord,
    PaperSubmissionRecord,
    QuestionVersionRecord,
    QuestionKPLinkRecord,
)
from APP.backend.grading_application_service import GradePracticeCommand, apply_practice_grading
from APP.backend.learning_workshop_service import (
    _normalized_item_scores,
    ensure_paper_question_authority,
)
from APP.backend.system_data_service import rebuild_system_data
from APP.backend.training_service import grade_practice_submission


class PaperSubmissionNotFound(ValueError):
    pass


class PaperSubmissionInvalid(ValueError):
    pass


def _paper(db: Session, learner_id: int, paper_id: str) -> PaperInstanceRecord:
    paper = db.query(PaperInstanceRecord).filter(
        PaperInstanceRecord.paper_id == paper_id,
        PaperInstanceRecord.learner_id == learner_id,
        PaperInstanceRecord.status.in_(("published", "grading", "submitted")),
    ).one_or_none()
    if paper is None:
        raise PaperSubmissionNotFound("paper was not found")
    return paper


def _items(db: Session, paper_id: str) -> list[PaperItemRecord]:
    return db.query(PaperItemRecord).filter_by(paper_id=paper_id).order_by(PaperItemRecord.position.asc()).all()


def _normalize_paper_scores(paper: PaperInstanceRecord, items: list[PaperItemRecord]) -> float:
    if not items:
        return 0.0
    try:
        blueprint = json.loads(paper.blueprint_json or "{}")
    except (TypeError, ValueError):
        blueprint = {}
    current = [float(item.max_score_snapshot or 0.0) for item in items]
    try:
        target = float(blueprint.get("total_score") or 0.0)
    except (TypeError, ValueError):
        target = 0.0
    if target <= 0:
        legacy_per_item_hundreds = len(current) > 1 and all(abs(score - 100.0) <= 0.005 for score in current)
        target = 100.0 if legacy_per_item_hundreds else (sum(current) or 100.0)
    if abs(sum(current) - target) > 0.005:
        scores = _normalized_item_scores(
            [{"score": score if score > 0 else None} for score in current],
            {"total_score": target},
            {},
        )
        for item, score in zip(items, scores):
            item.max_score_snapshot = score
        current = scores
    return round(sum(current), 2)


def _kp_names(db: Session, kp_ids: list[str]) -> list[str]:
    rows = db.query(KnowledgePoint).filter(KnowledgePoint.kp_id.in_(kp_ids)).all() if kp_ids else []
    names = {str(row.kp_id): str(row.name) for row in rows if str(row.name or "").strip()}
    return [names[kp_id] for kp_id in kp_ids if kp_id in names]


def _difficulty_source(source_kind: str | None) -> str:
    if source_kind == "agent_audited":
        return "agent_blueprint"
    if source_kind in {"formal_question_bank", "question_bank", "question_bank_snapshot"}:
        return "question_bank_snapshot"
    if source_kind == "variation":
        return "source_question"
    return source_kind or "paper_snapshot"


def _normalize_submission_result(
    result: dict[str, Any] | None,
    items: list[PaperItemRecord],
) -> tuple[dict[str, Any] | None, bool]:
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        return result, False
    scores = {
        item.paper_item_id: round(float(item.max_score_snapshot or 0.0), 2)
        for item in items
    }
    changed = False
    normalized_items: list[dict[str, Any]] = []
    for row in result["items"]:
        if not isinstance(row, dict):
            normalized_items.append(row)
            continue
        normalized = dict(row)
        item_id = str(row.get("paper_item_id") or "")
        target_max = scores.get(item_id)
        if target_max is not None:
            try:
                old_max = float(row.get("max_score") or 0.0)
                old_score = float(row.get("score") or 0.0)
            except (TypeError, ValueError):
                old_max, old_score = 0.0, 0.0
            target_score = round(target_max * max(0.0, min(1.0, old_score / old_max)), 2) if old_max > 0 else 0.0
            if abs(old_max - target_max) > 0.005 or abs(old_score - target_score) > 0.005:
                changed = True
            normalized["max_score"] = target_max
            normalized["score"] = target_score
            normalized.setdefault("is_correct", target_max > 0 and abs(target_score - target_max) <= 0.005)
        normalized_items.append(normalized)
    normalized_result = dict(result)
    normalized_result["items"] = normalized_items
    total = round(sum(float(row.get("score") or 0.0) for row in normalized_items if isinstance(row, dict)), 2)
    maximum = round(sum(float(row.get("max_score") or 0.0) for row in normalized_items if isinstance(row, dict)), 2)
    if normalized_result.get("score") != total or normalized_result.get("max_score") != maximum:
        changed = True
    normalized_result["score"] = total
    normalized_result["max_score"] = maximum
    return normalized_result, changed


def _hydrate_submission_result(
    db: Session,
    paper: PaperInstanceRecord,
    items: list[PaperItemRecord],
    result: dict[str, Any] | None,
    answers: dict[str, str],
) -> tuple[dict[str, Any] | None, bool]:
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        return result, False
    by_id = {item.paper_item_id: item for item in items}
    changed = False
    hydrated_rows: list[dict[str, Any]] = []
    for row in result["items"]:
        if not isinstance(row, dict):
            hydrated_rows.append(row)
            continue
        hydrated = dict(row)
        item = by_id.get(str(row.get("paper_item_id") or ""))
        if item is None:
            hydrated_rows.append(hydrated)
            continue
        authority = ensure_paper_question_authority(db, item)
        attempt_grading = db.query(LearningAttemptItemRecord, GradingResultRecord).join(
            LearningAttemptRecord,
            LearningAttemptRecord.attempt_id == LearningAttemptItemRecord.attempt_id,
        ).join(
            GradingResultRecord,
            GradingResultRecord.attempt_item_id == LearningAttemptItemRecord.attempt_item_id,
        ).filter(
            LearningAttemptRecord.learner_id == paper.learner_id,
            LearningAttemptRecord.source_task_id == paper.paper_id,
            LearningAttemptItemRecord.question_version_id == item.question_version_id,
        ).order_by(GradingResultRecord.id.desc()).first()
        grading_row = attempt_grading[1] if attempt_grading else None
        try:
            grading_payload = json.loads(grading_row.payload_json or "{}") if grading_row else {}
        except (TypeError, ValueError):
            grading_payload = {}
        explanation = str(
            hydrated.get("explanation")
            or authority.analysis
            or grading_payload.get("question_explanation")
            or grading_payload.get("feedback")
            or grading_payload.get("error_reason")
            or ""
        ).strip()
        additions = {
            "submitted_answer": hydrated.get("submitted_answer", answers.get(item.paper_item_id, "")),
            "standard_answer": hydrated.get("standard_answer", item.standard_answer_snapshot),
            "explanation": explanation,
            "grading_analysis": hydrated.get("grading_analysis") or str(
                grading_payload.get("feedback") or grading_payload.get("error_reason") or ""
            ),
            "difficulty": hydrated.get("difficulty", int(item.standard_difficulty or 2)),
            "difficulty_source": hydrated.get("difficulty_source", _difficulty_source(item.source_kind)),
            "is_correct": hydrated.get("is_correct", bool(grading_row.is_correct) if grading_row else False),
        }
        for key, value in additions.items():
            if key not in hydrated or hydrated.get(key) != value:
                hydrated[key] = value
                changed = True
        if explanation and not str(authority.analysis or "").strip():
            authority.analysis = explanation
        hydrated_rows.append(hydrated)
    hydrated_result = dict(result)
    hydrated_result["items"] = hydrated_rows
    return hydrated_result, changed


def _decode_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed) else []


def _decode_options(value: str) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _timing(paper: PaperInstanceRecord) -> dict[str, Any]:
    duration = max(1, int(paper.duration_minutes or 60))
    paused = paper.paused_remaining_seconds is not None
    if paper.started_at is None and paper.status == "published" and not paused:
        paper.started_at = utc_now()
        paper.expires_at = paper.started_at + timedelta(minutes=duration)
    remaining = max(0, int(paper.paused_remaining_seconds)) if paused else None
    expired = False
    if not paused and paper.expires_at is not None:
        remaining = max(0, int((paper.expires_at - utc_now()).total_seconds()))
        expired = remaining == 0
    return {
        "duration_minutes": duration,
        "started_at": paper.started_at.isoformat() if paper.started_at else None,
        "expires_at": paper.expires_at.isoformat() if paper.expires_at else None,
        "remaining_seconds": remaining,
        "expired": expired,
        "paused": paused,
        "paused_at": paper.paused_at.isoformat() if paper.paused_at else None,
    }


def pause_paper_timer(db: Session, learner_id: int, paper_id: str) -> dict[str, Any]:
    paper = _paper(db, learner_id, paper_id)
    if paper.status != "published":
        raise PaperSubmissionInvalid("paper timer can only be paused before submission")
    timing = _timing(paper)
    if timing["expired"]:
        raise PaperSubmissionInvalid("paper timer has expired")
    if not timing["paused"]:
        paper.paused_remaining_seconds = max(0, int(timing["remaining_seconds"] or 0))
        paper.paused_at = utc_now()
        paper.expires_at = None
        db.commit()
    return get_owned_paper(db, learner_id, paper_id)


def resume_paper_timer(db: Session, learner_id: int, paper_id: str) -> dict[str, Any]:
    paper = _paper(db, learner_id, paper_id)
    if paper.status != "published":
        raise PaperSubmissionInvalid("paper timer can only be resumed before submission")
    if paper.paused_remaining_seconds is not None:
        remaining = max(0, int(paper.paused_remaining_seconds))
        if remaining == 0:
            raise PaperSubmissionInvalid("paper timer has expired")
        now = utc_now()
        paper.expires_at = now + timedelta(seconds=remaining)
        paper.paused_remaining_seconds = None
        paper.paused_at = None
        db.commit()
    return get_owned_paper(db, learner_id, paper_id)


def get_owned_paper(db: Session, learner_id: int, paper_id: str) -> dict[str, Any]:
    paper = _paper(db, learner_id, paper_id)
    timing = _timing(paper)
    db.commit()
    answers = {
        answer.paper_item_id: answer.answer
        for answer in db.query(PaperAnswerRecord).filter_by(paper_id=paper.paper_id, learner_id=learner_id).all()
    }
    latest_submission = db.query(PaperSubmissionRecord).filter_by(
        paper_id=paper.paper_id, learner_id=learner_id, status="completed",
    ).order_by(PaperSubmissionRecord.id.desc()).first()
    items = _items(db, paper.paper_id)
    total_score = _normalize_paper_scores(paper, items)
    result = json.loads(latest_submission.result_json or "{}") if latest_submission else None
    result, result_changed = _normalize_submission_result(result, items)
    result, detail_changed = _hydrate_submission_result(db, paper, items, result, answers)
    result_changed = result_changed or detail_changed
    if latest_submission is not None and result_changed:
        latest_submission.result_json = json.dumps(result, ensure_ascii=False)
    db.commit()
    return {
        "paper_id": paper.paper_id,
        "title": paper.title,
        "status": paper.status,
        "timing": timing,
        "result": result,
        "total_score": total_score,
        "items": [{
            "paper_item_id": item.paper_item_id,
            "position": item.position,
            "question_version_id": item.question_version_id,
            "question_type": item.question_type,
            "stem": item.stem_snapshot,
            "options": _decode_options(item.options_snapshot_json),
            "kp_ids": _decode_list(item.kp_snapshot_json),
            "kp_names": _kp_names(db, _decode_list(item.kp_snapshot_json)),
            "difficulty": item.standard_difficulty,
            "difficulty_source": _difficulty_source(item.source_kind),
            "max_score": float(item.max_score_snapshot or 100.0),
            "answer": answers.get(item.paper_item_id, ""),
        } for item in items],
    }


def save_paper_answers(db: Session, learner_id: int, paper_id: str, answers: dict[str, str]) -> dict[str, Any]:
    paper = _paper(db, learner_id, paper_id)
    if paper.status != "published":
        raise PaperSubmissionInvalid("paper is no longer accepting answers")
    if not isinstance(answers, dict) or not answers:
        raise PaperSubmissionInvalid("answers are required")
    items = {item.paper_item_id: item for item in _items(db, paper_id)}
    if set(answers) - set(items) or any(not isinstance(value, str) or len(value) > 8000 for value in answers.values()):
        raise PaperSubmissionInvalid("answers are invalid")
    existing = {
        answer.paper_item_id: answer
        for answer in db.query(PaperAnswerRecord).filter(
            PaperAnswerRecord.paper_id == paper_id,
            PaperAnswerRecord.learner_id == learner_id,
            PaperAnswerRecord.paper_item_id.in_(answers),
        ).all()
    }
    for item_id, answer in answers.items():
        if item_id in existing:
            existing[item_id].answer = answer
        else:
            db.add(PaperAnswerRecord(
                paper_id=paper_id,
                paper_item_id=item_id,
                learner_id=learner_id,
                answer=answer,
            ))
    db.commit()
    return get_owned_paper(db, learner_id, paper_id)


def _authority(db: Session, item: PaperItemRecord) -> tuple[str, list[str]]:
    if not item.standard_answer_snapshot:
        raise PaperSubmissionInvalid("paper question is unavailable")
    kp_ids = _decode_list(item.kp_snapshot_json)
    if not kp_ids:
        kp_ids = [value for value, in db.query(QuestionKPLinkRecord.kp_id).filter_by(
            question_version_id=item.question_version_id,
            status="active",
        ).order_by(QuestionKPLinkRecord.id.asc()).all()]
        if kp_ids:
            item.kp_snapshot_json = json.dumps(kp_ids, ensure_ascii=False)
    return item.standard_answer_snapshot, kp_ids


def submit_paper(
    db: Session,
    learner_id: int,
    paper_id: str,
    request_id: str,
    *,
    runner: Callable[..., dict[str, Any]] = grade_practice_submission,
) -> dict[str, Any]:
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 120:
        raise PaperSubmissionInvalid("request id is invalid")
    paper = _paper(db, learner_id, paper_id)
    normalized_request_id = request_id.strip()
    existing = db.query(PaperSubmissionRecord).filter_by(
        paper_id=paper_id, learner_id=learner_id, request_id=normalized_request_id,
    ).one_or_none()
    if existing is not None:
        return json.loads(existing.result_json or "{}")
    if paper.status == "submitted":
        completed = db.query(PaperSubmissionRecord).filter_by(
            paper_id=paper_id, learner_id=learner_id, status="completed",
        ).order_by(PaperSubmissionRecord.id.desc()).first()
        if completed is not None:
            return json.loads(completed.result_json or "{}")
        raise PaperSubmissionInvalid("paper submission is unavailable")
    items = _items(db, paper_id)
    _normalize_paper_scores(paper, items)
    answers = {
        answer.paper_item_id: answer.answer
        for answer in db.query(PaperAnswerRecord).filter_by(paper_id=paper_id, learner_id=learner_id).all()
    }
    if not items or set(answers) != {item.paper_item_id for item in items}:
        raise PaperSubmissionInvalid("all paper answers are required")
    claimed = db.query(PaperInstanceRecord).filter(
        PaperInstanceRecord.paper_id == paper_id,
        PaperInstanceRecord.learner_id == learner_id,
        PaperInstanceRecord.status == "published",
    ).update({PaperInstanceRecord.status: "grading"}, synchronize_session=False)
    if claimed != 1:
        raise PaperSubmissionInvalid("paper submission is in progress")
    results = []
    try:
        for item in items:
            authority = ensure_paper_question_authority(db, item)
            standard_answer, kp_ids = _authority(db, item)
            command = GradePracticeCommand(
                learner_id=learner_id,
                source_channel="paper_submission",
                source_task_id=paper_id,
                request_id=f"{normalized_request_id}:{item.paper_item_id}",
                question_version_id=item.question_version_id,
                question_type=item.question_type,
                stem=item.stem_snapshot,
                submitted_answer=answers[item.paper_item_id],
                standard_answer=standard_answer,
                rubric="",
                kp_ids=tuple(kp_ids),
                kp_names=tuple(_kp_names(db, kp_ids)),
                difficulty=item.standard_difficulty,
                duration_sec=None,
                hint_used=False,
                profile={},
                memories=(),
                attempt_type="paper",
            )
            graded = apply_practice_grading(db, command, runner=runner, atomic=True, require_audit=True)
            grading = dict(graded.grading_payload or {})
            raw_score = float(grading.get("score") or 0.0)
            raw_maximum = float(grading.get("max_score") or 0.0)
            item_maximum = float(item.max_score_snapshot or 100.0)
            weighted_score = (
                item_maximum * max(0.0, min(1.0, raw_score / raw_maximum))
                if raw_maximum > 0
                else 0.0
            )
            results.append({
                "paper_item_id": item.paper_item_id,
                "score": round(weighted_score, 2),
                "max_score": round(item_maximum, 2),
                "is_correct": bool(grading.get("is_correct")),
                "submitted_answer": answers[item.paper_item_id],
                "standard_answer": standard_answer,
                "explanation": str(
                    grading.get("question_explanation")
                    or authority.analysis
                    or grading.get("feedback")
                    or grading.get("error_reason")
                    or ""
                ),
                "grading_analysis": str(
                    grading.get("feedback") or grading.get("error_reason") or ""
                ),
                "difficulty": int(item.standard_difficulty or 2),
                "difficulty_source": _difficulty_source(authority.source_kind or item.source_kind),
                "audit": graded.audit,
                "writeback": graded.writeback.status if graded.writeback else "skipped",
                "mistake_ids": list(graded.writeback.mistake_ids) if graded.writeback else [],
            })
        total = sum(item["score"] for item in results)
        maximum = sum(item["max_score"] for item in results)
        result = {"paper_id": paper_id, "status": "completed", "score": total, "max_score": maximum, "items": results}
        db.add(PaperSubmissionRecord(
            paper_id=paper_id,
            learner_id=learner_id,
            request_id=normalized_request_id,
            status="completed",
            result_json=json.dumps(result, ensure_ascii=False),
        ))
        db.query(PaperInstanceRecord).filter(
            PaperInstanceRecord.paper_id == paper_id,
            PaperInstanceRecord.learner_id == learner_id,
            PaperInstanceRecord.status == "grading",
        ).update({PaperInstanceRecord.status: "submitted"}, synchronize_session=False)
        db.add(LearningActivityRecord(
            user_id=learner_id,
            activity_type="paper_submission",
            resource_id=paper_id,
            resource_type="paper",
            completion_status="completed",
            score=total / maximum if maximum else None,
            payload_json=json.dumps({"task_type": "paper_submission", "request_id": normalized_request_id}, ensure_ascii=False),
            created_at=utc_now(),
        ))
        rebuild_system_data(db, user_id=learner_id)
        db.commit()
        return result
    except Exception:
        db.rollback()
        db.query(PaperInstanceRecord).filter(
            PaperInstanceRecord.paper_id == paper_id,
            PaperInstanceRecord.learner_id == learner_id,
            PaperInstanceRecord.status == "grading",
        ).update({PaperInstanceRecord.status: "published"}, synchronize_session=False)
        db.commit()
        raise

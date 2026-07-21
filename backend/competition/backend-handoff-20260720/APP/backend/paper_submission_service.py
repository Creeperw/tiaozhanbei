from __future__ import annotations
from APP.backend.time_utils import utc_now

import json
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from APP.backend.database import (
    LearningActivityRecord,
    PaperAnswerRecord,
    PaperInstanceRecord,
    PaperItemRecord,
    PaperSubmissionRecord,
)
from APP.backend.grading_application_service import GradePracticeCommand, apply_practice_grading
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
    if paper.started_at is None and paper.status == "published":
        paper.started_at = utc_now()
        paper.expires_at = paper.started_at + timedelta(minutes=duration)
    remaining = None
    expired = False
    if paper.expires_at is not None:
        remaining = max(0, int((paper.expires_at - utc_now()).total_seconds()))
        expired = remaining == 0
    return {
        "duration_minutes": duration,
        "started_at": paper.started_at.isoformat() if paper.started_at else None,
        "expires_at": paper.expires_at.isoformat() if paper.expires_at else None,
        "remaining_seconds": remaining,
        "expired": expired,
    }


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
    result = json.loads(latest_submission.result_json or "{}") if latest_submission else None
    return {
        "paper_id": paper.paper_id,
        "title": paper.title,
        "status": paper.status,
        "timing": timing,
        "result": result,
        "items": [{
            "paper_item_id": item.paper_item_id,
            "position": item.position,
            "question_version_id": item.question_version_id,
            "question_type": item.question_type,
            "stem": item.stem_snapshot,
            "options": _decode_options(item.options_snapshot_json),
            "kp_ids": _decode_list(item.kp_snapshot_json),
            "difficulty": item.standard_difficulty,
            "max_score": float(item.max_score_snapshot or 100.0),
            "answer": answers.get(item.paper_item_id, ""),
        } for item in _items(db, paper.paper_id)],
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


def _authority(item: PaperItemRecord) -> tuple[str, list[str]]:
    if not item.standard_answer_snapshot:
        raise PaperSubmissionInvalid("paper question is unavailable")
    return item.standard_answer_snapshot, _decode_list(item.kp_snapshot_json)


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
            standard_answer, kp_ids = _authority(item)
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
                difficulty=item.standard_difficulty,
                duration_sec=None,
                hint_used=False,
                profile={},
                memories=(),
                attempt_type="paper",
            )
            graded = apply_practice_grading(db, command, runner=runner, atomic=True, require_audit=True)
            raw_score = float(graded.grading_payload["score"] or 0.0)
            raw_maximum = float(graded.grading_payload["max_score"] or 0.0)
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
                "audit": graded.audit,
                "writeback": graded.writeback.status if graded.writeback else "skipped",
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

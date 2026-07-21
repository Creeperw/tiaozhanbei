from __future__ import annotations
from APP.backend.time_utils import utc_now

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from APP.backend.database import (
    LearningAgentContext,
    KnowledgePoint,
    LearningKnowledgePoint,
    LearningQuestion,
    LearningQuestionAttempt,
    QuestionBankItem,
    QuestionLearningStat,
    UserKnowledgeState,
)


@dataclass(frozen=True)
class PracticeOutcomeProjection:
    attempt: LearningQuestionAttempt
    stat: QuestionLearningStat
    state: UserKnowledgeState | None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item) for item in value if str(item).strip()))


def _submitted_answer(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value or "")]


def _accuracy(correct_count: int, attempt_count: int) -> float:
    return correct_count / attempt_count if attempt_count else 0.0


def resolve_controlled_practice_submission(db: Session, submission: dict[str, Any]) -> dict[str, Any] | None:
    question_id = str(submission.get("question_id") or "")
    question = db.query(QuestionBankItem).filter(
        QuestionBankItem.question_id == question_id,
        QuestionBankItem.status == "active",
    ).one_or_none()
    if question is None:
        return None

    try:
        kp_ids = _string_list(json.loads(question.kp_ids_json or "[]"))
    except (TypeError, ValueError):
        kp_ids = []
    if not kp_ids:
        return None

    registered = {
        row.kp_id
        for row in db.query(KnowledgePoint).filter(
            KnowledgePoint.kp_id.in_(kp_ids),
            KnowledgePoint.status == "active",
        ).all()
    }
    if set(kp_ids) != registered:
        return None

    for kp_id in kp_ids:
        if db.query(LearningKnowledgePoint).filter_by(kp_id=kp_id).one_or_none() is None:
            db.add(LearningKnowledgePoint(kp_id=kp_id))

    core_question = db.query(LearningQuestion).filter_by(question_id=question_id).one_or_none()
    if core_question is None:
        db.add(LearningQuestion(
            question_id=question.question_id,
            question_type=question.question_type,
            question_content=question.stem,
            answer_json=json.dumps(_submitted_answer(question.answer), ensure_ascii=False),
            explanation=question.analysis,
            difficulty=question.difficulty,
            kp_ids_json=json.dumps(kp_ids, ensure_ascii=False),
        ))

    return {
        **submission,
        "stem": question.stem,
        "standard_answer": question.answer,
        "rubric": question.analysis,
        "question_type": question.question_type,
        "knowledge_points": kp_ids,
        "difficulty": question.difficulty,
    }


def record_practice_outcome(
    db: Session,
    *,
    user_id: int,
    task_id: str | None,
    request_id: str,
    submission: dict[str, Any],
    grading: dict[str, Any],
) -> PracticeOutcomeProjection:
    question_id = str(submission.get("question_id") or "")
    if not question_id or not request_id:
        raise ValueError("practice outcome requires question_id and request_id")

    existing = db.query(LearningQuestionAttempt).filter_by(
        user_id=user_id,
        request_id=request_id,
    ).one_or_none()
    if existing is not None:
        stat = db.query(QuestionLearningStat).filter_by(user_id=user_id, question_id=question_id).one()
        state = db.query(UserKnowledgeState).filter_by(
            user_id=user_id,
            kp_id=_string_list(submission.get("knowledge_points"))[0],
        ).one_or_none()
        return PracticeOutcomeProjection(attempt=existing, stat=stat, state=state)

    is_correct = bool(grading.get("is_correct"))
    score = grading.get("score")
    reason_for_mistake = str(grading.get("error_reason") or "")
    if not is_correct and not reason_for_mistake:
        error_types = grading.get("error_types")
        reason_for_mistake = str(error_types[0]) if isinstance(error_types, list) and error_types else "练习错因"

    attempt = LearningQuestionAttempt(
        attempt_id=str(uuid.uuid4()),
        user_id=user_id,
        question_id=question_id,
        task_id=task_id,
        request_id=request_id,
        submitted_answer_json=json.dumps(_submitted_answer(submission.get("student_answer")), ensure_ascii=False),
        is_correct=is_correct,
        score=float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
        response_time_seconds=submission.get("response_time_seconds"),
        reason_for_mistake=reason_for_mistake,
        answered_at=utc_now(),
    )
    db.add(attempt)

    stat = db.query(QuestionLearningStat).filter_by(user_id=user_id, question_id=question_id).one_or_none()
    if stat is None:
        stat = QuestionLearningStat(user_id=user_id, question_id=question_id, attempt_count=0, correct_count=0)
        db.add(stat)
    stat.attempt_count = (stat.attempt_count or 0) + 1
    stat.correct_count = (stat.correct_count or 0) + int(is_correct)
    stat.answer_accuracy = _accuracy(stat.correct_count, stat.attempt_count)
    if not is_correct:
        stat.reason_for_mistake = reason_for_mistake

    state = None
    for kp_id in _string_list(submission.get("knowledge_points")):
        state = db.query(UserKnowledgeState).filter_by(user_id=user_id, kp_id=kp_id).one_or_none()
        if state is None:
            state = UserKnowledgeState(user_id=user_id, kp_id=kp_id, attempt_count=0, correct_count=0)
            db.add(state)
        state.attempt_count = (state.attempt_count or 0) + 1
        state.correct_count = (state.correct_count or 0) + int(is_correct)
        state.answer_accuracy = _accuracy(state.correct_count, state.attempt_count)
        state.knowledge_mastery = state.answer_accuracy
        state.kp_review_status = "active" if is_correct else "review"

    db.flush()
    return PracticeOutcomeProjection(attempt=attempt, stat=stat, state=state)


def record_agent_context(
    db: Session,
    *,
    user_id: int,
    session_id: str | None,
    source_agent: str,
    target_agent: str,
    purpose: str,
    user_input: str,
    tools_enabled: bool,
    files: list[Any] | None = None,
    task_id: str | None = None,
) -> LearningAgentContext:
    file_metadata = []
    for item in files or []:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, dict):
            continue
        file_id = item.get("id")
        name = item.get("name")
        if file_id or name:
            file_metadata.append({"id": str(file_id or ""), "name": str(name or "")})

    context = LearningAgentContext(
        trace_id=str(uuid.uuid4()),
        task_id=task_id,
        user_id=user_id,
        session_id=session_id,
        source_agent=source_agent,
        target_agent=target_agent,
        purpose=purpose,
        payload_json=json.dumps({
            "user_input": user_input,
            "tools_enabled": tools_enabled,
            "files": file_metadata,
        }, ensure_ascii=False),
    )
    db.add(context)
    db.flush()
    return context

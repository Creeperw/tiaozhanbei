from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy.orm import Session

from APP.backend.agent_orchestrator_service import run_agent_orchestration
from APP.backend.database import (
    AuditResultRecord,
    GradingResultRecord,
    LearningAttemptItemRecord,
    LearningAttemptRecord,
    MistakeRecord,
    QuestionKPLinkRecord,
    QuestionVersionRecord,
    VariationQuestionVersionRecord,
    VariationRubricRecord,
    VariationSetRecord,
)
from APP.backend.grading_application_service import (
    apply_practice_grading,
    from_workspace_request,
)
from APP.backend.training_orchestration_adapter import (
    OrchestrationRunner,
    TrainingOrchestrationInput,
    _persist_variation_audit,
    execute_training_orchestration,
)
from APP.backend.training_service import grade_practice_submission
from APP.backend.variation_repository import LearnerQuestionVersion, VariationRepository


class MistakeVariationNotFound(Exception):
    pass


@dataclass(frozen=True)
class _SourceSnapshot:
    mistake_id: int
    user_id: int
    source_question_version_id: str
    source_question_id: str
    source_stem: str
    source_question_type: str
    source_difficulty: int
    kp_ids: tuple[str, ...]
    attempt_id: str
    attempt_item_id: str
    source_artifact_id: str
    source_artifact_version: int
    source_audit_id: str
    source_audit_status: str
    source_audit_decision: str
    source_audit_generation: int

    def orchestration_inputs(self) -> dict[str, Any]:
        return {
            "mistake_id": self.mistake_id,
            "source_question_version_id": self.source_question_version_id,
            "source_question_id": self.source_question_id,
            "source_stem": self.source_stem,
            "source_question_type": self.source_question_type,
            "source_difficulty": self.source_difficulty,
            "kp_ids": list(self.kp_ids),
            "attempt_item_id": self.attempt_item_id,
        }


def _source(db: Session, user_id: int, mistake_id: int) -> _SourceSnapshot:
    row = db.query(MistakeRecord, QuestionVersionRecord, LearningAttemptItemRecord).join(
        QuestionVersionRecord,
        QuestionVersionRecord.question_version_id == MistakeRecord.question_version_id,
    ).join(
        LearningAttemptItemRecord,
        LearningAttemptItemRecord.attempt_item_id == MistakeRecord.attempt_item_id,
    ).join(
        LearningAttemptRecord,
        LearningAttemptRecord.attempt_id == LearningAttemptItemRecord.attempt_id,
    ).filter(
        MistakeRecord.id == mistake_id,
        MistakeRecord.user_id == user_id,
        MistakeRecord.status == "active",
        QuestionVersionRecord.status == "active",
        LearningAttemptRecord.learner_id == user_id,
        LearningAttemptItemRecord.question_version_id == MistakeRecord.question_version_id,
    ).one_or_none()
    if row is None:
        raise MistakeVariationNotFound("mistake was not found")
    mistake, question, attempt_item = row
    grading = db.query(GradingResultRecord).filter(
        GradingResultRecord.attempt_item_id == attempt_item.attempt_item_id,
    ).order_by(GradingResultRecord.id.desc()).first()
    if grading is None:
        raise MistakeVariationNotFound("mistake was not found")
    audit = db.query(AuditResultRecord).filter(
        AuditResultRecord.source_artifact_id == grading.artifact_id,
        AuditResultRecord.source_artifact_version == grading.version,
    ).order_by(AuditResultRecord.id.desc()).first()
    kp_ids = tuple(value for value, in db.query(QuestionKPLinkRecord.kp_id).filter(
        QuestionKPLinkRecord.question_version_id == question.question_version_id,
        QuestionKPLinkRecord.status == "active",
    ).all())
    if audit is None or audit.status != "completed" or audit.decision != "pass" or not kp_ids:
        raise MistakeVariationNotFound("mistake was not found")
    return _SourceSnapshot(
        mistake_id=mistake.id,
        user_id=user_id,
        source_question_version_id=question.question_version_id,
        source_question_id=question.question_id,
        source_stem=question.stem,
        source_question_type=question.question_type,
        source_difficulty=question.standard_difficulty,
        kp_ids=kp_ids,
        attempt_id=attempt_item.attempt_id,
        attempt_item_id=attempt_item.attempt_item_id,
        source_artifact_id=grading.artifact_id,
        source_artifact_version=grading.version,
        source_audit_id=audit.audit_id,
        source_audit_status=audit.status,
        source_audit_decision=audit.decision,
        source_audit_generation=grading.audit_generation,
    )


def _question_projection(question) -> dict[str, Any]:
    return {
        "question_version_id": question.question_version_id,
        "question_id": question.question_id,
        "stem": question.stem,
        "question_type": question.question_type,
        "difficulty": question.standard_difficulty,
        "kp_ids": list(question.kp_ids),
        "source_kind": question.source_kind,
    }


def available_variation_source_ids(
    db: Session,
    user_id: int,
    mistake_ids: list[int] | tuple[int, ...],
) -> set[int]:
    available = set()
    for mistake_id in mistake_ids:
        try:
            source = _source(db, user_id, int(mistake_id))
        except (MistakeVariationNotFound, TypeError, ValueError):
            continue
        available.add(source.mistake_id)
    return available


def list_available_variation_sources(db: Session, user_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    if user_id <= 0:
        return []
    mistake_ids = [mistake_id for mistake_id, in db.query(MistakeRecord.id).filter(
        MistakeRecord.user_id == user_id,
        MistakeRecord.status == "active",
    ).order_by(MistakeRecord.id.desc()).limit(limit).all()]
    sources = []
    for mistake_id in mistake_ids:
        try:
            source = _source(db, user_id, mistake_id)
        except MistakeVariationNotFound:
            continue
        sources.append({
            "mistake_id": source.mistake_id,
            "question_version_id": source.source_question_version_id,
            "stem": source.source_stem,
            "question_type": source.source_question_type,
            "difficulty": source.source_difficulty,
            "kp_ids": list(source.kp_ids),
        })
    return sources


def _owned_questions(db: Session, user_id: int):
    versions = db.query(QuestionVersionRecord).join(
        VariationQuestionVersionRecord,
        VariationQuestionVersionRecord.question_version_id == QuestionVersionRecord.question_version_id,
    ).join(
        VariationSetRecord,
        VariationSetRecord.variation_set_id == VariationQuestionVersionRecord.variation_set_id,
    ).filter(
        VariationQuestionVersionRecord.owner_user_id == user_id,
        VariationQuestionVersionRecord.scope == "user",
        VariationSetRecord.owner_user_id == user_id,
        VariationSetRecord.status == "published",
        QuestionVersionRecord.status == "active",
    ).all()
    projected = []
    for version in versions:
        kp_ids = tuple(value for value, in db.query(QuestionKPLinkRecord.kp_id).filter(
            QuestionKPLinkRecord.question_version_id == version.question_version_id,
            QuestionKPLinkRecord.status == "active",
        ).order_by(QuestionKPLinkRecord.kp_id).all())
        projected.append(LearnerQuestionVersion(
            question_version_id=version.question_version_id,
            question_id=version.question_id,
            stem=version.stem or "",
            question_type=version.question_type,
            standard_difficulty=int(version.standard_difficulty),
            kp_ids=kp_ids,
            source_kind=version.source_kind,
        ))
    return projected


def _grade_variation(
    db: Session,
    user_id: int,
    mistake_id: int,
    answer: dict[str, Any],
    grading_runner: Callable[..., dict[str, Any]],
    *,
    request_id: str,
    before_write: Callable[[], None] | None = None,
) -> dict[str, Any]:
    owned = {
        question.question_version_id: question
        for question in _owned_questions(db, user_id)
    }
    question = owned.get(answer.get("question_version_id"))
    if question is None:
        raise MistakeVariationNotFound("variation was not found")
    authority = db.query(VariationRubricRecord).join(
        VariationQuestionVersionRecord,
        VariationQuestionVersionRecord.question_version_id == VariationRubricRecord.question_version_id,
    ).join(
        VariationSetRecord,
        VariationSetRecord.variation_set_id == VariationQuestionVersionRecord.variation_set_id,
    ).filter(
        VariationRubricRecord.question_version_id == question.question_version_id,
        VariationQuestionVersionRecord.owner_user_id == user_id,
        VariationSetRecord.owner_user_id == user_id,
        VariationSetRecord.source_mistake_id == mistake_id,
        VariationSetRecord.status == "published",
    ).one_or_none()
    if authority is None:
        raise MistakeVariationNotFound("variation was not found")
    task_id = request_id
    command = from_workspace_request(
        user_id,
        {
            "task_id": task_id,
            "inputs": {
                **_question_projection(question),
                "student_answer": answer.get("student_answer", ""),
                "standard_answer": authority.standard_answer,
                "knowledge_points": list(question.kp_ids),
            },
        },
        profile={},
        memories=[],
        request_id=task_id,
    )
    db.rollback()
    result = apply_practice_grading(
        db, command, runner=grading_runner, atomic=True,
        before_atomic_write=before_write,
    )
    writeback = asdict(result.writeback) if result.writeback else None
    if writeback:
        writeback = {**writeback, "mistake_ids": list(writeback["mistake_ids"]), "mastery_updates": list(writeback["mastery_updates"]), "review_task_ids": list(writeback["review_task_ids"])}
    return {
        "grading": result.grading_payload,
        "audit": result.audit,
        "writeback": writeback,
        "attempt_id": result.attempt_id,
        "attempt_item_id": result.attempt_item_id,
    }


def apply_mistake_variations(
    db: Session,
    user_id: int,
    mistake_id: int,
    variation_count: int,
    *,
    runner: OrchestrationRunner = run_agent_orchestration,
    runtime: Any | None = None,
    answer: dict[str, Any] | None = None,
    grading_runner: Callable[..., dict[str, Any]] = grade_practice_submission,
    variation_publisher: Callable[..., Any] | None = None,
    task_id_prefix: str | None = None,
    answer_request_id: str | None = None,
    renew_lease: Callable[[], None] | None = None,
    before_persist: Callable[[], None] | None = None,
) -> dict[str, Any]:
    if isinstance(variation_count, bool) or not isinstance(variation_count, int) or not 1 <= variation_count <= 5:
        raise ValueError("variation_count must be between 1 and 5")
    source = _source(db, user_id, mistake_id)
    if answer is not None:
        if answer_request_id is None:
            raise ValueError("answer request id is required")
        return {"questions": [], "grading": _grade_variation(
            db, user_id, mistake_id, answer, grading_runner,
            request_id=answer_request_id,
            before_write=before_persist,
        )}
    source_inputs = source.orchestration_inputs()
    db.rollback()

    repository = VariationRepository(session=db)
    results = []
    try:
        for index in range(1, variation_count + 1):
            task_id = f"{task_id_prefix}:{index}" if task_id_prefix else f"MV_{uuid4().hex}"

            def publish(**content):
                publication_task_id = content.pop("variation_task_id", task_id)
                if renew_lease is not None:
                    renew_lease()
                target = variation_publisher or repository
                if isinstance(target, VariationRepository):
                    published = target.publish_variation(
                        variation_set_id=f"VS_{publication_task_id}",
                        question_version_id=content["artifact_source_id"],
                        question_id=f"variation:{publication_task_id}",
                        owner_user_id=content["owner_user_id"],
                        source_mistake_id=content["source_mistake_id"],
                        source_question_version_id=content["source_question_version_id"],
                        audit_id=content["audit_id"],
                        source_artifact_id=source.source_artifact_id,
                        source_artifact_version=source.source_artifact_version,
                        source_audit_id=source.source_audit_id,
                        source_audit_generation=source.source_audit_generation,
                        standard_answer=content["standard_answer"],
                        rubric=content["rubric"],
                        stem=content["stem"],
                        question_type=content["question_type"],
                        difficulty=content["difficulty"],
                        kp_ids=tuple(content["kp_ids"]),
                    )
                else:
                    published = target(**content)
                if renew_lease is not None:
                    renew_lease()
                return published

            value = TrainingOrchestrationInput(
                task_id=task_id,
                user_id=user_id,
                task_type="mistake_variation",
                title=f"错题变式 {index}",
                query="生成错题变式",
                inputs={**source_inputs, "variation_index": index},
                options={},
            )
            if renew_lease is not None:
                renew_lease()
            result = execute_training_orchestration(
                db=db, value=value, runtime=runtime, runner=runner,
                variation_publisher=publish, defer_variation_persistence=True,
            )
            if renew_lease is not None:
                renew_lease()
            if result["status"] != "completed":
                raise ValueError(result["audit"].get("reason") or "variation did not pass audit")
            results.append(result)
        projected = []
        if before_persist is not None:
            before_persist()
        if _source(db, user_id, mistake_id) != source:
            raise MistakeVariationNotFound("mistake was not found")
        for result in results:
            content = result["artifact"]["content"]
            publication_content = content.pop("_publication")
            current_audit_id = _persist_variation_audit(
                db,
                value=TrainingOrchestrationInput(
                    task_id=result["task_id"], user_id=user_id,
                    task_type="mistake_variation", title=result["title"],
                    query="生成错题变式", inputs=source_inputs, options={},
                ),
                artifact_source_id=publication_content["artifact_source_id"],
                candidate={**content, "answer": publication_content["standard_answer"], "analysis": publication_content["rubric"]["analysis"]},
                audit=result["audit"],
            )
            publication = publish(**publication_content, audit_id=current_audit_id)
            question_version_id = getattr(publication, "question_version_id", None)
            if not question_version_id and isinstance(publication, dict):
                question_version_id = publication.get("question_version_id")
            if not question_version_id:
                raise ValueError("variation publisher returned no question_version_id")
            projected.append({
                "question_version_id": question_version_id,
                "question_id": content.get("question_id", ""),
                "stem": content["stem"],
                "question_type": content["question_type"],
                "difficulty": content.get("difficulty"),
                "kp_ids": list(content["kp_ids"]),
                "source_kind": "variation",
            })
        db.flush()
        return {"questions": projected, "grading": None}
    except Exception:
        db.rollback()
        raise

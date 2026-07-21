from dataclasses import dataclass
import json
from typing import Callable

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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


@dataclass(frozen=True)
class PublishedVariation:
    variation_set_id: str
    owner_user_id: int
    question_version_id: str
    scope: str
    status: str


@dataclass(frozen=True)
class LearnerQuestionVersion:
    question_version_id: str
    question_id: str
    stem: str
    kp_ids: tuple[str, ...]
    question_type: str
    standard_difficulty: int
    source_kind: str


class VariationRepository:
    VERSION_ALLOCATION_ATTEMPTS = 3

    def __init__(self, session_factory: Callable[[], Session] | None = None, *, session: Session | None = None):
        if (session_factory is None) == (session is None):
            raise ValueError("provide exactly one session source")
        self._session_factory = session_factory
        self._session = session

    def _open_session(self):
        return (self._session, False) if self._session is not None else (self._session_factory(), True)

    def get_owned_mistake(self, owner_user_id: int, mistake_id: int):
        session, owned = self._open_session()
        try:
            return session.query(MistakeRecord).filter(
                MistakeRecord.id == mistake_id,
                MistakeRecord.user_id == owner_user_id,
            ).one_or_none()
        finally:
            if owned:
                session.close()

    def publish_variation(
        self,
        *,
        variation_set_id: str,
        question_version_id: str,
        question_id: str,
        owner_user_id: int,
        source_mistake_id: int,
        source_question_version_id: str,
        audit_id: str,
        source_artifact_id: str,
        source_artifact_version: int,
        source_audit_id: str,
        source_audit_generation: int,
        standard_answer: str,
        rubric: dict,
        stem: str = "",
        question_type: str = "single_choice",
        difficulty: int = 2,
        kp_ids: tuple[str, ...] = (),
        status: str = "published",
        scope: str = "user",
    ):
        identifiers = (
            variation_set_id, question_version_id, question_id,
            source_question_version_id, audit_id,
        )
        if owner_user_id <= 0 or source_mistake_id <= 0 or any(
            not isinstance(value, str) or not value.strip() for value in identifiers
        ):
            raise ValueError("variation identifiers must be nonblank and numeric ids positive")
        if status != "published" or scope != "user":
            raise ValueError("variation publish requires status=published and scope=user")

        session, owned = self._open_session()
        try:
            for attempt in range(self.VERSION_ALLOCATION_ATTEMPTS):
                try:
                    with session.begin_nested():
                        published = self._publish_variation_once(
                            session=session,
                            variation_set_id=variation_set_id,
                            question_version_id=question_version_id,
                            question_id=question_id,
                            owner_user_id=owner_user_id,
                            source_mistake_id=source_mistake_id,
                            source_question_version_id=source_question_version_id,
                            audit_id=audit_id,
                            source_artifact_id=source_artifact_id,
                            source_artifact_version=source_artifact_version,
                            source_audit_id=source_audit_id,
                            source_audit_generation=source_audit_generation,
                            standard_answer=standard_answer,
                            rubric=rubric,
                            stem=stem,
                            question_type=question_type,
                            difficulty=difficulty,
                            kp_ids=kp_ids,
                            status=status,
                            scope=scope,
                        )
                        session.flush()
                    if owned:
                        session.commit()
                    return published
                except IntegrityError as exc:
                    if not self._is_version_allocation_conflict(exc):
                        raise
                    if attempt + 1 == self.VERSION_ALLOCATION_ATTEMPTS:
                        raise RuntimeError("variation version allocation retries exhausted") from exc
        except Exception:
            if owned:
                session.rollback()
            raise
        finally:
            if owned:
                session.close()

    @staticmethod
    def _is_version_allocation_conflict(exc: IntegrityError):
        message = str(exc.orig).lower()
        return (
            "uq_question_version_question_version" in message
            or (
                "unique constraint failed" in message
                and "question_version_records.question_id" in message
                and "question_version_records.version" in message
            )
        )

    @staticmethod
    def _publish_variation_once(
        *,
        session,
        variation_set_id,
        question_version_id,
        question_id,
        owner_user_id,
        source_mistake_id,
        source_question_version_id,
        audit_id,
        source_artifact_id,
        source_artifact_version,
        source_audit_id,
        source_audit_generation,
        standard_answer,
        rubric,
        stem,
        question_type,
        difficulty,
        kp_ids,
        status,
        scope,
    ):
            mistake = session.query(MistakeRecord).filter(
                MistakeRecord.id == source_mistake_id,
                MistakeRecord.user_id == owner_user_id,
                MistakeRecord.status == "active",
            ).one_or_none()
            source_version = session.query(QuestionVersionRecord).filter(
                QuestionVersionRecord.question_version_id == source_question_version_id,
                QuestionVersionRecord.status == "active",
            ).one_or_none()
            if mistake is None or source_version is None or source_version.question_id != mistake.question_id:
                raise ValueError("source mistake and question version are not an active owned chain")
            if (
                mistake.question_version_id != source_question_version_id
                or not mistake.attempt_item_id
            ):
                raise ValueError("source mistake version does not match its attempt item")

            audit_matches = session.query(AuditResultRecord.id).filter(
                AuditResultRecord.audit_id == audit_id,
                AuditResultRecord.source_artifact_id == question_version_id,
                AuditResultRecord.decision == "pass",
                AuditResultRecord.status == "completed",
            ).first()
            attempt_matches = session.query(LearningAttemptItemRecord.id).join(
                LearningAttemptRecord,
                LearningAttemptRecord.attempt_id == LearningAttemptItemRecord.attempt_id,
            ).filter(
                LearningAttemptItemRecord.attempt_item_id == mistake.attempt_item_id,
                LearningAttemptItemRecord.question_version_id == source_question_version_id,
                LearningAttemptRecord.learner_id == owner_user_id,
            ).first()
            if audit_matches is None or attempt_matches is None:
                raise ValueError("audit is not associated with the current variation and owned source chain")

            latest_source_audit_id = session.query(AuditResultRecord.audit_id).filter(
                AuditResultRecord.source_artifact_id == source_artifact_id,
                AuditResultRecord.source_artifact_version == source_artifact_version,
            ).order_by(AuditResultRecord.id.desc()).limit(1).scalar_subquery()
            source_audit_matches = session.query(AuditResultRecord.id).filter(
                AuditResultRecord.audit_id == source_audit_id,
                AuditResultRecord.audit_id == latest_source_audit_id,
                AuditResultRecord.source_artifact_id == source_artifact_id,
                AuditResultRecord.source_artifact_version == source_artifact_version,
                AuditResultRecord.decision == "pass",
                AuditResultRecord.status == "completed",
            ).first()
            generation_claimed = session.query(GradingResultRecord).filter(
                GradingResultRecord.artifact_id == source_artifact_id,
                GradingResultRecord.version == source_artifact_version,
                GradingResultRecord.audit_generation == source_audit_generation,
            ).update(
                {GradingResultRecord.audit_generation: source_audit_generation},
                synchronize_session=False,
            )
            if source_audit_matches is None or generation_claimed != 1:
                raise ValueError("source audit changed during variation generation")

            next_version = (session.query(func.max(QuestionVersionRecord.version)).filter(
                QuestionVersionRecord.question_id == question_id,
            ).scalar() or 0) + 1
            session.add(VariationSetRecord(
                variation_set_id=variation_set_id,
                owner_user_id=owner_user_id,
                source_mistake_id=source_mistake_id,
                source_question_version_id=source_question_version_id,
                audit_id=audit_id,
                status=status,
            ))
            session.add(QuestionVersionRecord(
                question_version_id=question_version_id,
                question_id=question_id,
                version=next_version,
                stem=stem,
                question_type=question_type,
                standard_difficulty=difficulty,
                source_kind="variation",
            ))
            for index, kp_id in enumerate(kp_ids):
                session.add(QuestionKPLinkRecord(
                    question_version_id=question_version_id,
                    kp_id=kp_id,
                    is_primary=index == 0,
                    status="active",
                ))
            session.add(VariationRubricRecord(
                question_version_id=question_version_id,
                standard_answer=standard_answer,
                rubric_json=json.dumps(rubric, ensure_ascii=False),
            ))
            session.add(VariationQuestionVersionRecord(
                variation_set_id=variation_set_id,
                question_version_id=question_version_id,
                owner_user_id=owner_user_id,
                scope=scope,
            ))
            session.flush()
            return PublishedVariation(
                variation_set_id=variation_set_id,
                owner_user_id=owner_user_id,
                question_version_id=question_version_id,
                scope=scope,
                status=status,
            )

    def select_public_question_versions(self):
        session = self._session_factory()
        try:
            versions = session.query(QuestionVersionRecord).filter(
                QuestionVersionRecord.status == "active",
                ~QuestionVersionRecord.question_version_id.in_(
                    session.query(VariationQuestionVersionRecord.question_version_id)
                ),
            ).order_by(QuestionVersionRecord.question_version_id.asc()).all()
            return self._project(session, versions)
        finally:
            session.close()

    def select_owned_question_versions(self, owner_user_id: int):
        if owner_user_id <= 0:
            raise ValueError("owner_user_id must be positive")
        session = self._session_factory()
        try:
            versions = session.query(QuestionVersionRecord).join(
                VariationQuestionVersionRecord,
                VariationQuestionVersionRecord.question_version_id == QuestionVersionRecord.question_version_id,
            ).join(
                VariationSetRecord,
                VariationSetRecord.variation_set_id == VariationQuestionVersionRecord.variation_set_id,
            ).filter(
                VariationQuestionVersionRecord.owner_user_id == owner_user_id,
                VariationQuestionVersionRecord.scope == "user",
                VariationSetRecord.owner_user_id == owner_user_id,
                VariationSetRecord.status == "published",
                QuestionVersionRecord.status == "active",
            ).order_by(QuestionVersionRecord.question_version_id.asc()).all()
            return self._project(session, versions)
        finally:
            session.close()

    @staticmethod
    def _project(session, versions):
        ids = [version.question_version_id for version in versions]
        kp_ids = {}
        if ids:
            for version_id, kp_id in session.query(
                QuestionKPLinkRecord.question_version_id,
                QuestionKPLinkRecord.kp_id,
            ).filter(
                QuestionKPLinkRecord.question_version_id.in_(ids),
                QuestionKPLinkRecord.status == "active",
            ).order_by(QuestionKPLinkRecord.kp_id.asc()).all():
                kp_ids.setdefault(version_id, []).append(kp_id)
        return tuple(LearnerQuestionVersion(
            question_version_id=version.question_version_id,
            question_id=version.question_id,
            stem=version.stem or "",
            kp_ids=tuple(kp_ids.get(version.question_version_id, ())),
            question_type=version.question_type,
            standard_difficulty=int(version.standard_difficulty),
            source_kind=version.source_kind,
        ) for version in versions)

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from APP.backend.case_training_models import (
    CaseDefinitionRecord,
    CaseHelpRecord,
    CaseSessionMessageRecord,
    CaseSessionRecord,
    CaseVersionRecord,
)


MAX_CASE_MESSAGE_CONTENT_BYTES = 8192


@dataclass(frozen=True)
class CaseMessage:
    role: str
    sequence: int
    content: str
    facts: dict
    facts_expires_at: datetime | None


@dataclass(frozen=True)
class CaseSessionView:
    session_id: str
    owner_user_id: int
    case_definition_id: str
    case_version_id: str
    title: str
    visible_context: dict
    patient_context: dict
    messages: tuple[CaseMessage, ...]
    mode: str
    status: str
    learner_messages: int
    scoring_enabled: bool
    help_used: bool
    created_at: datetime
    expires_at: datetime | None


class CaseRepository:
    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    def create_case(
        self, *, case_definition_id: str, case_version_id: str, title: str,
        visible_context: dict, patient_context: dict, golden_standard: dict, rubric: dict,
    ):
        session = self._session_factory()
        try:
            session.add(CaseDefinitionRecord(
                case_definition_id=case_definition_id,
                title=title,
                visible_context_json=self._encode(visible_context),
                patient_context_json=self._encode(patient_context),
            ))
            session.add(CaseVersionRecord(
                case_version_id=case_version_id,
                case_definition_id=case_definition_id,
                golden_standard_json=self._encode(golden_standard),
                rubric_json=self._encode(rubric),
            ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def available_case_version_ids(self, *, case_type: str | None = None) -> tuple[str, ...]:
        session = self._session_factory()
        try:
            records = session.query(
                CaseVersionRecord.case_version_id,
                CaseDefinitionRecord.visible_context_json,
            ).join(
                CaseDefinitionRecord,
                CaseDefinitionRecord.case_definition_id == CaseVersionRecord.case_definition_id,
            ).order_by(CaseVersionRecord.case_version_id.asc()).all()
            return tuple(
                case_version_id
                for case_version_id, visible_context_json in records
                if case_type is None or self._decode(visible_context_json).get("case_type") == case_type
            )
        finally:
            session.close()

    def create_session(
        self,
        session_id: str,
        owner_user_id: int,
        case_version_id: str,
        *,
        mode: str = "full",
        status: str = "created",
        expires_at: datetime | None = None,
    ):
        self._write(CaseSessionRecord(
            session_id=session_id,
            owner_user_id=owner_user_id,
            case_version_id=case_version_id,
            mode=mode,
            status=status,
            expires_at=expires_at,
        ))

    def update_session_state(
        self,
        owner_user_id: int,
        session_id: str,
        *,
        status: str,
        learner_messages: int,
        scoring_enabled: bool,
        help_used: bool,
        expires_at: datetime | None,
    ):
        session = self._session_factory()
        try:
            record = self._lock_owned_session(session, owner_user_id, session_id)
            if record is None:
                raise ValueError("case session unavailable")
            record.status = status
            record.learner_messages = learner_messages
            record.scoring_enabled = int(scoring_enabled)
            record.help_used = int(help_used)
            record.expires_at = expires_at
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def claim_for_grading(
        self,
        owner_user_id: int,
        session_id: str,
        *,
        learner_messages: int,
        scoring_enabled: bool,
        help_used: bool,
        expires_at: datetime | None,
    ) -> bool:
        session = self._session_factory()
        try:
            updated = session.query(CaseSessionRecord).filter(
                CaseSessionRecord.session_id == session_id,
                CaseSessionRecord.owner_user_id == owner_user_id,
                CaseSessionRecord.status.in_(("active", "help_available")),
            ).update(
                {
                    CaseSessionRecord.status: "grading",
                    CaseSessionRecord.learner_messages: learner_messages,
                    CaseSessionRecord.scoring_enabled: int(scoring_enabled),
                    CaseSessionRecord.help_used: int(help_used),
                    CaseSessionRecord.expires_at: expires_at,
                },
                synchronize_session=False,
            )
            session.commit()
            return updated == 1
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def append_message(
        self, owner_user_id: int, session_id: str, role: str, content: str, *, sequence: int,
        facts: dict | None = None, facts_expires_at: datetime | None = None,
    ):
        if role not in {"learner", "patient"}:
            raise ValueError("invalid case message role")
        if len(content.encode("utf-8")) > MAX_CASE_MESSAGE_CONTENT_BYTES:
            raise ValueError(f"case message content exceeds {MAX_CASE_MESSAGE_CONTENT_BYTES} bytes")
        session = self._session_factory()
        try:
            if not self._lock_owned_session(session, owner_user_id, session_id):
                raise ValueError("case session unavailable")
            next_sequence = (session.query(func.max(CaseSessionMessageRecord.sequence)).filter_by(
                session_id=session_id,
            ).scalar() or 0) + 1
            if sequence != next_sequence:
                raise ValueError("invalid case message sequence")
            session.add(CaseSessionMessageRecord(
                session_id=session_id, role=role, content=content, sequence=sequence,
                facts_json=self._encode(facts or {}), facts_expires_at=facts_expires_at,
            ))
            session.commit()
        except IntegrityError:
            session.rollback()
            raise ValueError("invalid case message sequence") from None
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def save_help(self, owner_user_id: int, session_id: str, payload: dict):
        session = self._session_factory()
        try:
            if not self._lock_owned_session(session, owner_user_id, session_id):
                raise ValueError("case session unavailable")
            record = session.query(CaseHelpRecord).filter_by(session_id=session_id).one_or_none()
            if record is None:
                session.add(CaseHelpRecord(session_id=session_id, payload_json=self._encode(payload)))
            else:
                record.payload_json = self._encode(payload)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_owned_session(self, owner_user_id: int, session_id: str):
        session = self._session_factory()
        try:
            record = session.query(CaseSessionRecord, CaseVersionRecord, CaseDefinitionRecord).join(
                CaseVersionRecord, CaseVersionRecord.case_version_id == CaseSessionRecord.case_version_id,
            ).join(
                CaseDefinitionRecord,
                CaseDefinitionRecord.case_definition_id == CaseVersionRecord.case_definition_id,
            ).filter(
                CaseSessionRecord.session_id == session_id,
                CaseSessionRecord.owner_user_id == owner_user_id,
            ).one_or_none()
            if record is None:
                return None
            session_record, version, definition = record
            messages = session.query(CaseSessionMessageRecord).filter_by(
                session_id=session_id,
            ).order_by(CaseSessionMessageRecord.sequence.asc()).all()
            return CaseSessionView(
                session_id=session_record.session_id,
                owner_user_id=session_record.owner_user_id,
                case_definition_id=definition.case_definition_id,
                case_version_id=version.case_version_id,
                title=definition.title,
                visible_context=self._decode(definition.visible_context_json),
                patient_context=self._decode(definition.patient_context_json),
                messages=tuple(CaseMessage(
                    role=message.role,
                    sequence=message.sequence,
                    content=message.content,
                    facts=self._decode(message.facts_json),
                    facts_expires_at=message.facts_expires_at,
                ) for message in messages),
                mode=session_record.mode,
                status=session_record.status,
                learner_messages=session_record.learner_messages,
                scoring_enabled=bool(session_record.scoring_enabled),
                help_used=bool(session_record.help_used),
                created_at=session_record.created_at,
                expires_at=session_record.expires_at,
            )
        finally:
            session.close()

    def _lock_owned_session(self, session: Session, owner_user_id: int, session_id: str):
        return session.query(CaseSessionRecord).filter_by(
            session_id=session_id,
            owner_user_id=owner_user_id,
        ).with_for_update().one_or_none()

    def _write(self, record):
        session = self._session_factory()
        try:
            session.add(record)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _encode(value: dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _decode(value: str):
        return json.loads(value or "{}")

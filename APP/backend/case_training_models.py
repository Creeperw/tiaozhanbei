from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

from APP.backend.database import Base


class CaseDefinitionRecord(Base):
    __tablename__ = "case_definition_records"

    id = Column(Integer, primary_key=True)
    case_definition_id = Column(String(120), nullable=False, unique=True, index=True)
    title = Column(String(200), nullable=False, default="")
    visible_context_json = Column(Text, nullable=False, default="{}")
    patient_context_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class CaseVersionRecord(Base):
    __tablename__ = "case_version_records"

    id = Column(Integer, primary_key=True)
    case_version_id = Column(String(120), nullable=False, unique=True, index=True)
    case_definition_id = Column(
        String(120), ForeignKey("case_definition_records.case_definition_id"), nullable=False, index=True
    )
    golden_standard_json = Column(Text, nullable=False, default="{}")
    rubric_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class CaseSessionRecord(Base):
    __tablename__ = "case_session_records"

    id = Column(Integer, primary_key=True)
    session_id = Column(String(120), nullable=False, unique=True, index=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    case_version_id = Column(
        String(120), ForeignKey("case_version_records.case_version_id"), nullable=False, index=True
    )
    mode = Column(String(40), nullable=False, default="full")
    status = Column(String(40), nullable=False, default="created")
    learner_messages = Column(Integer, nullable=False, default=0)
    scoring_enabled = Column(Integer, nullable=False, default=1)
    help_used = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class CaseSessionMessageRecord(Base):
    __tablename__ = "case_session_message_records"
    __table_args__ = (UniqueConstraint("session_id", "sequence", name="uq_case_session_message_sequence"),)

    id = Column(Integer, primary_key=True)
    session_id = Column(String(120), ForeignKey("case_session_records.session_id"), nullable=False, index=True)
    role = Column(String(40), nullable=False)
    sequence = Column(Integer, nullable=False)
    content = Column(Text, nullable=False, default="")
    facts_json = Column(Text, nullable=False, default="{}")
    facts_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class CaseHelpRecord(Base):
    __tablename__ = "case_help_records"

    id = Column(Integer, primary_key=True)
    session_id = Column(String(120), ForeignKey("case_session_records.session_id"), nullable=False, unique=True, index=True)
    payload_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

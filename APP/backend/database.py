from APP.backend.time_utils import utc_now
import hashlib
import json
from dataclasses import dataclass

import pymysql
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, ForeignKeyConstraint, Text, Boolean, Float, UniqueConstraint, MetaData, Table, Index, event, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker, relationship
from datetime import datetime
from .config import SQLALCHEMY_DATABASE_URL, MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE

def ensure_mysql_database():
    """Create the configured MySQL database if it does not already exist."""
    conn = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, charset="utf8mb4")
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DATABASE}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()

# 仅在使用 MySQL 时确保目标数据库存在；本地走 SQLite 时跳过这次连接，
# 避免没有 MySQL 服务的本地开发机在 import 阶段就 ConnectionRefused。
if not SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    ensure_mysql_database()

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,  # 每次从连接池拿连接前，先 ping 一下数据库，如果断了就自动重连（最关键的一行）
    pool_recycle=3600,   # 每 1 小时主动回收重建连接，防止被 MySQL 服务端强制踢掉
    pool_size=10,        # 可选：常规连接数
    max_overflow=20,      # 可选：并发高时最多额外增加的连接数

)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    if engine.dialect.name == "sqlite":
        dbapi_connection.execute("PRAGMA foreign_keys = ON")


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Registers case-training record tables with the shared metadata after Base is available.
from APP.backend import case_training_models

# --- User 模型 (增加 email) ---
class UserModel(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True)
    email = Column(String(100), unique=True, index=True) # 🔥 新增
    hashed_password = Column(String(255)) # 长度改大一点以防万一
    role = Column(String(20), default="user", index=True)  # user/admin
    created_at = Column(DateTime, default=utc_now)
    
    sessions = relationship("DbSession", back_populates="owner", cascade="all, delete-orphan")

# --- Verification Code 模型 (🔥 新增) ---
class VerificationCode(Base):
    __tablename__ = "verification_codes"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(100), index=True)
    code = Column(String(6))
    purpose = Column(String(20)) # 用途: 'register' 或 'reset'
    expires_at = Column(DateTime)
    is_used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utc_now)

# --- Session 模型 ---
class DbSession(Base):
    __tablename__ = "sessions"
    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    title = Column(String(200))
    title_auto_enabled = Column(Boolean, default=True, nullable=False)
    active_leaf_message_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime, default=utc_now)
    owner = relationship("UserModel", back_populates="sessions")
    messages = relationship("DbMessage", back_populates="session", cascade="all, delete-orphan")

# --- Message 模型 ---
class DbMessage(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(36), ForeignKey("sessions.id"))
    parent_id = Column(Integer, nullable=True, index=True)
    role = Column(String(20))
    content = Column(Text)
    files = Column(Text, default="[]")
    timestamp = Column(String(20))
    created_at = Column(DateTime, default=utc_now)
    session = relationship("DbSession", back_populates="messages")

class UserProfile(Base):
    __tablename__ = "user_profiles"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, index=True)
    display_name = Column(String(100), nullable=True)
    constitution = Column(String(100), nullable=True)
    health_goals = Column(Text, default="")
    diet_restrictions = Column(Text, default="")
    exercise_preferences = Column(Text, default="")
    medical_history = Column(Text, default="")
    custom_needs = Column(Text, default="")
    survey_json = Column(Text, default="{}")
    locked_fields_json = Column(Text, default="[]")
    lock_reason_json = Column(Text, default="{}")
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    created_at = Column(DateTime, default=utc_now)

class PersonalizationMemory(Base):
    __tablename__ = "personalization_memories"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    category = Column(String(50), index=True)  # long_term / short_term / preference / feedback / note
    importance = Column(String(20), default="normal", index=True)  # important / normal
    title = Column(String(200), default="")
    content = Column(Text)
    source = Column(String(50), default="manual")
    is_active = Column(Boolean, default=True, index=True)
    expires_at = Column(DateTime, nullable=True)
    superseded_by = Column(Integer, ForeignKey("personalization_memories.id"), nullable=True, index=True)
    superseded_at = Column(DateTime, nullable=True)
    conflict_key = Column(String(120), nullable=True, index=True)
    confidence = Column(Float, default=0.8)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

class MemoryCandidate(Base):
    __tablename__ = "memory_candidates"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=True, index=True)
    title = Column(String(200), default="")
    content = Column(Text)
    importance = Column(String(20), default="normal", index=True)  # normal / low
    reason = Column(Text, default="")
    source = Column(String(50), default="auto_extract", index=True)
    status = Column(String(20), default="pending", index=True)  # pending / promoted / ignored
    promoted_memory_id = Column(Integer, ForeignKey("personalization_memories.id"), nullable=True)
    confidence = Column(Float, default=0.8)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

class MemorySummary(Base):
    __tablename__ = "memory_summaries"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=True, index=True)
    description = Column(Text)
    key_facts = Column(Text)  # JSON string
    message_from_id = Column(Integer, nullable=True)
    message_to_id = Column(Integer, nullable=True)
    compression_reason = Column(String(50), default="budget_warning")
    confidence = Column(Float, default=0.8)
    created_at = Column(DateTime, default=utc_now)

class AgentEvent(Base):
    __tablename__ = "agent_events"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=True, index=True)
    agent_name = Column(String(80), index=True)
    event_type = Column(String(80), default="run")
    input_summary = Column(Text, default="")
    output_summary = Column(Text, default="")
    payload = Column(Text, default="{}")
    created_at = Column(DateTime, default=utc_now)

class LearningActivityRecord(Base):
    __tablename__ = "learning_activity_records"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    activity_type = Column(String(80), index=True)
    resource_id = Column(String(120), default="")
    resource_type = Column(String(80), default="")
    duration_minutes = Column(Integer, default=0)
    completion_status = Column(String(50), default="unknown", index=True)
    score = Column(Float, nullable=True)
    payload_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=utc_now)
    __table_args__ = (Index("ix_learning_activity_user_created", "user_id", "created_at"),)

class TrainingTaskRecord(Base):
    __tablename__ = "training_task_records"
    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(120), unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    task_type = Column(String(80), index=True)
    title = Column(String(200), default="")
    status = Column(String(50), default="completed", index=True)
    artifact_type = Column(String(80), default="")
    artifact_json = Column(Text, default="{}")
    evidence_pack_id = Column(String(120), default="", index=True)
    evidence_pack_json = Column(Text, default="{}")
    audit_json = Column(Text, default="{}")
    trace_json = Column(Text, default="[]")
    learning_updates_json = Column(Text, default="{}")
    claim_owner = Column(String(64), nullable=True, index=True)
    claim_expires_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

class PaperInstanceRecord(Base):
    __tablename__ = "paper_instances"
    id = Column(Integer, primary_key=True, index=True)
    paper_id = Column(String(120), unique=True, nullable=False, index=True)
    task_id = Column(String(120), unique=True, nullable=False, index=True)
    orchestration_run_id = Column(String(120), default="", index=True)
    learner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(200), default="")
    status = Column(String(50), default="published", index=True)
    blueprint_json = Column(Text, default="{}")
    evidence_pack_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=utc_now)


class PaperItemRecord(Base):
    __tablename__ = "paper_items"
    id = Column(Integer, primary_key=True, index=True)
    paper_item_id = Column(String(120), unique=True, nullable=False, index=True)
    paper_id = Column(String(120), ForeignKey("paper_instances.paper_id"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    question_id = Column(String(120), nullable=False, index=True)
    question_version_id = Column(String(120), nullable=False, index=True)
    question_type = Column(String(50), default="")
    stem_snapshot = Column(Text, default="")
    standard_answer_snapshot = Column(Text, default="")
    kp_snapshot_json = Column(Text, default="[]")
    evidence_refs_json = Column(Text, default="[]")
    source_kind = Column(String(120), default="")
    standard_difficulty = Column(Integer, default=2)
    created_at = Column(DateTime, default=utc_now)
    __table_args__ = (UniqueConstraint("paper_id", "position", name="uq_paper_item_position"),)


class PaperAnswerRecord(Base):
    __tablename__ = "paper_answers"
    id = Column(Integer, primary_key=True, index=True)
    paper_id = Column(String(120), ForeignKey("paper_instances.paper_id"), nullable=False, index=True)
    paper_item_id = Column(String(120), ForeignKey("paper_items.paper_item_id"), nullable=False, index=True)
    learner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    answer = Column(Text, default="")
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    __table_args__ = (UniqueConstraint("paper_id", "paper_item_id", "learner_id", name="uq_paper_answer_owner_item"),)


class PaperSubmissionRecord(Base):
    __tablename__ = "paper_submissions"
    id = Column(Integer, primary_key=True, index=True)
    paper_id = Column(String(120), ForeignKey("paper_instances.paper_id"), nullable=False, index=True)
    learner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    request_id = Column(String(120), nullable=False, index=True)
    status = Column(String(50), default="submitted", index=True)
    result_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=utc_now)
    __table_args__ = (UniqueConstraint("paper_id", "learner_id", "request_id", name="uq_paper_submission_request"),)


class LearnerKnowledgeMastery(Base):
    __tablename__ = "learner_knowledge_mastery"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    kp_id = Column(String(120), index=True)
    mastery = Column(Float, default=0.0)
    confidence = Column(Float, default=0.8)
    wrong_count = Column(Integer, default=0)
    review_count = Column(Integer, default=0)
    last_review_at = Column(DateTime, nullable=True)
    next_review_at = Column(DateTime, nullable=True)
    mastery_status = Column(String(50), default="unknown", index=True)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    created_at = Column(DateTime, default=utc_now)


class UserLearningTarget(Base):
    __tablename__ = "user_learning_targets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    target_type = Column(String(50), nullable=False, index=True)
    exam_track_id = Column(String(120), nullable=False, index=True)
    exam_name_snapshot = Column(String(255), nullable=False)
    syllabus_version = Column(String(80), nullable=False, default="")
    exam_date = Column(DateTime, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    active_slot = Column(Integer, nullable=True, default=1)
    is_locked = Column(Boolean, nullable=False, default=True)
    lock_reason = Column(String(255), nullable=False, default="用户手动选择")
    source = Column(String(50), nullable=False, default="manual")
    archived_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    __table_args__ = (
        Index("ix_user_learning_targets_user_active", "user_id", "is_active"),
        Index(
            "uq_user_learning_targets_user_active_slot",
            "user_id",
            "active_slot",
            unique=True,
        ),
    )


class LearningAttemptRecord(Base):
    __tablename__ = "learning_attempts"
    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(String(120), unique=True, nullable=False, index=True)
    learner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    attempt_type = Column(String(80), index=True)
    source_task_id = Column(String(120), default="", index=True)
    request_id = Column(String(120), default="", index=True)
    status = Column(String(50), default="submitted", index=True)
    submitted_at = Column(DateTime, nullable=True)
    source_kind = Column(String(80), default="training_workshop", index=True)
    schema_version = Column(String(40), default="v1")
    created_at = Column(DateTime, default=utc_now)


class LearningAttemptItemRecord(Base):
    __tablename__ = "learning_attempt_items"
    id = Column(Integer, primary_key=True, index=True)
    attempt_item_id = Column(String(120), unique=True, nullable=False, index=True)
    attempt_id = Column(String(120), ForeignKey("learning_attempts.attempt_id"), nullable=False, index=True)
    question_version_id = Column(String(120), default="", index=True)
    submitted_answer = Column(Text, default="")
    duration_sec = Column(Integer, default=0)
    hint_used = Column(Boolean, default=False)
    kp_snapshot_json = Column(Text, default="[]")
    source_kind = Column(String(80), default="training_workshop", index=True)
    created_at = Column(DateTime, default=utc_now)


class GradingResultRecord(Base):
    __tablename__ = "grading_result_records"
    id = Column(Integer, primary_key=True, index=True)
    artifact_id = Column(String(120), nullable=False, index=True)
    attempt_item_id = Column(String(120), ForeignKey("learning_attempt_items.attempt_item_id"), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    score = Column(Float, nullable=True)
    max_score = Column(Float, nullable=True)
    is_correct = Column(Boolean, nullable=True)
    error_types_json = Column(Text, default="[]")
    error_reason = Column(Text, default="")
    kp_ids_json = Column(Text, default="[]")
    evidence_pack_id = Column(String(120), default="", index=True)
    confidence = Column(Float, default=0.0)
    status = Column(String(50), default="pending", index=True)
    schema_version = Column(String(40), default="v1")
    payload_json = Column(Text, default="{}")
    audit_generation = Column(Integer, nullable=False, default=0, server_default="0")
    __table_args__ = (UniqueConstraint("artifact_id", "version", name="uq_grading_result_artifact_version"),)


class AuditResultRecord(Base):
    __tablename__ = "audit_result_records"
    id = Column(Integer, primary_key=True, index=True)
    audit_id = Column(String(120), unique=True, nullable=False, index=True)
    source_artifact_id = Column(String(120), nullable=False, index=True)
    source_artifact_version = Column(Integer, nullable=False, default=1)
    decision = Column(String(50), default="pending", index=True)
    reason = Column(Text, default="")
    confidence = Column(Float, default=0.0)
    status = Column(String(50), default="pending", index=True)
    schema_version = Column(String(40), default="v1")
    payload_json = Column(Text, default="{}")
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_artifact_id", "source_artifact_version"],
            ["grading_result_records.artifact_id", "grading_result_records.version"],
        ),
        Index(
            "ix_audit_result_records_source_artifact",
            "source_artifact_id",
            "source_artifact_version",
        ),
    )


@event.listens_for(AuditResultRecord, "after_insert")
def _advance_source_audit_generation(mapper, connection, target):
    if getattr(target, "_generation_advanced_with_pending_grading", False):
        return
    connection.execute(
        GradingResultRecord.__table__.update().where(
            GradingResultRecord.artifact_id == target.source_artifact_id,
            GradingResultRecord.version == target.source_artifact_version,
        ).values(audit_generation=GradingResultRecord.audit_generation + 1)
    )


_IMMUTABLE_AUDIT_FIELDS = (
    "id",
    "audit_id",
    "source_artifact_id",
    "source_artifact_version",
    "decision",
    "reason",
    "confidence",
    "status",
    "schema_version",
    "payload_json",
)


@event.listens_for(Session, "do_orm_execute")
def _reject_bulk_audit_updates(execute_state):
    statement = execute_state.statement
    table = getattr(statement, "table", None)
    if execute_state.is_update and getattr(table, "fullname", None) == AuditResultRecord.__table__.fullname:
        raise ValueError("audit records are immutable; append a new audit result")


@event.listens_for(Session, "before_flush")
def _protect_audit_history_and_advance_pending_generations(session, flush_context, instances):
    for audit in session.dirty:
        if not isinstance(audit, AuditResultRecord):
            continue
        state = inspect(audit)
        if any(state.attrs[field].history.has_changes() for field in _IMMUTABLE_AUDIT_FIELDS):
            raise ValueError("audit records are immutable; append a new audit result")

    pending_gradings = {
        (record.artifact_id, record.version): record
        for record in session.new
        if isinstance(record, GradingResultRecord)
    }
    for audit in session.new:
        if isinstance(audit, AuditResultRecord):
            grading = pending_gradings.get((audit.source_artifact_id, audit.source_artifact_version))
            if grading is not None:
                grading.audit_generation = (grading.audit_generation or 0) + 1
                audit._generation_advanced_with_pending_grading = True


def append_audit_result(
    session: Session,
    *,
    previous_audit_id: str,
    audit_id: str,
    decision: str,
    status: str,
    reason: str = "",
    confidence: float = 0.0,
    payload_json: str = "{}",
):
    previous = session.query(AuditResultRecord).filter_by(audit_id=previous_audit_id).one()
    audit = AuditResultRecord(
        audit_id=audit_id,
        source_artifact_id=previous.source_artifact_id,
        source_artifact_version=previous.source_artifact_version,
        decision=decision,
        status=status,
        reason=reason,
        confidence=confidence,
        schema_version=previous.schema_version,
        payload_json=payload_json,
    )
    session.add(audit)
    session.flush()
    return audit


class LearningWritebackReceipt(Base):
    __tablename__ = "learning_writeback_receipts"
    id = Column(Integer, primary_key=True, index=True)
    receipt_id = Column(String(120), unique=True, nullable=False, index=True)
    idempotency_key = Column(String(200), unique=True, nullable=False, index=True)
    attempt_item_id = Column(String(120), ForeignKey("learning_attempt_items.attempt_item_id"), nullable=False, index=True)
    grading_artifact_id = Column(String(120), nullable=False, index=True)
    grading_artifact_version = Column(Integer, nullable=False, default=1)
    audit_id = Column(String(120), ForeignKey("audit_result_records.audit_id"), nullable=False, index=True)
    status = Column(String(50), default="pending", index=True)
    effect_refs_json = Column(Text, default="[]")
    formula_version = Column(String(40), default="v1")
    created_at = Column(DateTime, default=utc_now)
    __table_args__ = (
        ForeignKeyConstraint(
            ["grading_artifact_id", "grading_artifact_version"],
            ["grading_result_records.artifact_id", "grading_result_records.version"],
        ),
    )


class KnowledgeMasteryState(Base):
    __tablename__ = "knowledge_mastery_states"
    id = Column(Integer, primary_key=True, index=True)
    mastery_state_id = Column(String(120), unique=True, nullable=False, index=True)
    learner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kp_id = Column(String(120), nullable=False, index=True)
    mastery_score = Column(Float, default=0.0)
    mastery_confidence = Column(Float, default=0.0)
    attempt_count = Column(Integer, default=0)
    last_assessed_at = Column(DateTime, nullable=True)
    calculation_version = Column(String(40), default="v1")
    source_kind = Column(String(80), default="training_workshop", index=True)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    __table_args__ = (UniqueConstraint("learner_id", "kp_id", name="uq_mastery_state_learner_kp"),)


class MasteryHistoryRecord(Base):
    __tablename__ = "mastery_history_records"
    id = Column(Integer, primary_key=True, index=True)
    history_id = Column(String(120), unique=True, nullable=False, index=True)
    learner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kp_id = Column(String(120), index=True)
    trigger_attempt_item_id = Column(String(120), default="", index=True)
    mastery_score = Column(Float, default=0.0)
    mastery_confidence = Column(Float, default=0.0)
    calculation_version = Column(String(40), default="v1")
    formula_input_json = Column(Text, default="{}")
    calculated_at = Column(DateTime, default=utc_now)


class LearnerKPReviewState(Base):
    __tablename__ = "learner_kp_review_states"
    id = Column(Integer, primary_key=True, index=True)
    review_state_id = Column(String(120), unique=True, nullable=False, index=True)
    learner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kp_id = Column(String(120), nullable=False, index=True)
    lambda_per_day = Column(Float, default=0.0)
    recent_five_wrong_count = Column(Integer, default=0)
    consecutive_independent_correct = Column(Integer, default=0)
    consecutive_wrong_count = Column(Integer, default=0)
    review_stage = Column(String(50), default="new", index=True)
    stability_seconds = Column(Float, default=0.0)
    retention_estimate = Column(Float, default=0.0)
    last_review_at = Column(DateTime, nullable=True)
    next_review_at = Column(DateTime, nullable=True, index=True)
    requires_remediation = Column(Boolean, default=False)
    status = Column(String(50), default="active", index=True)
    formula_version = Column(String(40), default="v1")
    __table_args__ = (UniqueConstraint("learner_id", "kp_id", name="uq_review_state_learner_kp"),)


class ReviewTaskRecord(Base):
    __tablename__ = "review_tasks"
    id = Column(Integer, primary_key=True, index=True)
    review_task_id = Column(String(120), unique=True, nullable=False, index=True)
    learner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    review_state_id = Column(String(120), ForeignKey("learner_kp_review_states.review_state_id"), nullable=False, index=True)
    primary_kp_id = Column(String(120), index=True)
    source_type = Column(String(80), default="training_workshop", index=True)
    review_type = Column(String(80), default="review", index=True)
    reason_codes_json = Column(Text, default="[]")
    status = Column(String(50), default="pending", index=True)
    scheduled_at = Column(DateTime, nullable=True, index=True)
    source_attempt_item_id = Column(String(120), default="", index=True)
    created_at = Column(DateTime, default=utc_now)

class LearningPlanRecord(Base):
    __tablename__ = "learning_plan_records"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    plan_type = Column(String(50), index=True)
    title = Column(String(200), default="")
    summary = Column(Text, default="")
    status = Column(String(50), default="active", index=True)
    payload_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

class LearningInterventionRecord(Base):
    __tablename__ = "learning_intervention_records"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    t_stage = Column(String(20), default="", index=True)
    action = Column(String(120), default="")
    reason = Column(Text, default="")
    cooldown_hours = Column(Integer, default=0)
    feedback = Column(Text, default="")
    effect_status = Column(String(50), default="pending", index=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

class FeedbackRecord(Base):
    __tablename__ = "feedback_records"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=True, index=True)
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=True, index=True)
    feedback_type = Column(String(30), index=True)  # excellent / problem / user_like / user_dislike / compliance_fail
    rating = Column(String(20), default="")
    reason = Column(Text, default="")
    user_feedback = Column(Text, default="")
    question = Column(Text, default="")
    answer = Column(Text, default="")
    metadata_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=utc_now)

class KnowledgePoint(Base):
    __tablename__ = "knowledge_points"
    id = Column(Integer, primary_key=True, index=True)
    kp_id = Column(String(120), unique=True, index=True)
    name = Column(String(200), index=True)
    aliases_json = Column(Text, default="[]")
    description = Column(Text, default="")
    source = Column(String(120), default="manual")
    status = Column(String(50), default="active", index=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

class CandidateKnowledgePoint(Base):
    __tablename__ = "candidate_knowledge_points"
    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(String(120), unique=True, index=True)
    name = Column(String(200), default="")
    source_text = Column(Text, default="")
    status = Column(String(50), default="pending", index=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    evidence_json = Column(Text, default="[]")
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

class QuestionBankItem(Base):
    __tablename__ = "question_bank_items"
    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(String(120), unique=True, index=True)
    stem = Column(Text)
    answer = Column(Text, default="")
    analysis = Column(Text, default="")
    kp_ids_json = Column(Text, default="[]")
    question_type = Column(String(50), default="single_choice", index=True)
    difficulty = Column(Float, default=2.0)
    quality_score = Column(Float, default=0.7)
    source = Column(String(120), default="manual")
    status = Column(String(50), default="active", index=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

class QuestionVersionRecord(Base):
    __tablename__ = "question_version_records"
    id = Column(Integer, primary_key=True, index=True)
    question_version_id = Column(String(120), unique=True, nullable=False, index=True)
    question_id = Column(String(120), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    question_type = Column(String(50), default="single_choice", index=True)
    stem = Column(Text, default="")
    answer = Column(Text, default="")
    analysis = Column(Text, default="")
    standard_difficulty = Column(Integer, default=2, index=True)
    source_kind = Column(String(120), default="manual", index=True)
    status = Column(String(50), default="active", index=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    __table_args__ = (UniqueConstraint("question_id", "version", name="uq_question_version_question_version"),)


class VariationSetRecord(Base):
    __tablename__ = "variation_sets"
    id = Column(Integer, primary_key=True, index=True)
    variation_set_id = Column(String(120), unique=True, nullable=False, index=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source_mistake_id = Column(Integer, ForeignKey("mistake_records.id"), nullable=False, index=True)
    source_question_version_id = Column(
        String(120),
        ForeignKey("question_version_records.question_version_id"),
        nullable=False,
        index=True,
    )
    audit_id = Column(String(120), ForeignKey("audit_result_records.audit_id"), nullable=False, index=True)
    status = Column(String(50), default="published", nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now)


class VariationQuestionVersionRecord(Base):
    __tablename__ = "variation_question_versions"
    id = Column(Integer, primary_key=True, index=True)
    variation_set_id = Column(
        String(120),
        ForeignKey("variation_sets.variation_set_id"),
        nullable=False,
        index=True,
    )
    question_version_id = Column(
        String(120),
        ForeignKey("question_version_records.question_version_id"),
        unique=True,
        nullable=False,
        index=True,
    )
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    scope = Column(String(50), default="user", nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now)


class VariationRubricRecord(Base):
    __tablename__ = "variation_rubrics"
    id = Column(Integer, primary_key=True, index=True)
    question_version_id = Column(
        String(120),
        ForeignKey("question_version_records.question_version_id"),
        unique=True,
        nullable=False,
        index=True,
    )
    standard_answer = Column(Text, nullable=False)
    rubric_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=utc_now)


class QuestionKPLinkRecord(Base):
    __tablename__ = "question_kp_link_records"
    id = Column(Integer, primary_key=True, index=True)
    question_version_id = Column(
        String(120),
        ForeignKey("question_version_records.question_version_id"),
        nullable=False,
        index=True,
    )
    kp_id = Column(String(120), nullable=False, index=True)
    is_primary = Column(Boolean, default=False, nullable=False, index=True)
    status = Column(String(50), default="active", index=True)
    created_at = Column(DateTime, default=utc_now)
    __table_args__ = (UniqueConstraint("question_version_id", "kp_id", name="uq_question_kp_link"),)


class QuestionAttempt(Base):
    __tablename__ = "question_attempts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    question_id = Column(String(120), index=True)
    answer = Column(Text, default="")
    is_correct = Column(Boolean, default=False, index=True)
    score = Column(Float, nullable=True)
    kp_ids_json = Column(Text, default="[]")
    feedback = Column(Text, default="")
    created_at = Column(DateTime, default=utc_now)

class MistakeRecord(Base):
    __tablename__ = "mistake_records"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    question_id = Column(String(120), index=True)
    attempt_item_id = Column(String(120), ForeignKey("learning_attempt_items.attempt_item_id"), nullable=True, index=True)
    question_version_id = Column(String(120), ForeignKey("question_version_records.question_version_id"), nullable=True, index=True)
    kp_ids_json = Column(Text, default="[]")
    error_type = Column(String(120), default="", index=True)
    summary = Column(Text, default="")
    status = Column(String(50), default="active", index=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

class TeachingResource(Base):
    __tablename__ = "teaching_resources"
    id = Column(Integer, primary_key=True, index=True)
    resource_id = Column(String(120), unique=True, index=True)
    title = Column(String(200), default="")
    resource_type = Column(String(80), default="knowledge_card", index=True)
    summary = Column(Text, default="")
    kp_ids_json = Column(Text, default="[]")
    source = Column(String(120), default="manual")
    quality_score = Column(Float, default=0.7)
    status = Column(String(50), default="active", index=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

class EvidencePackRecord(Base):
    __tablename__ = "evidence_pack_records"
    id = Column(Integer, primary_key=True, index=True)
    pack_id = Column(String(120), unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    query = Column(Text, default="")
    resolved_kp_ids_json = Column(Text, default="[]")
    candidate_kp_ids_json = Column(Text, default="[]")
    payload_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=utc_now)

class EvidencePackItem(Base):
    __tablename__ = "evidence_pack_items"
    id = Column(Integer, primary_key=True, index=True)
    pack_id = Column(String(120), index=True)
    source_scope = Column(String(80), index=True)
    source_id = Column(String(200), default="")
    summary = Column(Text, default="")
    kp_ids_json = Column(Text, default="[]")
    confidence = Column(Float, default=0.7)
    payload_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=utc_now)


def _is_immutable_recovery_value(value):
    if isinstance(value, tuple):
        return all(_is_immutable_recovery_value(item) for item in value)
    return isinstance(value, (str, int, float, bool, bytes, type(None)))


@dataclass(frozen=True)
class RecoveryCandidateSnapshot:
    role: str
    expected_names: tuple[str, ...]
    discovered_names: tuple[str, ...]
    is_complete: bool
    objects_are_tables: bool
    ledger_owned: bool
    has_extra_objects: bool
    manifest_matches_source: bool
    physical_v2_matches: bool
    foreign_key_violations: tuple[tuple, ...]
    safe_partial_data: bool = False

    def __post_init__(self):
        if not self.discovered_names:
            object.__setattr__(self, "is_complete", False)
            object.__setattr__(self, "objects_are_tables", False)
            object.__setattr__(self, "manifest_matches_source", False)
            object.__setattr__(self, "physical_v2_matches", False)
        boolean_values = (
            self.is_complete, self.objects_are_tables, self.ledger_owned,
            self.has_extra_objects, self.manifest_matches_source, self.physical_v2_matches,
            self.safe_partial_data,
        )
        if (not isinstance(self.role, str)
                or any(type(value) is not bool for value in boolean_values)
                or not isinstance(self.expected_names, tuple)
                or not all(isinstance(name, str) for name in self.expected_names)
                or not isinstance(self.discovered_names, tuple)
                or not all(isinstance(name, str) for name in self.discovered_names)
                or not isinstance(self.foreign_key_violations, tuple)
                or not all(isinstance(item, tuple) and _is_immutable_recovery_value(item)
                           for item in self.foreign_key_violations)):
            raise TypeError("recovery snapshot collections must be immutable tuples")


@dataclass(frozen=True)
class RecoverySnapshot:
    ledger_status: str
    current: RecoveryCandidateSnapshot
    shadow: RecoveryCandidateSnapshot
    backup: RecoveryCandidateSnapshot
    unexpected_controlled_objects: tuple[tuple[str, str], ...]

    def __post_init__(self):
        if (not isinstance(self.ledger_status, str)
                or not isinstance(self.current, RecoveryCandidateSnapshot)
                or not isinstance(self.shadow, RecoveryCandidateSnapshot)
                or not isinstance(self.backup, RecoveryCandidateSnapshot)
                or not isinstance(self.unexpected_controlled_objects, tuple)
                or not all(
                    isinstance(item, tuple) and len(item) == 2
                    and all(isinstance(value, str) for value in item)
                    for item in self.unexpected_controlled_objects
                )):
            raise TypeError("recovery snapshot collections must be immutable tuples")


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    reason_code: str | None = None

    def __post_init__(self):
        if type(self.action) is not str:
            raise TypeError("recovery action must be a string")
        if self.action not in {"finalize_current", "perform_switch", "restore_backup", "fail_closed"}:
            raise ValueError("invalid recovery action")
        if self.reason_code is not None and not isinstance(self.reason_code, str):
            raise TypeError("recovery reason code must be a string or None")


class LearningKnowledgePoint(Base):
    __tablename__ = "kp"

    id = Column(Integer, primary_key=True, index=True)
    kp_id = Column(String(120), unique=True, nullable=False, index=True)
    kp_lv1 = Column(String(200), default="", index=True)
    kp_lv2 = Column(String(200), default="", index=True)
    kp_lv3 = Column(String(200), default="", index=True)
    raw_content = Column(Text, default="")
    other_name_json = Column(Text, default="[]")
    order_json = Column(Text, default="[]")
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class LearningQuestion(Base):
    __tablename__ = "question"

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(String(120), unique=True, nullable=False, index=True)
    question_type = Column(String(50), default="short_answer", index=True)
    question_content = Column(Text, default="")
    options_json = Column(Text, default="[]")
    answer_json = Column(Text, default="[]")
    explaination = Column(Text, default="")
    difficulty = Column(Float, default=0.0, index=True)
    kp_ids_json = Column(Text, default="[]")
    tokenized_content_json = Column(Text, default="[]")
    scoring_rubric = Column(Text, default="")
    key_points = Column(Text, default="")
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class LearningUserProfile(Base):
    __tablename__ = "user_profile"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    user_name = Column(String(100), default="")
    user_preference_json = Column(Text, default="{}")
    user_group_json = Column(Text, default="{}")
    user_major_or_profession = Column(String(200), default="")
    user_area = Column(String(200), default="")
    completed_courses_json = Column(Text, default="[]")
    goals_json = Column(Text, default="{}")
    daily_available_minutes = Column(Integer, nullable=True)
    education = Column(String(200), default="")
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class LearningProfile(Base):
    __tablename__ = "learning_profile"

    id = Column(Integer, primary_key=True, index=True)
    # The JSON contract omits identity, but this is a per-user current-state record.
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    current_status_json = Column(Text, default="{}")
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class LongTermPlan(Base):
    __tablename__ = "long_term_plan"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(String(120), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    content = Column(Text, default="")
    status = Column(String(50), default="active", index=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class ShortTermPlan(Base):
    __tablename__ = "short_term_plan"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(String(120), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    long_term_plan_id = Column(String(120), ForeignKey("long_term_plan.plan_id"), nullable=True, index=True)
    content = Column(Text, default="")
    priority_mode = Column(String(50), default="normal", index=True)
    start_at = Column(DateTime, nullable=True)
    end_at = Column(DateTime, nullable=True)
    status = Column(String(50), default="active", index=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class LearningQuestionAttempt(Base):
    __tablename__ = "question_attempt"

    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(String(120), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    question_id = Column(String(120), ForeignKey("question.question_id"), nullable=False, index=True)
    task_id = Column(String(120), ForeignKey("learning_task.task_id"), nullable=True, index=True)
    request_id = Column(String(120), nullable=True, index=True)
    submitted_answer_json = Column(Text, default="[]")
    is_correct = Column(Boolean, nullable=False, default=False, index=True)
    score = Column(Float, nullable=True)
    response_time_seconds = Column(Integer, nullable=True)
    reason_for_mistake = Column(Text, default="")
    answered_at = Column(DateTime, default=utc_now, index=True)
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_question_attempt_user_request"),
    )


class LearningTask(Base):
    __tablename__ = "learning_task"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(120), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    short_term_plan_id = Column(String(120), ForeignKey("short_term_plan.plan_id"), nullable=True, index=True)
    task_type = Column(String(80), default="learning", index=True)
    kp_ids_json = Column(Text, default="[]")
    question_ids_json = Column(Text, default="[]")
    resource_ids_json = Column(Text, default="[]")
    task_content = Column(Text, default="")
    estimated_minutes = Column(Integer, nullable=True)
    expected_output = Column(Text, default="")
    completion_criteria = Column(Text, default="")
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(50), default="pending", index=True)
    created_at = Column(DateTime, default=utc_now)
    due_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)


class LearningFocusSession(Base):
    __tablename__ = "learning_focus_sessions"

    id = Column(Integer, primary_key=True, index=True)
    focus_session_id = Column(String(120), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    task_id = Column(String(120), ForeignKey("learning_task.task_id"), nullable=True, index=True)
    resource_type = Column(String(80), default="", index=True)
    resource_id = Column(String(120), default="", index=True)
    status = Column(String(50), default="active", index=True)
    is_visible = Column(Boolean, default=True)
    last_interaction_at = Column(DateTime, nullable=True)
    active_seconds = Column(Integer, default=0)
    started_at = Column(DateTime, default=utc_now, index=True)
    ended_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class SystemData(Base):
    __tablename__ = "system_data"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    time_data_json = Column(Text, default="{}")
    task_completion_rate_json = Column(Text, default="{}")
    resource_click_rate_json = Column(Text, default="{}")
    data_source = Column(Text, default="")
    calculation_version = Column(String(40), default="v1")
    calculated_at = Column(DateTime, default=utc_now, index=True)
    __table_args__ = (UniqueConstraint("user_id", name="uq_system_data_user"),)


class LearningAgentContext(Base):
    __tablename__ = "agent_context"

    id = Column(Integer, primary_key=True, index=True)
    trace_id = Column(String(120), unique=True, nullable=False, index=True)
    task_id = Column(String(120), ForeignKey("learning_task.task_id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=True, index=True)
    source_agent = Column(String(80), nullable=False, index=True)
    target_agent = Column(String(80), nullable=False, index=True)
    purpose = Column(String(200), nullable=False)
    payload_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=utc_now, index=True)


class QuestionLearningStat(Base):
    __tablename__ = "question_learning_stats"

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(String(120), ForeignKey("question.question_id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reason_for_mistake = Column(Text, default="")
    answer_accuracy = Column(Float, default=0.0)
    attempt_count = Column(Integer, default=0)
    correct_count = Column(Integer, default=0)
    calculated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    __table_args__ = (UniqueConstraint("question_id", "user_id", name="uq_question_learning_stats_question_user"),)


class UserKnowledgeState(Base):
    __tablename__ = "user_knowledge_state"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kp_id = Column(String(120), ForeignKey("kp.kp_id"), nullable=False, index=True)
    knowledge_mastery = Column(Float, default=0.0)
    answer_accuracy = Column(Float, default=0.0)
    forgetting_coefficient = Column(Float, default=0.0)
    kp_review_status = Column(String(50), default="active", index=True)
    attempt_count = Column(Integer, default=0)
    correct_count = Column(Integer, default=0)
    calculated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    __table_args__ = (UniqueConstraint("user_id", "kp_id", name="uq_user_knowledge_state_user_kp"),)


class CorePracticeSubmissionClaim(Base):
    __tablename__ = "core_practice_submission_claims"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    request_id = Column(String(120), nullable=False, index=True)
    question_id = Column(String(120), ForeignKey("question.question_id"), nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now)
    __table_args__ = (UniqueConstraint("user_id", "request_id", name="uq_core_practice_claim_user_request"),)


_CORE_LEARNING_CONTRACT_TABLES = (
    "kp",
    "user_learning_targets",
    "question",
    "user_profile",
    "learning_profile",
    "long_term_plan",
    "short_term_plan",
    "learning_task",
    "learning_focus_sessions",
    "question_attempt",
    "system_data",
    "agent_context",
    "question_learning_stats",
    "user_knowledge_state",
    "core_practice_submission_claims",
)


def _ensure_core_learning_contract_tables(bind):
    Base.metadata.create_all(
        bind=bind,
        tables=[Base.metadata.tables[table_name] for table_name in _CORE_LEARNING_CONTRACT_TABLES],
    )
    _ensure_system_data_indexes(bind)
    inspector = inspect(bind)
    if "user_learning_targets" in inspector.get_table_names():
        columns = {
            column["name"]
            for column in inspector.get_columns("user_learning_targets")
        }
        if "active_slot" not in columns:
            with bind.begin() as connection:
                _add_column_if_missing_after_race(
                    connection,
                    "user_learning_targets",
                    "active_slot",
                    "ALTER TABLE user_learning_targets ADD COLUMN active_slot INTEGER NULL",
                )
        with bind.begin() as connection:
            connection.execute(text(
                "UPDATE user_learning_targets "
                "SET is_active = 0, active_slot = NULL "
                "WHERE is_active = 1 AND id NOT IN ("
                "SELECT keep_id FROM ("
                "SELECT MAX(id) AS keep_id FROM user_learning_targets "
                "WHERE is_active = 1 GROUP BY user_id"
                ") AS retained_targets)"
            ))
            connection.execute(text(
                "UPDATE user_learning_targets SET active_slot = NULL "
                "WHERE is_active = 0"
            ))
            connection.execute(text(
                "UPDATE user_learning_targets SET active_slot = 1 "
                "WHERE is_active = 1"
            ))
        inspector = inspect(bind)
        indexes = {
            item["name"]
            for item in inspector.get_indexes("user_learning_targets")
        }
        if "uq_user_learning_targets_user_active_slot" not in indexes:
            with bind.begin() as connection:
                connection.execute(text(
                    "CREATE UNIQUE INDEX uq_user_learning_targets_user_active_slot "
                    "ON user_learning_targets (user_id, active_slot)"
                ))
    inspector = inspect(bind)
    if "question_attempt" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("question_attempt")}
        if "request_id" not in columns:
            with bind.begin() as connection:
                _add_column_if_missing_after_race(
                    connection,
                    "question_attempt",
                    "request_id",
                    "ALTER TABLE question_attempt ADD COLUMN request_id VARCHAR(120) NULL",
                )
    inspector = inspect(bind)
    if "learning_task" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("learning_task")}
        if "version" not in columns:
            with bind.begin() as connection:
                _add_column_if_missing_after_race(
                    connection,
                    "learning_task",
                    "version",
                    "ALTER TABLE learning_task ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
                )
    inspector = inspect(bind)
    if "learning_focus_sessions" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("learning_focus_sessions")}
        missing_columns = {
            "is_visible": "ALTER TABLE learning_focus_sessions ADD COLUMN is_visible BOOLEAN NOT NULL DEFAULT 1",
            "last_interaction_at": "ALTER TABLE learning_focus_sessions ADD COLUMN last_interaction_at DATETIME NULL",
        }
        with bind.begin() as connection:
            for column_name, statement in missing_columns.items():
                if column_name not in columns:
                    _add_column_if_missing_after_race(
                        connection,
                        "learning_focus_sessions",
                        column_name,
                        statement,
                    )
    inspector = inspect(bind)
    for table_name in _CORE_LEARNING_CONTRACT_TABLES:
        actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
        expected_columns = set(Base.metadata.tables[table_name].columns.keys())
        if not expected_columns <= actual_columns:
            raise RuntimeError("core_learning_schema_incompatible")

def _ensure_system_data_indexes(bind):
    inspector = inspect(bind)
    with bind.begin() as connection:
        if "learning_activity_records" in inspector.get_table_names():
            indexes = {item["name"] for item in inspector.get_indexes("learning_activity_records")}
            if "ix_learning_activity_user_created" not in indexes:
                connection.execute(text(
                    "CREATE INDEX ix_learning_activity_user_created "
                    "ON learning_activity_records (user_id, created_at)"
                ))
        if "system_data" in inspector.get_table_names():
            if bind.dialect.name == "sqlite":
                has_user_constraint = connection.execute(text(
                    "SELECT 1 FROM sqlite_master WHERE type = 'index' "
                    "AND name = 'uq_system_data_user'"
                )).first() is not None
            else:
                unique_constraints = {
                    item["name"] for item in inspector.get_unique_constraints("system_data")
                }
                has_user_constraint = "uq_system_data_user" in unique_constraints
            if not has_user_constraint:
                duplicates = connection.execute(text(
                    "SELECT user_id FROM system_data GROUP BY user_id HAVING COUNT(*) > 1 LIMIT 1"
                )).first()
                if duplicates is not None:
                    raise RuntimeError("system_data_duplicate_user_snapshots")
                if bind.dialect.name == "sqlite":
                    connection.execute(text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_system_data_user ON system_data (user_id)"
                    ))
                else:
                    connection.execute(text(
                        "CREATE UNIQUE INDEX uq_system_data_user ON system_data (user_id)"
                    ))


class UserQuestionImportJob(Base):
    __tablename__ = "user_question_import_jobs"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String(120), unique=True, nullable=False, index=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    original_filename = Column(String(255), nullable=False)
    stored_path = Column(Text, nullable=False)
    content_type = Column(String(120), nullable=False, default="")
    file_size = Column(Integer, nullable=False, default=0)
    status = Column(String(40), nullable=False, default="processing", index=True)
    item_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class UserQuestionItem(Base):
    __tablename__ = "user_question_items"

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(String(120), unique=True, nullable=False, index=True)
    job_id = Column(String(120), ForeignKey("user_question_import_jobs.job_id"), nullable=False, index=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    question_type = Column(String(80), nullable=False, default="short_answer")
    stem = Column(Text, nullable=False)
    answer = Column(Text, nullable=False, default="")
    analysis = Column(Text, nullable=False, default="")
    options_json = Column(Text, nullable=False, default="[]")
    kp_ids_json = Column(Text, nullable=False, default="[]")
    content_hash = Column(String(64), nullable=False, index=True)
    status = Column(String(40), nullable=False, default="needs_human_review", index=True)
    review_reason = Column(Text, nullable=False, default="")
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    __table_args__ = (
        UniqueConstraint("owner_user_id", "content_hash", name="uq_user_question_owner_content"),
    )


class UserQuestionPracticeClaim(Base):
    __tablename__ = "user_question_practice_claims"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    request_id = Column(String(120), nullable=False, index=True)
    question_id = Column(String(120), ForeignKey("user_question_items.question_id"), nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now)
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_user_question_claim_user_request"),
    )


class QuestionIngestionTaskRecord(Base):
    __tablename__ = "question_ingestion_task_records"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(120), unique=True, nullable=False, index=True)
    submitted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    payload_json = Column(Text, nullable=False, default="{}")
    status = Column(String(40), nullable=False, default="queued", index=True)
    published_question_id = Column(String(120), nullable=True, index=True)
    result_json = Column(Text, nullable=False, default="{}")
    error_code = Column(String(120), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime, nullable=True)
    claim_expires_at = Column(DateTime, nullable=True, index=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class FormalContentImportBatch(Base):
    __tablename__ = "formal_content_import_batches"

    id = Column(Integer, primary_key=True, index=True)
    data_version = Column(String(120), nullable=False, index=True)
    content_sha256 = Column(String(64), nullable=False, index=True)
    source_tag = Column(String(160), nullable=False, unique=True, index=True)
    summary_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=utc_now)
    __table_args__ = (UniqueConstraint("data_version", "content_sha256", name="uq_formal_content_import_version_hash"),)


_FORMAL_CONTENT_TABLES = (
    "formal_content_import_batches",
    "question_ingestion_task_records",
    "user_question_import_jobs",
    "user_question_items",
    "user_question_practice_claims",
)


def _ensure_formal_content_tables(bind):
    Base.metadata.create_all(
        bind=bind,
        tables=[Base.metadata.tables[table_name] for table_name in _FORMAL_CONTENT_TABLES],
    )
    if bind.dialect.name != "sqlite":
        return
    columns = {column["name"] for column in inspect(bind).get_columns("question_ingestion_task_records")}
    additions = (
        ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
        ("started_at", "DATETIME NULL"),
        ("claim_expires_at", "DATETIME NULL"),
        ("finished_at", "DATETIME NULL"),
    )
    with bind.begin() as connection:
        for column_name, definition in additions:
            if column_name not in columns:
                _add_column_if_missing_after_race(
                    connection,
                    "question_ingestion_task_records",
                    column_name,
                    f"ALTER TABLE question_ingestion_task_records ADD COLUMN {column_name} {definition}",
                )


class RuntimeSchemaMigration(Base):
    __tablename__ = "runtime_schema_migrations"

    id = Column(Integer, primary_key=True)
    migration_id = Column(String(160), unique=True, nullable=False, index=True)
    target_version = Column(String(40), nullable=False)
    status = Column(String(40), nullable=False, index=True)
    current_step = Column(String(160), nullable=False, default="prepared")
    attempt_count = Column(Integer, nullable=False, default=0)
    controlled_objects_json = Column(Text, nullable=False, default="{}")
    source_manifest_json = Column(Text, nullable=False, default="{}")
    verification_summary_json = Column(Text, nullable=False, default="{}")
    failure_reason = Column(String(240), nullable=True)
    started_at = Column(DateTime, nullable=False, default=utc_now)
    completed_at = Column(DateTime, nullable=True)


def _is_duplicate_column_error(exc):
    original = getattr(exc, "orig", None)
    args = getattr(original, "args", ())
    mysql_code = args[0] if args and isinstance(args[0], int) else None
    sqlite_message = str(original).lower() if original is not None else ""
    return mysql_code == 1060 or "duplicate column name" in sqlite_message


def _add_column_if_missing_after_race(conn, table_name, column_name, statement):
    try:
        conn.execute(text(statement))
        return True
    except DBAPIError as exc:
        if not _is_duplicate_column_error(exc):
            raise
        columns = {col["name"] for col in inspect(conn).get_columns(table_name)}
        if column_name not in columns:
            raise
        return False


_AUTHORITATIVE_LEARNING_TABLES = (
    "learning_attempts",
    "learning_attempt_items",
    "grading_result_records",
    "audit_result_records",
    "learning_writeback_receipts",
    "knowledge_mastery_states",
    "mastery_history_records",
    "learner_kp_review_states",
    "review_tasks",
)
_AUTHORITATIVE_LEARNING_STABLE_IDS = {
    "learning_attempts": "attempt_id",
    "learning_attempt_items": "attempt_item_id",
    "grading_result_records": "artifact_id",
    "audit_result_records": "audit_id",
    "learning_writeback_receipts": "receipt_id",
    "knowledge_mastery_states": "mastery_state_id",
    "mastery_history_records": "history_id",
    "learner_kp_review_states": "review_state_id",
    "review_tasks": "review_task_id",
}
AUTHORITATIVE_LEARNING_MIGRATION_ID = "authoritative_learning_records_v2"
AUTHORITATIVE_LEARNING_TARGET_VERSION = "v2"


def _normalize_sqlite_type(type_value):
    value = str(type_value).upper().replace(" ", "")
    if value == "TINYINT(1)" or value == "BOOLEAN":
        return "BOOLEAN"
    if value == "INTEGER":
        return "INTEGER"
    if value == "TEXT":
        return "TEXT"
    if value == "DATETIME":
        return "DATETIME"
    if value == "FLOAT":
        return "FLOAT"
    if value.startswith("VARCHAR(") and value.endswith(")"):
        return value
    return value


def inspect_authoritative_schema_snapshot(bind, physical_names, table_names=None):
    inspector = inspect(bind)
    table_names = table_names or _AUTHORITATIVE_LEARNING_TABLES
    physical_names = physical_names or {}
    physical_to_logical = {physical: logical for logical, physical in physical_names.items()}
    snapshot = {}
    for logical_name in table_names:
        physical_name = physical_names.get(logical_name, logical_name)
        primary_key = inspector.get_pk_constraint(physical_name).get("constrained_columns") or []
        columns = [
            (column["name"], _normalize_sqlite_type(column["type"]), bool(column["nullable"]))
            for column in inspector.get_columns(physical_name)
        ]
        indexes = inspector.get_indexes(physical_name)
        uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints(physical_name)
            if item.get("column_names")
        }
        uniques.update(tuple(item["column_names"]) for item in indexes if item.get("unique"))
        foreign_keys = [
            (
                tuple(item["constrained_columns"]),
                physical_to_logical.get(item["referred_table"], item["referred_table"]),
                tuple(item["referred_columns"]),
                item.get("options", {}).get("onupdate"),
                item.get("options", {}).get("ondelete"),
            )
            for item in inspector.get_foreign_keys(physical_name)
        ]
        snapshot[logical_name] = {
            "primary_key": list(primary_key),
            "columns": columns,
            "uniques": sorted(uniques),
            "foreign_keys": sorted(foreign_keys),
            "indexes": sorted(
                (tuple(item["column_names"]), bool(item.get("unique")))
                for item in indexes if item.get("column_names")
            ),
        }
    return snapshot


def physical_schema_fingerprint(snapshot):
    encoded = json.dumps(snapshot, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _target_physical_schema_fingerprint(table_names=None):
    table_names = table_names or _AUTHORITATIVE_LEARNING_TABLES
    target = create_engine("sqlite://")
    try:
        with target.begin() as connection:
            Base.metadata.tables["users"].create(connection)
            for table_name in _AUTHORITATIVE_LEARNING_TABLES:
                Base.metadata.tables[table_name].create(connection)
        snapshot = inspect_authoritative_schema_snapshot(target, {})
        return physical_schema_fingerprint({name: snapshot[name] for name in table_names})
    finally:
        target.dispose()


def physical_schema_matches_target(bind, physical_names=None):
    return physical_schema_fingerprint(
        inspect_authoritative_schema_snapshot(bind, physical_names or {})
    ) == _target_physical_schema_fingerprint()


def _target_schema_fingerprint(table_names):
    return _target_physical_schema_fingerprint(table_names)


def _reflected_authoritative_schema_matches(bind, table_name_map=None):
    return physical_schema_matches_target(bind, table_name_map)


def _quote_sqlite_identifier(name):
    return '"' + name.replace('"', '""') + '"'


def build_authoritative_manifest(bind, table_names, table_name_map=None):
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    manifest = {"schema_fingerprint": _target_schema_fingerprint(table_names), "tables": {}}
    owns_connection = not hasattr(bind, "execute")
    connection = bind.connect() if owns_connection else bind
    table_name_map = table_name_map or {}
    try:
        for table_name in sorted(table_names):
            physical_name = table_name_map.get(table_name, table_name)
            stable_id = _AUTHORITATIVE_LEARNING_STABLE_IDS[table_name]
            if physical_name not in existing_tables:
                manifest["tables"][table_name] = {"row_count": 0, "stable_ids": []}
                continue
            columns = {column["name"] for column in inspector.get_columns(physical_name)}
            quoted_table = _quote_sqlite_identifier(physical_name)
            quoted_stable_id = _quote_sqlite_identifier(stable_id)
            if stable_id not in columns:
                manifest["tables"][table_name] = {"row_count": connection.execute(
                    text(f"SELECT COUNT(*) FROM {quoted_table}")).scalar_one(), "stable_ids": []}
                continue
            rows = connection.execute(text(
                f"SELECT {quoted_stable_id} FROM {quoted_table} ORDER BY {quoted_stable_id}"
            )).scalars().all()
            manifest["tables"][table_name] = {"row_count": len(rows), "stable_ids": rows}
    finally:
        if owns_connection:
            connection.close()
    return manifest


def controlled_sqlite_name(table_name, role):
    if table_name not in _AUTHORITATIVE_LEARNING_TABLES or role not in {"shadow", "backup"}:
        raise ValueError("invalid controlled SQLite object")
    return f"{table_name}__{AUTHORITATIVE_LEARNING_MIGRATION_ID}__{role}"


def clone_authoritative_table(table_name, clone_name):
    if table_name not in _AUTHORITATIVE_LEARNING_TABLES:
        raise ValueError("invalid authoritative learning table")
    metadata = MetaData()
    Table("users", metadata, Column("id", Integer, primary_key=True))
    controlled_names = {
        name: controlled_sqlite_name(name, "shadow")
        for name in _AUTHORITATIVE_LEARNING_TABLES
    }
    for source_name in _AUTHORITATIVE_LEARNING_TABLES:
        source = Base.metadata.tables[source_name]
        columns = []
        for column in source.columns:
            copied = column._copy()
            copied.index = False
            copied.unique = False
            columns.append(copied)
        constraints = []
        for constraint in source.constraints:
            if isinstance(constraint, UniqueConstraint):
                constraints.append(UniqueConstraint(
                    *(column.name for column in constraint.columns), name=constraint.name
                ))
        for constraint in source.foreign_key_constraints:
            local_columns = [element.parent.name for element in constraint.elements]
            remote_columns = []
            for element in constraint.elements:
                remote_table = element.column.table.name
                target_table = controlled_names.get(remote_table, remote_table)
                remote_columns.append(f"{target_table}.{element.column.name}")
            constraints.append(ForeignKeyConstraint(local_columns, remote_columns, name=constraint.name))
        clone = Table(controlled_names[source_name], metadata, *columns, *constraints)
        for index in source.indexes:
            Index(
                f"{index.name}__{AUTHORITATIVE_LEARNING_MIGRATION_ID}__shadow",
                *(clone.c[column.name] for column in index.columns), unique=index.unique
            )
    return metadata.tables[clone_name]


def _controlled_objects(migration):
    objects = json.loads(migration.controlled_objects_json)
    if not isinstance(objects, dict) or set(objects) != set(_AUTHORITATIVE_LEARNING_TABLES):
        raise ValueError("invalid controlled object ledger")
    for table_name, names in objects.items():
        if names != {"shadow": controlled_sqlite_name(table_name, "shadow"), "backup": controlled_sqlite_name(table_name, "backup")}:
            raise ValueError("invalid controlled object ledger")
    return objects


def _persist_migration(connection, migration, **values):
    connection.execute(RuntimeSchemaMigration.__table__.update().where(
        RuntimeSchemaMigration.__table__.c.id == migration.id
    ).values(**values))


def _fail_authoritative_recovery(connection, migration, reason):
    _persist_migration(connection, migration, status="recovery_failed", current_step="recover", failure_reason=reason)
    connection.commit()
    raise RuntimeError("authoritative_learning_schema_recovery_failed")


def _foreign_key_check(connection, table_names=_AUTHORITATIVE_LEARNING_TABLES):
    violations = []
    for table_name in table_names:
        violations.extend(connection.execute(text(
            f"PRAGMA foreign_key_check({_quote_sqlite_identifier(table_name)})"
        )).all())
    return violations


def _drop_controlled_objects(connection, controlled):
    inspector = inspect(connection)
    existing = set(inspector.get_table_names())
    controlled_names = {
        name for names in controlled.values() for name in names.values() if name in existing
    }
    ordered = []
    visiting = set()
    visited = set()

    def visit(name):
        if name in visited or name in visiting:
            return
        visiting.add(name)
        for foreign_key in inspector.get_foreign_keys(name):
            parent = foreign_key["referred_table"]
            if parent in controlled_names:
                visit(parent)
        visiting.remove(name)
        visited.add(name)
        ordered.append(name)

    for name in sorted(controlled_names):
        visit(name)
    for name in reversed(ordered):
        connection.execute(text(f"DROP TABLE {_quote_sqlite_identifier(name)}"))


def _verified_manifest(migration, manifest):
    expected = json.loads(migration.source_manifest_json)
    return manifest["schema_fingerprint"] == expected.get("schema_fingerprint") and manifest["tables"] == expected.get("tables")


def _shadow_names(controlled):
    return {table_name: names["shadow"] for table_name, names in controlled.items()}


def _inspect_sqlite_recovery_candidate(
        connection, *, role, physical_names, catalog, expected_manifest,
        expected_fingerprint, ledger_names):
    table_names = tuple(_AUTHORITATIVE_LEARNING_TABLES)
    mapped_names = {name: physical_names.get(name, name) for name in table_names}
    expected_names = tuple(mapped_names[name] for name in table_names)
    catalog_types = {name: kind for name, kind in catalog}
    discovered_names = tuple(name for name in expected_names if name in catalog_types)
    is_complete = len(discovered_names) == len(expected_names)
    objects_are_tables = is_complete and all(
        catalog_types[name] == "table" for name in expected_names
    )
    ledger_owned = role == "current" or set(expected_names) == set(ledger_names)
    role_suffix = f"__{AUTHORITATIVE_LEARNING_MIGRATION_ID}__{role}"
    candidate_objects = {
        name for name, _ in catalog if name.endswith(role_suffix)
    } if role != "current" else set()
    has_extra_objects = bool(candidate_objects - set(expected_names)) or (
        role != "shadow" and bool(discovered_names) and not is_complete
    )
    manifest_matches_source = False
    physical_v2_matches = False
    foreign_key_violations = ()
    safe_partial_data = False
    if is_complete and objects_are_tables:
        actual_manifest = build_authoritative_manifest(connection, table_names, mapped_names)
        actual_tables = actual_manifest["tables"]
        expected_tables = expected_manifest.get("tables", {})
        manifest_matches_source = actual_tables == expected_tables
        actual_schema = inspect_authoritative_schema_snapshot(
            connection, mapped_names, table_names
        )
        physical_v2_matches = physical_schema_fingerprint(actual_schema) == expected_fingerprint
        if physical_v2_matches:
            foreign_key_violations = tuple(
                tuple(value for value in row)
                for row in _foreign_key_check(connection, expected_names)
            )
            safe_partial_data = (
                not manifest_matches_source
                and not foreign_key_violations
                and set(actual_tables) == set(expected_tables)
                and all(
                    actual_tables[name]["row_count"] <= expected_tables[name]["row_count"]
                    and set(actual_tables[name]["stable_ids"]).issubset(
                        expected_tables[name]["stable_ids"]
                    )
                    for name in actual_tables
                )
            )
    return RecoveryCandidateSnapshot(
        role=role,
        expected_names=expected_names,
        discovered_names=discovered_names,
        is_complete=is_complete,
        objects_are_tables=objects_are_tables,
        ledger_owned=ledger_owned,
        has_extra_objects=has_extra_objects,
        manifest_matches_source=manifest_matches_source,
        physical_v2_matches=physical_v2_matches,
        foreign_key_violations=foreign_key_violations,
        safe_partial_data=safe_partial_data,
    )


def build_sqlite_recovery_snapshot(connection, migration):
    controlled = _controlled_objects(migration)
    catalog = tuple(tuple(row) for row in connection.execute(text(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view')"
    )).all())
    expected_manifest = json.loads(migration.source_manifest_json)
    expected_fingerprint = _target_physical_schema_fingerprint()
    shadow_names = _shadow_names(controlled)
    backup_names = {name: values["backup"] for name, values in controlled.items()}
    current = _inspect_sqlite_recovery_candidate(
        connection, role="current", physical_names={}, catalog=catalog,
        expected_manifest=expected_manifest, expected_fingerprint=expected_fingerprint,
        ledger_names=(),
    )
    shadow = _inspect_sqlite_recovery_candidate(
        connection, role="shadow", physical_names=shadow_names, catalog=catalog,
        expected_manifest=expected_manifest, expected_fingerprint=expected_fingerprint,
        ledger_names=tuple(shadow_names.values()),
    )
    backup = _inspect_sqlite_recovery_candidate(
        connection, role="backup", physical_names=backup_names, catalog=catalog,
        expected_manifest=expected_manifest, expected_fingerprint=expected_fingerprint,
        ledger_names=tuple(backup_names.values()),
    )
    expected = set(shadow_names.values()) | set(backup_names.values())
    unexpected = tuple(sorted(
        (name, kind) for name, kind in catalog
        if f"__{AUTHORITATIVE_LEARNING_MIGRATION_ID}__" in name and name not in expected
    ))
    return RecoverySnapshot(migration.status, current, shadow, backup, unexpected)


def _classify_controlled_objects(connection, migration, controlled):
    expected = json.loads(migration.source_manifest_json)
    entries = connection.execute(text(
        "SELECT name, type FROM sqlite_master "
        "WHERE type IN ('table', 'view') AND name LIKE :pattern"
    ), {"pattern": f"%__{AUTHORITATIVE_LEARNING_MIGRATION_ID}__%"}).all()
    object_types = {name: object_type for name, object_type in entries}
    shadow_names = _shadow_names(controlled)
    expected_names = set(shadow_names.values()) | {
        names["backup"] for names in controlled.values()
    }
    classifications = {
        name: "untrusted" for name in object_types if name not in expected_names
    }
    inspector = inspect(connection)
    canonical_tables = set(inspector.get_table_names())
    if not set(_AUTHORITATIVE_LEARNING_TABLES).issubset(canonical_tables):
        classifications["canonical"] = "untrusted"
    else:
        source = build_authoritative_manifest(connection, _AUTHORITATIVE_LEARNING_TABLES)
        classifications["canonical"] = (
            "valid_staged_shadow" if _verified_manifest(migration, source) else "untrusted"
        )
    for table_name, names in controlled.items():
        shadow = names["shadow"]
        backup = names["backup"]
        if backup in object_types:
            classifications[backup] = "untrusted"
        if shadow not in object_types:
            classifications[shadow] = "absent"
            continue
        if object_types[shadow] != "table":
            classifications[shadow] = "untrusted"
            continue
        snapshot = inspect_authoritative_schema_snapshot(
            connection, shadow_names, (table_name,)
        )[table_name]
        if physical_schema_fingerprint({table_name: snapshot}) != _target_schema_fingerprint((table_name,)):
            classifications[shadow] = "untrusted"
            continue
        shadow_manifest = build_authoritative_manifest(
            connection, (table_name,), {table_name: shadow}
        )["tables"][table_name]
        expected_table = expected.get("tables", {}).get(table_name)
        if expected_table is None:
            classifications[shadow] = "untrusted"
        elif shadow_manifest == expected_table:
            classifications[shadow] = "valid_staged_shadow"
        elif set(shadow_manifest["stable_ids"]).issubset(expected_table["stable_ids"]):
            classifications[shadow] = "safe_partial_shadow"
        else:
            classifications[shadow] = "untrusted"
    return classifications


def _shadows_match_source_manifest(connection, migration, controlled):
    classifications = _classify_controlled_objects(connection, migration, controlled)
    return (
        classifications.get("canonical") == "valid_staged_shadow"
        and all(
            classifications.get(name) == "valid_staged_shadow"
            for name in _shadow_names(controlled).values()
        )
    )


def _drop_controlled_shadows(connection, controlled):
    shadows = {
        table_name: {"shadow": names["shadow"]}
        for table_name, names in controlled.items()
    }
    _drop_controlled_objects(connection, shadows)


def stage_authoritative_learning_schema_for_sqlite(bind, migration, checkpoint=lambda stage: None):
    controlled = _controlled_objects(migration)
    with bind.begin() as connection:
        source_manifest = build_authoritative_manifest(connection, _AUTHORITATIVE_LEARNING_TABLES)
        if not _verified_manifest(migration, source_manifest):
            _fail_authoritative_recovery(connection, migration, "source_manifest_mismatch")
        classifications = _classify_controlled_objects(connection, migration, controlled)
        if (
            classifications.get("canonical") != "valid_staged_shadow"
            or any(value == "untrusted" for value in classifications.values())
        ):
            _fail_authoritative_recovery(connection, migration, "untrusted_controlled_object")
        _drop_controlled_shadows(connection, controlled)
        _persist_migration(connection, migration, status="prepared", current_step="prepared")
    checkpoint("prepared_committed")

    with bind.begin() as connection:
        controlled = _controlled_objects(migration)
        for table_name in _AUTHORITATIVE_LEARNING_TABLES:
            shadow = controlled[table_name]["shadow"]
            clone_authoritative_table(table_name, shadow).create(connection)
            source_columns = {column["name"] for column in inspect(connection).get_columns(table_name)}
            target_columns = [column.name for column in Base.metadata.tables[table_name].columns if column.name in source_columns]
            quoted_columns = ", ".join(_quote_sqlite_identifier(column) for column in target_columns)
            connection.execute(text(
                f"INSERT INTO {_quote_sqlite_identifier(shadow)} ({quoted_columns}) "
                f"SELECT {quoted_columns} FROM {_quote_sqlite_identifier(table_name)}"
            ))
            source_manifest = build_authoritative_manifest(connection, (table_name,))
            shadow_manifest = build_authoritative_manifest(connection, (table_name,), {table_name: shadow})
            if source_manifest["tables"] != shadow_manifest["tables"]:
                _fail_authoritative_recovery(connection, migration, "shadow_manifest_mismatch")
        if not _shadows_match_source_manifest(connection, migration, controlled):
            _fail_authoritative_recovery(connection, migration, "shadow_verification_failed")
        summary = build_authoritative_manifest(
            connection, _AUTHORITATIVE_LEARNING_TABLES, _shadow_names(controlled)
        )
        _persist_migration(
            connection, migration, status="staged", current_step="staged",
            verification_summary_json=json.dumps(summary, sort_keys=True),
        )
    checkpoint("staged_committed")


def _canonical_is_verified(connection, migration):
    try:
        manifest = build_authoritative_manifest(connection, _AUTHORITATIVE_LEARNING_TABLES)
        return (
            _verified_manifest(migration, manifest)
            and physical_schema_matches_target(connection)
            and not _foreign_key_check(connection)
        )
    except Exception:
        return False


class RecoveryActionError(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _record_recovery_failure(bind, migration, reason_code):
    try:
        with bind.begin() as connection:
            _persist_migration(
                connection, migration, status="recovery_failed", current_step="recover",
                failure_reason=reason_code,
            )
    except Exception:
        pass


def run_recovery_action(
        bind, migration, *, action_name, requires_foreign_keys_off, operation):
    connection = None
    raw = None
    original_foreign_keys = None
    reason_code = None
    try:
        connection = bind.connect().execution_options(isolation_level="AUTOCOMMIT")
        raw = connection.connection.driver_connection
        original_foreign_keys = raw.execute("PRAGMA foreign_keys").fetchone()[0]
        if requires_foreign_keys_off:
            raw.execute("PRAGMA foreign_keys = OFF")
            if raw.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
                raise RecoveryActionError("foreign_keys_disable_failed")
        operation(raw)
    except RecoveryActionError as exc:
        reason_code = exc.reason_code
    except Exception:
        reason_code = f"{action_name}_failed"
    finally:
        try:
            if raw is not None and original_foreign_keys is not None:
                raw.execute(f"PRAGMA foreign_keys = {int(original_foreign_keys)}")
                if raw.execute("PRAGMA foreign_keys").fetchone()[0] != original_foreign_keys:
                    reason_code = "foreign_keys_restore_failed"
        except Exception:
            reason_code = "foreign_keys_restore_failed"
        if connection is not None:
            try:
                connection.close()
            except Exception:
                if reason_code is None:
                    reason_code = "connection_close_failed"
    if reason_code is not None:
        try:
            _record_recovery_failure(bind, migration, reason_code)
        except Exception:
            pass
        raise RuntimeError("authoritative_learning_schema_recovery_failed")


def _run_sqlite_rename_transaction(
        raw_connection, statements, ledger_id, status, *, fault_hook=None):
    cursor = raw_connection.cursor()

    def checkpoint(stage):
        if fault_hook is not None:
            fault_hook(stage)

    try:
        try:
            checkpoint("before_begin_immediate")
            cursor.execute("BEGIN IMMEDIATE")
        except Exception as exc:
            raise RecoveryActionError("begin_immediate_failed") from exc
        try:
            for index, statement in enumerate(statements):
                cursor.execute(statement)
                checkpoint(f"after_rename_{index}")
        except Exception as exc:
            raise RecoveryActionError(
                "restore_rename_failed" if status == "prepared" else "switch_rename_failed"
            ) from exc
        try:
            checkpoint("before_ledger_update")
            cursor.execute(
                "UPDATE runtime_schema_migrations SET status = ?, current_step = ? WHERE id = ?",
                (status, status, ledger_id),
            )
        except Exception as exc:
            raise RecoveryActionError("ledger_transition_failed") from exc
        try:
            checkpoint("before_commit")
            raw_connection.commit()
        except Exception as exc:
            raise RecoveryActionError("sqlite_action_commit_failed") from exc
    except Exception as exc:
        try:
            checkpoint("before_rollback")
            raw_connection.rollback()
        except Exception as rollback_exc:
            if not isinstance(exc, RecoveryActionError):
                raise RecoveryActionError("sqlite_action_rollback_failed") from rollback_exc
        raise
    finally:
        cursor.close()


def verify_and_mark_authoritative_schema(bind, migration):
    try:
        with bind.begin() as connection:
            manifest = build_authoritative_manifest(connection, _AUTHORITATIVE_LEARNING_TABLES)
            snapshot = inspect_authoritative_schema_snapshot(connection, {})
            foreign_key_violations = _foreign_key_check(connection)
            if (not _verified_manifest(migration, manifest)
                    or physical_schema_fingerprint(snapshot) != _target_physical_schema_fingerprint()
                    or foreign_key_violations):
                _fail_authoritative_recovery(
                    connection, migration, "recovery_verification_failed"
                )
            summary = {
                "target_version": AUTHORITATIVE_LEARNING_TARGET_VERSION,
                "canonical_physical_fingerprint": physical_schema_fingerprint(snapshot),
                "source_manifest": json.loads(migration.source_manifest_json)["tables"],
                "foreign_key_check": [],
                "cleanup_status": "pending",
            }
            _persist_migration(
                connection, migration, status="verified", current_step="verified",
                completed_at=utc_now(), failure_reason=None,
                verification_summary_json=json.dumps(summary, sort_keys=True),
            )
    except RuntimeError:
        raise
    except Exception:
        _record_recovery_failure(bind, migration, "canonical_verification_failed")
        raise RuntimeError("authoritative_learning_schema_recovery_failed")


def _remaining_controlled_objects(connection, controlled):
    existing = set(inspect(connection).get_table_names())
    return sorted(
        name for names in controlled.values() for name in names.values()
        if name in existing
    )


def cleanup_verified_authoritative_schema(bind, migration):
    controlled = None
    try:
        controlled = _controlled_objects(migration)
        with bind.begin() as connection:
            _drop_controlled_objects(connection, controlled)
            summary = json.loads(migration.verification_summary_json)
            summary["cleanup_status"] = "completed"
            summary.pop("remaining_controlled_objects", None)
            _persist_migration(
                connection, migration, failure_reason=None,
                verification_summary_json=json.dumps(summary, sort_keys=True),
            )
    except Exception:
        try:
            with bind.begin() as connection:
                current = _load_authoritative_learning_migration(connection)
                try:
                    summary = json.loads(current.verification_summary_json)
                except (TypeError, ValueError):
                    summary = {}
                summary["cleanup_status"] = "controlled_cleanup_failed"
                summary["remaining_controlled_objects"] = (
                    _remaining_controlled_objects(connection, controlled)
                    if controlled is not None else []
                )
                _persist_migration(
                    connection, current, failure_reason=None,
                    verification_summary_json=json.dumps(summary, sort_keys=True),
                )
        except Exception:
            pass


def _verified_cleanup_is_pending(migration):
    try:
        return json.loads(migration.verification_summary_json).get("cleanup_status") != "completed"
    except (TypeError, ValueError):
        return True


def verified_authoritative_migration_is_current(connection, migration):
    if (migration.migration_id != AUTHORITATIVE_LEARNING_MIGRATION_ID
            or migration.target_version != AUTHORITATIVE_LEARNING_TARGET_VERSION
            or migration.status != "verified"):
        return False
    tables = set(inspect(connection).get_table_names())
    return set(_AUTHORITATIVE_LEARNING_TABLES).issubset(tables)


def _finalize_authoritative_switch(bind, migration, controlled):
    verify_and_mark_authoritative_schema(bind, migration)
    with bind.begin() as connection:
        verified = _load_authoritative_learning_migration(connection)
    cleanup_verified_authoritative_schema(bind, verified)


def perform_sqlite_schema_switch(raw_connection, migration, controlled):
    statements = []
    for table_name in reversed(_AUTHORITATIVE_LEARNING_TABLES):
        statements.append(
            f"ALTER TABLE {_quote_sqlite_identifier(table_name)} RENAME TO "
            f"{_quote_sqlite_identifier(controlled[table_name]['backup'])}"
        )
    for table_name in _AUTHORITATIVE_LEARNING_TABLES:
        statements.append(
            f"ALTER TABLE {_quote_sqlite_identifier(controlled[table_name]['shadow'])} RENAME TO "
            f"{_quote_sqlite_identifier(table_name)}"
        )
    _run_sqlite_rename_transaction(raw_connection, statements, migration.id, "switched")


def restore_sqlite_schema_backup(raw_connection, migration, controlled):
    statements = []
    for table_name in reversed(_AUTHORITATIVE_LEARNING_TABLES):
        statements.append(
            f"ALTER TABLE {_quote_sqlite_identifier(table_name)} RENAME TO "
            f"{_quote_sqlite_identifier(controlled[table_name]['shadow'])}"
        )
    for table_name in _AUTHORITATIVE_LEARNING_TABLES:
        statements.append(
            f"ALTER TABLE {_quote_sqlite_identifier(controlled[table_name]['backup'])} RENAME TO "
            f"{_quote_sqlite_identifier(table_name)}"
        )
    _run_sqlite_rename_transaction(raw_connection, statements, migration.id, "prepared")


def _perform_switch_from_snapshot(bind, migration, controlled, checkpoint):
    if migration.status in {"prepared", "staged"}:
        def rebuild_shadow(_raw):
            with bind.begin() as connection:
                _drop_controlled_shadows(connection, controlled)
                _persist_migration(
                    connection, migration, status="prepared", current_step="prepared",
                    verification_summary_json="{}",
                )
            with bind.begin() as connection:
                prepared = _load_authoritative_learning_migration(connection)
            stage_authoritative_learning_schema_for_sqlite(bind, prepared, checkpoint)

        run_recovery_action(
            bind, migration, action_name="sqlite_restage", requires_foreign_keys_off=False,
            operation=rebuild_shadow,
        )
        run_recovery_action(
            bind, migration, action_name="sqlite_restage_switching",
            requires_foreign_keys_off=False,
            operation=lambda _raw: _mark_restage_switching(bind, migration),
        )
        with bind.begin() as connection:
            migration = _load_authoritative_learning_migration(connection)
    run_recovery_action(
        bind, migration, action_name="sqlite_switch", requires_foreign_keys_off=True,
        operation=lambda raw: perform_sqlite_schema_switch(raw, migration, controlled),
    )
    checkpoint("switched_committed_before_verify")
    _finalize_authoritative_switch(bind, migration, controlled)


def _mark_restage_switching(bind, migration):
    with bind.begin() as connection:
        staged = _load_authoritative_learning_migration(connection)
        _persist_migration(connection, staged, status="switching", current_step="switching")


def switch_authoritative_learning_schema_for_sqlite(bind, migration, checkpoint=lambda stage: None):
    if migration.status != "switching":
        with bind.begin() as connection:
            _persist_migration(connection, migration, status="switching", current_step="switching")
        checkpoint("switching_committed_before_begin")
        with bind.begin() as connection:
            migration = _load_authoritative_learning_migration(connection)
    recover_authoritative_learning_schema_for_sqlite(bind, migration, checkpoint)


def _restore_backup_from_snapshot(bind, migration, controlled, checkpoint):
    run_recovery_action(
        bind, migration, action_name="sqlite_restore", requires_foreign_keys_off=True,
        operation=lambda raw: restore_sqlite_schema_backup(raw, migration, controlled),
    )
    run_recovery_action(
        bind, migration, action_name="sqlite_restore_restage", requires_foreign_keys_off=False,
        operation=lambda _raw: _drop_controlled_shadows_for_restage(bind, migration, controlled),
    )
    prepared_holder = {}
    run_recovery_action(
        bind, migration, action_name="sqlite_restore_load_prepared",
        requires_foreign_keys_off=False,
        operation=lambda _raw: prepared_holder.setdefault(
            "migration", _load_prepared_recovery_migration(bind)
        ),
    )
    prepared = prepared_holder["migration"]
    _perform_switch_from_snapshot(bind, prepared, controlled, checkpoint)


def _load_prepared_recovery_migration(bind):
    with bind.begin() as connection:
        return _load_authoritative_learning_migration(connection)


def _drop_controlled_shadows_for_restage(bind, migration, controlled):
    with bind.begin() as connection:
        _drop_controlled_shadows(connection, controlled)
    with bind.begin() as connection:
        _persist_migration(
            connection, migration, status="prepared", current_step="prepared",
            verification_summary_json="{}",
        )


def recover_authoritative_learning_schema_for_sqlite(bind, migration, checkpoint=lambda stage: None):
    try:
        controlled = _controlled_objects(migration)
        with bind.begin() as connection:
            snapshot = build_sqlite_recovery_snapshot(connection, migration)
        decision = decide_sqlite_recovery(snapshot)
    except Exception:
        _record_recovery_failure(bind, migration, "invalid_recovery_snapshot")
        raise RuntimeError("authoritative_learning_schema_recovery_failed")
    if decision.action == "finalize_current":
        _finalize_authoritative_switch(bind, migration, controlled)
    elif decision.action == "perform_switch":
        _perform_switch_from_snapshot(bind, migration, controlled, checkpoint)
    elif decision.action == "restore_backup":
        _restore_backup_from_snapshot(bind, migration, controlled, checkpoint)
    else:
        _record_recovery_failure(
            bind, migration, decision.reason_code or "no_unambiguous_recovery_candidate"
        )
        raise RuntimeError("authoritative_learning_schema_recovery_failed")


def _constraint_signature(constraint):
    return tuple(column.name for column in constraint.columns)


def _authoritative_learning_schema_needs_upgrade(bind):
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    present = [name for name in _AUTHORITATIVE_LEARNING_TABLES if name in existing_tables]
    if not present:
        return False
    if len(present) != len(_AUTHORITATIVE_LEARNING_TABLES):
        return True
    for table_name in _AUTHORITATIVE_LEARNING_TABLES:
        target_table = Base.metadata.tables[table_name]
        current_columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        if any(
            column.name not in current_columns
            or (not column.nullable and current_columns[column.name]["nullable"])
            for column in target_table.columns
        ):
            return True
        expected_unique = {
            _constraint_signature(constraint)
            for constraint in target_table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        actual_unique = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints(table_name)
        }
        actual_unique.update(
            tuple(item["column_names"])
            for item in inspector.get_indexes(table_name) if item["unique"]
        )
        if not expected_unique.issubset(actual_unique):
            return True
        expected_foreign_keys = {
            (tuple(element.parent.name for element in constraint.elements),
             constraint.elements[0].column.table.name,
             tuple(element.column.name for element in constraint.elements))
            for constraint in target_table.foreign_key_constraints
        }
        actual_foreign_keys = {
            (tuple(item["constrained_columns"]), item["referred_table"],
             tuple(item["referred_columns"]))
            for item in inspector.get_foreign_keys(table_name)
        }
        if not expected_foreign_keys.issubset(actual_foreign_keys):
            return True
    return False


def _load_authoritative_learning_migration(connection):
    migration = RuntimeSchemaMigration.__table__
    row = connection.execute(
        migration.select().where(
            migration.c.migration_id == AUTHORITATIVE_LEARNING_MIGRATION_ID
        )
    ).mappings().first()
    return RuntimeSchemaMigration(**dict(row)) if row is not None else None


def load_or_prepare_authoritative_learning_migration(bind):
    migration = RuntimeSchemaMigration.__table__
    with bind.begin() as connection:
        existing = _load_authoritative_learning_migration(connection)
        if existing is not None:
            if existing.status == "recovery_failed":
                raise RuntimeError("authoritative_learning_schema_recovery_failed")
            return existing
        manifest = build_authoritative_manifest(connection, _AUTHORITATIVE_LEARNING_TABLES)
        connection.execute(migration.insert().values(
            migration_id=AUTHORITATIVE_LEARNING_MIGRATION_ID,
            target_version=AUTHORITATIVE_LEARNING_TARGET_VERSION,
            status="prepared",
            current_step="prepare",
            attempt_count=1,
            controlled_objects_json=json.dumps({
                table_name: {
                    role: controlled_sqlite_name(table_name, role)
                    for role in ("shadow", "backup")
                }
                for table_name in _AUTHORITATIVE_LEARNING_TABLES
            }, sort_keys=True),
            source_manifest_json=json.dumps(manifest, separators=(",", ":"), sort_keys=True),
            verification_summary_json="{}",
        ))
        return _load_authoritative_learning_migration(connection)


def _decide_sqlite_recovery_from_manifests(migration, candidates):
    expected_manifest = json.loads(migration.source_manifest_json)
    expected_fingerprint = expected_manifest.get("schema_fingerprint")
    matches = [
        name for name, candidate in candidates.items()
        if candidate.get("schema_fingerprint") == expected_fingerprint
        and candidate.get("tables") == expected_manifest.get("tables")
    ]
    if matches == ["current"]:
        return "continue_current"
    if "current" not in matches and matches == ["backup"]:
        return "restore_backup"
    return "fail_closed"


def decide_sqlite_recovery(snapshot):
    if snapshot.unexpected_controlled_objects:
        return RecoveryDecision("fail_closed", "unexpected_controlled_object")

    current = snapshot.current
    current_requires_backup_recovery = (
        snapshot.ledger_status in {"switching", "switched"}
        and not current.manifest_matches_source
        and snapshot.backup.discovered_names
    )
    if (current.discovered_names and not current_requires_backup_recovery and (
            not current.is_complete or not current.objects_are_tables
            or not current.manifest_matches_source or current.foreign_key_violations)):
        return RecoveryDecision("fail_closed", "untrusted_current_object")

    for candidate in (snapshot.shadow, snapshot.backup):
        if not candidate.discovered_names:
            continue
        rebuildable_shadow = (
            candidate.role == "shadow" and snapshot.ledger_status in {"prepared", "staged"}
            and candidate.is_complete and candidate.objects_are_tables
            and candidate.ledger_owned and not candidate.has_extra_objects
            and candidate.physical_v2_matches and candidate.safe_partial_data
            and not candidate.foreign_key_violations
        )
        requires_v2_schema = candidate.role == "shadow"
        if ((not candidate.is_complete or not candidate.objects_are_tables
                or not candidate.ledger_owned or candidate.has_extra_objects
                or not candidate.manifest_matches_source
                or (requires_v2_schema and not candidate.physical_v2_matches)
                or candidate.foreign_key_violations) and not rebuildable_shadow):
            return RecoveryDecision("fail_closed", "untrusted_controlled_object")

    if (snapshot.ledger_status == "switched"
            and current.is_complete and current.objects_are_tables
            and current.manifest_matches_source and current.physical_v2_matches
            and not current.foreign_key_violations and not snapshot.shadow.discovered_names
            and snapshot.backup.is_complete and snapshot.backup.objects_are_tables
            and snapshot.backup.manifest_matches_source
            and not snapshot.backup.foreign_key_violations):
        return RecoveryDecision("finalize_current")
    if (snapshot.ledger_status in {"prepared", "staged", "switching"}
            and (not current.discovered_names or (
                current.is_complete and current.objects_are_tables
                and current.manifest_matches_source and not current.foreign_key_violations
            ))
            and snapshot.shadow.discovered_names and snapshot.shadow.is_complete
            and snapshot.shadow.objects_are_tables and snapshot.shadow.manifest_matches_source
            and snapshot.shadow.physical_v2_matches and not snapshot.shadow.foreign_key_violations
            and not snapshot.backup.discovered_names):
        return RecoveryDecision("perform_switch")
    if (snapshot.ledger_status in {"prepared", "staged"}
            and current.is_complete and current.objects_are_tables
            and current.manifest_matches_source and not current.physical_v2_matches
            and not current.foreign_key_violations
            and not snapshot.backup.discovered_names
            and (not snapshot.shadow.discovered_names or (
                snapshot.shadow.is_complete and snapshot.shadow.objects_are_tables
                and snapshot.shadow.ledger_owned and not snapshot.shadow.has_extra_objects
                and snapshot.shadow.physical_v2_matches
                and snapshot.shadow.safe_partial_data
                and not snapshot.shadow.foreign_key_violations
            ))):
        return RecoveryDecision("perform_switch")
    if (snapshot.ledger_status in {"switching", "switched"}
            and not current.manifest_matches_source
            and snapshot.backup.discovered_names
            and snapshot.backup.is_complete and snapshot.backup.objects_are_tables
            and snapshot.backup.manifest_matches_source
            and not snapshot.backup.foreign_key_violations
            and not snapshot.shadow.discovered_names):
        return RecoveryDecision("restore_backup")
    return RecoveryDecision("fail_closed", "no_unambiguous_recovery_candidate")


def _mysql_type_family(column_type):
    name = column_type.__class__.__name__.lower()
    if "int" in name:
        return "integer"
    if any(token in name for token in ("char", "string", "text")):
        return "string"
    if any(token in name for token in ("float", "double", "decimal", "numeric")):
        return "number"
    if any(token in name for token in ("date", "time")):
        return "datetime"
    return name


def _mysql_column_is_compatible(actual, expected):
    if _mysql_type_family(actual) != _mysql_type_family(expected):
        return False
    if _mysql_type_family(expected) == "string":
        expected_length = getattr(expected, "length", None)
        actual_length = getattr(actual, "length", None)
        if expected_length is not None and (
                actual_length is None or actual_length < expected_length):
            return False
    return True


_MYSQL_PHASE_THREE_TABLES = (
    "grading_result_records", "training_task_records", "mistake_records",
    "variation_sets", "variation_question_versions", "variation_rubrics",
)


def _preflight_mysql_phase_three_schema(inspector):
    methods = (
        "has_table", "get_columns", "get_indexes", "get_pk_constraint",
        "get_unique_constraints", "get_foreign_keys",
    )
    if any(not callable(getattr(inspector, method, None)) for method in methods):
        raise RuntimeError("phase3_mysql_schema_migration_failed")
    relevant_tables = set(_MYSQL_PHASE_THREE_TABLES)
    for table_name in _MYSQL_PHASE_THREE_TABLES:
        relevant_tables.update(
            constraint.elements[0].column.table.name
            for constraint in Base.metadata.tables[table_name].foreign_key_constraints
        )
    inventory = {}
    try:
        for table_name in sorted(relevant_tables):
            exists = bool(inspector.has_table(table_name))
            inventory[table_name] = {
                "exists": exists,
                "columns": tuple(inspector.get_columns(table_name)) if exists else (),
                "indexes": tuple(inspector.get_indexes(table_name)) if exists else (),
                "primary_key": inspector.get_pk_constraint(table_name) if exists else {},
                "uniques": tuple(inspector.get_unique_constraints(table_name)) if exists else (),
                "foreign_keys": tuple(inspector.get_foreign_keys(table_name)) if exists else (),
            }
    except Exception:
        raise RuntimeError("phase3_mysql_schema_migration_failed") from None
    return inventory


def _mysql_remote_key_is_indexed(table_inventory, remote_columns):
    if not remote_columns:
        return False
    primary_key = table_inventory.get("primary_key")
    if not isinstance(primary_key, dict):
        return False
    exact_keys = [primary_key.get("constrained_columns")]
    exact_keys.extend(item.get("column_names") for item in table_inventory["uniques"])
    exact_keys.extend(
        index.get("column_names") for index in table_inventory["indexes"]
        if index.get("unique")
    )
    if any(tuple(columns or ()) == remote_columns for columns in exact_keys):
        return True
    for index in table_inventory["indexes"]:
        columns = index.get("column_names")
        if not isinstance(columns, (list, tuple)) or any(not column for column in columns):
            continue
        if tuple(columns[:len(remote_columns)]) == remote_columns:
            return True
    return False


def _mysql_planned_remote_key_is_indexed(table, remote_columns):
    exact_keys = [tuple(column.name for column in table.primary_key.columns)]
    exact_keys.extend(
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
    )
    exact_keys.extend(
        tuple(column.name for column in index.columns)
        for index in table.indexes if index.unique
    )
    if remote_columns in exact_keys:
        return True
    return any(
        tuple(column.name for column in index.columns)[:len(remote_columns)] == remote_columns
        for index in table.indexes
    )


def _preflight_mysql_phase_three_foreign_key_targets(inventory):
    for table_name in _MYSQL_PHASE_THREE_TABLES:
        constraints = Base.metadata.tables[table_name].foreign_key_constraints
        for constraint in constraints:
            remote_table = constraint.elements[0].column.table.name
            remote_columns = tuple(element.column.name for element in constraint.elements)
            remote_inventory = inventory.get(remote_table)
            if not remote_inventory:
                raise RuntimeError("phase3_mysql_schema_migration_failed")
            if remote_inventory["exists"]:
                is_indexed = _mysql_remote_key_is_indexed(remote_inventory, remote_columns)
                if not is_indexed and remote_table in _MYSQL_PHASE_THREE_TABLES:
                    is_indexed = _mysql_planned_remote_key_is_indexed(
                        Base.metadata.tables[remote_table], remote_columns
                    )
            elif remote_table in _MYSQL_PHASE_THREE_TABLES:
                is_indexed = _mysql_planned_remote_key_is_indexed(
                    Base.metadata.tables[remote_table], remote_columns
                )
            else:
                is_indexed = False
            if not is_indexed:
                raise RuntimeError("phase3_mysql_schema_migration_failed")


def _repair_mysql_phase_three_schema(bind, inventory):
    _preflight_mysql_phase_three_foreign_key_targets(inventory)
    ddl_plan = []
    table_names = _MYSQL_PHASE_THREE_TABLES
    safe_defaults = {
        "audit_generation": "0", "status": "'published'", "scope": "'user'",
        "position": "0", "schema_version": "'v1'", "rubric_json": "'{}'",
    }
    dependency_columns = {
        "grading_result_records": {"audit_generation"},
        "training_task_records": {"claim_owner", "claim_expires_at"},
        "mistake_records": {"attempt_item_id", "question_version_id"},
    }
    dependency_definitions = {
        ("grading_result_records", "audit_generation"): "INT NOT NULL DEFAULT 0",
        ("training_task_records", "claim_owner"): "VARCHAR(64) NULL",
        ("training_task_records", "claim_expires_at"): "DATETIME NULL",
        ("mistake_records", "attempt_item_id"): "VARCHAR(120) NULL",
        ("mistake_records", "question_version_id"): "VARCHAR(120) NULL",
    }
    created_tables = set()
    for table_name in table_names:
        table = Base.metadata.tables[table_name]
        table_inventory = inventory[table_name]
        if not table_inventory["exists"]:
            ddl_plan.append(("table", table))
            created_tables.add(table_name)
            continue
        reflected = {column["name"]: column for column in table_inventory["columns"]}
        managed_columns = dependency_columns.get(table_name)
        for column in table.columns:
            existing = reflected.get(column.name)
            if existing is not None:
                if ("type" in existing and (
                        not _mysql_column_is_compatible(existing["type"], column.type)
                        or ("nullable" in existing
                            and bool(existing["nullable"]) != column.nullable))):
                    raise RuntimeError("phase3_mysql_schema_migration_failed")
                continue
            if managed_columns is not None and column.name not in managed_columns:
                raise RuntimeError("phase3_mysql_schema_migration_failed")
            default = safe_defaults.get(column.name)
            if not column.nullable and default is None:
                raise RuntimeError("phase3_mysql_schema_migration_failed")
            definition = dependency_definitions.get((table_name, column.name))
            if definition is None:
                definition = column.type.compile(dialect=bind.dialect)
                if column.nullable:
                    definition += " NULL"
                else:
                    definition += f" NOT NULL DEFAULT {default}"
            ddl_plan.append(("sql", f"ALTER TABLE {table_name} ADD COLUMN {column.name} {definition}"))
            reflected[column.name] = {
                "name": column.name, "type": column.type, "nullable": column.nullable,
            }

    for table_name in table_names:
        table = Base.metadata.tables[table_name]
        managed_columns = dependency_columns.get(table_name)
        if table_name in created_tables:
            continue
        table_inventory = inventory[table_name]
        if not table_inventory["exists"]:
            continue
        current_columns = {column["name"] for column in table_inventory["columns"]}
        planned_columns = {
            operation.split()[5] for kind, operation in ddl_plan
            if kind == "sql" and operation.startswith(f"ALTER TABLE {table_name} ADD COLUMN ")
        }
        available_columns = current_columns | planned_columns
        existing_indexes = {
            tuple(index.get("column_names") or ())
            for index in table_inventory["indexes"]
        }
        existing_uniques = {
            tuple(item.get("column_names") or ())
            for item in table_inventory["uniques"]
        }
        existing_uniques.update(
            tuple(index.get("column_names") or ())
            for index in table_inventory["indexes"] if index.get("unique")
        )
        for index in table.indexes:
            column_names = tuple(column.name for column in index.columns)
            if index.unique or column_names in existing_indexes or not set(column_names) <= available_columns:
                continue
            ddl_plan.append(("sql", f"CREATE INDEX {index.name} ON {table_name}({','.join(column_names)})"))
        expected_uniques = {
            tuple(column.name for column in constraint.columns): constraint.name
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        expected_uniques.update({
            tuple(column.name for column in index.columns): None
            for index in table.indexes if index.unique
        })
        for column_names, constraint_name in expected_uniques.items():
            if column_names in existing_uniques or not set(column_names) <= available_columns:
                continue
            name = constraint_name or f"uq_{table_name}_{'_'.join(column_names)}"
            columns_sql = ",".join(column_names)
            with bind.begin() as connection:
                duplicate_count = connection.execute(text(
                    f"SELECT COUNT(*) FROM (SELECT {columns_sql} FROM {table_name} "
                    f"GROUP BY {columns_sql} HAVING COUNT(*) > 1) AS duplicate_values"
                )).scalar_one()
                if duplicate_count:
                    raise RuntimeError("phase3_mysql_schema_migration_failed")
            ddl_plan.append(("sql", f"ALTER TABLE {table_name} ADD CONSTRAINT {name} UNIQUE ({columns_sql})"))
        existing_fks = {
            (tuple(fk.get("constrained_columns") or ()), fk.get("referred_table"),
             tuple(fk.get("referred_columns") or ()))
            for fk in table_inventory["foreign_keys"]
        }
        for constraint in table.foreign_key_constraints:
            local = tuple(element.parent.name for element in constraint.elements)
            remote_table = constraint.elements[0].column.table.name
            remote = tuple(element.column.name for element in constraint.elements)
            signature = (local, remote_table, remote)
            if signature in existing_fks or not set(local) <= available_columns:
                continue
            if not inventory.get(remote_table, {}).get("exists"):
                raise RuntimeError("phase3_mysql_schema_migration_failed")
            remote_inventory = {
                column["name"]: column
                for column in inventory[remote_table]["columns"]
            }
            for remote_name, element in zip(remote, constraint.elements):
                existing_remote = remote_inventory.get(remote_name)
                if existing_remote is None or (
                    "type" in existing_remote and not _mysql_column_is_compatible(
                        existing_remote["type"], element.column.type
                    )
                ):
                    raise RuntimeError("phase3_mysql_schema_migration_failed")
            name = constraint.name or f"fk_{table_name}_{'_'.join(local)}"
            join_conditions = " AND ".join(
                f"child.{local_column} = parent.{remote_column}"
                for local_column, remote_column in zip(local, remote)
            )
            non_null = " AND ".join(
                f"child.{local_column} IS NOT NULL" for local_column in local
            )
            missing_parent = " AND ".join(
                f"parent.{remote_column} IS NULL" for remote_column in remote
            )
            if set(local) <= current_columns:
                with bind.begin() as connection:
                    orphan_count = connection.execute(text(
                        f"SELECT COUNT(*) FROM {table_name} AS child "
                        f"LEFT JOIN {remote_table} AS parent ON {join_conditions} "
                        f"WHERE {non_null} AND {missing_parent}"
                    )).scalar_one()
                    if orphan_count:
                        raise RuntimeError("phase3_mysql_schema_migration_failed")
            ddl_plan.append(("sql", f"ALTER TABLE {table_name} ADD CONSTRAINT {name} FOREIGN KEY ({','.join(local)}) REFERENCES {remote_table}({','.join(remote)})"))


    for kind, operation in ddl_plan:
        if kind == "table":
            operation.create(bind=bind, checkfirst=True)
        else:
            with bind.begin() as connection:
                connection.execute(text(operation))


_CASE_TRAINING_TABLES = (
    "case_definition_records",
    "case_version_records",
    "case_session_records",
    "case_session_message_records",
    "case_help_records",
)


def _ensure_case_training_tables(bind):
    Base.metadata.create_all(
        bind=bind,
        tables=[Base.metadata.tables[table_name] for table_name in _CASE_TRAINING_TABLES],
    )


def _ensure_paper_item_snapshot_column(bind):
    inspector = inspect(bind)
    if "paper_items" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("paper_items")}
    if "standard_answer_snapshot" in columns:
        return
    with bind.begin() as connection:
        _add_column_if_missing_after_race(
            connection,
            "paper_items",
            "standard_answer_snapshot",
            "ALTER TABLE paper_items ADD COLUMN standard_answer_snapshot TEXT NOT NULL DEFAULT ''",
        )


def ensure_runtime_schema_for(bind, checkpoint=lambda stage: None):
    """Apply small additive schema updates that create_all will not add to existing tables."""
    if bind.dialect.name == "mysql":
        if not hasattr(bind.dialect, "get_columns"):
            return
        try:
            inventory = _preflight_mysql_phase_three_schema(inspect(bind))
            _repair_mysql_phase_three_schema(bind, inventory)
            if hasattr(bind, "connect"):
                _ensure_case_training_tables(bind)
                _ensure_core_learning_contract_tables(bind)
                _ensure_formal_content_tables(bind)
        except RuntimeError as exc:
            if str(exc) == "phase3_mysql_schema_migration_failed":
                raise
            raise RuntimeError("phase3_mysql_schema_migration_failed") from None
        except Exception:
            raise RuntimeError("phase3_mysql_schema_migration_failed") from None
        return
    if bind.dialect.name != "sqlite":
        return
    RuntimeSchemaMigration.__table__.create(bind=bind, checkfirst=True)
    with bind.begin() as connection:
        existing_migration = _load_authoritative_learning_migration(connection)
    if existing_migration is not None:
        if existing_migration.status == "recovery_failed":
            raise RuntimeError("authoritative_learning_schema_recovery_failed")
        if existing_migration.status == "verified":
            with bind.begin() as connection:
                is_current = verified_authoritative_migration_is_current(
                    connection, existing_migration
                )
            if not is_current:
                _record_recovery_failure(
                    bind, existing_migration, "verified_target_confirmation_failed"
                )
                raise RuntimeError("authoritative_learning_schema_recovery_failed")
            if _verified_cleanup_is_pending(existing_migration):
                cleanup_verified_authoritative_schema(bind, existing_migration)
            Base.metadata.create_all(
                bind=bind,
                tables=[
                    Base.metadata.tables["question_version_records"],
                    Base.metadata.tables["question_kp_link_records"],
                    Base.metadata.tables["paper_instances"],
                    Base.metadata.tables["paper_items"],
                    Base.metadata.tables["variation_sets"],
                    Base.metadata.tables["variation_question_versions"],
                    Base.metadata.tables["variation_rubrics"],
                    Base.metadata.tables["paper_answers"],
                    Base.metadata.tables["paper_submissions"],
                    *[Base.metadata.tables[table_name] for table_name in _CASE_TRAINING_TABLES],
                ],
            )
            _ensure_paper_item_snapshot_column(bind)
            _ensure_core_learning_contract_tables(bind)
            _ensure_formal_content_tables(bind)
            return
        if existing_migration.status in {"prepared", "staged", "switching", "switched"}:
            recover_authoritative_learning_schema_for_sqlite(bind, existing_migration, checkpoint)
            existing_migration = None
    needs_authoritative_upgrade = _authoritative_learning_schema_needs_upgrade(bind)
    tables = [
        table for table_name, table in Base.metadata.tables.items()
        if not needs_authoritative_upgrade or table_name not in _AUTHORITATIVE_LEARNING_TABLES
    ]
    Base.metadata.create_all(bind=bind, tables=tables)
    _ensure_formal_content_tables(bind)
    if needs_authoritative_upgrade:
        migration = load_or_prepare_authoritative_learning_migration(bind)
        if migration.status == "prepared":
            stage_authoritative_learning_schema_for_sqlite(bind, migration, checkpoint)
            with bind.begin() as connection:
                migration = _load_authoritative_learning_migration(connection)
        if migration.status == "staged":
            switch_authoritative_learning_schema_for_sqlite(bind, migration, checkpoint)
    inspector = inspect(bind)
    session_columns = {col["name"] for col in inspector.get_columns("sessions")}
    if "title_auto_enabled" not in session_columns:
        with bind.begin() as conn:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN title_auto_enabled TINYINT(1) NOT NULL DEFAULT 1"))
            conn.execute(text("UPDATE sessions SET title_auto_enabled = 0 WHERE title IS NOT NULL AND title <> '' AND title <> '新对话'"))
    inspector = inspect(bind)
    session_columns = {col["name"] for col in inspector.get_columns("sessions")}
    message_columns = {col["name"] for col in inspector.get_columns("messages")}
    memory_columns = {col["name"] for col in inspector.get_columns("personalization_memories")}
    candidate_columns = {col["name"] for col in inspector.get_columns("memory_candidates")}
    profile_columns = {col["name"] for col in inspector.get_columns("user_profiles")}
    training_task_columns = {col["name"] for col in inspector.get_columns("training_task_records")}
    mistake_columns = {col["name"] for col in inspector.get_columns("mistake_records")}
    grading_columns = {col["name"] for col in inspector.get_columns("grading_result_records")}
    case_session_columns = {col["name"] for col in inspector.get_columns("case_session_records")} if "case_session_records" in inspector.get_table_names() else set()
    with bind.begin() as conn:
        if case_session_columns:
            for column_name, definition in (
                ("mode", "VARCHAR(40) NOT NULL DEFAULT 'full'"),
                ("status", "VARCHAR(40) NOT NULL DEFAULT 'created'"),
                ("learner_messages", "INTEGER NOT NULL DEFAULT 0"),
                ("scoring_enabled", "INTEGER NOT NULL DEFAULT 1"),
                ("help_used", "INTEGER NOT NULL DEFAULT 0"),
                ("expires_at", "DATETIME NULL"),
            ):
                if column_name not in case_session_columns:
                    _add_column_if_missing_after_race(
                        conn,
                        "case_session_records",
                        column_name,
                        f"ALTER TABLE case_session_records ADD COLUMN {column_name} {definition}",
                    )
        if "audit_generation" not in grading_columns:
            _add_column_if_missing_after_race(
                conn,
                "grading_result_records",
                "audit_generation",
                "ALTER TABLE grading_result_records ADD COLUMN audit_generation INT NOT NULL DEFAULT 0",
            )
        if "active_leaf_message_id" not in session_columns:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN active_leaf_message_id INT NULL"))
            conn.execute(text("CREATE INDEX idx_sessions_active_leaf_message_id ON sessions(active_leaf_message_id)"))
        if "parent_id" not in message_columns:
            conn.execute(text("ALTER TABLE messages ADD COLUMN parent_id INT NULL"))
            conn.execute(text("CREATE INDEX idx_messages_parent_id ON messages(parent_id)"))
        if "superseded_by" not in memory_columns:
            conn.execute(text("ALTER TABLE personalization_memories ADD COLUMN superseded_by INT NULL"))
            conn.execute(text("CREATE INDEX idx_personalization_memories_superseded_by ON personalization_memories(superseded_by)"))
        if "superseded_at" not in memory_columns:
            conn.execute(text("ALTER TABLE personalization_memories ADD COLUMN superseded_at DATETIME NULL"))
        if "conflict_key" not in memory_columns:
            conn.execute(text("ALTER TABLE personalization_memories ADD COLUMN conflict_key VARCHAR(120) NULL"))
            conn.execute(text("CREATE INDEX idx_personalization_memories_conflict_key ON personalization_memories(conflict_key)"))
        if "confidence" not in memory_columns:
            conn.execute(text("ALTER TABLE personalization_memories ADD COLUMN confidence FLOAT NOT NULL DEFAULT 0.8"))
        if "confidence" not in candidate_columns:
            conn.execute(text("ALTER TABLE memory_candidates ADD COLUMN confidence FLOAT NOT NULL DEFAULT 0.8"))
        if "survey_json" not in profile_columns:
            conn.execute(text("ALTER TABLE user_profiles ADD COLUMN survey_json TEXT"))
        if "locked_fields_json" not in profile_columns:
            conn.execute(text("ALTER TABLE user_profiles ADD COLUMN locked_fields_json TEXT"))
        if "lock_reason_json" not in profile_columns:
            conn.execute(text("ALTER TABLE user_profiles ADD COLUMN lock_reason_json TEXT"))
        if "evidence_pack_json" not in training_task_columns:
            _add_column_if_missing_after_race(
                conn,
                "training_task_records",
                "evidence_pack_json",
                "ALTER TABLE training_task_records ADD COLUMN evidence_pack_json TEXT",
            )
            conn.execute(text("UPDATE training_task_records SET evidence_pack_json = '{}' WHERE evidence_pack_json IS NULL"))
        if "claim_owner" not in training_task_columns:
            _add_column_if_missing_after_race(
                conn, "training_task_records", "claim_owner",
                "ALTER TABLE training_task_records ADD COLUMN claim_owner VARCHAR(64) NULL",
            )
        if "claim_expires_at" not in training_task_columns:
            _add_column_if_missing_after_race(
                conn, "training_task_records", "claim_expires_at",
                "ALTER TABLE training_task_records ADD COLUMN claim_expires_at DATETIME NULL",
            )
        if "attempt_item_id" not in mistake_columns:
            _add_column_if_missing_after_race(
                conn, "mistake_records", "attempt_item_id",
                "ALTER TABLE mistake_records ADD COLUMN attempt_item_id VARCHAR(120) NULL",
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_mistake_records_attempt_item_id ON mistake_records(attempt_item_id)"))
        if "question_version_id" not in mistake_columns:
            _add_column_if_missing_after_race(
                conn, "mistake_records", "question_version_id",
                "ALTER TABLE mistake_records ADD COLUMN question_version_id VARCHAR(120) NULL",
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_mistake_records_question_version_id ON mistake_records(question_version_id)"))

        session_ids = [row[0] for row in conn.execute(text("SELECT id FROM sessions"))]
        for sid in session_ids:
            rows = conn.execute(text("SELECT id, parent_id FROM messages WHERE session_id = :sid ORDER BY id ASC"), {"sid": sid}).fetchall()
            previous_id = None
            for message_id, parent_id in rows:
                if parent_id is None and previous_id is not None:
                    conn.execute(text("UPDATE messages SET parent_id = :pid WHERE id = :mid"), {"pid": previous_id, "mid": message_id})
                previous_id = message_id
            if previous_id:
                conn.execute(text("UPDATE sessions SET active_leaf_message_id = COALESCE(active_leaf_message_id, :mid) WHERE id = :sid"), {"mid": previous_id, "sid": sid})
    _ensure_paper_item_snapshot_column(bind)
    _ensure_core_learning_contract_tables(bind)
    _ensure_formal_content_tables(bind)


def ensure_runtime_schema():
    ensure_runtime_schema_for(engine)


ensure_runtime_schema()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
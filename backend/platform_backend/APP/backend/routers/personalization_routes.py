from APP.backend.system_data_service import build_learning_trends
from APP.backend.time_utils import utc_now
import json
import functools
from datetime import date, datetime, timedelta
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from typing import Optional

from APP.backend.auth import get_current_user
from APP.backend.config import SHORT_TERM_MEMORY_DAYS
from APP.backend.database import get_db, UserModel, PersonalizationMemory, MemoryCandidate, MemorySummary, AgentEvent
from APP.backend.health_memory import get_or_create_profile, memory_rw_lock, resolve_personalization_conflicts
from APP.backend.learner_profile_service import (
    apply_learner_profile_update,
    build_learner_profile_payload,
    parse_json_field,
    serialize_json_field,
)
from APP.backend import exam_learning_service
from APP.backend import learning_governance_service
from APP.backend.learning_target_service import (
    LearningTargetLockedError,
    LearningTargetValidationError,
    get_active_learning_target,
    list_learning_targets,
    requires_official_exam_repository,
    serialize_learning_target,
    set_active_learning_target,
)

router = APIRouter(prefix="/personalization", tags=["Personalization"])

MAX_MARKDOWN_UPLOAD_BYTES = 1024 * 1024
MAX_MARKDOWN_MEMORY_SECTIONS = 100
MAX_MARKDOWN_MEMORY_SECTION_CHARS = 10_000
MAX_MARKDOWN_MEMORY_TOTAL_CHARS = 500_000


def _with_memory_write_lock(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with memory_rw_lock.write():
            return func(*args, **kwargs)
    return wrapper


def _with_memory_read_lock(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with memory_rw_lock.read():
            return func(*args, **kwargs)
    return wrapper

class ProfileUpdate(BaseModel):
    display_name: Optional[str] = None
    constitution: Optional[str] = None
    health_goals: Optional[str] = None
    diet_restrictions: Optional[str] = None
    exercise_preferences: Optional[str] = None
    medical_history: Optional[str] = None
    custom_needs: Optional[str] = None

class LearnerProfileUpdate(BaseModel):
    display_name: Optional[str] = None
    learner_group: Optional[str] = None
    learning_goal: Optional[str] = None
    education_major: Optional[str] = None
    learning_background: Optional[str] = None
    time_constraints: Optional[str] = None
    resource_preferences: Optional[str] = None
    current_difficulties: Optional[str] = None
    learning_needs: Optional[str] = None
    learning_habits: Optional[str] = None
    locked_fields: Optional[list[str]] = None
    lock_reason: Optional[dict[str, str]] = None


class LearnerSettingsUpdate(BaseModel):
    analysis_frequency: Optional[str] = None
    locked_fields: Optional[list[str]] = None
    notification_preferences: Optional[dict] = None


class LearningTargetUpdate(BaseModel):
    target_type: str
    exam_track_id: str
    exam_date: Optional[date] = None
    is_locked: bool = True
    lock_reason: str = "用户手动选择"


class LearningTargetBatchUpdate(BaseModel):
    exam_track_ids: list[str]
    current_exam_track_id: str


class MemoryCreate(BaseModel):
    category: str = "short_term"
    importance: str = "normal"
    title: str = ""
    content: str
    expires_at: Optional[datetime] = None

class MemoryUpdate(BaseModel):
    category: Optional[str] = None
    importance: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    expires_at: Optional[datetime] = None

class CandidateCreate(BaseModel):
    title: str = ""
    content: str
    importance: str = "normal"
    reason: str = ""
    session_id: Optional[str] = None

class CandidateUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    importance: Optional[str] = None
    reason: Optional[str] = None
    status: Optional[str] = None

class CandidatePromote(BaseModel):
    category: str = "short_term"  # short_term / long_term / preference / note
    importance: str = "normal"
    expires_at: Optional[datetime] = None

def serialize_memory(row: PersonalizationMemory):
    return {
        "id": row.id,
        "category": row.category,
        "importance": row.importance,
        "title": row.title,
        "content": row.content,
        "source": row.source,
        "is_active": row.is_active,
        "superseded_by": row.superseded_by,
        "superseded_at": row.superseded_at.isoformat() if row.superseded_at else None,
        "conflict_key": row.conflict_key,
        "confidence": row.confidence,
        "created_at": row.created_at.timestamp() if row.created_at else None,
        "updated_at": row.updated_at.timestamp() if row.updated_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }

def serialize_candidate(row: MemoryCandidate):
    return {
        "id": row.id,
        "session_id": row.session_id,
        "title": row.title,
        "content": row.content,
        "importance": row.importance,
        "reason": row.reason,
        "source": row.source,
        "status": row.status,
        "promoted_memory_id": row.promoted_memory_id,
        "created_at": row.created_at.timestamp() if row.created_at else None,
        "updated_at": row.updated_at.timestamp() if row.updated_at else None,
    }


def learner_profile_response(profile):
    payload = build_learner_profile_payload(profile)
    survey = parse_json_field(getattr(profile, "survey_json", "{}"), {})
    survey = survey if isinstance(survey, dict) else {}
    payload["locked_fields"] = parse_json_field(getattr(profile, "locked_fields_json", "[]"), [])
    payload["lock_reason"] = parse_json_field(getattr(profile, "lock_reason_json", "{}"), {})
    payload["survey"] = survey
    return payload


def _sync_personalization_conflicts(db: Session, user_id: int) -> None:
    """Compatibility hook that now performs deterministic expiry only."""
    changed = resolve_personalization_conflicts(db, user_id)
    if changed:
        db.commit()

def split_markdown_memories(text: str, fallback_title: str) -> list[dict]:
    chunks = []
    current_title = fallback_title
    current_lines = []
    for line in text.splitlines():
        if line.startswith("#"):
            content = "\n".join(current_lines).strip()
            if content:
                chunks.append({"title": current_title.strip("# ") or fallback_title, "content": content})
            current_title = line.strip("# ").strip() or fallback_title
            current_lines = []
        else:
            current_lines.append(line)
    content = "\n".join(current_lines).strip()
    if content:
        chunks.append({"title": current_title.strip("# ") or fallback_title, "content": content})
    if not chunks and text.strip():
        chunks.append({"title": fallback_title, "content": text.strip()})
    return chunks


def _survey_settings(profile) -> dict:
    survey = parse_json_field(getattr(profile, "survey_json", "{}"), {})
    settings = survey.get("settings") if isinstance(survey, dict) else {}
    return settings if isinstance(settings, dict) else {}


@router.get("/learning-target")
def get_learning_target(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return {"target": serialize_learning_target(get_active_learning_target(db, current_user.id))}


@router.get("/learning-targets")
def get_learning_targets(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items = [serialize_learning_target(item) for item in list_learning_targets(db, current_user.id)]
    return {
        "items": items,
        "current": next((item for item in items if item and item.get("is_active")), None),
    }


@router.post("/learning-targets")
def enroll_learning_targets(
    body: LearningTargetBatchUpdate,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    exam_track_ids = list(dict.fromkeys(
        str(item).strip() for item in body.exam_track_ids if str(item).strip()
    ))
    if not exam_track_ids:
        raise HTTPException(status_code=422, detail="至少选择一项考试")
    if body.current_exam_track_id not in exam_track_ids:
        raise HTTPException(status_code=422, detail="当前考试必须属于已选择考试")
    try:
        # Persist all enrollments without changing the final current target.
        for exam_track_id in exam_track_ids:
            set_active_learning_target(
                db,
                user_id=current_user.id,
                target_type="certification",
                exam_track_id=exam_track_id,
                repository=None,
                is_locked=True,
                lock_reason="用户手动选择",
                source="manual",
                commit=False,
            )
        current = set_active_learning_target(
            db,
            user_id=current_user.id,
            target_type="certification",
            exam_track_id=body.current_exam_track_id,
            repository=None,
            is_locked=True,
            lock_reason="用户手动选择",
            source="manual",
            commit=True,
        )
    except LearningTargetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LearningTargetLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    items = [serialize_learning_target(item) for item in list_learning_targets(db, current_user.id)]
    return {"success": True, "target": serialize_learning_target(current), "items": items}


@router.put("/learning-target/current")
def switch_current_learning_target(
    body: LearningTargetUpdate,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return update_learning_target(body, current_user, db)


@router.put("/learning-target")
def update_learning_target(
    body: LearningTargetUpdate,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        target_repository = (
            exam_learning_service.get_official_exam_repository()
            if requires_official_exam_repository(body.target_type, body.exam_track_id)
            else None
        )
        target = set_active_learning_target(
            db,
            user_id=current_user.id,
            target_type=body.target_type,
            exam_track_id=body.exam_track_id,
            repository=target_repository,
            exam_date=body.exam_date,
            is_locked=body.is_locked,
            lock_reason=body.lock_reason,
            source="manual",
        )
    except LearningTargetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LearningTargetLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "target": serialize_learning_target(target)}


@router.get("/profile")
def get_profile(current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = get_or_create_profile(db, current_user.id)
    return {k: getattr(profile, k) for k in ["display_name", "constitution", "health_goals", "diet_restrictions", "exercise_preferences", "medical_history", "custom_needs"]}

@router.put("/profile")
def update_profile(body: ProfileUpdate, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = get_or_create_profile(db, current_user.id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, key, value)
    db.commit()
    return {"success": True}

@router.get("/learner-profile")
def get_learner_profile(current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = get_or_create_profile(db, current_user.id)
    return learner_profile_response(profile)

@router.put("/learner-profile")
def update_learner_profile(body: LearnerProfileUpdate, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = get_or_create_profile(db, current_user.id)
    data = body.model_dump(exclude_unset=True)
    locked_fields = data.pop("locked_fields", None)
    lock_reason = data.pop("lock_reason", None)
    education_major = data.pop("education_major", None)
    learning_background = data.pop("learning_background", None)
    learning_habits = data.pop("learning_habits", None)
    apply_learner_profile_update(profile, data, source="manual")
    if education_major is not None or learning_background is not None or learning_habits is not None:
        survey = parse_json_field(getattr(profile, "survey_json", "{}"), {})
        survey = survey if isinstance(survey, dict) else {}
        background = survey.get("background") if isinstance(survey.get("background"), dict) else {}
        if education_major is not None:
            survey["education_major"] = education_major
            background["education_major"] = education_major
            background["major_or_role"] = education_major
            survey["background"] = background
        if learning_background is not None:
            survey["learning_background"] = learning_background
        if learning_habits is not None:
            survey["learning_habits"] = learning_habits
        profile.survey_json = serialize_json_field(survey)
    if locked_fields is not None:
        profile.locked_fields_json = serialize_json_field(locked_fields)
    if lock_reason is not None:
        profile.lock_reason_json = serialize_json_field(lock_reason)
    db.commit()
    return {"success": True, "profile": learner_profile_response(profile)}


@router.get("/learner-settings")
def get_learner_settings(current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = get_or_create_profile(db, current_user.id)
    notification_preferences = learning_governance_service.serialize_notification_preferences(
        learning_governance_service.get_notification_preferences(db, current_user.id)
    )
    return {
        "settings": {
            "analysis_frequency": "daily",
            **_survey_settings(profile),
            "notification_preferences": notification_preferences,
        },
        "locked_fields": parse_json_field(getattr(profile, "locked_fields_json", "[]"), []),
        "lock_reason": parse_json_field(getattr(profile, "lock_reason_json", "{}"), {}),
    }


@router.put("/learner-settings")
def update_learner_settings(body: LearnerSettingsUpdate, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = get_or_create_profile(db, current_user.id)
    survey = parse_json_field(getattr(profile, "survey_json", "{}"), {})
    if not isinstance(survey, dict):
        survey = {}
    settings = survey.get("settings") if isinstance(survey.get("settings"), dict) else {}
    if body.analysis_frequency is not None:
        settings = {**settings, "analysis_frequency": body.analysis_frequency}
        survey["settings"] = settings
        profile.survey_json = json.dumps(survey, ensure_ascii=False)
    if body.locked_fields is not None:
        next_locked_fields = list(body.locked_fields)
        existing_reasons = parse_json_field(getattr(profile, "lock_reason_json", "{}"), {})
        if not isinstance(existing_reasons, dict):
            existing_reasons = {}
        next_reasons = {
            field: existing_reasons.get(field, "用户在设置页锁定")
            for field in next_locked_fields
        }
        profile.locked_fields_json = serialize_json_field(next_locked_fields)
        profile.lock_reason_json = serialize_json_field(next_reasons)
    if body.notification_preferences is not None:
        learning_governance_service.update_notification_preferences(
            db, current_user.id, body.notification_preferences
        )
    db.commit()
    return get_learner_settings(current_user=current_user, db=db)


@router.get("/learning-trends")
def learning_trends(
    days: int = Query(default=30, ge=7, le=90),
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if days not in {7, 30, 90}:
        raise HTTPException(status_code=422, detail="days must be one of: 7, 30, 90")
    return build_learning_trends(db, user_id=current_user.id, days=days)


@router.get("/overview")
def overview(current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    # S 锁：get_or_create_profile 自管理锁；统计查询持共享读锁。
    profile = get_or_create_profile(db, current_user.id)
    with memory_rw_lock.read():
        active_rows = db.query(PersonalizationMemory).filter(PersonalizationMemory.user_id == current_user.id, PersonalizationMemory.is_active == True).all()
        inactive_count = db.query(PersonalizationMemory).filter(PersonalizationMemory.user_id == current_user.id, PersonalizationMemory.is_active == False).count()
        pending_candidate_count = db.query(MemoryCandidate).filter(MemoryCandidate.user_id == current_user.id, MemoryCandidate.status == "pending").count()
        ignored_candidate_count = db.query(MemoryCandidate).filter(MemoryCandidate.user_id == current_user.id, MemoryCandidate.status == "ignored").count()
        promoted_candidate_count = db.query(MemoryCandidate).filter(MemoryCandidate.user_id == current_user.id, MemoryCandidate.status == "promoted").count()
        summaries = db.query(MemorySummary).filter(MemorySummary.user_id == current_user.id).order_by(MemorySummary.created_at.desc()).limit(8).all()
        events = db.query(AgentEvent).filter(AgentEvent.user_id == current_user.id).order_by(AgentEvent.created_at.desc()).limit(12).all()
        by_category = {}
        by_source = {}
        important_count = 0
        expiring_count = 0
        now = utc_now()
        for row in active_rows:
            by_category[row.category] = by_category.get(row.category, 0) + 1
            by_source[row.source] = by_source.get(row.source, 0) + 1
            if row.importance == "important":
                important_count += 1
            if row.expires_at and row.expires_at <= now:
                expiring_count += 1
        return {
            "profile": {k: getattr(profile, k) for k in ["display_name", "constitution", "health_goals", "diet_restrictions", "exercise_preferences", "medical_history", "custom_needs"]},
            "stats": {
                "active_count": len(active_rows),
                "inactive_count": inactive_count,
                "important_count": important_count,
                "expired_count": expiring_count,
                "candidate_pending_count": pending_candidate_count,
                "candidate_ignored_count": ignored_candidate_count,
                "candidate_promoted_count": promoted_candidate_count,
                "by_category": by_category,
                "by_source": by_source,
            },
            "recent_summaries": [{
                "id": s.id,
                "description": s.description,
                "key_facts": s.key_facts,
                "compression_reason": s.compression_reason,
                "created_at": s.created_at.timestamp() if s.created_at else None,
            } for s in summaries],
            "recent_events": [{
                "id": e.id,
                "agent_name": e.agent_name,
                "event_type": e.event_type,
                "output_summary": e.output_summary,
                "payload": e.payload,
                "created_at": e.created_at.timestamp() if e.created_at else None,
            } for e in events],
        }

@router.get("/memories")
@_with_memory_read_lock
def list_memories(
    category: Optional[str] = None,
    importance: Optional[str] = None,
    source: Optional[str] = None,
    q: Optional[str] = None,
    include_inactive: bool = Query(False),
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(PersonalizationMemory).filter(PersonalizationMemory.user_id == current_user.id)
    if not include_inactive:
        query = query.filter(PersonalizationMemory.is_active == True)
    if category and category != "all":
        query = query.filter(PersonalizationMemory.category == category)
    if importance and importance != "all":
        query = query.filter(PersonalizationMemory.importance == importance)
    if source and source != "all":
        query = query.filter(PersonalizationMemory.source == source)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(PersonalizationMemory.title.like(like), PersonalizationMemory.content.like(like)))
    rows = query.order_by(PersonalizationMemory.is_active.desc(), PersonalizationMemory.updated_at.desc()).all()
    return [serialize_memory(r) for r in rows]

@router.post("/memories")
@_with_memory_write_lock
def create_memory(body: MemoryCreate, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    expires_at = body.expires_at
    if expires_at is None and body.category == "short_term":
        expires_at = utc_now() + timedelta(days=SHORT_TERM_MEMORY_DAYS)
    item = PersonalizationMemory(user_id=current_user.id, category=body.category, importance=body.importance, title=body.title, content=body.content, source="manual", expires_at=expires_at)
    db.add(item)
    db.commit()
    _sync_personalization_conflicts(db, current_user.id)
    return {"success": True, "id": item.id, "memory": serialize_memory(item)}

@router.post("/memories/upload-md")
async def upload_markdown_memory(
    file: UploadFile = File(...),
    category: str = "long_term",
    importance: str = "important",
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not file.filename.lower().endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md files are supported")
    raw = bytearray()
    while chunk := await file.read(64 * 1024):
        raw.extend(chunk)
        if len(raw) > MAX_MARKDOWN_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Markdown file exceeds the 1 MiB upload limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="ignore")
    if len(text) > MAX_MARKDOWN_MEMORY_TOTAL_CHARS:
        raise HTTPException(status_code=400, detail="Markdown content exceeds the allowed length")
    fallback_title = file.filename.rsplit(".", 1)[0]
    chunks = split_markdown_memories(text, fallback_title)
    if len(chunks) > MAX_MARKDOWN_MEMORY_SECTIONS:
        raise HTTPException(status_code=400, detail="Markdown file contains too many memory sections")
    if any(len(chunk["content"]) > MAX_MARKDOWN_MEMORY_SECTION_CHARS for chunk in chunks):
        raise HTTPException(status_code=400, detail="Markdown memory section exceeds the allowed length")
    expires_at = utc_now() + timedelta(days=7) if category == "short_term" else None
    created = []
    for chunk in chunks:
        content = chunk["content"].strip()
        exists = db.query(PersonalizationMemory).filter(
            PersonalizationMemory.user_id == current_user.id,
            PersonalizationMemory.content == content,
            PersonalizationMemory.is_active == True,
        ).first()
        if exists:
            exists.title = chunk["title"] or exists.title
            exists.category = category
            exists.importance = importance
            exists.expires_at = expires_at
            exists.updated_at = utc_now()
            created.append(exists)
            continue
        item = PersonalizationMemory(
            user_id=current_user.id,
            category=category,
            importance=importance,
            title=chunk["title"],
            content=content,
            source="md_upload",
            expires_at=expires_at,
        )
        db.add(item)
        created.append(item)
    db.commit()
    _sync_personalization_conflicts(db, current_user.id)
    return {"success": True, "count": len(created), "memories": [serialize_memory(x) for x in created]}

@router.put("/memories/{memory_id}")
@_with_memory_write_lock
def update_memory(memory_id: int, body: MemoryUpdate, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(PersonalizationMemory).filter(PersonalizationMemory.id == memory_id, PersonalizationMemory.user_id == current_user.id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Memory not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.commit()
    _sync_personalization_conflicts(db, current_user.id)
    db.refresh(item)
    return {"success": True, "memory": serialize_memory(item)}

@router.delete("/memories/{memory_id}")
@_with_memory_write_lock
def delete_memory(memory_id: int, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(PersonalizationMemory).filter(PersonalizationMemory.id == memory_id, PersonalizationMemory.user_id == current_user.id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Memory not found")
    item.is_active = False
    db.commit()
    return {"success": True}

@router.patch("/memories/{memory_id}/restore")
@_with_memory_write_lock
def restore_memory(memory_id: int, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(PersonalizationMemory).filter(PersonalizationMemory.id == memory_id, PersonalizationMemory.user_id == current_user.id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Memory not found")
    item.is_active = True
    db.commit()
    _sync_personalization_conflicts(db, current_user.id)
    return {"success": True, "memory": serialize_memory(item)}

@router.patch("/memories/{memory_id}/promote")
@_with_memory_write_lock
def promote_memory(memory_id: int, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(PersonalizationMemory).filter(PersonalizationMemory.id == memory_id, PersonalizationMemory.user_id == current_user.id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Memory not found")
    item.category = "long_term"
    item.expires_at = None
    item.is_active = True
    item.updated_at = utc_now()
    db.commit()
    _sync_personalization_conflicts(db, current_user.id)
    db.refresh(item)
    return {"success": True, "memory": serialize_memory(item)}

@router.post("/memories/cleanup")
@_with_memory_write_lock
def cleanup_expired(current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(PersonalizationMemory).filter(
        PersonalizationMemory.user_id == current_user.id,
        PersonalizationMemory.is_active == True,
        PersonalizationMemory.expires_at.isnot(None),
        PersonalizationMemory.expires_at <= utc_now(),
    ).all()
    for row in rows:
        row.is_active = False
    db.commit()
    _sync_personalization_conflicts(db, current_user.id)
    return {"success": True, "cleaned": len(rows)}

@router.get("/candidates")
@_with_memory_read_lock
def list_candidates(
    status: str = "pending",
    importance: Optional[str] = None,
    source: Optional[str] = None,
    q: Optional[str] = None,
    session_id: Optional[str] = None,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(MemoryCandidate).filter(MemoryCandidate.user_id == current_user.id)
    if status and status != "all":
        query = query.filter(MemoryCandidate.status == status)
    if importance and importance != "all":
        query = query.filter(MemoryCandidate.importance == importance)
    if source and source != "all":
        query = query.filter(MemoryCandidate.source == source)
    if session_id:
        query = query.filter(MemoryCandidate.session_id == session_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(MemoryCandidate.title.like(like), MemoryCandidate.content.like(like), MemoryCandidate.reason.like(like)))
    rows = query.order_by(MemoryCandidate.updated_at.desc()).all()
    return [serialize_candidate(r) for r in rows]

@router.post("/candidates")
@_with_memory_write_lock
def create_candidate(body: CandidateCreate, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Candidate content is required")
    item = MemoryCandidate(
        user_id=current_user.id,
        session_id=body.session_id,
        title=body.title,
        content=content,
        importance=body.importance,
        reason=body.reason,
        source="manual",
        status="pending",
    )
    db.add(item)
    db.commit()
    _sync_personalization_conflicts(db, current_user.id)
    db.refresh(item)
    return {"success": True, "id": item.id, "candidate": serialize_candidate(item)}

@router.put("/candidates/{candidate_id}")
@_with_memory_write_lock
def update_candidate(candidate_id: int, body: CandidateUpdate, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(MemoryCandidate).filter(MemoryCandidate.id == candidate_id, MemoryCandidate.user_id == current_user.id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Candidate not found")
    data = body.model_dump(exclude_unset=True)
    if item.status == "promoted" and any(
        key in data for key in ("status", "content", "title")
    ):
        raise HTTPException(status_code=409, detail="Promoted candidate is immutable")
    if "status" in data and data["status"] not in {"pending", "promoted", "ignored"}:
        raise HTTPException(status_code=400, detail="Invalid candidate status")
    if "content" in data and data["content"] is not None and not data["content"].strip():
        raise HTTPException(status_code=400, detail="Candidate content is required")
    for key, value in data.items():
        setattr(item, key, value.strip() if isinstance(value, str) else value)
    item.updated_at = utc_now()
    db.commit()
    _sync_personalization_conflicts(db, current_user.id)
    db.refresh(item)
    return {"success": True, "candidate": serialize_candidate(item)}

@router.patch("/candidates/{candidate_id}/ignore")
@_with_memory_write_lock
def ignore_candidate(candidate_id: int, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(MemoryCandidate).filter(MemoryCandidate.id == candidate_id, MemoryCandidate.user_id == current_user.id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if item.status == "promoted":
        raise HTTPException(status_code=409, detail="Promoted candidate cannot be ignored")
    item.status = "ignored"
    item.updated_at = utc_now()
    db.commit()
    _sync_personalization_conflicts(db, current_user.id)
    db.refresh(item)
    return {"success": True, "candidate": serialize_candidate(item)}

@router.delete("/candidates/{candidate_id}")
@_with_memory_write_lock
def delete_candidate(candidate_id: int, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(MemoryCandidate).filter(MemoryCandidate.id == candidate_id, MemoryCandidate.user_id == current_user.id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Candidate not found")
    db.delete(item)
    db.commit()
    _sync_personalization_conflicts(db, current_user.id)
    return {"success": True}

@router.patch("/candidates/{candidate_id}/promote")
@_with_memory_write_lock
def promote_candidate(candidate_id: int, body: CandidatePromote, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.query(MemoryCandidate).filter(MemoryCandidate.id == candidate_id, MemoryCandidate.user_id == current_user.id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if item.status == "promoted" and item.promoted_memory_id is not None:
        memory = db.query(PersonalizationMemory).filter(
            PersonalizationMemory.id == item.promoted_memory_id,
            PersonalizationMemory.user_id == current_user.id,
        ).first()
        if memory is None:
            raise HTTPException(status_code=409, detail="Promoted memory is unavailable")
        return {
            "success": True,
            "candidate": serialize_candidate(item),
            "memory": serialize_memory(memory),
            "replayed": True,
        }
    if item.status != "pending":
        raise HTTPException(status_code=409, detail="Candidate is not promotable")
    if body.category not in {"short_term", "long_term", "preference", "note"}:
        raise HTTPException(status_code=400, detail="Invalid target memory category")
    expires_at = body.expires_at
    if body.category == "short_term" and expires_at is None:
        expires_at = utc_now() + timedelta(days=7)
    if body.category != "short_term":
        expires_at = None
    memory = PersonalizationMemory(
        user_id=current_user.id,
        category=body.category,
        importance=body.importance,
        title=item.title,
        content=item.content,
        source="candidate_promote",
        source_candidate_id=item.id,
        expires_at=expires_at,
    )
    try:
        db.add(memory)
        db.flush()
        item.status = "promoted"
        item.promoted_memory_id = memory.id
        item.updated_at = utc_now()
        db.commit()
    except IntegrityError:
        db.rollback()
        memory = db.query(PersonalizationMemory).filter(
            PersonalizationMemory.source_candidate_id == candidate_id,
            PersonalizationMemory.user_id == current_user.id,
        ).first()
        item = db.query(MemoryCandidate).filter(
            MemoryCandidate.id == candidate_id,
            MemoryCandidate.user_id == current_user.id,
        ).first()
        if (
            memory is None
            or item is None
            or item.status != "promoted"
            or item.promoted_memory_id != memory.id
        ):
            raise
        return {
            "success": True,
            "candidate": serialize_candidate(item),
            "memory": serialize_memory(memory),
            "replayed": True,
        }
    _sync_personalization_conflicts(db, current_user.id)
    db.refresh(item)
    db.refresh(memory)
    return {"success": True, "candidate": serialize_candidate(item), "memory": serialize_memory(memory)}

@router.get("/export")
def export_personalization(current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    # S 锁：get_or_create_profile 自行管理读写锁（双检锁），查询持共享读锁。
    profile = get_or_create_profile(db, current_user.id)
    with memory_rw_lock.read():
        memories = db.query(PersonalizationMemory).filter(PersonalizationMemory.user_id == current_user.id).order_by(PersonalizationMemory.updated_at.desc()).all()
        candidates = db.query(MemoryCandidate).filter(MemoryCandidate.user_id == current_user.id).order_by(MemoryCandidate.updated_at.desc()).all()
        return {
            "profile": {k: getattr(profile, k) for k in ["display_name", "constitution", "health_goals", "diet_restrictions", "exercise_preferences", "medical_history", "custom_needs"]},
            "memories": [serialize_memory(m) for m in memories],
            "candidates": [serialize_candidate(c) for c in candidates],
            "exported_at": utc_now().isoformat(),
        }

from APP.backend.time_utils import utc_now
import csv
import io
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from APP.backend.auth import get_current_user, require_admin_user
from APP.backend.config import SYSTEM_USERNAMES
from APP.backend.database import get_db, UserModel, FeedbackRecord, DbMessage, DbSession, PersonalizationMemory
from APP.backend.health_utils import safe_json_dumps

router = APIRouter(prefix="/feedback", tags=["Feedback"])

class FeedbackCreate(BaseModel):
    session_id: Optional[str] = None
    message_id: Optional[int] = None
    feedback_type: str
    reason: str = ""
    user_feedback: str = ""
    question: str = ""
    answer: str = ""
    metadata: dict = {}

class FeedbackUpdate(BaseModel):
    feedback_type: Optional[str] = None
    rating: Optional[str] = None
    reason: Optional[str] = None
    user_feedback: Optional[str] = None
    question: Optional[str] = None
    answer: Optional[str] = None
    metadata: Optional[dict] = None

def is_admin(user: UserModel) -> bool:
    return user.role == "admin" or user.username in SYSTEM_USERNAMES

def _delete_feedback_memory(db: Session, user_id: int, content: str = "") -> None:
    query = db.query(PersonalizationMemory).filter(
        PersonalizationMemory.user_id == user_id,
        PersonalizationMemory.category == "feedback",
        PersonalizationMemory.source == "user_feedback",
        PersonalizationMemory.title == "用户点踩反馈",
    )
    if content:
        query = query.filter(PersonalizationMemory.content == content)
    memory = query.order_by(PersonalizationMemory.updated_at.desc()).first()
    if memory:
        db.delete(memory)

def _turn_question_for_assistant(db: Session, assistant_msg: DbMessage | None) -> str:
    if not assistant_msg:
        return ""
    if assistant_msg.parent_id:
        parent = db.query(DbMessage).filter(
            DbMessage.id == assistant_msg.parent_id,
            DbMessage.session_id == assistant_msg.session_id,
            DbMessage.role == "user",
        ).first()
        if parent and parent.content:
            return parent.content
    parent = db.query(DbMessage).filter(
        DbMessage.session_id == assistant_msg.session_id,
        DbMessage.role == "user",
        DbMessage.id < assistant_msg.id,
    ).order_by(DbMessage.id.desc()).first()
    return parent.content if parent and parent.content else ""

@router.post("")
def create_feedback(body: FeedbackCreate, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    answer = body.answer
    question = body.question
    msg = None
    if body.message_id:
        msg = db.query(DbMessage).join(DbSession, DbMessage.session_id == DbSession.id).filter(
            DbMessage.id == body.message_id,
            DbSession.user_id == current_user.id,
        ).first()
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found")
        if msg.role != "assistant":
            raise HTTPException(status_code=400, detail="Feedback can only be attached to assistant messages")
        if body.session_id and msg.session_id != body.session_id:
            raise HTTPException(status_code=400, detail="Message does not belong to this session")
        existing = db.query(FeedbackRecord).filter(
            FeedbackRecord.user_id == current_user.id,
            FeedbackRecord.message_id == body.message_id,
        ).order_by(FeedbackRecord.created_at.desc()).first()
        if not answer:
            answer = msg.content
        if not question:
            question = _turn_question_for_assistant(db, msg)
    mapped = "excellent" if body.feedback_type in ("like", "excellent", "user_like") else "problem"
    requested_status = "like" if mapped == "excellent" else "dislike"
    if msg and existing:
        current_status = "like" if (existing.rating or existing.feedback_type) in ("like", "excellent", "user_like") else "dislike"
        if current_status == requested_status:
            if current_status == "dislike":
                _delete_feedback_memory(db, current_user.id, existing.reason or existing.user_feedback)
            db.delete(existing)
            db.commit()
            return {"success": True, "id": existing.id, "feedback_status": None, "cancelled": True}
        existing.feedback_type = mapped
        existing.rating = body.feedback_type
        existing.reason = body.reason
        existing.user_feedback = body.user_feedback
        existing.question = question
        existing.answer = answer
        existing.metadata_json = safe_json_dumps(body.metadata)
        db.commit()
        return {"success": True, "id": existing.id, "feedback_status": requested_status, "updated": True}
    rec = FeedbackRecord(
        user_id=current_user.id, session_id=(body.session_id or (msg.session_id if msg else None)), message_id=body.message_id,
        feedback_type=mapped, rating=body.feedback_type, reason=body.reason,
        user_feedback=body.user_feedback, question=question, answer=answer,
        metadata_json=safe_json_dumps(body.metadata),
    )
    db.add(rec)
    db.commit()
    return {"success": True, "id": rec.id, "feedback_status": requested_status}

@router.get("/admin")
def list_feedback(current_user: UserModel = Depends(require_admin_user), db: Session = Depends(get_db)):
    rows = db.query(FeedbackRecord).order_by(FeedbackRecord.created_at.desc()).limit(200).all()
    return [_serialize_feedback(r) for r in rows]

def _serialize_feedback(r: FeedbackRecord):
    return {
        "id": r.id,
        "user_id": r.user_id,
        "session_id": r.session_id,
        "message_id": r.message_id,
        "feedback_type": r.feedback_type,
        "rating": r.rating,
        "reason": r.reason,
        "user_feedback": r.user_feedback,
        "question": r.question,
        "answer": r.answer,
        "metadata": _safe_load_json(r.metadata_json),
        "metadata_json": r.metadata_json,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }

def _safe_load_json(value: str):
    try:
        return json.loads(value or "{}")
    except Exception:
        return {}

@router.patch("/admin/items/{feedback_id}")
def update_feedback(feedback_id: int, body: FeedbackUpdate, current_user: UserModel = Depends(require_admin_user), db: Session = Depends(get_db)):
    rec = db.query(FeedbackRecord).filter(FeedbackRecord.id == feedback_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Feedback not found")
    data = body.model_dump(exclude_unset=True) if hasattr(body, "model_dump") else body.dict(exclude_unset=True)
    for field in ["feedback_type", "rating", "reason", "user_feedback", "question", "answer"]:
        if field in data:
            setattr(rec, field, data[field] or "")
    if "metadata" in data:
        rec.metadata_json = safe_json_dumps(data["metadata"] or {})
    db.commit()
    db.refresh(rec)
    return _serialize_feedback(rec)

@router.delete("/admin/items/{feedback_id}")
def delete_feedback(feedback_id: int, current_user: UserModel = Depends(require_admin_user), db: Session = Depends(get_db)):
    rec = db.query(FeedbackRecord).filter(FeedbackRecord.id == feedback_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Feedback not found")
    db.delete(rec)
    db.commit()
    return {"success": True}

@router.get("/admin/export")
def export_feedback(current_user: UserModel = Depends(require_admin_user), db: Session = Depends(get_db)):
    rows = db.query(FeedbackRecord).order_by(FeedbackRecord.created_at.desc()).all()
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "id", "user_id", "session_id", "message_id", "feedback_type", "rating",
        "reason", "user_feedback", "question", "answer", "metadata_json", "created_at",
    ])
    writer.writeheader()
    for r in rows:
        writer.writerow({
            "id": r.id,
            "user_id": r.user_id,
            "session_id": r.session_id or "",
            "message_id": r.message_id or "",
            "feedback_type": r.feedback_type or "",
            "rating": r.rating or "",
            "reason": r.reason or "",
            "user_feedback": r.user_feedback or "",
            "question": r.question or "",
            "answer": r.answer or "",
            "metadata_json": r.metadata_json or "{}",
            "created_at": r.created_at.isoformat() if r.created_at else "",
        })
    filename = f"feedback_export_{utc_now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
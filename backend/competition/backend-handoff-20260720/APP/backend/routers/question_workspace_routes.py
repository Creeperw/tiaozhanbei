from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from APP.backend.auth import get_current_user
from APP.backend.contracts.common import page_meta
from APP.backend.contracts.question import (
    QuestionImportCollection,
    QuestionImportCreated,
    QuestionImportDetail,
    QuestionIndexResponse,
    QuestionRevisionRequest,
    QuestionStateResponse,
    QuestionWorkspaceCollection,
)
from APP.backend.database import UserModel, get_db
from APP.backend.question_workspace_service import (
    QuestionWorkspaceError,
    confirm_item,
    create_import,
    deactivate_item,
    get_import,
    list_active_questions,
    list_imports,
    list_job_items,
    reject_item,
    revise_item,
    sync_personal_question_index,
)

router = APIRouter(prefix="/question-workspace", tags=["Question Workspace"])
QUESTION_WORKSPACE_UPLOAD_ROOT = Path(__file__).resolve().parents[1] / "user_questions" / "uploads"
QUESTION_WORKSPACE_INDEX_ROOT = Path(__file__).resolve().parents[1] / "user_questions" / "indexes"


def question_index_sync(db: Session, *, owner_user_id: int) -> dict:
    return sync_personal_question_index(
        db,
        owner_user_id=owner_user_id,
        index_root=QUESTION_WORKSPACE_INDEX_ROOT,
    )


def _raise_workspace_error(exc: QuestionWorkspaceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post(
    "/imports",
    status_code=status.HTTP_201_CREATED,
    response_model=QuestionImportCreated,
)
async def upload_questions(
    file: UploadFile = File(...),
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return await create_import(
            db,
            owner_user_id=current_user.id,
            upload=file,
            upload_root=QUESTION_WORKSPACE_UPLOAD_ROOT,
        )
    except QuestionWorkspaceError as exc:
        _raise_workspace_error(exc)


@router.get("/imports", response_model=QuestionImportCollection)
def read_import_history(
    status_filter: str | None = Query(default=None, alias="status"),
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    allowed_statuses = {
        "processing",
        "preview_ready",
        "needs_human_review",
        "failed",
    }
    if status_filter and status_filter not in allowed_statuses:
        raise HTTPException(status_code=422, detail="invalid import status")
    items = list_imports(
        db,
        owner_user_id=current_user.id,
        status=status_filter,
    )
    return {"items": items, "page": page_meta(item_count=len(items))}


@router.get("/imports/{job_id}", response_model=QuestionImportDetail)
def read_import(
    job_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = get_import(db, owner_user_id=current_user.id, job_id=job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="question import was not found")
    return {
        "job_id": job.job_id,
        "status": job.status,
        "item_count": job.item_count,
        "original_filename": job.original_filename,
        "created_at": job.created_at,
    }


@router.get("/imports/{job_id}/items", response_model=QuestionWorkspaceCollection)
def read_import_items(
    job_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if get_import(db, owner_user_id=current_user.id, job_id=job_id) is None:
        raise HTTPException(status_code=404, detail="question import was not found")
    items = list_job_items(db, owner_user_id=current_user.id, job_id=job_id)
    return {"items": items, "page": page_meta(item_count=len(items))}


@router.patch("/items/{question_id}", response_model=QuestionStateResponse)
def revise_question(
    question_id: str,
    req: QuestionRevisionRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        changes = req.model_dump(exclude_unset=True)
        if "explanation" in changes:
            changes["analysis"] = changes.pop("explanation")
        item = revise_item(
            db,
            owner_user_id=current_user.id,
            question_id=question_id,
            changes=changes,
        )
    except QuestionWorkspaceError as exc:
        _raise_workspace_error(exc)
    if item is None:
        raise HTTPException(status_code=404, detail="question was not found")
    return {"question_id": item.question_id, "status": item.status}


@router.post("/items/{question_id}/reject", response_model=QuestionStateResponse)
def reject_question(
    question_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        item = reject_item(db, owner_user_id=current_user.id, question_id=question_id)
    except QuestionWorkspaceError as exc:
        _raise_workspace_error(exc)
    if item is None:
        raise HTTPException(status_code=404, detail="question was not found")
    return {"question_id": item.question_id, "status": item.status}


@router.post("/items/{question_id}/confirm", response_model=QuestionStateResponse)
def confirm_question(
    question_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        item = confirm_item(db, owner_user_id=current_user.id, question_id=question_id)
    except QuestionWorkspaceError as exc:
        _raise_workspace_error(exc)
    if item is None:
        raise HTTPException(status_code=404, detail="question was not found")
    try:
        vector_index = question_index_sync(db, owner_user_id=current_user.id)
    except Exception as exc:
        vector_index = {
            "ok": False,
            "owner_user_id": current_user.id,
            "error_type": type(exc).__name__,
            "rebuild_required": True,
        }
    return {
        "question_id": item.question_id,
        "status": item.status,
        "vector_index": vector_index,
    }


@router.post("/questions/{question_id}/deactivate", response_model=QuestionStateResponse)
def deactivate_question(
    question_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        item = deactivate_item(db, owner_user_id=current_user.id, question_id=question_id)
    except QuestionWorkspaceError as exc:
        _raise_workspace_error(exc)
    if item is None:
        raise HTTPException(status_code=404, detail="question was not found")
    try:
        vector_index = question_index_sync(db, owner_user_id=current_user.id)
    except Exception as exc:
        vector_index = {
            "ok": False,
            "owner_user_id": current_user.id,
            "error_type": type(exc).__name__,
            "rebuild_required": True,
        }
    return {
        "question_id": item.question_id,
        "status": item.status,
        "vector_index": vector_index,
    }


@router.post("/index/rebuild", response_model=QuestionIndexResponse)
def rebuild_personal_index(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        vector_index = question_index_sync(db, owner_user_id=current_user.id)
    except Exception as exc:
        vector_index = {
            "ok": False,
            "owner_user_id": current_user.id,
            "error_type": type(exc).__name__,
            "rebuild_required": True,
        }
    return {"vector_index": vector_index}


@router.get("/questions", response_model=QuestionWorkspaceCollection)
def read_active_questions(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items = list_active_questions(db, owner_user_id=current_user.id)
    return {"items": items, "page": page_meta(item_count=len(items))}

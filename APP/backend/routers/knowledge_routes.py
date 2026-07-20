import json
import os
import shutil
import time
import uuid
from typing import List
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from APP.backend.auth import get_current_user, require_admin_user
from APP.backend.database import UserModel, get_db
from APP.backend.config import UPLOAD_DIR
from APP.backend.store import FILES, save_file_metadata
from APP.backend.rag_core import rag_service, Config, RAGUnavailableError
from APP.backend.knowledge_atlas_service import atlas_service
from APP.backend.memory_agent_service import build_learner_context_brief
from APP.backend.knowledge_agent_service import align_knowledge_points, build_evidence_pack, list_questions
from APP.backend.document_ingestion_service import ingest_document
from APP.backend.question_ingestion_service import QuestionIngestionService
from APP.backend.pdf_question_ingestion_service import PdfQuestionIngestionService
from APP.backend.question_ingestion_task_service import QuestionIngestionTaskService
from APP.backend.database import QuestionIngestionTaskRecord

router = APIRouter()
question_ingestion_service_factory = QuestionIngestionService
question_pdf_ingestion_service_factory = PdfQuestionIngestionService
question_ingestion_task_service_factory = QuestionIngestionTaskService

class SearchRequest(BaseModel):
    query: str
    top_k: int = 5

class AlignKnowledgeRequest(BaseModel):
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("text must not be blank")
        return stripped

class EvidencePackRequest(BaseModel):
    query: str = Field(min_length=1)
    task_type: str | None = None
    document_result: dict | None = None

    @field_validator("query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped

class DocumentIngestRequest(BaseModel):
    file_path: str = Field(min_length=1)
    original_filename: str = Field(min_length=1)
    scope: str = Field(default="personal", pattern="^(public|personal)$")
    document_kind: str = Field(min_length=1)

    @field_validator("file_path", "original_filename", "document_kind")
    @classmethod
    def reject_blank_fields(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("field must not be blank")
        return stripped


class QuestionIngestRequest(BaseModel):
    stem: str = Field(min_length=1, max_length=10000)
    answer: str = Field(default="", max_length=10000)
    analysis: str = Field(default="", max_length=20000)
    options: list[str] = Field(default_factory=list, max_length=10)
    question_type: str = Field(default="short_answer", max_length=50)
    difficulty: float = Field(default=2.0, ge=0, le=5)
    requested_kp_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("stem")
    @classmethod
    def reject_blank_stem(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("stem must not be blank")
        return stripped


class PdfQuestionIngestRequest(BaseModel):
    file_id: str = Field(min_length=1, max_length=120)

    @field_validator("file_id")
    @classmethod
    def reject_blank_file_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("file_id must not be blank")
        return stripped


@router.get("/status")
def get_status(scope: str = Query("all", pattern="^(all|public|personal)$"), current_user: UserModel = Depends(get_current_user)):
    return rag_service.get_stats(scope=scope, user_id=current_user.id)

@router.get("/files")
def get_files(scope: str = Query("all", pattern="^(all|public|personal)$"), current_user: UserModel = Depends(get_current_user)):
    files = rag_service.list_files(scope=scope, user_id=current_user.id)
    is_admin = current_user.role == "admin"
    for item in files:
        if item.get("scope") == "public":
            item["can_delete"] = is_admin
    return {"files": files}


@router.get("/catalog")
def get_catalog(
    scope: str = Query("all", pattern="^(all|public|personal)$"),
    current_user: UserModel = Depends(get_current_user),
):
    """Describe documents, structured datasets and vector indexes separately."""

    catalog = rag_service.get_catalog(scope=scope, user_id=current_user.id)
    if scope in {"all", "public"}:
        catalog["datasets"] = [
            *catalog.get("datasets", []),
            *atlas_service.catalog_datasets(),
        ]
    return catalog

@router.post("/upload")
async def upload_files(
    files: List[UploadFile] = File(...),
    scope: str = Query("personal", pattern="^(public|personal)$"),
    current_user: UserModel = Depends(get_current_user),
):
    if scope == "public" and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admin can upload public knowledge")
    data_dir, _ = rag_service._paths_for_scope(scope, current_user.id if scope == "personal" else None)
    os.makedirs(data_dir, exist_ok=True)
    uploaded_names = []
    
    for file in files:
        safe_name = rag_service._safe_filename(file.filename)
        file_path = os.path.join(data_dir, safe_name)
        # 覆盖上传：如果存在旧文件，先彻底清除它的旧向量
        if os.path.exists(file_path):
            rag_service.delete_file(safe_name, scope=scope, user_id=current_user.id if scope == "personal" else None)
            
        try:
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            uploaded_names.append(safe_name)
        except Exception:
            pass
            
    if not rag_service.is_processing:
        rag_service.rebuild_index(scope=scope, user_id=current_user.id if scope == "personal" else None)
        
    return {"message": f"成功上传 {len(uploaded_names)} 个文件", "files": uploaded_names, "scope": scope}

@router.delete("/files/{filename}")
def delete_file(
    filename: str,
    scope: str = Query("personal", pattern="^(public|personal)$"),
    current_user: UserModel = Depends(get_current_user),
):
    if scope == "public" and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admin can delete public knowledge")
    try:
        rag_service.delete_file(filename, scope=scope, user_id=current_user.id if scope == "personal" else None)
        return {"message": f"成功删除 {filename} 及向量数据", "scope": scope}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/rebuild")
def trigger_rebuild(
    scope: str = Query("personal", pattern="^(public|personal)$"),
    current_user: UserModel = Depends(get_current_user),
):
    if scope == "public" and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admin can rebuild public knowledge")
    if rag_service.is_processing:
        return {"message": "正在处理中，请稍后"}
    rag_service.rebuild_index(scope=scope, user_id=current_user.id if scope == "personal" else None)
    return {"message": "开始扫描和构建", "scope": scope}

@router.post("/search_test")
def search_test(req: SearchRequest, current_user: UserModel = Depends(get_current_user)):
    try:
        return rag_service.search(req.query, req.top_k, user_id=current_user.id)
    except RAGUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"state": exc.state, "message": exc.message},
        ) from exc

@router.post("/points/align")
def align_points(req: AlignKnowledgeRequest, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    return align_knowledge_points(db, req.text, user_id=current_user.id)

@router.post("/ingest")
def ingest_knowledge_document(req: DocumentIngestRequest, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    if req.scope == "public" and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admin can ingest public knowledge")
    result = ingest_document(
        db,
        file_path=req.file_path,
        original_filename=req.original_filename,
        scope=req.scope,
        user_id=current_user.id,
        document_kind=req.document_kind,
    )
    return result.model_dump()

@router.post("/evidence-pack")
def create_evidence_pack(req: EvidencePackRequest, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    learner_context = build_learner_context_brief(db, current_user.id)
    try:
        pack = build_evidence_pack(
            db,
            query=req.query,
            learner_context=learner_context,
            task_type=req.task_type,
            document_result=req.document_result,
        )
    except RAGUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"state": exc.state, "message": exc.message},
        ) from exc
    return pack.model_dump()

@router.post("/questions/ingest")
def ingest_question(
    req: QuestionIngestRequest,
    current_user: UserModel = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    return _submit_question_ingestion_task(req, current_user, db)


def _submit_question_ingestion_task(
    req: QuestionIngestRequest,
    current_user: UserModel,
    db: Session,
):
    payload = {
        **req.model_dump(),
        "source_type": "admin_upload",
        "owner_id": str(current_user.id),
        "source_ref": f"admin_upload:{current_user.id}",
    }
    service = question_ingestion_task_service_factory()
    task = service.submit(
        db,
        submitted_by_user_id=current_user.id,
        payload=payload,
    )
    return JSONResponse({
        "task_id": task.task_id,
        "status": task.status,
        "outcome_status": None,
        "published_question_id": None,
        "error_code": None,
    }, status_code=status.HTTP_202_ACCEPTED)


@router.post("/admin/question-ingestion-tasks")
def submit_question_ingestion_task(
    req: QuestionIngestRequest,
    current_user: UserModel = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    return _submit_question_ingestion_task(req, current_user, db)


_MAX_PDF_UPLOAD_BYTES = 20 * 1024 * 1024


@router.post("/admin/question-ingestion-pdf-upload")
async def upload_pdf_question_source(
    file: UploadFile = File(...),
    current_user: UserModel = Depends(require_admin_user),
):
    filename = str(file.filename or "")
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported")
    content = await file.read(_MAX_PDF_UPLOAD_BYTES + 1)
    if len(content) > _MAX_PDF_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="PDF file exceeds 20 MiB limit")
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="Invalid PDF file")
    file_id = str(uuid.uuid4())
    safe_filename = f"{int(time.time())}_{file_id[:8]}.pdf"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)
    with open(file_path, "wb") as handle:
        handle.write(content)
    FILES[file_id] = {
        "original_name": filename,
        "saved_path": file_path,
        "file_size": len(content),
        "upload_time": int(time.time()),
        "uploader_id": current_user.id,
    }
    save_file_metadata()
    return {"file_id": file_id, "filename": filename}


@router.post("/admin/question-ingestion-pdf-tasks")
def submit_pdf_question_ingestion_task(
    req: PdfQuestionIngestRequest,
    current_user: UserModel = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    try:
        payload = question_pdf_ingestion_service_factory().build_payload(
            file_id=req.file_id,
            submitted_by_user_id=current_user.id,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = status.HTTP_404_NOT_FOUND if detail == "Uploaded PDF was not found" else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=detail) from exc
    task = question_ingestion_task_service_factory().submit(
        db,
        submitted_by_user_id=current_user.id,
        payload={**payload, "task_kind": "pdf"},
    )
    return JSONResponse({
        "task_id": task.task_id,
        "status": task.status,
        "outcome_status": None,
        "published_question_id": None,
        "error_code": None,
    }, status_code=status.HTTP_202_ACCEPTED)


@router.post("/admin/question-ingestion-tasks/{task_id}/retry")
def retry_question_ingestion_task(
    task_id: str,
    current_user: UserModel = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    try:
        task = question_ingestion_task_service_factory().retry(db, task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse({
        "task_id": task.task_id,
        "status": task.status,
        "retry_count": task.retry_count,
        "outcome_status": None,
        "published_question_id": None,
        "error_code": None,
    }, status_code=status.HTTP_202_ACCEPTED)


def _question_ingestion_task_payload(task: QuestionIngestionTaskRecord) -> dict:
    task_result = json.loads(task.result_json or "{}")
    return {
        "task_id": task.task_id,
        "status": task.status,
        "outcome_status": task_result.get("status"),
        "published_question_id": task.published_question_id,
        "error_code": task.error_code,
        "retry_count": task.retry_count,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


@router.get("/admin/question-ingestion-tasks")
def list_question_ingestion_tasks(
    limit: int = Query(20, ge=1, le=100),
    current_user: UserModel = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    tasks = db.query(QuestionIngestionTaskRecord).order_by(
        QuestionIngestionTaskRecord.created_at.desc(),
        QuestionIngestionTaskRecord.id.desc(),
    ).limit(limit).all()
    return {"tasks": [_question_ingestion_task_payload(task) for task in tasks]}


@router.get("/admin/question-ingestion-tasks/{task_id}")
def get_question_ingestion_task(
    task_id: str,
    current_user: UserModel = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    task = db.query(QuestionIngestionTaskRecord).filter_by(task_id=task_id).one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Question ingestion task not found")
    return _question_ingestion_task_payload(task)


@router.get("/questions")
def get_questions(
    kp_id: list[str] | None = Query(default=None),
    limit: int = Query(20, ge=1, le=100),
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return {"questions": list_questions(db, kp_ids=kp_id, limit=limit)}

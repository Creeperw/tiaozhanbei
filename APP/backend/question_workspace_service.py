from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import unicodedata
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from APP.backend.database import UserQuestionImportJob, UserQuestionItem
from APP.backend.time_utils import utc_now

ALLOWED_EXTENSIONS = {".pdf", ".md", ".txt"}
ALLOWED_CONTENT_TYPES = {
    ".pdf": {"application/pdf"},
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
    ".txt": {"text/plain", "application/octet-stream"},
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_INDEX_LOCKS: dict[int, threading.Lock] = {}
_INDEX_LOCKS_GUARD = threading.Lock()


def _owner_index_lock(owner_user_id: int) -> threading.Lock:
    with _INDEX_LOCKS_GUARD:
        return _INDEX_LOCKS.setdefault(owner_user_id, threading.Lock())


def _remove_failed_upload(stored_path: Path) -> None:
    try:
        stored_path.unlink(missing_ok=True)
    except OSError:
        pass


class QuestionWorkspaceError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", normalized).strip()


def _content_hash(stem: str, answer: str, question_type: str) -> str:
    raw = "\x1f".join(_normalize_text(value) for value in (question_type, stem, answer))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _extract_structured_questions(markdown: str) -> list[dict[str, Any]]:
    blocks = re.split(r"(?m)^##\s*题目[^\n]*\n", markdown)
    rows: list[dict[str, Any]] = []
    for block in blocks[1:]:
        fields: dict[str, str] = {}
        matches = list(re.finditer(
            r"(?m)^[-*]?\s*(题型|题干|选项|答案|解析|知识点)\s*[:：]\s*",
            block,
        ))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(block)
            fields[match.group(1)] = block[match.end():end].strip()
        stem = _normalize_text(fields.get("题干", ""))
        if not stem:
            continue
        options = [
            _normalize_text(value)
            for value in re.split(r"(?:\n|[；;])", fields.get("选项", ""))
            if _normalize_text(value)
        ]
        rows.append({
            "question_type": _normalize_text(fields.get("题型", "")) or "未分类",
            "stem": stem,
            "options": options,
            "answer": _normalize_text(fields.get("答案", "")),
            "analysis": _normalize_text(fields.get("解析", "")),
            "kp_ids": [
                value for value in re.split(r"[,，\s]+", fields.get("知识点", ""))
                if value
            ],
        })
    if not rows:
        raise QuestionWorkspaceError(
            "未识别到题目；请使用“## 题目”及“题干/答案”字段",
            status_code=422,
        )
    return rows


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise QuestionWorkspaceError("文本编码不受支持", status_code=422)


def _extract_pdf_text(content: bytes) -> str:
    try:
        import pdfplumber
        from io import BytesIO

        with pdfplumber.open(BytesIO(content)) as document:
            return "\n\n".join(page.extract_text() or "" for page in document.pages)
    except ImportError as exc:
        raise QuestionWorkspaceError("PDF 解析组件不可用", status_code=503) from exc
    except Exception as exc:
        raise QuestionWorkspaceError("PDF 解析失败", status_code=422) from exc


def _safe_filename(filename: str) -> str:
    raw = str(filename or "")
    if not raw or Path(raw).name != raw or "/" in raw or "\\" in raw or ".." in raw:
        raise QuestionWorkspaceError("文件名不安全")
    return raw


def _public_item(item: UserQuestionItem) -> dict[str, Any]:
    return {
        "question_id": item.question_id,
        "question_type": item.question_type,
        "stem": item.stem,
        "answer": item.answer,
        "analysis": item.analysis,
        "options": json.loads(item.options_json or "[]"),
        "kp_ids": json.loads(item.kp_ids_json or "[]"),
        "status": item.status,
        "review_reason": item.review_reason,
    }


async def create_import(
    db: Session,
    *,
    owner_user_id: int,
    upload: UploadFile,
    upload_root: Path,
) -> dict[str, Any]:
    filename = _safe_filename(upload.filename or "")
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise QuestionWorkspaceError("仅支持 PDF、Markdown 和 TXT 文件", status_code=415)
    content_type = str(upload.content_type or "").lower()
    if content_type not in ALLOWED_CONTENT_TYPES[extension]:
        raise QuestionWorkspaceError("文件 MIME 类型与扩展名不匹配", status_code=415)
    content = await upload.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise QuestionWorkspaceError("文件不能为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise QuestionWorkspaceError("文件超过 10 MiB 限制", status_code=413)

    job_id = f"UQJ_{uuid.uuid4().hex[:16]}"
    owner_root = upload_root / str(owner_user_id)
    owner_root.mkdir(parents=True, exist_ok=True)
    stored_path = owner_root / f"{job_id}{extension}"
    stored_path.write_bytes(content)
    job = UserQuestionImportJob(
        job_id=job_id,
        owner_user_id=owner_user_id,
        original_filename=filename,
        stored_path=str(stored_path),
        content_type=content_type,
        file_size=len(content),
        status="processing",
    )
    db.add(job)
    db.commit()
    try:
        text = _extract_pdf_text(content) if extension == ".pdf" else _decode_text(content)
        rows = _extract_structured_questions(text)
    except QuestionWorkspaceError as exc:
        job.status = "failed"
        job.error_message = str(exc)
        db.commit()
        _remove_failed_upload(stored_path)
        raise
    items = []
    for row in rows:
        has_answer = bool(row["answer"])
        requires_options = any(
            marker in row["question_type"].lower()
            for marker in ("选择", "choice")
        )
        has_options = bool(row["options"])
        ready = has_answer and (not requires_options or has_options)
        if not has_answer:
            review_reason = "缺少答案，需要人工修订"
        elif requires_options and not has_options:
            review_reason = "选择题缺少选项，需要人工修订"
        else:
            review_reason = ""
        items.append(UserQuestionItem(
            question_id=f"UQ_{uuid.uuid4().hex[:16]}",
            job_id=job_id,
            owner_user_id=owner_user_id,
            question_type=row["question_type"],
            stem=row["stem"],
            answer=row["answer"],
            analysis=row["analysis"],
            options_json=json.dumps(row["options"], ensure_ascii=False),
            kp_ids_json=json.dumps(row["kp_ids"], ensure_ascii=False),
            content_hash=_content_hash(row["stem"], row["answer"], row["question_type"]),
            status="preview_ready" if ready else "needs_human_review",
            review_reason=review_reason,
        ))
    overall_status = (
        "preview_ready"
        if all(item.status == "preview_ready" for item in items)
        else "needs_human_review"
    )
    job.status = overall_status
    job.item_count = len(items)
    try:
        db.add_all(items)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        job = db.query(UserQuestionImportJob).filter_by(job_id=job_id).one()
        job.status = "failed"
        job.error_message = "文件中包含已导入的重复题目"
        db.commit()
        _remove_failed_upload(stored_path)
        raise QuestionWorkspaceError("文件中包含已导入的重复题目", status_code=409) from exc
    return {
        "job_id": job.job_id,
        "status": job.status,
        "item_count": job.item_count,
        "items": [_public_item(item) for item in items],
    }


def get_import(db: Session, *, owner_user_id: int, job_id: str) -> UserQuestionImportJob | None:
    return db.query(UserQuestionImportJob).filter_by(
        job_id=job_id,
        owner_user_id=owner_user_id,
    ).one_or_none()


def list_imports(
    db: Session,
    *,
    owner_user_id: int,
    status: str | None = None,
) -> list[dict[str, Any]]:
    query = db.query(UserQuestionImportJob).filter_by(owner_user_id=owner_user_id)
    if status:
        query = query.filter_by(status=status)
    jobs = query.order_by(
        UserQuestionImportJob.created_at.desc(),
        UserQuestionImportJob.id.desc(),
    ).all()
    return [
        {
            "job_id": job.job_id,
            "status": job.status,
            "item_count": job.item_count,
            "original_filename": job.original_filename,
            "error_message": job.error_message,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }
        for job in jobs
    ]


def list_job_items(db: Session, *, owner_user_id: int, job_id: str) -> list[dict[str, Any]]:
    return [
        _public_item(item)
        for item in db.query(UserQuestionItem).filter_by(
            job_id=job_id,
            owner_user_id=owner_user_id,
        ).order_by(UserQuestionItem.id).all()
    ]


def _sync_personal_question_index(
    db: Session,
    *,
    owner_user_id: int,
    index_root: Path,
) -> dict[str, Any]:
    from APP.backend.rag_core import rag_service

    model = rag_service.model
    if model is None:
        return {
            "ok": False,
            "owner_user_id": owner_user_id,
            "status": "disabled",
            "rebuild_required": True,
        }
    questions = db.query(UserQuestionItem).filter_by(
        owner_user_id=owner_user_id,
        status="active",
    ).order_by(UserQuestionItem.question_id).all()
    target_dir = index_root / str(owner_user_id) / "题库"
    if not questions:
        if target_dir.exists():
            shutil.rmtree(target_dir)
        return {
            "ok": True,
            "owner_user_id": owner_user_id,
            "status": "empty",
            "count": 0,
            "index_dir": str(target_dir),
        }
    texts = ["\n".join(filter(None, (item.stem, item.answer, item.analysis))) for item in questions]
    try:
        import faiss
        import numpy as np

        vectors = np.asarray(model.encode(texts, convert_to_numpy=True), dtype="float32")
        if vectors.ndim != 2 or vectors.shape[0] != len(questions) or not vectors.shape[1]:
            raise ValueError("Embedding 返回形状异常")
        faiss.normalize_L2(vectors)
        index = faiss.IndexFlatIP(int(vectors.shape[1]))
        index.add(vectors)
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=target_dir.parent, prefix=".题库-") as temp_name:
            temp_dir = Path(temp_name)
            faiss.write_index(index, str(temp_dir / "index.faiss"))
            with (temp_dir / "metadata.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
                for position, item in enumerate(questions):
                    handle.write(json.dumps({
                        "type": "qa",
                        "record_id": position,
                        "entity_id": item.question_id,
                        "scope": "user",
                        "owner_id": str(owner_user_id),
                        "content": texts[position],
                        "original": _public_item(item),
                    }, ensure_ascii=False, separators=(",", ":")) + "\n")
            (temp_dir / "manifest.json").write_text(json.dumps({
                "schema_version": "1.0.0",
                "scope": "user",
                "owner_id": str(owner_user_id),
                "collection": "题库",
                "dimension": int(index.d),
                "count": int(index.ntotal),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            backup = target_dir.with_name(".题库-old")
            if backup.exists():
                shutil.rmtree(backup)
            if target_dir.exists():
                os.replace(target_dir, backup)
            os.replace(temp_dir, target_dir)
            if backup.exists():
                shutil.rmtree(backup)
        return {
            "ok": True,
            "owner_user_id": owner_user_id,
            "status": "rebuilt",
            "count": len(questions),
            "dimension": int(index.d),
            "index_dir": str(target_dir),
        }
    except Exception as exc:
        return {
            "ok": False,
            "owner_user_id": owner_user_id,
            "error_type": type(exc).__name__,
            "rebuild_required": True,
        }


def sync_personal_question_index(
    db: Session,
    *,
    owner_user_id: int,
    index_root: Path,
) -> dict[str, Any]:
    with _owner_index_lock(owner_user_id):
        db.expire_all()
        return _sync_personal_question_index(
            db,
            owner_user_id=owner_user_id,
            index_root=index_root,
        )


def revise_item(
    db: Session,
    *,
    owner_user_id: int,
    question_id: str,
    changes: dict[str, Any],
) -> UserQuestionItem | None:
    item = db.query(UserQuestionItem).filter_by(
        question_id=question_id,
        owner_user_id=owner_user_id,
    ).one_or_none()
    if item is None:
        return None
    if item.status == "active":
        raise QuestionWorkspaceError("请先停用题目再修订", status_code=409)
    for field in ("question_type", "stem", "answer", "analysis"):
        if field in changes:
            setattr(item, field, _normalize_text(str(changes[field] or "")))
    if "options" in changes:
        options = changes["options"] if isinstance(changes["options"], list) else []
        item.options_json = json.dumps(
            list(dict.fromkeys(_normalize_text(str(value)) for value in options if _normalize_text(str(value)))),
            ensure_ascii=False,
        )
    if "kp_ids" in changes:
        kp_ids = changes["kp_ids"] if isinstance(changes["kp_ids"], list) else []
        item.kp_ids_json = json.dumps(
            list(dict.fromkeys(str(value).strip() for value in kp_ids if str(value).strip())),
            ensure_ascii=False,
        )
    if not item.stem:
        raise QuestionWorkspaceError("题干不能为空", status_code=422)
    requires_options = any(
        marker in item.question_type.lower()
        for marker in ("选择", "choice")
    )
    has_options = bool(json.loads(item.options_json or "[]"))
    ready = bool(item.answer) and (not requires_options or has_options)
    item.status = "preview_ready" if ready else "needs_human_review"
    if not item.answer:
        item.review_reason = "缺少答案，需要人工修订"
    elif requires_options and not has_options:
        item.review_reason = "选择题缺少选项，需要人工修订"
    else:
        item.review_reason = ""
    item.content_hash = _content_hash(item.stem, item.answer, item.question_type)
    try:
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise QuestionWorkspaceError("修订后与已有题目重复", status_code=409) from exc
    return item


def reject_item(db: Session, *, owner_user_id: int, question_id: str) -> UserQuestionItem | None:
    item = db.query(UserQuestionItem).filter_by(
        question_id=question_id,
        owner_user_id=owner_user_id,
    ).one_or_none()
    if item is None:
        return None
    if item.status == "active":
        raise QuestionWorkspaceError("已激活题目请先停用", status_code=409)
    item.status = "rejected"
    item.review_reason = "用户拒绝导入"
    db.commit()
    db.refresh(item)
    return item


def deactivate_item(db: Session, *, owner_user_id: int, question_id: str) -> UserQuestionItem | None:
    item = db.query(UserQuestionItem).filter_by(
        question_id=question_id,
        owner_user_id=owner_user_id,
    ).one_or_none()
    if item is None:
        return None
    if item.status != "active":
        raise QuestionWorkspaceError("只有已激活题目可以停用", status_code=409)
    item.status = "inactive"
    db.commit()
    db.refresh(item)
    return item


def confirm_item(db: Session, *, owner_user_id: int, question_id: str) -> UserQuestionItem | None:
    item = db.query(UserQuestionItem).filter_by(
        question_id=question_id,
        owner_user_id=owner_user_id,
    ).one_or_none()
    if item is None:
        return None
    if item.status != "preview_ready":
        raise QuestionWorkspaceError("题目尚未达到可确认状态", status_code=409)
    item.status = "active"
    item.confirmed_at = utc_now()
    db.commit()
    db.refresh(item)
    return item


def list_active_questions(db: Session, *, owner_user_id: int) -> list[dict[str, Any]]:
    rows = db.query(UserQuestionItem).filter_by(
        owner_user_id=owner_user_id,
        status="active",
    ).order_by(UserQuestionItem.created_at.desc()).all()
    return [_public_item(item) for item in rows]

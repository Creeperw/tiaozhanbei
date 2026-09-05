from __future__ import annotations

import asyncio
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
from APP.backend.health_llm import build_llm_client
from APP.backend.mineru_pdf_service import MinerUPdfParser
from APP.backend.time_utils import utc_now
from competition_app.contracts.difficulty import parse_difficulty

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
ALLOWED_EXTENSIONS = {".pdf", ".md", ".txt", *IMAGE_EXTENSIONS}
ALLOWED_CONTENT_TYPES = {
    ".pdf": {"application/pdf"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".webp": {"image/webp"},
    ".bmp": {"image/bmp", "image/x-ms-bmp"},
    ".tif": {"image/tiff"},
    ".tiff": {"image/tiff"},
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
            r"(?m)^[-*]?\s*(题型|题干|选项|答案|解析|知识点|难度)\s*[:：]\s*",
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
            "difficulty": _normalize_text(fields.get("难度", "")) or None,
        })
    if not rows:
        raise QuestionWorkspaceError(
            "未识别到题目；请使用“## 题目”及“题干/答案”字段",
            status_code=422,
        )
    return rows


def _json_payload(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise QuestionWorkspaceError("模型未返回可解析的题目 JSON", status_code=502) from exc
    if not isinstance(payload, dict):
        raise QuestionWorkspaceError("模型返回的题目结构无效", status_code=502)
    return payload


def _normalize_llm_row(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    stem = _normalize_text(str(value.get("stem") or value.get("题干") or ""))
    if not stem:
        return None
    raw_options = value.get("options") or value.get("选项") or []
    if isinstance(raw_options, str):
        raw_options = re.split(r"(?:\n|[；;])", raw_options)
    options = [_normalize_text(str(item)) for item in raw_options if _normalize_text(str(item))]
    raw_kp_ids = value.get("kp_ids") or value.get("知识点") or []
    if isinstance(raw_kp_ids, str):
        raw_kp_ids = re.split(r"[,，\s]+", raw_kp_ids)
    raw_difficulty = value.get("difficulty") or value.get("难度")
    return {
        "question_type": _normalize_text(str(value.get("question_type") or value.get("题型") or "未分类")),
        "stem": stem,
        "options": options,
        "answer": _normalize_text(str(value.get("answer") or value.get("答案") or "")),
        "analysis": _normalize_text(str(value.get("analysis") or value.get("解析") or "")),
        "kp_ids": [_normalize_text(str(item)) for item in raw_kp_ids if _normalize_text(str(item))],
        "difficulty": str(raw_difficulty).strip() if raw_difficulty is not None else None,
    }


def _llm_extract_questions(markdown: str) -> list[dict[str, Any]]:
    from APP.backend.config import LLM_API_KEY, LLM_MODE

    if LLM_MODE == "api" and not LLM_API_KEY:
        raise QuestionWorkspaceError("题目结构化需要配置大模型 API Key", status_code=503)
    client = build_llm_client(role="reviewer")
    system_prompt = (
        "你是医学题库结构化处理器。只输出 JSON，不要解释。完整提取材料中的每一道题；"
        "已有答案和解析必须原样保留，不得改写。材料没有答案时，结合医学知识生成可靠答案和解析。"
        "输出格式：{\"items\":[{\"question_type\":\"单项选择题/简答题/病例分析题\","
        "\"stem\":\"题干\",\"options\":[\"A. ...\"],\"answer\":\"答案\","
        "\"analysis\":\"解析\",\"kp_ids\":[],\"difficulty\":\"1-5或D1-D5之一，材料未标注时省略\"}]}。"
    )
    chunks: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", markdown):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) + 2 > 12000:
            chunks.append(current)
            current = ""
        if len(paragraph) > 12000:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(paragraph[index:index + 12000] for index in range(0, len(paragraph), 12000))
        else:
            current = f"{current}\n\n{paragraph}".strip()
    if current:
        chunks.append(current)

    rows: list[dict[str, Any]] = []
    seen_stems: set[str] = set()
    for chunk in chunks:
        response = client.chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": chunk},
        ], temperature=0.1, max_tokens=8192)
        for value in _json_payload(response).get("items", []):
            row = _normalize_llm_row(value)
            if row is None:
                continue
            stem_key = _normalize_text(row["stem"]).lower()
            if stem_key in seen_stems:
                continue
            seen_stems.add(stem_key)
            rows.append(row)
    if not rows:
        raise QuestionWorkspaceError("模型未从文件中识别到题目", status_code=422)
    return rows


def _complete_missing_answers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    missing = [
        {"index": index, "question_type": row["question_type"], "stem": row["stem"],
         "options": row["options"], "existing_analysis": row["analysis"]}
        for index, row in enumerate(rows) if not row["answer"]
    ]
    if not missing:
        return rows
    from APP.backend.config import LLM_API_KEY, LLM_MODE

    if LLM_MODE == "api" and not LLM_API_KEY:
        return rows
    try:
        client = build_llm_client(role="reviewer")
        generated: list[Any] = []
        for offset in range(0, len(missing), 20):
            response = client.chat([
                {
                    "role": "system",
                    "content": (
                        "你是医学题库答案补全器。只处理缺少答案的题，不改动题干和选项。"
                        "只输出 JSON：{\"items\":[{\"index\":0,\"answer\":\"答案\","
                        "\"analysis\":\"解析\"}]}。无法可靠作答时 answer 留空。"
                    ),
                },
                {"role": "user", "content": json.dumps({"items": missing[offset:offset + 20]}, ensure_ascii=False)},
            ], temperature=0.1, max_tokens=8192)
            generated.extend(_json_payload(response).get("items", []))
    except Exception:
        return rows
    for value in generated:
        if not isinstance(value, dict):
            continue
        try:
            index = int(value.get("index"))
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(rows) or rows[index]["answer"]:
            continue
        rows[index]["answer"] = _normalize_text(str(value.get("answer") or ""))
        generated_analysis = _normalize_text(str(value.get("analysis") or ""))
        if generated_analysis:
            rows[index]["analysis"] = generated_analysis
    return rows


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise QuestionWorkspaceError("文本编码不受支持", status_code=422)


def _image_as_pdf(source: Path, target: Path) -> None:
    try:
        from PIL import Image

        with Image.open(source) as image:
            image.convert("RGB").save(target, "PDF", resolution=150.0)
    except ImportError as exc:
        raise QuestionWorkspaceError("图片转 PDF 组件不可用", status_code=503) from exc
    except Exception as exc:
        raise QuestionWorkspaceError("图片文件无效", status_code=422) from exc


def _extract_with_mineru(source: Path, extension: str) -> str:
    mineru_source = source
    converted_pdf = source.with_suffix(f"{source.suffix}.mineru.pdf")
    try:
        if extension == ".pdf":
            if not source.read_bytes()[:5].startswith(b"%PDF-"):
                raise QuestionWorkspaceError("PDF 解析失败", status_code=422)
        else:
            _image_as_pdf(source, converted_pdf)
            mineru_source = converted_pdf
        return MinerUPdfParser().parse(mineru_source)
    except QuestionWorkspaceError:
        raise
    except ValueError as exc:
        raise QuestionWorkspaceError("PDF/图片解析失败", status_code=422) from exc
    except RuntimeError as exc:
        raise QuestionWorkspaceError(f"MinerU 解析失败：{exc}", status_code=503) from exc
    finally:
        if converted_pdf != source:
            converted_pdf.unlink(missing_ok=True)


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
        "explanation": item.analysis,
        "options": json.loads(item.options_json or "[]"),
        "kp_ids": json.loads(item.kp_ids_json or "[]"),
        "difficulty": item.difficulty,
        "difficulty_source": item.difficulty_source,
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
        raise QuestionWorkspaceError("仅支持 PDF、图片、Markdown 和 TXT 文件", status_code=415)
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
        if extension == ".pdf" or extension in IMAGE_EXTENSIONS:
            text = await asyncio.to_thread(_extract_with_mineru, stored_path, extension)
        else:
            text = _decode_text(content)
        try:
            rows = _extract_structured_questions(text)
            rows = await asyncio.to_thread(_complete_missing_answers, rows)
        except QuestionWorkspaceError as exc:
            if "未识别到题目" not in str(exc):
                raise
            rows = await asyncio.to_thread(_llm_extract_questions, text)
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
            difficulty=parse_difficulty(row.get("difficulty")),
            difficulty_source=(
                f"user-import:{job_id[:16]}"
                if parse_difficulty(row.get("difficulty")) is not None
                else None
            ),
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
            "error_message": job.error_message or "",
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


def confirm_import_items(
    db: Session,
    *,
    owner_user_id: int,
    job_id: str,
) -> list[dict[str, Any]] | None:
    job = get_import(db, owner_user_id=owner_user_id, job_id=job_id)
    if job is None:
        return None

    items = db.query(UserQuestionItem).filter_by(
        owner_user_id=owner_user_id,
        job_id=job_id,
    ).order_by(UserQuestionItem.created_at.asc()).all()
    pending_items = [item for item in items if item.status not in {"active", "rejected", "inactive"}]
    if not pending_items:
        raise QuestionWorkspaceError("没有可确认导入的题目", status_code=409)
    if any(item.status != "preview_ready" for item in pending_items):
        raise QuestionWorkspaceError("仍有题目需要人工修订，暂不能全部确认", status_code=409)

    confirmed_at = utc_now()
    for item in pending_items:
        item.status = "active"
        item.confirmed_at = confirmed_at
    db.commit()
    for item in pending_items:
        db.refresh(item)
    return [_public_item(item) for item in pending_items]


def list_active_questions(db: Session, *, owner_user_id: int) -> list[dict[str, Any]]:
    rows = db.query(UserQuestionItem).filter_by(
        owner_user_id=owner_user_id,
        status="active",
    ).order_by(UserQuestionItem.created_at.desc()).all()
    return [_public_item(item) for item in rows]

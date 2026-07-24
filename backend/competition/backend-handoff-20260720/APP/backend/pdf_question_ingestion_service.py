from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Protocol

from sqlalchemy.orm import Session

from APP.backend.config import UPLOAD_DIR
from APP.backend.question_ingestion_service import QuestionIngestionService
from APP.backend.store import FILES
from APP.backend.mineru_pdf_service import MinerUPdfParser


class PdfParser(Protocol):
    def parse(self, file_path: Path) -> str: ...


class MarkdownQuestionExtractor(Protocol):
    def extract(
        self,
        markdown: str,
        source_ref: str,
        source_type: str,
        owner_id: str | None,
    ) -> list[dict[str, Any]]: ...


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", normalized).strip()


class StructuredMarkdownExtractor:
    def extract(
        self,
        markdown: str,
        source_ref: str,
        source_type: str,
        owner_id: str | None,
    ) -> list[dict[str, Any]]:
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
            rows.append({
                "stem": stem,
                "answer": _normalize_text(fields.get("答案", "")),
                "analysis": _normalize_text(fields.get("解析", "")),
                "question_type": _normalize_text(fields.get("题型", "")) or "short_answer",
                "requested_kp_ids": [
                    value
                    for value in re.split(r"[,，\s]+", fields.get("知识点", ""))
                    if value
                ],
                "source_ref": source_ref,
                "source_type": source_type,
                "owner_id": owner_id,
            })
        if not rows:
            raise ValueError("No structured questions were found in the PDF")
        return rows


class PdfQuestionIngestionService:
    def __init__(
        self,
        mineru_factory: Callable[[], PdfParser] = MinerUPdfParser,
        extractor_factory: Callable[[], MarkdownQuestionExtractor] | None = None,
        ingestion_service_factory: Callable[[], QuestionIngestionService] = QuestionIngestionService,
    ):
        self._mineru_factory = mineru_factory
        self._extractor_factory = extractor_factory or StructuredMarkdownExtractor
        self._ingestion_service_factory = ingestion_service_factory

    def build_payload(self, *, file_id: str, submitted_by_user_id: int) -> dict[str, Any]:
        info = FILES.get(file_id)
        if not info:
            raise ValueError("Uploaded PDF was not found")
        if info.get("uploader_id") != submitted_by_user_id:
            raise ValueError("Uploaded PDF does not belong to this user")
        filename = str(info.get("original_name") or "")
        file_path = Path(str(info.get("saved_path") or "")).resolve()
        upload_root = Path(UPLOAD_DIR).resolve()
        if file_path.suffix.lower() != ".pdf":
            raise ValueError("Only PDF uploads are supported")
        if upload_root not in file_path.parents or not file_path.is_file():
            raise ValueError("Uploaded PDF is outside the upload directory")
        return {
            "file_id": file_id,
            "file_path": str(file_path),
            "original_filename": filename,
            "source_ref": f"upload:{file_id}",
            "source_type": "admin_pdf_upload",
            "owner_id": str(submitted_by_user_id),
        }

    def ingest(self, db: Session, payload: dict[str, Any]) -> dict[str, Any]:
        markdown = self._mineru_factory().parse(Path(str(payload["file_path"])))
        rows = self._extractor_factory().extract(
            markdown,
            str(payload["source_ref"]),
            str(payload.get("source_type") or "admin_pdf_upload"),
            str(payload["owner_id"]),
        )
        ingestion = self._ingestion_service_factory()
        results = []
        for row in rows:
            results.append(ingestion.ingest(db, row))
            db.commit()
        counts = Counter(str(result.get("status") or "failed") for result in results)
        return {
            "status": "completed",
            "file_id": payload["file_id"],
            "question_count": len(results),
            "status_counts": dict(counts),
            "results": results,
        }

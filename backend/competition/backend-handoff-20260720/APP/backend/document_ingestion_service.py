from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from APP.backend.config import MARKITDOWN_EXTRACT_TIMEOUT_SECONDS
from APP.backend.database import KnowledgePoint, QuestionBankItem, TeachingResource
from APP.backend.rag_core import rag_service
from APP.backend import audit_agent_service


class DocumentIngestionResult(BaseModel):
    status: str
    markdown: str = ""
    audit_decision: dict[str, Any]
    extracted_knowledge_points: list[str] = Field(default_factory=list)
    extracted_questions: list[str] = Field(default_factory=list)
    extracted_resources: list[str] = Field(default_factory=list)
    rag_index_status: str = "skipped"
    risk_notes: list[str] = Field(default_factory=list)


def extract_document_with_markitdown(file_path: str) -> str:
    completed = subprocess.run(
        [sys.executable, "-m", "markitdown", file_path],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=MARKITDOWN_EXTRACT_TIMEOUT_SECONDS,
    )
    return completed.stdout


def review_document_ingestion(markdown: str, metadata: dict[str, Any]) -> dict[str, Any]:
    return audit_agent_service.review_document_ingestion(markdown, metadata)


def _parts(line: str) -> list[str]:
    return [item.strip() for item in line.split("｜")]


def _json(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False)


def _kp_ids(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _upsert_knowledge_point(db: Session, parts: list[str], source: str) -> str | None:
    if len(parts) < 2:
        return None
    kp_id = parts[0]
    name = parts[1]
    aliases = _kp_ids(parts[2]) if len(parts) > 2 else []
    description = parts[3] if len(parts) > 3 else ""
    row = db.query(KnowledgePoint).filter(KnowledgePoint.kp_id == kp_id).first()
    if row is None:
        row = KnowledgePoint(kp_id=kp_id, name=name, aliases_json=_json(aliases), description=description, source=source)
        db.add(row)
    else:
        row.name = name
        row.aliases_json = _json(aliases)
        row.description = description
        row.source = source
        row.status = "active"
    return kp_id


def _upsert_question(db: Session, parts: list[str], source: str) -> str | None:
    if len(parts) < 5:
        return None
    question_id = parts[0]
    difficulty = 2.0
    if len(parts) > 5:
        try:
            difficulty = float(parts[5])
        except ValueError:
            difficulty = 2.0
    row = db.query(QuestionBankItem).filter(QuestionBankItem.question_id == question_id).first()
    fields = {
        "stem": parts[1],
        "answer": parts[2],
        "analysis": parts[3],
        "kp_ids_json": _json(_kp_ids(parts[4])),
        "difficulty": difficulty,
        "quality_score": 0.75,
        "source": source,
        "status": "active",
    }
    if row is None:
        row = QuestionBankItem(question_id=question_id, **fields)
        db.add(row)
    else:
        for key, value in fields.items():
            setattr(row, key, value)
    return question_id


def _upsert_resource(db: Session, parts: list[str], source: str) -> str | None:
    if len(parts) < 5:
        return None
    resource_id = parts[0]
    row = db.query(TeachingResource).filter(TeachingResource.resource_id == resource_id).first()
    fields = {
        "resource_type": parts[1],
        "title": parts[2],
        "summary": parts[3],
        "kp_ids_json": _json(_kp_ids(parts[4])),
        "source": source,
        "quality_score": 0.75,
        "status": "active",
    }
    if row is None:
        row = TeachingResource(resource_id=resource_id, **fields)
        db.add(row)
    else:
        for key, value in fields.items():
            setattr(row, key, value)
    return resource_id


def extract_structured_assets(db: Session, markdown: str, source: str) -> dict[str, list[str]]:
    kp_ids: list[str] = []
    question_ids: list[str] = []
    resource_ids: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("知识点："):
            kp_id = _upsert_knowledge_point(db, _parts(line.removeprefix("知识点：")), source)
            if kp_id:
                kp_ids.append(kp_id)
        elif line.startswith("题目："):
            question_id = _upsert_question(db, _parts(line.removeprefix("题目：")), source)
            if question_id:
                question_ids.append(question_id)
        elif line.startswith("资源："):
            resource_id = _upsert_resource(db, _parts(line.removeprefix("资源：")), source)
            if resource_id:
                resource_ids.append(resource_id)
    return {
        "knowledge_points": list(dict.fromkeys(kp_ids)),
        "questions": list(dict.fromkeys(question_ids)),
        "resources": list(dict.fromkeys(resource_ids)),
    }


def write_markdown_to_knowledge_source(*, markdown: str, original_filename: str, scope: str, user_id: int | None) -> str:
    target_user_id = user_id if scope == "personal" else None
    data_dir, _ = rag_service._paths_for_scope(scope, target_user_id)
    stem = Path(original_filename or "document").stem or "document"
    safe_name = rag_service._safe_filename(f"{stem}.md")
    target = os.path.join(data_dir, safe_name)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(markdown)
    return target


def ingest_document(
    db: Session,
    *,
    file_path: str,
    original_filename: str,
    scope: str,
    user_id: int | None,
    document_kind: str,
    extractor: Callable[[str], str] = extract_document_with_markitdown,
    audit_reviewer: Callable[[str, dict[str, Any]], dict[str, Any]] = review_document_ingestion,
    knowledge_source_writer: Callable[..., Any] = write_markdown_to_knowledge_source,
    rag_rebuild: Callable[..., Any] | None = None,
) -> DocumentIngestionResult:
    markdown = extractor(file_path)
    metadata = {
        "original_filename": original_filename,
        "scope": scope,
        "user_id": user_id,
        "document_kind": document_kind,
    }
    audit = audit_reviewer(markdown, metadata)
    risk_notes = [str(note) for note in audit.get("risk_notes", [])]
    if audit.get("decision") != "pass":
        return DocumentIngestionResult(
            status="rejected",
            markdown=markdown,
            audit_decision=audit,
            rag_index_status="skipped",
            risk_notes=risk_notes,
        )

    source = f"document:{original_filename}"
    assets = extract_structured_assets(db, markdown, source)
    try:
        knowledge_source_writer(markdown=markdown, original_filename=original_filename, scope=scope, user_id=user_id)
    except Exception:
        db.rollback()
        raise
    db.commit()
    (rag_rebuild or rag_service.rebuild_index)(scope=scope, user_id=user_id if scope == "personal" else None)
    return DocumentIngestionResult(
        status="approved",
        markdown=markdown,
        audit_decision=audit,
        extracted_knowledge_points=assets["knowledge_points"],
        extracted_questions=assets["questions"],
        extracted_resources=assets["resources"],
        rag_index_status="rebuild_started",
        risk_notes=risk_notes,
    )

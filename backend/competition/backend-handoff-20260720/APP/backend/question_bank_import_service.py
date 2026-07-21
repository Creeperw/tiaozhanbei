from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from APP.backend.database import FormalContentImportBatch, QuestionBankItem


_SOURCE_PREFIX = "formal-vector-question-bank:"
_QUESTION_TYPES = {
    "单项选择题": "single_choice",
    "单选题": "single_choice",
    "多项选择题": "multiple_choice",
    "多选题": "multiple_choice",
    "判断题": "true_false",
    "填空题": "fill_blank",
    "名词解释": "term_explanation",
    "简答题": "short_answer",
    "案例分析/实验报告": "case_quiz",
    "病例分析/实践技能": "case_quiz",
    "临床病例问答": "case_quiz",
    "临床案例问答": "case_quiz",
    "问答题": "short_answer",
}


@dataclass(frozen=True)
class QuestionBankImportSummary:
    content_sha256: str
    source_tag: str
    created_count: int
    skipped_count: int
    invalid_count: int
    unlinked_count: int
    idempotent: bool = False


def import_question_bank_metadata(
    db: Session,
    *,
    metadata_path: str | Path,
) -> QuestionBankImportSummary:
    path = Path(metadata_path)
    content_sha256 = _sha256(path)
    source_tag = f"{_SOURCE_PREFIX}{content_sha256[:24]}"
    existing_batch = db.query(FormalContentImportBatch).filter_by(
        data_version="vector-question-bank-v1",
        content_sha256=content_sha256,
    ).one_or_none()
    if existing_batch is not None:
        return replace(_summary_from_json(existing_batch.summary_json), idempotent=True)

    created_count = 0
    skipped_count = 0
    invalid_count = 0
    unlinked_count = 0
    try:
        with db.begin_nested():
            known_ids = {
                question_id
                for question_id, in db.query(QuestionBankItem.question_id).all()
            }
            for record in _records(path):
                if not isinstance(record, dict):
                    invalid_count += 1
                    continue
                item = record.get("original")
                if not isinstance(item, dict):
                    invalid_count += 1
                    continue
                question_id = _text(item.get("题目id"))
                stem = _text(item.get("题目内容"))
                if not question_id or not stem:
                    invalid_count += 1
                    continue
                if question_id in known_ids:
                    skipped_count += 1
                    continue
                db.add(QuestionBankItem(
                    question_id=question_id,
                    stem=stem,
                    answer=_text(item.get("题目答案")),
                    analysis=_text(item.get("题目答案解析")),
                    kp_ids_json="[]",
                    question_type=_question_type(item.get("题型")),
                    difficulty=2.0,
                    quality_score=0.7,
                    source=_source_label(item),
                    status="pending_link",
                ))
                known_ids.add(question_id)
                created_count += 1
                unlinked_count += 1
            summary = QuestionBankImportSummary(
                content_sha256=content_sha256,
                source_tag=source_tag,
                created_count=created_count,
                skipped_count=skipped_count,
                invalid_count=invalid_count,
                unlinked_count=unlinked_count,
            )
            db.add(FormalContentImportBatch(
                data_version="vector-question-bank-v1",
                content_sha256=content_sha256,
                source_tag=source_tag,
                summary_json=json.dumps(asdict(summary), ensure_ascii=False, sort_keys=True),
            ))
            db.flush()
    except IntegrityError:
        existing_batch = db.query(FormalContentImportBatch).filter_by(
            data_version="vector-question-bank-v1",
            content_sha256=content_sha256,
        ).one_or_none()
        if existing_batch is None:
            raise
        return replace(_summary_from_json(existing_batch.summary_json), idempotent=True)
    return summary


def _records(path: Path):
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _question_type(value: Any) -> str:
    return _QUESTION_TYPES.get(_text(value), "short_answer")


def _source_label(item: dict[str, Any]) -> str:
    values = [_text(item.get("题目大来源")), _text(item.get("题目章节来源"))]
    return " / ".join(value for value in values if value)[:120] or "vector_question_bank"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _summary_from_json(value: str) -> QuestionBankImportSummary:
    payload = json.loads(value or "{}")
    return QuestionBankImportSummary(
        content_sha256=str(payload["content_sha256"]),
        source_tag=str(payload["source_tag"]),
        created_count=int(payload["created_count"]),
        skipped_count=int(payload["skipped_count"]),
        invalid_count=int(payload["invalid_count"]),
        unlinked_count=int(payload["unlinked_count"]),
        idempotent=bool(payload.get("idempotent", False)),
    )

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from APP.backend.database import (
    FormalContentImportBatch,
    KnowledgePoint,
    LearningKnowledgePoint,
    LearningQuestion,
    QuestionBankItem,
    QuestionKPLinkRecord,
    QuestionVersionRecord,
)

_SOURCE_PREFIX = "formal-content:"
_QUESTION_TYPES = {
    "单项选择题": "single_choice",
    "多项选择题": "multiple_choice",
    "判断题": "true_false",
    "填空题": "fill_blank",
    "名词解释": "term_explanation",
    "简答题": "short_answer",
    "案例分析/实验报告": "case_quiz",
    "临床病例问答": "case_quiz",
}


@dataclass(frozen=True)
class FormalContentImportSummary:
    data_version: str
    content_sha256: str
    source_tag: str
    knowledge_points: int
    questions: int
    active_questions: int
    pending_link_questions: int
    invalid_links: int
    idempotent: bool = False


def import_formal_learning_content(
    db: Session,
    *,
    knowledge_points_path: str | Path,
    questions_path: str | Path,
    question_kp_links_path: str | Path | None,
    data_version: str,
) -> FormalContentImportSummary:
    if not data_version.strip():
        raise ValueError("data_version is required")

    sources = {
        "knowledge_points": Path(knowledge_points_path),
        "questions": Path(questions_path),
    }
    if question_kp_links_path is not None:
        sources["question_kp_links"] = Path(question_kp_links_path)
    content_sha256 = _content_sha256(sources)
    source_tag = f"{_SOURCE_PREFIX}{data_version}:{content_sha256[:16]}"
    if len(source_tag) > 120:
        raise ValueError("data_version is too long")
    batch = db.query(FormalContentImportBatch).filter_by(
        data_version=data_version,
        content_sha256=content_sha256,
    ).one_or_none()
    if batch is not None:
        return replace(_summary_from_json(batch.summary_json), idempotent=True)

    knowledge_points = _json_array(sources["knowledge_points"])
    questions = _json_array(sources["questions"])
    links = _jsonl(sources["question_kp_links"]) if "question_kp_links" in sources else []
    _require_unique(knowledge_points, "kp_id")
    _require_unique(questions, "题目id")

    try:
        with db.begin_nested():
            _assert_no_foreign_source_collisions(db, knowledge_points, questions)
            known_kp_ids = {str(row["kp_id"]) for row in knowledge_points}
            question_ids = {str(row["题目id"]) for row in questions}
            links_by_question, invalid_links = _valid_links(links, known_kp_ids, question_ids)

            _upsert_knowledge_points(db, knowledge_points, source_tag)
            _deactivate_missing_formal_questions(db, question_ids, source_tag)
            _upsert_questions(db, questions, links_by_question, source_tag)
            summary = FormalContentImportSummary(
                data_version=data_version,
                content_sha256=content_sha256,
                source_tag=source_tag,
                knowledge_points=len(knowledge_points),
                questions=len(questions),
                active_questions=len(links_by_question),
                pending_link_questions=len(questions) - len(links_by_question),
                invalid_links=invalid_links,
            )
            db.add(FormalContentImportBatch(
                data_version=data_version,
                content_sha256=content_sha256,
                source_tag=source_tag,
                summary_json=json.dumps(asdict(summary), ensure_ascii=False, sort_keys=True),
            ))
            db.flush()
        return summary
    except IntegrityError:
        batch = db.query(FormalContentImportBatch).filter_by(
            data_version=data_version,
            content_sha256=content_sha256,
        ).one_or_none()
        if batch is None:
            raise
        return replace(_summary_from_json(batch.summary_json), idempotent=True)


def _content_sha256(sources: dict[str, Path]) -> str:
    digest = hashlib.sha256()
    for name, path in sorted(sources.items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _json_array(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{path} must contain a JSON array of objects")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number} must contain a JSON object")
            records.append(record)
    return records


def _require_unique(rows: list[dict[str, Any]], key: str) -> None:
    values = [str(row.get(key) or "").strip() for row in rows]
    if not all(values) or len(values) != len(set(values)):
        raise ValueError(f"{key} values must be present and unique")


def _valid_links(
    links: list[dict[str, Any]], known_kp_ids: set[str], question_ids: set[str]
) -> tuple[dict[str, tuple[str, ...]], int]:
    valid: dict[str, set[str]] = {}
    invalid = 0
    for link in links:
        question_id = str(link.get("question_id") or link.get("题目id") or "").strip()
        kp_id = str(link.get("kp_id") or "").strip()
        if not question_id or not kp_id or question_id not in question_ids or kp_id not in known_kp_ids:
            invalid += 1
            continue
        valid.setdefault(question_id, set()).add(kp_id)
    return {question_id: tuple(sorted(kp_ids)) for question_id, kp_ids in valid.items()}, invalid


def _assert_no_foreign_source_collisions(
    db: Session, knowledge_points: list[dict[str, Any]], questions: list[dict[str, Any]]
) -> None:
    kp_ids = {str(row["kp_id"]) for row in knowledge_points}
    question_ids = {str(row["题目id"]) for row in questions}
    for row in _rows_for_ids(db, KnowledgePoint, KnowledgePoint.kp_id, kp_ids):
        if row.source and not row.source.startswith(_SOURCE_PREFIX):
            raise ValueError(f"knowledge point {row.kp_id} belongs to another source")
    for row in _rows_for_ids(db, QuestionBankItem, QuestionBankItem.question_id, question_ids):
        if row.source and not row.source.startswith(_SOURCE_PREFIX):
            raise ValueError(f"question {row.question_id} belongs to another source")


def _upsert_knowledge_points(db: Session, rows: list[dict[str, Any]], source_tag: str) -> None:
    ids = {str(row["kp_id"]) for row in rows}
    current = {row.kp_id: row for row in _rows_for_ids(db, KnowledgePoint, KnowledgePoint.kp_id, ids)}
    core = {row.kp_id: row for row in _rows_for_ids(db, LearningKnowledgePoint, LearningKnowledgePoint.kp_id, ids)}
    for item in rows:
        kp_id = str(item["kp_id"])
        aliases = _aliases(item.get("kp_Lv3_others"))
        point = current.get(kp_id)
        if point is None:
            point = KnowledgePoint(kp_id=kp_id)
            db.add(point)
        point.name = str(item.get("kp_Lv3_standard") or kp_id)
        point.aliases_json = json.dumps(aliases, ensure_ascii=False)
        point.description = " / ".join(filter(None, (str(item.get("kp_Lv1") or ""), str(item.get("kp_Lv2") or ""))))
        point.source = source_tag
        point.status = "active"

        mirror = core.get(kp_id)
        if mirror is None:
            mirror = LearningKnowledgePoint(kp_id=kp_id)
            db.add(mirror)
        mirror.kp_lv1 = str(item.get("kp_Lv1") or "")
        mirror.kp_lv2 = str(item.get("kp_Lv2") or "")
        mirror.kp_lv3 = str(item.get("kp_Lv3_standard") or "")
        mirror.raw_content = json.dumps(item.get("raw_content") or [], ensure_ascii=False)
        mirror.other_name_json = json.dumps(aliases, ensure_ascii=False)
        mirror.order_json = json.dumps({
            "global_order": item.get("global_order"),
            "order_code": item.get("order_code"),
        }, ensure_ascii=False)


def _upsert_questions(
    db: Session,
    rows: list[dict[str, Any]],
    links_by_question: dict[str, tuple[str, ...]],
    source_tag: str,
) -> None:
    ids = {str(row["题目id"]) for row in rows}
    current = {row.question_id: row for row in _rows_for_ids(db, QuestionBankItem, QuestionBankItem.question_id, ids)}
    core = {row.question_id: row for row in _rows_for_ids(db, LearningQuestion, LearningQuestion.question_id, ids)}
    _deactivate_superseded_formal_versions(db, ids, source_tag)
    for item in rows:
        question_id = str(item["题目id"])
        kp_ids = links_by_question.get(question_id, ())
        question = current.get(question_id)
        if question is None:
            question = QuestionBankItem(question_id=question_id)
            db.add(question)
        question.stem = str(item.get("题目内容") or "")
        question.answer = str(item.get("题目答案") or "")
        question.analysis = str(item.get("题目答案解析") or "")
        question.kp_ids_json = json.dumps(kp_ids, ensure_ascii=False)
        question.question_type = _question_type(item.get("题型"))
        question.difficulty = 2.0
        question.quality_score = 0.7
        question.source = source_tag
        question.status = "active" if kp_ids else "pending_link"
        if not kp_ids:
            _deactivate_core_question_mirror(core.get(question_id))
            continue

        mirror = core.get(question_id)
        if mirror is None:
            mirror = LearningQuestion(question_id=question_id)
            db.add(mirror)
        mirror.question_type = question.question_type
        mirror.question_content = question.stem
        mirror.answer_json = json.dumps([question.answer], ensure_ascii=False)
        mirror.explanation = question.analysis
        mirror.difficulty = question.difficulty
        mirror.kp_ids_json = question.kp_ids_json

        version_id = _formal_version_id(question_id, source_tag)
        version = db.query(QuestionVersionRecord).filter_by(question_version_id=version_id).one_or_none()
        if version is None:
            version = QuestionVersionRecord(
                question_version_id=version_id,
                question_id=question_id,
                version=_next_question_version(db, question_id),
            )
            db.add(version)
        version.question_type = question.question_type
        version.stem = question.stem
        version.answer = question.answer
        version.analysis = question.analysis
        version.standard_difficulty = 2
        version.source_kind = source_tag
        version.status = "active"
        db.flush()
        _upsert_question_links(db, version.question_version_id, kp_ids)


def _formal_version_id(question_id: str, source_tag: str) -> str:
    digest = hashlib.sha256(f"{question_id}\0{source_tag}".encode("utf-8")).hexdigest()[:24]
    prefix = question_id[:87]
    return f"{prefix}:formal:{digest}"


def _deactivate_missing_formal_questions(db: Session, current_question_ids: set[str], source_tag: str) -> None:
    stale = db.query(QuestionBankItem).filter(
        QuestionBankItem.source.startswith(_SOURCE_PREFIX),
        ~QuestionBankItem.question_id.in_(current_question_ids),
    ).all()
    for question in stale:
        question.status = "inactive"
        mirror = db.query(LearningQuestion).filter_by(question_id=question.question_id).one_or_none()
        _deactivate_core_question_mirror(mirror)
        for version in db.query(QuestionVersionRecord).filter_by(question_id=question.question_id).all():
            if version.source_kind.startswith(_SOURCE_PREFIX):
                version.status = "superseded"


def _deactivate_core_question_mirror(mirror: LearningQuestion | None) -> None:
    if mirror is not None:
        mirror.kp_ids_json = "[]"


def _next_question_version(db: Session, question_id: str) -> int:
    versions = [
        version
        for version, in db.query(QuestionVersionRecord.version).filter_by(question_id=question_id).all()
    ]
    return max(versions, default=0) + 1


def _question_type(value: Any) -> str:
    return _QUESTION_TYPES.get(str(value or "").strip(), "unknown")


def _deactivate_superseded_formal_versions(db: Session, question_ids: set[str], source_tag: str) -> None:
    for version in _rows_for_ids(db, QuestionVersionRecord, QuestionVersionRecord.question_id, question_ids):
        if version.source_kind.startswith(_SOURCE_PREFIX) and version.source_kind != source_tag:
            version.status = "superseded"


def _upsert_question_links(db: Session, question_version_id: str, kp_ids: tuple[str, ...]) -> None:
    existing = {
        row.kp_id: row
        for row in db.query(QuestionKPLinkRecord).filter_by(question_version_id=question_version_id).all()
    }
    for kp_id, row in existing.items():
        row.status = "active" if kp_id in kp_ids else "inactive"
    for index, kp_id in enumerate(kp_ids):
        if kp_id in existing:
            existing[kp_id].is_primary = index == 0
            continue
        db.add(QuestionKPLinkRecord(
            question_version_id=question_version_id,
            kp_id=kp_id,
            is_primary=index == 0,
            status="active",
        ))


def _rows_for_ids(db: Session, model, column, values: set[str]) -> Iterable[Any]:
    ordered = sorted(values)
    for index in range(0, len(ordered), 500):
        yield from db.query(model).filter(column.in_(ordered[index:index + 500])).all()


def _aliases(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return list(dict.fromkeys(item.strip() for item in value.replace("；", ";").split(";") if item.strip()))


def _summary_from_json(value: str) -> FormalContentImportSummary:
    payload = json.loads(value or "{}")
    return FormalContentImportSummary(
        data_version=str(payload["data_version"]),
        content_sha256=str(payload["content_sha256"]),
        source_tag=str(payload["source_tag"]),
        knowledge_points=int(payload["knowledge_points"]),
        questions=int(payload["questions"]),
        active_questions=int(payload["active_questions"]),
        pending_link_questions=int(payload["pending_link_questions"]),
        invalid_links=int(payload["invalid_links"]),
        idempotent=bool(payload.get("idempotent", False)),
    )

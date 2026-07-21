from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from APP.backend.database import PaperInstanceRecord, PaperItemRecord
from APP.backend.question_repository import (
    QuestionRepository,
    QuestionSelectionCriteria,
    QuestionShortage,
)


def _criteria(blueprint: dict[str, Any]) -> QuestionSelectionCriteria | None:
    distribution = blueprint.get("distribution")
    question_types = blueprint.get("types")
    difficulty = blueprint.get("difficulty")
    question_count = blueprint.get("question_count")
    if (
        not isinstance(distribution, dict)
        or not isinstance(question_types, list)
        or not question_types
        or len(question_types) != len(set(question_types))
        or set(distribution) != set(question_types)
        or isinstance(difficulty, bool)
        or not isinstance(difficulty, int)
        or isinstance(question_count, bool)
        or not isinstance(question_count, int)
        or any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in distribution.values())
        or sum(distribution.values()) != question_count
        or question_count < 1
    ):
        return None
    return QuestionSelectionCriteria(
        kp_ids=tuple(blueprint.get("kp_ids") or ()),
        type_difficulty_counts=tuple(
            (question_type, difficulty, distribution[question_type])
            for question_type in question_types
        ),
        exclude_question_ids=tuple(blueprint.get("exclude_question_ids") or ()),
    )


def _evidence_refs(evidence_pack: dict[str, Any], kp_ids: tuple[str, ...]) -> list[dict[str, str]]:
    requested = set(kp_ids)
    return [
        {"source_scope": item["source_scope"], "source_id": item["source_id"]}
        for item in evidence_pack.get("items") or []
        if isinstance(item, dict)
        and isinstance(item.get("source_scope"), str)
        and isinstance(item.get("source_id"), str)
        and requested.intersection(item.get("kp_ids") or [])
    ]


def _safe_unpublished_result(
    orchestration_result: dict[str, Any],
    *,
    status: str,
    summary: str,
    audit_decision: str,
) -> dict[str, Any]:
    result = {
        "task_id": orchestration_result.get("task_id", ""),
        "task_type": "paper_generation",
        "status": status,
        "title": "试卷生成未完成",
        "summary": summary,
        "artifact": {"artifact_type": "paper", "title": "试卷生成未完成", "content": {}},
        "evidence_pack": {
            "pack_id": "",
            "source_scope": "paper_generation_service",
            "source_id": orchestration_result.get("task_id", ""),
            "items": [],
            "kp_ids": [],
            "resolved_kp_ids": [],
        },
        "audit": {"decision": audit_decision, "status": status},
        "trace": [],
        "learning_updates": {},
    }
    run_id = orchestration_result.get("orchestration_run_id")
    if isinstance(run_id, str) and run_id.strip():
        result["orchestration_run_id"] = run_id
    return result


def _valid_selection(selected: Any, criteria: QuestionSelectionCriteria) -> bool:
    if not isinstance(selected, (tuple, list)):
        return False
    expected = {
        (question_type, difficulty): count
        for question_type, difficulty, count in criteria.type_difficulty_counts
    }
    actual = {key: 0 for key in expected}
    version_ids = set()
    question_ids = set()
    for question in selected:
        version_id = getattr(question, "question_version_id", None)
        question_id = getattr(question, "question_id", None)
        key = (getattr(question, "question_type", None), getattr(question, "standard_difficulty", None))
        if (
            not isinstance(version_id, str)
            or not version_id.strip()
            or version_id in version_ids
            or not isinstance(question_id, str)
            or not question_id.strip()
            or question_id in question_ids
            or key not in actual
        ):
            return False
        version_ids.add(version_id)
        question_ids.add(question_id)
        actual[key] += 1
    return actual == expected


def generate_and_publish_paper(
    *,
    db: Session,
    user_id: int,
    orchestration_result: dict[str, Any],
    repository: Any | None = None,
    need_audit: bool = True,
) -> dict[str, Any]:
    if need_audit is not True:
        raise ValueError("need_audit must be true for paper generation")
    audit = orchestration_result.get("audit") or {}
    if str(audit.get("decision", "")).strip().lower() != "pass":
        return _safe_unpublished_result(
            orchestration_result,
            status="failed",
            summary="试卷审核未通过，未发布任何题目内容。",
            audit_decision="reject",
        )

    artifact = orchestration_result.get("artifact") or {}
    blueprint = (artifact.get("content") or {}).get("paper_blueprint") or {}
    criteria = _criteria(blueprint)
    if criteria is None:
        return _safe_unpublished_result(
            orchestration_result,
            status="needs_clarification",
            summary="组卷蓝图缺少有效题型分布，请补充后重试。",
            audit_decision="needs_clarification",
        )
    repository = repository or QuestionRepository(sessionmaker(bind=db.get_bind()))
    selected = repository.select(criteria)
    if isinstance(selected, QuestionShortage) or not _valid_selection(selected, criteria):
        return _safe_unpublished_result(
            orchestration_result,
            status="needs_clarification",
            summary="题库候选不足，请调整组卷条件。",
            audit_decision="needs_clarification",
        )

    paper_id = f"PAPER_{uuid4().hex}"
    db.add(PaperInstanceRecord(
        paper_id=paper_id,
        task_id=orchestration_result["task_id"],
        orchestration_run_id=orchestration_result.get("orchestration_run_id", ""),
        learner_id=user_id,
        title=orchestration_result.get("title", ""),
        duration_minutes=max(1, min(24 * 60, int(blueprint.get("expected_duration_min") or 60))),
        blueprint_json=json.dumps(blueprint, ensure_ascii=False),
        evidence_pack_json=json.dumps(orchestration_result.get("evidence_pack") or {}, ensure_ascii=False),
    ))
    learner_items = []
    for position, question in enumerate(selected, start=1):
        refs = _evidence_refs(orchestration_result.get("evidence_pack") or {}, question.kp_ids)
        db.add(PaperItemRecord(
            paper_item_id=f"PI_{uuid4().hex}", paper_id=paper_id, position=position,
            question_id=question.question_id, question_version_id=question.question_version_id,
            question_type=question.question_type, stem_snapshot=question.stem,
            options_snapshot_json="[]",
            standard_answer_snapshot=question.answer,
            kp_snapshot_json=json.dumps(list(question.kp_ids), ensure_ascii=False),
            evidence_refs_json=json.dumps(refs, ensure_ascii=False), source_kind=question.source_kind,
            standard_difficulty=question.standard_difficulty,
            max_score_snapshot=100.0,
        ))
        learner_items.append({
            "position": position, "question_id": question.question_id,
            "question_version_id": question.question_version_id,
            "question_type": question.question_type, "stem": question.stem,
            "kp_ids": list(question.kp_ids), "standard_difficulty": question.standard_difficulty,
            "source_kind": question.source_kind, "evidence_refs": refs,
        })
    db.flush()
    return {
        **orchestration_result, "status": "completed", "paper_id": paper_id,
        "artifact": {"artifact_type": "paper", "title": orchestration_result.get("title", ""), "content": {"paper_id": paper_id, "items": learner_items}},
    }

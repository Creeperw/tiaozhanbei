from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Callable
from uuid import uuid5, NAMESPACE_URL

from sqlalchemy.orm import Session

from APP.backend.database import AuditResultRecord, GradingResultRecord

from APP.backend.agent_orchestrator_service import (
    OrchestrationRequest,
    OrchestrationTaskContext,
    run_agent_orchestration,
)


OrchestrationRunner = Callable[..., dict[str, Any]]
VariationPublisher = Callable[..., Any]

OUTPUT_BY_TASK_TYPE = {
    "handout_generation": "handout",
    "knowledge_card_generation": "knowledge_card",
    "paper_generation": "paper",
    "mistake_variation": "question_variation",
}


@dataclass(frozen=True)
class TrainingOrchestrationInput:
    task_id: str
    user_id: int
    task_type: str
    title: str
    query: str
    inputs: dict[str, Any]
    options: dict[str, Any]


def build_orchestration_request(value: TrainingOrchestrationInput) -> OrchestrationRequest:
    requested_output = OUTPUT_BY_TASK_TYPE.get(value.task_type)
    if requested_output is None:
        raise ValueError(f"unsupported training orchestration task type: {value.task_type}")

    difficulty = value.options.get("difficulty", value.inputs.get("difficulty"))
    expected_duration = value.options.get(
        "expected_duration_min",
        value.inputs.get("duration_minutes"),
    )
    return OrchestrationRequest(
        query=value.query,
        task_type=value.task_type,
        requested_outputs=[requested_output],
        task_context=OrchestrationTaskContext(
            correlation_id=value.task_id,
            kp_ids=list(value.inputs.get("kp_ids") or []),
            difficulty=difficulty,
            expected_duration_min=expected_duration,
            question_count=value.options.get("question_count", value.inputs.get("question_count")),
            types=list(value.options.get("types", value.inputs.get("types", [])) or []),
            distribution=dict(value.options.get("distribution", value.inputs.get("distribution", {})) or {}),
            mistake_id=value.inputs.get("mistake_id"),
            source_question_version_id=_safe_string(value.inputs.get("source_question_version_id")),
            source_question_id=_safe_string(value.inputs.get("source_question_id")),
            source_stem=_safe_string(value.inputs.get("source_stem")),
            source_answer=_safe_string(value.inputs.get("source_answer")),
            source_analysis=_safe_string(value.inputs.get("source_analysis")),
            source_question_type=_safe_string(value.inputs.get("source_question_type")) or "single_choice",
            source_difficulty=value.inputs.get("source_difficulty"),
        ),
    )


def _safe_artifact(value: TrainingOrchestrationInput) -> dict[str, Any]:
    return {
        "artifact_type": OUTPUT_BY_TASK_TYPE[value.task_type],
        "title": value.title,
        "content": {},
    }


def _safe_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_safe_string(item) for item in value) if item]


def _nonempty_string(value: Any) -> bool:
    return bool(_safe_string(value))


def _safe_confidence(value: Any) -> Any | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return value


def _project_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    items = []
    for item in value.get("items") if isinstance(value.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        projected_item = {
            "source_id": _safe_string(item.get("source_id")),
            "source_scope": _safe_string(item.get("source_scope")),
            "summary": _safe_string(item.get("summary")),
        }
        kp_ids = _safe_string_list(item.get("kp_ids"))
        if "kp_ids" in item:
            projected_item["kp_ids"] = kp_ids
        confidence = _safe_confidence(item.get("confidence"))
        if confidence is not None:
            projected_item["confidence"] = confidence
        items.append(projected_item)
    return {
        "pack_id": _safe_string(value.get("pack_id")),
        "source_scope": _safe_string(value.get("source_scope")),
        "source_id": _safe_string(value.get("source_id")),
        "resolved_kp_ids": _safe_string_list(value.get("resolved_kp_ids")),
        "items": items,
    }


def _project_audit(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "decision": _safe_string(value.get("decision")),
        "reason": _safe_string(value.get("reason")),
        "source_scope": _safe_string(value.get("source_scope")),
        "source_id": _safe_string(value.get("source_id")),
        "source_ids": _safe_string_list(value.get("source_ids")),
        "audit_id": _safe_string(value.get("audit_id")),
    }


def _project_variation_content(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    difficulty = value.get("difficulty")
    return {
        "stem": _safe_string(value.get("stem")),
        "question_type": _safe_string(value.get("question_type")) or "single_choice",
        "difficulty": difficulty if isinstance(difficulty, int) and not isinstance(difficulty, bool) else 2,
        "kp_ids": _safe_string_list(value.get("kp_ids")),
        "source_mistake_id": value.get("source_mistake_id"),
        "source_question_version_id": _safe_string(value.get("source_question_version_id")),
        "source_question_id": _safe_string(value.get("source_question_id")),
    }


def _persist_variation_audit(
    db: Session,
    *,
    value: TrainingOrchestrationInput,
    artifact_source_id: str,
    candidate: dict[str, Any],
    audit: dict[str, Any],
) -> str:
    audit_id = f"AUD_{uuid5(NAMESPACE_URL, f'{value.task_id}:{artifact_source_id}').hex}"
    db.add(GradingResultRecord(
        artifact_id=artifact_source_id,
        attempt_item_id=value.inputs["attempt_item_id"],
        version=1,
        status="audited_variation_candidate",
        schema_version="question_variation_v1",
        payload_json=json.dumps(candidate, ensure_ascii=False),
    ))
    # AuditResultRecord uses a composite foreign key to the immutable candidate
    # artifact.  Flush the parent first explicitly: MySQL must be able to resolve
    # that key before the audit row is inserted, and the two mappers do not have
    # an ORM relationship that would otherwise guarantee unit-of-work ordering.
    db.flush()
    db.add(AuditResultRecord(
        audit_id=audit_id,
        source_artifact_id=artifact_source_id,
        source_artifact_version=1,
        decision="pass",
        reason=_safe_string(audit.get("reason")),
        status="completed",
        schema_version="question_variation_v1",
        payload_json=json.dumps({"source_scope": "audit_agent"}, ensure_ascii=False),
    ))
    db.flush()
    return audit_id


def _normalize_orchestration_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "failed", "run_id": "", "execution_plan": {}, "steps": [], "final": {}}

    plan = value.get("execution_plan")
    normalized_plan = (
        {
            "objective": _safe_string(plan.get("objective")),
            "status": _safe_string(plan.get("status")),
            "assigned_agents": _safe_string_list(plan.get("assigned_agents")),
        }
        if isinstance(plan, dict)
        else {}
    )
    steps = []
    for raw_step in value.get("steps") if isinstance(value.get("steps"), list) else []:
        if not isinstance(raw_step, dict):
            continue
        steps.append(
            {
                "step_id": _safe_string(raw_step.get("step_id")),
                "agent_name": _safe_string(raw_step.get("agent_name")),
                "action": _safe_string(raw_step.get("action")),
                "status": _safe_string(raw_step.get("status")),
            }
        )
    final = value.get("final")
    artifact = final.get("artifact") if isinstance(final, dict) else None
    normalized_final = {
        "artifact": {
            "artifact_type": _safe_string(artifact.get("artifact_type")),
            "title": _safe_string(artifact.get("title")),
            "content": artifact.get("content") if isinstance(artifact.get("content"), dict) else {},
            "source_id": _safe_string(artifact.get("source_id")),
        }
        if isinstance(artifact, dict)
        else {},
        "evidence_pack": _project_evidence(final.get("evidence_pack")) if isinstance(final, dict) else {},
        "audit": _project_audit(final.get("audit")) if isinstance(final, dict) else {},
    }
    return {
        "status": _safe_string(value.get("status")),
        "run_id": _safe_string(value.get("run_id")),
        "execution_plan": normalized_plan,
        "steps": steps,
        "final": normalized_final,
    }


def _valid_evidence_item(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    kp_ids = value.get("kp_ids")
    return bool(
        _nonempty_string(value.get("source_id"))
        and _nonempty_string(value.get("source_scope"))
        and _nonempty_string(value.get("summary"))
        and (
            kp_ids is None
            or (
                isinstance(kp_ids, list)
                and all(_nonempty_string(kp_id) for kp_id in kp_ids)
            )
        )
    )


def _valid_evidence(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    source_id = value.get("source_id") or value.get("pack_id")
    items = value.get("items") or []
    resolved = value.get("resolved_kp_ids") or []
    return bool(
        _nonempty_string(source_id)
        and _nonempty_string(value.get("source_scope"))
        and isinstance(items, list)
        and isinstance(resolved, list)
        and (
            any(_valid_evidence_item(item) for item in items)
            or any(_nonempty_string(kp_id) for kp_id in resolved)
        )
    )


def _audit_source_ids(value: dict[str, Any]) -> set[str]:
    valid_source_ids = set(_safe_string_list(value.get("source_ids")))
    source_id = _safe_string(value.get("source_id"))
    if source_id:
        valid_source_ids.add(source_id)
    return valid_source_ids


def _safe_evidence(value: dict[str, Any], *, published: bool) -> dict[str, Any]:
    if not published:
        return {
            "pack_id": "",
            "source_scope": "",
            "source_id": "",
            "resolved_kp_ids": [],
            "items": [],
        }

    evidence = {
        "pack_id": _safe_string(value.get("pack_id")),
        "source_scope": _safe_string(value.get("source_scope")),
        "source_id": _safe_string(value.get("source_id")),
        "resolved_kp_ids": _safe_string_list(value.get("resolved_kp_ids")),
    }
    if published:
        items = []
        for item in value.get("items") or []:
            if not _valid_evidence_item(item):
                continue
            safe_item = {
                key: item[key]
                for key in ("source_id", "source_scope", "summary", "kp_ids")
                if key in item
            }
            confidence = _safe_confidence(item.get("confidence"))
            if confidence is not None:
                safe_item["confidence"] = confidence
            items.append(safe_item)
        evidence["items"] = items
    return evidence


def _safe_audit(value: dict[str, Any], *, reason: str, published: bool) -> dict[str, Any]:
    audit = {
        "decision": _safe_string(value.get("decision")) or "failed",
        "reason": reason,
        "status": _safe_string(value.get("status")),
    }
    if published:
        audit.update({
            "source_scope": _safe_string(value.get("source_scope")),
            "source_id": _safe_string(value.get("source_id")),
            "source_ids": _safe_string_list(value.get("source_ids")),
            "audit_id": _safe_string(value.get("audit_id")),
        })
    return audit


def _trace(
    value: TrainingOrchestrationInput,
    orchestration: dict[str, Any],
    *,
    published: bool,
    reason: str,
) -> list[dict[str, Any]]:
    run_id = orchestration["run_id"]
    plan = orchestration["execution_plan"]
    assigned_agents = plan.get("assigned_agents", [])
    trace = [
        {
            "step_id": "orchestration",
            "agent": "planner_orchestrator",
            "action": "execute_plan",
            "status": orchestration["status"] or "failed",
            "summary": "orchestration completed" if published else reason,
            "run_id": run_id,
            "task_type": value.task_type,
            "plan_status": plan.get("status", ""),
            "assigned_agents": assigned_agents,
        }
    ]
    for index, step in enumerate(orchestration.get("steps") or [], start=1):
        if not isinstance(step, dict):
            continue
        trace.append(
            {
                "step_id": step["step_id"] or f"step_{index}",
                "agent": step["agent_name"] or "agent_orchestrator",
                "action": step["action"] or "execute_step",
                "status": step["status"] or "failed",
            }
        )
    trace.append(
        {
            "step_id": "publication_gate",
            "agent": "training_orchestration_adapter",
            "action": "publication_gate",
            "status": "success" if published else "failed",
            "summary": reason,
            "published": published,
        }
    )
    return trace


def _publication_failure_reason(audit: dict[str, Any], failed_checks: list[str]) -> str:
    audit_reason = _safe_string(audit.get("reason"))
    if audit_reason.startswith("missing_evidence_ids:"):
        return "缺少可引用的正式训练证据，请先导入知识点和教学资源。"
    if _safe_string(audit.get("decision")).lower() in {"reject", "revise"}:
        return "audit 未通过，无法发布训练资料。"
    return f"未通过发布门禁：{', '.join(failed_checks)}"


def execute_training_orchestration(
    *,
    db: Session,
    value: TrainingOrchestrationInput,
    runtime: Any | None = None,
    runner: OrchestrationRunner = run_agent_orchestration,
    variation_publisher: VariationPublisher | None = None,
    defer_variation_persistence: bool = False,
) -> dict[str, Any]:
    try:
        orchestration = runner(
            db=db,
            user_id=value.user_id,
            request=build_orchestration_request(value),
            runtime=runtime,
        )
    except Exception:
        orchestration = {"status": "failed", "final": {}}
    orchestration = _normalize_orchestration_payload(orchestration)
    final = orchestration["final"]
    candidate = final.get("artifact")
    authoritative_variation = candidate.get("content") if (
        value.task_type == "mistake_variation" and isinstance(candidate, dict)
        and isinstance(candidate.get("content"), dict)
    ) else {}
    if value.task_type == "mistake_variation" and isinstance(candidate, dict):
        candidate = {**candidate, "content": _project_variation_content(candidate.get("content"))}
    evidence = final.get("evidence_pack") if isinstance(final.get("evidence_pack"), dict) else {}
    audit = final.get("audit") if isinstance(final.get("audit"), dict) else {}
    expected_type = OUTPUT_BY_TASK_TYPE[value.task_type]
    failed_step = any(
        isinstance(step, dict) and step.get("status") == "failed"
        for step in orchestration.get("steps") or []
    )
    artifact_source_id = candidate.get("source_id") if isinstance(candidate, dict) else None
    decision = _safe_string(audit.get("decision")).lower()

    checks = {
        "orchestration_success": orchestration.get("status") == "success",
        "artifact_type": isinstance(candidate, dict) and candidate.get("artifact_type") == expected_type,
        "artifact_content": isinstance(candidate, dict) and isinstance(candidate.get("content"), dict),
        "evidence": _valid_evidence(evidence),
        "audit": decision == "pass",
        "audit_scope": audit.get("source_scope") == "audit_agent",
        "audit_source": bool(artifact_source_id and artifact_source_id in _audit_source_ids(audit)),
        "steps": not failed_step,
    }
    published = all(checks.values())
    if published and value.task_type == "mistake_variation":
        content = candidate["content"]
        variation_checks = {
            "publisher": variation_publisher is not None,
            "mistake": content.get("source_mistake_id") == value.inputs.get("mistake_id"),
            "source_version": content.get("source_question_version_id") == value.inputs.get("source_question_version_id"),
            "kp_ids": bool(_safe_string_list(content.get("kp_ids"))),
            "stem": _nonempty_string(content.get("stem")),
            "standard_answer": _nonempty_string(authoritative_variation.get("answer")),
            "analysis": _nonempty_string(authoritative_variation.get("analysis")),
            "attempt_item_id": _nonempty_string(value.inputs.get("attempt_item_id")),
        }
        published = all(variation_checks.values())
        checks.update({f"variation_{key}": passed for key, passed in variation_checks.items()})
        if published:
            publication_content = {
                "owner_user_id": value.user_id,
                "source_mistake_id": content["source_mistake_id"],
                "source_question_version_id": content["source_question_version_id"],
                "source_question_id": _safe_string(content.get("source_question_id")),
                "stem": _safe_string(content.get("stem")),
                "question_type": _safe_string(content.get("question_type")) or "single_choice",
                "difficulty": content.get("difficulty"),
                "kp_ids": _safe_string_list(content.get("kp_ids")),
                "artifact_source_id": _safe_string(candidate.get("source_id")),
                "standard_answer": _safe_string(authoritative_variation.get("answer")),
                "rubric": {"analysis": _safe_string(authoritative_variation.get("analysis"))},
            }
            if defer_variation_persistence:
                publication_content = {**publication_content, "variation_task_id": value.task_id}
                candidate = {**candidate, "content": {**candidate["content"], "_publication": publication_content}}
            else:
                current_audit_id = _persist_variation_audit(
                    db,
                    value=value,
                    artifact_source_id=_safe_string(candidate.get("source_id")),
                    candidate=authoritative_variation,
                    audit=audit,
                )
                publication = variation_publisher(**publication_content, audit_id=current_audit_id)
                published_question_version_id = _safe_string(
                    getattr(publication, "question_version_id", None)
                    or (publication.get("question_version_id") if isinstance(publication, dict) else None)
                )
                if not published_question_version_id:
                    raise ValueError("variation publisher returned no question_version_id")
                candidate = {
                    **candidate,
                    "content": {
                        **candidate["content"],
                        "question_version_id": published_question_version_id,
                        "audit_id": current_audit_id,
                    },
                }
                audit = {
                    "audit_id": current_audit_id,
                    "decision": "pass",
                    "status": "completed",
                    "source_scope": "audit_agent",
                    "source_id": _safe_string(candidate.get("source_id")),
                    "source_ids": [_safe_string(candidate.get("source_id"))],
                }
    if decision in {"human_review", "needs_human_review"}:
        status = "needs_human_review"
    else:
        status = "completed" if published else "failed"
    failed_checks = [name for name, passed in checks.items() if not passed]
    reason = (
        "编排成功且审核通过，已发布培训资料。"
        if published
        else _publication_failure_reason(audit, failed_checks)
    )
    artifact = (
        {
            "artifact_type": candidate["artifact_type"],
            "title": _safe_string(candidate.get("title")) or value.title,
            "content": candidate["content"],
        }
        if published
        else _safe_artifact(value)
    )

    return {
        "task_id": value.task_id,
        "task_type": value.task_type,
        "status": status,
        "title": value.title,
        "summary": "培训资料已生成。" if published else "培训资料未完成，请检查后重试。",
        "artifact": artifact,
        "evidence_pack": _safe_evidence(evidence, published=published),
        "audit": _safe_audit(audit, reason=reason, published=published),
        "trace": _trace(value, orchestration, published=published, reason=reason),
        "learning_updates": {
            "activity_recorded": True,
            "mistake_recorded": False,
            "mastery_updates": [],
            "review_tasks": [],
            "profile_suggestions": [],
        },
        "next_actions": [],
        "orchestration_run_id": orchestration["run_id"],
    }

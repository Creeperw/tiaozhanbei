from __future__ import annotations

from typing import Any, Callable

from APP.backend.agent_contracts import DiagnosisReport, EvidencePack, ExpertArtifact, LearnerContextBrief, ReviewDecision


LlmJudge = Callable[..., dict[str, Any] | None]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)]


def _available_source_ids(evidence_pack: EvidencePack) -> set[str]:
    return {item.source_id for item in evidence_pack.items if item.source_id}


def _required_kp_ids(evidence_pack: EvidencePack, learner_context: LearnerContextBrief) -> set[str]:
    return set(evidence_pack.resolved_kp_ids or evidence_pack.kp_ids or learner_context.kp_ids)


def _artifact_kp_ids(artifact: ExpertArtifact) -> set[str]:
    content = artifact.content or {}
    return set(_string_list(content.get("kp_ids")) or artifact.kp_ids)


def _artifact_source_ids(artifact: ExpertArtifact) -> set[str]:
    return set(_string_list((artifact.content or {}).get("source_ids")))


def _claim_entries(artifact: ExpertArtifact) -> list[dict[str, Any]]:
    claims = []
    for item in (artifact.content or {}).get("claims", []):
        if isinstance(item, dict):
            text = _text(item.get("text"))
            evidence_ids = _string_list(item.get("evidence_ids"))
            if text:
                claims.append({"text": text, "evidence_ids": evidence_ids, "has_evidence_ids": "evidence_ids" in item})
        else:
            text = _text(item)
            if text:
                claims.append({"text": text, "evidence_ids": [], "has_evidence_ids": False})
    return claims


def _schema_conflicts(artifact: ExpertArtifact) -> list[str]:
    content = artifact.content or {}
    conflicts = []
    required_fields = ("schema_version", "source_ids", "kp_ids", "difficulty")
    for field in required_fields:
        if field not in content:
            conflicts.append(f"schema_invalid:missing_{field}")
    return conflicts


def _difficulty_score(actual: Any, expected: Any) -> float:
    if isinstance(actual, int) and isinstance(expected, int):
        return max(0.0, round(1.0 - 0.2 * abs(actual - expected), 4))
    return 1.0


def _expected_difficulty(learner_context: LearnerContextBrief, diagnosis_report: DiagnosisReport, actual: Any) -> int | None:
    learning_state = learner_context.learning_state or {}
    target = learning_state.get("target_difficulty")
    if isinstance(target, int):
        return target
    stage_name = _text(diagnosis_report.stage_name)
    if "难度不适" in stage_name and isinstance(actual, int):
        return max(1, actual - 1)
    return actual if isinstance(actual, int) else None


def _contains_copyright_risk(artifact: ExpertArtifact, diagnosis_report: DiagnosisReport) -> bool:
    flags = _string_list((artifact.content or {}).get("copyright_flags"))
    notes = [_text(note).lower() for note in [*artifact.risk_notes, *diagnosis_report.risk_notes]]
    return bool(flags) or any("copyright" in note or "版权" in note for note in notes)


def _safety_risk(artifact: ExpertArtifact, diagnosis_report: DiagnosisReport) -> str:
    notes = [_text(note).lower() for note in [*artifact.risk_notes, *diagnosis_report.risk_notes]]
    if any("medical_high_risk" in note for note in notes):
        return "high"
    return "low"


def _merge_notes(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for item in group:
            if item and item not in merged:
                merged.append(item)
    return merged


def _call_llm_judge(
    llm_judge: LlmJudge | None,
    *,
    artifact: ExpertArtifact,
    evidence_pack: EvidencePack,
    learner_context: LearnerContextBrief,
    diagnosis_report: DiagnosisReport,
    draft_review: dict[str, Any],
) -> tuple[float | None, str | None, list[dict[str, Any]]]:
    if not callable(llm_judge):
        return None, None, []

    result = llm_judge(
        artifact=artifact,
        evidence_pack=evidence_pack,
        learner_context=learner_context,
        diagnosis_report=diagnosis_report,
        draft_review=draft_review,
    )
    if not isinstance(result, dict):
        return None, None, [{"agent": "audit_llm_judge", "status": "ignored"}]
    confidence = result.get("confidence")
    reason = _text(result.get("reason")) or None
    trace = [{"agent": "audit_llm_judge", "status": "used", "reason": reason or "hook_returned"}]
    return confidence if isinstance(confidence, (int, float)) else None, reason, trace


def audit_artifact(
    *,
    artifact: ExpertArtifact,
    evidence_pack: EvidencePack,
    learner_context: LearnerContextBrief,
    diagnosis_report: DiagnosisReport,
    llm_judge: LlmJudge | None = None,
) -> ReviewDecision:
    available_source_ids = _available_source_ids(evidence_pack)
    claims = _claim_entries(artifact)
    supported_claims = 0
    conflicts: list[str] = []
    for claim in claims:
        evidence_ids = set(claim["evidence_ids"])
        if evidence_ids and evidence_ids.issubset(available_source_ids):
            supported_claims += 1
        elif not claim.get("has_evidence_ids") or not evidence_ids:
            conflicts.append(f"missing_evidence_ids:{claim['text']}")
        else:
            conflicts.append(f"unsupported_claim:{claim['text']}")

    fact_consistency = 1.0 if not claims else round(supported_claims / len(claims), 4)

    artifact_source_ids = _artifact_source_ids(artifact)
    evidence_coverage = 1.0
    if artifact_source_ids:
        evidence_coverage = round(len(artifact_source_ids & available_source_ids) / len(artifact_source_ids), 4)

    required_kp_ids = _required_kp_ids(evidence_pack, learner_context)
    artifact_kp_ids = _artifact_kp_ids(artifact)
    knowledge_coverage = 1.0
    if required_kp_ids:
        knowledge_coverage = round(len(artifact_kp_ids & required_kp_ids) / len(required_kp_ids), 4)
        if knowledge_coverage < 1.0:
            conflicts.append("knowledge_gap:artifact_kp_ids_do_not_cover_required_kps")

    conflicts.extend(_schema_conflicts(artifact))

    actual_difficulty = (artifact.content or {}).get("difficulty")
    expected_difficulty = _expected_difficulty(learner_context, diagnosis_report, actual_difficulty)
    difficulty_match = _difficulty_score(actual_difficulty, expected_difficulty)
    if difficulty_match < 0.7:
        conflicts.append(f"difficulty_mismatch:expected_{expected_difficulty}_actual_{actual_difficulty}")

    safety_risk = _safety_risk(artifact, diagnosis_report)
    if safety_risk == "high":
        conflicts.append("medical_high_risk:requires_human_review")

    risk_notes = _merge_notes(list(artifact.risk_notes), list(diagnosis_report.risk_notes))
    if _contains_copyright_risk(artifact, diagnosis_report):
        risk_notes = _merge_notes(risk_notes, ["copyright_warning:check_source_quotation_scope"])

    decision = "pass"
    if safety_risk == "high":
        decision = "human_review"
    elif any(item.startswith("unsupported_claim:") for item in conflicts):
        decision = "reject"
    elif any(item.startswith("missing_evidence_ids:") for item in conflicts):
        decision = "reject"
    elif any(item.startswith("schema_invalid:") for item in conflicts):
        decision = "reject"
    elif difficulty_match < 0.7:
        decision = "reject"
    elif knowledge_coverage < 1.0:
        decision = "revise"
    elif fact_consistency < 1.0:
        decision = "revise"

    reason = "规则校验通过"
    if decision != "pass":
        reason = conflicts[0] if conflicts else "规则校验未通过"
    elif any(note.startswith("copyright_warning:") for note in risk_notes):
        reason = "规则校验通过，但需关注版权来源风险"

    llm_confidence, llm_reason, llm_trace = _call_llm_judge(
        llm_judge,
        artifact=artifact,
        evidence_pack=evidence_pack,
        learner_context=learner_context,
        diagnosis_report=diagnosis_report,
        draft_review={
            "decision": decision,
            "reason": reason,
            "conflicts": conflicts,
            "risk_notes": risk_notes,
        },
    )
    confidence = llm_confidence if llm_confidence is not None else round((fact_consistency + evidence_coverage + difficulty_match + knowledge_coverage) / 4, 4)
    if llm_reason and decision == "pass":
        reason = llm_reason

    return ReviewDecision(
        decision=decision,
        reviewer="audit_agent",
        reason=reason,
        source_scope="audit_agent",
        source_id=artifact.source_id,
        kp_ids=list(artifact_kp_ids or required_kp_ids),
        risk_notes=risk_notes,
        confidence=confidence,
        fact_consistency=fact_consistency,
        evidence_coverage=evidence_coverage,
        difficulty_match=difficulty_match,
        knowledge_coverage=knowledge_coverage,
        safety_risk=safety_risk,
        conflicts=conflicts,
        agent_trace=[
            {"agent": "audit_agent", "action": "audit_artifact", "status": decision},
            *llm_trace,
        ],
    )


def review_document_ingestion(markdown: str, metadata: dict[str, Any]) -> dict[str, Any]:
    text = (markdown or "").strip()
    if not text:
        return {"decision": "reject", "reason": "文档提取结果为空", "risk_notes": ["empty_document"]}
    if "禁止入库" in text or "未授权" in text:
        return {"decision": "reject", "reason": "文档存在授权或版权风险", "risk_notes": ["copyright_risk"]}
    return {"decision": "pass", "reason": "文档可进入结构化抽取流程", "risk_notes": []}

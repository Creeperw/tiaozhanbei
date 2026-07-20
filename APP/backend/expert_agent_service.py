from __future__ import annotations

import hashlib
from typing import Any

from APP.backend.agent_contracts import DiagnosisReport, EvidencePack, ExpertArtifact, LearnerContextBrief
from APP.backend.cross_validation_service import validate_grading_artifact as cross_validate_grading_output
from APP.backend.cross_validation_service import validate_resource_artifact as cross_validate_output


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _source_ids(evidence_pack: EvidencePack) -> list[str]:
    return [item.source_id for item in evidence_pack.items]


def _kp_ids(evidence_pack: EvidencePack, learner_context: LearnerContextBrief) -> list[str]:
    return list(evidence_pack.resolved_kp_ids or evidence_pack.kp_ids or learner_context.kp_ids)


def _difficulty(value: Any, learner_context: LearnerContextBrief, fallback: int) -> int:
    if isinstance(value, int):
        return value
    learning_state = learner_context.learning_state or {}
    candidate = learning_state.get("target_difficulty", fallback)
    return candidate if isinstance(candidate, int) else fallback


def _duration(value: Any, fallback: int) -> int:
    return value if isinstance(value, int) and value > 0 else fallback


def _artifact_id(prefix: str, topic: str) -> str:
    digest = hashlib.sha1(topic.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"


def _topic(request: dict[str, Any] | None, fallback: str = "脾胃气虚证 + 四君子汤") -> str:
    return _text((request or {}).get("topic"), fallback)


def _diagnosis_summary(report: DiagnosisReport) -> str:
    return _text(report.summary, "当前需要补强证型与方剂匹配。")


def _common_content(
    *,
    learner_context: LearnerContextBrief,
    evidence_pack: EvidencePack,
    difficulty: int,
    expected_duration_min: int,
    diagnosis_report: DiagnosisReport,
) -> dict[str, Any]:
    return {
        "source_ids": _source_ids(evidence_pack),
        "kp_ids": _kp_ids(evidence_pack, learner_context),
        "difficulty": difficulty,
        "expected_duration_min": expected_duration_min,
        "remediation_suggestions": [
            "先用一句话复述四君子汤主治脾胃气虚证的证机。",
            "把四君子汤与理中丸做一组对比，重点区分气虚与虚寒。",
            _diagnosis_summary(diagnosis_report),
        ],
    }


def _build_memories(learner_context: LearnerContextBrief, diagnosis_report: DiagnosisReport) -> list[dict[str, Any]]:
    memories: list[dict[str, Any]] = []
    for title, payload in (
        ("short_term", learner_context.short_term_memory),
        ("long_term", learner_context.long_term_memory),
        ("planning", learner_context.planning_memory),
    ):
        if payload:
            memories.append({"category": title, "title": title, "content": _text(payload)})
    if diagnosis_report.summary:
        memories.append({"category": "diagnosis", "title": "diagnosis", "content": diagnosis_report.summary})
    return memories


def _grading_profile(learner_context: LearnerContextBrief, diagnosis_report: DiagnosisReport) -> dict[str, Any]:
    return {
        "constitution": learner_context.learner_group,
        "health_goals": learner_context.goal,
        "medical_history": _text(diagnosis_report.summary, "证型与方剂匹配不稳定"),
        "exercise_preferences": "知识卡和短练",
    }


def _content_claims(*texts: str, source_ids: list[str]) -> list[dict[str, Any]]:
    evidence_ids = list(source_ids)
    claims = []
    for text in texts:
        claim_text = _text(text)
        if claim_text:
            claims.append({"text": claim_text, "evidence_ids": evidence_ids})
    return claims


def _with_audit_shape(content: dict[str, Any], *, claim_texts: list[str]) -> dict[str, Any]:
    return {
        **content,
        "schema_version": "v1",
        "claims": _content_claims(*claim_texts, source_ids=list(content.get("source_ids", []))),
    }


FORMAL_RESOURCE_EVIDENCE_SCOPES = frozenset({"knowledge_point", "teaching_resource", "public"})


def _formal_resource_facts(evidence_pack: EvidencePack) -> list[tuple[str, str]]:
    formal_items = [
        item
        for item in evidence_pack.items
        if item.source_scope in FORMAL_RESOURCE_EVIDENCE_SCOPES
        and item.source_id
        and _text(item.summary)
    ]
    scopes_by_source_id: dict[str, set[str]] = {}
    for item in evidence_pack.items:
        if item.source_id:
            scopes_by_source_id.setdefault(item.source_id, set()).add(item.source_scope)
    ambiguous_source_ids = {
        source_id
        for source_id, scopes in scopes_by_source_id.items()
        if len(scopes) > 1
    }

    summaries_by_source_id: dict[str, list[str]] = {}
    for item in formal_items:
        if item.source_id in ambiguous_source_ids:
            continue
        summaries = summaries_by_source_id.setdefault(item.source_id, [])
        summary = _text(item.summary)
        if summary not in summaries:
            summaries.append(summary)
    return [
        (source_id, "\n".join(summaries))
        for source_id, summaries in summaries_by_source_id.items()
    ]


def _resource_claims(facts: list[tuple[str, str]]) -> list[dict[str, Any]]:
    if facts:
        return [{"text": summary, "evidence_ids": [source_id]} for source_id, summary in facts]
    return [{"text": "暂无可用正式证据。", "evidence_ids": []}]


def _resource_content(content: dict[str, Any], facts: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        **content,
        "source_ids": [source_id for source_id, _ in facts],
        "schema_version": "v1",
        "claims": _resource_claims(facts),
    }


def _attach_review(
    artifact: ExpertArtifact,
    *,
    learner_context: LearnerContextBrief,
    evidence_pack: EvidencePack,
    diagnosis_report: DiagnosisReport,
    validator: Any | None = None,
) -> ExpertArtifact:
    review_validator = validator or cross_validate_output
    review, summary = review_validator(
        artifact=artifact,
        evidence_pack=evidence_pack,
        learner_context=learner_context,
        diagnosis_report=diagnosis_report,
    )
    artifact.content = {
        **artifact.content,
        "review_decision": review.model_dump(),
        "review_summary": summary,
    }
    artifact.agent_trace = [
        *artifact.agent_trace,
        {"agent": "cross_validation_service", "action": "cross_validate_output", "status": review.decision},
    ]
    return artifact


def generate_handout(
    *,
    learner_context: LearnerContextBrief,
    evidence_pack: EvidencePack,
    diagnosis_report: DiagnosisReport,
    request: dict[str, Any],
) -> ExpertArtifact:
    topic = _topic(request)
    difficulty = _difficulty(request.get("difficulty"), learner_context, 3)
    expected_duration_min = _duration(request.get("expected_duration_min"), 18)
    content = _common_content(
        learner_context=learner_context,
        evidence_pack=evidence_pack,
        difficulty=difficulty,
        expected_duration_min=expected_duration_min,
        diagnosis_report=diagnosis_report,
    )
    facts = _formal_resource_facts(evidence_pack)
    content = _resource_content(content, facts)
    content["remediation_suggestions"] = ["以正式证据要点复习。"]
    content.update({
        "sections": [{
            "title": "正式证据要点",
            "bullets": [summary for _, summary in facts] or ["暂无可用正式证据。"],
        }],
        "diagnosis_focus": diagnosis_report.stage_name,
    })
    artifact = ExpertArtifact(
        artifact_type="handout",
        title=f"讲义：{topic}",
        content=content,
        source_scope="expert_handout",
        source_id=_artifact_id("handout", topic),
        kp_ids=content["kp_ids"],
        risk_notes=list(diagnosis_report.risk_notes),
        confidence=0.9,
        agent_trace=[{"agent": "expert_handout", "action": "generate_handout", "status": "success"}],
    )
    return _attach_review(
        artifact,
        learner_context=learner_context,
        evidence_pack=evidence_pack,
        diagnosis_report=diagnosis_report,
    )


def generate_knowledge_card(
    *,
    learner_context: LearnerContextBrief,
    evidence_pack: EvidencePack,
    diagnosis_report: DiagnosisReport,
    request: dict[str, Any],
) -> ExpertArtifact:
    topic = _topic(request)
    difficulty = _difficulty(request.get("difficulty"), learner_context, 2)
    expected_duration_min = _duration(request.get("expected_duration_min"), 8)
    content = _common_content(
        learner_context=learner_context,
        evidence_pack=evidence_pack,
        difficulty=difficulty,
        expected_duration_min=expected_duration_min,
        diagnosis_report=diagnosis_report,
    )
    facts = _formal_resource_facts(evidence_pack)
    content = _resource_content(content, facts)
    content["remediation_suggestions"] = ["以正式证据要点复习。"]
    content.update({
        "front": "正式证据要点是什么？",
        "back": "\n".join(summary for _, summary in facts) or "暂无可用正式证据。",
        "memory_anchor": "以正式证据复习。",
    })
    artifact = ExpertArtifact(
        artifact_type="knowledge_card",
        title=f"知识卡：{topic}",
        content=content,
        source_scope="expert_knowledge_card",
        source_id=_artifact_id("knowledge_card", topic),
        kp_ids=content["kp_ids"],
        risk_notes=list(diagnosis_report.risk_notes),
        confidence=0.92,
        agent_trace=[{"agent": "expert_knowledge_card", "action": "generate_knowledge_card", "status": "success"}],
    )
    return _attach_review(
        artifact,
        learner_context=learner_context,
        evidence_pack=evidence_pack,
        diagnosis_report=diagnosis_report,
    )


PAPER_TYPES = frozenset({"single_choice", "multiple_choice", "short_answer", "case_quiz"})


def _paper_blueprint(
    request: dict[str, Any],
    learner_context: LearnerContextBrief,
    difficulty: int,
    authoritative_kp_ids: list[str],
) -> dict[str, Any]:
    question_count = request.get("question_count", 3)
    if isinstance(question_count, bool) or not isinstance(question_count, int) or not 1 <= question_count <= 50:
        raise ValueError("question_count must be between 1 and 50")
    requested_kp_ids = request.get("kp_ids", list(authoritative_kp_ids))
    if not isinstance(requested_kp_ids, list):
        raise ValueError("kp_ids must be a nonempty list")
    kp_ids = [value.strip() for value in requested_kp_ids if isinstance(value, str)]
    if not kp_ids or len(kp_ids) != len(requested_kp_ids) or any(not value for value in kp_ids):
        raise ValueError("kp_ids must be nonempty")
    unresolved_kp_ids = set(kp_ids) - set(authoritative_kp_ids)
    if unresolved_kp_ids:
        raise ValueError(f"kp_ids must be resolved by evidence pack: {sorted(unresolved_kp_ids)}")
    types = request.get("types", ["single_choice", "short_answer", "case_quiz"])
    if not isinstance(types, list) or not types or any(not isinstance(value, str) or value not in PAPER_TYPES for value in types):
        raise ValueError("types must be controlled paper types")
    types = list(dict.fromkeys(types))
    distribution = request.get("distribution")
    if distribution is None:
        distribution = {question_type: 0 for question_type in types}
        for index in range(question_count):
            distribution[types[index % len(types)]] += 1
    if (
        not isinstance(distribution, dict)
        or set(distribution) != set(types)
        or any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in distribution.values())
        or sum(distribution.values()) != question_count
    ):
        raise ValueError("distribution must match types and question_count")
    return {
        "question_count": question_count,
        "kp_ids": list(dict.fromkeys(kp_ids)),
        "types": types,
        "distribution": distribution,
        "difficulty": difficulty,
        "exclusion_criteria": ["不生成试题正文或标准答案", "仅使用已解析知识点"],
    }


def generate_paper(
    *,
    learner_context: LearnerContextBrief,
    evidence_pack: EvidencePack,
    diagnosis_report: DiagnosisReport,
    request: dict[str, Any],
) -> ExpertArtifact:
    topic = _topic(request)
    difficulty = _difficulty(request.get("difficulty"), learner_context, 3)
    expected_duration_min = _duration(request.get("expected_duration_min"), 20)
    content = _common_content(
        learner_context=learner_context,
        evidence_pack=evidence_pack,
        difficulty=difficulty,
        expected_duration_min=expected_duration_min,
        diagnosis_report=diagnosis_report,
    )
    content.update({
        "paper_blueprint": _paper_blueprint(
            request,
            learner_context,
            difficulty,
            list(evidence_pack.resolved_kp_ids),
        ),
    })
    content = _with_audit_shape(content, claim_texts=[])
    artifact = ExpertArtifact(
        artifact_type="paper",
        title=f"练习卷：{topic}",
        content=content,
        source_scope="expert_paper",
        source_id=_artifact_id("paper", topic),
        kp_ids=content["kp_ids"],
        risk_notes=list(diagnosis_report.risk_notes),
        confidence=0.89,
        agent_trace=[{"agent": "expert_paper", "action": "generate_paper", "status": "success"}],
    )
    return _attach_review(
        artifact,
        learner_context=learner_context,
        evidence_pack=evidence_pack,
        diagnosis_report=diagnosis_report,
    )


def grade_submission(
    *,
    learner_context: LearnerContextBrief,
    evidence_pack: EvidencePack,
    diagnosis_report: DiagnosisReport,
    submission: dict[str, Any],
    profile: dict[str, Any] | None = None,
    memories: list[dict[str, Any]] | None = None,
) -> ExpertArtifact:
    from APP.backend import training_service

    grading_payload = training_service._grade_submission_payload(
        profile=profile or _grading_profile(learner_context, diagnosis_report),
        memories=memories or _build_memories(learner_context, diagnosis_report),
        submission=submission,
    )
    difficulty = _difficulty(submission.get("difficulty"), learner_context, 2)
    content = _common_content(
        learner_context=learner_context,
        evidence_pack=evidence_pack,
        difficulty=difficulty,
        expected_duration_min=12,
        diagnosis_report=diagnosis_report,
    )
    content.update(grading_payload)
    grading = content.get("grading", {})
    content = _with_audit_shape(content, claim_texts=[
        _text(grading.get("analysis")),
        _text(grading.get("standard_answer")),
        _text(content.get("remediation", {}).get("review_card", {}).get("content")),
    ])
    artifact = ExpertArtifact(
        artifact_type="grading",
        title=f"批改：{_text(submission.get('stem'), '练习题')[:24]}",
        content=content,
        source_scope="expert_grading",
        source_id=_text(submission.get("question_id"), "manual-question"),
        kp_ids=content["kp_ids"],
        risk_notes=list(diagnosis_report.risk_notes),
        confidence=0.91,
        agent_trace=[{"agent": "expert_grading", "action": "grade_submission", "status": "success"}],
    )
    return _attach_review(
        artifact,
        learner_context=learner_context,
        evidence_pack=evidence_pack,
        diagnosis_report=diagnosis_report,
        validator=cross_validate_grading_output,
    )


def generate_question_variation(
    *,
    learner_context: LearnerContextBrief,
    evidence_pack: EvidencePack,
    request: dict[str, Any],
) -> ExpertArtifact:
    mistake_id = request.get("mistake_id")
    source_version_id = _text(request.get("source_question_version_id"))
    source_question_id = _text(request.get("source_question_id"))
    source_stem = _text(request.get("source_stem"))
    requested_kp_ids = request.get("kp_ids")
    if not isinstance(mistake_id, int) or mistake_id <= 0 or not source_version_id or not source_stem:
        raise ValueError("owned mistake and source question are required")
    if not isinstance(requested_kp_ids, list) or not requested_kp_ids:
        raise ValueError("kp_ids are required")
    kp_ids = [value.strip() for value in requested_kp_ids if isinstance(value, str) and value.strip()]
    if len(kp_ids) != len(requested_kp_ids) or not set(kp_ids).issubset(set(evidence_pack.resolved_kp_ids)):
        raise ValueError("kp_ids must be resolved by evidence pack")
    stem = f"换一种学习情境：{source_stem}"
    content = _with_audit_shape({
        "stem": stem,
        "question_type": _text(request.get("source_question_type"), "single_choice"),
        "difficulty": _difficulty(request.get("source_difficulty"), learner_context, 2),
        "kp_ids": kp_ids,
        "source_ids": _source_ids(evidence_pack),
        "source_mistake_id": mistake_id,
        "source_question_id": source_question_id,
        "source_question_version_id": source_version_id,
    }, claim_texts=[stem])
    artifact = ExpertArtifact(
        artifact_type="question_variation",
        title="错题变式",
        content=content,
        source_scope="expert_question_variation",
        source_id=_artifact_id("question_variation", f"{mistake_id}:{source_version_id}:{stem}"),
        kp_ids=kp_ids,
        risk_notes=[],
        confidence=0.9,
        agent_trace=[{"agent": "expert_question_variation", "action": "generate_question_variation", "status": "success"}],
    )
    return artifact


def generate_case_training(
    *,
    learner_context: LearnerContextBrief,
    evidence_pack: EvidencePack,
    diagnosis_report: DiagnosisReport,
    request: dict[str, Any],
) -> ExpertArtifact:
    topic = _topic(request)
    difficulty = _difficulty(request.get("difficulty"), learner_context, 3)
    expected_duration_min = _duration(request.get("expected_duration_min"), 15)
    content = _common_content(
        learner_context=learner_context,
        evidence_pack=evidence_pack,
        difficulty=difficulty,
        expected_duration_min=expected_duration_min,
        diagnosis_report=diagnosis_report,
    )
    content.update({
        "case_summary": "患者久病后食少乏力，大便溏薄，面色萎黄，舌淡苔白，辨为脾胃气虚证。",
        "checkpoints": [
            "先指出支持脾胃气虚证的两个关键证据。",
            "再说明为何应选四君子汤而不是理中丸。",
            "补充一条随访时需要复查的学习性提示。",
        ],
        "reference_answer": "本案应辨为脾胃气虚证，治以益气健脾，方选四君子汤；若见畏寒肢冷、脘腹冷痛，则更偏向理中丸所治的中焦虚寒证。",
    })
    content = _with_audit_shape(content, claim_texts=[
        content["case_summary"],
        *content["checkpoints"],
        content["reference_answer"],
    ])
    artifact = ExpertArtifact(
        artifact_type="case_training",
        title=f"案例训练：{topic}",
        content=content,
        source_scope="expert_case_training",
        source_id=_artifact_id("case_training", topic),
        kp_ids=content["kp_ids"],
        risk_notes=list(diagnosis_report.risk_notes),
        confidence=0.9,
        agent_trace=[{"agent": "expert_case_training", "action": "generate_case_training", "status": "success"}],
    )
    return _attach_review(
        artifact,
        learner_context=learner_context,
        evidence_pack=evidence_pack,
        diagnosis_report=diagnosis_report,
    )

from __future__ import annotations

from typing import Any, Callable

from APP.backend.agent_contracts import AgentExecutionPlan, DiagnosisReport, EvidenceItem, EvidencePack, ExpertArtifact, LearnerContextBrief, ReviewDecision
from APP.backend.audit_agent_service import audit_artifact
from APP.backend.health_utils import safe_json_dumps


LlmJudge = Callable[..., dict[str, Any] | None]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _round(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)


def _merge_unique(items: list[str]) -> list[str]:
    merged: list[str] = []
    for item in items:
        if item and item not in merged:
            merged.append(item)
    return merged


def _knowledge_lens(artifact: ExpertArtifact, evidence_pack: EvidencePack) -> dict[str, Any]:
    required = set(evidence_pack.resolved_kp_ids or evidence_pack.kp_ids)
    actual = set(artifact.content.get("kp_ids", []) or artifact.kp_ids)
    score = 1.0 if not required else len(required & actual) / len(required)
    conflicts = []
    if score < 1.0:
        conflicts.append("knowledge_gap:artifact_kp_ids_do_not_cover_required_kps")
    return {"score": _round(score), "conflicts": conflicts}


def _diagnosis_lens(artifact: ExpertArtifact, learner_context: LearnerContextBrief, diagnosis_report: DiagnosisReport) -> dict[str, Any]:
    learning_state = learner_context.learning_state or {}
    actual_difficulty = artifact.content.get("difficulty")
    expected = learning_state.get("target_difficulty")
    if not isinstance(expected, int) and "难度不适" in _text(diagnosis_report.stage_name) and isinstance(actual_difficulty, int):
        expected = max(1, actual_difficulty - 1)
    if isinstance(expected, int) and isinstance(actual_difficulty, int):
        score = 1.0 - min(abs(expected - actual_difficulty) * 0.2, 1.0)
    else:
        score = 1.0
    conflicts = []
    if score < 0.7:
        conflicts.append(f"difficulty_mismatch:expected_{expected}_actual_{actual_difficulty}")
    return {"score": _round(score), "conflicts": conflicts, "expected_difficulty": expected}


def _self_check_lens(artifact: ExpertArtifact) -> dict[str, Any]:
    content = artifact.content or {}
    required_fields = ("schema_version", "source_ids", "kp_ids", "difficulty")
    missing = [field for field in required_fields if field not in content]
    conflicts = [f"schema_invalid:missing_{field}" for field in missing]
    return {"score": 1.0 if not conflicts else 0.0, "conflicts": conflicts}


def _audit_lens(
    artifact: ExpertArtifact,
    evidence_pack: EvidencePack,
    learner_context: LearnerContextBrief,
    diagnosis_report: DiagnosisReport,
    llm_judge: LlmJudge | None = None,
) -> ReviewDecision:
    return audit_artifact(
        artifact=artifact,
        evidence_pack=evidence_pack,
        learner_context=learner_context,
        diagnosis_report=diagnosis_report,
        llm_judge=llm_judge,
    )


def _overall_score(review: ReviewDecision) -> float:
    values = [
        review.fact_consistency or 0.0,
        review.evidence_coverage or 0.0,
        review.difficulty_match or 0.0,
        review.knowledge_coverage or 0.0,
    ]
    return _round(sum(values) / len(values))


def _summary_payload(
    review: ReviewDecision,
    *,
    knowledge_lens: dict[str, Any],
    diagnosis_lens: dict[str, Any],
    self_check_lens: dict[str, Any],
) -> dict[str, Any]:
    warnings = [note for note in review.risk_notes if "warning" in note]
    return {
        "decision": review.decision,
        "overall_score": _overall_score(review),
        "needs_human_review": review.decision == "human_review",
        "safety_risk": review.safety_risk or "low",
        "conflicts": list(review.conflicts),
        "warnings": warnings,
        "lenses": {
            "knowledge": knowledge_lens,
            "diagnosis": diagnosis_lens,
            "self_check": self_check_lens,
            "audit": {
                "score": _overall_score(review),
                "reviewer": review.reviewer,
            },
        },
    }


def persist_review_decision(
    *,
    db: Any,
    review: ReviewDecision,
    summary: dict[str, Any],
    user_id: int | None = None,
    session_id: str | None = None,
    event_type: str = "cross_validation",
) -> Any | None:
    if db is None or user_id is None:
        return None

    from APP.backend.database import AgentEvent

    payload = {"review": review.model_dump(), "summary": summary}
    event = AgentEvent(
        user_id=user_id,
        session_id=session_id,
        agent_name="cross_validation_service",
        event_type=event_type,
        input_summary=review.source_id or "",
        output_summary=f"cross_validation:{review.decision}",
        payload=safe_json_dumps(payload),
    )
    db.add(event)
    db.commit()
    return event


def _default_diagnosis_report(*, kp_ids: list[str], source_id: str, summary: str, stage_name: str = "稳定学习") -> DiagnosisReport:
    return DiagnosisReport(
        diagnosis_id=f"diag:{source_id}",
        stage_id="T0",
        stage_name=stage_name,
        summary=summary,
        source_scope="cross_validation_service",
        source_id=source_id,
        kp_ids=kp_ids,
        confidence=0.9,
    )


def _default_learner_context(*, kp_ids: list[str], source_id: str, goal: str, target_difficulty: int = 2) -> LearnerContextBrief:
    return LearnerContextBrief(
        learner_id="cross-validation-surface",
        learner_group="surface_adapter",
        goal=goal,
        source_scope="cross_validation_service",
        source_id=source_id,
        kp_ids=kp_ids,
        confidence=0.9,
        learning_state={"target_difficulty": target_difficulty},
    )


def _evidence_pack_from_source_ids(*, source_scope: str, source_id: str, kp_ids: list[str], source_ids: list[str], summary_prefix: str, summaries: dict[str, str] | None = None) -> EvidencePack:
    items = [
        EvidenceItem(
            source_scope=source_scope,
            source_id=item,
            summary=(summaries or {}).get(item, f"{summary_prefix}：{item}"),
            kp_ids=kp_ids,
            confidence=1.0,
        )
        for item in source_ids
    ]
    return EvidencePack(
        source_scope=source_scope,
        source_id=source_id,
        items=items,
        kp_ids=kp_ids,
        resolved_kp_ids=kp_ids,
        confidence=0.9,
    )


def validate_resource_artifact(
    *,
    artifact: ExpertArtifact,
    evidence_pack: EvidencePack,
    learner_context: LearnerContextBrief,
    diagnosis_report: DiagnosisReport,
    llm_judge: LlmJudge | None = None,
    db: Any | None = None,
    user_id: int | None = None,
    session_id: str | None = None,
) -> tuple[ReviewDecision, dict[str, Any]]:
    return cross_validate_output(
        artifact=artifact,
        evidence_pack=evidence_pack,
        learner_context=learner_context,
        diagnosis_report=diagnosis_report,
        llm_judge=llm_judge,
        db=db,
        user_id=user_id,
        session_id=session_id,
    )


def validate_grading_artifact(
    *,
    artifact: ExpertArtifact,
    evidence_pack: EvidencePack,
    learner_context: LearnerContextBrief,
    diagnosis_report: DiagnosisReport,
    llm_judge: LlmJudge | None = None,
    db: Any | None = None,
    user_id: int | None = None,
    session_id: str | None = None,
) -> tuple[ReviewDecision, dict[str, Any]]:
    return cross_validate_output(
        artifact=artifact,
        evidence_pack=evidence_pack,
        learner_context=learner_context,
        diagnosis_report=diagnosis_report,
        llm_judge=llm_judge,
        db=db,
        user_id=user_id,
        session_id=session_id,
    )


def validate_execution_plan(
    *,
    plan: AgentExecutionPlan,
    learner_context: LearnerContextBrief,
    llm_judge: LlmJudge | None = None,
    db: Any | None = None,
    user_id: int | None = None,
    session_id: str | None = None,
) -> tuple[ReviewDecision, dict[str, Any]]:
    kp_ids = list(plan.kp_ids)
    source_ids = [step.get("id") for step in plan.steps if isinstance(step, dict) and _text(step.get("id"))]
    artifact = ExpertArtifact(
        artifact_type="execution_plan",
        title=_text(plan.objective) or "Execution plan",
        content={
            "schema_version": "v1",
            "source_ids": source_ids,
            "kp_ids": kp_ids,
            "difficulty": 2,
            "claims": [{"text": _text(plan.objective) or "plan objective", "evidence_ids": source_ids[:1] or [plan.source_id or "plan"]}],
            "steps": list(plan.steps),
            "constraints": plan.constraints or {},
        },
        source_scope=plan.source_scope or "planner_agent",
        source_id=plan.source_id or "execution-plan",
        kp_ids=kp_ids,
        risk_notes=[f"risk_level:{plan.risk_level}"] if _text(plan.risk_level) else [],
        confidence=plan.confidence or 0.8,
    )
    evidence_pack = _evidence_pack_from_source_ids(
        source_scope="planner_agent",
        source_id=f"evidence:{plan.source_id or 'execution-plan'}",
        kp_ids=kp_ids,
        source_ids=source_ids[:1] or [plan.source_id or "plan"],
        summary_prefix="计划步骤证据",
    )
    diagnosis_report = _default_diagnosis_report(
        kp_ids=kp_ids,
        source_id=plan.source_id or "execution-plan",
        summary="规划输出需要经过交叉校验后再进入执行。",
    )
    return cross_validate_output(
        artifact=artifact,
        evidence_pack=evidence_pack,
        learner_context=learner_context,
        diagnosis_report=diagnosis_report,
        llm_judge=llm_judge,
        db=db,
        user_id=user_id,
        session_id=session_id,
    )


def validate_dynamic_question_selection(
    *,
    selection: dict[str, Any],
    target_kp_ids: list[str],
    llm_judge: LlmJudge | None = None,
    db: Any | None = None,
    user_id: int | None = None,
    session_id: str | None = None,
) -> tuple[ReviewDecision, dict[str, Any]]:
    questions = selection.get("questions", [])
    selected_ids = [_text(item.get("question_id")) for item in questions if isinstance(item, dict) and _text(item.get("question_id"))]
    actual_kp_ids = sorted({
        _text(kp_id)
        for item in questions if isinstance(item, dict)
        for kp_id in item.get("kp_ids", [])
        if _text(kp_id)
    })
    if selection.get("coverage_report", {}).get("covered_kp_ids"):
        actual_kp_ids = [
            _text(kp_id)
            for kp_id in selection.get("coverage_report", {}).get("covered_kp_ids", [])
            if _text(kp_id)
        ]
    artifact = ExpertArtifact(
        artifact_type="dynamic_question_selection",
        title="Dynamic question selection",
        content={
            "schema_version": "v1",
            "source_ids": selected_ids,
            "kp_ids": actual_kp_ids,
            "difficulty": 2,
            "claims": [
                {"text": _text(item.get("stem")) or _text(item.get("question_id")), "evidence_ids": [_text(item.get("question_id"))]}
                for item in questions if isinstance(item, dict)
            ],
            "questions": questions,
        },
        source_scope="deep_training_service",
        source_id="dynamic-question-selection",
        kp_ids=actual_kp_ids,
        confidence=0.85,
    )
    evidence_pack = _evidence_pack_from_source_ids(
        source_scope="deep_training_service",
        source_id="evidence:dynamic-question-selection",
        kp_ids=list(target_kp_ids),
        source_ids=selected_ids,
        summary_prefix="动态插题证据",
    )
    learner_context = _default_learner_context(
        kp_ids=list(target_kp_ids),
        source_id="dynamic-question-selection",
        goal="动态插题结果校验",
    )
    diagnosis_report = _default_diagnosis_report(
        kp_ids=list(target_kp_ids),
        source_id="dynamic-question-selection",
        summary="动态插题结果需要满足目标知识点覆盖。",
    )
    return cross_validate_output(
        artifact=artifact,
        evidence_pack=evidence_pack,
        learner_context=learner_context,
        diagnosis_report=diagnosis_report,
        llm_judge=llm_judge,
        db=db,
        user_id=user_id,
        session_id=session_id,
    )


def validate_visual_parse_result(
    *,
    result: Any,
    task_hint: str,
    llm_judge: LlmJudge | None = None,
    db: Any | None = None,
    user_id: int | None = None,
    session_id: str | None = None,
) -> tuple[ReviewDecision, dict[str, Any]]:
    source_id = f"visual:{task_hint}"
    claim_text = _text(getattr(result, "question", "")) or _text(getattr(result, "image_type", "")) or task_hint
    visual_observations = [
        _text(item)
        for item in getattr(result, "visual_observations", []) or []
        if _text(item)
    ]
    metadata = getattr(result, "raw_model_metadata", {}) or {}
    evidence_anchors = [
        _text(item)
        for item in metadata.get("evidence_spans", []) or []
        if _text(item)
    ]
    if not evidence_anchors:
        evidence_anchors = [
            _text(item)
            for item in metadata.get("ocr_spans", []) or []
            if _text(item)
        ]
    if not evidence_anchors:
        evidence_anchors = [
            _text(item)
            for item in metadata.get("source_boxes", []) or []
            if _text(item)
        ]
    anchor_ids = [f"visual-anchor:{index + 1}" for index, _item in enumerate(evidence_anchors)]
    risk_notes = []
    conflicts = []
    if not anchor_ids:
        risk_notes.append("visual_unanchored:requires_human_review")
        conflicts.append("visual_unanchored:no_independent_evidence_anchor")
    if any(term in claim_text for term in ("诊断", "处方", "治疗", "急诊", "胸痛")):
        risk_notes.append("medical_high_risk:visual_parse_claim")
    artifact = ExpertArtifact(
        artifact_type="visual_parse",
        title=f"Visual parse: {task_hint}",
        content={
            "schema_version": "v1",
            "source_ids": anchor_ids,
            "kp_ids": [],
            "difficulty": 2,
            "claims": [{"text": claim_text, "evidence_ids": anchor_ids[:1]}] if anchor_ids else [{"text": claim_text, "evidence_ids": []}],
            "visual_observations": visual_observations,
        },
        source_scope="vision_parse_service",
        source_id=source_id,
        kp_ids=[],
        risk_notes=risk_notes,
        confidence=float(getattr(result, "confidence", 0.0) or 0.0),
    )
    evidence_pack = _evidence_pack_from_source_ids(
        source_scope="vision_parse_service",
        source_id=f"evidence:{source_id}",
        kp_ids=[],
        source_ids=anchor_ids,
        summary_prefix="视觉解析证据",
        summaries={anchor_ids[index]: evidence_anchors[index] for index in range(len(anchor_ids))},
    )
    learner_context = _default_learner_context(kp_ids=[], source_id=source_id, goal="视觉解析结果校验")
    diagnosis_report = _default_diagnosis_report(kp_ids=[], source_id=source_id, summary="视觉解析结果需要保持教学边界。")
    review, summary = cross_validate_output(
        artifact=artifact,
        evidence_pack=evidence_pack,
        learner_context=learner_context,
        diagnosis_report=diagnosis_report,
        llm_judge=llm_judge,
        db=None,
        user_id=None,
        session_id=None,
    )
    if conflicts:
        review.conflicts = _merge_unique([*review.conflicts, *conflicts])
        summary["conflicts"] = list(review.conflicts)
    if conflicts:
        review.decision = "human_review"
        summary["decision"] = review.decision
        summary["needs_human_review"] = True
    _persist_final_review(
        review=review,
        summary=summary,
        db=db,
        user_id=user_id,
        session_id=session_id,
    )
    return review, summary


def _build_review(
    *,
    artifact: ExpertArtifact,
    evidence_pack: EvidencePack,
    learner_context: LearnerContextBrief,
    diagnosis_report: DiagnosisReport,
    llm_judge: LlmJudge | None = None,
    db: Any | None = None,
    user_id: int | None = None,
    session_id: str | None = None,
) -> tuple[ReviewDecision, dict[str, Any]]:
    knowledge_lens = _knowledge_lens(artifact, evidence_pack)
    diagnosis_lens = _diagnosis_lens(artifact, learner_context, diagnosis_report)
    self_check_lens = _self_check_lens(artifact)
    audit_review = _audit_lens(
        artifact,
        evidence_pack,
        learner_context,
        diagnosis_report,
        llm_judge=llm_judge,
    )

    combined_conflicts = _merge_unique([
        *knowledge_lens["conflicts"],
        *diagnosis_lens["conflicts"],
        *self_check_lens["conflicts"],
        *audit_review.conflicts,
    ])
    risk_notes = _merge_unique(list(audit_review.risk_notes))

    review = ReviewDecision(
        decision=audit_review.decision,
        reviewer="cross_validation_service",
        reason=audit_review.reason,
        source_scope="cross_validation",
        source_id=artifact.source_id,
        kp_ids=list(artifact.kp_ids),
        risk_notes=risk_notes,
        confidence=audit_review.confidence,
        fact_consistency=audit_review.fact_consistency,
        evidence_coverage=audit_review.evidence_coverage,
        difficulty_match=min(audit_review.difficulty_match or 0.0, diagnosis_lens["score"]),
        knowledge_coverage=min(audit_review.knowledge_coverage or 0.0, knowledge_lens["score"]),
        safety_risk=audit_review.safety_risk,
        conflicts=combined_conflicts,
        agent_trace=[
            {"agent": "knowledge_lens", "status": "evaluated", "score": knowledge_lens["score"]},
            {"agent": "diagnosis_lens", "status": "evaluated", "score": diagnosis_lens["score"]},
            {"agent": "expert_self_check", "status": "evaluated", "score": self_check_lens["score"]},
            *audit_review.agent_trace,
        ],
    )
    return review, _summary_payload(
        review,
        knowledge_lens=knowledge_lens,
        diagnosis_lens=diagnosis_lens,
        self_check_lens=self_check_lens,
    )


def _persist_final_review(
    *,
    review: ReviewDecision,
    summary: dict[str, Any],
    db: Any | None,
    user_id: int | None,
    session_id: str | None,
) -> None:
    persist_review_decision(
        db=db,
        review=review,
        summary=summary,
        user_id=user_id,
        session_id=session_id,
    )


def cross_validate_output(
    *,
    artifact: ExpertArtifact,
    evidence_pack: EvidencePack,
    learner_context: LearnerContextBrief,
    diagnosis_report: DiagnosisReport,
    llm_judge: LlmJudge | None = None,
    db: Any | None = None,
    user_id: int | None = None,
    session_id: str | None = None,
) -> tuple[ReviewDecision, dict[str, Any]]:
    review, summary = _build_review(
        artifact=artifact,
        evidence_pack=evidence_pack,
        learner_context=learner_context,
        diagnosis_report=diagnosis_report,
        llm_judge=llm_judge,
    )
    _persist_final_review(
        review=review,
        summary=summary,
        db=db,
        user_id=user_id,
        session_id=session_id,
    )
    return review, summary

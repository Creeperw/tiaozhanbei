from __future__ import annotations

import hashlib
from typing import Any

from APP.backend.agent_contracts import DiagnosisReport, EvidenceItem, EvidencePack, ExpertArtifact, LearnerContextBrief
from APP.backend.cross_validation_service import cross_validate_output as cross_validate_artifact_output
from APP.backend.cross_validation_service import validate_dynamic_question_selection


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _contains_any(text: str, terms: list[str]) -> bool:
    normalized = text.lower()
    return any(term and term.lower() in normalized for term in terms)


def _candidate_id(text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8].upper()
    return f"CAND_KP_{digest}"


def align_knowledge_points(*, text: str, knowledge_points: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = []
    matched_names = []
    for item in knowledge_points:
        terms = [_text(item.get("name")), *[_text(alias) for alias in item.get("aliases", [])]]
        if _contains_any(text, terms):
            kp_id = _text(item.get("kp_id"))
            if kp_id and kp_id not in resolved:
                resolved.append(kp_id)
                matched_names.append(_text(item.get("name")))

    if resolved:
        return {
            "resolved_kp_ids": resolved,
            "candidate_kp_ids": [],
            "label_status": "matched",
            "evidence": [f"命中知识点：{name}" for name in matched_names],
        }

    candidate = _candidate_id(text)
    return {
        "resolved_kp_ids": [],
        "candidate_kp_ids": [candidate],
        "label_status": "pending_review",
        "evidence": ["未命中正式知识点，进入候选知识点审核流程"],
    }


def _mistake_kp_ids(mistakes: list[dict[str, Any]]) -> set[str]:
    result = set()
    for item in mistakes:
        result.update(_text(kp_id) for kp_id in item.get("kp_ids", []) if _text(kp_id))
    return result


def select_practice_questions(
    *,
    target_kp_ids: list[str],
    mistakes: list[dict[str, Any]],
    question_bank: list[dict[str, Any]],
    limit: int = 5,
    db: Any | None = None,
    user_id: int | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    targets = set(target_kp_ids)
    mistake_targets = _mistake_kp_ids(mistakes)

    def score(item: dict[str, Any]) -> float:
        kp_ids = set(item.get("kp_ids", []))
        weak_match = 1.0 if kp_ids & mistake_targets else 0.0
        target_match = 1.0 if kp_ids & targets else 0.0
        quality = float(item.get("quality_score", 0.7))
        difficulty = float(item.get("difficulty", 2))
        difficulty_match = 1.0 - min(abs(difficulty - 2.5), 2.5) / 2.5
        return 0.35 * weak_match + 0.30 * target_match + 0.20 * quality + 0.15 * difficulty_match

    selected = sorted(question_bank, key=score, reverse=True)[:limit]
    covered = set()
    for item in selected:
        covered.update(item.get("kp_ids", []))
    coverage = len(covered & targets) / len(targets) if targets else 1.0

    selection = {
        "questions": selected,
        "coverage_report": {
            "target_kp_ids": target_kp_ids,
            "covered_kp_ids": sorted(covered & targets),
            "target_coverage": round(coverage, 4),
        },
        "selection_policy": "0.35*薄弱点 + 0.30*目标知识点 + 0.20*题目质量 + 0.15*难度匹配",
    }
    review, summary = validate_dynamic_question_selection(
        selection=selection,
        target_kp_ids=target_kp_ids,
        db=db,
        user_id=user_id,
        session_id=session_id,
    )
    selection["review_decision"] = review.model_dump()
    selection["review_summary"] = summary
    return selection


def generate_mistake_variant(mistake: dict[str, Any], question: dict[str, Any]) -> dict[str, Any]:
    source_stem = _text(question.get("stem")) or "原题"
    kp_ids = question.get("kp_ids") or mistake.get("kp_ids", [])
    return {
        "question_id": f"VAR_{_text(question.get('question_id')) or _candidate_id(source_stem)}",
        "source_question_id": _text(question.get("question_id")),
        "status": "draft",
        "stem": f"变式：如果换一个案例表述，仍围绕“{source_stem}”考查同一知识点，应如何判断？",
        "kp_ids": kp_ids,
        "error_type": _text(mistake.get("error_type")) or "待复盘错因",
        "review_required": True,
    }


def diagnose_learning_state(
    *,
    l0_baseline: dict[str, Any],
    l3_behavior: dict[str, Any],
    mistakes: list[dict[str, Any]],
) -> dict[str, Any]:
    completion = float(l3_behavior.get("task_completion_rate", 1.0))
    login_change = float(l3_behavior.get("login_weekly_change", 0.0))
    focus_change = float(l3_behavior.get("focus_time_change", 0.0))
    retry_count = int(l3_behavior.get("retry_count", 0))
    has_mistake_pressure = bool(mistakes)

    if login_change <= -0.35 and focus_change <= -0.35 and completion < 0.55:
        stage_id = "T2"
        stage_name = "行为怠惰"
        attribution = "节奏下降"
        action = "reduce_daily_tasks_and_send_popup"
    elif retry_count >= 3 and completion < 0.7:
        stage_id = "T1"
        stage_name = "高耗低效"
        attribution = "难度不适"
        action = "switch_to_micro_lesson_and_comparison_card"
    elif has_mistake_pressure and completion < 0.75:
        stage_id = "T5"
        stage_name = "难度不适"
        attribution = "复盘缺失"
        action = "generate_mistake_review_card"
    else:
        stage_id = "T4" if float(l3_behavior.get("path_deviation", 0.0)) > 0.4 else "T0"
        stage_name = "路径偏离" if stage_id == "T4" else "稳定学习"
        attribution = "路径偏离" if stage_id == "T4" else "暂无明显风险"
        action = "return_to_main_path" if stage_id == "T4" else "keep_current_plan"

    evidence = [
        f"任务完成率 {completion:.0%}",
        f"登录频率变化 {login_change:.0%}",
        f"专注时长变化 {focus_change:.0%}",
        f"错题压力 {len(mistakes)} 条",
    ]
    return {
        "t_stage": {
            "stage_id": stage_id,
            "stage_name": stage_name,
            "severity": "medium" if stage_id != "T0" else "low",
            "evidence": evidence,
            "suggested_action": action,
        },
        "l0_baseline": l0_baseline,
        "l3_window": l3_behavior,
        "attribution": {"primary": attribution, "evidence_count": len(evidence)},
    }


def create_intervention(diagnosis: dict[str, Any]) -> dict[str, Any]:
    stage = diagnosis["t_stage"]
    action = stage.get("suggested_action", "keep_current_plan")
    stage_name = stage.get("stage_name", "当前阶段")
    return {
        "intervention_id": f"INT_{stage.get('stage_id', 'NA')}",
        "route": ["notification_service", "learning_plan_service"],
        "action": action,
        "explainable_message": f"为什么收到这条建议：系统判断你当前处于“{stage_name}”，依据包括：{'；'.join(stage.get('evidence', [])[:3])}。",
        "cooldown_hours": 24,
        "effect_status": "pending",
    }


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


def _cross_validation_contracts(generated: dict[str, Any], evidence: dict[str, Any]) -> tuple[ExpertArtifact, EvidencePack, LearnerContextBrief, DiagnosisReport]:
    source_ids = [item for item in generated.get("source_ids", evidence.get("source_ids", [])) if isinstance(item, str) and item.strip()]
    kp_ids = [item for item in generated.get("kp_ids", evidence.get("required_kp_ids", [])) if isinstance(item, str) and item.strip()]
    difficulty = generated.get("difficulty") if isinstance(generated.get("difficulty"), int) else evidence.get("expected_difficulty", 2)
    claims = []
    supported_claims = set(evidence.get("supported_claims", []))
    for claim in generated.get("claims", []):
        if isinstance(claim, str):
            evidence_ids = source_ids if claim in supported_claims else ["UNSUPPORTED_CLAIM"]
            claims.append({"text": claim, "evidence_ids": evidence_ids})
        elif isinstance(claim, dict):
            claims.append({
                "text": _text(claim.get("text")),
                "evidence_ids": [item for item in claim.get("evidence_ids", []) if isinstance(item, str) and item.strip()],
            })

    artifact = ExpertArtifact(
        artifact_type="cross_validation_payload",
        title="Cross validation payload",
        content={
            "schema_version": "v1",
            "source_ids": source_ids,
            "kp_ids": kp_ids,
            "difficulty": difficulty,
            "claims": claims,
        },
        source_scope="deep_training_service",
        source_id=_text(generated.get("source_id")) or "cross-validation-generated",
        kp_ids=kp_ids,
        risk_notes=[_text(generated.get("safety_risk"))] if _text(generated.get("safety_risk")) not in ("", "low", "normal") else [],
        confidence=0.9,
    )
    evidence_items = [
        EvidenceItem(
            source_scope="deep_training_service",
            source_id=source_id,
            summary=f"支持性证据：{source_id}",
            kp_ids=kp_ids,
            confidence=1.0,
        )
        for source_id in source_ids
    ]
    evidence_pack = EvidencePack(
        source_scope="deep_training_service",
        source_id=_text(evidence.get("source_id")) or "cross-validation-evidence",
        items=evidence_items,
        kp_ids=kp_ids,
        resolved_kp_ids=list(evidence.get("required_kp_ids", kp_ids)),
        confidence=0.9,
    )
    learner_context = LearnerContextBrief(
        learner_id="cross-validation-learner",
        learner_group="deep_training_service",
        goal="cross_validate_output",
        source_scope="deep_training_service",
        source_id="cross-validation-context",
        kp_ids=kp_ids,
        confidence=0.9,
        learning_state={"target_difficulty": evidence.get("expected_difficulty", difficulty)},
    )
    diagnosis_report = DiagnosisReport(
        diagnosis_id="cross-validation-diagnosis",
        stage_id="T0",
        stage_name="稳定学习",
        summary="用于兼容 deep_training_service.cross_validate_output 的最小诊断上下文。",
        source_scope="deep_training_service",
        source_id="cross-validation-diagnosis",
        kp_ids=kp_ids,
        risk_notes=[_text(generated.get("safety_risk"))] if _text(generated.get("safety_risk")) not in ("", "low", "normal") else [],
        confidence=0.9,
    )
    return artifact, evidence_pack, learner_context, diagnosis_report


def cross_validate_output(*, generated: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    artifact, evidence_pack, learner_context, diagnosis_report = _cross_validation_contracts(generated, evidence)
    review, _summary = cross_validate_artifact_output(
        artifact=artifact,
        evidence_pack=evidence_pack,
        learner_context=learner_context,
        diagnosis_report=diagnosis_report,
    )
    return review.model_dump()


def compute_evaluation_metrics(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    if not reviews:
        return {
            "hallucination_rate": 0.0,
            "difficulty_match_rate": 0.0,
            "knowledge_coverage_rate": 0.0,
            "pass_rate": 0.0,
        }
    hallucination_rate = 1 - sum(item.get("fact_consistency", 0.0) for item in reviews) / len(reviews)
    difficulty_match_rate = sum(item.get("difficulty_match", 0.0) for item in reviews) / len(reviews)
    knowledge_coverage_rate = sum(item.get("knowledge_coverage", 0.0) for item in reviews) / len(reviews)
    pass_rate = sum(1 for item in reviews if item.get("decision") == "pass") / len(reviews)
    return {
        "hallucination_rate": round(hallucination_rate, 4),
        "difficulty_match_rate": round(difficulty_match_rate, 4),
        "knowledge_coverage_rate": round(knowledge_coverage_rate, 4),
        "pass_rate": round(pass_rate, 4),
    }

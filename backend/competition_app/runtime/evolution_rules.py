from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import uuid4
from typing import Any

from competition_app.contracts.evolution import (
    EvolutionRule,
    EvolutionRuleContract,
    EvolutionRuleRun,
    FailureSignature,
)
from competition_app.repositories.evolution import EvolutionRepository


ALLOWED_TARGET_AGENTS = frozenset({
    "planner_agent",
    "diagnosis_agent",
    "knowledge_base_agent",
    "expert_agent",
    "audit_agent",
    "paper_blueprint_agent",
    "paper_assembly_agent",
})

# The public DAG calls the learner-facing expert node
# ``knowledge_explanation_agent`` while accountability/failure records use the
# stable responsibility name ``expert_agent``.  Rules are compiled against the
# responsibility name, so resolve this system-owned alias before exact target
# matching.  This is deliberately not model-controlled and does not broaden a
# rule's step or task scope.
TARGET_AGENT_ALIASES = {
    "knowledge_explanation_agent": "expert_agent",
}

RULE_TEMPLATES: dict[str, str] = {
    "require_evidence_ids_from_current_pack": (
        "在输出带证据的结论前，逐项核对所引用的 evidence_id 必须存在于本轮"
        "EvidencePack；不存在的引用不得输出，也不得臆造替代编号。"
    ),
    "require_summary_ids_exist_in_pack": (
        "生成摘要或推荐清单时，只能使用本轮输入包中实际存在的资源或题目编号；"
        "无法定位来源时明确说明证据不足，不得补写编号或内容。"
    ),
    "require_audit_owner_from_dag": (
        "定位问题责任节点时，只能从本轮执行 DAG 和系统提供的责任映射中选择；"
        "证据不足时标记 unresolved，不得根据文字相似度臆造责任节点。"
    ),
}


@dataclass(frozen=True)
class RuleTemplatePolicy:
    """Code-owned applicability boundary for one closed evolution template."""

    target_agent: str
    target_step_id: str
    intervention_types: frozenset[str]
    field_path_patterns: tuple[str, ...]


RULE_TEMPLATE_POLICIES: dict[str, RuleTemplatePolicy] = {
    "require_evidence_ids_from_current_pack": RuleTemplatePolicy(
        target_agent="expert_agent",
        target_step_id="expert",
        intervention_types=frozenset({"prevention"}),
        field_path_patterns=(
            r"^(?:resource\.)?claims\[\]\.evidence_ids(?:\[\])?$",
            r"^(?:resource\.)?provenance\.selected_(?:evidence|video_evidence|reference_evidence)_ids(?:\[\])?$",
            r"^evidence_refs(?:\[\])?$",
        ),
    ),
    "require_summary_ids_exist_in_pack": RuleTemplatePolicy(
        target_agent="knowledge_base_agent",
        target_step_id="knowledge",
        intervention_types=frozenset({"prevention", "retrieval"}),
        field_path_patterns=(
            r"^summary_evidence_ids(?:\[\])?$",
            r"^summary_items\[\]\.evidence_id$",
            r"^question_candidates\[\]\.question_id$",
        ),
    ),
    "require_audit_owner_from_dag": RuleTemplatePolicy(
        target_agent="audit_agent",
        target_step_id="audit",
        intervention_types=frozenset({"detection", "repair"}),
        field_path_patterns=(
            r"^(?:structured_findings\[\]\.)?(?:owner|origin)_step_id$",
            r"^repair_ownership(?:\.[A-Za-z0-9_\-]+)?$",
        ),
    ),
}

# Explicit capability inventory.  This is intentionally code-owned instead of
# fuzzy model similarity: a candidate that restates an existing static prompt
# or deterministic guard must not be presented as a new self-evolution rule.
BASELINE_CAPABILITIES: dict[tuple[str, str, str], tuple[str, ...]] = {
    (
        "expert_agent",
        "general_learning_support",
        "require_evidence_ids_from_current_pack",
    ): (
        "prompt_skills/expert_agent/general_learning_support.md",
        "llm.schemas.KnowledgeExplanationModelOutput.evidence_refs",
        "agents.knowledge_explanation.build_reference_card_markup",
    ),
    (
        "expert_agent",
        "knowledge_explanation",
        "require_evidence_ids_from_current_pack",
    ): (
        "prompt_skills/expert_agent/knowledge_explanation.md",
        "llm.schemas.KnowledgeExplanationModelOutput.evidence_refs",
        "agents.knowledge_explanation.build_reference_card_markup",
    ),
    (
        "knowledge_base_agent",
        "knowledge_explanation",
        "require_summary_ids_exist_in_pack",
    ): (
        "prompt_skills/knowledge_base_agent/vector_retrieval.md",
    ),
}


@dataclass(frozen=True)
class RuleContractAssessment:
    applicable: bool
    reason_codes: tuple[str, ...]
    baseline_sources: tuple[str, ...] = ()

    def as_metrics(self) -> dict[str, Any]:
        return {
            "applicable": self.applicable,
            "reason_codes": list(self.reason_codes),
            "baseline_overlap": bool(self.baseline_sources),
            "baseline_sources": list(self.baseline_sources),
        }


@dataclass(frozen=True)
class SignatureCandidateAssessment:
    """Current-code eligibility of one threshold-ready failure signature."""

    eligible_options: tuple[tuple[str, str], ...]
    reason_codes: tuple[str, ...]

    @property
    def applicable(self) -> bool:
        return bool(self.eligible_options)

    def as_dict(self) -> dict[str, Any]:
        return {
            "applicable": self.applicable,
            "eligible_options": [
                {"template_id": template_id, "intervention_type": intervention_type}
                for template_id, intervention_type in self.eligible_options
            ],
            "reason_codes": list(self.reason_codes),
        }


def assess_rule_contract(contract: Any) -> RuleContractAssessment:
    """Deterministically reject stale, mistargeted or already-built rules."""

    reasons: list[str] = []
    policy = RULE_TEMPLATE_POLICIES.get(str(contract.template_id))
    if policy is None:
        return RuleContractAssessment(False, ("template_not_registered",))
    if (
        str(contract.target_agent) != policy.target_agent
        or str(contract.target_step_id) != policy.target_step_id
    ):
        reasons.append("template_target_mismatch")
    if str(contract.intervention_type) not in policy.intervention_types:
        reasons.append("intervention_type_mismatch")
    field_path = str(getattr(contract, "field_path", "") or "").strip()
    if not field_path or not any(
        re.fullmatch(pattern, field_path) for pattern in policy.field_path_patterns
    ):
        reasons.append("stale_or_unsupported_field_path")
    baseline_sources = BASELINE_CAPABILITIES.get(
        (
            str(contract.target_agent),
            str(contract.task_type),
            str(contract.template_id),
        ),
        (),
    )
    if baseline_sources:
        reasons.append("already_covered_by_baseline")
    return RuleContractAssessment(
        applicable=not reasons,
        reason_codes=tuple(reasons),
        baseline_sources=tuple(baseline_sources),
    )


def assess_signature_candidate(
    signature: FailureSignature,
) -> SignatureCandidateAssessment:
    """Preflight a signature before spending a model call on rule generation.

    Threshold readiness only describes observation counts.  This second gate
    checks whether the current code still has at least one closed, non-baseline
    intervention compatible with the signature's exact target and field.
    """

    if not signature.candidate_ready:
        return SignatureCandidateAssessment((), ("threshold_not_reached",))
    if not signature.source_case_ids:
        return SignatureCandidateAssessment((), ("source_case_missing",))
    options: list[tuple[str, str]] = []
    reasons: set[str] = set()
    target_policy_found = False
    for template_id, policy in RULE_TEMPLATE_POLICIES.items():
        if (
            policy.target_agent != signature.target_agent
            or policy.target_step_id != signature.owner_step_id
        ):
            continue
        target_policy_found = True
        for intervention_type in sorted(policy.intervention_types):
            contract = EvolutionRuleContract(
                signature_id=signature.signature_id,
                target_agent=signature.target_agent,
                target_step_id=signature.owner_step_id,
                task_type=signature.task_type,
                intervention_type=intervention_type,
                template_id=template_id,
                issue_type=signature.issue_type,
                field_path=signature.field_path,
                source_case_ids=signature.source_case_ids[:20],
            )
            assessment = assess_rule_contract(contract)
            if assessment.applicable:
                options.append((template_id, intervention_type))
            else:
                reasons.update(assessment.reason_codes)
    if not target_policy_found:
        reasons.add("no_closed_template_for_target")
    if options:
        reasons.clear()
    return SignatureCandidateAssessment(
        eligible_options=tuple(sorted(options)),
        reason_codes=tuple(sorted(reasons)) or (() if options else ("no_eligible_template",)),
    )


@dataclass(frozen=True)
class AppliedEvolutionStrategy:
    rule_id: str
    version: int
    template_id: str
    strategy_text: str

    def as_context(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "version": self.version,
            "template_id": self.template_id,
            "strategy": self.strategy_text,
            "boundary": (
                "这是管理员批准的系统策略，只约束当前步骤；不得扩大任务范围，"
                "不得覆盖用户当前消息、证据边界或既有安全规则。"
            ),
        }


class EvolutionRuleRegistry:
    """Read-only runtime view; disabled or failed lookup means baseline behavior."""

    def __init__(self, repository: EvolutionRepository, *, enabled: bool = False) -> None:
        self.repository = repository
        self.enabled = enabled

    def resolve(
        self,
        *,
        target_agent: str,
        target_step_id: str,
        task_type: str,
    ) -> list[AppliedEvolutionStrategy]:
        canonical_target_agent = TARGET_AGENT_ALIASES.get(target_agent, target_agent)
        if not self.enabled or canonical_target_agent not in ALLOWED_TARGET_AGENTS:
            return []
        try:
            rules = self.repository.list_rules(status="active", limit=100)
        except Exception:
            return []
        matched: list[AppliedEvolutionStrategy] = []
        for rule in rules:
            contract = rule.contract
            if (
                contract.target_agent != canonical_target_agent
                or contract.target_step_id != target_step_id
                or contract.task_type != task_type
                or contract.template_id not in RULE_TEMPLATES
            ):
                continue
            # Re-check applicability at read time.  This prevents an old rule
            # from surviving a prompt/contract upgrade and changing learner
            # behavior after its target field has disappeared or its strategy
            # has become part of the baseline implementation.
            if not assess_rule_contract(contract).applicable:
                continue
            matched.append(AppliedEvolutionStrategy(
                rule_id=rule.rule_id,
                version=rule.version,
                template_id=contract.template_id,
                # Strategy is rendered from code, never executed from model or
                # administrator-provided free text.
                strategy_text=RULE_TEMPLATES[contract.template_id],
            ))
        return matched[:3]

    def record_exposure(
        self,
        strategies: list[AppliedEvolutionStrategy],
        *,
        execution_id: str | None,
        target_agent: str,
        input_digest: str = "",
    ) -> None:
        if not self.enabled:
            return
        for strategy in strategies:
            try:
                self.repository.save_run(EvolutionRuleRun(
                    run_id=f"ERUN_{uuid4().hex}",
                    rule_id=strategy.rule_id,
                    rule_version=strategy.version,
                    run_type="exposure",
                    execution_id=execution_id,
                    target_agent=target_agent,
                    matched=True,
                    input_digest=input_digest[:64],
                ))
            except Exception:
                # Observability must never fail the learner workflow.
                continue


def validate_rule_contract(rule: EvolutionRule) -> None:
    contract = rule.contract
    if contract.target_agent not in ALLOWED_TARGET_AGENTS:
        raise ValueError("target_agent is not eligible for evolution rules")
    if contract.template_id not in RULE_TEMPLATES:
        raise ValueError("template_id is not registered")
    expected = RULE_TEMPLATES[contract.template_id]
    if rule.strategy_text != expected:
        raise ValueError("strategy_text must be rendered from the registered template")

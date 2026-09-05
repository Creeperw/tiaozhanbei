from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from competition_app.contracts.base import ContractModel


D1_RULE_ID = "semantic_conflict_pair_closure_v1"


class ConflictEvidencePair(ContractModel):
    """The two current-pack evidence items involved in one conflict."""

    support_evidence_id: str = Field(min_length=1, max_length=300)
    conflict_evidence_id: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def has_two_distinct_evidence_items(self) -> "ConflictEvidencePair":
        if self.support_evidence_id == self.conflict_evidence_id:
            raise ValueError("a conflict pair requires two distinct evidence IDs")
        return self


class SemanticConflictBinding(ContractModel):
    """Finite, system-consumable binding for one semantic conflict."""

    issue_id: str = Field(min_length=1, max_length=200)
    claim_location: str = Field(min_length=1, max_length=300)
    evidence_pair: ConflictEvidencePair
    owner_step_id: Literal["expert", "knowledge"]

    model_config = {"extra": "forbid"}


class SemanticConflictClosureInput(ContractModel):
    """Evaluation-only inputs; all authority checks remain code-owned."""

    binding: SemanticConflictBinding
    current_evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    allowed_location_keys: list[str] = Field(default_factory=list, max_length=200)
    actual_rerun_step_ids: list[str] = Field(default_factory=list, max_length=20)
    initial_audit_decision: str | None = None
    same_conflict_pair_resolved: bool | None = None
    new_unsupported_claims: bool | None = None

    model_config = {"extra": "forbid"}


@dataclass(frozen=True)
class SemanticConflictClosureResult:
    rule_id: str
    rule_applied: bool
    allowed: bool
    disposition: Literal["baseline_noop", "continue_to_audit", "needs_human_review"]
    reason_codes: tuple[str, ...]
    expected_rerun_step_ids: tuple[str, ...]


_EXPECTED_RERUN_CHAINS: dict[str, tuple[str, ...]] = {
    "expert": ("expert", "audit"),
    "knowledge": ("knowledge", "expert", "audit"),
}


def evaluate_semantic_conflict_closure(
    value: SemanticConflictClosureInput,
    *,
    enabled: bool,
) -> SemanticConflictClosureResult:
    """Apply D1 without changing baseline behavior when disabled.

    The rule is deliberately fail-closed.  It never trusts a model-provided
    owner or rerun chain: the owner is restricted by the contract and the
    expected chain is derived from this code-owned table.  A disabled rule is
    an observational no-op and returns ``allowed=True``.
    """

    expected_chain = _EXPECTED_RERUN_CHAINS[value.binding.owner_step_id]
    if not enabled:
        return SemanticConflictClosureResult(
            rule_id=D1_RULE_ID,
            rule_applied=False,
            allowed=True,
            disposition="baseline_noop",
            reason_codes=("rule_disabled",),
            expected_rerun_step_ids=expected_chain,
        )

    reasons: list[str] = []
    current_ids = {item.strip() for item in value.current_evidence_ids if item.strip()}
    pair_ids = {
        value.binding.evidence_pair.support_evidence_id,
        value.binding.evidence_pair.conflict_evidence_id,
    }
    if not pair_ids.issubset(current_ids):
        reasons.append("conflict_evidence_not_in_current_pack")

    allowed_locations = {
        item.strip() for item in value.allowed_location_keys if item.strip()
    }
    if value.binding.claim_location not in allowed_locations:
        reasons.append("claim_location_not_allowed")

    # A clean first Audit is a valid zero-repair terminal path.  Once a repair
    # is actually needed, the rule still requires its exact minimal chain.
    zero_repair_first_pass = (
        value.initial_audit_decision == "pass"
        and not value.actual_rerun_step_ids
    )
    if not zero_repair_first_pass and tuple(value.actual_rerun_step_ids) != expected_chain:
        reasons.append("rerun_chain_not_minimal")

    if value.same_conflict_pair_resolved is None:
        reasons.append("same_conflict_pair_check_missing")
    elif value.same_conflict_pair_resolved is not True:
        reasons.append("same_conflict_pair_unresolved")

    if value.new_unsupported_claims is None:
        reasons.append("new_unsupported_claim_check_missing")
    elif value.new_unsupported_claims:
        reasons.append("new_unsupported_claims")

    return SemanticConflictClosureResult(
        rule_id=D1_RULE_ID,
        rule_applied=True,
        allowed=not reasons,
        disposition="continue_to_audit" if not reasons else "needs_human_review",
        reason_codes=tuple(dict.fromkeys(reasons)),
        expected_rerun_step_ids=expected_chain,
    )

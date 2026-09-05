import pytest
from pydantic import ValidationError

from competition_app.evaluation.semantic_conflict_closure import (
    D1_RULE_ID,
    ConflictEvidencePair,
    SemanticConflictBinding,
    SemanticConflictClosureInput,
    evaluate_semantic_conflict_closure,
)


def _binding(**updates):
    values = {
        "issue_id": "ISSUE_CONFLICT_1",
        "claim_location": "resource:body",
        "evidence_pair": ConflictEvidencePair(
            support_evidence_id="E_SUPPORT",
            conflict_evidence_id="E_CONFLICT",
        ),
        "owner_step_id": "expert",
    }
    values.update(updates)
    return SemanticConflictBinding(**values)


def _input(**updates):
    values = {
        "binding": _binding(),
        "current_evidence_ids": ["E_SUPPORT", "E_CONFLICT"],
        "allowed_location_keys": ["resource:body"],
        "actual_rerun_step_ids": ["expert", "audit"],
        "same_conflict_pair_resolved": True,
        "new_unsupported_claims": False,
    }
    values.update(updates)
    return SemanticConflictClosureInput(**values)


def test_d1_accepts_a_closed_expert_conflict_with_the_minimal_chain():
    result = evaluate_semantic_conflict_closure(_input(), enabled=True)

    assert result.rule_id == D1_RULE_ID
    assert result.rule_applied is True
    assert result.allowed is True
    assert result.reason_codes == ()
    assert result.disposition == "continue_to_audit"
    assert result.expected_rerun_step_ids == ("expert", "audit")


def test_d1_rejects_evidence_ids_not_in_the_current_pack():
    result = evaluate_semantic_conflict_closure(
        _input(current_evidence_ids=["E_SUPPORT"]), enabled=True
    )

    assert result.allowed is False
    assert "conflict_evidence_not_in_current_pack" in result.reason_codes


def test_d1_rejects_locations_outside_the_system_catalog():
    result = evaluate_semantic_conflict_closure(
        _input(
            binding=_binding(claim_location="resource:invented_path"),
        ),
        enabled=True,
    )

    assert result.allowed is False
    assert "claim_location_not_allowed" in result.reason_codes


def test_d1_rejects_a_non_minimal_rerun_chain():
    result = evaluate_semantic_conflict_closure(
        _input(actual_rerun_step_ids=["knowledge", "expert", "audit"]),
        enabled=True,
    )

    assert result.allowed is False
    assert "rerun_chain_not_minimal" in result.reason_codes


def test_d1_fails_closed_when_the_same_conflict_was_not_resolved():
    result = evaluate_semantic_conflict_closure(
        _input(same_conflict_pair_resolved=False), enabled=True
    )

    assert result.allowed is False
    assert "same_conflict_pair_unresolved" in result.reason_codes
    assert result.disposition == "needs_human_review"


def test_d1_fails_closed_when_post_repair_checks_are_missing_or_unsafe():
    missing = evaluate_semantic_conflict_closure(
        _input(same_conflict_pair_resolved=None), enabled=True
    )
    new_claim = evaluate_semantic_conflict_closure(
        _input(new_unsupported_claims=True), enabled=True
    )

    assert missing.allowed is False
    assert "same_conflict_pair_check_missing" in missing.reason_codes
    assert new_claim.allowed is False
    assert "new_unsupported_claims" in new_claim.reason_codes


def test_d1_disabled_is_a_noop_for_the_baseline():
    result = evaluate_semantic_conflict_closure(_input(), enabled=False)

    assert result.rule_id == D1_RULE_ID
    assert result.rule_applied is False
    assert result.allowed is True
    assert result.reason_codes == ("rule_disabled",)
    assert result.disposition == "baseline_noop"


def test_d1_contract_rejects_a_pair_with_only_one_distinct_evidence_id():
    with pytest.raises(ValidationError, match="two distinct evidence IDs"):
        ConflictEvidencePair(
            support_evidence_id="E_SAME",
            conflict_evidence_id="E_SAME",
        )


def test_d1_contract_forbids_unregistered_fields():
    with pytest.raises(ValidationError):
        SemanticConflictBinding(
            issue_id="ISSUE_CONFLICT_1",
            claim_location="resource:body",
            evidence_pair=ConflictEvidencePair(
                support_evidence_id="E_SUPPORT",
                conflict_evidence_id="E_CONFLICT",
            ),
            owner_step_id="expert",
            model_generated_action="rerun_everything",
        )

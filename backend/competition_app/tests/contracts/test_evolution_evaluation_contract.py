from __future__ import annotations

import pytest
from pydantic import ValidationError

from competition_app.contracts.evolution import (
    EvolutionEvaluationArm,
    EvolutionEvaluationPairReceipt,
)


def _arm(repair_count: int, *, exposed: bool = False) -> EvolutionEvaluationArm:
    exhausted = repair_count == 3
    attempts = 2 if exhausted else repair_count
    return EvolutionEvaluationArm(
        has_target_failure=repair_count > 0,
        initial_target_failure=repair_count > 0,
        final_target_failure=exhausted,
        audit_decision="revise" if exhausted else "pass",
        first_audit_decision="pass" if repair_count == 0 else "revise",
        final_audit_decision="revise" if exhausted else "pass",
        repair_count=repair_count,
        repair_attempt_count=attempts,
        repair_exhausted=exhausted,
        max_repair_attempts=2,
        release_allowed=not exhausted,
        actual_rerun_step_ids=[] if repair_count == 0 else ["expert", "audit"],
        rule_exposed=exposed,
    )


@pytest.mark.parametrize("repair_count", [0, 1, 2, 3])
def test_evolution_arm_accepts_bounded_repair_states(repair_count):
    arm = _arm(repair_count)
    assert arm.repair_attempt_count == min(repair_count, 2)
    assert arm.repair_exhausted is (repair_count == 3)


def test_evolution_pair_requires_equal_budget_and_evidence():
    with pytest.raises(ValidationError, match="same repair budget"):
        EvolutionEvaluationPairReceipt(
            case_id="CASE_1",
            case_group="target_fault",
            rule_id="RULE_12345678",
            rule_version=1,
            context_equal=True,
            evidence_pack_equal=True,
            baseline=_arm(0),
            candidate=_arm(0, exposed=True).model_copy(
                update={"max_repair_attempts": 1}
            ),
        )
    with pytest.raises(ValidationError, match="equal frozen context/evidence"):
        EvolutionEvaluationPairReceipt(
            case_id="CASE_1",
            case_group="target_fault",
            rule_id="RULE_12345678",
            rule_version=1,
            context_equal=True,
            evidence_pack_equal=False,
            baseline=_arm(0),
            candidate=_arm(0, exposed=True),
        )


def test_evolution_failed_receipt_requires_failure_code():
    with pytest.raises(ValidationError, match="failure_code"):
        EvolutionEvaluationPairReceipt(
            case_id="CASE_1",
            case_group="target_fault",
            rule_id="RULE_12345678",
            rule_version=1,
            context_equal=False,
            evidence_pack_equal=False,
            baseline=EvolutionEvaluationArm(has_target_failure=False),
            candidate=EvolutionEvaluationArm(has_target_failure=False),
            execution_status="technical_failure",
        )


def test_evolution_arm_can_block_release_for_non_target_safety_reason():
    arm = EvolutionEvaluationArm(
        has_target_failure=True,
        initial_target_failure=True,
        final_target_failure=False,
        final_audit_decision="needs_human_review",
        release_allowed=False,
    )
    assert arm.final_target_failure is False
    assert arm.release_allowed is False

    with pytest.raises(ValidationError, match="final target failure"):
        EvolutionEvaluationArm(
            has_target_failure=True,
            initial_target_failure=True,
            final_target_failure=True,
            final_audit_decision="pass",
            release_allowed=True,
        )

from __future__ import annotations

from competition_app.evaluation.d1_ab100_v2_runner import load_d1_ab100_v2_cases
from competition_app.evaluation.d1_v2_scorer import score_d1_v2


SHA256 = "bbc322a6f25c69666fac760ecaef5c46025342ded3d6e29ebac40981b131ceaf"


def _arm(
    arm: str,
    *,
    exposed: bool,
    initial_failure: bool,
    final_failure: bool,
    repairs: int,
) -> dict:
    exhausted = repairs == 3
    return {
        "arm": arm,
        "input_digest": "a" * 64,
        "rule_exposed": exposed,
        "exposed_target_agent": "expert_agent" if exposed else None,
        "user_output": "安全摘要",
        "target_failure": initial_failure,
        "initial_target_failure": initial_failure,
        "final_target_failure": final_failure,
        "closure_allowed": not final_failure,
        "first_audit_decision": "pass" if repairs == 0 else "revise",
        "final_audit_decision": "revise" if final_failure else "pass",
        "repair_count": repairs,
        "repair_attempt_count": min(repairs, 2),
        "repair_exhausted": exhausted,
        "max_repair_attempts": 2,
        "release_allowed": not final_failure,
        "actual_rerun_step_ids": [] if repairs == 0 else ["expert", "audit"],
        "new_unsupported_claims": False,
    }


def _receipts():
    rows = []
    for index, case in enumerate(load_d1_ab100_v2_cases(), 1):
        expected_exposure = bool(case.frozen_evidence.expected_rule_exposure)
        if case.case_group == "target_fault":
            a_failure, b_failure = True, index > 30
            a_repairs, b_repairs = 2, (1 if index <= 30 else 3)
        elif case.case_group == "targeting_negative_control":
            a_failure = b_failure = False
            a_repairs = b_repairs = 0
            if case.case_id == "EVO-D1-V2-091":
                b_failure, b_repairs = True, 3
        else:
            a_failure = b_failure = False
            a_repairs = b_repairs = 0
        rows.append({
            "schema_version": "d1-v2-case-receipt-1.0",
            "case_id": case.case_id,
            "pair_order": case.pair_order,
            "attempt": 2 if case.case_id == "EVO-D1-V2-001" else 1,
            "formal_environment_write_allowed": False,
            "validation": {"valid": True, "repair_contract_valid": True},
            "arms": [
                _arm(
                    "A",
                    exposed=False,
                    initial_failure=a_failure,
                    final_failure=a_failure,
                    repairs=a_repairs,
                ),
                _arm(
                    "B",
                    exposed=expected_exposure,
                    initial_failure=a_failure,
                    final_failure=b_failure,
                    repairs=b_repairs,
                ),
            ],
        })
    return rows


def test_v2_scorer_reports_fixed_denominators_and_rmax_sentinel():
    cases = load_d1_ab100_v2_cases()
    receipts = _receipts()
    result = score_d1_v2(
        cases=cases,
        receipts=receipts,
        dataset_file_sha256=SHA256,
        expected_dataset_file_sha256=SHA256,
        technical_errors=[{"case_id": "EVO-D1-V2-001"}],
        blind_reviews=[
            {
                "case_id": case.case_id,
                "reviewer_relation": (
                    "compatible" if index == 0 else case.frozen_evidence.gold_relation
                ),
            }
            for index, case in enumerate(cases[:20])
        ],
        repeat_receipts=receipts[:20],
    )

    assert result["integrity"]["passed"] is True
    improvement = result["core_metrics"]["same_class_problem_improvement"]
    assert improvement["numerator"] == 30
    assert improvement["denominator"] == 60
    assert improvement["rate"] == 0.5
    harm = result["core_metrics"]["compatible_boundary_harm"]
    assert harm["numerator"] == 1
    assert harm["denominator"] == 10
    assert harm["harmed_case_ids"] == ["EVO-D1-V2-091"]
    repairs = result["core_metrics"]["average_repair_count_reduction"]
    assert repairs["baseline_total"] == 120
    assert repairs["candidate_total"] == 120
    assert repairs["relative_reduction"] == 0.0
    assert result["technical_execution"]["recovered_case_count"] == 1
    assert result["blind_review"]["minimum_met"] is True
    assert result["blind_review"]["agreement_count"] == 19
    assert result["repeatability"]["minimum_met"] is True
    assert result["repeatability"]["exact_outcome_agreement_count"] == 20
    assert result["evidence_completeness_gate"]["passed"] is True


def test_v2_scorer_rejects_incomplete_coverage():
    result = score_d1_v2(
        cases=load_d1_ab100_v2_cases(),
        receipts=_receipts()[:-1],
        dataset_file_sha256=SHA256,
        expected_dataset_file_sha256=SHA256,
    )

    assert result["integrity"]["passed"] is False
    assert "missing_case_receipt" in result["integrity"]["reason_codes"]
    assert "compatible_boundary_denominator_mismatch" in result["integrity"]["reason_codes"]


def test_v2_scorer_requires_distinct_review_and_repeat_cases():
    cases = load_d1_ab100_v2_cases()
    receipts = _receipts()
    result = score_d1_v2(
        cases=cases,
        receipts=receipts,
        dataset_file_sha256=SHA256,
        expected_dataset_file_sha256=SHA256,
        blind_reviews=[
            {
                "case_id": cases[0].case_id,
                "reviewer_relation": cases[0].frozen_evidence.gold_relation,
            }
            for _ in range(20)
        ],
        repeat_receipts=[receipts[0] for _ in range(20)],
    )

    assert result["blind_review"]["distinct_case_count"] == 1
    assert result["blind_review"]["minimum_met"] is False
    assert result["repeatability"]["distinct_case_count"] == 1
    assert result["repeatability"]["minimum_met"] is False
    assert result["evidence_completeness_gate"]["passed"] is False


def test_v2_scorer_rejects_non_rmax2_receipt():
    receipts = _receipts()
    receipts[0]["arms"][0]["max_repair_attempts"] = 1
    result = score_d1_v2(
        cases=load_d1_ab100_v2_cases(),
        receipts=receipts,
        dataset_file_sha256=SHA256,
        expected_dataset_file_sha256=SHA256,
    )

    assert result["integrity"]["passed"] is False
    assert result["integrity"]["invalid_case_ids"] == ["EVO-D1-V2-001"]


def test_v2_scorer_counts_non_target_release_block_as_boundary_harm():
    receipts = _receipts()
    boundary = next(row for row in receipts if row["case_id"] == "EVO-D1-V2-092")
    candidate = next(arm for arm in boundary["arms"] if arm["arm"] == "B")
    candidate["final_target_failure"] = False
    candidate["release_allowed"] = False
    candidate["final_audit_decision"] = "needs_human_review"
    result = score_d1_v2(
        cases=load_d1_ab100_v2_cases(),
        receipts=receipts,
        dataset_file_sha256=SHA256,
        expected_dataset_file_sha256=SHA256,
    )

    harm = result["core_metrics"]["compatible_boundary_harm"]
    assert harm["numerator"] == 2
    assert "EVO-D1-V2-092" in harm["harmed_case_ids"]

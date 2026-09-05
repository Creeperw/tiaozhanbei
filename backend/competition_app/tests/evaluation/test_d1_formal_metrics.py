from copy import deepcopy

from evaluation.evolution.summarize_d1_formal_results import (
    _mcnemar_exact_two_sided,
    _wilson_interval,
    summarize,
)


def _dataset(case_id: str, group: str, order: str = "AB") -> dict:
    return {
        "case_id": case_id,
        "case_group": group,
        "scenario": "target" if group == "target_fault" else "control",
        "pair_order": order,
    }


def _result(
    case_id: str,
    *,
    a_failure: bool,
    b_closure: bool,
    b_rule: bool,
    b_audit: str = "pass",
) -> dict:
    digest = f"digest-{case_id}"
    return {
        "case_id": case_id,
        "pair_order": "AB",
        "formal_environment_write_allowed": False,
        "retrieval_pack_digest": digest,
        "validation": {
            "valid": True,
            "context_equal": True,
            "a_rule_exposure": 0,
            "b_target_rule_exposure": b_rule,
        },
        "arms": {
            "A": {
                "target_failure": a_failure,
                "audit_decision": "pass",
                "pack_digest": digest,
            },
            "B": {
                "target_failure": True,
                "closure_allowed": b_closure,
                "audit_decision": b_audit,
                "same_conflict_pair_resolved": True,
                "new_unsupported_claims": False,
                "actual_rerun_step_ids": ["expert", "audit"] if b_rule else [],
                "rule_exposed": b_rule,
                "pack_digest": digest,
            },
        },
    }


def test_final_b_defect_uses_closure_not_pre_repair_failure():
    datasets = [_dataset("T1", "target_fault"), _dataset("T2", "target_fault")]
    results = [
        _result("T1", a_failure=True, b_closure=True, b_rule=True),
        _result("T2", a_failure=True, b_closure=False, b_rule=True, b_audit="revise"),
    ]
    metrics = summarize(datasets, results)

    assert metrics["primary_effect"]["a_target_defects"] == 2
    assert metrics["primary_effect"]["b_final_target_defects"] == 1
    assert metrics["primary_effect"]["paired_improvements"] == 1
    assert metrics["primary_effect"]["both_defective"] == 1
    assert metrics["process_and_safety"]["safe_blocks"] == 1


def test_control_audit_variability_is_not_counted_as_d1_regression():
    datasets = [_dataset("C1", "non_regression_control")]
    result = _result("C1", a_failure=False, b_closure=True, b_rule=False, b_audit="revise")
    result["arms"]["A"]["audit_decision"] = "pass"

    metrics = summarize(datasets, [result])

    assert metrics["controls"]["audit_decision_disagreements"] == 1
    assert metrics["process_and_safety"]["b_control_rule_exposures"] == 0
    assert metrics["primary_effect"]["paired_regressions"] == 0


def test_integrity_gate_rejects_duplicate_or_mismatched_context():
    datasets = [_dataset("T1", "target_fault")]
    result = _result("T1", a_failure=True, b_closure=True, b_rule=True)
    duplicate = deepcopy(result)
    duplicate["validation"]["context_equal"] = False

    metrics = summarize(datasets, [result, duplicate])

    assert metrics["run_integrity"]["duplicate_case_ids"] == ["T1"]
    assert metrics["run_integrity"]["integrity_passed"] is False
    assert metrics["primary_effect"]["effect_gate_passed"] is False


def test_exact_statistics_are_stable():
    assert _mcnemar_exact_two_sided(18, 0) == 0.000007629395
    assert _mcnemar_exact_two_sided(0, 0) is None
    interval = _wilson_interval(18, 30)
    assert interval is not None
    assert interval[0] < 0.6 < interval[1]

from __future__ import annotations

from collections import Counter
import math
from typing import Any

from competition_app.evaluation.d1_ab100_v2_runner import validate_d1_ab100_v2_cases
from competition_app.evaluation.d1_precheck_dataset import D1PilotCase


def rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def wilson(successes: int, total: int) -> list[float] | None:
    if total <= 0:
        return None
    z = 1.959963984540054
    proportion = successes / total
    z2 = z * z
    denominator = 1 + z2 / total
    centre = (proportion + z2 / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total + z2 / (4 * total * total)
        )
        / denominator
    )
    return [round(max(0.0, centre - margin), 6), round(min(1.0, centre + margin), 6)]


def mcnemar_exact(improvements: int, regressions: int) -> float | None:
    discordant = improvements + regressions
    if not discordant:
        return None
    tail = sum(
        math.comb(discordant, index)
        for index in range(min(improvements, regressions) + 1)
    ) / (2**discordant)
    return round(min(1.0, 2 * tail), 12)


def score_d1_v2(
    *,
    cases: list[D1PilotCase],
    receipts: list[dict[str, Any]],
    dataset_file_sha256: str,
    expected_dataset_file_sha256: str,
    technical_errors: list[dict[str, Any]] | None = None,
    blind_reviews: list[dict[str, Any]] | None = None,
    repeat_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    validate_d1_ab100_v2_cases(cases)
    technical_errors = technical_errors or []
    blind_reviews = blind_reviews or []
    repeat_receipts = repeat_receipts or []
    expected_ids = [case.case_id for case in cases]
    by_case = {case.case_id: case for case in cases}
    observed_ids = [str(item.get("case_id") or "") for item in receipts]
    duplicate_ids = sorted(
        case_id for case_id, count in Counter(observed_ids).items() if count > 1
    )
    missing_ids = sorted(set(expected_ids) - set(observed_ids))
    unexpected_ids = sorted(set(observed_ids) - set(expected_ids))
    integrity_reasons: list[str] = []
    if dataset_file_sha256 != expected_dataset_file_sha256:
        integrity_reasons.append("dataset_file_hash_mismatch")
    if duplicate_ids:
        integrity_reasons.append("duplicate_case_receipt")
    if missing_ids:
        integrity_reasons.append("missing_case_receipt")
    if unexpected_ids:
        integrity_reasons.append("unexpected_case_receipt")
    rows_by_id = {str(item.get("case_id") or ""): item for item in receipts}
    ordered_rows = [rows_by_id[item] for item in expected_ids if item in rows_by_id]
    invalid_ids = [
        row["case_id"] for row in ordered_rows if not _receipt_valid(row, by_case[row["case_id"]])
    ]
    if invalid_ids:
        integrity_reasons.append("invalid_case_receipt")

    targets = [row for row in ordered_rows if by_case[row["case_id"]].case_group == "target_fault"]
    boundaries = [
        row
        for row in ordered_rows
        if by_case[row["case_id"]].case_group == "targeting_negative_control"
    ]
    normals = [
        row
        for row in ordered_rows
        if by_case[row["case_id"]].case_group == "non_regression_control"
    ]
    if len(targets) != 60:
        integrity_reasons.append("target_denominator_mismatch")
    if len(boundaries) != 10:
        integrity_reasons.append("compatible_boundary_denominator_mismatch")
    if len(normals) != 30:
        integrity_reasons.append("normal_control_denominator_mismatch")

    improvements = [
        row["case_id"]
        for row in targets
        if _final_failure(row, "A") and not _final_failure(row, "B")
    ]
    regressions = [
        row["case_id"]
        for row in targets
        if not _final_failure(row, "A") and _final_failure(row, "B")
    ]
    a_first_pass = sum(_arm(row, "A").get("first_audit_decision") == "pass" for row in targets)
    b_first_pass = sum(_arm(row, "B").get("first_audit_decision") == "pass" for row in targets)
    a_repair_total = sum(_scored_repair_count(_arm(row, "A")) for row in targets)
    b_repair_total = sum(_scored_repair_count(_arm(row, "B")) for row in targets)
    a_repair_mean = a_repair_total / len(targets) if targets else None
    b_repair_mean = b_repair_total / len(targets) if targets else None
    repair_reduction = (
        None
        if not a_repair_mean
        else round((a_repair_mean - (b_repair_mean or 0.0)) / a_repair_mean, 6)
    )
    boundary_harm_ids = [
        row["case_id"] for row in boundaries if _compatible_boundary_harm(row)
    ]
    boundary_observations = _compatible_boundary_observations(boundaries)
    normal_regression_ids = [
        row["case_id"]
        for row in normals
        if not _final_failure(row, "A") and _final_failure(row, "B")
    ]

    order = {}
    for pair_order in ("AB", "BA"):
        order_rows = [row for row in targets if by_case[row["case_id"]].pair_order == pair_order]
        order[pair_order] = {
            "target_count": len(order_rows),
            "improvement_count": sum(
                _final_failure(row, "A") and not _final_failure(row, "B")
                for row in order_rows
            ),
            "regression_count": sum(
                not _final_failure(row, "A") and _final_failure(row, "B")
                for row in order_rows
            ),
            "candidate_first_audit_pass_count": sum(
                _arm(row, "B").get("first_audit_decision") == "pass"
                for row in order_rows
            ),
        }

    blind = _score_blind_reviews(blind_reviews, by_case)
    repeats = _score_repeats(repeat_receipts, rows_by_id, by_case)
    recovered_case_ids = sorted({
        str(row.get("case_id") or "")
        for row in receipts
        if int(row.get("attempt") or 1) > 1
    })
    failed_technical_ids = sorted(
        set(expected_ids) - set(observed_ids)
        if technical_errors else set()
    )
    integrity_passed = not integrity_reasons
    return {
        "schema_version": "d1-v2-score-1.0",
        "formal_environment_write_allowed": False,
        "formal_rule_status_change_allowed": False,
        "integrity": {
            "passed": integrity_passed,
            "reason_codes": list(dict.fromkeys(integrity_reasons)),
            "dataset_file_sha256": dataset_file_sha256,
            "expected_dataset_file_sha256": expected_dataset_file_sha256,
            "observed_receipt_count": len(receipts),
            "duplicate_case_ids": duplicate_ids,
            "missing_case_ids": missing_ids,
            "unexpected_case_ids": unexpected_ids,
            "invalid_case_ids": invalid_ids,
        },
        "core_metrics": {
            "same_class_problem_improvement": {
                "numerator": len(improvements),
                "denominator": 60,
                "rate": rate(len(improvements), 60),
                "wilson_95ci": wilson(len(improvements), 60),
                "regression_count": len(regressions),
                "mcnemar_exact_two_sided_p": mcnemar_exact(
                    len(improvements), len(regressions)
                ),
                "improved_case_ids": improvements,
                "regressed_case_ids": regressions,
            },
            "first_audit_pass_lift": {
                "baseline_numerator": a_first_pass,
                "candidate_numerator": b_first_pass,
                "denominator": 60,
                "baseline_rate": rate(a_first_pass, 60),
                "candidate_rate": rate(b_first_pass, 60),
                "absolute_percentage_point_change": round(
                    (b_first_pass - a_first_pass) / 60 * 100, 6
                ),
                "relative_change": (
                    round((b_first_pass - a_first_pass) / a_first_pass, 6)
                    if a_first_pass
                    else None
                ),
                "baseline_wilson_95ci": wilson(a_first_pass, 60),
                "candidate_wilson_95ci": wilson(b_first_pass, 60),
            },
            "compatible_boundary_harm": {
                "numerator": len(boundary_harm_ids),
                "denominator": 10,
                "rate": rate(len(boundary_harm_ids), 10),
                "wilson_95ci": wilson(len(boundary_harm_ids), 10),
                "harmed_case_ids": boundary_harm_ids,
                "observations": boundary_observations,
            },
            "average_repair_count_reduction": {
                "baseline_total": a_repair_total,
                "candidate_total": b_repair_total,
                "denominator": 60,
                "baseline_mean": round(a_repair_mean, 6) if a_repair_mean is not None else None,
                "candidate_mean": round(b_repair_mean, 6) if b_repair_mean is not None else None,
                "relative_reduction": repair_reduction,
                "relative_reduction_display": "N/A" if repair_reduction is None else repair_reduction,
                "scoring_contract": "0=first-pass,1=one-repair-pass,2=two-repair-pass,3=RMAX-exhausted",
            },
        },
        "controls": {
            "normal_control_count": len(normals),
            "normal_control_regression_count": len(normal_regression_ids),
            "normal_control_regression_case_ids": normal_regression_ids,
        },
        "technical_execution": {
            "technical_error_attempt_count": len(technical_errors),
            "recovered_case_count": len(recovered_case_ids),
            "recovered_case_ids": recovered_case_ids,
            "failed_case_count": len(failed_technical_ids),
            "failed_case_ids": failed_technical_ids,
        },
        "blind_review": blind,
        "repeatability": repeats,
        "pair_order_sensitivity": order,
        "evidence_completeness_gate": {
            "passed": bool(
                integrity_passed
                and blind["minimum_met"]
                and repeats["minimum_met"]
                and not failed_technical_ids
            ),
            "requires_integrity": True,
            "requires_distinct_blind_reviews": 20,
            "requires_distinct_repeat_runs": 20,
            "formal_rule_status_change_allowed": False,
        },
    }


def _receipt_valid(row: dict[str, Any], case: D1PilotCase) -> bool:
    if row.get("case_id") != case.case_id:
        return False
    if row.get("pair_order") != case.pair_order:
        return False
    if row.get("formal_environment_write_allowed") is not False:
        return False
    validation = row.get("validation") or {}
    arms = row.get("arms") or []
    if not validation.get("valid") or not validation.get("repair_contract_valid"):
        return False
    if len(arms) != 2 or {item.get("arm") for item in arms} != {"A", "B"}:
        return False
    by_arm = {item["arm"]: item for item in arms}
    expected_exposure = bool(case.frozen_evidence.expected_rule_exposure)
    repair_contract_valid = all(
        int(arm.get("max_repair_attempts") or -1) == 2
        and int(arm.get("repair_attempt_count") or 0) in {0, 1, 2}
        and int(arm.get("repair_count") or 0) in {0, 1, 2, 3}
        and bool(arm.get("repair_exhausted"))
        == (int(arm.get("repair_count") or 0) == 3)
        and (
            int(arm.get("repair_count") or 0) == 3
            or int(arm.get("repair_count") or 0)
            == int(arm.get("repair_attempt_count") or 0)
        )
        for arm in by_arm.values()
    )
    return bool(
        repair_contract_valid
        and
        not by_arm["A"].get("rule_exposed")
        and bool(by_arm["B"].get("rule_exposed")) == expected_exposure
        and by_arm["A"].get("input_digest") == by_arm["B"].get("input_digest")
    )


def _arm(row: dict[str, Any], arm: str) -> dict[str, Any]:
    return next(item for item in row["arms"] if item["arm"] == arm)


def _final_failure(row: dict[str, Any], arm: str) -> bool:
    value = _arm(row, arm)
    return bool(
        value.get("final_target_failure")
        or not bool(value.get("release_allowed"))
    )


def _scored_repair_count(value: dict[str, Any]) -> int:
    maximum = int(value.get("max_repair_attempts") or 2)
    if value.get("repair_exhausted"):
        return maximum + 1
    return int(value.get("repair_count") or 0)


def _compatible_boundary_harm(row: dict[str, Any]) -> bool:
    a = _arm(row, "A")
    b = _arm(row, "B")
    return bool(
        _final_failure(row, "B") and not _final_failure(row, "A")
        or int(b.get("repair_attempt_count") or 0) > int(a.get("repair_attempt_count") or 0)
        or b.get("final_audit_decision") == "needs_human_review"
        and a.get("final_audit_decision") != "needs_human_review"
        or b.get("new_unsupported_claims") and not a.get("new_unsupported_claims")
        or b.get("audit_conflicting_evidence") and not a.get("audit_conflicting_evidence")
        or b.get("semantic_conflict_binding_created")
        and not a.get("semantic_conflict_binding_created")
        or b.get("closure_rule_applied") and not a.get("closure_rule_applied")
    )


def _compatible_boundary_observations(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    predicates = {
        "introduced_contradiction_finding": lambda a, b: bool(
            b.get("audit_conflicting_evidence")
            and not a.get("audit_conflicting_evidence")
        ),
        "introduced_conflict_binding": lambda a, b: bool(
            b.get("semantic_conflict_binding_created")
            and not a.get("semantic_conflict_binding_created")
        ),
        "closure_rule_wrongly_applied": lambda a, b: bool(
            b.get("closure_rule_applied") and not a.get("closure_rule_applied")
        ),
        "unnecessary_repair": lambda a, b: bool(
            int(b.get("repair_attempt_count") or 0)
            > int(a.get("repair_attempt_count") or 0)
        ),
        "introduced_human_review": lambda a, b: bool(
            b.get("final_audit_decision") == "needs_human_review"
            and a.get("final_audit_decision") != "needs_human_review"
        ),
        "introduced_unsupported_claim": lambda a, b: bool(
            b.get("new_unsupported_claims")
            and not a.get("new_unsupported_claims")
        ),
    }
    result: dict[str, dict[str, Any]] = {}
    for name, predicate in predicates.items():
        case_ids = [
            row["case_id"]
            for row in rows
            if predicate(_arm(row, "A"), _arm(row, "B"))
        ]
        result[name] = {
            "count": len(case_ids),
            "denominator": 10,
            "case_ids": case_ids,
        }
    return result


def _score_blind_reviews(
    reviews: list[dict[str, Any]],
    by_case: dict[str, D1PilotCase],
) -> dict[str, Any]:
    allowed_relations = {"contradiction", "compatible", "not_applicable"}
    observed_ids = [
        str(row.get("case_id") or "")
        for row in reviews
        if str(row.get("case_id") or "") in by_case
    ]
    duplicate_ids = sorted(
        case_id for case_id, count in Counter(observed_ids).items() if count > 1
    )
    invalid_label_case_ids = sorted({
        str(row.get("case_id") or "")
        for row in reviews
        if str(row.get("case_id") or "") in by_case
        and str(row.get("reviewer_relation") or "").strip().lower()
        not in allowed_relations
    })
    eligible_by_id: dict[str, dict[str, Any]] = {}
    for row in reviews:
        case_id = str(row.get("case_id") or "")
        label = str(row.get("reviewer_relation") or "").strip().lower()
        if case_id in by_case and label in allowed_relations and case_id not in eligible_by_id:
            eligible_by_id[case_id] = row
    eligible = list(eligible_by_id.values())
    agreements = sum(
        str(row.get("reviewer_relation") or "").strip().lower()
        == str(by_case[str(row["case_id"])].frozen_evidence.gold_relation)
        for row in eligible
    )
    return {
        "review_count": len(eligible),
        "distinct_case_count": len(eligible),
        "minimum_required": 20,
        "minimum_met": len(eligible) >= 20,
        "duplicate_case_ids": duplicate_ids,
        "invalid_label_case_ids": invalid_label_case_ids,
        "agreement_count": agreements,
        "agreement_rate": rate(agreements, len(eligible)),
        "disagreement_case_ids": sorted(
            str(row.get("case_id") or "")
            for row in eligible
            if str(row.get("reviewer_relation") or "").strip().lower()
            != str(by_case[str(row["case_id"])].frozen_evidence.gold_relation)
        ),
    }


def _score_repeats(
    repeats: list[dict[str, Any]],
    originals: dict[str, dict[str, Any]],
    by_case: dict[str, D1PilotCase],
) -> dict[str, Any]:
    observed_ids = [
        str(row.get("case_id") or "")
        for row in repeats
        if str(row.get("case_id") or "") in originals
        and str(row.get("case_id") or "") in by_case
    ]
    duplicate_ids = sorted(
        case_id for case_id, count in Counter(observed_ids).items() if count > 1
    )
    invalid_case_ids: list[str] = []
    eligible_by_id: dict[str, dict[str, Any]] = {}
    for row in repeats:
        case_id = str(row.get("case_id") or "")
        if case_id not in originals or case_id not in by_case:
            continue
        if not _receipt_valid(row, by_case[case_id]):
            invalid_case_ids.append(case_id)
            continue
        if case_id not in eligible_by_id:
            eligible_by_id[case_id] = row
    eligible = list(eligible_by_id.values())
    agreements = 0
    disagreements: list[str] = []
    for row in eligible:
        case_id = str(row["case_id"])
        original = originals[case_id]
        same = all(
            _final_failure(row, arm) == _final_failure(original, arm)
            and _scored_repair_count(_arm(row, arm))
            == _scored_repair_count(_arm(original, arm))
            for arm in ("A", "B")
        )
        agreements += int(same)
        if not same:
            disagreements.append(case_id)
    return {
        "repeat_count": len(eligible),
        "distinct_case_count": len(eligible),
        "minimum_required": 20,
        "minimum_met": len(eligible) >= 20,
        "duplicate_case_ids": duplicate_ids,
        "invalid_case_ids": sorted(set(invalid_case_ids)),
        "exact_outcome_agreement_count": agreements,
        "exact_outcome_agreement_rate": rate(agreements, len(eligible)),
        "disagreement_case_ids": sorted(disagreements),
    }

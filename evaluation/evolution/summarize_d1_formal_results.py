from __future__ import annotations

"""Deterministically summarize the frozen D1 formal paired evaluation.

The scorer intentionally consumes only the frozen dataset and compact receipts.
It never calls a model and never reads or writes production application state.
"""

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DATASET = Path(
    "evaluation/evolution/datasets/semantic_conflict_closure_ab50_20260819.jsonl"
)
DEFAULT_MANIFEST = Path(
    "evaluation/evolution/datasets/semantic_conflict_closure_ab50_20260819.manifest.json"
)
DEFAULT_RESULTS = Path(
    "evaluation/evolution/outputs/semantic_conflict_closure_ab50_results.jsonl"
)
DEFAULT_METRICS = Path(
    "evaluation/evolution/outputs/semantic_conflict_closure_ab50_metrics.json"
)
DEFAULT_CASES_CSV = Path(
    "evaluation/evolution/outputs/semantic_conflict_closure_ab50_cases.csv"
)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: expected JSON object")
        rows.append(value)
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rate(count: int, total: int) -> float | None:
    return round(count / total, 6) if total else None


def _wilson_interval(successes: int, total: int, *, z: float = 1.959963984540054) -> list[float] | None:
    """Return a two-sided Wilson score interval for a binomial proportion."""
    if total <= 0:
        return None
    proportion = successes / total
    z2 = z * z
    denominator = 1 + z2 / total
    centre = (proportion + z2 / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z2 / (4 * total * total))
        / denominator
    )
    return [round(max(0.0, centre - margin), 6), round(min(1.0, centre + margin), 6)]


def _mcnemar_exact_two_sided(improvements: int, regressions: int) -> float | None:
    """Exact two-sided McNemar p-value using the discordant-pair binomial."""
    discordant = improvements + regressions
    if discordant == 0:
        return None
    smaller = min(improvements, regressions)
    tail = sum(math.comb(discordant, index) for index in range(smaller + 1)) / (2**discordant)
    return round(min(1.0, 2 * tail), 12)


def _duplicates(values: Iterable[str]) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def _target_outcome(row: dict[str, Any], arm: str) -> bool:
    """True means the target defect remains in the arm's final business outcome.

    A has no D1 closure, so its measured target failure is the final defect.
    B succeeds only when the isolated closure gate and final Audit both pass.
    The B ``target_failure`` receipt field records the pre-repair state and must
    therefore not be used as the final B defect label.
    """
    value = row["arms"][arm]
    if arm == "A":
        return bool(value["target_failure"])
    return not bool(value["closure_allowed"])


def _scenario_metrics(rows: list[dict[str, Any]], dataset_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(dataset_by_id[row["case_id"]]["scenario"])].append(row)
    output: dict[str, Any] = {}
    for scenario, items in sorted(grouped.items()):
        target_items = [
            item for item in items if dataset_by_id[item["case_id"]]["case_group"] == "target_fault"
        ]
        output[scenario] = {
            "case_count": len(items),
            "target_case_count": len(target_items),
            "a_target_defects": sum(_target_outcome(item, "A") for item in target_items),
            "b_final_target_defects": sum(_target_outcome(item, "B") for item in target_items),
            "b_closure_successes": sum(not _target_outcome(item, "B") for item in target_items),
            "b_audit_decisions": dict(
                Counter(str(item["arms"]["B"]["audit_decision"]) for item in items)
            ),
        }
    return output


def summarize(
    dataset_rows: list[dict[str, Any]],
    result_rows: list[dict[str, Any]],
    *,
    dataset_digest: str | None = None,
    expected_dataset_digest: str | None = None,
) -> dict[str, Any]:
    dataset_ids = [str(row["case_id"]) for row in dataset_rows]
    result_ids = [str(row.get("case_id", "")) for row in result_rows]
    dataset_by_id = {str(row["case_id"]): row for row in dataset_rows}
    result_by_id = {str(row.get("case_id", "")): row for row in result_rows}
    duplicate_result_ids = _duplicates(result_ids)
    missing_case_ids = sorted(set(dataset_ids) - set(result_ids))
    unexpected_case_ids = sorted(set(result_ids) - set(dataset_ids))

    complete_rows = [result_by_id[case_id] for case_id in dataset_ids if case_id in result_by_id]
    target_rows = [
        row for row in complete_rows if dataset_by_id[row["case_id"]]["case_group"] == "target_fault"
    ]
    non_regression_rows = [
        row
        for row in complete_rows
        if dataset_by_id[row["case_id"]]["case_group"] == "non_regression_control"
    ]
    negative_rows = [
        row
        for row in complete_rows
        if dataset_by_id[row["case_id"]]["case_group"] == "targeting_negative_control"
    ]
    control_rows = non_regression_rows + negative_rows

    a_defects = sum(_target_outcome(row, "A") for row in target_rows)
    b_defects = sum(_target_outcome(row, "B") for row in target_rows)
    improvements = sum(
        _target_outcome(row, "A") and not _target_outcome(row, "B") for row in target_rows
    )
    regressions = sum(
        not _target_outcome(row, "A") and _target_outcome(row, "B") for row in target_rows
    )
    both_defective = sum(
        _target_outcome(row, "A") and _target_outcome(row, "B") for row in target_rows
    )
    both_successful = sum(
        not _target_outcome(row, "A") and not _target_outcome(row, "B") for row in target_rows
    )
    target_total = len(target_rows)
    a_rate = a_defects / target_total if target_total else 0.0
    b_rate = b_defects / target_total if target_total else 0.0
    absolute_reduction = a_rate - b_rate
    relative_reduction = absolute_reduction / a_rate if a_rate else None

    unsafe_release_ids = [
        row["case_id"]
        for row in target_rows
        if bool(row["arms"]["B"]["closure_allowed"])
        and (
            str(row["arms"]["B"]["audit_decision"]) != "pass"
            or not bool(row["arms"]["B"]["same_conflict_pair_resolved"])
            or bool(row["arms"]["B"]["new_unsupported_claims"])
        )
    ]
    safe_block_ids = [
        row["case_id"]
        for row in target_rows
        if _target_outcome(row, "B")
        and str(row["arms"]["B"]["audit_decision"])
        in {"revise", "reject", "needs_human_review"}
    ]
    exact_chain_ids = [
        row["case_id"]
        for row in target_rows
        if list(row["arms"]["B"]["actual_rerun_step_ids"]) == ["expert", "audit"]
    ]

    context_equal = sum(bool(row["validation"]["context_equal"]) for row in complete_rows)
    pack_equal = sum(
        row["arms"]["A"]["pack_digest"]
        == row["arms"]["B"]["pack_digest"]
        == row["retrieval_pack_digest"]
        for row in complete_rows
    )
    valid_receipts = sum(bool(row["validation"]["valid"]) for row in complete_rows)
    write_disabled = sum(row.get("formal_environment_write_allowed") is False for row in complete_rows)

    order_metrics: dict[str, Any] = {}
    for order in ("AB", "BA"):
        items = [row for row in target_rows if row.get("pair_order") == order]
        order_metrics[order] = {
            "target_cases": len(items),
            "a_target_defects": sum(_target_outcome(row, "A") for row in items),
            "b_final_target_defects": sum(_target_outcome(row, "B") for row in items),
            "paired_improvements": sum(
                _target_outcome(row, "A") and not _target_outcome(row, "B") for row in items
            ),
            "paired_regressions": sum(
                not _target_outcome(row, "A") and _target_outcome(row, "B") for row in items
            ),
        }

    integrity_passed = not any(
        (
            len(dataset_rows) != 50,
            len(result_rows) != 50,
            duplicate_result_ids,
            missing_case_ids,
            unexpected_case_ids,
            valid_receipts != len(complete_rows),
            context_equal != len(complete_rows),
            pack_equal != len(complete_rows),
            write_disabled != len(complete_rows),
            dataset_digest is not None
            and expected_dataset_digest is not None
            and dataset_digest != expected_dataset_digest,
        )
    )
    effect_gate_passed = bool(
        integrity_passed
        and target_total == 30
        and a_defects >= 15
        and b_defects < a_defects
        and improvements > regressions
        and not unsafe_release_ids
        and not any(bool(row["arms"]["B"]["rule_exposed"]) for row in control_rows)
    )

    return {
        "schema_version": "d1-formal-metrics-1.0",
        "metric_basis": "strictly_recomputable_from_frozen_dataset_and_compact_receipts",
        "outcome_contract": {
            "a_final_target_defect": "A.target_failure == true",
            "b_final_target_defect": "B.closure_allowed != true",
            "important_note": (
                "B.target_failure is the pre-repair state and is not used as B final outcome"
            ),
        },
        "dataset": {
            "case_count": len(dataset_rows),
            "case_groups": dict(Counter(str(row["case_group"]) for row in dataset_rows)),
            "pair_orders": dict(Counter(str(row["pair_order"]) for row in dataset_rows)),
            "sha256": dataset_digest,
            "expected_sha256": expected_dataset_digest,
            "hash_matches_manifest": (
                dataset_digest == expected_dataset_digest
                if dataset_digest is not None and expected_dataset_digest is not None
                else None
            ),
        },
        "run_integrity": {
            "observed_results": len(result_rows),
            "unique_results": len(set(result_ids)),
            "duplicate_case_ids": duplicate_result_ids,
            "missing_case_ids": missing_case_ids,
            "unexpected_case_ids": unexpected_case_ids,
            "valid_receipts": valid_receipts,
            "context_equal_pairs": context_equal,
            "evidence_pack_equal_pairs": pack_equal,
            "formal_write_disabled_pairs": write_disabled,
            "integrity_passed": integrity_passed,
        },
        "primary_effect": {
            "target_cases": target_total,
            "a_target_defects": a_defects,
            "a_target_defect_rate": _rate(a_defects, target_total),
            "b_final_target_defects": b_defects,
            "b_final_target_defect_rate": _rate(b_defects, target_total),
            "absolute_defect_reduction_pp": round(absolute_reduction * 100, 4),
            "relative_defect_reduction": round(relative_reduction, 6) if relative_reduction is not None else None,
            "paired_improvements": improvements,
            "paired_improvement_rate": _rate(improvements, target_total),
            "paired_improvement_rate_wilson_95ci": _wilson_interval(improvements, target_total),
            "paired_regressions": regressions,
            "paired_regression_rate": _rate(regressions, target_total),
            "net_paired_improvement_rate": _rate(improvements - regressions, target_total),
            "both_defective": both_defective,
            "both_successful": both_successful,
            "mcnemar_exact_two_sided_p": _mcnemar_exact_two_sided(improvements, regressions),
            "effect_gate_passed": effect_gate_passed,
        },
        "process_and_safety": {
            "b_final_audit_decisions_target": dict(
                Counter(str(row["arms"]["B"]["audit_decision"]) for row in target_rows)
            ),
            "b_same_conflict_pair_resolved": sum(
                bool(row["arms"]["B"]["same_conflict_pair_resolved"]) for row in target_rows
            ),
            "b_exact_minimal_rerun_chain": len(exact_chain_ids),
            "b_new_unsupported_claims": sum(
                bool(row["arms"]["B"]["new_unsupported_claims"]) for row in target_rows
            ),
            "safe_blocks": len(safe_block_ids),
            "safe_block_case_ids": safe_block_ids,
            "unsafe_releases": len(unsafe_release_ids),
            "unsafe_release_case_ids": unsafe_release_ids,
            "a_rule_exposures": sum(int(row["validation"]["a_rule_exposure"]) for row in complete_rows),
            "b_correct_target_rule_exposures": sum(
                bool(row["validation"]["b_target_rule_exposure"]) for row in target_rows
            ),
            "b_control_rule_exposures": sum(
                bool(row["arms"]["B"]["rule_exposed"]) for row in control_rows
            ),
        },
        "controls": {
            "non_regression_cases": len(non_regression_rows),
            "targeting_negative_cases": len(negative_rows),
            "a_target_failures": sum(bool(row["arms"]["A"]["target_failure"]) for row in control_rows),
            "b_target_failures": sum(bool(row["arms"]["B"]["target_failure"]) for row in control_rows),
            "b_new_unsupported_claims": sum(
                bool(row["arms"]["B"]["new_unsupported_claims"]) for row in control_rows
            ),
            "audit_decision_agreements": sum(
                row["arms"]["A"]["audit_decision"] == row["arms"]["B"]["audit_decision"]
                for row in control_rows
            ),
            "audit_decision_disagreements": sum(
                row["arms"]["A"]["audit_decision"] != row["arms"]["B"]["audit_decision"]
                for row in control_rows
            ),
            "audit_variability_note": (
                "Control arms receive no D1 exposure; Audit disagreement is reported as live-model variability, "
                "not attributed to D1 treatment."
            ),
        },
        "scenario_metrics": _scenario_metrics(complete_rows, dataset_by_id),
        "pair_order_sensitivity": order_metrics,
        "historical_accountability_boundary": {
            "allowed_use": "historical capability baseline and development replay material",
            "forbidden_use": "formal D1 A arm or direct subtraction from D1 effect metrics",
        },
        "instrumentation_limits": [
            "Compact receipts do not retain first-round B Audit decisions.",
            "Transient failed attempts were not persisted by the original runner.",
            "Audit disagreement on unexposed controls reflects independent live-model calls.",
        ],
    }


def _write_case_csv(
    path: Path,
    dataset_rows: list[dict[str, Any]],
    result_rows: list[dict[str, Any]],
) -> None:
    dataset_by_id = {str(row["case_id"]): row for row in dataset_rows}
    result_by_id = {str(row["case_id"]): row for row in result_rows}
    fields = [
        "case_id",
        "case_group",
        "scenario",
        "pair_order",
        "validation_valid",
        "context_equal",
        "a_target_defect",
        "b_final_target_defect",
        "paired_outcome",
        "b_audit_decision",
        "b_closure_allowed",
        "b_same_conflict_pair_resolved",
        "b_new_unsupported_claims",
        "b_rule_exposed",
        "b_actual_rerun_step_ids",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for case_id in [str(row["case_id"]) for row in dataset_rows]:
            result = result_by_id.get(case_id)
            if result is None:
                continue
            is_target = dataset_by_id[case_id]["case_group"] == "target_fault"
            a_defect = _target_outcome(result, "A") if is_target else False
            b_defect = _target_outcome(result, "B") if is_target else False
            if not is_target:
                paired_outcome = "control"
            elif a_defect and not b_defect:
                paired_outcome = "improved"
            elif not a_defect and b_defect:
                paired_outcome = "regressed"
            elif a_defect and b_defect:
                paired_outcome = "both_defective"
            else:
                paired_outcome = "both_successful"
            writer.writerow(
                {
                    "case_id": case_id,
                    "case_group": dataset_by_id[case_id]["case_group"],
                    "scenario": dataset_by_id[case_id]["scenario"],
                    "pair_order": result["pair_order"],
                    "validation_valid": result["validation"]["valid"],
                    "context_equal": result["validation"]["context_equal"],
                    "a_target_defect": a_defect if is_target else "",
                    "b_final_target_defect": b_defect if is_target else "",
                    "paired_outcome": paired_outcome,
                    "b_audit_decision": result["arms"]["B"]["audit_decision"],
                    "b_closure_allowed": result["arms"]["B"]["closure_allowed"],
                    "b_same_conflict_pair_resolved": result["arms"]["B"]["same_conflict_pair_resolved"],
                    "b_new_unsupported_claims": result["arms"]["B"]["new_unsupported_claims"],
                    "b_rule_exposed": result["arms"]["B"]["rule_exposed"],
                    "b_actual_rerun_step_ids": ">".join(result["arms"]["B"]["actual_rerun_step_ids"]),
                }
            )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize frozen D1 formal paired results")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--metrics-output", type=Path, default=DEFAULT_METRICS)
    parser.add_argument("--cases-output", type=Path, default=DEFAULT_CASES_CSV)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    dataset_rows = _jsonl(args.dataset)
    result_rows = _jsonl(args.results)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    expected_digest = manifest["files"][args.dataset.name]
    metrics = summarize(
        dataset_rows,
        result_rows,
        dataset_digest=_sha256(args.dataset),
        expected_dataset_digest=str(expected_digest),
    )
    args.metrics_output.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_output.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_case_csv(args.cases_output, dataset_rows, result_rows)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

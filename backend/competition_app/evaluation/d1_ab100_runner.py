from __future__ import annotations

"""Isolated runner contract for the expanded 100-case D1 evaluation.

The original AB50 runner remains unchanged.  This module opts into the new
dataset explicitly so production code and completed AB50 evidence cannot be
silently switched to a different evaluation contract.
"""

from collections import Counter
from pathlib import Path
from typing import Any

from competition_app.evaluation.d1_formal_runner import load_d1_formal_cases
from competition_app.evaluation.d1_precheck_dataset import D1PilotCase
from competition_app.evaluation.d1_precheck_runner import D1PrecheckRunner


AB100_DATASET = (
    Path(__file__).resolve().parents[3]
    / "evaluation"
    / "evolution"
    / "datasets"
    / "semantic_conflict_closure_ab100_20260826.jsonl"
)


def load_d1_ab100_cases(path: Path = AB100_DATASET) -> list[D1PilotCase]:
    return load_d1_formal_cases(path)


def validate_d1_ab100_cases(cases: list[D1PilotCase]) -> dict[str, Any]:
    if len(cases) != 100:
        raise ValueError(f"expected 100 cases, got {len(cases)}")

    ids = [case.case_id for case in cases]
    expected_ids = [f"EVO-D1-AB50-{index:03d}" for index in range(1, 51)] + [
        f"EVO-D1-AB100-{index:03d}" for index in range(51, 101)
    ]
    if ids != expected_ids:
        raise ValueError("AB100 must retain AB50-001..050 and append AB100-051..100")
    if len(set(ids)) != 100:
        raise ValueError("case IDs must be unique")
    if len({"".join(case.prompt.split()) for case in cases}) != 100:
        raise ValueError("AB100 prompts must be unique")

    groups = Counter(case.case_group for case in cases)
    expected_groups = Counter(
        {
            "target_fault": 60,
            "non_regression_control": 30,
            "targeting_negative_control": 10,
        }
    )
    if groups != expected_groups:
        raise ValueError(f"unexpected case groups: {dict(groups)}")

    orders = Counter(case.pair_order for case in cases)
    if orders != Counter({"AB": 50, "BA": 50}):
        raise ValueError(f"unexpected pair orders: {dict(orders)}")

    target_scenarios = Counter(
        case.scenario for case in cases if case.case_group == "target_fault"
    )
    expected_target_scenarios = Counter(
        {
            "expert_conflict_pair_resolvable": 20,
            "expert_conflict_pair_residual_after_repair": 20,
            "expert_conflict_pair_expanded_rerun": 20,
        }
    )
    if target_scenarios != expected_target_scenarios:
        raise ValueError(f"unexpected target scenarios: {dict(target_scenarios)}")

    if any(case.target_rule != "semantic_conflict_pair_closure_v1" for case in cases):
        raise ValueError("all AB100 cases must target D1")
    if any(
        case.target_agent != "expert_agent" or case.owner_step_id != "expert"
        for case in cases
    ):
        raise ValueError("AB100 D1 target/owner is invalid")
    if any(case.expected_rerun_step_ids != ("expert", "audit") for case in cases):
        raise ValueError("AB100 D1 rerun chain must be expert -> audit")
    if any(case.formal_environment_write_allowed for case in cases):
        raise ValueError("formal environment writeback must remain disabled")

    forbidden = (
        "semantic_conflict_pair_closure",
        "evidence_id",
        "EvidencePack",
        "提示词",
        "Schema",
        "Trace",
    )
    if any(any(token in case.prompt for token in forbidden) for case in cases):
        raise ValueError("AB100 learner prompts contain internal evaluation hints")

    return {
        "dataset_id": "semantic_conflict_closure_ab100_20260826",
        "case_count": len(cases),
        "base_case_count": 50,
        "extension_case_count": 50,
        "case_groups": dict(groups),
        "pair_orders": dict(orders),
        "target_scenarios": dict(target_scenarios),
        "paired_model_run_count": len(cases) * 2,
        "formal_environment_write_allowed": False,
    }


class D1AB100Runner(D1PrecheckRunner):
    """Use the isolated pair mechanics with an explicit AB100 contract."""

    def __init__(self, cases: list[D1PilotCase] | None = None) -> None:
        self.cases = cases or load_d1_ab100_cases()
        validate_d1_ab100_cases(self.cases)
        self._by_id = {case.case_id: case for case in self.cases}

    def cases_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "case_id": case.case_id,
                "case_group": case.case_group,
                "expected_task_type": case.expected_task_type,
                "pair_order": case.pair_order,
                "target_rule": case.target_rule,
                "target_agent": case.target_agent,
                "owner_step_id": case.owner_step_id,
                "expected_rerun_step_ids": list(case.expected_rerun_step_ids),
                "scenario": case.scenario,
                "formal_environment_write_allowed": case.formal_environment_write_allowed,
            }
            for case in self.cases
        ]


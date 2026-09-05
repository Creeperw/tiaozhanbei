from __future__ import annotations

"""Formal 50-case D1 runner.

It reuses the isolated pair preparation/receipt validation logic but has a
separate dataset contract from the five-case pilot. It never writes to formal
application state.
"""

from collections import Counter
import json
from pathlib import Path
from typing import Any

from competition_app.evaluation.d1_precheck_dataset import D1PilotCase, d1_case_from_raw
from competition_app.evaluation.d1_precheck_runner import D1PrecheckRunner

FORMAL_DATASET = (
    Path(__file__).resolve().parents[3]
    / "evaluation"
    / "evolution"
    / "datasets"
    / "semantic_conflict_closure_ab50_20260819.jsonl"
)
_ALLOWED_GROUPS = frozenset({"target_fault", "non_regression_control", "targeting_negative_control"})


def load_d1_formal_cases(path: Path = FORMAL_DATASET) -> list[D1PilotCase]:
    cases: list[D1PilotCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        cases.append(d1_case_from_raw(raw))
    return cases


def validate_d1_formal_cases(cases: list[D1PilotCase]) -> dict[str, Any]:
    if len(cases) != 50:
        raise ValueError(f"expected 50 cases, got {len(cases)}")
    ids = [case.case_id for case in cases]
    if ids != [f"EVO-D1-AB50-{index:03d}" for index in range(1, 51)]:
        raise ValueError("case IDs must be contiguous EVO-D1-AB50-001..050")
    if len(set(ids)) != len(ids):
        raise ValueError("case IDs must be unique")
    if len({"".join(case.prompt.split()) for case in cases}) != 50:
        raise ValueError("formal prompts must be unique")
    groups = Counter(case.case_group for case in cases)
    if groups != Counter({"target_fault": 30, "non_regression_control": 15, "targeting_negative_control": 5}):
        raise ValueError(f"unexpected case groups: {dict(groups)}")
    orders = Counter(case.pair_order for case in cases)
    if orders != Counter({"AB": 25, "BA": 25}):
        raise ValueError(f"unexpected pair orders: {dict(orders)}")
    if any(case.target_rule != "semantic_conflict_pair_closure_v1" for case in cases):
        raise ValueError("all formal cases must target D1")
    if any(case.target_agent != "expert_agent" or case.owner_step_id != "expert" for case in cases):
        raise ValueError("formal D1 target/owner is invalid")
    if any(case.expected_rerun_step_ids != ("expert", "audit") for case in cases):
        raise ValueError("formal D1 rerun chain must be expert -> audit")
    if any(case.formal_environment_write_allowed for case in cases):
        raise ValueError("formal environment writeback must remain disabled")
    if any(any(token in case.prompt for token in ("semantic_conflict_pair_closure", "evidence_id", "EvidencePack", "提示词", "Schema", "Trace")) for case in cases):
        raise ValueError("formal learner prompts contain internal evaluation hints")
    return {
        "case_count": len(cases),
        "case_groups": dict(groups),
        "pair_orders": dict(orders),
        "paired_model_run_count": len(cases) * 2,
        "formal_environment_write_allowed": False,
    }


class D1FormalRunner(D1PrecheckRunner):
    """Same isolated pair mechanics, with the frozen 50-case contract."""

    def __init__(self, cases: list[D1PilotCase] | None = None) -> None:
        self.cases = cases or load_d1_formal_cases()
        validate_d1_formal_cases(self.cases)
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


def main() -> None:
    print(json.dumps(validate_d1_formal_cases(load_d1_formal_cases()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

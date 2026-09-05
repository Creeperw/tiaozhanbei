from __future__ import annotations

"""Runner contract for the construct-valid, frozen-evidence D1 AB100 V2."""

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

from competition_app.evaluation.d1_formal_runner import load_d1_formal_cases
from competition_app.evaluation.d1_precheck_dataset import D1PilotCase
from competition_app.evaluation.d1_precheck_runner import D1PrecheckRunner


AB100_V2_DATASET = (
    Path(__file__).resolve().parents[3]
    / "evaluation"
    / "evolution"
    / "datasets"
    / "semantic_conflict_closure_ab100_v2_20260901.jsonl"
)


def load_d1_ab100_v2_cases(path: Path = AB100_V2_DATASET) -> list[D1PilotCase]:
    return load_d1_formal_cases(path)


def validate_d1_ab100_v2_cases(cases: list[D1PilotCase]) -> dict[str, Any]:
    if len(cases) != 100:
        raise ValueError(f"expected 100 cases, got {len(cases)}")
    if [case.case_id for case in cases] != [f"EVO-D1-V2-{i:03d}" for i in range(1, 101)]:
        raise ValueError("V2 case IDs must be contiguous")
    groups = Counter(case.case_group for case in cases)
    if groups != Counter({"target_fault": 60, "non_regression_control": 30, "targeting_negative_control": 10}):
        raise ValueError(f"unexpected groups: {dict(groups)}")
    orders = Counter(case.pair_order for case in cases)
    if orders != Counter({"AB": 50, "BA": 50}):
        raise ValueError(f"unexpected pair orders: {dict(orders)}")
    if any(case.frozen_evidence is None for case in cases):
        raise ValueError("every V2 case must freeze its evidence fixture")
    relations = Counter(case.frozen_evidence.gold_relation for case in cases if case.frozen_evidence)
    if relations != Counter({"contradiction": 60, "not_applicable": 30, "compatible": 10}):
        raise ValueError(f"unexpected gold relations: {dict(relations)}")
    exposures = Counter(
        bool(case.frozen_evidence.expected_rule_exposure)
        for case in cases
        if case.frozen_evidence
    )
    if exposures != Counter({True: 70, False: 30}):
        raise ValueError(f"unexpected exposure scope: {dict(exposures)}")
    if any(case.formal_environment_write_allowed for case in cases):
        raise ValueError("formal environment writeback must remain disabled")
    for case in cases:
        fixture = case.frozen_evidence
        if fixture is None:
            continue
        if case.target_agent != "expert_agent" or case.owner_step_id != "expert":
            raise ValueError(f"invalid target/owner contract for {case.case_id}")
        if case.expected_rerun_step_ids != ("expert", "audit"):
            raise ValueError(f"invalid rerun contract for {case.case_id}")
        if fixture.gold_relation == "contradiction":
            if not fixture.synthetic_fault or not fixture.expected_rule_exposure:
                raise ValueError(f"target fixture scope mismatch for {case.case_id}")
            if case.expected_task_type != "knowledge_explanation":
                raise ValueError(f"target task type mismatch for {case.case_id}")
        elif fixture.gold_relation == "compatible":
            if case.case_group != "targeting_negative_control":
                raise ValueError(f"compatible fixture group mismatch for {case.case_id}")
            if not fixture.expected_rule_exposure:
                raise ValueError(f"compatible boundary must expose B for {case.case_id}")
            if case.expected_task_type != "knowledge_explanation":
                raise ValueError(f"compatible task type mismatch for {case.case_id}")
        elif fixture.gold_relation == "not_applicable":
            if case.case_group != "non_regression_control":
                raise ValueError(f"normal fixture group mismatch for {case.case_id}")
            if fixture.expected_rule_exposure:
                raise ValueError(f"normal control must not expose B for {case.case_id}")
        else:
            raise ValueError(f"unknown gold relation for {case.case_id}")
        if fixture.evidence_a_id == fixture.evidence_b_id:
            raise ValueError(f"evidence IDs must be distinct for {case.case_id}")
    canonical = [
        {
            "case_id": case.case_id,
            "case_group": case.case_group,
            "expected_task_type": case.expected_task_type,
            "target_agent": case.target_agent,
            "owner_step_id": case.owner_step_id,
            "expected_rule_exposure": bool(
                case.frozen_evidence.expected_rule_exposure
            ),
            "input_digest": None,
        }
        for case in cases
    ]
    canonical_digest = hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "dataset_id": "semantic_conflict_closure_ab100_v2_20260901",
        "case_count": 100,
        "paired_model_run_count": 200,
        "case_groups": dict(groups),
        "pair_orders": dict(orders),
        "gold_relations": dict(relations),
        "b_rule_exposure_scope": {"exposed": exposures[True], "not_exposed": exposures[False]},
        "formal_environment_write_allowed": False,
        "canonical_case_digest": canonical_digest,
    }


class D1AB100V2Runner(D1PrecheckRunner):
    def __init__(self, cases: list[D1PilotCase] | None = None) -> None:
        self.cases = cases or load_d1_ab100_v2_cases()
        validate_d1_ab100_v2_cases(self.cases)
        self._by_id = {case.case_id: case for case in self.cases}


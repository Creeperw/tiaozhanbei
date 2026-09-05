from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path


D1_RULE_ID = "semantic_conflict_pair_closure_v1"
D1_PILOT_DATASET = (
    Path(__file__).resolve().parents[3]
    / "evaluation"
    / "evolution"
    / "datasets"
    / "semantic_conflict_closure_pilot5_20260819.jsonl"
)
_ALLOWED_GROUPS = frozenset({"target_fault", "targeting_negative_control", "non_regression_control"})
_ALLOWED_ORDERS = frozenset({"AB", "BA"})
_FORBIDDEN_PROMPT_TOKENS = (
    D1_RULE_ID,
    "D1",
    "EvidencePack",
    "evidence_id",
    "提示词",
    "Schema",
    "规则曝光",
)


@dataclass(frozen=True)
class D1FrozenEvidence:
    """Evaluation-only, immutable evidence pair and its gold relation."""

    fixture_version: str
    claim_id: str
    claim_text: str
    evidence_a_id: str
    evidence_a_text: str
    evidence_b_id: str
    evidence_b_text: str
    gold_relation: str
    relation_dimension: str
    expected_rule_exposure: bool
    synthetic_fault: bool
    annotation_method: str
    annotation_status: str
    gold_rationale: str
    fixture_digest: str


@dataclass(frozen=True)
class D1PilotCase:
    case_id: str
    case_group: str
    prompt: str
    expected_task_type: str
    pair_order: str
    target_rule: str
    target_agent: str
    owner_step_id: str
    expected_rerun_step_ids: tuple[str, ...]
    scenario: str
    formal_environment_write_allowed: bool
    frozen_evidence: D1FrozenEvidence | None = None


@dataclass(frozen=True)
class D1PilotSummary:
    case_count: int
    case_groups: dict[str, int]
    pair_orders: dict[str, int]
    target_rule_exposure_agent: str


def d1_case_from_raw(raw: dict[str, object]) -> D1PilotCase:
    frozen_raw = raw.get("frozen_evidence")
    frozen = (
        D1FrozenEvidence(
            fixture_version=str(frozen_raw["fixture_version"]),
            claim_id=str(frozen_raw["claim_id"]),
            claim_text=str(frozen_raw["claim_text"]),
            evidence_a_id=str(frozen_raw["evidence_a_id"]),
            evidence_a_text=str(frozen_raw["evidence_a_text"]),
            evidence_b_id=str(frozen_raw["evidence_b_id"]),
            evidence_b_text=str(frozen_raw["evidence_b_text"]),
            gold_relation=str(frozen_raw["gold_relation"]),
            relation_dimension=str(frozen_raw["relation_dimension"]),
            expected_rule_exposure=bool(frozen_raw["expected_rule_exposure"]),
            synthetic_fault=bool(frozen_raw["synthetic_fault"]),
            annotation_method=str(frozen_raw["annotation_method"]),
            annotation_status=str(frozen_raw["annotation_status"]),
            gold_rationale=str(frozen_raw["gold_rationale"]),
            fixture_digest=str(frozen_raw["fixture_digest"]),
        )
        if isinstance(frozen_raw, dict)
        else None
    )
    return D1PilotCase(
        case_id=str(raw["case_id"]),
        case_group=str(raw["case_group"]),
        prompt=str(raw["prompt"]),
        expected_task_type=str(raw["expected_task_type"]),
        pair_order=str(raw["pair_order"]),
        target_rule=str(raw["target_rule"]),
        target_agent=str(raw["target_agent"]),
        owner_step_id=str(raw["owner_step_id"]),
        expected_rerun_step_ids=tuple(str(x) for x in raw["expected_rerun_step_ids"]),
        scenario=str(raw["scenario"]),
        formal_environment_write_allowed=bool(raw["formal_environment_write_allowed"]),
        frozen_evidence=frozen,
    )


def load_d1_pilot_cases(path: Path = D1_PILOT_DATASET) -> list[D1PilotCase]:
    cases: list[D1PilotCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            cases.append(d1_case_from_raw(raw))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid D1 pilot case at line {line_number}") from exc
    return cases


def validate_d1_pilot_cases(cases: list[D1PilotCase]) -> D1PilotSummary:
    if len(cases) != 5:
        raise ValueError(f"expected 5 cases, got {len(cases)}")
    case_ids = [case.case_id for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("case_id values must be unique")
    if any(not item.startswith("EVO-D1-PRE-") for item in case_ids):
        raise ValueError("case_id must use the fresh EVO-D1-PRE prefix")
    if any(case.case_group not in _ALLOWED_GROUPS for case in cases):
        raise ValueError("case_group is not allowed")
    if any(case.pair_order not in _ALLOWED_ORDERS for case in cases):
        raise ValueError("pair_order is not allowed")
    if any(case.target_rule != D1_RULE_ID for case in cases):
        raise ValueError("all pilot cases must declare the D1 target rule")
    if any(case.target_agent != "expert_agent" for case in cases):
        raise ValueError("D1 target agent must be expert_agent")
    if any(case.owner_step_id != "expert" for case in cases):
        raise ValueError("pilot cases must use the expert owner for this precheck")
    if any(case.expected_rerun_step_ids != ("expert", "audit") for case in cases):
        raise ValueError("pilot cases must use the expert minimal rerun chain")
    if any(not case.prompt.strip() for case in cases):
        raise ValueError("prompt must not be empty")
    if any(any(token in case.prompt for token in _FORBIDDEN_PROMPT_TOKENS) for case in cases):
        raise ValueError("prompt contains an internal evaluation token")
    if any(case.formal_environment_write_allowed for case in cases):
        raise ValueError("formal environment writes must remain disabled")
    groups = dict(Counter(case.case_group for case in cases))
    if groups != {"target_fault": 3, "targeting_negative_control": 1, "non_regression_control": 1}:
        raise ValueError(f"unexpected case groups: {groups}")
    orders = dict(Counter(case.pair_order for case in cases))
    if orders != {"AB": 3, "BA": 2}:
        raise ValueError(f"unexpected pair orders: {orders}")
    return D1PilotSummary(
        case_count=len(cases),
        case_groups=groups,
        pair_orders=orders,
        target_rule_exposure_agent="expert_agent",
    )


def main() -> None:
    summary = validate_d1_pilot_cases(load_d1_pilot_cases())
    print("D1 pilot dataset valid")
    print(summary)


if __name__ == "__main__":
    main()

from pathlib import Path

import pytest

from competition_app.evaluation.d1_precheck_dataset import (
    D1_PILOT_DATASET,
    load_d1_pilot_cases,
    validate_d1_pilot_cases,
)


def test_d1_pilot_fixture_has_five_fresh_cases_with_balanced_pair_order():
    cases = load_d1_pilot_cases(D1_PILOT_DATASET)
    summary = validate_d1_pilot_cases(cases)

    assert summary.case_count == 5
    assert summary.case_groups == {
        "target_fault": 3,
        "targeting_negative_control": 1,
        "non_regression_control": 1,
    }
    assert summary.pair_orders == {"AB": 3, "BA": 2}
    assert summary.target_rule_exposure_agent == "expert_agent"


def test_d1_pilot_prompts_do_not_leak_rule_or_internal_protocol():
    cases = load_d1_pilot_cases(D1_PILOT_DATASET)

    forbidden = (
        "semantic_conflict_pair_closure",
        "D1",
        "EvidencePack",
        "evidence_id",
        "提示词",
        "Schema",
        "规则曝光",
    )
    for case in cases:
        assert not any(token in case.prompt for token in forbidden), case.case_id
        assert case.formal_environment_write_allowed is False


def test_d1_pilot_rejects_duplicate_case_ids_and_unknown_group(tmp_path: Path):
    source = D1_PILOT_DATASET.read_text(encoding="utf-8")
    rows = source.splitlines()
    rows[1] = rows[0]
    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text("\n".join(rows) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="case_id"):
        validate_d1_pilot_cases(load_d1_pilot_cases(duplicate))

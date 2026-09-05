import json

from competition_app.evaluation.d1_ab100_runner import (
    D1AB100Runner,
    load_d1_ab100_cases,
    validate_d1_ab100_cases,
)
from competition_app.evaluation.d1_formal_runner import FORMAL_DATASET
from evaluation.evolution.build_d1_ab100_dataset import DATASET


def _rows(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_ab100_preserves_frozen_ab50_and_appends_balanced_extension():
    old_rows = _rows(FORMAL_DATASET)
    new_rows = _rows(DATASET)

    assert len(new_rows) == 100
    assert new_rows[:50] == old_rows
    assert [row["case_id"] for row in new_rows[50:]] == [
        f"EVO-D1-AB100-{index:03d}" for index in range(51, 101)
    ]
    assert all(
        row["dataset_partition"] == "extension_20260826"
        for row in new_rows[50:]
    )


def test_ab100_runner_contract_is_balanced_and_isolated():
    cases = load_d1_ab100_cases()
    summary = validate_d1_ab100_cases(cases)

    assert summary["case_count"] == 100
    assert summary["paired_model_run_count"] == 200
    assert summary["case_groups"] == {
        "target_fault": 60,
        "non_regression_control": 30,
        "targeting_negative_control": 10,
    }
    assert summary["pair_orders"] == {"AB": 50, "BA": 50}
    assert summary["target_scenarios"] == {
        "expert_conflict_pair_resolvable": 20,
        "expert_conflict_pair_residual_after_repair": 20,
        "expert_conflict_pair_expanded_rerun": 20,
    }
    assert all(case.formal_environment_write_allowed is False for case in cases)


def test_ab100_runner_keeps_rule_disabled_for_negative_control():
    runner = D1AB100Runner()
    target = runner.prepare_pair("EVO-D1-AB100-051")
    negative = runner.prepare_pair("EVO-D1-AB100-096")

    assert [arm.rule_enabled for arm in target.arms] == [False, True]
    assert target.arms[0].input_digest == target.arms[1].input_digest
    assert all(arm.rule_enabled is False for arm in negative.arms)


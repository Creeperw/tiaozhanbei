from competition_app.evaluation.d1_formal_runner import (
    D1FormalRunner,
    load_d1_formal_cases,
    validate_d1_formal_cases,
)


def test_formal_dataset_is_frozen_and_balanced():
    cases = load_d1_formal_cases()
    summary = validate_d1_formal_cases(cases)
    assert summary["case_count"] == 50
    assert summary["paired_model_run_count"] == 100
    assert summary["case_groups"] == {
        "target_fault": 30,
        "non_regression_control": 15,
        "targeting_negative_control": 5,
    }
    assert summary["pair_orders"] == {"AB": 25, "BA": 25}
    assert all(case.formal_environment_write_allowed is False for case in cases)


def test_formal_runner_prepares_only_the_target_arm_for_fault_cases():
    runner = D1FormalRunner()
    target = runner.prepare_pair("EVO-D1-AB50-001")
    negative = runner.prepare_pair("EVO-D1-AB50-046")

    assert [arm.arm for arm in target.arms] == ["A", "B"]
    assert [arm.rule_enabled for arm in target.arms] == [False, True]
    assert target.arms[0].input_digest == target.arms[1].input_digest
    assert all(arm.rule_enabled is False for arm in negative.arms)

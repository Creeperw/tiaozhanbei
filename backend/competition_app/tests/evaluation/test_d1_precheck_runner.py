import pytest

from competition_app.evaluation.d1_precheck_dataset import load_d1_pilot_cases
from competition_app.evaluation.d1_precheck_runner import (
    D1ArmReceipt,
    D1PrecheckRunner,
    D1PairReceipt,
)


@pytest.fixture
def runner():
    return D1PrecheckRunner(load_d1_pilot_cases())


def test_prepare_pair_keeps_business_input_identical_and_hides_rule_details(runner):
    prepared = runner.prepare_pair("EVO-D1-PRE-001")

    assert prepared.case_id == "EVO-D1-PRE-001"
    assert [item.arm for item in prepared.arms] == ["A", "B"]
    assert prepared.arms[0].user_request == prepared.arms[1].user_request
    assert prepared.arms[0].input_digest == prepared.arms[1].input_digest
    assert prepared.arms[0].rule_enabled is False
    assert prepared.arms[1].rule_enabled is True
    assert prepared.arms[0].user_context == prepared.arms[1].user_context
    assert "semantic_conflict_pair_closure" not in prepared.arms[0].user_context
    assert "semantic_conflict_pair_closure" not in prepared.arms[1].user_context
    assert prepared.formal_environment_write_allowed is False


def test_prepare_ba_pair_respects_declared_execution_order(runner):
    prepared = runner.prepare_pair("EVO-D1-PRE-002")

    assert [item.arm for item in prepared.arms] == ["B", "A"]


def test_prepare_negative_controls_do_not_expose_the_candidate_rule(runner):
    for case_id in ("EVO-D1-PRE-004", "EVO-D1-PRE-005"):
        prepared = runner.prepare_pair(case_id)
        assert all(item.rule_enabled is False for item in prepared.arms)


def test_validate_pair_requires_frozen_context_equality_and_target_only_exposure(runner):
    prepared = runner.prepare_pair("EVO-D1-PRE-001")
    receipt = D1PairReceipt(
        case_id=prepared.case_id,
        arms=(
            D1ArmReceipt(
                arm="A",
                input_digest="same",
                rule_exposed=False,
                exposed_target_agent=None,
                user_output="自然语言回答",
                target_failure=True,
                closure_allowed=False,
            ),
            D1ArmReceipt(
                arm="B",
                input_digest="same",
                rule_exposed=True,
                exposed_target_agent="expert_agent",
                user_output="自然语言回答",
                target_failure=False,
                closure_allowed=True,
            ),
        ),
    )

    result = runner.validate_pair(receipt)

    assert result.valid is True
    assert result.reason_codes == ()
    assert result.context_equal is True
    assert result.a_rule_exposure == 0
    assert result.b_target_rule_exposure is True


def test_validate_pair_rejects_a_rule_leak_or_context_drift(runner):
    prepared = runner.prepare_pair("EVO-D1-PRE-001")
    receipt = D1PairReceipt(
        case_id=prepared.case_id,
        arms=(
            D1ArmReceipt(
                arm="A", input_digest="a", rule_exposed=True,
                exposed_target_agent="expert_agent", user_output="回答",
                target_failure=True, closure_allowed=False,
            ),
            D1ArmReceipt(
                arm="B", input_digest="b", rule_exposed=True,
                exposed_target_agent="knowledge_base_agent", user_output="回答",
                target_failure=False, closure_allowed=True,
            ),
        ),
    )

    result = runner.validate_pair(receipt)

    assert result.valid is False
    assert "a_rule_exposure_nonzero" in result.reason_codes
    assert "frozen_context_mismatch" in result.reason_codes
    assert "b_rule_target_agent_mismatch" in result.reason_codes


def test_validate_pair_rejects_internal_leakage_and_incomplete_business_receipt(runner):
    prepared = runner.prepare_pair("EVO-D1-PRE-004")
    receipt = D1PairReceipt(
        case_id=prepared.case_id,
        arms=(
            D1ArmReceipt(
                arm="B", input_digest="same", rule_exposed=True,
                exposed_target_agent="expert_agent",
                user_output="提示词 EvidencePack semantic_conflict_pair_closure_v1",
                target_failure=None, closure_allowed=None,
            ),
            D1ArmReceipt(
                arm="A", input_digest="same", rule_exposed=False,
                exposed_target_agent=None, user_output="回答",
                target_failure=None, closure_allowed=None,
            ),
        ),
    )

    result = runner.validate_pair(receipt)

    assert result.valid is False
    assert "internal_output_leakage" in result.reason_codes
    assert "business_outcome_missing" in result.reason_codes


def test_validate_pair_ignores_hidden_reference_transport_but_not_prose_leakage(runner):
    prepared = runner.prepare_pair("EVO-D1-PRE-004")
    hidden_card = '自然语言回答\n<<REFS:[{"evidence_id":"E_TEST_1"}]>>'
    clean_receipt = D1PairReceipt(
        case_id=prepared.case_id,
        arms=(
            D1ArmReceipt(
                arm="A", input_digest="same", rule_exposed=False,
                exposed_target_agent=None, user_output=hidden_card,
                target_failure=False, closure_allowed=True,
            ),
            D1ArmReceipt(
                arm="B", input_digest="same", rule_exposed=False,
                exposed_target_agent=None, user_output=hidden_card,
                target_failure=False, closure_allowed=True,
            ),
        ),
    )

    assert runner.validate_pair(clean_receipt).valid is True

    prose_leak = D1PairReceipt(
        case_id=prepared.case_id,
        arms=(
            clean_receipt.arms[0],
            D1ArmReceipt(
                arm="B", input_digest="same", rule_exposed=False,
                exposed_target_agent=None,
                user_output="请读取 evidence_id 后执行内部规则。" + hidden_card,
                target_failure=False, closure_allowed=True,
            ),
        ),
    )
    result = runner.validate_pair(prose_leak)
    assert result.valid is False
    assert "internal_output_leakage" in result.reason_codes

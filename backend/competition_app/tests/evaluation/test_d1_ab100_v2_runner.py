from __future__ import annotations

import pytest

from competition_app.evaluation.d1_ab100_v2_runner import (
    D1AB100V2Runner,
    load_d1_ab100_v2_cases,
    validate_d1_ab100_v2_cases,
)
from competition_app.evaluation.d1_precheck_execution import (
    D1ExecutionDependencies,
    D1PrecheckExecutionService,
)


def test_v2_contract_freezes_construct_valid_evidence():
    cases = load_d1_ab100_v2_cases()
    summary = validate_d1_ab100_v2_cases(cases)

    assert summary["case_count"] == 100
    assert summary["gold_relations"] == {
        "contradiction": 60,
        "not_applicable": 30,
        "compatible": 10,
    }
    assert summary["b_rule_exposure_scope"] == {"exposed": 70, "not_exposed": 30}
    assert len({case.frozen_evidence.fixture_digest for case in cases}) == 100
    assert len({
        evidence_id
        for case in cases
        for evidence_id in (
            case.frozen_evidence.evidence_a_id,
            case.frozen_evidence.evidence_b_id,
        )
    }) == 200


def test_v2_pair_scope_exposes_compatible_boundary_but_not_normal_task():
    runner = D1AB100V2Runner()
    target = runner.prepare_pair("EVO-D1-V2-001")
    normal = runner.prepare_pair("EVO-D1-V2-061")
    boundary = runner.prepare_pair("EVO-D1-V2-091")

    assert [arm.rule_enabled for arm in target.arms] == [False, True]
    assert all(not arm.rule_enabled for arm in normal.arms)
    assert [arm.rule_enabled for arm in boundary.arms] == [False, True]
    assert target.arms[0].input_digest == target.arms[1].input_digest
    assert boundary.arms[0].input_digest == boundary.arms[1].input_digest


@pytest.mark.asyncio
async def test_v2_execution_uses_frozen_pair_without_live_retrieval():
    class RetrievalMustNotRun:
        async def get_kp_with_content(self, *_args, **_kwargs):
            raise AssertionError("V2 fixture must not use live retrieval")

    runner = D1AB100V2Runner()
    service = D1PrecheckExecutionService(
        D1ExecutionDependencies(RetrievalMustNotRun(), object(), object()),
        runner=runner,
    )
    target = next(case for case in runner.cases if case.case_id == "EVO-D1-V2-001")
    boundary = next(case for case in runner.cases if case.case_id == "EVO-D1-V2-091")

    target_pack = await service._retrieve_frozen_pack(target)
    target_pair = service._inject_isolated_conflict(target, target_pack)
    boundary_pack = await service._retrieve_frozen_pack(boundary)
    boundary_pair = service._inject_isolated_conflict(boundary, boundary_pack)

    assert [item.content_summary for item in target_pack.evidence_items] == [
        target.frozen_evidence.evidence_a_text,
        target.frozen_evidence.evidence_b_text,
    ]
    assert target_pair["binding"].evidence_pair.support_evidence_id == "E_D1V2_001_A"
    assert target_pair["binding"].evidence_pair.conflict_evidence_id == "E_D1V2_001_B"
    assert boundary_pair["binding"] is None
    assert boundary_pack.conflict_evidence == []


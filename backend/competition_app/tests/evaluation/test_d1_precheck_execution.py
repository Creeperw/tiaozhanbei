from __future__ import annotations

from uuid import uuid4

import pytest

from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.knowledge import EvidenceItem, EvidencePack
from competition_app.contracts.resource import AuditResult, ResourceClaim, ResourceDraft
from competition_app.evaluation.d1_precheck_execution import (
    D1ExecutionDependencies,
    D1PrecheckExecutionService,
)
from competition_app.evaluation.semantic_conflict_closure import (
    ConflictEvidencePair,
    SemanticConflictBinding,
)


class FakeRetrieval:
    async def get_kp_with_content(self, query: str, *, concepts=None):
        return EvidencePack(
            evidence_pack_id="EP_TEST",
            query=query,
            resolved_kp_ids=["KP_TEST"],
            evidence_items=[
                EvidenceItem(
                    evidence_id="E_TEST_1",
                    source_id="SRC_1",
                    content_summary="支持侧原始教材片段",
                    authority_level="textbook",
                    confidence=0.95,
                ),
                EvidenceItem(
                    evidence_id="E_TEST_2",
                    source_id="SRC_2",
                    content_summary="冲突侧原始参考片段",
                    authority_level="reference",
                    confidence=0.8,
                    resource_type="reference",
                ),
            ],
        )


class FakeExpert:
    async def run(self, context):
        pack = context["dependency_outputs"]["knowledge"].payload
        ids = [item.evidence_id for item in pack.evidence_items]
        if context.get("repair_instruction"):
            cited = ids[:2]
        else:
            cited = ids[:2] if context.get("evolution_strategies") else ids[:1]
        draft = ResourceDraft(
            resource_draft_id=f"RD_{uuid4().hex}",
            title="测试讲解",
            content={"body": "仅用于隔离评测的自然语言讲解"},
            estimated_minutes=10,
            claims=[
                ResourceClaim(
                    claim_id="CLAIM_1",
                    text="测试声明",
                    evidence_ids=cited,
                )
            ],
        )
        return _envelope(context, "expert_agent", "resource_draft", draft)


class FakeAudit:
    async def run(self, context):
        expert = context["dependency_outputs"]["expert"].payload
        decision = "pass" if len(expert.claims[0].evidence_ids) >= 2 else "revise"
        result = AuditResult(
            audit_result_id=f"AR_{uuid4().hex}",
            decision=decision,
            audit_report="测试审核",
            findings=[] if decision == "pass" else ["冲突证据对未闭环"],
        )
        return _envelope(context, "audit_agent", "audit_result", result)


class SequenceExpert:
    """Cite the conflict pair only after a configurable repair round."""

    def __init__(self, resolve_after_by_arm: dict[str, int]) -> None:
        self.resolve_after_by_arm = dict(resolve_after_by_arm)
        self.calls: list[tuple[str, int, bool]] = []
        self._arm_rounds: dict[str, int] = {"A": 0, "B": 0}

    async def run(self, context):
        arm = _arm_from_context(context)
        is_repair = bool(context.get("repair_instruction"))
        if is_repair:
            self._arm_rounds[arm] += 1
        round_number = self._arm_rounds[arm]
        self.calls.append((arm, round_number, is_repair))
        pack = context["dependency_outputs"]["knowledge"].payload
        ids = [item.evidence_id for item in pack.evidence_items]
        threshold = self.resolve_after_by_arm[arm]
        cited = ids[:2] if round_number >= threshold else ids[:1]
        draft = ResourceDraft(
            resource_draft_id=f"RD_{uuid4().hex}",
            title="序列测试讲解",
            content={"body": "用于验证有限返修状态机"},
            estimated_minutes=10,
            claims=[ResourceClaim(claim_id="CLAIM_1", text="测试声明", evidence_ids=cited)],
        )
        return _envelope(context, "expert_agent", "resource_draft", draft)


class SequenceAudit(FakeAudit):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, context):
        self.calls.append(_arm_from_context(context))
        return await super().run(context)


def _envelope(context, producer, artifact_type, payload):
    return AgentEnvelope(
        artifact_id=f"ART_{uuid4().hex}",
        artifact_type=artifact_type,
        case_id=context["case_id"],
        trace_id=context["trace_id"],
        request_id=context["request_id"],
        execution_id=context["execution_id"],
        step_id=context["step_id"],
        producer=producer,
        task_type=context["task_type"],
        learner_id=context["learner_id"],
        payload=payload,
    )


def _arm_from_context(context) -> str:
    parts = str(context["thread_id"]).split("_")
    return next(item for item in parts if item in {"A", "B"})


async def _execute_sequence(resolve_after_by_arm: dict[str, int]):
    expert = SequenceExpert(resolve_after_by_arm)
    audit = SequenceAudit()
    service = D1PrecheckExecutionService(
        D1ExecutionDependencies(FakeRetrieval(), expert, audit)
    )
    result = await service.execute_pair("EVO-D1-PRE-001", learner_id="learner-1")
    return result, expert, audit


@pytest.mark.asyncio
async def test_d1_execution_freezes_pack_and_closes_target_fault():
    service = D1PrecheckExecutionService(
        D1ExecutionDependencies(FakeRetrieval(), FakeExpert(), FakeAudit())
    )

    result = await service.execute_pair("EVO-D1-PRE-001", learner_id="learner-1")

    assert result["formal_environment_write_allowed"] is False
    assert result["validation"]["valid"] is True
    assert result["validation"]["context_equal"] is True
    assert result["arms"]["A"]["target_failure"] is True
    # A is the unchanged baseline. D1 is a no-op there, so the receipt must
    # not be interpreted as a production block; the defect is still recorded
    # for the A/B effect comparison.
    assert result["arms"]["A"]["closure_allowed"] is True
    assert result["arms"]["B"]["rule_exposed"] is True
    assert result["arms"]["B"]["exposed_target_agent"] == "expert_agent"
    assert result["arms"]["B"]["closure_allowed"] is True
    assert result["arms"]["B"]["first_audit_decision"] == "pass"
    assert result["arms"]["B"]["final_audit_decision"] == "pass"
    assert result["arms"]["B"]["repair_count"] == 0
    assert result["arms"]["B"]["repair_attempt_count"] == 0
    assert result["arms"]["B"]["actual_rerun_step_ids"] == []
    assert result["arms"]["B"]["release_allowed"] is True


@pytest.mark.asyncio
async def test_d1_execution_keeps_negative_control_baseline_only():
    service = D1PrecheckExecutionService(
        D1ExecutionDependencies(FakeRetrieval(), FakeExpert(), FakeAudit())
    )

    result = await service.execute_pair("EVO-D1-PRE-004", learner_id="learner-1")

    assert result["validation"]["valid"] is True
    assert all(not arm["rule_exposed"] for arm in result["arms"].values())
    assert all(arm["actual_rerun_step_ids"] == [] for arm in result["arms"].values())


@pytest.mark.asyncio
async def test_d1_does_not_allow_closure_when_final_audit_is_not_pass():
    class HumanReviewAudit(FakeAudit):
        async def run(self, context):
            result = await super().run(context)
            result.payload.decision = "needs_human_review"
            return result

    service = D1PrecheckExecutionService(
        D1ExecutionDependencies(FakeRetrieval(), FakeExpert(), HumanReviewAudit())
    )
    result = await service.execute_pair("EVO-D1-PRE-001", learner_id="learner-1")

    assert result["arms"]["B"]["same_conflict_pair_resolved"] is True
    assert result["arms"]["B"]["audit_decision"] == "needs_human_review"
    assert result["arms"]["B"]["closure_allowed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolve_after", "expected_count", "expected_attempts", "expected_exhausted"),
    [
        (0, 0, 0, False),
        (1, 1, 1, False),
        (2, 2, 2, False),
        (3, 3, 2, True),
    ],
)
async def test_d1_bounded_repair_paths_are_symmetric(
    resolve_after,
    expected_count,
    expected_attempts,
    expected_exhausted,
):
    result, expert, audit = await _execute_sequence(
        {"A": resolve_after, "B": resolve_after}
    )

    assert result["validation"]["valid"] is True
    assert result["validation"]["repair_contract_valid"] is True
    for arm in ("A", "B"):
        value = result["arms"][arm]
        assert value["repair_count"] == expected_count
        assert value["repair_attempt_count"] == expected_attempts
        assert value["repair_exhausted"] is expected_exhausted
        assert value["max_repair_attempts"] == 2
        assert value["first_audit_decision"] == ("pass" if resolve_after == 0 else "revise")
        assert value["final_audit_decision"] == ("revise" if expected_exhausted else "pass")
        assert value["actual_rerun_step_ids"] == (
            [] if expected_count == 0 else ["expert", "audit"]
        )
        assert value["release_allowed"] is (not expected_exhausted)
    assert [item[0] for item in expert.calls].count("A") == 1 + expected_attempts
    assert [item[0] for item in expert.calls].count("B") == 1 + expected_attempts
    assert audit.calls.count("A") == 1 + expected_attempts
    assert audit.calls.count("B") == 1 + expected_attempts


@pytest.mark.asyncio
async def test_d1_paired_arms_use_the_same_production_generation_protocol():
    class CapturingExpert(FakeExpert):
        def __init__(self) -> None:
            self.protocols: list[tuple[str, bool, bool, bool]] = []

        async def run(self, context):
            self.protocols.append((
                _arm_from_context(context),
                bool(context.get("d1_evaluation_mode")),
                bool(context.get("d1_conflict_binding")),
                bool(context.get("evolution_strategies")),
            ))
            return await super().run(context)

    expert = CapturingExpert()
    service = D1PrecheckExecutionService(
        D1ExecutionDependencies(FakeRetrieval(), expert, FakeAudit())
    )

    await service.execute_pair("EVO-D1-PRE-001", learner_id="learner-1")

    assert expert.protocols
    assert all(evaluation_mode for _, evaluation_mode, _, _ in expert.protocols)
    initial_by_arm: dict[str, tuple[bool, bool]] = {}
    for arm, _, has_binding, has_strategy in expert.protocols:
        initial_by_arm.setdefault(arm, (has_binding, has_strategy))
    assert initial_by_arm["A"] == (False, False)
    assert initial_by_arm["B"] == (True, True)


def test_d1_audit_context_is_blind_to_candidate_and_repair_metadata():
    treatment_keys = {
        "evolution_strategies": [{"rule_id": "candidate"}],
        "d1_evaluation_mode": True,
        "d1_conflict_binding": {"issue_id": "D1_TEST"},
        "repair_instruction": {"instruction": "candidate repair"},
        "audit_feedback": {"decision": "revise"},
        "previous_step_output": {"body": "previous"},
    }
    base = {
        **treatment_keys,
        "dependency_outputs": {"knowledge": "frozen-pack"},
        "step_id": "expert",
    }

    context = D1PrecheckExecutionService._audit_context(
        base,
        pack=object(),
        expert="candidate-output",
    )

    assert all(key not in context for key in treatment_keys)
    assert context["step_id"] == "audit"
    assert context["dependency_outputs"] == {
        "knowledge": "frozen-pack",
        "expert": "candidate-output",
    }


def test_reference_card_ids_do_not_count_as_conflict_claim_binding():
    pack = EvidencePack(
        evidence_pack_id="EP_TEST",
        query="测试冲突",
        evidence_items=[
            EvidenceItem(
                evidence_id="E_TEST_1",
                source_id="SRC_1",
                content_summary="支持侧",
                authority_level="textbook",
                confidence=0.95,
            ),
            EvidenceItem(
                evidence_id="E_TEST_2",
                source_id="SRC_2",
                content_summary="冲突侧",
                authority_level="reference",
                confidence=0.8,
            ),
        ],
    )
    pair = SemanticConflictBinding(
        issue_id="D1_TEST",
        claim_location="resource:claims",
        evidence_pair=ConflictEvidencePair(
            support_evidence_id="E_TEST_1",
            conflict_evidence_id="E_TEST_2",
        ),
        owner_step_id="expert",
    )
    draft = ResourceDraft(
        resource_draft_id="RD_TEST",
        title="测试讲解",
        content={
            "body": (
                "正文没有形成同一声明的双证据绑定。\n"
                "<<REFS:[{\"evidence_id\":\"E_TEST_1\"},"
                "{\"evidence_id\":\"E_TEST_2\"}]>>"
            )
        },
        estimated_minutes=10,
        claims=[
            ResourceClaim(
                claim_id="CLAIM_1",
                text="只绑定支持侧的声明",
                evidence_ids=["E_TEST_1"],
            )
        ],
    )
    audit = AuditResult(
        audit_result_id="AR_TEST",
        decision="pass",
        audit_report="测试审核",
        findings=[],
    )

    state = D1PrecheckExecutionService._arm_state(
        type("ExpertEnvelope", (), {"payload": draft})(),
        type("AuditEnvelope", (), {"payload": audit})(),
        pack,
        pair,
    )

    assert state["same_conflict_pair_resolved"] is False
    assert state["target_failure"] is True

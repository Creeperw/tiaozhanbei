from __future__ import annotations

from types import SimpleNamespace

import pytest

from competition_app.agents.common import envelope
from competition_app.contracts.evolution import (
    EvolutionRule,
    EvolutionRuleContract,
)
from competition_app.contracts.knowledge import (
    EvidenceItem,
    EvidencePack,
    RetrievalSummaryItem,
)
from competition_app.contracts.resource import AuditResult, ResourceClaim, ResourceDraft
from competition_app.evaluation.accountability_faults import (
    AccountabilityEvaluationService,
    AccountabilityFaultController,
    FaultSpec,
    additional_unique_accountability_fault_specs,
    accountability_fault_specs,
    evolution_effect_fault_specs,
)
from competition_app.repositories.evolution import InMemoryEvolutionRepository
from competition_app.runtime.model_trace import ModelTraceRecorder


def _context(thread_id: str, step_id: str) -> dict:
    return {
        "case_id": "CASE_TEST",
        "trace_id": "TRACE_TEST",
        "request_id": "REQUEST_TEST",
        "execution_id": "EXECUTION_TEST",
        "thread_id": thread_id,
        "step_id": step_id,
        "learner_id": "LEARNER_TEST",
        "task_type": "knowledge_explanation",
    }


def _pack(context: dict):
    return envelope(
        context,
        "knowledge_base_agent",
        "evidence_pack",
        EvidencePack(
            evidence_pack_id="PACK_TEST",
            query="四君子汤",
            evidence_items=[
                EvidenceItem(
                    evidence_id="EVIDENCE_REAL",
                    source_id="TEXTBOOK_REAL",
                    content_summary="四君子汤由人参、白术、茯苓、甘草组成。",
                    authority_level="textbook",
                    confidence=1.0,
                )
            ],
            summary_items=[
                RetrievalSummaryItem(
                    evidence_id="EVIDENCE_REAL",
                    source_id="TEXTBOOK_REAL",
                    source_label="《中医学基础》· 四君子汤",
                    content="四君子汤由人参、白术、茯苓、甘草组成。",
                )
            ],
            summary_evidence_ids=["EVIDENCE_REAL"],
        ),
    )


def _draft(context: dict):
    return envelope(
        context,
        "expert_agent",
        "resource_draft",
        ResourceDraft(
            resource_draft_id="DRAFT_TEST",
            title="四君子汤",
            content={"body": "四君子汤由人参、白术、茯苓、甘草组成。"},
            estimated_minutes=5,
            claims=[
                ResourceClaim(
                    claim_id="CLAIM_REAL",
                    text="四君子汤由人参、白术、茯苓、甘草组成。",
                    evidence_ids=["EVIDENCE_REAL"],
                )
            ],
        ),
    )


def test_manifest_contains_original_twenty_and_unique_thirty_case_extension():
    specs = accountability_fault_specs()

    assert len(specs) == 50
    assert sum(item.fault_type == "expert_fake_evidence_id" for item in specs.values()) == 16
    assert sum(item.fault_type == "expert_evidence_contradiction" for item in specs.values()) == 15
    assert sum(item.fault_type == "knowledge_invalid_evidence_pack" for item in specs.values()) == 16
    assert sum(item.fault_type == "none" for item in specs.values()) == 3
    assert sum(item.inject_mode == "persistent" for item in specs.values()) == 2

    added = additional_unique_accountability_fault_specs()
    assert len(added) == 30
    assert all(item.case_id.startswith("ONL_FAULT_NEW30_") for item in added)
    assert len({item.case_id for item in added}) == 30
    assert len({item.topic for item in added}) == 30
    assert len({item.prompt for item in added}) == 30
    assert not {item.topic for item in added}.intersection(
        {"四君子汤", "理中丸", "八纲辨证", "五行关系", "脾主运化"}
    )


def test_evolution_effect_manifest_loads_fifty_private_context_cases():
    specs = evolution_effect_fault_specs()

    assert len(specs) == 50
    assert set(specs) == {f"EVO_AB50_{index:03d}" for index in range(1, 51)}
    assert all(item.evaluation_kind == "evolution_effect" for item in specs.values())
    assert sum(item.case_group == "target_fault" for item in specs.values()) == 30
    assert sum(item.case_group == "non_regression_control" for item in specs.values()) == 15
    assert sum(item.case_group == "targeting_negative_control" for item in specs.values()) == 5
    assert len({item.prompt for item in specs.values()}) == 50


def test_evolution_context_mutation_is_knowledge_only_and_records_post_mutation_allowlist():
    controller = AccountabilityFaultController()
    spec = FaultSpec(
        case_id="EVO_AB50_UNIT_STALE",
        topic="四君子汤",
        prompt="整理学习资源",
        fault_type="none",
        evaluation_kind="evolution_effect",
        context_operator="stale_removed_reference",
        case_group="target_fault",
        pair_order="AB",
    )
    knowledge_context = _context("THREAD_EVOLUTION", "knowledge")
    expert_context = _context("THREAD_EVOLUTION", "expert")
    controller.register("THREAD_EVOLUTION", spec)

    source_pack = _pack(knowledge_context)
    mutated_pack = controller.apply(
        "knowledge_base_agent", knowledge_context, source_pack
    )
    expert = _draft(expert_context)
    assert controller.apply("expert_agent", expert_context, expert) is expert
    records = controller.finish("THREAD_EVOLUTION")

    assert source_pack.payload.evidence_items[0].evidence_id == "EVIDENCE_REAL"
    assert mutated_pack.payload.evidence_items == []
    assert mutated_pack.payload.summary_items[0].evidence_id == "EVIDENCE_REAL"
    assert len(records) == 1
    assert records[0].allowed_evidence_ids == ()
    assert records[0].injection_eligible is True
    assert "《中医学基础》· 四君子汤" in records[0].forbidden_reference_tokens
    assert records[0].before_digest != records[0].after_digest


def test_once_expert_fault_changes_only_first_call_and_keeps_source_immutable():
    controller = AccountabilityFaultController()
    spec = FaultSpec(
        case_id="ONL_FAULT_UNIT_FAKE",
        topic="四君子汤",
        prompt="解释四君子汤",
        fault_type="expert_fake_evidence_id",
        expected_owner_step_ids=("expert",),
        expected_rerun_step_ids=("expert", "audit"),
    )
    context = _context("THREAD_FAKE", "expert")
    source = _draft(context)
    controller.register("THREAD_FAKE", spec)

    first = controller.apply("expert_agent", context, source)
    second = controller.apply("expert_agent", context, source)
    records = controller.finish("THREAD_FAKE")

    assert source.payload.claims[0].evidence_ids == ["EVIDENCE_REAL"]
    assert first.payload.claims[0].evidence_ids == [
        "EVAL_FAKE_EVIDENCE_ONL_FAULT_UNIT_FAKE"
    ]
    assert second is source
    assert len(records) == 1
    assert records[0].before_digest != records[0].after_digest


def test_persistent_fault_is_applied_on_repair_call():
    controller = AccountabilityFaultController()
    spec = FaultSpec(
        case_id="ONL_FAULT_UNIT_PERSIST",
        topic="四君子汤",
        prompt="解释四君子汤",
        fault_type="expert_fake_evidence_id",
        inject_mode="persistent",
    )
    context = _context("THREAD_PERSIST", "expert")
    controller.register("THREAD_PERSIST", spec)

    controller.apply("expert_agent", context, _draft(context))
    controller.apply("expert_agent", context, _draft(context))
    records = controller.finish("THREAD_PERSIST")

    assert [item.call_number for item in records] == [1, 2]


def test_knowledge_fault_mutates_pack_and_downstream_claim_with_same_missing_id():
    controller = AccountabilityFaultController()
    spec = FaultSpec(
        case_id="ONL_FAULT_UNIT_KNOWLEDGE",
        topic="四君子汤",
        prompt="解释四君子汤",
        fault_type="knowledge_invalid_evidence_pack",
    )
    knowledge_context = _context("THREAD_KNOWLEDGE", "knowledge")
    expert_context = _context("THREAD_KNOWLEDGE", "expert")
    controller.register("THREAD_KNOWLEDGE", spec)

    pack = controller.apply(
        "knowledge_base_agent", knowledge_context, _pack(knowledge_context)
    )
    draft = controller.apply("expert_agent", expert_context, _draft(expert_context))
    controller.finish("THREAD_KNOWLEDGE")

    missing = "EVAL_MISSING_SUMMARY_ONL_FAULT_UNIT_KNOWLEDGE"
    assert missing in pack.payload.summary_evidence_ids
    assert draft.payload.claims[0].evidence_ids == [missing]


def test_semantic_fault_appends_fixed_human_reviewed_claim_with_real_evidence():
    controller = AccountabilityFaultController()
    false_claim = "四君子汤的原方组成中不含人参。"
    spec = FaultSpec(
        case_id="ONL_FAULT_UNIT_CONTRA",
        topic="四君子汤",
        prompt="解释四君子汤",
        fault_type="expert_evidence_contradiction",
        false_claim=false_claim,
    )
    context = _context("THREAD_CONTRA", "expert")
    controller.register("THREAD_CONTRA", spec)

    mutated = controller.apply("expert_agent", context, _draft(context))
    controller.finish("THREAD_CONTRA")

    assert false_claim in mutated.payload.content["body"]
    assert mutated.payload.claims[-1].text == false_claim
    assert mutated.payload.claims[-1].evidence_ids == ["EVIDENCE_REAL"]


def test_unregistered_or_clean_threads_are_strict_passthrough():
    controller = AccountabilityFaultController()
    context = _context("THREAD_CLEAN", "expert")
    source = _draft(context)
    assert controller.apply("expert_agent", context, source) is source

    controller.register(
        "THREAD_CLEAN",
        FaultSpec(
            case_id="ONL_FAULT_UNIT_CLEAN",
            topic="四君子汤",
            prompt="解释四君子汤",
            fault_type="none",
        ),
    )
    assert controller.apply("expert_agent", context, source) is source
    assert controller.finish("THREAD_CLEAN") == []


def test_concurrent_thread_state_does_not_cross_contaminate():
    controller = AccountabilityFaultController()
    controller.register(
        "THREAD_A",
        FaultSpec(
            case_id="ONL_FAULT_UNIT_A",
            topic="四君子汤",
            prompt="解释四君子汤",
            fault_type="expert_fake_evidence_id",
        ),
    )
    controller.register(
        "THREAD_B",
        FaultSpec(
            case_id="ONL_FAULT_UNIT_B",
            topic="四君子汤",
            prompt="解释四君子汤",
            fault_type="none",
        ),
    )
    context_a = _context("THREAD_A", "expert")
    context_b = _context("THREAD_B", "expert")

    result_a = controller.apply("expert_agent", context_a, _draft(context_a))
    result_b = controller.apply("expert_agent", context_b, _draft(context_b))

    assert result_a.payload.claims[0].evidence_ids[0].startswith("EVAL_FAKE_")
    assert result_b.payload.claims[0].evidence_ids == ["EVIDENCE_REAL"]
    assert len(controller.finish("THREAD_A")) == 1
    assert controller.finish("THREAD_B") == []


@pytest.mark.asyncio
async def test_frozen_pair_changes_only_closed_strategy_and_detects_raw_failure():
    controller = AccountabilityFaultController()
    recorder = ModelTraceRecorder()
    spec = FaultSpec(
        case_id="EVO_AB50_UNIT_PAIR",
        topic="四君子汤",
        prompt="整理学习资源",
        fault_type="none",
        evaluation_kind="evolution_effect",
        context_operator="stale_removed_reference",
        case_group="target_fault",
        pair_order="AB",
    )
    repository = InMemoryEvolutionRepository()
    rule = repository.save_rule(EvolutionRule(
        rule_id="ERULE_UNIT_PAIR",
        signature_id="SIG_UNIT_PAIR",
        natural_language_analysis="引用必须限定在当前证据包。",
        contract=EvolutionRuleContract(
            signature_id="SIG_UNIT_PAIR",
            target_agent="expert_agent",
            target_step_id="expert",
            task_type="personalized_review_card",
            intervention_type="prevention",
            template_id="require_evidence_ids_from_current_pack",
            issue_type="missing_evidence",
            field_path="claims[].evidence_ids[]",
            source_case_ids=["CASE_UNIT"],
        ),
        strategy_text=(
            "在输出带证据的结论前，逐项核对所引用的 evidence_id 必须存在于本轮"
            "EvidencePack；不存在的引用不得输出，也不得臆造替代编号。"
        ),
    ))

    class SeedUseCase:
        async def execute(self, request):
            recorder.reset()
            context = _context(request.thread_id, "knowledge")
            pack = EvidencePack(
                evidence_pack_id="PACK_PAIR",
                query="四君子汤",
                evidence_items=[
                    EvidenceItem(
                        evidence_id="EVIDENCE_ALLOWED",
                        source_id="BOOK_ALLOWED",
                        source_label="《中医学基础》· 合法章节",
                        content_summary="合法内容",
                        authority_level="textbook",
                        confidence=1.0,
                    ),
                    EvidenceItem(
                        evidence_id="EVIDENCE_STALE",
                        source_id="BOOK_STALE",
                        source_label="《旧教材》· 陈旧章节",
                        content_summary="陈旧内容",
                        authority_level="textbook",
                        confidence=1.0,
                    ),
                ],
                summary_items=[
                    RetrievalSummaryItem(
                        evidence_id="EVIDENCE_ALLOWED",
                        source_id="BOOK_ALLOWED",
                        source_label="《中医学基础》· 合法章节",
                        content="合法内容",
                    ),
                    RetrievalSummaryItem(
                        evidence_id="EVIDENCE_STALE",
                        source_id="BOOK_STALE",
                        source_label="《旧教材》· 陈旧章节",
                        content="陈旧内容",
                    ),
                ],
            )
            knowledge = envelope(
                context, "knowledge_base_agent", "evidence_pack", pack
            )
            knowledge = controller.apply("knowledge_base_agent", context, knowledge)
            expert_context = _context(request.thread_id, "expert")
            expert_context["task_type"] = "personalized_review_card"
            expert_context["dependency_outputs"] = {"knowledge": knowledge}
            controller.capture_input("expert_agent", expert_context)
            audit_context = _context(request.thread_id, "audit")
            audit_context["task_type"] = "personalized_review_card"
            audit_context["dependency_outputs"] = {}
            controller.capture_input("audit_agent", audit_context)
            return SimpleNamespace(
                status="success", task_type="personalized_review_card",
                model_trace=[], resource=None, audit=None,
            )

    class FrozenExpert:
        async def run(self, context):
            enabled = bool(context.get("evolution_strategies"))
            raw_input = {
                "target_agent": "expert_agent",
                "payload": {
                    "topic": "四君子汤",
                    **(
                        {"approved_evolution_strategies": context["evolution_strategies"]}
                        if enabled else {}
                    ),
                },
            }
            index = recorder.begin("expert_agent", raw_input)
            ref = "EVIDENCE_ALLOWED" if enabled else "EVIDENCE_STALE"
            recorder.succeed(index, {
                "title": "资源",
                "content": (
                    "仅使用《中医学基础》· 合法章节"
                    if enabled else "推荐《旧教材》· 陈旧章节"
                ),
                "evidence_refs": [ref],
            })
            draft = ResourceDraft(
                resource_draft_id=f"DRAFT_{'B' if enabled else 'A'}",
                title="资源",
                content={
                    "学习支持": (
                        "仅使用《中医学基础》· 合法章节"
                        if enabled else "推荐《旧教材》· 陈旧章节"
                    )
                },
                estimated_minutes=5,
                claims=[ResourceClaim(
                    claim_id="CLAIM_PAIR",
                    text="资源说明",
                    evidence_ids=[ref],
                )],
            )
            return envelope(context, "expert_agent", "knowledge_explanation", draft)

    class FrozenAudit:
        async def run(self, context):
            return envelope(
                context,
                "audit_agent",
                "audit_result",
                AuditResult(
                    audit_result_id="AUDIT_PAIR",
                    decision="pass",
                ),
            )

    service = AccountabilityEvaluationService(
        SeedUseCase(),
        controller,
        {spec.case_id: spec},
        expert_agent=FrozenExpert(),
        audit_agent=FrozenAudit(),
        evolution_repository=repository,
        model_trace_recorder=recorder,
    )
    result = await service.execute_evolution_pair(
        spec.case_id,
        SimpleNamespace(thread_id="THREAD_PAIR"),
        rule_id=rule.rule_id,
    )

    assert result["status"] == "completed"
    assert result["frozen_context_equal"] is True
    assert result["arms"]["A"]["strategy_injected"] is False
    assert result["arms"]["B"]["strategy_injected"] is True
    assert result["arms"]["A"]["has_target_failure"] is True
    assert result["arms"]["B"]["has_target_failure"] is False
    assert result["arms"]["A"]["invalid_raw_model_evidence_refs"] == [
        "EVIDENCE_STALE"
    ]
    assert result["arms"]["A"]["forbidden_reference_mentions"] == [
        "《旧教材》· 陈旧章节"
    ]

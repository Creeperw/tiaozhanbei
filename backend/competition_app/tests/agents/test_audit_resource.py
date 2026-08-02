from types import SimpleNamespace

import pytest

from competition_app.agents.audit import AuditAgent
from competition_app.contracts.knowledge import EvidenceItem, EvidencePack
from competition_app.contracts.plan_compilation import (
    CompiledPlanContractResult,
    CompiledShortTermContract,
    PlanCompilationEnvelope,
)
from competition_app.contracts.resource import ResourceClaim, ResourceDraft
from competition_app.llm.stub import StubChatModel


async def _compile_audit_findings(role, payload, on_delta=None):
    if role != "audit_findings_compiler":
        return None
    return await StubChatModel().complete_json(role, payload, on_delta=on_delta)


class EmptyRevisionAuditModel:
    async def complete_json(self, role, payload, on_delta=None):
        compiled = await _compile_audit_findings(role, payload, on_delta)
        if compiled is not None:
            return compiled
        return {
            "decision": "revise",
            "findings": [],
            "audit_report": "建议修订，但未发现具体问题。",
        }


class ActionableRevisionAuditModel:
    async def complete_json(self, role, payload, on_delta=None):
        compiled = await _compile_audit_findings(role, payload, on_delta)
        if compiled is not None:
            return compiled
        return {
            "decision": "revise",
            "findings": [
                "配套练习题题干事实错误且缺乏证据支持。",
                "讲解内容缺失辨证分型与具体治法等核心教学要素。",
                "证据材料中存在大量无关噪声数据。",
            ],
            "audit_report": "讲解需要由内容生成节点完成一次受控修订。",
        }


class AdvisoryPlanRevisionModel:
    async def complete_json(self, role, payload, on_delta=None):
        compiled = await _compile_audit_findings(role, payload, on_delta)
        if compiled is not None:
            return compiled
        return {
            "decision": "revise",
            "findings": ["可以进一步润色第二个推进节点的表达。"],
            "audit_report": "合同已通过，但文字仍可润色。",
        }


class RejectThenPassPlanModel:
    def __init__(self):
        self.calls = 0

    async def complete_json(self, role, payload, on_delta=None):
        compiled = await _compile_audit_findings(role, payload, on_delta)
        if compiled is not None:
            return compiled
        self.calls += 1
        if self.calls == 1:
            return {
                "decision": "reject",
                "findings": ["当前推进节点与学习目标衔接不够清楚。"],
                "audit_report": "建议重新生成推进节点。",
            }
        return {
            "decision": "pass",
            "findings": [],
            "audit_report": "修订后可执行。",
        }


class RepairedPlanStillRejectedModel:
    async def complete_json(self, role, payload, on_delta=None):
        compiled = await _compile_audit_findings(role, payload, on_delta)
        if compiled is not None:
            return compiled
        return {
            "decision": "reject",
            "findings": ["还可以进一步优化学习节奏。"],
            "audit_report": "仍有可优化空间。",
        }


def _resource_context() -> dict:
    evidence = EvidencePack(
        evidence_pack_id="EVIDENCE_PACK_1",
        query="四君子汤",
        resolved_kp_ids=["KP_1"],
        evidence_items=[
            EvidenceItem(
                evidence_id="EVIDENCE_1",
                source_id="SOURCE_1",
                content_summary="四君子汤由人参、白术、茯苓和甘草组成。",
                authority_level="textbook",
                confidence=1.0,
            )
        ],
    )
    expert = ResourceDraft(
        resource_draft_id="DRAFT_1",
        title="四君子汤讲解",
        target_kp_id="KP_1",
        content={"body": "教材知识讲解"},
        estimated_minutes=10,
        claims=[
            ResourceClaim(
                claim_id="CLAIM_1",
                text="四君子汤由四味药组成。",
                evidence_ids=["EVIDENCE_1"],
            )
        ],
    )
    return {
        "case_id": "CASE_1",
        "trace_id": "TRACE_1",
        "request_id": "REQUEST_1",
        "execution_id": "EXECUTION_1",
        "step_id": "audit",
        "learner_id": "LEARNER_1",
        "task_type": "knowledge_explanation",
        "available_minutes": 15,
        "dependency_outputs": {
            "knowledge": SimpleNamespace(payload=evidence),
            "expert": SimpleNamespace(payload=expert),
        },
    }


def _short_plan_context(*, audit_feedback=None) -> dict:
    content = "【当前主目标】学习《方剂学》。【具体任务块】分两步推进。"
    contract = CompiledShortTermContract(
        scope="short_term",
        short_term_plan_content=content,
        duration_days=7,
        progression_nodes=["阅读《方剂学》", "完成《方剂学》自测"],
        expected_output="一份学习记录",
        completion_criteria="完成两个节点并通过自测",
        selected_stage_id="stage-1",
        selected_books=["《方剂学》"],
        field_anchors={
            "/short_term_plan_content": [
                {
                    "source_field": "short_term_plan_content",
                    "source_quote": content,
                }
            ]
        },
    )
    compilation = PlanCompilationEnvelope(
        result=CompiledPlanContractResult(status="compiled", contract=contract),
        source_digest="a" * 64,
    )
    proposal = SimpleNamespace(
        model_dump=lambda mode="json": {"short_term_plan_content": content}
    )
    return {
        "case_id": "CASE_SHORT",
        "trace_id": "TRACE_SHORT",
        "request_id": "REQUEST_SHORT",
        "execution_id": "EXECUTION_SHORT",
        "step_id": "audit",
        "learner_id": "LEARNER_SHORT",
        "task_type": "learning_plan",
        "plan_scope": "short_term",
        "audit_feedback": audit_feedback,
        "dependency_outputs": {
            "diagnosis": SimpleNamespace(
                payload=SimpleNamespace(
                    plan_scope="short_term",
                    requires_clarification=False,
                    learning_plan_proposal=proposal,
                    compiled_plan_contract=compilation,
                    trusted_plan_route={},
                    parent_plan_constraints={
                        "current_stage_id": "stage-1",
                        "current_stage_duration_days": 30,
                    },
                )
            )
        },
    }


@pytest.mark.asyncio
async def test_empty_model_revision_passes_when_deterministic_gates_pass() -> None:
    result = await AuditAgent(EmptyRevisionAuditModel()).run(_resource_context())

    assert result.payload.decision == "pass"
    assert result.payload.findings == []
    assert "确定性门禁均已通过" in result.payload.audit_report


@pytest.mark.asyncio
async def test_resource_revision_compiles_every_finding_to_expert_repair() -> None:
    result = await AuditAgent(ActionableRevisionAuditModel()).run(
        _resource_context()
    )

    assert result.payload.decision == "revise"
    assert len(result.payload.structured_findings) == 3
    assert {
        issue.issue_type for issue in result.payload.structured_findings
    } == {"content_quality"}
    assert all(
        issue.owner_step_id == "expert"
        and issue.affected_step_ids == ["expert"]
        for issue in result.payload.structured_findings
    )


@pytest.mark.asyncio
async def test_daily_task_audit_is_a_defensive_noop_without_resource_dependencies() -> None:
    context = {
        "case_id": "CASE_DAILY",
        "trace_id": "TRACE_DAILY",
        "request_id": "REQUEST_DAILY",
        "execution_id": "EXECUTION_DAILY",
        "step_id": "audit",
        "learner_id": "LEARNER_DAILY",
        "task_type": "learning_plan",
        "plan_scope": "daily_task",
        "dependency_outputs": {
            "diagnosis": SimpleNamespace(
                payload=SimpleNamespace(plan_scope="daily_task")
            )
        },
    }

    result = await AuditAgent(AdvisoryPlanRevisionModel()).run(context)

    assert result.payload.decision == "pass"
    assert result.payload.plan_scope == "daily_task"


@pytest.mark.asyncio
async def test_repaired_short_plan_converges_when_deterministic_contract_passes() -> None:
    context = _short_plan_context(
        audit_feedback=SimpleNamespace(findings=["上一轮建议"])
    )

    result = await AuditAgent(AdvisoryPlanRevisionModel()).run(context)

    assert result.payload.decision == "pass"
    assert result.payload.structured_findings == []
    assert result.payload.findings == ["可以进一步润色第二个推进节点的表达。"]


@pytest.mark.asyncio
async def test_initial_plan_rejection_is_routed_back_as_revision() -> None:
    context = _short_plan_context(audit_feedback=None)

    result = await AuditAgent(RejectThenPassPlanModel()).run(context)

    assert result.payload.decision == "revise"
    assert result.payload.structured_findings
    assert result.payload.structured_findings[0].owner_step_id == "diagnosis"


@pytest.mark.asyncio
async def test_repaired_plan_model_rejection_becomes_non_blocking_advice() -> None:
    context = _short_plan_context(
        audit_feedback=SimpleNamespace(findings=["上一轮审核要求重做"])
    )

    result = await AuditAgent(RepairedPlanStillRejectedModel()).run(context)

    assert result.payload.decision == "pass"
    assert result.payload.structured_findings == []
    assert result.payload.findings == ["还可以进一步优化学习节奏。"]

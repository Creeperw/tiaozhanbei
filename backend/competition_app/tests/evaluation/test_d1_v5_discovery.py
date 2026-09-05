from __future__ import annotations

from uuid import uuid4

import pytest

from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.local_repair import RepairIssue
from competition_app.contracts.resource import AuditResult, ResourceClaim, ResourceDraft
from competition_app.evaluation.d1_v5_discovery import (
    D1V5DiscoveryDataset,
    D1V5DiscoveryExecutor,
)
from competition_app.runtime.model_trace import ModelTraceRecorder


class FakeExpert:
    def __init__(self) -> None:
        self.contexts: list[dict] = []

    async def run(self, context):
        self.contexts.append(context)
        pack = context["dependency_outputs"]["knowledge"].payload
        repaired = bool(context.get("repair_instruction"))
        draft = ResourceDraft(
            resource_draft_id=f"DRAFT_{uuid4().hex}",
            title="隔离发现测试",
            content={
                "知识讲解": (
                    "材料 A 与材料 B 的说法不能同时成立，应保留材料 A 的区分。"
                    if repaired
                    else "只采用材料 B 的说法。"
                )
            },
            estimated_minutes=15,
            claims=[
                ResourceClaim(
                    claim_id="CLAIM_1",
                    text=pack.evidence_items[0].content_summary,
                    evidence_ids=[pack.evidence_items[0].evidence_id],
                )
            ],
        )
        return _envelope(context, "expert_agent", "knowledge_explanation", draft)


class FakeAudit:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, context):
        self.calls += 1
        decision = "revise" if self.calls == 1 else "pass"
        result = AuditResult(
            audit_result_id=f"AUDIT_{uuid4().hex}",
            decision=decision,
            audit_report="需要修正事实" if decision == "revise" else "通过",
            findings=["讲解包含事实错误"] if decision == "revise" else [],
            structured_findings=(
                [
                    RepairIssue(
                        issue_id="RESOURCE_MODEL_ISSUE_1",
                        issue_type="factual_error",
                        message="讲解包含事实错误",
                        origin_step_id="expert",
                        owner_step_id="expert",
                        affected_step_ids=["expert"],
                    )
                ]
                if decision == "revise"
                else []
            ),
        )
        return _envelope(context, "audit_agent", "audit_result", result)


class FakeJudge:
    async def judge(self, **kwargs):
        assert set(kwargs) == {"question", "material_a", "material_b", "answer"}
        return {"status": "judged", "acceptable": True}


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


def test_discovery_dataset_is_frozen_runtime_only():
    dataset = D1V5DiscoveryDataset()

    manifest = dataset.manifest_payload(runtime_mode="live")

    assert len(dataset.cases) == 8
    assert manifest["gold_content_loaded"] is False
    assert manifest["candidate_rules_enabled"] is False
    assert manifest["formal_environment_write_allowed"] is False


@pytest.mark.asyncio
async def test_discovery_executor_uses_production_repair_without_d1_metadata():
    expert = FakeExpert()
    audit = FakeAudit()
    executor = D1V5DiscoveryExecutor(
        expert_agent=expert,
        audit_agent=audit,
        semantic_judge=FakeJudge(),
        model_trace_recorder=ModelTraceRecorder(),
        model_name="fake",
    )

    receipt = await executor.execute_case(
        D1V5DiscoveryDataset().cases[0],
        learner_id="isolated-learner",
        attempt=1,
    )

    assert receipt["repair_triggered"] is True
    assert receipt["first_audit"]["decision"] == "revise"
    assert receipt["final_audit"]["decision"] == "pass"
    assert receipt["release_allowed"] is True
    assert receipt["learner_visible_body"]
    assert receipt["isolation_validation"]["valid"] is True
    assert audit.calls == 2
    assert len(expert.contexts) == 2
    for context in expert.contexts:
        assert "evolution_strategies" not in context
        assert "d1_evaluation_mode" not in context
        assert "d1_conflict_binding" not in context
        serialized = str(context)
        assert "D1V5-DISCOVERY-T001" not in serialized
        assert "D1V5-DISCOVERY-T001-M1" not in serialized
        assert "gold_relation" not in serialized

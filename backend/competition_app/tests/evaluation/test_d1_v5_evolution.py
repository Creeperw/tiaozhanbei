from __future__ import annotations

import asyncio
import hashlib
import json
from uuid import uuid4

import pytest

from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.local_repair import RepairIssue
from competition_app.contracts.resource import AuditResult, ResourceClaim, ResourceDraft
from competition_app.evaluation.d1_v5_evolution import (
    D1V5EvolutionDataset,
    D1V5EvolutionExecutor,
    D1V5EvolutionSandboxService,
)
from competition_app.repositories.evolution import InMemoryEvolutionRepository
from competition_app.runtime.model_trace import ModelTraceRecorder


CANDIDATE = {
    "rule_id": "ERULE_D1V5_SANDBOX_TEST",
    "version": 1,
    "template_id": "semantic_conflict_pair_closure",
    "target_agent": "expert_agent",
    "target_step_id": "expert",
    "task_type": "knowledge_explanation",
    "strategy_text": "先显式指出冲突，再说明仅凭当前材料不能裁定绝对真伪。",
}


class FakeExpert:
    def __init__(self) -> None:
        self.contexts: list[dict] = []

    async def run(self, context):
        self.contexts.append(context)
        pack = context["dependency_outputs"]["knowledge"].payload
        repaired = bool(context.get("repair_instruction"))
        treated = bool(context.get("evolution_strategies"))
        if repaired or treated:
            body = "材料 A 与材料 B 不能同时成立；仅凭当前材料不能裁定绝对真伪，应保留口径边界。"
        else:
            body = "只采用材料 B，材料 A 错误。"
        draft = ResourceDraft(
            resource_draft_id=f"DRAFT_{uuid4().hex}",
            title="V5 隔离测试",
            content={"知识讲解": body},
            estimated_minutes=10,
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
        self.contexts: list[dict] = []

    async def run(self, context):
        self.contexts.append(context)
        body = str(context["dependency_outputs"]["expert"].payload.content)
        passes = "不能同时成立" in body
        result = AuditResult(
            audit_result_id=f"AUDIT_{uuid4().hex}",
            decision="pass" if passes else "revise",
            audit_report="通过" if passes else "应显式处理冲突",
            findings=[] if passes else ["讲解越权裁定冲突材料"],
            structured_findings=(
                []
                if passes
                else [
                    RepairIssue(
                        issue_id="RESOURCE_CONFLICT_1",
                        issue_type="conflicting_evidence",
                        message="应显式处理冲突材料",
                        origin_step_id="expert",
                        owner_step_id="expert",
                        affected_step_ids=["expert"],
                    )
                ]
            ),
        )
        return _envelope(context, "audit_agent", "audit_result", result)


class FakeJudge:
    async def judge(self, **kwargs):
        assert set(kwargs) == {"question", "material_a", "material_b", "answer"}
        return {
            "status": "judged",
            "acceptable": "不能同时成立" in kwargs["answer"],
        }


class TraceAwareExpert(FakeExpert):
    def __init__(self, recorder: ModelTraceRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    async def run(self, context):
        index = self.recorder.begin("expert_agent", context)
        try:
            result = await super().run(context)
        except Exception as exc:
            self.recorder.fail(index, exc)
            raise
        self.recorder.succeed(index, result.model_dump(mode="json"))
        return result


class TraceAwareAudit(FakeAudit):
    def __init__(self, recorder: ModelTraceRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    async def run(self, context):
        index = self.recorder.begin("audit_agent", context)
        try:
            result = await super().run(context)
        except Exception as exc:
            self.recorder.fail(index, exc)
            raise
        self.recorder.succeed(index, result.model_dump(mode="json"))
        return result


class NoopEvolutionAgent:
    async def analyze(self, *args, **kwargs):
        return "重复失败显示 Expert 未闭合冲突材料。"


class FakeCandidateCompiler:
    async def compile(self, *, source_case_ids, **kwargs):
        proposal = {
            "strategy_text": (
                "先分别忠实陈述当前两份材料的核心主张，再判断它们是否能够同时成立。"
                "若同一概念、同一条件下出现直接冲突，应明确向学习者说明冲突；当前材料"
                "没有提供权威性、版本或课程口径依据时，不得擅自选边、虚构层级或借助"
                "材料外知识消解。若两份材料只是总分、因果或并列关系，则应正常整合，"
                "不得误报冲突，并始终保留当前材料能够支持的边界。"
            ),
            "mechanism_summary": "防止 Expert 在缺乏裁决依据时擅自消解直接冲突。",
            "expected_advantage": "提高直接冲突综合场景中的学习者可见语义正确率。",
            "risk_boundary": "相容材料必须继续正常整合，且不得影响其他任务类型。",
            "required_behaviors": [
                "state_each_material_claim",
                "identify_direct_conflict",
                "avoid_unsupported_authority_resolution",
                "preserve_compatible_integration",
            ],
            "source_case_ids": list(source_case_ids),
        }
        encoded = json.dumps(
            proposal,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            **proposal,
            "compiler_version": "fake-model-candidate-compiler",
            "proposal_model_name": "fake",
            "proposal_digest": hashlib.sha256(encoded).hexdigest(),
            "strategy_sha256": hashlib.sha256(
                proposal["strategy_text"].encode("utf-8")
            ).hexdigest(),
            "authorship": "experimental_model_authored_candidate",
            "hardcoded_strategy_fallback_used": False,
            "production_compiler_compatible": False,
        }


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


def _executor(*, audit_timeout_seconds: float | None = None):
    recorder = ModelTraceRecorder()
    expert = TraceAwareExpert(recorder)
    audit = TraceAwareAudit(recorder)
    return (
        D1V5EvolutionExecutor(
            expert_agent=expert,
            audit_agent=audit,
            semantic_judge=FakeJudge(),
            model_trace_recorder=recorder,
            model_name="fake",
            max_repair_attempts=1,
            audit_timeout_seconds=audit_timeout_seconds,
        ),
        expert,
        audit,
    )


def _discovery_artifact(path):
    payload = {
        "schema_version": "d1-v5-discovery-run-1.0",
        "run_id": "D1V5DISC_" + "a" * 32,
        "status": "completed",
        "dataset_id": "d1_three_stage_v5_advantage",
        "dataset_version": "5.1.0",
        "runtime_file_sha256": D1V5EvolutionDataset().manifest["files"]["d1_rule_discovery_advantage_v5_1.jsonl"],
        "technical_failure_count": 0,
        "completed_case_count": 8,
        "total_case_count": 8,
        "formal_environment_write_allowed": False,
        "receipts": [
            {
                "case_id": f"D1V5-DISCOVERY-T{index:03d}",
                "semantic_verdict": {
                    "status": "judged",
                    "acceptable": False if index <= 2 else True,
                    "rationale_code": (
                        "unsupported_resolution" if index <= 2 else "acceptable"
                    ),
                },
                "isolation_validation": {
                    "valid": True,
                    "formal_environment_write_allowed": False,
                },
            }
            for index in range(1, 9)
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload["run_id"]


@pytest.mark.asyncio
async def test_executor_uses_orchestrator_repair_and_exact_task_targeting():
    executor, expert, audit = _executor()
    dataset = D1V5EvolutionDataset()
    target = dataset.qualification[0]

    receipt = await executor.execute_pair(
        target,
        learner_id="isolated",
        candidate=CANDIDATE,
        attempt=1,
    )

    assert receipt["single_treatment_valid"] is True
    arms = {arm["arm"]: arm for arm in receipt["arms"]}
    assert arms["A"]["rule_exposed"] is False
    assert arms["A"]["repair_attempt_count"] == 1
    assert arms["A"]["actual_rerun_step_ids"] == ["audit", "expert"]
    assert arms["B"]["rule_exposed"] is True
    assert arms["B"]["repair_attempt_count"] == 0
    assert arms["A"]["canonical_input_digest"] == arms["B"]["canonical_input_digest"]
    assert all("evolution_strategies" not in context for context in audit.contexts)
    assert sum("evolution_strategies" in context for context in expert.contexts) == 1

    ordinary = next(
        case for case in dataset.final if case.case_group == "ordinary_control"
    )
    ordinary_receipt = await executor.execute_pair(
        ordinary,
        learner_id="isolated",
        candidate=CANDIDATE,
        attempt=1,
    )
    ordinary_arms = {arm["arm"]: arm for arm in ordinary_receipt["arms"]}
    assert ordinary.task_type == "general_learning_support"
    assert ordinary_receipt["single_treatment_valid"] is True
    assert ordinary_arms["A"]["rule_exposed"] is False
    assert ordinary_arms["B"]["rule_exposed"] is False


@pytest.mark.asyncio
async def test_executor_rejects_evaluation_identifiers_in_model_input():
    recorder = ModelTraceRecorder()

    class PollutingExpert(TraceAwareExpert):
        async def run(self, context):
            polluted = dict(context)
            polluted["case_id"] = "D1V5-QUALIFICATION-T001"
            return await super().run(polluted)

    executor = D1V5EvolutionExecutor(
        expert_agent=PollutingExpert(recorder),
        audit_agent=TraceAwareAudit(recorder),
        semantic_judge=FakeJudge(),
        model_trace_recorder=recorder,
        model_name="fake",
        max_repair_attempts=1,
    )

    with pytest.raises(ValueError, match="forbidden evaluator metadata"):
        await executor.execute_pair(
            D1V5EvolutionDataset().qualification[0],
            learner_id="isolated",
            candidate=CANDIDATE,
                attempt=1,
        )


@pytest.mark.asyncio
async def test_executor_applies_a_distinct_audit_deadline():
    class HangingAudit(FakeAudit):
        async def run(self, context):
            await asyncio.Event().wait()

    executor = D1V5EvolutionExecutor(
        expert_agent=FakeExpert(),
        audit_agent=HangingAudit(),
        semantic_judge=FakeJudge(),
        model_trace_recorder=ModelTraceRecorder(),
        model_name="fake",
        max_repair_attempts=1,
        step_timeout_seconds=1,
        audit_timeout_seconds=0.01,
        judge_timeout_seconds=0.1,
        arm_timeout_seconds=31,
    )

    with pytest.raises(RuntimeError, match="StepDeadlineExceeded"):
        await asyncio.wait_for(
            executor.execute_pair(
                D1V5EvolutionDataset().qualification[0],
                learner_id="isolated",
                candidate=CANDIDATE,
                attempt=1,
            ),
            timeout=0.2,
        )


@pytest.mark.asyncio
async def test_sandbox_extracts_first_two_discovery_failures_and_cleanup_is_idempotent(tmp_path):
    dataset = D1V5EvolutionDataset()
    discovery_root = tmp_path / "discovery"
    discovery_root.mkdir()
    discovery_run_id = _discovery_artifact(
        discovery_root / (("D1V5DISC_" + "a" * 32) + ".json")
    )
    executor, _expert, _audit = _executor()
    production = InMemoryEvolutionRepository()
    service = D1V5EvolutionSandboxService(
        executor=executor,
        evolution_agent=NoopEvolutionAgent(),
        candidate_compiler=FakeCandidateCompiler(),
        production_repository=production,
        runtime_mode="live",
        production_evolution_enabled=False,
        production_rules_enabled=False,
        state_root=tmp_path / "evolution",
        discovery_state_root=discovery_root,
    )

    packet = await service.create(discovery_run_id)
    run_id = packet["run_id"]
    assert packet["candidate"]["source_case_ids"] == [
        "D1V5-DISCOVERY-T001",
        "D1V5-DISCOVERY-T002",
    ]
    assert packet["candidate"]["candidate_authorship"] == (
        "experimental_model_authored_candidate"
    )
    assert packet["candidate"]["hardcoded_strategy_fallback_used"] is False
    assert packet["review_constraints"]["independent_human_review"] is False
    assert production.list_feedback() == []
    assert production.list_signatures() == []
    assert production.list_rules() == []

    # A service restart can restore only the evaluation artifact, then purge it.
    restarted = D1V5EvolutionSandboxService(
        executor=executor,
        evolution_agent=NoopEvolutionAgent(),
        candidate_compiler=FakeCandidateCompiler(),
        production_repository=production,
        runtime_mode="live",
        production_evolution_enabled=False,
        production_rules_enabled=False,
        state_root=tmp_path / "evolution",
        discovery_state_root=discovery_root,
    )
    assert restarted.status(run_id)["status"] == "awaiting_review"
    cleanup = await restarted.cleanup(run_id, purge_artifacts=True)
    assert cleanup["cleanup_completed"] is True
    assert cleanup["sandbox_repository_removed"] is True
    assert cleanup["candidate_removed"] is True
    assert cleanup["artifact_purged"] is True
    assert cleanup["production_unchanged"] is True
    again = await restarted.cleanup(run_id, purge_artifacts=True)
    assert again["cleanup_completed"] is True
    assert again["already_absent"] is True


@pytest.mark.asyncio
async def test_cleaned_audit_timeout_continuation_reuses_frozen_receipts(tmp_path):
    dataset = D1V5EvolutionDataset()
    discovery_root = tmp_path / "discovery"
    discovery_root.mkdir()
    discovery_run_id = _discovery_artifact(
        discovery_root / (("D1V5DISC_" + "a" * 32) + ".json")
    )
    executor, _expert, _audit = _executor(audit_timeout_seconds=600)
    service = D1V5EvolutionSandboxService(
        executor=executor,
        evolution_agent=NoopEvolutionAgent(),
        candidate_compiler=FakeCandidateCompiler(),
        production_repository=InMemoryEvolutionRepository(),
        runtime_mode="live",
        production_evolution_enabled=False,
        production_rules_enabled=False,
        state_root=tmp_path / "evolution",
        discovery_state_root=discovery_root,
    )
    packet = await service.create(discovery_run_id)
    run_id = packet["run_id"]
    completed_cases = dataset.final[:21]
    next_case_id = dataset.final[21].case_id
    with service._lock:
        run = service._runs[run_id]
        run.pop("execution_deadlines", None)  # Simulate the pre-amendment artifact.
        run["review"] = {"decision": "approve"}
        run["qualification"] = {"passed": True}
        run["status"] = "final_failed"
        run["final"] = {
            "integrity": {
                "passed": False,
                "reason_codes": [
                    "final_case_coverage_mismatch",
                    "unresolved_technical_failure",
                    "target_denominator_mismatch",
                    "compatible_boundary_denominator_mismatch",
                    "normal_control_denominator_mismatch",
                ],
                "unexpected_case_ids": [],
                "invalid_case_ids": [],
            },
            "receipts": [{"case_id": case.case_id} for case in completed_cases],
            "technical_errors": [
                {
                    "case_id": next_case_id,
                    "attempt": attempt,
                    "error_type": "RuntimeError",
                    "error_digest": (
                        "bddbdbe73b86f1e70f3bc1a46193a83791d32cb71bacb7bdcbce7686bc988c05"
                    ),
                }
                for attempt in (1, 2)
            ],
            "pair_checkpoints": {},
            "dataset_file_sha256": dataset.final_sha256,
        }
        run["completed_case_count"] = len(completed_cases)
        run["total_case_count"] = len(dataset.final)
        service._persist_locked(run)

    cleanup = await service.cleanup(run_id)
    assert cleanup["production_unchanged"] is True

    resumed = service.resume(run_id)
    assert resumed["status"] == "final_running"
    assert resumed["completed_case_count"] == 21
    assert resumed["active_case_id"] == next_case_id
    assert resumed["technical_continuation_count"] == 1
    assert resumed["execution_deadlines"]["audit_step_timeout_seconds"] == 600
    assert resumed["protocol_amendments"][0]["completed_receipt_count_reused"] == 21
    evidence = service.evidence(run_id)
    assert len(evidence["final"]["receipts"]) == 21
    assert [item["attempt"] for item in evidence["final"]["technical_errors"]] == [1, 2]

    await service.cleanup(run_id, purge_artifacts=True)


def test_qualification_gate_accepts_recovered_technical_retry():
    receipt = {
        "case_id": "D1V5-QUALIFICATION-T001",
        "context_equal": True,
        "evidence_pack_equal": True,
        "single_treatment_valid": True,
        "arms": [
            {
                "arm": "A",
                "final_target_failure": True,
                "semantic_verdict": {"status": "judged"},
            },
            {
                "arm": "B",
                "final_target_failure": False,
                "semantic_verdict": {"status": "judged"},
            },
        ],
    }
    receipts = [
        {**receipt, "case_id": f"D1V5-QUALIFICATION-T{index:03d}"}
        for index in range(1, 9)
    ]
    score = D1V5EvolutionSandboxService._score_qualification(
        receipts,
        [{"case_id": "D1V5-QUALIFICATION-T001", "attempt": 1}],
    )

    assert score["passed"] is True
    assert score["technical_failure_attempt_count"] == 1
    assert score["recovered_technical_attempt_count"] == 1
    assert score["unresolved_technical_failure_count"] == 0

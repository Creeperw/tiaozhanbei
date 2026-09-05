from __future__ import annotations

"""Isolated three-stage D1 V5 evolution experiment.

This evaluation-only service derives a candidate from a completed discovery
artifact, records Copilot review, runs qualification and final paired trials,
and persists evidence under ``runtime/evaluation``. It never references the
production evolution repository or enables the production rule registry.
"""

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from competition_app.agents.common import envelope
from competition_app.agents.evolution import EvolutionAgent
from competition_app.contracts.execution import ExecutionPlan, ExecutionStep
from competition_app.contracts.evolution import FailureSignature
from competition_app.contracts.knowledge import EvidenceItem, EvidencePack, RetrievalSummaryItem
from competition_app.evaluation.d1_experimental_candidate import (
    D1ExperimentalCandidateCompiler,
)
from competition_app.evaluation.d1_v5_run_gate import D1V5EvaluationRunGate
from competition_app.repositories.evolution import InMemoryEvolutionRepository
from competition_app.runtime.agent_registry import AgentRegistry
from competition_app.runtime.event_stream import (
    bind_event_sink,
    current_event_sink,
    project_public_business_text,
    reset_event_sink,
)
from competition_app.runtime.model_trace import ModelTraceRecorder
from competition_app.runtime.orchestrator import Orchestrator
from competition_app.services.failure_signature import FailureSignatureService
from competition_app.services.feedback_governance import FeedbackGovernanceService


_DATASET_ROOT = (
    Path(__file__).resolve().parents[3]
    / "evaluation"
    / "evolution"
    / "datasets"
    / "d1_three_stage_v5_advantage"
)
_MANIFEST_FILE = _DATASET_ROOT / "d1_rule_three_stage_advantage_v5_1.manifest.json"
_DISCOVERY_FILE_NAME = "d1_rule_discovery_advantage_v5_1.jsonl"
_QUALIFICATION_FILE_NAME = "d1_rule_qualification_advantage_v5_1.jsonl"
_FINAL_FILE_NAME = "d1_rule_final_ab_advantage_v5_1.jsonl"
_DATASET_ID = "d1_three_stage_v5_advantage"
_DATASET_VERSION = "5.1.0"
_DISCOVERY_RUN_ID_RE = re.compile(r"^D1V5DISC_[0-9a-f]{32}$")
_SANDBOX_RUN_ID_RE = re.compile(r"^D1V5EVO_[0-9a-f]{32}$")
_EXECUTION_PROTOCOL_VERSION = "production-prose-orchestrator-v5"

# This identity is deliberately local to the evaluation module. Registering it
# in runtime.evolution_rules would make it production-addressable and violate
# the experiment's isolation contract. The strategy itself is model-authored;
# there is intentionally no hard-coded executable strategy fallback here.
_EXPERIMENT_TEMPLATE_ID = "d1_v5_conflict_authority_boundary"
_DISCOVERY_SOURCE_COUNT = 2
# V5.1 的旧运行没有持久化步骤截止时间。该摘要对应旧协议中 Audit
# 在 300 秒被 Orchestrator 截止后，由沙箱包装出的安全错误摘要。它只
# 用于确认历史 artifact 确实属于可续跑的 Audit 技术超时，而不是业务失败。
_LEGACY_AUDIT_300_TIMEOUT_ERROR_DIGEST = (
    "bddbdbe73b86f1e70f3bc1a46193a83791d32cb71bacb7bdcbce7686bc988c05"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_seconds(value: Any) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _wilson(successes: int, total: int) -> list[float] | None:
    if total <= 0:
        return None
    z = 1.959963984540054
    proportion = successes / total
    z2 = z * z
    denominator = 1 + z2 / total
    centre = (proportion + z2 / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total + z2 / (4 * total * total)
        )
        / denominator
    )
    return [
        round(max(0.0, centre - margin), 6),
        round(min(1.0, centre + margin), 6),
    ]


def _mcnemar_exact(improvements: int, regressions: int) -> float | None:
    discordant = improvements + regressions
    if not discordant:
        return None
    tail = sum(
        math.comb(discordant, index)
        for index in range(min(improvements, regressions) + 1)
    ) / (2**discordant)
    return round(min(1.0, 2 * tail), 12)


class D1V5EvolutionMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    material_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=3, max_length=4_000)


class D1V5EvolutionCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    case_id: str = Field(min_length=1, max_length=128)
    stage: Literal["qualification", "final_ab"]
    case_group: Literal["target_fault", "compatible_boundary", "ordinary_control"]
    prompt: str = Field(min_length=3, max_length=2_000)
    task_type: Literal["knowledge_explanation", "general_learning_support"]
    materials: tuple[D1V5EvolutionMaterial, D1V5EvolutionMaterial]
    formal_environment_write_allowed: Literal[False]
    pair_order: Literal["AB", "BA"] | None = None


class D1V5CopilotReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    reviewer_type: Literal["github_copilot"]
    independent_human_review: Literal[False]
    note: str = Field(min_length=3, max_length=2_000)
    candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class D1V5ExperimentStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discovery_run_id: str = Field(pattern=r"^D1V5DISC_[0-9a-f]{32}$")


class _FrozenEvidenceAgent:
    def __init__(self, pack: EvidencePack) -> None:
        self.pack = pack

    async def run(self, context: dict[str, Any]):
        return envelope(context, "knowledge_base_agent", "evidence_pack", self.pack)


class _CapturingAgent:
    def __init__(self, target: Any) -> None:
        self.target = target
        self.outputs: list[Any] = []

    async def run(self, context: dict[str, Any]):
        result = await self.target.run(context)
        self.outputs.append(result)
        return result


class _SandboxStrategy:
    def __init__(self, candidate: dict[str, Any]) -> None:
        self.candidate = candidate

    def as_context(self) -> dict[str, Any]:
        return {
            "rule_id": self.candidate["rule_id"],
            "version": self.candidate["version"],
            "template_id": self.candidate["template_id"],
            "strategy": self.candidate["strategy_text"],
            "boundary": (
                "评测沙箱内经 GitHub Copilot 审核的候选策略；只约束当前 Expert 步骤，"
                "不得扩大任务范围，不得覆盖当前问题、证据边界或安全规则。"
            ),
        }


class _SandboxEvolutionRegistry:
    """Evaluation-only read view with no repository or persistence methods."""

    def __init__(self, candidate: dict[str, Any] | None) -> None:
        self.candidate = candidate
        self.exposures: list[dict[str, Any]] = []

    def resolve(
        self,
        *,
        target_agent: str,
        target_step_id: str,
        task_type: str,
    ) -> list[_SandboxStrategy]:
        candidate = self.candidate
        if candidate is None:
            return []
        if (
            target_agent != candidate["target_agent"]
            or target_step_id != candidate["target_step_id"]
            or task_type != candidate["task_type"]
        ):
            return []
        return [_SandboxStrategy(candidate)]

    def record_exposure(
        self,
        strategies: list[_SandboxStrategy],
        *,
        execution_id: str | None,
        target_agent: str,
        input_digest: str = "",
    ) -> None:
        self.exposures.extend(
            {
                "rule_id": strategy.candidate["rule_id"],
                "execution_id": execution_id,
                "target_agent": target_agent,
                "input_digest": input_digest,
            }
            for strategy in strategies
        )


class D1V5EvolutionDataset:
    """Load frozen qualification/final runtime rows without opening Gold."""

    def __init__(self) -> None:
        manifest_bytes = _MANIFEST_FILE.read_bytes()
        manifest = json.loads(manifest_bytes)
        if manifest.get("dataset_id") != _DATASET_ID:
            raise ValueError("unexpected D1 V5 dataset identity")
        if manifest.get("version") != _DATASET_VERSION:
            raise ValueError("unexpected D1 V5 dataset version")
        if manifest.get("runtime_gold_separated") is not True:
            raise ValueError("D1 V5 runtime/Gold separation is not declared")
        if manifest.get("formal_environment_write_allowed") is not False:
            raise ValueError("D1 V5 manifest permits formal writeback")

        self.manifest = manifest
        self.manifest_sha256 = _sha256_bytes(manifest_bytes)
        self.qualification, self.qualification_sha256 = self._load_stage(
            _QUALIFICATION_FILE_NAME,
            expected_stage="qualification",
            expected_count=8,
        )
        self.final, self.final_sha256 = self._load_stage(
            _FINAL_FILE_NAME,
            expected_stage="final_ab",
            expected_count=100,
        )
        groups: dict[str, int] = {}
        orders: dict[str, int] = {}
        for case in self.final:
            groups[case.case_group] = groups.get(case.case_group, 0) + 1
            orders[str(case.pair_order)] = orders.get(str(case.pair_order), 0) + 1
        if groups != {
            "target_fault": 60,
            "compatible_boundary": 20,
            "ordinary_control": 20,
        }:
            raise ValueError("D1 V5 final group distribution is invalid")
        if orders != {"AB": 50, "BA": 50}:
            raise ValueError("D1 V5 final pair-order distribution is invalid")

    def _load_stage(
        self,
        file_name: str,
        *,
        expected_stage: str,
        expected_count: int,
    ) -> tuple[tuple[D1V5EvolutionCase, ...], str]:
        path = _DATASET_ROOT / file_name
        raw_bytes = path.read_bytes()
        actual_hash = _sha256_bytes(raw_bytes)
        expected_hash = str(self.manifest.get("files", {}).get(file_name) or "")
        if not expected_hash or actual_hash != expected_hash:
            raise ValueError(f"D1 V5 runtime hash mismatch: {file_name}")
        cases = tuple(
            D1V5EvolutionCase.model_validate(json.loads(line))
            for line in raw_bytes.decode("utf-8").splitlines()
            if line.strip()
        )
        if len(cases) != expected_count:
            raise ValueError(f"D1 V5 {expected_stage} case count is invalid")
        if any(
            case.schema_version != "d1-three-stage-runtime-1.1"
            or case.stage != expected_stage
            or case.formal_environment_write_allowed is not False
            for case in cases
        ):
            raise ValueError(f"D1 V5 {expected_stage} runtime contract is invalid")
        if expected_stage == "qualification" and any(
            case.case_group != "target_fault" or case.pair_order is not None
            for case in cases
        ):
            raise ValueError("D1 V5 qualification rows are invalid")
        return cases, actual_hash


class D1V5EvolutionExecutor:
    """Run one strict single-variable pair through Expert, Audit and judge."""

    def __init__(
        self,
        *,
        expert_agent: Any,
        audit_agent: Any,
        semantic_judge: Any,
        model_trace_recorder: ModelTraceRecorder,
        model_name: str,
        max_repair_attempts: int = 2,
        step_timeout_seconds: float = 300.0,
        audit_timeout_seconds: float | None = None,
        judge_timeout_seconds: float = 240.0,
        arm_timeout_seconds: float = 1_000.0,
    ) -> None:
        if max_repair_attempts != 1:
            raise ValueError(
                "the production Orchestrator currently owns exactly one local-repair round"
            )
        self.expert_agent = expert_agent
        self.audit_agent = audit_agent
        self.semantic_judge = semantic_judge
        self.model_trace_recorder = model_trace_recorder
        self.model_name = model_name
        self.max_repair_attempts = max_repair_attempts
        resolved_audit_timeout = (
            step_timeout_seconds
            if audit_timeout_seconds is None
            else audit_timeout_seconds
        )
        if min(
            step_timeout_seconds,
            resolved_audit_timeout,
            judge_timeout_seconds,
            arm_timeout_seconds,
        ) <= 0:
            raise ValueError("evaluation deadlines must be positive")
        if arm_timeout_seconds <= max(
            step_timeout_seconds,
            resolved_audit_timeout,
            judge_timeout_seconds,
        ):
            raise ValueError("arm deadline must exceed every inner deadline")
        self.step_timeout_seconds = float(step_timeout_seconds)
        self.audit_timeout_seconds = float(resolved_audit_timeout)
        self.judge_timeout_seconds = float(judge_timeout_seconds)
        self.arm_timeout_seconds = float(arm_timeout_seconds)

    def deadline_contract(self) -> dict[str, float]:
        """Return the evaluation-only deadlines persisted with run evidence."""

        return {
            "expert_step_timeout_seconds": self.step_timeout_seconds,
            "audit_step_timeout_seconds": self.audit_timeout_seconds,
            "semantic_judge_timeout_seconds": self.judge_timeout_seconds,
            "arm_timeout_seconds": self.arm_timeout_seconds,
        }

    async def execute_pair(
        self,
        case: D1V5EvolutionCase,
        *,
        learner_id: str,
        candidate: dict[str, Any],
        attempt: int,
        completed_arms: dict[str, dict[str, Any]] | None = None,
        on_arm_completed: Callable[[str, dict[str, Any]], None] | None = None,
        heartbeat: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        pack = self._evidence_pack(case)
        pack_digest = _digest(pack)
        execution_order = list(case.pair_order or "AB")
        expected_b_rule_exposure = case.task_type == candidate["task_type"]
        arms: dict[str, dict[str, Any]] = deepcopy(completed_arms or {})
        for arm, value in arms.items():
            if arm not in {"A", "B"} or value.get("arm") != arm:
                raise ValueError("invalid persisted arm checkpoint")
            if value.get("evidence_pack_digest") != pack_digest:
                raise ValueError("persisted arm checkpoint uses different evidence")
        for arm in execution_order:
            if arm in arms:
                if heartbeat is not None:
                    heartbeat(
                        {
                            "phase": "arm_checkpoint_reused",
                            "arm": arm,
                            "attempt": attempt,
                        }
                    )
                continue
            expected_rule_exposure = bool(arm == "B" and expected_b_rule_exposure)
            if heartbeat is not None:
                heartbeat({"phase": "arm_started", "arm": arm, "attempt": attempt})
            arms[arm] = await asyncio.wait_for(
                self._run_arm(
                    case,
                    arm=arm,
                    learner_id=learner_id,
                    pack=pack,
                    pack_digest=pack_digest,
                    candidate=candidate if expected_rule_exposure else None,
                    heartbeat=heartbeat,
                ),
                timeout=self.arm_timeout_seconds,
            )
            arms[arm]["technical_attempt"] = attempt
            if on_arm_completed is not None:
                on_arm_completed(arm, deepcopy(arms[arm]))
            if heartbeat is not None:
                heartbeat({"phase": "arm_completed", "arm": arm, "attempt": attempt})
        if set(arms) != {"A", "B"}:
            raise RuntimeError("pair execution did not produce both arm checkpoints")
        context_equal = bool(
            arms["A"]["canonical_input_digest"]
            and arms["A"]["canonical_input_digest"]
            == arms["B"]["canonical_input_digest"]
        )
        return {
            "schema_version": "d1-v5-evolution-pair-1.0",
            "case_id": case.case_id,
            "case_group": case.case_group,
            "pair_order": case.pair_order,
            "attempt": attempt,
            "model_name": self.model_name,
            "execution_protocol_version": _EXECUTION_PROTOCOL_VERSION,
            "formal_environment_write_allowed": False,
            "context_equal": context_equal,
            "evidence_pack_equal": bool(
                arms["A"]["evidence_pack_digest"]
                == arms["B"]["evidence_pack_digest"]
                == pack_digest
            ),
            "single_treatment_valid": bool(
                not arms["A"]["rule_exposed"]
                and arms["B"]["rule_exposed"] == expected_b_rule_exposure
                and arms["A"]["treatment_difference_count"] == 0
                and arms["B"]["treatment_difference_count"]
                == (1 if expected_b_rule_exposure else 0)
            ),
            "arms": [arms["A"], arms["B"]],
            "finished_at": _now(),
        }

    async def _run_arm(
        self,
        case: D1V5EvolutionCase,
        *,
        arm: str,
        learner_id: str,
        pack: EvidencePack,
        pack_digest: str,
        candidate: dict[str, Any] | None,
        heartbeat: Callable[[dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        root_context = self._context(case, learner_id=learner_id, pack=pack)
        expert = _CapturingAgent(self.expert_agent)
        audit = _CapturingAgent(self.audit_agent)
        registry = AgentRegistry()
        registry.register("frozen_evidence_agent", _FrozenEvidenceAgent(pack))
        registry.register("expert_agent", expert)
        registry.register("audit_agent", audit)
        sandbox_registry = _SandboxEvolutionRegistry(candidate)
        orchestrator = Orchestrator(
            registry,
            evolution_rule_registry=sandbox_registry,
        )
        plan = ExecutionPlan(
            plan_id=f"D1V5_EVO_{uuid4().hex}",
            task_type=case.task_type,
            steps=[
                ExecutionStep(
                    step_id="knowledge",
                    agent="frozen_evidence_agent",
                    timeout_seconds=30,
                    max_retries=0,
                ),
                ExecutionStep(
                    step_id="expert",
                    agent="expert_agent",
                    depends_on=["knowledge"],
                    timeout_seconds=self.step_timeout_seconds,
                    max_retries=0,
                ),
                ExecutionStep(
                    step_id="audit",
                    agent="audit_agent",
                    depends_on=["knowledge", "expert"],
                    audit_subject="resource",
                    timeout_seconds=self.audit_timeout_seconds,
                    max_retries=0,
                ),
            ],
            graph_timeout_seconds=self.arm_timeout_seconds,
        )
        previous_sink = current_event_sink()
        heartbeat_event_types = {
            "step_started",
            "step_completed",
            "step_timeout",
            "step_retrying",
            "model_input",
            "model_output",
            "model_failed",
            "model_transport",
            "audit_revision_started",
            "audit_revision_completed",
            "audit_revision_failed",
            "repair_planned",
            "repair_step_started",
            "repair_step_completed",
            "repair_reaudit_started",
            "repair_completed",
            "repair_stopped",
        }

        def evaluation_sink(event: dict[str, Any]) -> None:
            event_type = str(event.get("event") or "")
            if heartbeat is not None and event_type in heartbeat_event_types:
                heartbeat(
                    {
                        "phase": event_type,
                        "arm": arm,
                        "step_id": str(
                            event.get("step_id")
                            or event.get("audit_step_id")
                            or ""
                        ),
                        "agent": str(event.get("agent") or ""),
                    }
                )
            if previous_sink is not None:
                previous_sink(event)

        sink_token = bind_event_sink(evaluation_sink)
        self.model_trace_recorder.reset()
        try:
            with self.model_trace_recorder.capture_full():
                result = await orchestrator.execute(plan, root_context)
            if not expert.outputs or not audit.outputs:
                raise RuntimeError(
                    "sandbox chain did not produce Expert and Audit outputs: "
                    f"{result.error_type or result.status}: "
                    f"{result.error_message or 'no execution error message'}"
                )
            first_audit = audit.outputs[0]
            final_expert = expert.outputs[-1]
            final_audit = audit.outputs[-1]
            repair_attempts = len(result.repair_trace)
            final_decision = str(getattr(final_audit.payload, "decision", ""))
            exhausted = bool(
                repair_attempts >= self.max_repair_attempts and final_decision != "pass"
            )
            repair_count = self.max_repair_attempts + 1 if exhausted else repair_attempts
            learner_body = self._learner_body(final_expert.payload.content)
            if heartbeat is not None:
                heartbeat({"phase": "semantic_judge_started", "arm": arm})
            semantic_verdict = await asyncio.wait_for(
                self.semantic_judge.judge(
                    question=case.prompt,
                    material_a=case.materials[0].text,
                    material_b=case.materials[1].text,
                    answer=learner_body,
                ),
                timeout=self.judge_timeout_seconds,
            )
            if heartbeat is not None:
                heartbeat({"phase": "semantic_judge_completed", "arm": arm})
            release_allowed = bool(result.status == "success" and final_decision == "pass")
            semantic_failure = semantic_verdict.get("acceptable") is not True
            contamination = self._model_trace_contamination(case)
            if contamination:
                raise ValueError(
                    "forbidden evaluator metadata reached a model input: "
                    + ", ".join(contamination)
                )
            return {
                "arm": arm,
                "rule_exposed": bool(sandbox_registry.exposures),
                "treatment_difference_count": 1 if sandbox_registry.exposures else 0,
                "evidence_pack_digest": pack_digest,
                "canonical_input_digest": self._canonical_input_digest(root_context),
                "first_audit_decision": str(getattr(first_audit.payload, "decision", "")),
                "final_audit_decision": final_decision,
                "repair_count": repair_count,
                "repair_attempt_count": repair_attempts,
                "repair_exhausted": exhausted,
                "max_repair_attempts": self.max_repair_attempts,
                "release_allowed": release_allowed,
                "initial_target_failure": (
                    str(getattr(first_audit.payload, "decision", "")) != "pass"
                ),
                "final_target_failure": semantic_failure or not release_allowed,
                "semantic_failure": semantic_failure,
                "learner_visible_body": learner_body if release_allowed else None,
                "semantic_verdict": semantic_verdict,
                "actual_rerun_step_ids": sorted(
                    {
                        step_id
                        for trace in result.repair_trace
                        for step_id in trace.rerun_step_ids
                    }
                ),
                "execution_status": result.status,
                "model_input_forbidden_markers": [],
            }
        finally:
            reset_event_sink(sink_token)

    def _model_trace_contamination(
        self,
        case: D1V5EvolutionCase,
    ) -> list[str]:
        forbidden_keys = {
            "case_group",
            "pair_order",
            "gold",
            "gold_relation",
            "expected_relation",
            "semantic_verdict",
        }
        forbidden_values = {
            case.case_id,
            *(material.material_id for material in case.materials),
        }
        findings: set[str] = set()

        def inspect(value: Any, path: str = "$") -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    normalized = str(key).strip().lower()
                    if normalized in forbidden_keys:
                        findings.add(f"key:{path}.{normalized}")
                    inspect(nested, f"{path}.{normalized}")
            elif isinstance(value, (list, tuple)):
                for index, nested in enumerate(value):
                    inspect(nested, f"{path}[{index}]")
            elif isinstance(value, str):
                for forbidden in forbidden_values:
                    if forbidden and forbidden in value:
                        findings.add(f"value:{forbidden}")

        for trace in self.model_trace_recorder.items:
            inspect(getattr(trace, "raw_input", None) or {})
            inspect(getattr(trace, "transport_input", None) or {})
        return sorted(findings)

    @staticmethod
    def _canonical_input_digest(context: dict[str, Any]) -> str:
        normalized = deepcopy(context)
        normalized.pop("evolution_strategies", None)
        for key in (
            "case_id",
            "trace_id",
            "request_id",
            "execution_id",
            "workflow_task_id",
        ):
            normalized.pop(key, None)
        dependencies = normalized.get("dependency_outputs") or {}
        knowledge = dependencies.get("knowledge")
        if knowledge is not None:
            normalized["dependency_outputs"] = {
                "knowledge": getattr(knowledge, "payload", knowledge)
            }
        return _digest(normalized)

    @staticmethod
    def _context(
        case: D1V5EvolutionCase,
        *,
        learner_id: str,
        pack: EvidencePack,
    ) -> dict[str, Any]:
        token = uuid4().hex
        knowledge_context = {
            "case_id": f"ISOLATED_{token}",
            "trace_id": f"TRACE_{token}",
            "request_id": f"REQ_{token}",
            "execution_id": f"EXEC_{token}",
            "step_id": "knowledge",
            "task_type": case.task_type,
            "learner_id": learner_id,
            "dependency_outputs": {},
        }
        knowledge = envelope(
            knowledge_context,
            "knowledge_base_agent",
            "evidence_pack",
            pack,
        )
        return {
            "case_id": knowledge_context["case_id"],
            "trace_id": knowledge_context["trace_id"],
            "request_id": knowledge_context["request_id"],
            "workflow_task_id": knowledge_context["request_id"],
            "execution_id": knowledge_context["execution_id"],
            "step_id": "expert",
            "learner_id": learner_id,
            "task_type": case.task_type,
            "user_request": case.prompt,
            "original_user_request": case.prompt,
            "messages": [{"role": "user", "content": case.prompt}],
            "available_minutes": 15,
            "user_profile": {},
            "source_policy": {"trusted_source_types": ["reference"]},
            "formal_task": {"status": "not_available"},
            "formal_environment_write_allowed": False,
            "dependency_outputs": {"knowledge": knowledge},
        }

    @staticmethod
    def _evidence_pack(case: D1V5EvolutionCase) -> EvidencePack:
        evidence_items: list[EvidenceItem] = []
        summary_items: list[RetrievalSummaryItem] = []
        for index, material in enumerate(case.materials):
            suffix = "A" if index == 0 else "B"
            evidence_id = f"EVIDENCE_{suffix}"
            source_id = f"PROVIDED_SOURCE_{suffix}"
            source_label = f"材料 {suffix}"
            evidence_items.append(
                EvidenceItem(
                    evidence_id=evidence_id,
                    source_id=source_id,
                    content_summary=material.text,
                    authority_level="reference",
                    confidence=1.0,
                    resource_type="reference",
                    source_label=source_label,
                )
            )
            summary_items.append(
                RetrievalSummaryItem(
                    evidence_id=evidence_id,
                    source_id=source_id,
                    authority_level="reference",
                    resource_type="reference",
                    source_label=source_label,
                    content=material.text,
                )
            )
        return EvidencePack(
            evidence_pack_id="PACK_FROZEN_PAIR",
            query=case.prompt,
            evidence_items=evidence_items,
            retrieval_summary="\n".join(
                f"[材料 {'A' if index == 0 else 'B'}] {material.text}"
                for index, material in enumerate(case.materials)
            ),
            summary_items=summary_items,
            summary_evidence_ids=[item.evidence_id for item in evidence_items],
            conflict_evidence=[],
            risk_notes=["仅用于中医药教学测试，不构成现实诊疗建议。"],
        )

    @staticmethod
    def _learner_body(content: dict[str, object]) -> str:
        for key in ("知识讲解", "题目讲解", "学习支持"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return project_public_business_text(value)
        card = content.get("知识卡片")
        if isinstance(card, dict):
            value = card.get("exp")
            if isinstance(value, str) and value.strip():
                return project_public_business_text(value)
        return project_public_business_text(content)


class D1V5EvolutionSandboxService:
    """In-process sandbox whose only durable output is evaluation evidence."""

    def __init__(
        self,
        *,
        evolution_agent: EvolutionAgent,
        candidate_compiler: D1ExperimentalCandidateCompiler,
        executor: D1V5EvolutionExecutor,
        runtime_mode: str,
        state_root: Path,
        discovery_state_root: Path,
        production_repository: Any,
        production_evolution_enabled: bool,
        production_rules_enabled: bool,
        run_gate: D1V5EvaluationRunGate | None = None,
        max_attempts: int = 2,
        stale_after_seconds: float = 1_500.0,
        driver_lease_timeout_seconds: float = 90.0,
    ) -> None:
        self.evolution_agent = evolution_agent
        self.candidate_compiler = candidate_compiler
        self.executor = executor
        self.runtime_mode = runtime_mode
        self.state_root = state_root.resolve()
        self.discovery_state_root = discovery_state_root.resolve()
        self.production_repository = production_repository
        self.production_evolution_enabled = production_evolution_enabled
        self.production_rules_enabled = production_rules_enabled
        self.run_gate = run_gate
        self.max_attempts = max_attempts
        if stale_after_seconds <= 0:
            raise ValueError("stale watchdog threshold must be positive")
        if driver_lease_timeout_seconds <= 0:
            raise ValueError("driver lease timeout must be positive")
        self.stale_after_seconds = float(stale_after_seconds)
        self.driver_lease_timeout_seconds = float(driver_lease_timeout_seconds)
        self.dataset = D1V5EvolutionDataset()
        self._runs: dict[str, dict[str, Any]] = {}
        self._repositories: dict[str, InMemoryEvolutionRepository] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._watchdogs: dict[str, asyncio.Task[None]] = {}
        self._lock = RLock()

    async def create(self, discovery_run_id: str) -> dict[str, Any]:
        if self.runtime_mode != "live":
            raise ValueError("D1 V5 evolution experiment requires COMPETITION_APP_MODE=live")
        if self.production_evolution_enabled or self.production_rules_enabled:
            raise ValueError("production evolution switches must remain disabled")
        discovery = self._load_discovery(discovery_run_id)
        baseline = self._production_snapshot()
        sandbox_repository = InMemoryEvolutionRepository()
        governance = FeedbackGovernanceService(sandbox_repository, enabled=True)
        signature_service = FailureSignatureService(
            sandbox_repository,
            min_cases=2,
            min_executions=2,
            min_high_trust=0,
        )
        receipts = {
            str(item.get("case_id") or ""): item
            for item in discovery.get("receipts") or []
        }
        discovery_order = [
            f"D1V5-DISCOVERY-T{index:03d}" for index in range(1, 9)
        ]
        discovery_source_case_ids = tuple(
            case_id
            for case_id in discovery_order
            if (
                (receipts.get(case_id, {}).get("semantic_verdict") or {}).get(
                    "acceptable"
                )
                is False
                and (
                    receipts.get(case_id, {}).get("semantic_verdict") or {}
                ).get("rationale_code")
                in {"relationship_mishandled", "unsupported_resolution"}
            )
        )[:_DISCOVERY_SOURCE_COUNT]
        if len(discovery_source_case_ids) != _DISCOVERY_SOURCE_COUNT:
            raise ValueError(
                "discovery artifact contains fewer than two mechanism-matched "
                "semantic failures; "
                "no experimental candidate can be derived"
            )
        summaries: list[str] = []
        signature: FailureSignature | None = None
        for case_id in discovery_source_case_ids:
            receipt = receipts.get(case_id)
            if receipt is None:
                raise ValueError(f"discovery artifact is missing {case_id}")
            verdict = receipt.get("semantic_verdict") or {}
            if verdict.get("acceptable") is not False:
                raise ValueError(f"discovery source case is not a semantic failure: {case_id}")
            summary = (
                f"学习者最终正文未正确处理两份直接冲突材料；rationale_code="
                f"{str(verdict.get('rationale_code') or 'unknown')}. "
                "系统未提供权威性或版本元数据时，正文仍擅自建立材料等级或用材料外事实消解冲突。"
            )
            feedback = governance.record_automatic_feedback(
                source_type="audit",
                execution_id=str(receipt.get("initial_expert_draft", {}).get("resource_draft_id") or case_id),
                learner_id=None,
                task_type="knowledge_explanation",
                issue_type="unsupported_conflict_resolution",
                summary=summary,
                source_case_id=case_id,
                target_agent="expert_agent",
                owner_step_id="expert",
                field_path="resource.content",
                constraint_category="conflict_authority_boundary",
                severity="blocking",
                trust_level="medium",
                status="validated",
            )
            if feedback is None:
                raise RuntimeError("sandbox feedback governance is unavailable")
            signature = signature_service.ingest(feedback)
            summaries.append(summary)
        if signature is None or not signature.candidate_ready:
            raise ValueError("two-case experimental candidate threshold was not reached")
        run_id = f"D1V5EVO_{uuid4().hex}"
        lease_handed_off = False
        if self.run_gate is not None:
            self.run_gate.handoff(discovery_run_id, run_id)
            lease_handed_off = True
        try:
            context = {
                "trace_id": f"EVOTRACE_{uuid4().hex}",
                "request_id": f"EVOREQ_{uuid4().hex}",
                "workflow_task_id": f"EVOTASK_{uuid4().hex}",
                "learner_id": "d1-v5-evaluation-sandbox",
                "user_request": "分析隔离评测中已审核的重复失败签名",
                "original_user_request": "分析隔离评测中已审核的重复失败签名",
                "messages": [],
                "now": datetime.now(timezone.utc),
            }
            analysis = await self.evolution_agent.analyze(
                context,
                signature=signature,
                source_summaries=summaries,
            )
            proposal = await self.candidate_compiler.compile(
                signature=signature,
                natural_language_analysis=str(analysis),
                source_summaries=summaries,
                source_case_ids=discovery_source_case_ids,
            )
        except BaseException:
            if lease_handed_off and self.run_gate is not None:
                self.run_gate.release(run_id)
            raise
        candidate = {
            "schema_version": "d1-v5-experimental-rule-1.0",
            "rule_id": f"D1V5RULE_{uuid4().hex}",
            "version": 1,
            "signature_id": signature.signature_id,
            "template_id": _EXPERIMENT_TEMPLATE_ID,
            "target_agent": "expert_agent",
            "target_step_id": "expert",
            "task_type": "knowledge_explanation",
            "intervention_type": "prevention",
            "issue_type": "unsupported_conflict_resolution",
            "field_path": "resource.content",
            "source_case_ids": list(discovery_source_case_ids),
            "strategy_text": proposal["strategy_text"],
            "natural_language_analysis": str(analysis)[:12_000],
            "model_authored_proposal": proposal,
            "candidate_authorship": proposal["authorship"],
            "candidate_compiler_version": proposal["compiler_version"],
            "candidate_proposal_digest": proposal["proposal_digest"],
            "strategy_sha256": proposal["strategy_sha256"],
            "proposal_model_name": proposal["proposal_model_name"],
            "hardcoded_strategy_fallback_used": False,
            "production_compiler_compatible": False,
            "status": "awaiting_copilot_review",
            "production_registered": False,
            "production_active": False,
        }
        candidate_digest = _digest(candidate)
        run = {
            "schema_version": "d1-v5-evolution-run-1.0",
            "run_id": run_id,
            "discovery_run_id": discovery_run_id,
            "status": "awaiting_review",
            "active_stage": None,
            "dataset_id": _DATASET_ID,
            "dataset_version": _DATASET_VERSION,
            "execution_protocol_version": _EXECUTION_PROTOCOL_VERSION,
            "execution_deadlines": self.executor.deadline_contract(),
            "manifest_sha256": self.dataset.manifest_sha256,
            "candidate": candidate,
            "candidate_digest": candidate_digest,
            "signature": signature.model_dump(mode="json"),
            "review": None,
            "safety_replay": self._safety_replay(candidate, signature),
            "qualification": None,
            "final": None,
            "technical_errors": [],
            "heartbeat_at": _now(),
            "heartbeat": {
                "phase": "candidate_created",
                "arm": None,
                "step_id": None,
                "agent": None,
            },
            "resume_count": 0,
            "technical_continuation_count": 0,
            "protocol_amendments": [],
            "driver_lease_at": None,
            "cancel_reason": None,
            "production_snapshot_before": baseline,
            "production_snapshot_after": None,
            "production_unchanged": None,
            "sandbox_storage": "in_memory",
            "formal_environment_write_allowed": False,
            "failure_observation_source": "model_semantic_judge",
            "failure_observation_human_reviewed": False,
            "candidate_generation": {
                "authorship": proposal["authorship"],
                "compiler_version": proposal["compiler_version"],
                "proposal_model_name": proposal["proposal_model_name"],
                "proposal_digest": proposal["proposal_digest"],
                "strategy_sha256": proposal["strategy_sha256"],
                "source_case_ids": list(discovery_source_case_ids),
                "hardcoded_strategy_fallback_used": False,
                "production_compiler_compatible": False,
            },
            "model_side_later_stage_unseen_during_extraction": True,
            "operator_full_blindness": False,
            "operator_blindness_note": (
                "当前主代理上下文已接触资格集和数据生成逻辑；候选提取模型只收到 discovery 摘要。"
            ),
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "cleanup": {
                "completed": False,
                "in_memory_repository_removed": False,
                "candidate_removed_from_memory": False,
                "background_task_cancelled": False,
                "artifact_retained": True,
                "completed_at": None,
            },
        }
        with self._lock:
            self._runs[run_id] = run
            self._repositories[run_id] = sandbox_repository
            self._persist_locked(run)
        return self.review_packet(run_id)

    def review_packet(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        return {
            "schema_version": "d1-v5-copilot-review-packet-1.0",
            "run_id": run_id,
            "status": run["status"],
            "candidate_digest": run["candidate_digest"],
            "candidate": deepcopy(run["candidate"]),
            "signature": deepcopy(run["signature"]),
            "safety_replay": deepcopy(run["safety_replay"]),
            "review_constraints": {
                "reviewer_type": "github_copilot",
                "independent_human_review": False,
                "must_use_discovery_only": True,
                "source_selection_method": (
                    "first_two_semantic_failures_in_frozen_discovery_order"
                ),
                "production_registration_allowed": False,
                "production_activation_allowed": False,
            },
        }

    def submit_review(
        self,
        run_id: str,
        request: D1V5CopilotReviewRequest,
    ) -> dict[str, Any]:
        with self._lock:
            run = self._require_active_locked(run_id)
            if run["status"] != "awaiting_review":
                raise ValueError("sandbox run is not awaiting review")
            if request.candidate_digest != run["candidate_digest"]:
                raise ValueError("review candidate digest does not match")
            approved = request.decision == "approve"
            run["review"] = {
                **request.model_dump(mode="json"),
                "reviewed_at": _now(),
                "human_reviewer": False,
                "review_independence_claimed": False,
            }
            run["candidate"]["status"] = "sandbox_approved" if approved else "rejected"
            run["status"] = "reviewed" if approved else "rejected"
            if not approved:
                run["finished_at"] = _now()
            self._persist_locked(run)
            return self._public_status(run)

    def start_qualification(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._require_active_locked(run_id)
            if run["status"] != "reviewed":
                raise ValueError("approved Copilot review is required")
            if not run["safety_replay"].get("passed"):
                raise ValueError("deterministic safety replay did not pass")
            self._assert_production_unchanged(run)
            run["status"] = "qualification_running"
            run["active_stage"] = "qualification"
            run["resume_stage"] = "qualification"
            run["heartbeat_at"] = _now()
            run["heartbeat"] = {
                "phase": "qualification_started",
                "arm": None,
                "step_id": None,
                "agent": None,
            }
            run["driver_lease_at"] = _now()
            run["cancel_reason"] = None
            run["started_at"] = run["started_at"] or _now()
            self._persist_locked(run)
            self._launch_stage_locked(run_id, "qualification")
            return self._public_status(run)

    def resume(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._require_active_locked(run_id)
            if run["status"] == "cleaned":
                self._resume_cleaned_technical_final_locked(run)
                return self._public_status(run)
            if run["status"] not in {
                "interrupted",
                "qualification_interrupted",
                "final_interrupted",
                "stale",
            }:
                raise ValueError("only an interrupted or stale stage can be resumed")
            stage = str(run.get("resume_stage") or "")
            if stage not in {"qualification", "final_ab"}:
                raise ValueError("persisted run has no resumable stage")
            if stage == "final_ab":
                qualification = run.get("qualification") or {}
                if not qualification.get("passed"):
                    raise ValueError("passing qualification is required before final resume")
            self._assert_production_unchanged(run)
            run["status"] = (
                "qualification_running" if stage == "qualification" else "final_running"
            )
            run["active_stage"] = stage
            run["resume_count"] = int(run.get("resume_count") or 0) + 1
            run["heartbeat_at"] = _now()
            run["heartbeat"] = {
                "phase": "stage_resumed",
                "arm": run.get("active_arm"),
                "step_id": None,
                "agent": None,
            }
            run["finished_at"] = None
            run["driver_lease_at"] = _now()
            run["cancel_reason"] = None
            self._persist_locked(run)
            self._launch_stage_locked(run_id, stage)
            return self._public_status(run)

    def _resume_cleaned_technical_final_locked(
        self,
        run: dict[str, Any],
    ) -> None:
        """Continue one cleaned final run stopped solely by an Audit timeout.

        This is deliberately narrower than a generic terminal-state retry. It
        preserves the frozen dataset, candidate, completed receipts and score
        contract; permits one operator-requested deadline amendment only; and
        records that partial results were visible before the amendment.
        """

        run_id = str(run["run_id"])
        qualification = run.get("qualification") or {}
        final = run.get("final") or {}
        integrity = final.get("integrity") or {}
        cleanup = run.get("cleanup") or {}
        receipts = deepcopy(final.get("receipts") or [])
        technical_errors = deepcopy(final.get("technical_errors") or [])
        expected_case_ids = [case.case_id for case in self.dataset.final]
        observed_case_ids = [str(item.get("case_id") or "") for item in receipts]
        observed_case_id_set = set(observed_case_ids)
        unresolved_errors = [
            item
            for item in technical_errors
            if str(item.get("case_id") or "") not in observed_case_id_set
        ]
        unresolved_case_ids = {
            str(item.get("case_id") or "") for item in unresolved_errors
        }
        expected_next_case_id = (
            expected_case_ids[len(observed_case_ids)]
            if len(observed_case_ids) < len(expected_case_ids)
            else None
        )
        allowed_integrity_reasons = {
            "final_case_coverage_mismatch",
            "unresolved_technical_failure",
            "target_denominator_mismatch",
            "compatible_boundary_denominator_mismatch",
            "normal_control_denominator_mismatch",
        }
        integrity_reasons = set(integrity.get("reason_codes") or [])
        audit_timeout_confirmed = bool(unresolved_errors) and all(
            (
                item.get("failure_step_id") == "audit"
                and item.get("failure_type") == "StepDeadlineExceeded"
            )
            or item.get("error_digest")
            == _LEGACY_AUDIT_300_TIMEOUT_ERROR_DIGEST
            for item in unresolved_errors
        )

        if qualification.get("passed") is not True:
            raise ValueError("passing qualification is required before final continuation")
        if cleanup.get("completed") is not True or run.get("production_unchanged") is not True:
            raise ValueError("only a successfully cleaned isolated run can be continued")
        if integrity.get("passed") is not False:
            raise ValueError("only a failed final integrity result can be continued")
        if (
            "unresolved_technical_failure" not in integrity_reasons
            or not integrity_reasons.issubset(allowed_integrity_reasons)
            or integrity.get("unexpected_case_ids")
            or integrity.get("invalid_case_ids")
        ):
            raise ValueError("final run did not fail solely from bounded technical coverage")
        if observed_case_ids != expected_case_ids[: len(observed_case_ids)]:
            raise ValueError("completed final receipts are not a frozen dataset prefix")
        if (
            expected_next_case_id is None
            or unresolved_case_ids != {expected_next_case_id}
            or len(unresolved_errors) != self.max_attempts
            or not audit_timeout_confirmed
        ):
            raise ValueError("next frozen case is not an exhausted Audit deadline failure")
        if final.get("dataset_file_sha256") != self.dataset.final_sha256:
            raise ValueError("persisted final checkpoint uses a different frozen dataset")
        if (run.get("review") or {}).get("decision") != "approve":
            raise ValueError("approved candidate review is required for continuation")
        if not (run.get("safety_replay") or {}).get("passed"):
            raise ValueError("candidate safety replay is required for continuation")
        if int(run.get("technical_continuation_count") or 0) >= 1:
            raise ValueError("the one-time technical continuation was already used")

        previous_deadlines = deepcopy(
            run.get("execution_deadlines")
            or {
                "expert_step_timeout_seconds": 300.0,
                "audit_step_timeout_seconds": 300.0,
                "semantic_judge_timeout_seconds": 240.0,
                "arm_timeout_seconds": 1_000.0,
            }
        )
        current_deadlines = self.executor.deadline_contract()
        if (
            current_deadlines["expert_step_timeout_seconds"]
            != float(previous_deadlines["expert_step_timeout_seconds"])
            or current_deadlines["semantic_judge_timeout_seconds"]
            != float(previous_deadlines["semantic_judge_timeout_seconds"])
            or current_deadlines["audit_step_timeout_seconds"]
            <= float(previous_deadlines["audit_step_timeout_seconds"])
        ):
            raise ValueError("continuation must change only by extending the Audit deadline")

        self._assert_production_unchanged(run)
        previous_run = deepcopy(run)
        lease_acquired = False
        try:
            if self.run_gate is not None:
                self.run_gate.acquire(run_id)
                lease_acquired = True
            amendment = {
                "type": "audit_deadline_extension_after_technical_failure",
                "recorded_at": _now(),
                "operator_requested": True,
                "partial_results_visible_before_amendment": True,
                "completed_receipt_count_reused": len(receipts),
                "continued_from_case_id": expected_next_case_id,
                "previous_deadlines": previous_deadlines,
                "new_deadlines": current_deadlines,
                "frozen_dataset_unchanged": True,
                "candidate_unchanged": True,
                "scoring_contract_unchanged": True,
                "both_arms_receive_same_deadlines": True,
                "previous_terminal_summary_digest": _digest(
                    {
                        key: value
                        for key, value in final.items()
                        if key not in {"receipts", "technical_errors", "pair_checkpoints"}
                    }
                ),
                "previous_integrity_reason_codes": sorted(integrity_reasons),
                "previous_cleanup": deepcopy(cleanup),
            }
            run.setdefault("protocol_amendments", []).append(amendment)
            run["technical_continuation_count"] = (
                int(run.get("technical_continuation_count") or 0) + 1
            )
            run["execution_deadlines"] = current_deadlines
            run["final"] = {
                "status": "running",
                "completed_case_count": len(receipts),
                "receipts": receipts,
                "technical_errors": technical_errors,
                "pair_checkpoints": deepcopy(final.get("pair_checkpoints") or {}),
                "dataset_file_sha256": self.dataset.final_sha256,
            }
            run["status"] = "final_running"
            run["active_stage"] = "final_ab"
            run["resume_stage"] = "final_ab"
            run["resume_count"] = int(run.get("resume_count") or 0) + 1
            run["active_case_id"] = expected_next_case_id
            run["active_attempt"] = None
            run["active_arm"] = None
            run["completed_case_count"] = len(receipts)
            run["total_case_count"] = len(expected_case_ids)
            run["heartbeat_at"] = _now()
            run["heartbeat"] = {
                "phase": "technical_final_continuation_started",
                "arm": None,
                "step_id": None,
                "agent": None,
            }
            run["driver_lease_at"] = _now()
            run["cancel_reason"] = None
            run["finished_at"] = None
            run["production_snapshot_after"] = None
            run["production_unchanged"] = None
            run["cleanup"] = {
                "completed": False,
                "in_memory_repository_removed": False,
                "candidate_removed_from_memory": False,
                "background_task_cancelled": False,
                "artifact_retained": True,
                "completed_at": None,
            }
            self._repositories[run_id] = InMemoryEvolutionRepository()
            self._persist_locked(run)
            self._launch_stage_locked(run_id, "final_ab")
        except BaseException:
            self._runs[run_id] = previous_run
            self._repositories.pop(run_id, None)
            if lease_acquired and self.run_gate is not None:
                self.run_gate.release(run_id)
            self._persist_locked(previous_run)
            raise

    def start_final(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._require_active_locked(run_id)
            qualification = run.get("qualification") or {}
            if run["status"] != "qualification_passed" or not qualification.get("passed"):
                raise ValueError("passing qualification is required before final A/B")
            self._assert_production_unchanged(run)
            run["status"] = "final_running"
            run["active_stage"] = "final_ab"
            run["resume_stage"] = "final_ab"
            run["heartbeat_at"] = _now()
            run["heartbeat"] = {
                "phase": "final_started",
                "arm": None,
                "step_id": None,
                "agent": None,
            }
            run["driver_lease_at"] = _now()
            run["cancel_reason"] = None
            self._persist_locked(run)
            self._launch_stage_locked(run_id, "final_ab")
            return self._public_status(run)

    def status(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._require_active_locked(run_id)
            if run.get("status") in {"qualification_running", "final_running"}:
                run["driver_lease_at"] = _now()
        self._mark_stale_if_needed(run_id)
        return self._public_status(self._run(run_id))

    def _launch_stage_locked(self, run_id: str, stage: str) -> None:
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            raise ValueError("a sandbox stage is already running")
        existing_watchdog = self._watchdogs.pop(run_id, None)
        if existing_watchdog is not None and not existing_watchdog.done():
            existing_watchdog.cancel()
        task = asyncio.create_task(self._run_stage(run_id, stage))
        self._tasks[run_id] = task
        self._watchdogs[run_id] = asyncio.create_task(
            self._watch_driver_lease(run_id, task)
        )

    async def _watch_driver_lease(
        self,
        run_id: str,
        stage_task: asyncio.Task[None],
    ) -> None:
        interval = max(1.0, min(10.0, self.driver_lease_timeout_seconds / 4))
        try:
            while not stage_task.done():
                await asyncio.sleep(interval)
                with self._lock:
                    run = self._runs.get(run_id)
                    if run is None or run.get("status") not in {
                        "qualification_running",
                        "final_running",
                    }:
                        return
                    age = _age_seconds(run.get("driver_lease_at"))
                    if age is None or age <= self.driver_lease_timeout_seconds:
                        continue
                    run["cancel_reason"] = "driver_lease_expired"
                    run["heartbeat_at"] = _now()
                    run["heartbeat"] = {
                        "phase": "driver_lease_expired",
                        "arm": run.get("active_arm"),
                        "step_id": None,
                        "agent": None,
                    }
                    self._persist_locked(run)
                    stage_task.cancel()
                    return
        except asyncio.CancelledError:
            return
        finally:
            with self._lock:
                if self._watchdogs.get(run_id) is asyncio.current_task():
                    self._watchdogs.pop(run_id, None)

    def evidence(self, run_id: str) -> dict[str, Any]:
        self._mark_stale_if_needed(run_id)
        run = self._run(run_id)
        payload = deepcopy(run)
        payload["candidate_reviewed_by"] = (
            (run.get("review") or {}).get("reviewer")
        )
        payload["candidate_review_is_human"] = False
        payload["artifact_file"] = str(self._artifact_path(run_id))
        return payload

    def _mark_stale_if_needed(self, run_id: str) -> None:
        with self._lock:
            run = self._require_active_locked(run_id)
            if run.get("status") not in {"qualification_running", "final_running"}:
                return
            task = self._tasks.get(run_id)
            if task is not None and not task.done():
                return
            age = _age_seconds(run.get("heartbeat_at"))
            if age is None or age <= self.stale_after_seconds:
                return
            stage = str(run.get("active_stage") or "")
            run["status"] = "stale"
            run["resume_stage"] = stage if stage in {"qualification", "final_ab"} else None
            run["active_stage"] = None
            run["heartbeat"] = {
                "phase": "stale_watchdog_triggered",
                "arm": run.get("active_arm"),
                "step_id": None,
                "agent": None,
            }
            run["finished_at"] = _now()
            self._persist_locked(run)

    async def cleanup(self, run_id: str, *, purge_artifacts: bool = False) -> dict[str, Any]:
        task: asyncio.Task[None] | None = None
        watchdog: asyncio.Task[None] | None = None
        with self._lock:
            if run_id not in self._runs:
                try:
                    self._restore_locked(run_id)
                except (KeyError, ValueError):
                    pass
            if run_id not in self._runs:
                if purge_artifacts:
                    path = self._artifact_path(run_id)
                    if (
                        _SANDBOX_RUN_ID_RE.fullmatch(run_id)
                        and path.parent.resolve() == self.state_root
                        and path.is_file()
                    ):
                        path.unlink()
                    work = self._work_dir(run_id)
                    if (
                        _SANDBOX_RUN_ID_RE.fullmatch(run_id)
                        and work.parent.resolve() == self.state_root
                        and work.is_dir()
                    ):
                        shutil.rmtree(work)
                return {
                    "run_id": run_id,
                    "cleanup_completed": True,
                    "already_absent": True,
                    "artifact_purged": purge_artifacts,
                }
            run = self._runs[run_id]
            task = self._tasks.get(run_id)
            watchdog = self._watchdogs.get(run_id)
            if task is not None and not task.done():
                run["cancel_reason"] = "explicit_cleanup"
                task.cancel()
            if watchdog is not None and not watchdog.done():
                watchdog.cancel()
        pending = [item for item in (task, watchdog) if item is not None and not item.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        with self._lock:
            run = self._runs[run_id]
            production_after = self._production_snapshot()
            production_unchanged = bool(
                production_after == run["production_snapshot_before"]
            )
            run["production_snapshot_after"] = production_after
            run["production_unchanged"] = production_unchanged
            run["status"] = "cleaned"
            run["active_stage"] = None
            run["finished_at"] = run["finished_at"] or _now()
            run["cleanup"] = {
                "completed": True,
                "in_memory_repository_removed": self._repositories.pop(run_id, None) is not None,
                "candidate_removed_from_memory": True,
                "background_task_cancelled": task is not None and task.cancelled(),
                "artifact_retained": not purge_artifacts,
                "completed_at": _now(),
            }
            self._tasks.pop(run_id, None)
            self._watchdogs.pop(run_id, None)
            if not purge_artifacts:
                self._persist_locked(run)
            result = {
                "run_id": run_id,
                "cleanup_completed": True,
                "production_unchanged": production_unchanged,
                "sandbox_repository_removed": run["cleanup"]["in_memory_repository_removed"],
                "candidate_removed": True,
                "artifact_purged": purge_artifacts,
                "production_change_detected": not production_unchanged,
                "evaluation_lease_released": bool(
                    self.run_gate is not None and self.run_gate.release(run_id)
                ),
            }
            self._runs.pop(run_id, None)
        if purge_artifacts:
            path = self._artifact_path(run_id)
            if path.is_file():
                path.unlink()
            work = self._work_dir(run_id)
            if work.is_dir():
                shutil.rmtree(work)
        return result

    async def shutdown(self) -> None:
        with self._lock:
            tasks = [
                task
                for task in (*self._tasks.values(), *self._watchdogs.values())
                if not task.done()
            ]
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        with self._lock:
            self._tasks.clear()
            self._watchdogs.clear()
            for run in self._runs.values():
                if run.get("status") not in {
                    "qualification_running",
                    "final_running",
                }:
                    continue
                stage = str(run.get("active_stage") or "")
                run["status"] = (
                    "qualification_interrupted"
                    if stage == "qualification"
                    else "final_interrupted"
                )
                run["resume_stage"] = stage
                run["active_stage"] = None
                run["heartbeat_at"] = _now()
                run["heartbeat"] = {
                    "phase": "service_shutdown",
                    "arm": run.get("active_arm"),
                    "step_id": None,
                    "agent": None,
                }
                run["finished_at"] = _now()
                self._persist_locked(run)

    async def _run_stage(self, run_id: str, stage: str) -> None:
        cases = self.dataset.qualification if stage == "qualification" else self.dataset.final
        run = self._run(run_id)
        key = stage if stage == "qualification" else "final"
        checkpoint = run.get(key) or {}
        receipts: list[dict[str, Any]] = deepcopy(checkpoint.get("receipts") or [])
        technical_errors: list[dict[str, Any]] = deepcopy(
            checkpoint.get("technical_errors") or []
        )
        pair_checkpoints: dict[str, dict[str, Any]] = deepcopy(
            checkpoint.get("pair_checkpoints") or {}
        )
        completed_case_ids = {
            str(receipt.get("case_id") or "") for receipt in receipts
        }
        try:
            run = self._run(run_id)
            candidate = deepcopy(run["candidate"])
            learner_id = "D1V5_EVAL_" + _sha256_bytes(run_id.encode("utf-8"))[:16]
            for case in cases:
                if case.case_id in completed_case_ids:
                    continue
                completed = False
                prior_attempts: list[int] = []
                for error in technical_errors:
                    if str(error.get("case_id") or "") != case.case_id:
                        continue
                    try:
                        prior_attempts.append(int(error.get("attempt") or 0))
                    except (TypeError, ValueError):
                        continue
                first_attempt = max(prior_attempts, default=0) + 1
                for attempt in range(
                    first_attempt,
                    first_attempt + self.max_attempts,
                ):
                    checkpoint_arms = deepcopy(
                        (pair_checkpoints.get(case.case_id) or {}).get("arms") or {}
                    )
                    self._set_progress(
                        run_id,
                        case.case_id,
                        attempt,
                        len(receipts),
                        len(cases),
                        phase="pair_started",
                        arm=None,
                    )

                    def heartbeat(event: dict[str, Any]) -> None:
                        self._heartbeat(run_id, case.case_id, attempt, event)

                    def arm_completed(arm: str, value: dict[str, Any]) -> None:
                        pair_checkpoints.setdefault(case.case_id, {"arms": {}})["arms"][
                            arm
                        ] = deepcopy(value)
                        self._persist_stage_checkpoint(
                            run_id,
                            stage,
                            receipts,
                            technical_errors,
                            pair_checkpoints=pair_checkpoints,
                        )
                    try:
                        receipt = await self.executor.execute_pair(
                            case,
                            learner_id=learner_id,
                            candidate=candidate,
                            attempt=attempt,
                            completed_arms=checkpoint_arms,
                            on_arm_completed=arm_completed,
                            heartbeat=heartbeat,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        error_text = f"{type(exc).__name__}:{exc}"
                        technical_errors.append(
                            {
                                "case_id": case.case_id,
                                "attempt": attempt,
                                "error_type": type(exc).__name__[:128],
                                "error_digest": _sha256_bytes(error_text.encode("utf-8")),
                                "recorded_at": _now(),
                                "raw_prompt_saved": False,
                                "raw_output_saved": False,
                            }
                        )
                        self._persist_stage_checkpoint(
                            run_id,
                            stage,
                            receipts,
                            technical_errors,
                            pair_checkpoints=pair_checkpoints,
                        )
                        continue
                    receipts.append(receipt)
                    completed_case_ids.add(case.case_id)
                    pair_checkpoints.pop(case.case_id, None)
                    completed = True
                    break
                if not completed:
                    break
                self._persist_stage_checkpoint(
                    run_id,
                    stage,
                    receipts,
                    technical_errors,
                    pair_checkpoints=pair_checkpoints,
                )
            if stage == "qualification":
                summary = self._score_qualification(receipts, technical_errors)
                status = "qualification_passed" if summary["passed"] else "qualification_failed"
            else:
                summary = self._score_final(receipts, technical_errors)
                current_run = self._run(run_id)
                amendments = deepcopy(current_run.get("protocol_amendments") or [])
                if amendments:
                    summary["technical_execution"]["technical_continuation_count"] = int(
                        current_run.get("technical_continuation_count") or 0
                    )
                    summary["measurement_limitations"].update(
                        {
                            "runtime_protocol_amended_after_partial_observation": True,
                            "partial_results_visible_before_amendment": True,
                            "mixed_audit_deadline_regimes": True,
                            "completed_receipts_reused_without_rerun": True,
                        }
                    )
                status = "completed" if summary["integrity"]["passed"] else "final_failed"
            with self._lock:
                run = self._runs[run_id]
                run[stage if stage == "qualification" else "final"] = {
                    **summary,
                    "receipts": receipts,
                    "technical_errors": technical_errors,
                    "pair_checkpoints": {},
                    "dataset_file_sha256": (
                        self.dataset.qualification_sha256
                        if stage == "qualification"
                        else self.dataset.final_sha256
                    ),
                }
                run["status"] = status
                run["active_stage"] = None
                run["active_case_id"] = None
                run["active_attempt"] = None
                run["active_arm"] = None
                run["resume_stage"] = None
                run["heartbeat_at"] = _now()
                run["heartbeat"] = {
                    "phase": "stage_completed",
                    "arm": None,
                    "step_id": None,
                    "agent": None,
                }
                if status in {"qualification_failed", "completed", "final_failed"}:
                    run["finished_at"] = _now()
                self._assert_production_unchanged(run)
                self._persist_locked(run)
        except asyncio.CancelledError:
            with self._lock:
                if run_id in self._runs:
                    run = self._runs[run_id]
                    run["status"] = (
                        "qualification_interrupted"
                        if stage == "qualification"
                        else "final_interrupted"
                    )
                    run["active_stage"] = None
                    run["resume_stage"] = stage
                    run["cancel_reason"] = run.get("cancel_reason") or "service_shutdown"
                    run["heartbeat_at"] = _now()
                    run["heartbeat"] = {
                        "phase": "stage_interrupted",
                        "arm": run.get("active_arm"),
                        "step_id": None,
                        "agent": None,
                    }
                    run["finished_at"] = _now()
                    self._persist_locked(run)
            raise
        except Exception as exc:
            with self._lock:
                if run_id in self._runs:
                    run = self._runs[run_id]
                    run["status"] = "failed"
                    run["active_stage"] = None
                    run["finished_at"] = _now()
                    run["technical_errors"].append(
                        {
                            "stage": stage,
                            "error_type": type(exc).__name__[:128],
                            "error_digest": _sha256_bytes(
                                f"{type(exc).__name__}:{exc}".encode("utf-8")
                            ),
                            "recorded_at": _now(),
                        }
                    )
                    self._persist_locked(run)
        finally:
            current = asyncio.current_task()
            with self._lock:
                if self._tasks.get(run_id) is current:
                    self._tasks.pop(run_id, None)

    def _load_discovery(self, discovery_run_id: str) -> dict[str, Any]:
        if not _DISCOVERY_RUN_ID_RE.fullmatch(discovery_run_id):
            raise ValueError("invalid D1 V5 discovery run ID")
        path = (self.discovery_state_root / f"{discovery_run_id}.json").resolve()
        if path.parent != self.discovery_state_root or not path.is_file():
            raise KeyError(discovery_run_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected_hash = str(
            self.dataset.manifest.get("files", {}).get(_DISCOVERY_FILE_NAME) or ""
        )
        receipts = payload.get("receipts") or []
        expected_case_ids = [
            f"D1V5-DISCOVERY-T{index:03d}" for index in range(1, 9)
        ]
        observed_case_ids = [
            str(receipt.get("case_id") or "") for receipt in receipts
        ]
        if (
            payload.get("schema_version") != "d1-v5-discovery-run-1.0"
            or payload.get("run_id") != discovery_run_id
            or payload.get("status") != "completed"
            or payload.get("dataset_id") != _DATASET_ID
            or payload.get("dataset_version") != _DATASET_VERSION
            or payload.get("runtime_file_sha256") != expected_hash
            or payload.get("completed_case_count") != 8
            or payload.get("technical_failure_count") != 0
            or payload.get("formal_environment_write_allowed") is not False
            or observed_case_ids != expected_case_ids
        ):
            raise ValueError("discovery artifact failed frozen-run validation")
        for receipt in receipts:
            isolation = receipt.get("isolation_validation") or {}
            if isolation.get("valid") is not True:
                raise ValueError("discovery artifact failed isolation validation")
            if isolation.get("formal_environment_write_allowed") is not False:
                raise ValueError("discovery receipt permits formal writeback")
        return payload

    @staticmethod
    def _safety_replay(
        candidate: dict[str, Any],
        signature: FailureSignature,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        if candidate.get("template_id") != _EXPERIMENT_TEMPLATE_ID:
            reasons.append("template_mismatch")
        if candidate.get("target_agent") != "expert_agent":
            reasons.append("target_agent_mismatch")
        if candidate.get("target_step_id") != "expert":
            reasons.append("target_step_mismatch")
        if candidate.get("task_type") != "knowledge_explanation":
            reasons.append("task_type_mismatch")
        if candidate.get("field_path") != "resource.content":
            reasons.append("field_path_mismatch")
        if candidate.get("candidate_authorship") != "experimental_model_authored_candidate":
            reasons.append("candidate_not_model_authored")
        if candidate.get("hardcoded_strategy_fallback_used") is not False:
            reasons.append("hardcoded_strategy_fallback_detected")
        strategy_text = str(candidate.get("strategy_text") or "")
        if not strategy_text or _sha256_bytes(strategy_text.encode("utf-8")) != candidate.get(
            "strategy_sha256"
        ):
            reasons.append("strategy_digest_mismatch")
        proposal = candidate.get("model_authored_proposal") or {}
        if _digest({
            key: value
            for key, value in proposal.items()
            if key not in {
                "compiler_version",
                "proposal_model_name",
                "proposal_digest",
                "strategy_sha256",
                "authorship",
                "hardcoded_strategy_fallback_used",
                "production_compiler_compatible",
            }
        }) != candidate.get("candidate_proposal_digest"):
            reasons.append("candidate_proposal_digest_mismatch")
        expected_source_case_ids = tuple(signature.source_case_ids)
        if tuple(candidate.get("source_case_ids") or ()) != expected_source_case_ids:
            reasons.append("source_case_boundary_mismatch")
        if signature.case_count != 2 or signature.execution_count != 2:
            reasons.append("experimental_threshold_mismatch")
        return {
            "schema_version": "d1-v5-safety-replay-1.0",
            "passed": not reasons,
            "reason_codes": reasons,
            "experimental_min_cases": 2,
            "production_min_cases_unchanged": 3,
            "production_template_registered": False,
            "production_rule_activated": False,
            "checked_at": _now(),
        }

    @staticmethod
    def _score_qualification(
        receipts: list[dict[str, Any]],
        technical_errors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        completed_case_ids = {
            str(row.get("case_id") or "") for row in receipts
        }
        unresolved_technical_errors = [
            error
            for error in technical_errors
            if str(error.get("case_id") or "") not in completed_case_ids
        ]
        improvements = [
            row["case_id"]
            for row in receipts
            if D1V5EvolutionSandboxService._arm(row, "A")["final_target_failure"]
            and not D1V5EvolutionSandboxService._arm(row, "B")["final_target_failure"]
        ]
        regressions = [
            row["case_id"]
            for row in receipts
            if not D1V5EvolutionSandboxService._arm(row, "A")["final_target_failure"]
            and D1V5EvolutionSandboxService._arm(row, "B")["final_target_failure"]
        ]
        invalid_pairs = [
            row["case_id"]
            for row in receipts
            if not row.get("context_equal")
            or not row.get("evidence_pack_equal")
            or not row.get("single_treatment_valid")
        ]
        invalid_judgements = [
            row["case_id"]
            for row in receipts
            if any(
                D1V5EvolutionSandboxService._arm(row, arm)["semantic_verdict"].get(
                    "status"
                )
                != "judged"
                for arm in ("A", "B")
            )
        ]
        passed = bool(
            len(receipts) == 8
            and not unresolved_technical_errors
            and not invalid_pairs
            and not invalid_judgements
        )
        reasons: list[str] = []
        if len(receipts) != 8:
            reasons.append("qualification_case_count_mismatch")
        if unresolved_technical_errors:
            reasons.append("unresolved_technical_failure")
        if invalid_pairs:
            reasons.append("paired_single_variable_violation")
        if invalid_judgements:
            reasons.append("semantic_judge_unavailable")
        return {
            "schema_version": "d1-v5-qualification-score-1.0",
            "passed": passed,
            "reason_codes": reasons,
            "case_count": len(receipts),
            "paired_improvement_count": len(improvements),
            "paired_improved_case_ids": improvements,
            "paired_regression_count": len(regressions),
            "paired_regressed_case_ids": regressions,
            "invalid_pair_case_ids": invalid_pairs,
            "invalid_judgement_case_ids": invalid_judgements,
            "technical_failure_attempt_count": len(technical_errors),
            "recovered_technical_attempt_count": (
                len(technical_errors) - len(unresolved_technical_errors)
            ),
            "unresolved_technical_failure_count": len(
                unresolved_technical_errors
            ),
            "gate_contract": (
                "technical_isolation_and_measurement_only; observed candidate effect "
                "does not determine final eligibility"
            ),
            "observed_effect_used_for_selection": False,
            "formal_rule_status_change_allowed": False,
        }

    def _score_final(
        self,
        receipts: list[dict[str, Any]],
        technical_errors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        expected_ids = [case.case_id for case in self.dataset.final]
        by_case = {case.case_id: case for case in self.dataset.final}
        rows_by_id = {str(row.get("case_id") or ""): row for row in receipts}
        unresolved_technical_errors = [
            error
            for error in technical_errors
            if str(error.get("case_id") or "") not in rows_by_id
        ]
        missing = sorted(set(expected_ids) - set(rows_by_id))
        unexpected = sorted(set(rows_by_id) - set(expected_ids))
        invalid = [
            case_id
            for case_id, row in rows_by_id.items()
            if case_id not in by_case
            or not row.get("context_equal")
            or not row.get("evidence_pack_equal")
            or not row.get("single_treatment_valid")
            or row.get("formal_environment_write_allowed") is not False
        ]
        targets = [
            row for row in receipts
            if row.get("case_group") == "target_fault"
        ]
        boundaries = [
            row for row in receipts
            if row.get("case_group") == "compatible_boundary"
        ]
        normals = [
            row for row in receipts
            if row.get("case_group") == "ordinary_control"
        ]
        integrity_reasons: list[str] = []
        if len(receipts) != 100 or missing or unexpected:
            integrity_reasons.append("final_case_coverage_mismatch")
        if invalid:
            integrity_reasons.append("invalid_pair_receipt")
        if unresolved_technical_errors:
            integrity_reasons.append("unresolved_technical_failure")
        if len(targets) != 60:
            integrity_reasons.append("target_denominator_mismatch")
        if len(boundaries) != 20:
            integrity_reasons.append("compatible_boundary_denominator_mismatch")
        if len(normals) != 20:
            integrity_reasons.append("normal_control_denominator_mismatch")

        improvements = [
            row["case_id"]
            for row in targets
            if self._semantic_failure(row, "A") and not self._semantic_failure(row, "B")
        ]
        regressions = [
            row["case_id"]
            for row in targets
            if not self._semantic_failure(row, "A") and self._semantic_failure(row, "B")
        ]
        baseline_failures = [
            row["case_id"] for row in targets if self._semantic_failure(row, "A")
        ]
        candidate_failures = [
            row["case_id"] for row in targets if self._semantic_failure(row, "B")
        ]
        net_rescues = len(improvements) - len(regressions)
        a_first_pass = sum(
            self._arm(row, "A").get("first_audit_decision") == "pass"
            for row in targets
        )
        b_first_pass = sum(
            self._arm(row, "B").get("first_audit_decision") == "pass"
            for row in targets
        )
        a_repair_total = sum(self._repair_count(self._arm(row, "A")) for row in targets)
        b_repair_total = sum(self._repair_count(self._arm(row, "B")) for row in targets)
        a_repair_mean = a_repair_total / len(targets) if targets else None
        b_repair_mean = b_repair_total / len(targets) if targets else None
        repair_reduction = (
            None
            if not a_repair_mean
            else round((a_repair_mean - (b_repair_mean or 0.0)) / a_repair_mean, 6)
        )
        boundary_semantic_regressions = [
            row["case_id"]
            for row in boundaries
            if not self._semantic_failure(row, "A")
            and self._semantic_failure(row, "B")
        ]
        boundary_unnecessary_repairs = [
            row["case_id"]
            for row in boundaries
            if self._arm(row, "B")["repair_attempt_count"]
            > self._arm(row, "A")["repair_attempt_count"]
        ]
        boundary_release_gate_regressions = [
            row["case_id"]
            for row in boundaries
            if self._arm(row, "A").get("release_allowed")
            and not self._arm(row, "B").get("release_allowed")
        ]
        normal_semantic_regressions = [
            row["case_id"]
            for row in normals
            if not self._semantic_failure(row, "A")
            and self._semantic_failure(row, "B")
        ]
        normal_release_gate_regressions = [
            row["case_id"]
            for row in normals
            if self._arm(row, "A").get("release_allowed")
            and not self._arm(row, "B").get("release_allowed")
        ]
        order: dict[str, dict[str, Any]] = {}
        for pair_order in ("AB", "BA"):
            subset = [row for row in targets if row.get("pair_order") == pair_order]
            order[pair_order] = {
                "target_count": len(subset),
                "improvement_count": sum(
                    self._semantic_failure(row, "A")
                    and not self._semantic_failure(row, "B")
                    for row in subset
                ),
                "regression_count": sum(
                    not self._semantic_failure(row, "A")
                    and self._semantic_failure(row, "B")
                    for row in subset
                ),
                "candidate_first_audit_pass_count": sum(
                    self._arm(row, "B").get("first_audit_decision") == "pass"
                    for row in subset
                ),
            }
        return {
            "schema_version": "d1-v5-final-score-1.0",
            "integrity": {
                "passed": not integrity_reasons,
                "reason_codes": integrity_reasons,
                "observed_receipt_count": len(receipts),
                "missing_case_ids": missing,
                "unexpected_case_ids": unexpected,
                "invalid_case_ids": sorted(invalid),
                "dataset_file_sha256": self.dataset.final_sha256,
                "expected_dataset_file_sha256": str(
                    self.dataset.manifest.get("files", {}).get(_FINAL_FILE_NAME) or ""
                ),
            },
            "core_metrics": {
                "learner_visible_semantic_net_rescue": {
                    "numerator": len(improvements),
                    "baseline_failure_denominator": len(baseline_failures),
                    "gross_rescue_rate": _rate(len(improvements), len(baseline_failures)),
                    "gross_rescue_wilson_95ci": _wilson(
                        len(improvements), len(baseline_failures)
                    ),
                    "regression_count": len(regressions),
                    "target_denominator": len(targets),
                    "net_rescue_count": net_rescues,
                    "net_rescue_rate": _rate(net_rescues, len(targets)),
                    "baseline_semantic_failure_count": len(baseline_failures),
                    "candidate_semantic_failure_count": len(candidate_failures),
                    "mcnemar_exact_two_sided_p": _mcnemar_exact(
                        len(improvements), len(regressions)
                    ),
                    "improved_case_ids": improvements,
                    "regressed_case_ids": regressions,
                },
                "first_audit_pass_lift": {
                    "baseline_numerator": a_first_pass,
                    "candidate_numerator": b_first_pass,
                    "denominator": 60,
                    "baseline_rate": _rate(a_first_pass, 60),
                    "candidate_rate": _rate(b_first_pass, 60),
                    "absolute_percentage_point_change": round(
                        (b_first_pass - a_first_pass) / 60 * 100, 6
                    ),
                    "baseline_wilson_95ci": _wilson(a_first_pass, 60),
                    "candidate_wilson_95ci": _wilson(b_first_pass, 60),
                },
                "compatible_boundary_semantic_regression": {
                    "numerator": len(boundary_semantic_regressions),
                    "denominator": 20,
                    "rate": _rate(len(boundary_semantic_regressions), 20),
                    "wilson_95ci": _wilson(
                        len(boundary_semantic_regressions), 20
                    ),
                    "regressed_case_ids": boundary_semantic_regressions,
                },
                "compatible_boundary_unnecessary_repair": {
                    "numerator": len(boundary_unnecessary_repairs),
                    "denominator": 20,
                    "rate": _rate(len(boundary_unnecessary_repairs), 20),
                    "case_ids": boundary_unnecessary_repairs,
                },
                "compatible_boundary_release_gate_regression": {
                    "numerator": len(boundary_release_gate_regressions),
                    "denominator": 20,
                    "rate": _rate(len(boundary_release_gate_regressions), 20),
                    "case_ids": boundary_release_gate_regressions,
                },
                "average_repair_count_reduction": {
                    "baseline_total": a_repair_total,
                    "candidate_total": b_repair_total,
                    "denominator": 60,
                    "baseline_mean": round(a_repair_mean, 6) if a_repair_mean is not None else None,
                    "candidate_mean": round(b_repair_mean, 6) if b_repair_mean is not None else None,
                    "relative_reduction": repair_reduction,
                    "relative_reduction_display": "N/A" if repair_reduction is None else repair_reduction,
                    "scoring_contract": "0=first-pass,1=one-repair-pass,2=two-repair-pass,3=RMAX-exhausted",
                },
            },
            "controls": {
                "normal_control_count": len(normals),
                "normal_control_semantic_regression_count": len(
                    normal_semantic_regressions
                ),
                "normal_control_semantic_regression_case_ids": (
                    normal_semantic_regressions
                ),
                "normal_control_release_gate_regression_count": len(
                    normal_release_gate_regressions
                ),
                "normal_control_release_gate_regression_case_ids": (
                    normal_release_gate_regressions
                ),
            },
            "technical_execution": {
                "technical_error_attempt_count": len(technical_errors),
                "recovered_technical_attempt_count": (
                    len(technical_errors) - len(unresolved_technical_errors)
                ),
                "unresolved_technical_failure_count": len(
                    unresolved_technical_errors
                ),
            },
            "measurement_limitations": {
                "judge_independence": False,
                "judge_independence_reason": "semantic judge and Expert share the configured chat model",
                "automatic_effect_estimate_requires_blind_human_confirmation": True,
                "gold_independently_human_reviewed": False,
            },
            "pair_order_sensitivity": order,
            "formal_environment_write_allowed": False,
            "formal_rule_status_change_allowed": False,
        }

    @staticmethod
    def _arm(row: dict[str, Any], arm: str) -> dict[str, Any]:
        return next(item for item in row["arms"] if item["arm"] == arm)

    @classmethod
    def _final_failure(cls, row: dict[str, Any], arm: str) -> bool:
        value = cls._arm(row, arm)
        return bool(value.get("final_target_failure") or not value.get("release_allowed"))

    @classmethod
    def _semantic_failure(cls, row: dict[str, Any], arm: str) -> bool:
        value = cls._arm(row, arm)
        if "semantic_failure" in value:
            return bool(value["semantic_failure"])
        return (value.get("semantic_verdict") or {}).get("acceptable") is not True

    @staticmethod
    def _repair_count(value: dict[str, Any]) -> int:
        if value.get("repair_exhausted"):
            return int(value.get("max_repair_attempts") or 2) + 1
        return int(value.get("repair_count") or 0)

    def _set_progress(
        self,
        run_id: str,
        case_id: str,
        attempt: int,
        completed: int,
        total: int,
        *,
        phase: str,
        arm: str | None,
    ) -> None:
        with self._lock:
            run = self._runs[run_id]
            run["active_case_id"] = case_id
            run["active_attempt"] = attempt
            run["active_arm"] = arm
            run["completed_case_count"] = completed
            run["total_case_count"] = total
            run["heartbeat_at"] = _now()
            run["heartbeat"] = {
                "phase": phase,
                "arm": arm,
                "step_id": None,
                "agent": None,
            }
            self._persist_locked(run)

    def _heartbeat(
        self,
        run_id: str,
        case_id: str,
        attempt: int,
        event: dict[str, Any],
    ) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            run["active_case_id"] = case_id
            run["active_attempt"] = attempt
            run["active_arm"] = event.get("arm")
            run["heartbeat_at"] = _now()
            run["heartbeat"] = {
                "phase": str(event.get("phase") or "running")[:128],
                "arm": event.get("arm"),
                "step_id": str(event.get("step_id") or "")[:128] or None,
                "agent": str(event.get("agent") or "")[:128] or None,
            }
            self._persist_locked(run)

    def _persist_stage_checkpoint(
        self,
        run_id: str,
        stage: str,
        receipts: list[dict[str, Any]],
        technical_errors: list[dict[str, Any]],
        *,
        pair_checkpoints: dict[str, dict[str, Any]],
    ) -> None:
        with self._lock:
            run = self._runs[run_id]
            key = stage if stage == "qualification" else "final"
            run[key] = {
                "status": "running",
                "completed_case_count": len(receipts),
                "receipts": receipts,
                "technical_errors": technical_errors,
                "pair_checkpoints": pair_checkpoints,
            }
            run["completed_case_count"] = len(receipts)
            run["heartbeat_at"] = _now()
            self._persist_locked(run)

    def _production_snapshot(self) -> dict[str, Any]:
        repository = self.production_repository

        def full_count(method_name: str) -> int:
            method = getattr(repository, method_name)
            if hasattr(repository, "_all"):
                table = {
                    "list_feedback": "evolution_feedback",
                    "list_signatures": "evolution_signatures",
                    "list_rules": "evolution_rules",
                    "list_runs": "evolution_rule_runs",
                }.get(method_name)
                if table:
                    rows = repository._all(
                        f"SELECT COUNT(*) AS count FROM {table}",
                        {},
                    )
                    return int(rows[0]["count"]) if rows else 0
            return len(method(limit=10_000))

        snapshot = {
            "evolution_enabled": self.production_evolution_enabled,
            "runtime_rules_enabled": self.production_rules_enabled,
            "feedback_count": full_count("list_feedback"),
            "signature_count": full_count("list_signatures"),
            "rule_count": full_count("list_rules"),
            "rule_run_count": full_count("list_runs"),
        }
        if hasattr(repository, "_all"):
            for table in (
                "app_users",
                "auth_sessions",
                "learner_profiles",
                "learner_memories",
                "memory_candidates",
                "conversation_sessions",
                "conversation_messages",
                "long_term_plan_versions",
                "short_term_plan_versions",
                "learning_task_versions",
                "workflow_run_states",
                "execution_runs",
                "execution_steps",
                "artifacts",
                "tool_calls",
                "writeback_intents",
                "evidence_packs",
                "review_tasks",
                "resource_versions",
                "audit_results",
            ):
                try:
                    rows = repository._all(
                        f"SELECT COUNT(*) AS count FROM {table}",
                        {},
                    )
                except Exception:
                    snapshot[f"table:{table}"] = "unavailable"
                else:
                    snapshot[f"table:{table}"] = (
                        int(rows[0]["count"]) if rows else 0
                    )
        return snapshot

    def _assert_production_unchanged(self, run: dict[str, Any]) -> bool:
        current = self._production_snapshot()
        if current != run["production_snapshot_before"]:
            raise RuntimeError("production evolution state changed during sandbox evaluation")
        return True

    def _run(self, run_id: str) -> dict[str, Any]:
        if not _SANDBOX_RUN_ID_RE.fullmatch(run_id):
            raise KeyError(run_id)
        with self._lock:
            if run_id not in self._runs:
                self._restore_locked(run_id)
            return deepcopy(self._runs[run_id])

    def _require_active_locked(self, run_id: str) -> dict[str, Any]:
        if not _SANDBOX_RUN_ID_RE.fullmatch(run_id):
            raise KeyError(run_id)
        if run_id not in self._runs:
            self._restore_locked(run_id)
        return self._runs[run_id]

    def _restore_locked(self, run_id: str) -> None:
        path = self._artifact_path(run_id).resolve()
        if path.parent != self.state_root or not path.is_file():
            raise KeyError(run_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            payload.get("schema_version") != "d1-v5-evolution-run-1.0"
            or payload.get("run_id") != run_id
            or payload.get("dataset_id") != _DATASET_ID
            or payload.get("dataset_version") != _DATASET_VERSION
            or payload.get("execution_protocol_version") != _EXECUTION_PROTOCOL_VERSION
            or payload.get("manifest_sha256") != self.dataset.manifest_sha256
            or payload.get("formal_environment_write_allowed") is not False
        ):
            raise ValueError("persisted V5 sandbox artifact failed validation")
        if payload.get("status") in {"qualification_running", "final_running"}:
            stage = str(payload.get("active_stage") or "")
            payload["status"] = (
                "qualification_interrupted"
                if stage == "qualification"
                else "final_interrupted"
                if stage == "final_ab"
                else "interrupted"
            )
            payload["resume_stage"] = stage or payload.get("resume_stage")
            payload["active_stage"] = None
            payload["heartbeat_at"] = _now()
            payload["heartbeat"] = {
                "phase": "service_restart_detected",
                "arm": payload.get("active_arm"),
                "step_id": None,
                "agent": None,
            }
            payload["finished_at"] = payload.get("finished_at") or _now()
        self._runs[run_id] = payload
        self._repositories[run_id] = InMemoryEvolutionRepository()
        if self.run_gate is not None and payload.get("status") != "cleaned":
            self.run_gate.handoff(run_id, run_id)
        if payload.get("heartbeat", {}).get("phase") == "service_restart_detected":
            self._persist_locked(payload)

    def _public_status(self, run: dict[str, Any]) -> dict[str, Any]:
        qualification = run.get("qualification") or {}
        final = run.get("final") or {}
        return {
            "schema_version": run["schema_version"],
            "run_id": run["run_id"],
            "discovery_run_id": run["discovery_run_id"],
            "status": run["status"],
            "active_stage": run.get("active_stage"),
            "active_case_id": run.get("active_case_id"),
            "active_attempt": run.get("active_attempt"),
            "active_arm": run.get("active_arm"),
            "completed_case_count": run.get("completed_case_count", 0),
            "total_case_count": run.get("total_case_count", 0),
            "heartbeat_at": run.get("heartbeat_at"),
            "heartbeat": deepcopy(run.get("heartbeat")),
            "resume_stage": run.get("resume_stage"),
            "resume_count": int(run.get("resume_count") or 0),
            "technical_continuation_count": int(
                run.get("technical_continuation_count") or 0
            ),
            "execution_deadlines": deepcopy(run.get("execution_deadlines")),
            "protocol_amendments": deepcopy(run.get("protocol_amendments") or []),
            "stale_after_seconds": self.stale_after_seconds,
            "candidate_digest": run["candidate_digest"],
            "review": deepcopy(run.get("review")),
            "safety_replay": deepcopy(run.get("safety_replay")),
            "qualification_summary": {
                key: value
                for key, value in qualification.items()
                if key not in {"receipts", "technical_errors"}
            },
            "final_score": {
                key: value
                for key, value in final.items()
                if key not in {"receipts", "technical_errors"}
            },
            "formal_environment_write_allowed": False,
            "production_evolution_enabled": self.production_evolution_enabled,
            "production_rules_enabled": self.production_rules_enabled,
            "sandbox_storage": "in_memory",
            "created_at": run["created_at"],
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
        }

    def _artifact_path(self, run_id: str) -> Path:
        return self.state_root / f"{run_id}.json"

    def _work_dir(self, run_id: str) -> Path:
        return self.state_root / run_id

    def _persist_locked(self, run: dict[str, Any]) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        target = self._artifact_path(str(run["run_id"]))
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(run, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from competition_app.agents.common import envelope
from competition_app.contracts.knowledge import EvidenceItem, EvidencePack, RetrievalSummaryItem
from competition_app.contracts.execution import ExecutionPlan, ExecutionStep
from competition_app.evaluation.d1_semantic_judge import D1LearnerVisibleSemanticJudge
from competition_app.evaluation.d1_v5_run_gate import D1V5EvaluationRunGate
from competition_app.runtime.agent_registry import AgentRegistry
from competition_app.runtime.event_stream import project_public_business_text
from competition_app.runtime.model_trace import ModelTraceRecorder
from competition_app.runtime.orchestrator import Orchestrator


_DATASET_ROOT = (
    Path(__file__).resolve().parents[3]
    / "evaluation"
    / "evolution"
    / "datasets"
    / "d1_three_stage_v5_advantage"
)
_RUNTIME_FILE = _DATASET_ROOT / "d1_rule_discovery_advantage_v5_1.jsonl"
_MANIFEST_FILE = _DATASET_ROOT / "d1_rule_three_stage_advantage_v5_1.manifest.json"
_EXPECTED_DATASET_ID = "d1_three_stage_v5_advantage"
_EXPECTED_VERSION = "5.1.0"
_EXPECTED_CASE_COUNT = 8
_FORBIDDEN_RUNTIME_FIELDS = frozenset(
    {
        "gold_relation",
        "gold_rationale",
        "expected_rule_exposure",
        "conflict_binding",
        "repair_instruction",
        "target_rule",
    }
)
_FORBIDDEN_MODEL_KEYS = frozenset(
    {
        "case_group",
        "stage",
        "gold_relation",
        "gold_rationale",
        "expected_rule_exposure",
        "target_rule",
        "d1_evaluation_mode",
        "d1_conflict_binding",
        "conflict_binding",
        "evaluation_contract",
        "approved_evolution_strategies",
        "evolution_strategies",
        "required_evidence_ids",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


class D1V5DiscoveryMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    material_id: str = Field(pattern=r"^D1V5-DISCOVERY-T\d{3}-M[12]$")
    text: str = Field(min_length=3, max_length=4_000)


class D1V5DiscoveryCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    case_id: str = Field(pattern=r"^D1V5-DISCOVERY-T\d{3}$")
    stage: str
    case_group: str
    prompt: str = Field(min_length=3, max_length=2_000)
    task_type: str
    materials: tuple[D1V5DiscoveryMaterial, D1V5DiscoveryMaterial]
    formal_environment_write_allowed: bool


class D1V5DiscoveryDataset:
    """Load only the frozen runtime discovery file; the Gold file is never opened."""

    def __init__(self) -> None:
        manifest = json.loads(_MANIFEST_FILE.read_text(encoding="utf-8"))
        if manifest.get("dataset_id") != _EXPECTED_DATASET_ID:
            raise ValueError("unexpected D1 V5 dataset identity")
        if manifest.get("version") != _EXPECTED_VERSION:
            raise ValueError("unexpected D1 V5 dataset version")
        if manifest.get("runtime_gold_separated") is not True:
            raise ValueError("D1 V5 runtime/Gold separation is not declared")
        if manifest.get("formal_environment_write_allowed") is not False:
            raise ValueError("D1 V5 manifest permits formal writeback")
        expected_hash = str(manifest.get("files", {}).get(_RUNTIME_FILE.name) or "")
        runtime_bytes = _RUNTIME_FILE.read_bytes()
        if not expected_hash or _sha256_bytes(runtime_bytes) != expected_hash:
            raise ValueError("D1 V5 discovery runtime hash mismatch")

        cases: list[D1V5DiscoveryCase] = []
        for raw_line in runtime_bytes.decode("utf-8").splitlines():
            if not raw_line.strip():
                continue
            raw = json.loads(raw_line)
            forbidden = _FORBIDDEN_RUNTIME_FIELDS.intersection(raw)
            if forbidden:
                raise ValueError(
                    "D1 V5 runtime contains evaluator-only fields: "
                    + ", ".join(sorted(forbidden))
                )
            case = D1V5DiscoveryCase.model_validate(raw)
            if case.schema_version != "d1-three-stage-runtime-1.1":
                raise ValueError("unexpected D1 V5 runtime schema")
            if case.stage != "discovery" or case.case_group != "target_fault":
                raise ValueError("D1 V5 discovery runtime contains an invalid stage")
            if case.task_type != "knowledge_explanation":
                raise ValueError("D1 V5 discovery task type is not supported")
            if case.formal_environment_write_allowed is not False:
                raise ValueError("D1 V5 discovery case permits formal writeback")
            cases.append(case)

        expected_ids = [f"D1V5-DISCOVERY-T{index:03d}" for index in range(1, 9)]
        if [case.case_id for case in cases] != expected_ids:
            raise ValueError("D1 V5 discovery cases are incomplete or out of order")
        if len(cases) != _EXPECTED_CASE_COUNT:
            raise ValueError("D1 V5 discovery must contain exactly eight cases")

        self.cases = tuple(cases)
        self.runtime_sha256 = expected_hash

    def manifest_payload(self, *, runtime_mode: str) -> dict[str, Any]:
        return {
            "schema_version": "d1-v5-discovery-manifest-1.0",
            "dataset_id": _EXPECTED_DATASET_ID,
            "version": _EXPECTED_VERSION,
            "runtime_mode": runtime_mode,
            "case_count": len(self.cases),
            "case_ids": [case.case_id for case in self.cases],
            "runtime_file_sha256": self.runtime_sha256,
            "baseline_only": True,
            "gold_content_loaded": False,
            "candidate_rules_enabled": False,
            "evaluation_specific_conflict_binding_enabled": False,
            "formal_environment_write_allowed": False,
        }


class _FrozenEvidenceAgent:
    def __init__(self, pack: EvidencePack) -> None:
        self.pack = pack

    async def run(self, context: dict[str, Any]):
        return envelope(
            context,
            "knowledge_base_agent",
            "evidence_pack",
            self.pack,
        )


class _CapturingAgent:
    def __init__(self, target: Any) -> None:
        self.target = target
        self.outputs: list[Any] = []

    async def run(self, context: dict[str, Any]):
        result = await self.target.run(context)
        self.outputs.append(result)
        return result


class D1V5DiscoveryExecutor:
    def __init__(
        self,
        *,
        expert_agent: Any,
        audit_agent: Any,
        semantic_judge: D1LearnerVisibleSemanticJudge,
        model_trace_recorder: ModelTraceRecorder,
        model_name: str,
    ) -> None:
        self.expert_agent = expert_agent
        self.audit_agent = audit_agent
        self.semantic_judge = semantic_judge
        self.model_trace_recorder = model_trace_recorder
        self.model_name = model_name

    async def execute_case(
        self,
        case: D1V5DiscoveryCase,
        *,
        learner_id: str,
        attempt: int,
    ) -> dict[str, Any]:
        pack = self._evidence_pack(case)
        expert = _CapturingAgent(self.expert_agent)
        audit = _CapturingAgent(self.audit_agent)
        registry = AgentRegistry()
        registry.register("frozen_evidence_agent", _FrozenEvidenceAgent(pack))
        registry.register("expert_agent", expert)
        registry.register("audit_agent", audit)
        # Intentionally use an isolated orchestrator with no evolution registry,
        # persistence adapter, business writer, memory writer, or production state.
        orchestrator = Orchestrator(registry, evolution_rule_registry=None)
        plan = ExecutionPlan(
            plan_id=f"D1V5_DISCOVERY_{uuid4().hex}",
            task_type="knowledge_explanation",
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
                    timeout_seconds=2_100,
                    max_retries=0,
                ),
                ExecutionStep(
                    step_id="audit",
                    agent="audit_agent",
                    depends_on=["knowledge", "expert"],
                    audit_subject="resource",
                    timeout_seconds=2_100,
                    max_retries=0,
                ),
            ],
            graph_timeout_seconds=7_200,
        )
        request_id = f"REQ_{uuid4().hex}"
        execution_id = f"EXEC_{uuid4().hex}"
        context = {
            "case_id": f"ISOLATED_{uuid4().hex}",
            "trace_id": f"TRACE_{uuid4().hex}",
            "request_id": request_id,
            "workflow_task_id": request_id,
            "execution_id": execution_id,
            "learner_id": learner_id,
            "task_type": "knowledge_explanation",
            "user_request": case.prompt,
            "original_user_request": case.prompt,
            "messages": [{"role": "user", "content": case.prompt}],
            "available_minutes": 15,
            "user_profile": {},
            "source_policy": {"trusted_source_types": ["reference"]},
            # Satisfies the normal handoff contract without inventing a plan.
            "formal_task": {"status": "not_available"},
            "model_trace_recorder": self.model_trace_recorder,
        }

        self.model_trace_recorder.reset()
        with self.model_trace_recorder.capture_full():
            result = await orchestrator.execute(plan, context)
            if not expert.outputs or not audit.outputs:
                raise RuntimeError(
                    "discovery chain did not produce Expert and Audit outputs: "
                    f"{result.error_type or result.status}: "
                    f"{result.error_message or 'no execution error message'}"
                )
            initial_draft = expert.outputs[0].payload
            initial_audit = audit.outputs[0].payload
            final_draft = expert.outputs[-1].payload
            final_audit = audit.outputs[-1].payload
            candidate_body = self._learner_body(final_draft.content)
            semantic_verdict = await self.semantic_judge.judge(
                question=case.prompt,
                material_a=case.materials[0].text,
                material_b=case.materials[1].text,
                answer=candidate_body,
            )

        trace_summary = self._model_trace_summary(
            case,
            self.model_trace_recorder.items,
        )
        contamination = [
            finding
            for call in trace_summary
            for finding in call["forbidden_markers_found"]
        ]
        if contamination:
            raise ValueError(
                "forbidden evaluator metadata reached a model input: "
                + ", ".join(sorted(set(contamination)))
            )
        release_allowed = bool(
            result.status == "success" and final_audit.decision == "pass"
        )
        return {
            "schema_version": "d1-v5-discovery-receipt-1.0",
            "case_id": case.case_id,
            "attempt": attempt,
            "prompt": case.prompt,
            "materials": [
                {"label": "A" if index == 0 else "B", "text": item.text}
                for index, item in enumerate(case.materials)
            ],
            "model_name": self.model_name,
            "execution_status": result.status,
            "initial_expert_draft": initial_draft.model_dump(mode="json"),
            "first_audit": initial_audit.model_dump(mode="json"),
            "repair_triggered": len(expert.outputs) > 1,
            "repair_trace": [item.model_dump(mode="json") for item in result.repair_trace],
            "post_repair_expert_draft": (
                final_draft.model_dump(mode="json") if len(expert.outputs) > 1 else None
            ),
            "final_audit": final_audit.model_dump(mode="json"),
            "candidate_body_after_pipeline": candidate_body,
            "release_allowed": release_allowed,
            "learner_visible_body": candidate_body if release_allowed else None,
            "semantic_verdict": semantic_verdict,
            "model_input_summaries": trace_summary,
            "isolation_validation": {
                "valid": True,
                "gold_content_loaded": False,
                "candidate_rules_enabled": False,
                "evolution_registry_attached": False,
                "d1_evaluation_mode_enabled": False,
                "conflict_binding_injected": False,
                "dataset_material_ids_exposed_to_models": False,
                "provider_reasoning_exported": False,
                "formal_environment_write_allowed": False,
            },
            "finished_at": _now(),
        }

    @staticmethod
    def _evidence_pack(case: D1V5DiscoveryCase) -> EvidencePack:
        evidence_items = []
        summary_items = []
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
            evidence_pack_id=f"PACK_{uuid4().hex}",
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

    @staticmethod
    def _model_trace_summary(
        case: D1V5DiscoveryCase,
        traces: list[Any],
    ) -> list[dict[str, Any]]:
        forbidden_values = {
            case.case_id,
            *(material.material_id for material in case.materials),
            "semantic_conflict_pair_closure_v1",
        }
        summaries: list[dict[str, Any]] = []
        for trace in traces:
            raw_input = getattr(trace, "raw_input", None) or {}
            findings: list[str] = []

            def inspect(value: Any, path: str = "$") -> None:
                if isinstance(value, dict):
                    for key, nested in value.items():
                        normalized = str(key).strip().lower()
                        if normalized in _FORBIDDEN_MODEL_KEYS:
                            findings.append(f"key:{path}.{normalized}")
                        inspect(nested, f"{path}.{normalized}")
                elif isinstance(value, (list, tuple)):
                    for index, nested in enumerate(value):
                        inspect(nested, f"{path}[{index}]")
                elif isinstance(value, str):
                    for forbidden in forbidden_values:
                        if forbidden and forbidden in value:
                            findings.append(f"value:{forbidden}")

            inspect(raw_input)
            business_payload = raw_input.get("payload")
            summaries.append(
                {
                    "sequence": int(getattr(trace, "sequence", 0)),
                    "agent": str(getattr(trace, "agent", "")),
                    "input_sha256": str(getattr(trace, "input_digest", "") or ""),
                    "input_chars": int(getattr(trace, "input_chars", 0) or 0),
                    "payload_keys": (
                        sorted(str(key) for key in business_payload)
                        if isinstance(business_payload, dict)
                        else []
                    ),
                    "forbidden_markers_found": sorted(set(findings)),
                    "total_duration_ms": getattr(trace, "total_duration_ms", None),
                    "request_attempt_count": getattr(trace, "request_attempt_count", None),
                    "error_type": getattr(trace, "error_type", None),
                }
            )
        return summaries


class D1V5DiscoveryBatchService:
    def __init__(
        self,
        *,
        dataset: D1V5DiscoveryDataset,
        executor: D1V5DiscoveryExecutor,
        runtime_mode: str,
        state_root: Path,
        run_gate: D1V5EvaluationRunGate | None = None,
        max_attempts: int = 2,
    ) -> None:
        self.dataset = dataset
        self.executor = executor
        self.runtime_mode = runtime_mode
        self.state_root = state_root
        self.run_gate = run_gate
        self.max_attempts = max_attempts
        self._runs: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = RLock()

    def manifest_payload(self) -> dict[str, Any]:
        return self.dataset.manifest_payload(runtime_mode=self.runtime_mode)

    def start(self, *, requested_by: str) -> dict[str, Any]:
        if self.runtime_mode != "live":
            raise ValueError("D1 V5 discovery requires COMPETITION_APP_MODE=live")
        with self._lock:
            if any(run["status"] in {"queued", "running"} for run in self._runs.values()):
                raise ValueError("another D1 V5 discovery batch is already running")
            run_id = f"D1V5DISC_{uuid4().hex}"
            if self.run_gate is not None:
                self.run_gate.acquire(run_id)
            run = {
                "schema_version": "d1-v5-discovery-run-1.0",
                "run_id": run_id,
                "requested_by": requested_by,
                "status": "queued",
                "dataset_id": _EXPECTED_DATASET_ID,
                "dataset_version": _EXPECTED_VERSION,
                "runtime_file_sha256": self.dataset.runtime_sha256,
                "total_case_count": len(self.dataset.cases),
                "attempted_case_count": 0,
                "completed_case_count": 0,
                "technical_failure_count": 0,
                "active_case_id": None,
                "active_attempt": None,
                "receipts": [],
                "technical_errors": [],
                "created_at": _now(),
                "started_at": None,
                "finished_at": None,
                "formal_environment_write_allowed": False,
            }
            self._runs[run_id] = run
            self._persist_locked(run)
            self._tasks[run_id] = asyncio.create_task(self._run(run_id))
            return self._public_status(run)

    def is_owned_by(self, run_id: str, user_id: str) -> bool:
        with self._lock:
            if run_id not in self._runs:
                raise KeyError(run_id)
            return self._runs[run_id]["requested_by"] == user_id

    def status(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            if run_id not in self._runs:
                raise KeyError(run_id)
            return self._public_status(self._runs[run_id])

    def receipts(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            if run_id not in self._runs:
                raise KeyError(run_id)
            run = self._runs[run_id]
            return {
                "schema_version": "d1-v5-discovery-receipts-1.0",
                "run": self._public_status(run),
                "receipts": json.loads(json.dumps(run["receipts"], ensure_ascii=False)),
                "technical_errors": list(run["technical_errors"]),
                "artifact_file": str(self._artifact_path(run_id)),
            }

    async def cleanup(self, run_id: str, *, purge_artifact: bool = True) -> dict[str, Any]:
        task: asyncio.Task[None] | None = None
        with self._lock:
            if not run_id.startswith("D1V5DISC_"):
                raise KeyError(run_id)
            task = self._tasks.get(run_id)
            if task is not None and not task.done():
                task.cancel()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)
        with self._lock:
            existed = self._runs.pop(run_id, None) is not None
            self._tasks.pop(run_id, None)
            lease_released = bool(
                self.run_gate is not None and self.run_gate.release(run_id)
            )
        artifact = self._artifact_path(run_id)
        artifact_purged = False
        if purge_artifact and artifact.parent.resolve() == self.state_root.resolve():
            if artifact.is_file():
                artifact.unlink()
                artifact_purged = True
        return {
            "run_id": run_id,
            "cleanup_completed": True,
            "in_memory_run_removed": existed,
            "background_task_cancelled": task is not None and task.cancelled(),
            "artifact_purged": artifact_purged,
            "evaluation_lease_released": lease_released,
            "formal_environment_write_allowed": False,
        }

    async def shutdown(self) -> None:
        with self._lock:
            tasks = [task for task in self._tasks.values() if not task.done()]
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        with self._lock:
            self._tasks.clear()

    async def _run(self, run_id: str) -> None:
        self._update(run_id, status="running", started_at=_now())
        pseudonymous_learner_id = "D1V5_EVAL_" + _sha256_bytes(
            self._runs[run_id]["requested_by"].encode("utf-8")
        )[:16]
        try:
            for case in self.dataset.cases:
                completed = False
                for attempt in range(1, self.max_attempts + 1):
                    self._update(
                        run_id,
                        active_case_id=case.case_id,
                        active_attempt=attempt,
                    )
                    try:
                        receipt = await self.executor.execute_case(
                            case,
                            learner_id=pseudonymous_learner_id,
                            attempt=attempt,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        error_text = f"{type(exc).__name__}:{exc}"
                        with self._lock:
                            run = self._runs[run_id]
                            run["technical_errors"].append(
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
                            self._persist_locked(run)
                        continue
                    with self._lock:
                        run = self._runs[run_id]
                        run["receipts"].append(receipt)
                        run["completed_case_count"] += 1
                        self._persist_locked(run)
                    completed = True
                    break
                with self._lock:
                    run = self._runs[run_id]
                    run["attempted_case_count"] += 1
                    if not completed:
                        run["technical_failure_count"] += 1
                    run["active_case_id"] = None
                    run["active_attempt"] = None
                    self._persist_locked(run)
            with self._lock:
                run = self._runs[run_id]
                run["status"] = (
                    "completed"
                    if run["completed_case_count"] == run["total_case_count"]
                    else "failed"
                )
                run["finished_at"] = _now()
                self._persist_locked(run)
        except asyncio.CancelledError:
            self._update(
                run_id,
                status="interrupted",
                finished_at=_now(),
                active_case_id=None,
                active_attempt=None,
            )
            raise
        finally:
            with self._lock:
                self._tasks.pop(run_id, None)

    def _update(self, run_id: str, **changes: Any) -> None:
        with self._lock:
            run = self._runs[run_id]
            run.update(changes)
            self._persist_locked(run)

    def _artifact_path(self, run_id: str) -> Path:
        return self.state_root / f"{run_id}.json"

    def _persist_locked(self, run: dict[str, Any]) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        target = self._artifact_path(str(run["run_id"]))
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(run, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)

    @staticmethod
    def _public_status(run: dict[str, Any]) -> dict[str, Any]:
        return {
            key: run.get(key)
            for key in (
                "schema_version",
                "run_id",
                "status",
                "dataset_id",
                "dataset_version",
                "runtime_file_sha256",
                "total_case_count",
                "attempted_case_count",
                "completed_case_count",
                "technical_failure_count",
                "active_case_id",
                "active_attempt",
                "created_at",
                "started_at",
                "finished_at",
                "formal_environment_write_allowed",
            )
        }

"""Innovation-1 deterministic accountability smoke evaluation.

This is the low-cost layer of the v2 evaluation.  It exercises the production
AuditAgent, AuditFindingsCompiler, responsibility resolver, repair controller
and Orchestrator without calling a remote model.  Controlled agents inject one
fault on their first call and recover normally when the real repair chain reruns
them; no oracle answer is inserted during repair.

Usage:
  python evaluation_delivery/codex7_accountability.py --limit 20
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from competition_app.agents.audit import AuditAgent  # noqa: E402
from competition_app.agents.common import envelope  # noqa: E402
from competition_app.contracts.execution import ExecutionPlan, ExecutionStep  # noqa: E402
from competition_app.contracts.knowledge import EvidenceItem, EvidencePack  # noqa: E402
from competition_app.contracts.resource import ResourceClaim, ResourceDraft  # noqa: E402
from competition_app.llm.stub import StubChatModel  # noqa: E402
from competition_app.runtime.agent_registry import AgentRegistry  # noqa: E402
from competition_app.runtime.orchestrator import Orchestrator  # noqa: E402


FAULT_EXPERT_FAKE = "expert_fake_evidence_id"
FAULT_EXPERT_CONTRADICTION = "expert_evidence_contradiction"
FAULT_KNOWLEDGE_INVALID = "knowledge_invalid_evidence_pack"
FAULT_NONE = "none"


class SmokeAuditModel:
    """Deterministic semantic auditor plus the production stub Compiler."""

    def __init__(self) -> None:
        self.compiler = StubChatModel()

    async def complete_json(self, role, payload, on_delta=None):
        if role == "audit_findings_compiler":
            return await self.compiler.complete_json(
                role, payload, on_delta=on_delta
            )
        claim_texts = (
            payload.get("payload", {})
            .get("semantic_resource", {})
            .get("claim_texts", [])
        )
        if any("错误结论" in str(item) for item in claim_texts):
            return {
                "decision": "revise",
                "findings": [
                    "正文给出了错误结论，与本次教材证据明确相反，属于判定错误。"
                ],
                "audit_report": "发现可定位的知识性判定错误，必须返修。",
            }
        return {
            "decision": "pass",
            "findings": [],
            "audit_report": "确定性证据门禁和语义审核均已通过。",
        }


class ControlledKnowledgeAgent:
    def __init__(self, fault_type: str) -> None:
        self.fault_type = fault_type
        self.calls = 0

    async def run(self, context):
        self.calls += 1
        first_invalid_pack = (
            self.fault_type == FAULT_KNOWLEDGE_INVALID and self.calls == 1
        )
        items = [
            EvidenceItem(
                evidence_id="EVIDENCE_1",
                source_id="TEXTBOOK_1",
                content_summary="四君子汤由人参、白术、茯苓、甘草组成。",
                authority_level="textbook",
                confidence=1.0,
            )
        ]
        pack = EvidencePack(
            evidence_pack_id=f"PACK_{context['case_id']}_{self.calls}",
            query="四君子汤组成",
            resolved_kp_ids=["KP_1"],
            evidence_items=items,
            summary_evidence_ids=(
                ["EVIDENCE_SUMMARY_MISSING"] if first_invalid_pack else []
            ),
        )
        return envelope(context, "knowledge_base_agent", "evidence_pack", pack)


class ControlledExpertAgent:
    def __init__(self, fault_type: str) -> None:
        self.fault_type = fault_type
        self.calls = 0

    async def run(self, context):
        self.calls += 1
        first_fault = self.calls == 1
        evidence_ids = ["EVIDENCE_1"]
        claim_text = "四君子汤由人参、白术、茯苓、甘草组成。"
        if self.fault_type == FAULT_EXPERT_FAKE and first_fault:
            evidence_ids = ["EVIDENCE_FAKE"]
        if self.fault_type == FAULT_KNOWLEDGE_INVALID and first_fault:
            # The Expert consumes the invalid summary reference emitted by
            # Knowledge.  Audit can therefore locate the upstream contract
            # fault without relying on hidden ground truth or model wording.
            evidence_ids = ["EVIDENCE_SUMMARY_MISSING"]
        if self.fault_type == FAULT_EXPERT_CONTRADICTION and first_fault:
            claim_text = "错误结论：四君子汤不含人参。"
        draft = ResourceDraft(
            resource_draft_id=f"DRAFT_{context['case_id']}_{self.calls}",
            title="四君子汤组成讲解",
            target_kp_id="KP_1",
            content={"body": claim_text},
            estimated_minutes=5,
            claims=[
                ResourceClaim(
                    claim_id="CLAIM_1",
                    text=claim_text,
                    evidence_ids=evidence_ids,
                )
            ],
        )
        return envelope(context, "expert_agent", "resource_draft", draft)


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    fault_type: str
    gold_owner_step_ids: tuple[str, ...]
    gold_rerun_step_ids: tuple[str, ...]


def _case_specs(minimum_count: int = 8) -> list[CaseSpec]:
    """Build a balanced deterministic set large enough for ``--limit``.

    Each four-case block contains three independently executed fault classes
    and one clean control.  Case IDs remain unique so a larger smoke run tests
    repeated orchestration/repair isolation instead of truncating at the
    original eight fixtures.
    """

    specs: list[CaseSpec] = []
    block_count = max(2, (max(1, minimum_count) + 3) // 4)
    for index in range(block_count):
        specs.extend(
            [
                CaseSpec(
                    case_id=f"ACC_FAKE_{index + 1}",
                    fault_type=FAULT_EXPERT_FAKE,
                    gold_owner_step_ids=("expert",),
                    gold_rerun_step_ids=("expert", "audit"),
                ),
                CaseSpec(
                    case_id=f"ACC_CONTRA_{index + 1}",
                    fault_type=FAULT_EXPERT_CONTRADICTION,
                    gold_owner_step_ids=("expert",),
                    gold_rerun_step_ids=("expert", "audit"),
                ),
                CaseSpec(
                    case_id=f"ACC_KNOWLEDGE_{index + 1}",
                    fault_type=FAULT_KNOWLEDGE_INVALID,
                    gold_owner_step_ids=("knowledge",),
                    gold_rerun_step_ids=("knowledge", "expert", "audit"),
                ),
                CaseSpec(
                    case_id=f"ACC_CLEAN_{index + 1}",
                    fault_type=FAULT_NONE,
                    gold_owner_step_ids=(),
                    gold_rerun_step_ids=(),
                ),
            ]
        )
    return specs


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="ACCOUNTABILITY_SMOKE",
        task_type="knowledge_explanation",
        steps=[
            ExecutionStep(step_id="knowledge", agent="knowledge_base_agent"),
            ExecutionStep(
                step_id="expert",
                agent="expert_agent",
                depends_on=["knowledge"],
            ),
            ExecutionStep(
                step_id="audit",
                agent="audit_agent",
                depends_on=["knowledge", "expert"],
            ),
        ],
    )


async def _run_case(spec: CaseSpec) -> dict[str, Any]:
    knowledge = ControlledKnowledgeAgent(spec.fault_type)
    expert = ControlledExpertAgent(spec.fault_type)
    registry = AgentRegistry()
    registry.register("knowledge_base_agent", knowledge)
    registry.register("expert_agent", expert)
    registry.register("audit_agent", AuditAgent(SmokeAuditModel()))
    result = await Orchestrator(registry).execute(
        _plan(),
        {
            "case_id": spec.case_id,
            "trace_id": f"TRACE_{spec.case_id}",
            "request_id": f"REQUEST_{spec.case_id}",
            "execution_id": f"EXECUTION_{spec.case_id}",
            "learner_id": "ACCOUNTABILITY_SMOKE_USER",
            "task_type": "knowledge_explanation",
            "available_minutes": 15,
            "user_request": "请依据教材解释四君子汤的组成。",
            "source_policy": {
                "trusted_source_types": ["textbook", "knowledge_base"]
            },
            "formal_task": {
                "task_content": "依据教材解释四君子汤组成",
                "expected_output": "一段有证据的组成讲解",
                "completion_criteria": "组成准确且证据引用有效",
            },
        },
    )
    repair = result.repair_trace[0] if result.repair_trace else None
    audit = getattr(result.outputs.get("audit"), "payload", None)
    expert_payload = getattr(result.outputs.get("expert"), "payload", None)
    final_claims = list(getattr(expert_payload, "claims", []) or [])
    final_correct = bool(final_claims) and all(
        "错误结论" not in str(item.text)
        and "EVIDENCE_FAKE" not in set(item.evidence_ids)
        and "EVIDENCE_SUMMARY_MISSING" not in set(item.evidence_ids)
        for item in final_claims
    )
    predicted_owners = tuple(repair.owner_step_ids) if repair else ()
    actual_rerun = tuple(repair.rerun_step_ids) if repair else ()
    is_fault = spec.fault_type != FAULT_NONE
    detected = bool(repair and repair.initial_audit_decision == "revise")
    final_pass = getattr(audit, "decision", None) == "pass"
    return {
        "case_id": spec.case_id,
        "mode": "deterministic_smoke",
        "fault_type": spec.fault_type,
        "gold_owner_step_ids": list(spec.gold_owner_step_ids),
        "predicted_owner_step_ids": list(predicted_owners),
        "gold_rerun_step_ids": list(spec.gold_rerun_step_ids),
        "actual_rerun_step_ids": list(actual_rerun),
        "fault_detected": detected,
        "strict_owner_correct": predicted_owners == spec.gold_owner_step_ids,
        "rerun_chain_correct": actual_rerun == spec.gold_rerun_step_ids,
        "final_audit_decision": getattr(audit, "decision", None),
        "final_answer_correct": final_correct,
        "actual_repair_success": (
            detected and final_correct and final_pass if is_fault else None
        ),
        "clean_false_positive": (
            getattr(audit, "decision", None) != "pass" if not is_fault else None
        ),
        "preserved_outputs_unchanged": (
            repair.preserved_outputs_unchanged if repair else None
        ),
        "knowledge_calls": knowledge.calls,
        "expert_calls": expert.calls,
        "execution_status": result.status,
    }


def _rate(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [row[key] for row in rows if row.get(key) is not None]
    successes = sum(value is True for value in values)
    return {
        "successes": successes,
        "total": len(values),
        "rate": round(successes / len(values), 4) if values else None,
    }


async def _main(limit: int, output_dir: Path) -> tuple[Path, Path, dict[str, Any]]:
    specs = _case_specs(limit)[:limit]
    rows = [await _run_case(spec) for spec in specs]
    faults = [row for row in rows if row["fault_type"] != FAULT_NONE]
    metrics = {
        "mode": "deterministic_smoke",
        "case_count": len(rows),
        "fault_case_count": len(faults),
        "clean_case_count": len(rows) - len(faults),
        "fault_detection_rate": _rate(faults, "fault_detected"),
        "strict_owner_accuracy": _rate(faults, "strict_owner_correct"),
        "actual_repair_success_rate": _rate(faults, "actual_repair_success"),
        "rerun_chain_accuracy": _rate(faults, "rerun_chain_correct"),
        "preserved_output_integrity": _rate(
            faults, "preserved_outputs_unchanged"
        ),
        "clean_false_positive_rate": _rate(rows, "clean_false_positive"),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jsonl_path = output_dir / f"accountability_smoke_{stamp}.jsonl"
    metrics_path = output_dir / f"accountability_smoke_{stamp}_metrics.json"
    jsonl_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return jsonl_path, metrics_path, metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent / "outputs" / "accountability_v2",
    )
    args = parser.parse_args()
    rows_path, metrics_path, summary = asyncio.run(
        _main(max(1, args.limit), args.output_dir)
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"rows={rows_path}")
    print(f"metrics={metrics_path}")

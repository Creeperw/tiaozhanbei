from __future__ import annotations

"""Isolated, no-writeback execution for the D1 pilot.

This module deliberately does not modify the production orchestrator or any
production prompt.  It reuses the configured retrieval, Expert and Audit
implementations behind a small evaluation-only adapter, freezes the retrieved
pack before either arm runs, and returns a compact receipt for the D1 runner.
"""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any
from uuid import uuid4

from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.knowledge import EvidenceItem, EvidencePack, RetrievalSummaryItem
from competition_app.evaluation.d1_precheck_dataset import D1PilotCase
from competition_app.evaluation.d1_precheck_runner import D1PrecheckRunner
from competition_app.evaluation.semantic_conflict_closure import (
    ConflictEvidencePair,
    SemanticConflictBinding,
    SemanticConflictClosureInput,
    evaluate_semantic_conflict_closure,
)


# The evaluation budget is deliberately owned by the isolated executor rather
# than by either model.  A value of ``RMAX + 1`` in ``repair_count`` is the
# explicit sentinel for an exhausted budget that still failed the final Audit.
RMAX = 2


@dataclass(frozen=True)
class D1ExecutionDependencies:
    retrieval_tool: Any
    expert_agent: Any
    audit_agent: Any
    semantic_judge: Any | None = None


class D1PrecheckExecutionService:
    """Run one frozen A/B pair using real configured agents without writeback."""

    def __init__(
        self,
        dependencies: D1ExecutionDependencies,
        runner: D1PrecheckRunner | None = None,
        *,
        max_repair_attempts: int = RMAX,
    ) -> None:
        if max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must be non-negative")
        self.dependencies = dependencies
        self.runner = runner or D1PrecheckRunner()
        self.max_repair_attempts = max_repair_attempts

    async def execute_pair(self, case_id: str, *, learner_id: str) -> dict[str, Any]:
        prepared = self.runner.prepare_pair(case_id)
        case = next(item for item in self.runner.cases if item.case_id == case_id)
        if not learner_id.strip():
            raise ValueError("D1 execution requires an authenticated learner")

        pack = await self._retrieve_frozen_pack(case)
        pair = self._inject_isolated_conflict(case, pack)
        frozen_pack = pair["pack"]
        pack_digest = _digest(frozen_pack)
        arm_results: dict[str, dict[str, Any]] = {}
        for arm in prepared.arms:
            arm_results[arm.arm] = await self._run_arm(
                case,
                arm=arm.arm,
                rule_enabled=arm.rule_enabled,
                pack=frozen_pack,
                pack_digest=pack_digest,
                pair=pair.get("binding"),
                learner_id=learner_id,
            )

        receipt = {
            "schema_version": "d1-precheck-pair-1.1",
            "case_id": case_id,
            "arms": [
                {
                    "arm": arm,
                    "input_digest": prepared_arm.input_digest,
                    "rule_exposed": arm_results[arm]["rule_exposed"],
                    "exposed_target_agent": arm_results[arm]["exposed_target_agent"],
                    "user_output": arm_results[arm]["user_output"],
                    "target_failure": arm_results[arm]["target_failure"],
                    "closure_allowed": arm_results[arm]["closure_allowed"],
                    "first_audit_decision": arm_results[arm]["first_audit_decision"],
                    "final_audit_decision": arm_results[arm]["final_audit_decision"],
                    "repair_count": arm_results[arm]["repair_count"],
                    "repair_attempt_count": arm_results[arm]["repair_attempt_count"],
                    "repair_exhausted": arm_results[arm]["repair_exhausted"],
                    "max_repair_attempts": arm_results[arm]["max_repair_attempts"],
                    "release_allowed": arm_results[arm]["release_allowed"],
                    "initial_target_failure": arm_results[arm]["initial_target_failure"],
                    "final_target_failure": arm_results[arm]["final_target_failure"],
                    "actual_rerun_step_ids": arm_results[arm]["actual_rerun_step_ids"],
                    "same_conflict_pair_resolved": arm_results[arm]["same_conflict_pair_resolved"],
                    "new_unsupported_claims": arm_results[arm]["new_unsupported_claims"],
                    "audit_conflicting_evidence": arm_results[arm]["audit_conflicting_evidence"],
                    "semantic_conflict_binding_created": arm_results[arm]["semantic_conflict_binding_created"],
                    "closure_rule_applied": arm_results[arm]["closure_rule_applied"],
                    "semantic_verdict": arm_results[arm]["semantic_verdict"],
                }
                for arm, prepared_arm in ((item.arm, item) for item in prepared.arms)
            ],
        }
        validation = self.runner.validate_payload(receipt)
        return {
            "schema_version": "d1-precheck-execution-1.1",
            "case_id": case_id,
            "pair_order": prepared.pair_order,
            "formal_environment_write_allowed": False,
            "retrieval_pack_digest": pack_digest,
            "conflict_binding": (
                pair["binding"].model_dump(mode="json") if pair.get("binding") else None
            ),
            "receipt": receipt,
            "validation": validation,
            "arms": arm_results,
        }

    async def _retrieve_frozen_pack(self, case: D1PilotCase) -> EvidencePack:
        fixture = case.frozen_evidence
        if fixture is not None:
            items = [
                EvidenceItem(
                    evidence_id=fixture.evidence_a_id,
                    source_id=f"CURRICULUM_SOURCE_A_{case.case_id}",
                    content_summary=fixture.evidence_a_text,
                    authority_level="textbook",
                    confidence=0.99,
                    resource_type="textbook",
                    source_label="教材或课程材料 A",
                ),
                EvidenceItem(
                    evidence_id=fixture.evidence_b_id,
                    source_id=f"CURRICULUM_SOURCE_B_{case.case_id}",
                    content_summary=fixture.evidence_b_text,
                    authority_level="reference",
                    confidence=0.85,
                    resource_type="reference",
                    source_label="教材或课程材料 B",
                ),
            ]
            summary_items = [
                RetrievalSummaryItem(
                    evidence_id=item.evidence_id,
                    source_id=item.source_id,
                    authority_level=item.authority_level,
                    resource_type=item.resource_type,
                    source_label=item.source_label,
                    content=item.content_summary,
                )
                for item in items
            ]
            return EvidencePack(
                evidence_pack_id=f"D1V2_PACK_{case.case_id}",
                query=case.prompt,
                evidence_items=items,
                retrieval_summary="\n".join(item.content_summary for item in items),
                summary_items=summary_items,
                summary_evidence_ids=[item.evidence_id for item in items],
                conflict_evidence=(
                    [fixture.evidence_a_id, fixture.evidence_b_id]
                    if fixture.gold_relation == "contradiction"
                    else []
                ),
                risk_notes=["材料用于知识讲解，不构成现实诊疗建议。"],
            )
        tool = self.dependencies.retrieval_tool
        handler = getattr(tool, "get_kp_with_content", None)
        if handler is None:
            raise RuntimeError("D1 evaluation requires the configured retrieval tool")
        pack = await handler(case.prompt, concepts=[])
        if not isinstance(pack, EvidencePack):
            raise TypeError("retrieval tool returned an invalid EvidencePack")
        if len(pack.evidence_items) < 2:
            raise RuntimeError("D1 evaluation requires at least two retrieved evidence items")
        # Freeze a private deep copy. Nothing from this fixture is written back.
        return pack.model_copy(deep=True)

    @staticmethod
    def _inject_isolated_conflict(case: D1PilotCase, pack: EvidencePack) -> dict[str, Any]:
        fixture = case.frozen_evidence
        if fixture is not None:
            if fixture.gold_relation != "contradiction":
                return {"pack": pack, "binding": None}
            return {
                "pack": pack,
                "binding": SemanticConflictBinding(
                    issue_id=f"D1_{case.case_id}",
                    claim_location="resource:claims",
                    evidence_pair=ConflictEvidencePair(
                        support_evidence_id=fixture.evidence_a_id,
                        conflict_evidence_id=fixture.evidence_b_id,
                    ),
                    owner_step_id="expert",
                ),
            }
        if case.case_group != "target_fault":
            return {"pack": pack, "binding": None}
        first, second = pack.evidence_items[:2]
        pair = ConflictEvidencePair(
            support_evidence_id=first.evidence_id,
            conflict_evidence_id=second.evidence_id,
        )
        scenario_text = {
            "expert_conflict_pair_resolvable": (
                "该来源明确支持目标概念的定义；另一来源对同一概念给出不同表述。"
            ),
            "expert_conflict_pair_residual_after_repair": (
                "该来源明确支持目标概念的适用范围；另一来源把范围扩大到相反结论。"
            ),
            "expert_conflict_pair_expanded_rerun": (
                "该来源给出目标概念的判断条件；另一来源对同一判断条件作出相反说明。"
            ),
        }.get(case.scenario, "两条来源对同一主张存在需要核对的不同表述。")
        first_update = f"{scenario_text}支持侧原文：{first.content_summary}"
        second_update = f"{scenario_text}冲突侧原文：与支持侧结论相反；原始材料：{second.content_summary}"
        items = [
            first.model_copy(update={"content_summary": first_update}),
            second.model_copy(update={"content_summary": second_update}),
            *pack.evidence_items[2:],
        ]
        summary_items = []
        for item in pack.summary_items:
            if item.evidence_id == first.evidence_id:
                summary_items.append(item.model_copy(update={"content": first_update}))
            elif item.evidence_id == second.evidence_id:
                summary_items.append(item.model_copy(update={"content": second_update}))
            else:
                summary_items.append(item)
        updated = pack.model_copy(
            update={
                "evidence_items": items,
                "summary_items": summary_items,
                "conflict_evidence": [first.evidence_id, second.evidence_id],
            },
            deep=True,
        )
        return {
            "pack": updated,
            "binding": SemanticConflictBinding(
                issue_id=f"D1_{case.case_id}",
                claim_location="resource:claims",
                evidence_pair=pair,
                owner_step_id="expert",
            ),
        }

    async def _run_arm(
        self,
        case: D1PilotCase,
        *,
        arm: str,
        rule_enabled: bool,
        pack: EvidencePack,
        pack_digest: str,
        pair: SemanticConflictBinding | None,
        learner_id: str,
    ) -> dict[str, Any]:
        base = self._context(case, arm=arm, learner_id=learner_id, pack=pack)
        # Keep both arms on the same production prose protocol. This flag
        # selects isolated D1 bookkeeping only; it must not switch Expert from
        # complete_text to a long-body JSON response.
        base["d1_evaluation_mode"] = True
        if rule_enabled and pair is not None:
            base["d1_conflict_binding"] = {
                "issue_id": pair.issue_id,
                "claim_location": pair.claim_location,
                "required_evidence_ids": [
                    pair.evidence_pair.support_evidence_id,
                    pair.evidence_pair.conflict_evidence_id,
                ],
            }
        if rule_enabled:
            base["evolution_strategies"] = [
                {
                    "rule_id": "semantic_conflict_pair_closure_v1",
                    "version": 1,
                    "target_agent": "expert_agent",
                    "target_step_id": "expert",
                    "strategy_text": (
                        "当两条当前证据对同一声明存在语义冲突时，必须绑定这两条证据，"
                        "只返修该声明并重新运行 expert→audit；返修后再次比较同一证据对，"
                        "无法闭环时不得放行。"
                    ),
                }
            ]
        first_expert = await self.dependencies.expert_agent.run(base)
        first_audit = await self.dependencies.audit_agent.run(
            self._audit_context(base, pack=pack, expert=first_expert)
        )
        initial = self._arm_state(first_expert, first_audit, pack, pair)
        final = initial
        first_audit_decision = str(initial["audit_decision"])
        current_audit = first_audit
        repair_attempts = 0
        actual_chain: list[str] = []

        # Both arms receive the same bounded repair opportunity.  The only
        # treatment difference is the strategy contract already attached to B:
        # A gets a generic Audit-feedback repair, while B gets the
        # code-owned conflict-pair repair.  A conflict binding is required for
        # this D1 repair loop; compatible and ordinary controls must not be
        # turned into artificial repairs merely because B received the rule.
        while (
            pair is not None
            and str(final["audit_decision"]) != "pass"
            and repair_attempts < self.max_repair_attempts
        ):
            repair_attempts += 1
            repair_context = self._repair_context(
                base,
                pair=pair,
                audit=first_audit if repair_attempts == 1 else current_audit,
                rule_enabled=rule_enabled,
            )
            repaired_expert = await self.dependencies.expert_agent.run(repair_context)
            repaired_audit = await self.dependencies.audit_agent.run(
                self._audit_context(repair_context, pack=pack, expert=repaired_expert)
            )
            final = self._arm_state(repaired_expert, repaired_audit, pack, pair)
            current_audit = repaired_audit
            # The receipt records the unique rerun chain, not one repeated copy
            # per round.  The separate repair_attempt_count below preserves the
            # exact number of model repair rounds.
            actual_chain = ["expert", "audit"]

        exhausted = bool(
            pair is not None
            and repair_attempts == self.max_repair_attempts
            and str(final["audit_decision"]) != "pass"
        )
        repair_count = self.max_repair_attempts + 1 if exhausted else repair_attempts

        closure_allowed = False
        if pair is None:
            # No conflict binding means that the D1 closure rule is not
            # applicable.  This is not itself a business success: the final
            # Audit still has to pass, and unsupported claims still block
            # release.  Keeping this distinction is required for compatible
            # boundary false-positive measurement.
            closure_allowed = (
                str(final["audit_decision"]) == "pass"
                and not final["new_unsupported_claims"]
            )
        else:
            closure_input = SemanticConflictClosureInput(
                binding=pair,
                current_evidence_ids=[item.evidence_id for item in pack.evidence_items],
                allowed_location_keys=["resource:claims"],
                actual_rerun_step_ids=list(actual_chain),
                initial_audit_decision=first_audit_decision,
                same_conflict_pair_resolved=final["same_conflict_pair_resolved"],
                new_unsupported_claims=final["new_unsupported_claims"],
            )
            closure_allowed = (
                evaluate_semantic_conflict_closure(
                    closure_input,
                    enabled=rule_enabled,
                ).allowed
                if rule_enabled
                else True
            )
            if rule_enabled and str(final["audit_decision"]) != "pass":
                closure_allowed = False
        release_allowed = bool(
            str(final["audit_decision"]) == "pass"
            and closure_allowed
            and not final["new_unsupported_claims"]
        )
        semantic_verdict = await self._judge_final_output(
            case,
            final["user_output"],
        )
        return {
            "rule_exposed": bool(rule_enabled),
            "exposed_target_agent": "expert_agent" if rule_enabled else None,
            "user_output": final["user_output"],
            # ``target_failure`` is retained as the pre-repair defect label for
            # compatibility with the original D1 receipts.  Consumers that
            # need the final business outcome should use final_target_failure
            # or release_allowed instead.
            "target_failure": bool(initial["target_failure"]),
            "initial_target_failure": bool(initial["target_failure"]),
            "final_target_failure": bool(final["target_failure"]),
            "closure_allowed": closure_allowed,
            "release_allowed": release_allowed,
            "first_audit_decision": first_audit_decision,
            "final_audit_decision": final["audit_decision"],
            "repair_count": repair_count,
            "repair_attempt_count": repair_attempts,
            "repair_exhausted": exhausted,
            "max_repair_attempts": self.max_repair_attempts,
            "audit_decision": final["audit_decision"],
            "same_conflict_pair_resolved": final["same_conflict_pair_resolved"],
            "new_unsupported_claims": final["new_unsupported_claims"],
            "actual_rerun_step_ids": actual_chain,
            "pack_digest": pack_digest,
            "audit_conflicting_evidence": final["audit_conflicting_evidence"],
            "semantic_conflict_binding_created": pair is not None,
            "closure_rule_applied": bool(rule_enabled and pair is not None),
            "semantic_verdict": semantic_verdict,
        }

    async def _judge_final_output(
        self,
        case: D1PilotCase,
        user_output: str,
    ) -> dict[str, Any]:
        """Blindly judge one final body without treatment or system metadata."""

        judge = self.dependencies.semantic_judge
        fixture = case.frozen_evidence
        if judge is None or fixture is None:
            return {
                "schema_version": "d1-semantic-verdict-1.0",
                "judge_version": "unconfigured",
                "status": "unavailable",
                "acceptable": None,
                "request_fulfilled": None,
                "material_a_handled_correctly": None,
                "material_b_handled_correctly": None,
                "relationship_handled_correctly": None,
                "no_unsupported_resolution": None,
                "rationale_code": "judge_unavailable",
                "error_type": "JudgeNotConfigured",
            }
        return await judge.judge(
            question=case.prompt,
            material_a=fixture.evidence_a_text,
            material_b=fixture.evidence_b_text,
            answer=user_output,
        )

    @staticmethod
    def _repair_context(
        base: dict[str, Any],
        *,
        pair: SemanticConflictBinding,
        audit: Any,
        rule_enabled: bool,
    ) -> dict[str, Any]:
        """Build one isolated repair request without changing the paired input.

        The baseline arm deliberately receives no evolution strategy.  It is
        still allowed to perform the same ordinary Audit-feedback repair so
        that repair budget, stopping conditions, and Audit observations are
        symmetric between A and B.
        """

        context = deepcopy(base)
        if rule_enabled:
            instruction = {
                "issue_ids": [pair.issue_id],
                "locations": [pair.claim_location],
                "repair_instruction": (
                    "只修复同一冲突声明：同时比较并绑定支持侧与冲突侧证据，"
                    "不得添加未在当前证据包中的声明；修复后保持自然语言输出。"
                ),
            }
        else:
            instruction = {
                "issue_ids": [],
                "locations": [],
                "repair_instruction": (
                    "根据审核反馈修复当前资源中的问题；只使用本轮证据包，"
                    "保持原任务范围，不得添加无法由当前证据支持的新声明；"
                    "修复后保持自然语言输出。"
                ),
            }
        context["repair_instruction"] = instruction
        context["audit_feedback"] = audit
        return context

    @staticmethod
    def _context(case: D1PilotCase, *, arm: str, learner_id: str, pack: EvidencePack) -> dict[str, Any]:
        token = uuid4().hex
        knowledge = AgentEnvelope(
            artifact_id=f"D1_EVIDENCE_{token}",
            artifact_type="evidence_pack",
            case_id=case.case_id,
            trace_id=f"D1_TRACE_{token}",
            request_id=f"D1_REQ_{token}",
            execution_id=f"D1_EXEC_{token}",
            step_id="knowledge",
            producer="knowledge_base_agent",
            task_type=case.expected_task_type,
            learner_id=learner_id,
            payload=pack,
        )
        return {
            "thread_id": f"D1_{case.case_id}_{arm}_{token}",
            "case_id": case.case_id,
            "trace_id": knowledge.trace_id,
            "request_id": knowledge.request_id,
            "execution_id": knowledge.execution_id,
            "workflow_task_id": knowledge.request_id,
            "step_id": "expert",
            "learner_id": learner_id,
            "task_type": case.expected_task_type,
            "user_request": case.prompt,
            "original_user_request": case.prompt,
            "messages": [{"role": "user", "content": case.prompt}],
            "dependency_outputs": {"knowledge": knowledge},
            "user_profile": {},
            "formal_environment_write_allowed": False,
            # Explicit opt-in: set per arm below. The baseline arm must not
            # receive the D1 contract, otherwise A/B would differ by more than
            # the candidate rule switch.
            "d1_evaluation_mode": False,
        }

    @staticmethod
    def _audit_context(base: dict[str, Any], *, pack: EvidencePack, expert: Any) -> dict[str, Any]:
        context = deepcopy(base)
        # Audit is the paired evaluator, not a treatment target.  Re-audit
        # must independently judge the produced artifact without learning the
        # arm assignment, candidate strategy, binding contract or prior repair
        # instructions.  The Expert output itself remains fully visible.
        for key in (
            "evolution_strategies",
            "d1_evaluation_mode",
            "d1_conflict_binding",
            "repair_instruction",
            "audit_feedback",
            "previous_step_output",
        ):
            context.pop(key, None)
        context["step_id"] = "audit"
        context["audit_subject"] = "resource"
        dependencies = dict(context.get("dependency_outputs") or {})
        knowledge = dependencies["knowledge"]
        dependencies["knowledge"] = knowledge
        dependencies["expert"] = expert
        context["dependency_outputs"] = dependencies
        return context

    @staticmethod
    def _arm_state(expert: Any, audit: Any, pack: EvidencePack, pair: SemanticConflictBinding | None) -> dict[str, Any]:
        draft = expert.payload
        audit_result = audit.payload
        current_ids = {item.evidence_id for item in pack.evidence_items}
        referenced_ids = {
            evidence_id
            for claim in getattr(draft, "claims", [])
            for evidence_id in getattr(claim, "evidence_ids", [])
        }
        unsupported = bool(referenced_ids - current_ids)
        pair_ids = (
            {
                pair.evidence_pair.support_evidence_id,
                pair.evidence_pair.conflict_evidence_id,
            }
            if pair is not None
            else set()
        )
        content_text = _natural_text(getattr(draft, "content", {}))
        same_pair = bool(pair_ids and pair_ids.issubset(referenced_ids))
        user_output = "\n".join(
            part for part in [str(getattr(draft, "title", "")).strip(), content_text] if part
        )
        return {
            "user_output": user_output,
            "audit_decision": str(getattr(audit_result, "decision", "")),
            "same_conflict_pair_resolved": same_pair if pair is not None else True,
            "new_unsupported_claims": unsupported,
            "audit_conflicting_evidence": any(
                str(getattr(item, "issue_type", "")) == "conflicting_evidence"
                for item in getattr(audit_result, "structured_findings", [])
            ),
            "target_failure": bool(
                pair is not None
                and (
                    not same_pair
                    or str(getattr(audit_result, "decision", "")) != "pass"
                )
            ),
        }


def _natural_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return "\n".join(
            text for text in (_natural_text(item) for item in value.values()) if text
        )
    if isinstance(value, (list, tuple)):
        return "\n".join(
            text for text in (_natural_text(item) for item in value) if text
        )
    return str(value).strip() if value not in (None, "") else ""


def _digest(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

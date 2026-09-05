from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from competition_app.contracts.evolution import (
    EvolutionEvaluationDataset,
    EvolutionEvaluationPairReceipt,
    EvolutionEvaluationPolicy,
    EvolutionEvaluationSummary,
    EvolutionRule,
    EvolutionRuleContract,
    EvolutionRuleRun,
    RuleApprovalRequest,
)
from competition_app.repositories.evolution import EvolutionRepository
from competition_app.runtime.evolution_rules import (
    RULE_TEMPLATES,
    assess_rule_contract,
    validate_rule_contract,
)


_TRANSITIONS = {
    "draft": {"safety_replay_passed", "rejected"},
    # The guarded approve edge preserves a precise API error when an
    # administrator tries to approve before behavior/regression evidence exists.
    "safety_replay_passed": {"behavior_eval_passed", "approved", "rejected"},
    "behavior_eval_passed": {"regression_gate_passed", "rejected"},
    "regression_gate_passed": {"approved", "rejected"},
    "approved": {"active", "rejected"},
    "active": {"paused", "retired"},
    "paused": {"active", "retired"},
    "retired": set(),
    "rejected": set(),
}


class EvolutionRuleService:
    def __init__(self, repository: EvolutionRepository, *, enabled: bool = False) -> None:
        self.repository = repository
        self.enabled = enabled

    def create_from_signature(
        self,
        signature_id: str,
        *,
        analysis: str,
        template_id: str,
        intervention_type: str = "prevention",
    ) -> EvolutionRule:
        if not self.enabled:
            raise RuntimeError("evolution governance is disabled")
        signature = self.repository.get_signature(signature_id)
        if signature is None:
            raise KeyError(signature_id)
        if not signature.candidate_ready:
            raise ValueError("signature has not reached the candidate threshold")
        if template_id not in RULE_TEMPLATES:
            raise ValueError("unregistered rule template")
        contract = EvolutionRuleContract(
            signature_id=signature.signature_id,
            target_agent=signature.target_agent,
            target_step_id=signature.owner_step_id,
            task_type=signature.task_type,
            intervention_type=intervention_type,
            template_id=template_id,
            issue_type=signature.issue_type,
            field_path=signature.field_path,
            source_case_ids=signature.source_case_ids[:20],
        )
        return self.create_from_compiled(contract, analysis=analysis)

    def create_from_compiled(
        self,
        contract: EvolutionRuleContract,
        *,
        analysis: str,
    ) -> EvolutionRule:
        if not self.enabled:
            raise RuntimeError("evolution governance is disabled")
        signature = self.repository.get_signature(contract.signature_id)
        if signature is None or not signature.candidate_ready:
            raise ValueError("source signature is missing or not candidate-ready")
        if (
            contract.target_agent != signature.target_agent
            or contract.target_step_id != signature.owner_step_id
            or contract.task_type != signature.task_type
            or contract.issue_type != signature.issue_type
            or contract.field_path != signature.field_path
            or not set(contract.source_case_ids).issubset(signature.source_case_ids)
        ):
            raise ValueError("compiled contract exceeds source signature")
        if contract.template_id not in RULE_TEMPLATES:
            raise ValueError("unregistered rule template")
        assessment = self._assert_candidate(contract)
        rule = EvolutionRule(
            rule_id=f"ERULE_{uuid4().hex}",
            signature_id=signature.signature_id,
            natural_language_analysis=str(analysis).strip()[:12000] or "重复失败已达到候选阈值。",
            contract=contract,
            strategy_text=RULE_TEMPLATES[contract.template_id],
            status="draft",
        )
        validate_rule_contract(rule)
        return self.repository.save_rule(
            rule.model_copy(
                update={
                    "replay_metrics": {
                        "candidate_gate": assessment.as_metrics(),
                    }
                }
            )
        )

    def run_contract_replay(self, rule_id: str, *, reviewer_id: str) -> EvolutionRule:
        """Run a deterministic safety/coverage replay, not a claimed model-effect eval."""

        rule = self._required(rule_id)
        if rule.status != "draft":
            raise ValueError("only draft rules can be replayed")
        validate_rule_contract(rule)
        assessment = self._assert_candidate(rule.contract, exclude_rule_id=rule.rule_id)
        signature = self.repository.get_signature(rule.signature_id)
        if signature is None:
            raise ValueError("source signature no longer exists")
        source_set = set(signature.source_case_ids)
        anchored = set(rule.contract.source_case_ids)
        coverage = len(anchored & source_set) / max(1, len(source_set))
        safe = bool(anchored) and anchored.issubset(source_set)
        metrics = {
            "replay_kind": "deterministic_contract_safety",
            "source_case_count": len(source_set),
            "anchored_case_count": len(anchored),
            "source_coverage_rate": round(coverage, 4),
            "target_exact_match": (
                rule.contract.target_agent == signature.target_agent
                and rule.contract.target_step_id == signature.owner_step_id
            ),
            "template_whitelisted": rule.contract.template_id in RULE_TEMPLATES,
            "prompt_injection_surface": "closed_template",
            "reviewed_by": reviewer_id,
            "candidate_gate": assessment.as_metrics(),
        }
        if not safe or not metrics["target_exact_match"] or not metrics["template_whitelisted"]:
            raise ValueError("contract replay did not pass safety checks")
        updated = rule.model_copy(
            update={"status": "safety_replay_passed", "replay_metrics": metrics}
        )
        self.repository.save_run(EvolutionRuleRun(
            run_id=f"ERUN_{uuid4().hex}",
            rule_id=rule.rule_id,
            rule_version=rule.version,
            run_type="safety_replay",
            target_agent=rule.contract.target_agent,
            matched=True,
            metrics=metrics,
        ))
        return self.repository.save_rule(updated)

    def record_behavior_evaluation(
        self,
        rule_id: str,
        *,
        dataset: EvolutionEvaluationDataset,
        receipts: list[EvolutionEvaluationPairReceipt],
        policy: EvolutionEvaluationPolicy,
        evaluator_id: str,
    ) -> EvolutionEvaluationSummary:
        """Aggregate an already isolated A/B evaluation without model claims.

        The evaluator owns execution and freezing of context.  This service
        only accepts typed receipts, verifies their scope, computes deterministic
        counts, and persists redacted metrics.  No prompt, completion, or
        reasoning text is accepted here.
        """

        rule = self._required(rule_id)
        if any(
            item.rule_version == rule.version
            and item.dataset_id == dataset.dataset_id
            and item.dataset_version == dataset.dataset_version
            and item.dataset_digest == dataset.dataset_digest
            for item in self.repository.list_evaluations(rule_id=rule.rule_id, limit=500)
        ):
            raise ValueError("evaluation already exists for this rule version and dataset")
        if rule.status != "safety_replay_passed":
            raise ValueError("behavior evaluation requires a passed safety replay")
        if not receipts:
            raise ValueError("behavior evaluation requires at least one pair receipt")
        case_map = {item.case_id: item for item in dataset.cases}
        canonical_cases = [
            item.model_dump(mode="json", exclude={"input_digest"})
            for item in dataset.cases
        ]
        computed_dataset_digest = hashlib.sha256(
            json.dumps(canonical_cases, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if computed_dataset_digest != dataset.dataset_digest:
            raise ValueError("evaluation dataset digest does not match its manifest")
        receipt_ids = [item.case_id for item in receipts]
        duplicate_ids = sorted({
            case_id for case_id in receipt_ids if receipt_ids.count(case_id) > 1
        })
        missing_ids = sorted(set(case_map) - set(receipt_ids))
        unexpected_ids = sorted(set(receipt_ids) - set(case_map))
        if duplicate_ids or missing_ids or unexpected_ids:
            raise ValueError("behavior evaluation does not exactly cover the dataset")
        for receipt in receipts:
            if (
                receipt.rule_id != rule.rule_id
                or receipt.rule_version != rule.version
                or receipt.formal_environment_write_allowed
            ):
                raise ValueError("behavior evaluation receipt violates isolation contract")
            case = case_map.get(receipt.case_id)
            if case is None:
                raise ValueError("behavior evaluation receipt is outside the dataset scope")
            if case.expected_rule_exposure != receipt.candidate.rule_exposed:
                raise ValueError("candidate rule exposure does not match dataset expectation")
            if receipt.baseline.rule_exposed:
                raise ValueError("baseline arm cannot receive candidate rule exposure")
            if case.expected_rule_exposure and (
                case.task_type != rule.contract.task_type
                or case.target_agent != rule.contract.target_agent
            ):
                raise ValueError("exposed evaluation case exceeds the rule contract")
            if (
                receipt.execution_status == "completed"
                and (not receipt.context_equal or receipt.evidence_pack_equal is not True)
            ):
                raise ValueError("completed behavior receipt violates frozen equality")
            if receipt.input_digest and case.input_digest:
                if receipt.input_digest != case.input_digest:
                    raise ValueError("evaluation receipt input digest does not match dataset")

        completed = [
            item for item in receipts if item.execution_status == "completed"
        ]
        ineligible_count = sum(
            item.execution_status == "ineligible" for item in receipts
        )
        technical_failure_count = sum(
            item.execution_status == "technical_failure" for item in receipts
        )
        target = [item for item in completed if item.case_group == "target_fault"]
        controls = [
            item for item in completed if item.case_group == "non_regression_control"
        ]
        negative_controls = [
            item for item in completed
            if item.case_group == "targeting_negative_control"
        ]
        improvements = sum(
            item.baseline.has_target_failure and not item.candidate.has_target_failure
            for item in target
        )
        regressions = sum(
            not item.baseline.has_target_failure and item.candidate.has_target_failure
            for item in target
        )
        control_regressions = sum(
            not item.baseline.has_target_failure and item.candidate.has_target_failure
            for item in controls + negative_controls
        )
        unsafe_releases = sum(item.candidate.unsafe_release for item in completed)
        internal_leaks = sum(item.candidate.internal_leak_detected for item in completed)
        invalid_references = sum(
            item.candidate.invalid_reference_count for item in completed
        )
        latencies = [
            (item.baseline.duration_ms, item.candidate.duration_ms)
            for item in completed
            if item.baseline.duration_ms is not None and item.candidate.duration_ms is not None
        ]
        tokens = [
            (item.baseline.token_count, item.candidate.token_count)
            for item in completed
            if item.baseline.token_count is not None and item.candidate.token_count is not None
        ]
        latency_ratio = self._relative_increase(latencies)
        token_ratio = self._relative_increase(tokens)
        reasons: list[str] = []
        if len(target) < policy.min_target_cases:
            reasons.append("insufficient_target_cases")
        if len(controls) + len(negative_controls) < policy.min_control_cases:
            reasons.append("insufficient_control_cases")
        if improvements < policy.min_paired_improvements:
            reasons.append("insufficient_paired_improvements")
        if regressions > policy.max_paired_regressions:
            reasons.append("paired_regression_limit_exceeded")
        if control_regressions > policy.max_control_regressions:
            reasons.append("control_regression_limit_exceeded")
        if unsafe_releases > policy.max_unsafe_releases:
            reasons.append("unsafe_release_detected")
        if internal_leaks > policy.max_internal_leaks:
            reasons.append("internal_leak_detected")
        if invalid_references > policy.max_invalid_references:
            reasons.append("invalid_reference_detected")
        if ineligible_count:
            reasons.append("ineligible_case_present")
        if technical_failure_count:
            reasons.append("technical_failure_present")
        if (
            policy.max_latency_increase_ratio is not None
            and latency_ratio is not None
            and latency_ratio > policy.max_latency_increase_ratio
        ):
            reasons.append("latency_regression_limit_exceeded")
        if (
            policy.max_token_increase_ratio is not None
            and token_ratio is not None
            and token_ratio > policy.max_token_increase_ratio
        ):
            reasons.append("token_regression_limit_exceeded")
        behavior_passed = not any(
            reason in reasons
            for reason in {
                "insufficient_target_cases",
                "insufficient_control_cases",
                "insufficient_paired_improvements",
                "unsafe_release_detected",
                "internal_leak_detected",
                "invalid_reference_detected",
                "ineligible_case_present",
                "technical_failure_present",
            }
        )
        regression_passed = not any(
            reason.endswith("limit_exceeded") for reason in reasons
        ) and control_regressions <= policy.max_control_regressions
        evaluation_id = f"EEVAL_{uuid4().hex}"
        summary = EvolutionEvaluationSummary(
            evaluation_id=evaluation_id,
            rule_id=rule.rule_id,
            rule_version=rule.version,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.dataset_version,
            dataset_digest=dataset.dataset_digest,
            policy_version=policy.policy_version,
            case_count=len(receipts),
            completed_case_count=len(completed),
            ineligible_case_count=ineligible_count,
            technical_failure_count=technical_failure_count,
            target_case_count=len(target),
            control_case_count=len(controls) + len(negative_controls),
            paired_improvements=improvements,
            paired_regressions=regressions,
            control_regressions=control_regressions,
            unsafe_releases=unsafe_releases,
            internal_leaks=internal_leaks,
            invalid_references=invalid_references,
            latency_increase_ratio=latency_ratio,
            token_increase_ratio=token_ratio,
            behavior_eval_passed=behavior_passed,
            regression_gate_passed=regression_passed,
            reason_codes=reasons,
            created_by=evaluator_id,
            created_at=datetime.now(timezone.utc),
        )
        metrics = {
            "schema_version": "evolution-evaluation-summary-1.1",
            **summary.model_dump(mode="json"),
        }
        for receipt in receipts:
            if receipt.execution_status != "completed":
                continue
            for run_type, arm in (
                ("behavior_eval_baseline", receipt.baseline),
                ("behavior_eval_candidate", receipt.candidate),
            ):
                self.repository.save_run(EvolutionRuleRun(
                    run_id=f"ERUN_{uuid4().hex}",
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    run_type=run_type,
                    target_agent=rule.contract.target_agent,
                    matched=arm.rule_exposed,
                    first_audit_decision=(
                        arm.first_audit_decision or arm.audit_decision
                    ),
                    final_audit_decision=(
                        arm.final_audit_decision or arm.audit_decision
                    ),
                    repair_count=arm.repair_count,
                    released=arm.release_allowed,
                    metrics={
                        "schema_version": "evolution-eval-arm-1.1",
                        "evaluation_id": evaluation_id,
                        "case_id": receipt.case_id,
                        "case_group": receipt.case_group,
                        "context_equal": receipt.context_equal,
                        "evidence_pack_equal": receipt.evidence_pack_equal,
                        "has_target_failure": arm.has_target_failure,
                        "initial_target_failure": arm.initial_target_failure,
                        "final_target_failure": arm.final_target_failure,
                        "repair_attempt_count": arm.repair_attempt_count,
                        "repair_exhausted": arm.repair_exhausted,
                        "max_repair_attempts": arm.max_repair_attempts,
                        "release_allowed": arm.release_allowed,
                        "actual_rerun_step_ids": arm.actual_rerun_step_ids,
                        "internal_leak_detected": arm.internal_leak_detected,
                        "invalid_reference_count": arm.invalid_reference_count,
                        "unsafe_release": arm.unsafe_release,
                        "duration_ms": arm.duration_ms,
                        "token_count": arm.token_count,
                    },
                    input_digest=receipt.input_digest or dataset.dataset_digest,
                ))
        gate_run = EvolutionRuleRun(
            run_id=f"ERUN_{uuid4().hex}",
            rule_id=rule.rule_id,
            rule_version=rule.version,
            run_type="behavior_eval_gate",
            target_agent=rule.contract.target_agent,
            matched=True,
            metrics=metrics,
            input_digest=dataset.dataset_digest,
        )
        self.repository.save_run(gate_run)
        self.repository.save_evaluation(summary)
        if behavior_passed and rule.status == "safety_replay_passed":
            rule = self.repository.save_rule(rule.model_copy(update={
                "status": "behavior_eval_passed",
                "replay_metrics": {**rule.replay_metrics, "behavior_evaluation": metrics},
                "updated_at": datetime.now(timezone.utc),
            }))
        if regression_passed and behavior_passed and rule.status == "behavior_eval_passed":
            rule = self.repository.save_rule(rule.model_copy(update={
                "status": "regression_gate_passed",
                "replay_metrics": {**rule.replay_metrics, "regression_gate": metrics},
                "updated_at": datetime.now(timezone.utc),
            }))
        return summary

    @staticmethod
    def _relative_increase(pairs: list[tuple[int, int]]) -> float | None:
        if not pairs:
            return None
        baseline = sum(left for left, _ in pairs)
        candidate = sum(right for _, right in pairs)
        if baseline <= 0:
            return 0.0 if candidate <= 0 else None
        return round((candidate - baseline) / baseline, 4)

    def transition(
        self,
        rule_id: str,
        request: RuleApprovalRequest,
        *,
        reviewer_id: str,
    ) -> EvolutionRule:
        rule = self._required(rule_id)
        target = {
            "approve": "approved",
            "reject": "rejected",
            "activate": "active",
            "pause": "paused",
            "resume": "active",
            "retire": "retired",
        }[request.action]
        if target not in _TRANSITIONS.get(rule.status, set()):
            raise ValueError(f"invalid rule transition: {rule.status} -> {target}")
        validate_rule_contract(rule)
        if request.action in {"approve", "activate", "resume"}:
            gates_present = (
                "behavior_evaluation" in rule.replay_metrics
                and "regression_gate" in rule.replay_metrics
            )
            if not gates_present:
                raise ValueError(
                    "rule requires behavior evaluation and regression gate before approval"
                )
            self._assert_candidate(rule.contract, exclude_rule_id=rule.rule_id)
        updated = self.repository.save_rule(rule.model_copy(update={
            "status": target,
            "approved_by": reviewer_id if target in {"approved", "active"} else rule.approved_by,
            "reviewer_domain": request.reviewer_domain,
            "approval_note": str(request.note).strip()[:2000],
            "updated_at": datetime.now(timezone.utc),
        }))
        if request.action in {"pause", "retire"}:
            self.repository.save_run(EvolutionRuleRun(
                run_id=f"ERUN_{uuid4().hex}",
                rule_id=rule.rule_id,
                rule_version=rule.version,
                run_type="rollback_check",
                target_agent=rule.contract.target_agent,
                matched=True,
                metrics={"action": request.action, "reviewer_id": reviewer_id},
            ))
        return updated

    def _required(self, rule_id: str) -> EvolutionRule:
        rule = self.repository.get_rule(rule_id)
        if rule is None:
            raise KeyError(rule_id)
        return rule

    def _assert_candidate(
        self,
        contract: EvolutionRuleContract,
        *,
        exclude_rule_id: str | None = None,
    ):
        assessment = assess_rule_contract(contract)
        if not assessment.applicable:
            reasons = ",".join(assessment.reason_codes)
            raise ValueError(f"evolution rule candidate gate rejected: {reasons}")
        duplicate_ids = sorted({
            item.rule_id
            for item in self.repository.list_rules(limit=500)
            if item.rule_id != exclude_rule_id
            and item.status not in {"rejected", "retired"}
            and item.contract.target_agent == contract.target_agent
            and item.contract.target_step_id == contract.target_step_id
            and item.contract.task_type == contract.task_type
            and item.contract.template_id == contract.template_id
        })
        if duplicate_ids:
            raise ValueError(
                "evolution rule candidate gate rejected: duplicate_existing_rule:"
                + ",".join(duplicate_ids[:5])
            )
        return assessment

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

from competition_app.evaluation.d1_precheck_dataset import (
    D1_RULE_ID,
    D1PilotCase,
    load_d1_pilot_cases,
    validate_d1_pilot_cases,
)


_INTERNAL_LEAK_TOKENS = (
    D1_RULE_ID,
    "EvidencePack",
    "evidence_id",
    "提示词",
    "Schema",
    "Trace",
    "规则曝光",
)


@dataclass(frozen=True)
class D1ArmInput:
    arm: str
    user_request: str
    user_context: dict[str, Any]
    input_digest: str
    rule_enabled: bool


@dataclass(frozen=True)
class D1PreparedPair:
    case_id: str
    pair_order: str
    arms: tuple[D1ArmInput, D1ArmInput]
    formal_environment_write_allowed: bool


@dataclass(frozen=True)
class D1ArmReceipt:
    arm: str
    input_digest: str
    rule_exposed: bool
    exposed_target_agent: str | None
    user_output: str
    target_failure: bool | None
    closure_allowed: bool | None
    initial_target_failure: bool | None = None
    final_target_failure: bool | None = None
    first_audit_decision: str | None = None
    final_audit_decision: str | None = None
    repair_count: int | None = None
    repair_attempt_count: int | None = None
    repair_exhausted: bool | None = None
    release_allowed: bool | None = None
    max_repair_attempts: int | None = None
    actual_rerun_step_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class D1PairReceipt:
    case_id: str
    arms: tuple[D1ArmReceipt, D1ArmReceipt]


@dataclass(frozen=True)
class D1PairValidationResult:
    valid: bool
    reason_codes: tuple[str, ...]
    context_equal: bool
    a_rule_exposure: int
    b_target_rule_exposure: bool
    repair_contract_valid: bool = True


class D1PrecheckRunner:
    """Prepare and validate isolated D1 A/B pairs; never runs production Agents."""

    def __init__(self, cases: list[D1PilotCase] | None = None) -> None:
        self.cases = cases or load_d1_pilot_cases()
        validate_d1_pilot_cases(self.cases)
        self._by_id = {case.case_id: case for case in self.cases}

    def prepare_pair(self, case_id: str) -> D1PreparedPair:
        try:
            case = self._by_id[case_id]
        except KeyError as exc:
            raise KeyError(f"unknown D1 precheck case: {case_id}") from exc
        business_context = {
            "task_type": case.expected_task_type,
            "owner_step_id": case.owner_step_id,
            "expected_rerun_step_ids": list(case.expected_rerun_step_ids),
        }
        if case.frozen_evidence is not None:
            # The digest binds the paired arms to the frozen evidence without
            # exposing gold labels or evidence text to the learner request.
            business_context["frozen_fixture_digest"] = case.frozen_evidence.fixture_digest
        expected_rule_exposure = (
            case.frozen_evidence.expected_rule_exposure
            if case.frozen_evidence is not None
            else case.case_group == "target_fault"
        )
        digest = _digest({
            "user_request": case.prompt,
            "user_context": business_context,
        })
        arms = tuple(
            D1ArmInput(
                arm=arm,
                user_request=case.prompt,
                user_context=dict(business_context),
                input_digest=digest,
                    rule_enabled=arm == "B" and expected_rule_exposure,
            )
            for arm in case.pair_order
        )
        return D1PreparedPair(
            case_id=case.case_id,
            pair_order=case.pair_order,
            arms=arms,
            formal_environment_write_allowed=case.formal_environment_write_allowed,
        )

    def validate_pair(self, receipt: D1PairReceipt) -> D1PairValidationResult:
        case = self._by_id.get(receipt.case_id)
        if case is None:
            raise KeyError(f"unknown D1 precheck case: {receipt.case_id}")
        reasons: list[str] = []
        arms = {item.arm: item for item in receipt.arms}
        if set(arms) != {"A", "B"} or len(receipt.arms) != 2:
            reasons.append("pair_must_contain_one_A_and_one_B")
            return D1PairValidationResult(False, tuple(reasons), False, 0, False, False)
        a = arms["A"]
        b = arms["B"]
        context_equal = a.input_digest == b.input_digest
        if not context_equal:
            reasons.append("frozen_context_mismatch")
        if a.rule_exposed:
            reasons.append("a_rule_exposure_nonzero")
        expected_rule_exposure = (
            case.frozen_evidence.expected_rule_exposure
            if case.frozen_evidence is not None
            else case.case_group == "target_fault"
        )
        b_target_exposure = b.rule_exposed and b.exposed_target_agent == "expert_agent"
        if expected_rule_exposure:
            if not b.rule_exposed:
                reasons.append("b_target_rule_exposure_missing")
            elif b.exposed_target_agent != "expert_agent":
                reasons.append("b_rule_target_agent_mismatch")
        elif b.rule_exposed:
            reasons.append("negative_control_rule_exposure")
        if b.rule_exposed and b.exposed_target_agent != "expert_agent":
            reasons.append("b_rule_target_agent_mismatch")
        if any(
            any(token in _learner_visible_output(item.user_output) for token in _INTERNAL_LEAK_TOKENS)
            for item in (a, b)
        ):
            reasons.append("internal_output_leakage")
        if any(item.target_failure is None or item.closure_allowed is None for item in (a, b)):
            reasons.append("business_outcome_missing")
        repair_reasons = _validate_repair_contract(a, b)
        reasons.extend(repair_reasons)
        return D1PairValidationResult(
            valid=not reasons,
            reason_codes=tuple(dict.fromkeys(reasons)),
            context_equal=context_equal,
            a_rule_exposure=int(a.rule_exposed),
            b_target_rule_exposure=b_target_exposure,
            repair_contract_valid=not repair_reasons,
        )


    def prepare_payload(self, case_id: str) -> dict[str, Any]:
        prepared = self.prepare_pair(case_id)
        return {
            "schema_version": "d1-precheck-pair-1.1",
            "case_id": prepared.case_id,
            "pair_order": prepared.pair_order,
            "formal_environment_write_allowed": prepared.formal_environment_write_allowed,
            "arms": [
                {
                    "arm": arm.arm,
                    "user_request": arm.user_request,
                    "user_context": arm.user_context,
                    "input_digest": arm.input_digest,
                    "rule_enabled": arm.rule_enabled,
                }
                for arm in prepared.arms
            ],
        }

    def cases_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "case_id": case.case_id,
                "case_group": case.case_group,
                "expected_task_type": case.expected_task_type,
                "pair_order": case.pair_order,
                "target_rule": case.target_rule,
                "target_agent": case.target_agent,
                "owner_step_id": case.owner_step_id,
                "expected_rerun_step_ids": list(case.expected_rerun_step_ids),
                "scenario": case.scenario,
                "formal_environment_write_allowed": case.formal_environment_write_allowed,
            }
            for case in self.cases
        ]

    def receipt_from_payload(self, payload: Mapping[str, Any]) -> D1PairReceipt:
        if not isinstance(payload, Mapping):
            raise ValueError("D1 pair receipt must be an object")
        raw_arms = payload.get("arms")
        if not isinstance(raw_arms, list) or len(raw_arms) != 2:
            raise ValueError("D1 pair receipt requires exactly two arms")
        arms: list[D1ArmReceipt] = []
        for raw in raw_arms:
            if not isinstance(raw, Mapping):
                raise ValueError("D1 arm receipt must be an object")
            try:
                arms.append(D1ArmReceipt(
                    arm=str(raw["arm"]),
                    input_digest=str(raw["input_digest"]),
                    rule_exposed=bool(raw["rule_exposed"]),
                    exposed_target_agent=(
                        str(raw["exposed_target_agent"])
                        if raw.get("exposed_target_agent") is not None else None
                    ),
                    user_output=str(raw.get("user_output") or ""),
                    target_failure=(
                        bool(raw["target_failure"])
                        if raw.get("target_failure") is not None else None
                    ),
                    closure_allowed=(
                        bool(raw["closure_allowed"])
                        if raw.get("closure_allowed") is not None else None
                    ),
                    initial_target_failure=(
                        bool(raw["initial_target_failure"])
                        if raw.get("initial_target_failure") is not None else None
                    ),
                    final_target_failure=(
                        bool(raw["final_target_failure"])
                        if raw.get("final_target_failure") is not None else None
                    ),
                    first_audit_decision=(
                        str(raw["first_audit_decision"])
                        if raw.get("first_audit_decision") is not None else None
                    ),
                    final_audit_decision=(
                        str(raw["final_audit_decision"])
                        if raw.get("final_audit_decision") is not None else None
                    ),
                    repair_count=(
                        int(raw["repair_count"])
                        if raw.get("repair_count") is not None else None
                    ),
                    repair_attempt_count=(
                        int(raw["repair_attempt_count"])
                        if raw.get("repair_attempt_count") is not None else None
                    ),
                    repair_exhausted=(
                        bool(raw["repair_exhausted"])
                        if raw.get("repair_exhausted") is not None else None
                    ),
                    release_allowed=(
                        bool(raw["release_allowed"])
                        if raw.get("release_allowed") is not None else None
                    ),
                    max_repair_attempts=(
                        int(raw["max_repair_attempts"])
                        if raw.get("max_repair_attempts") is not None else None
                    ),
                    actual_rerun_step_ids=tuple(
                        str(item) for item in (raw.get("actual_rerun_step_ids") or [])
                    ),
                ))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid D1 arm receipt") from exc
        return D1PairReceipt(
            case_id=str(payload.get("case_id") or ""),
            arms=(arms[0], arms[1]),
        )

    def validate_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        result = self.validate_pair(self.receipt_from_payload(payload))
        return {
            "schema_version": "d1-precheck-validation-1.1",
            "valid": result.valid,
            "reason_codes": list(result.reason_codes),
            "context_equal": result.context_equal,
            "a_rule_exposure": result.a_rule_exposure,
            "b_target_rule_exposure": result.b_target_rule_exposure,
            "repair_contract_valid": result.repair_contract_valid,
        }


def _learner_visible_output(value: str) -> str:
    """Remove the system-owned reference transport before leakage checks.

    ``<<REFS:[...]>>`` is parsed by the frontend into source cards and is not
    learner-visible prose.  Its JSON necessarily contains ``evidence_id``;
    treating that contract key as a prompt leak makes every cited answer a
    false positive.  Only the hidden transport is removed—an internal token
    written in ordinary prose remains detectable.
    """

    return re.sub(r"\s*<<REFS:.*?>>\s*", "\n", str(value or ""), flags=re.DOTALL).strip()


def _validate_repair_contract(
    a: D1ArmReceipt,
    b: D1ArmReceipt,
) -> list[str]:
    """Validate the executor-owned bounded repair fields when present.

    Older pilot receipts predate these fields, so the validator remains
    backward-compatible for callers that construct the old typed contract.
    New execution receipts contain the complete set and are checked strictly.
    """

    reasons: list[str] = []
    arms = (a, b)
    if not any(
        value is not None
        for arm in arms
        for value in (
            arm.initial_target_failure,
            arm.final_target_failure,
            arm.first_audit_decision,
            arm.final_audit_decision,
            arm.repair_count,
            arm.repair_attempt_count,
            arm.repair_exhausted,
            arm.release_allowed,
            arm.max_repair_attempts,
        )
    ):
        return reasons
    if any(
        arm.initial_target_failure is None
        or arm.final_target_failure is None
        or not arm.first_audit_decision
        or not arm.final_audit_decision
        or arm.repair_count is None
        or arm.repair_attempt_count is None
        or arm.repair_exhausted is None
        or arm.release_allowed is None
        or arm.max_repair_attempts is None
        for arm in arms
    ):
        reasons.append("repair_contract_fields_missing")
        return reasons
    if any(
        arm.repair_attempt_count < 0
        or arm.repair_attempt_count > arm.max_repair_attempts
        or arm.repair_count < 0
        or arm.repair_count > arm.max_repair_attempts + 1
        for arm in arms
    ):
        reasons.append("repair_count_out_of_range")
    for arm in arms:
        if arm.repair_exhausted != (arm.repair_count == arm.max_repair_attempts + 1):
            reasons.append("repair_exhaustion_sentinel_mismatch")
        if (
            arm.repair_count != arm.max_repair_attempts + 1
            and arm.repair_count != arm.repair_attempt_count
        ):
            reasons.append("repair_count_attempt_count_mismatch")
        if (
            arm.repair_count == arm.max_repair_attempts + 1
            and arm.repair_attempt_count != arm.max_repair_attempts
        ):
            reasons.append("exhausted_repair_attempt_count_mismatch")
        if arm.repair_count == 0 and arm.actual_rerun_step_ids:
            reasons.append("zero_repair_has_rerun_chain")
        if arm.repair_count > 0 and arm.actual_rerun_step_ids != ("expert", "audit"):
            reasons.append("repair_rerun_chain_mismatch")
        if arm.first_audit_decision == "pass" and arm.repair_count != 0:
            reasons.append("first_pass_must_not_repair")
        if arm.final_audit_decision == "pass" and arm.repair_exhausted:
            reasons.append("exhausted_arm_cannot_final_pass")
    if a.max_repair_attempts != b.max_repair_attempts:
        reasons.append("ab_repair_budget_mismatch")
    return list(dict.fromkeys(reasons))


def _digest(value: dict[str, Any]) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Literal

from pydantic import Field, field_validator, model_validator

from competition_app.contracts.base import ContractModel


FeedbackSource = Literal["user", "audit", "repair", "runtime", "human_review"]
FeedbackTrust = Literal["low", "medium", "high"]
FeedbackStatus = Literal["pending", "validated", "rejected"]
FeedbackRuleClassificationId = Literal[
    "expert_evidence_reference",
    "knowledge_summary_reference",
    "audit_owner_assignment",
]
RuleStatus = Literal[
    "draft",
    "safety_replay_passed",
    "behavior_eval_passed",
    "regression_gate_passed",
    "approved",
    "active",
    "paused",
    "retired",
    "rejected",
]
InterventionType = Literal["prevention", "retrieval", "repair", "detection"]
EvaluationCaseGroup = Literal[
    "target_fault", "non_regression_control", "targeting_negative_control"
]


class EvolutionFeedback(ContractModel):
    feedback_id: str
    source_type: FeedbackSource
    trust_level: FeedbackTrust
    status: FeedbackStatus = "pending"
    execution_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    learner_id: str | None = None
    task_type: str
    target_agent: str | None = None
    owner_step_id: str | None = None
    issue_type: str
    severity: Literal["info", "warning", "blocking"] = "warning"
    field_path: str = ""
    constraint_category: str = "general"
    summary: str = Field(default="", max_length=2000)
    source_case_id: str | None = None
    dedup_key: str
    created_at: datetime | None = None
    reviewed_at: datetime | None = None
    reviewer_id: str | None = None

    @field_validator(
        "task_type", "issue_type", "field_path", "constraint_category", "summary",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value: object) -> str:
        return str(value or "").strip()


class FailureSignature(ContractModel):
    signature_id: str
    signature_key: str
    task_type: str
    owner_step_id: str
    target_agent: str
    issue_type: str
    field_path: str
    constraint_category: str
    case_count: int = Field(default=0, ge=0)
    execution_count: int = Field(default=0, ge=0)
    high_trust_count: int = Field(default=0, ge=0)
    source_case_ids: list[str] = Field(default_factory=list)
    source_execution_ids: list[str] = Field(default_factory=list)
    candidate_ready: bool = False
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


class EvolutionRuleContract(ContractModel):
    signature_id: str
    target_agent: str
    target_step_id: str
    task_type: str
    intervention_type: InterventionType
    template_id: str
    issue_type: str
    field_path: str = ""
    source_case_ids: list[str] = Field(min_length=1, max_length=20)

    model_config = {"extra": "forbid"}


class EvolutionRule(ContractModel):
    rule_id: str
    version: int = Field(default=1, ge=1)
    signature_id: str
    natural_language_analysis: str = Field(min_length=1, max_length=12000)
    contract: EvolutionRuleContract
    strategy_text: str = Field(min_length=1, max_length=1600)
    status: RuleStatus = "draft"
    replay_metrics: dict[str, object] = Field(default_factory=dict)
    approved_by: str | None = None
    reviewer_domain: Literal["technical", "teaching", "mixed"] | None = None
    approval_note: str = Field(default="", max_length=2000)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EvolutionRuleRun(ContractModel):
    run_id: str
    rule_id: str
    rule_version: int = Field(ge=1)
    run_type: Literal[
        "safety_replay",
        "behavior_eval_baseline",
        "behavior_eval_candidate",
        "behavior_eval_gate",
        "exposure",
        "rollback_check",
    ]
    execution_id: str | None = None
    target_agent: str | None = None
    matched: bool = False
    first_audit_decision: str | None = None
    final_audit_decision: str | None = None
    repair_count: int = Field(default=0, ge=0)
    released: bool = False
    metrics: dict[str, object] = Field(default_factory=dict)
    input_digest: str = ""
    created_at: datetime | None = None


class EvolutionEvaluationCase(ContractModel):
    case_id: str = Field(min_length=1, max_length=128)
    case_group: EvaluationCaseGroup
    task_type: str = Field(min_length=1, max_length=128)
    target_agent: str = Field(default="expert_agent", min_length=1, max_length=128)
    expected_rule_exposure: bool = False
    input_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )

    model_config = {"extra": "forbid"}


class EvolutionEvaluationDataset(ContractModel):
    dataset_id: str = Field(min_length=1, max_length=128)
    dataset_version: int = Field(ge=1)
    dataset_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    cases: list[EvolutionEvaluationCase] = Field(min_length=1, max_length=5000)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def case_ids_must_be_unique(self):
        case_ids = [item.case_id for item in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("evaluation case_ids must be unique")
        return self

    @staticmethod
    def compute_digest(cases: list["EvolutionEvaluationCase"]) -> str:
        canonical = [
            item.model_dump(mode="json", exclude={"input_digest"})
            for item in cases
        ]
        return hashlib.sha256(
            json.dumps(
                canonical,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


class EvolutionEvaluationArm(ContractModel):
    has_target_failure: bool
    initial_target_failure: bool | None = None
    final_target_failure: bool | None = None
    audit_decision: str | None = None
    first_audit_decision: str | None = None
    final_audit_decision: str | None = None
    repair_count: int = Field(default=0, ge=0)
    repair_attempt_count: int = Field(default=0, ge=0)
    repair_exhausted: bool = False
    max_repair_attempts: int = Field(default=2, ge=0)
    release_allowed: bool = False
    actual_rerun_step_ids: list[str] = Field(default_factory=list)
    internal_leak_detected: bool = False
    invalid_reference_count: int = Field(default=0, ge=0)
    unsafe_release: bool = False
    duration_ms: int | None = Field(default=None, ge=0)
    token_count: int | None = Field(default=None, ge=0)
    rule_exposed: bool = False

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_repair_fields(self):
        if self.repair_attempt_count > self.max_repair_attempts:
            raise ValueError("repair_attempt_count exceeds max_repair_attempts")
        if self.repair_count > self.max_repair_attempts + 1:
            raise ValueError("repair_count exceeds the exhaustion sentinel")
        if self.repair_exhausted != (
            self.repair_count == self.max_repair_attempts + 1
        ):
            raise ValueError("repair_exhausted does not match repair_count")
        if self.repair_count != self.max_repair_attempts + 1 and (
            self.repair_count != self.repair_attempt_count
        ):
            raise ValueError("repair_count must match repair_attempt_count")
        if self.first_audit_decision == "pass" and self.repair_count != 0:
            raise ValueError("a first-pass arm cannot have repairs")
        if self.final_audit_decision == "pass" and self.repair_exhausted:
            raise ValueError("an exhausted arm cannot have a final pass")
        if self.repair_count == 0 and self.actual_rerun_step_ids:
            raise ValueError("zero-repair arm cannot have a rerun chain")
        if self.repair_count > 0 and self.actual_rerun_step_ids != ["expert", "audit"]:
            raise ValueError("repair arm must use the expert/audit rerun chain")
        if self.initial_target_failure is not None and (
            self.has_target_failure != self.initial_target_failure
        ):
            raise ValueError("has_target_failure must retain the initial defect label")
        if self.final_target_failure is True and self.release_allowed:
            raise ValueError("release cannot retain the final target failure")
        if self.release_allowed and self.final_audit_decision not in {None, "pass"}:
            raise ValueError("release requires a final Audit pass")
        return self


class EvolutionEvaluationPairReceipt(ContractModel):
    case_id: str = Field(min_length=1, max_length=128)
    case_group: EvaluationCaseGroup
    rule_id: str = Field(min_length=1, max_length=128)
    rule_version: int = Field(ge=1)
    pair_order: Literal["AB", "BA"] | None = None
    formal_environment_write_allowed: bool = False
    context_equal: bool
    evidence_pack_equal: bool | None = None
    input_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    baseline: EvolutionEvaluationArm
    candidate: EvolutionEvaluationArm
    execution_status: Literal["completed", "ineligible", "technical_failure"] = "completed"
    failure_code: str | None = None

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_pair_contract(self):
        if self.formal_environment_write_allowed:
            raise ValueError("formal environment writeback is forbidden")
        if self.execution_status == "completed":
            if self.failure_code is not None:
                raise ValueError("completed receipt cannot contain failure_code")
            if not self.context_equal or self.evidence_pack_equal is not True:
                raise ValueError("completed receipt requires equal frozen context/evidence")
            if self.baseline.rule_exposed:
                raise ValueError("baseline arm must never receive the candidate rule")
            if self.baseline.max_repair_attempts != self.candidate.max_repair_attempts:
                raise ValueError("paired arms must use the same repair budget")
        else:
            if not self.failure_code:
                raise ValueError("failed receipt requires a sanitized failure_code")
            if self.context_equal or self.evidence_pack_equal is True:
                raise ValueError("failed receipt cannot claim complete paired equality")
        return self


class EvolutionEvaluationPolicy(ContractModel):
    policy_version: str = "1.0"
    min_target_cases: int = Field(default=3, ge=1)
    min_control_cases: int = Field(default=2, ge=1)
    min_paired_improvements: int = Field(default=1, ge=1)
    max_paired_regressions: int = Field(default=0, ge=0)
    max_control_regressions: int = Field(default=0, ge=0)
    max_unsafe_releases: int = Field(default=0, ge=0)
    max_internal_leaks: int = Field(default=0, ge=0)
    max_invalid_references: int = Field(default=0, ge=0)
    max_latency_increase_ratio: float | None = Field(default=0.5, ge=0)
    max_token_increase_ratio: float | None = Field(default=0.5, ge=0)

    model_config = {"extra": "forbid"}


class EvolutionEvaluationSummary(ContractModel):
    evaluation_id: str
    rule_id: str
    rule_version: int = Field(ge=1)
    dataset_id: str
    dataset_version: int = Field(ge=1)
    dataset_digest: str
    policy_version: str
    case_count: int = Field(ge=0)
    completed_case_count: int = Field(ge=0)
    ineligible_case_count: int = Field(ge=0)
    technical_failure_count: int = Field(ge=0)
    target_case_count: int = Field(ge=0)
    control_case_count: int = Field(ge=0)
    paired_improvements: int = Field(ge=0)
    paired_regressions: int = Field(ge=0)
    control_regressions: int = Field(ge=0)
    unsafe_releases: int = Field(ge=0)
    internal_leaks: int = Field(ge=0)
    invalid_references: int = Field(ge=0)
    latency_increase_ratio: float | None = None
    token_increase_ratio: float | None = None
    behavior_eval_passed: bool
    regression_gate_passed: bool
    reason_codes: list[str] = Field(default_factory=list)
    created_by: str
    created_at: datetime | None = None


class EvolutionEvaluationSubmitRequest(ContractModel):
    dataset: EvolutionEvaluationDataset
    policy: EvolutionEvaluationPolicy = Field(default_factory=EvolutionEvaluationPolicy)


class UserFeedbackRequest(ContractModel):
    feedback_type: Literal["like", "dislike"]
    issue_type: Literal[
        "content_error",
        "off_topic",
        "insufficient_evidence",
        "unreasonable_plan",
        "execution_failure",
        "other",
    ] | None = None
    comment: str = Field(default="", max_length=1200)
    conversation_id: str | None = None
    message_id: str | None = None
    execution_id: str | None = None
    task_type: str = "general_learning_support"


class FeedbackReviewRequest(ContractModel):
    status: Literal["validated", "rejected"]
    classification_id: FeedbackRuleClassificationId | None = None
    issue_type: str | None = None
    target_agent: str | None = None
    owner_step_id: str | None = None
    reviewer_note: str = Field(default="", max_length=2000)


class RuleApprovalRequest(ContractModel):
    action: Literal["approve", "reject", "activate", "pause", "resume", "retire"]
    reviewer_domain: Literal["technical", "teaching", "mixed"] = "technical"
    note: str = Field(default="", max_length=2000)

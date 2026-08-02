from typing import Literal

from pydantic import Field

from competition_app.contracts.base import ContractModel
from competition_app.contracts.audit_compilation import AuditLocation, AuditSourceAnchor


class RepairIssue(ContractModel):
    issue_id: str
    issue_type: Literal[
        "missing_evidence",
        "conflicting_evidence",
        "learner_mismatch",
        "route_or_prerequisite_error",
        "content_quality",
        "paper_blueprint_mismatch",
        "plan_quality",
        "plan_contract_invalid",
        "plan_parent_constraint",
        "question_pool_insufficient",
        "paper_item_invalid",
        "answer_or_explanation_invalid",
        "safety_violation",
        "unresolved",
    ]
    message: str
    claim_ref: str | None = None
    evidence_ref: str | None = None
    owner_step_id: str | None = None
    affected_step_ids: list[str] = Field(default_factory=list)
    severity: Literal["low", "medium", "high"] = "medium"
    origin: Literal["deterministic", "audit_model", "legacy"] = "legacy"
    blocking: bool = True
    locations: list[AuditLocation] = Field(default_factory=list, max_length=8)
    source_anchors: list[AuditSourceAnchor] = Field(default_factory=list, max_length=4)
    policy_id: str | None = None


class RepairAction(ContractModel):
    action_id: str
    action_type: Literal["rerun"]
    step_id: str
    reason: str
    depends_on: list[str] = Field(default_factory=list)
    preserve_outputs: list[str] = Field(default_factory=list)
    issue_ids: list[str] = Field(default_factory=list)
    locations: list[AuditLocation] = Field(default_factory=list, max_length=8)
    repair_instruction: str = ""
    previous_output_digest: str | None = Field(default=None, min_length=64, max_length=64)


class LocalRepairPlan(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    repair_id: str
    execution_id: str
    trigger_step_id: str
    issues: list[RepairIssue]
    actions: list[RepairAction]
    max_rounds: Literal[1] = 1
    requires_reaudit: bool = True
    status: Literal["planned", "needs_human_review"]

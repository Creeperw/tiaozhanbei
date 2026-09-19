from datetime import datetime
from typing import Literal

from pydantic import Field

from competition_app.contracts.base import ContractModel
from competition_app.contracts.local_repair import RepairIssue
from competition_app.contracts.audit_policy import ResourceProvenance


class ResourceClaim(ContractModel):
    claim_id: str
    text: str
    evidence_ids: list[str] = Field(min_length=1)


class QuestionConsumptionDecision(ContractModel):
    use_question_candidates: bool
    usage_reason: str
    selected_question_ids: list[str] = Field(default_factory=list)
    resource_type: Literal["none", "practice", "variant", "grading_support"] = "none"


class ResourceDraft(ContractModel):
    resource_draft_id: str
    title: str
    target_kp_id: str | None = None
    content: dict[str, object]
    estimated_minutes: int = Field(gt=0)
    claims: list[ResourceClaim] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    question_consumption: QuestionConsumptionDecision | None = None
    provenance: ResourceProvenance = Field(default_factory=ResourceProvenance)
    status: Literal["pending_review"] = "pending_review"


class AuditResult(ContractModel):
    audit_result_id: str
    decision: Literal["pass", "revise", "reject", "needs_human_review"]
    audit_report: str = ""
    findings: list[str] = Field(default_factory=list)
    structured_findings: list[RepairIssue] = Field(default_factory=list)
    verified_claim_ids: list[str] = Field(default_factory=list)
    subject_digest: str | None = Field(default=None, min_length=64, max_length=64)
    subject_type: Literal[
        "long_term_plan", "short_term_plan", "resource"
    ] | None = None
    parent_subject_digest: str | None = Field(default=None, min_length=64, max_length=64)
    plan_scope: Literal["long_term", "short_term", "daily_task"] | None = None
    medical_safety_approval: dict[str, object] | None = None
    # 本次审核是否真的拿到了一次可用的语义审核结论。模型输出不符合协议时，
    # 审核器只能退回确定性硬门禁：``decision`` 反映的是硬门禁结果，不是语义
    # 审核结论。发布侧不得把这种内容当作“已通过审核”放行——审核器失效不构成
    # 内容安全的证据。
    semantic_verdict_available: bool = True


class ResourceVersion(ContractModel):
    resource_id: str
    resource_version: int = Field(default=1, ge=1)
    source_draft_id: str
    title: str
    content: dict[str, object]
    audit_result_id: str
    status: Literal["published"] = "published"
    published_at: datetime

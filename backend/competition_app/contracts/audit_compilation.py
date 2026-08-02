from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from competition_app.contracts.base import ContractModel


AuditIssueType = Literal[
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


class AuditLocation(ContractModel):
    """A system-known location inside the artifact being audited."""

    location_key: str = Field(min_length=1, max_length=300)
    subject_type: Literal[
        "resource",
        "long_term_plan",
        "short_term_plan",
        "exam_paper",
        "paper_blueprint",
        "question_candidate_pool",
    ]
    location_type: Literal[
        "whole_subject",
        "section",
        "field",
        "stage",
        "progression_node",
        "unit",
        "question",
        "answer_key",
        "explanation",
    ]
    display_label: str = Field(min_length=1, max_length=300)


class AuditSourceAnchor(ContractModel):
    source_field: Literal["audit_report", "findings"]
    source_quote: str = Field(min_length=1, max_length=2_000)


class CompiledAuditFinding(ContractModel):
    issue_type: AuditIssueType
    message: str = Field(min_length=1, max_length=2_000)
    blocking: bool = True
    location_keys: list[str] = Field(min_length=1, max_length=8)
    source_anchors: list[AuditSourceAnchor] = Field(min_length=1, max_length=4)


class CompiledAuditFindings(ContractModel):
    status: Literal["compiled"]
    contract_version: Literal["1.0"] = "1.0"
    issues: list[CompiledAuditFinding] = Field(default_factory=list, max_length=16)


class AuditCompilationIssue(ContractModel):
    code: Literal[
        "schema_invalid",
        "source_anchor_missing",
        "source_anchor_invalid",
        "message_not_verbatim",
        "location_not_allowed",
    ]
    field_path: str = Field(min_length=1)
    detail: str | None = None


class AuditFindingsNeedRevision(ContractModel):
    status: Literal["needs_revision"]
    contract_version: Literal["1.0"] = "1.0"
    issues: list[AuditCompilationIssue] = Field(min_length=1)


AuditFindingsCompilerResult = Annotated[
    CompiledAuditFindings | AuditFindingsNeedRevision,
    Field(discriminator="status"),
]


class AuditFindingsCompilationEnvelope(ContractModel):
    result: AuditFindingsCompilerResult
    source_digest: str = Field(min_length=64, max_length=64)


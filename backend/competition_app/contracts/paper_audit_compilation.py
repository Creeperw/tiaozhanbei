from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from competition_app.contracts.base import ContractModel


PaperAuditIssueType = Literal[
    "missing_evidence",
    "conflicting_evidence",
    "content_quality",
    "paper_blueprint_mismatch",
    "unresolved",
]


class PaperAuditSourceAnchor(ContractModel):
    """Verbatim source for a compiler-classified paper audit issue."""

    source_field: Literal["audit_report", "findings"]
    source_quote: str = Field(min_length=1)


class CompiledPaperAuditIssue(ContractModel):
    issue_type: PaperAuditIssueType
    message: str = Field(min_length=1)
    blocking: bool
    source_anchors: list[PaperAuditSourceAnchor] = Field(min_length=1)


class CompiledPaperAuditFindings(ContractModel):
    status: Literal["compiled"]
    contract_version: Literal["1.0"] = "1.0"
    issues: list[CompiledPaperAuditIssue] = Field(default_factory=list)


class PaperAuditCompilationIssue(ContractModel):
    code: Literal[
        "schema_invalid",
        "source_anchor_missing",
        "source_anchor_invalid",
        "message_not_verbatim",
    ]
    field_path: str = Field(min_length=1)
    detail: str | None = None


class PaperAuditNeedsRevision(ContractModel):
    status: Literal["needs_revision"]
    contract_version: Literal["1.0"] = "1.0"
    issues: list[PaperAuditCompilationIssue] = Field(min_length=1)


PaperAuditCompilerResult = Annotated[
    CompiledPaperAuditFindings | PaperAuditNeedsRevision,
    Field(discriminator="status"),
]


class PaperAuditCompilationEnvelope(ContractModel):
    result: PaperAuditCompilerResult
    source_digest: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def compiled_issues_have_anchors(self) -> "PaperAuditCompilationEnvelope":
        if self.result.status == "compiled":
            for issue in self.result.issues:
                if not issue.source_anchors:
                    raise ValueError("compiled paper audit issue requires source anchors")
        return self

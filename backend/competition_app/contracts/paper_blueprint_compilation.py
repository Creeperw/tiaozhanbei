from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from competition_app.contracts.base import ContractModel


class PaperBlueprintSourceAnchor(ContractModel):
    source_field: Literal["blueprint_document"]
    source_quote: str = Field(min_length=1)


class CompiledBlueprintUnit(ContractModel):
    unit_key: str = Field(min_length=1)
    knowledge_module: str = Field(min_length=1)
    learning_objective: str = Field(min_length=1)
    retrieval_query: str = Field(min_length=1)
    question_type_preferences: list[str] = Field(default_factory=list)
    required_question_count: int = Field(gt=0, le=100)
    score_total: float | None = Field(default=None, gt=0)
    selection_rules: list[str] = Field(default_factory=list)


class CompiledPaperBlueprintContract(ContractModel):
    title: str = Field(min_length=1)
    scope_summary: str = Field(min_length=1)
    duration_minutes: int | None = Field(default=None, gt=0)
    total_score: float | None = Field(default=None, gt=0)
    units: list[CompiledBlueprintUnit] = Field(min_length=1, max_length=20)
    assumptions: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    field_anchors: dict[str, list[PaperBlueprintSourceAnchor]] = Field(
        default_factory=dict
    )


class CompiledPaperBlueprintResult(ContractModel):
    status: Literal["compiled"]
    contract_version: Literal["1.0"] = "1.0"
    contract: CompiledPaperBlueprintContract


class PaperBlueprintCompilationIssue(ContractModel):
    code: Literal[
        "schema_invalid",
        "missing_required_field",
        "source_anchor_missing",
        "source_anchor_invalid",
        "forbidden_system_field",
    ]
    field_path: str = Field(min_length=1)
    detail: str | None = None


class PaperBlueprintNeedsRevision(ContractModel):
    status: Literal["needs_revision"]
    contract_version: Literal["1.0"] = "1.0"
    issues: list[PaperBlueprintCompilationIssue] = Field(min_length=1)


PaperBlueprintCompilerResult = Annotated[
    CompiledPaperBlueprintResult | PaperBlueprintNeedsRevision,
    Field(discriminator="status"),
]


class PaperBlueprintCompilationEnvelope(ContractModel):
    result: PaperBlueprintCompilerResult
    source_digest: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def compiled_contract_has_anchors(self) -> "PaperBlueprintCompilationEnvelope":
        if self.result.status == "compiled" and not self.result.contract.field_anchors:
            raise ValueError("compiled paper blueprint requires source anchors")
        return self

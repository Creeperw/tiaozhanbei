from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from competition_app.contracts.base import ContractModel


class PaperAssemblySourceAnchor(ContractModel):
    source_field: Literal["assembly_document"]
    source_quote: str = Field(min_length=1)


class CompiledSelectedPaperItem(ContractModel):
    unit_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    score: float | None = Field(default=None, gt=0)
    source_anchors: list[PaperAssemblySourceAnchor] = Field(min_length=1)


class CompiledGeneratedPaperItem(ContractModel):
    unit_id: str = Field(min_length=1)
    question_type: str = Field(min_length=1)
    stem: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    reference_answer: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    source_basis_refs: list[str] = Field(default_factory=list)
    source_anchors: list[PaperAssemblySourceAnchor] = Field(min_length=1)

    @model_validator(mode="after")
    def choice_question_has_options(self) -> "CompiledGeneratedPaperItem":
        if "选择" in self.question_type and len(self.options) < 2:
            raise ValueError("generated choice question requires at least two options")
        return self


class CompiledPaperAssemblyContract(ContractModel):
    title: str = Field(min_length=1)
    selected_items: list[CompiledSelectedPaperItem] = Field(default_factory=list)
    generated_items: list[CompiledGeneratedPaperItem] = Field(default_factory=list)
    field_anchors: dict[str, list[PaperAssemblySourceAnchor]] = Field(
        default_factory=dict
    )


class CompiledPaperAssemblyResult(ContractModel):
    status: Literal["compiled"]
    contract_version: Literal["1.0"] = "1.0"
    contract: CompiledPaperAssemblyContract


class PaperAssemblyCompilationIssue(ContractModel):
    code: Literal[
        "schema_invalid",
        "source_anchor_missing",
        "source_anchor_invalid",
        "candidate_unknown",
        "candidate_unit_mismatch",
    ]
    field_path: str = Field(min_length=1)
    detail: str | None = None


class PaperAssemblyNeedsRevision(ContractModel):
    status: Literal["needs_revision"]
    contract_version: Literal["1.0"] = "1.0"
    issues: list[PaperAssemblyCompilationIssue] = Field(min_length=1)


PaperAssemblyCompilerResult = Annotated[
    CompiledPaperAssemblyResult | PaperAssemblyNeedsRevision,
    Field(discriminator="status"),
]


class PaperAssemblyCompilationEnvelope(ContractModel):
    result: PaperAssemblyCompilerResult
    source_digest: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def compiled_contract_has_anchors(self) -> "PaperAssemblyCompilationEnvelope":
        if self.result.status == "compiled" and not self.result.contract.field_anchors:
            raise ValueError("compiled paper assembly requires title anchor")
        return self

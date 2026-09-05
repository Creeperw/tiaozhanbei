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
    # The model selects stable IDs; the compiler materializes the exact quote.
    source_anchor_ids: list[str] = Field(default_factory=list)
    source_anchors: list[PaperAssemblySourceAnchor] = Field(default_factory=list)


class CompiledGeneratedPaperItem(ContractModel):
    unit_id: str = Field(min_length=1)
    question_type: str = Field(min_length=1)
    stem: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    reference_answer: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    source_basis_refs: list[str] = Field(default_factory=list)
    # Keep source_anchors for backward-compatible snapshots, but prefer IDs in
    # new model output so the model cannot paraphrase source text.
    source_anchor_ids: list[str] = Field(default_factory=list)
    source_anchors: list[PaperAssemblySourceAnchor] = Field(default_factory=list)

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


class PaperAssemblyCandidateBinding(ContractModel):
    """System-owned, run-local binding for one selectable candidate.

    ``candidate_no`` is the only identity exposed to the selection model.  All
    execution-critical fields remain in this immutable server-side snapshot,
    so a model response cannot author or override a question ID, unit binding,
    source binding, or canonical question content.
    """

    candidate_no: int = Field(ge=1)
    binding_id: str = Field(min_length=16, max_length=80)
    unit_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    question_type: str = Field(min_length=1)
    content_digest: str = Field(min_length=64, max_length=64)
    selectable: bool = True


class PaperAssemblyCandidateCatalogSnapshot(ContractModel):
    """Immutable candidate-number mapping scoped to one workflow execution."""

    catalog_id: str = Field(min_length=16, max_length=80)
    execution_id: str = Field(min_length=1)
    candidate_pool_id: str = Field(min_length=1)
    catalog_digest: str = Field(min_length=64, max_length=64)
    candidates: list[PaperAssemblyCandidateBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def candidate_numbers_and_bindings_are_unique(
        self,
    ) -> "PaperAssemblyCandidateCatalogSnapshot":
        candidate_numbers = [item.candidate_no for item in self.candidates]
        if len(candidate_numbers) != len(set(candidate_numbers)):
            raise ValueError("candidate catalog contains duplicate candidate_no")
        binding_ids = [item.binding_id for item in self.candidates]
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("candidate catalog contains duplicate binding_id")
        return self


class ResolvedPaperCandidateSelection(ContractModel):
    """A model choice after deterministic resolution against the snapshot."""

    candidate_no: int = Field(ge=1)
    binding_id: str = Field(min_length=16, max_length=80)
    unit_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    selection_rationale: str = Field(min_length=1, max_length=500)


class PaperAssemblySelectionIssue(ContractModel):
    code: Literal[
        "catalog_execution_mismatch",
        "candidate_no_unknown",
        "candidate_no_duplicate",
        "candidate_not_selectable",
    ]
    candidate_no: int | None = Field(default=None, ge=1)
    detail: str | None = Field(default=None, max_length=500)


class PaperAssemblySelectionCompilation(ContractModel):
    """Result of the non-LLM selection compiler."""

    status: Literal["compiled"] = "compiled"
    contract_version: Literal["2.0"] = "2.0"
    catalog_id: str = Field(min_length=16, max_length=80)
    catalog_digest: str = Field(min_length=64, max_length=64)
    selected_items: list[ResolvedPaperCandidateSelection] = Field(default_factory=list)
    issues: list[PaperAssemblySelectionIssue] = Field(default_factory=list)

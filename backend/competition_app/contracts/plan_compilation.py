from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from competition_app.contracts.base import ContractModel


PlanScope: TypeAlias = Literal["long_term", "short_term", "daily_task"]


class PlanSourceAnchor(ContractModel):
    """Verbatim evidence for one compiler-derived field."""

    source_field: str = Field(min_length=1)
    source_quote: str = Field(min_length=1)


class PlanContractIssue(ContractModel):
    code: Literal[
        "missing_required_field",
        "missing_source",
        "source_anchor_missing",
        "source_anchor_invalid",
        "source_value_not_verbatim",
        "scope_violation",
        "forbidden_system_field",
        "immutable_route_conflict",
        "route_stage_missing",
        "route_book_missing",
        "parent_plan_missing",
        "parent_plan_conflict",
        "candidate_unknown",
        "candidate_blocked",
        "candidate_scope_mismatch",
        "textbook_selection_conflict",
        "prerequisite_unconfirmed",
        "time_budget_exceeded",
        "schema_invalid",
    ]
    category: Literal["missing", "conflict", "invalid"]
    field_path: str = Field(min_length=1)
    source_refs: list[str] = Field(default_factory=list)
    conflicting_source_refs: list[str] = Field(default_factory=list)


class CompiledLongTermStage(ContractModel):
    stage: int = Field(ge=1)
    stage_name: str = Field(min_length=1)
    books: list[str] = Field(min_length=1)
    goal: str = Field(min_length=1)
    duration_days: int = Field(gt=0, le=3_650)
    schedule_summary: str = Field(min_length=1)


class CompiledLongTermContract(ContractModel):
    scope: Literal["long_term"]
    long_term_plan_content: str = Field(min_length=1)
    total_duration_days: int = Field(gt=0, le=3_650)
    stages: list[CompiledLongTermStage] = Field(min_length=1)
    field_anchors: dict[str, list[PlanSourceAnchor]] = Field(default_factory=dict)


class CompiledShortTermContract(ContractModel):
    scope: Literal["short_term"]
    short_term_plan_content: str = Field(min_length=1)
    duration_days: int = Field(gt=0, le=365)
    progression_nodes: list[str] = Field(min_length=2, max_length=12)
    expected_output: str = Field(min_length=1)
    completion_criteria: str = Field(min_length=1)
    selected_stage_id: str | None = None
    selected_books: list[str] = Field(min_length=1, max_length=2)
    field_anchors: dict[str, list[PlanSourceAnchor]] = Field(default_factory=dict)


class CompiledDailyTaskContract(ContractModel):
    scope: Literal["daily_task"]
    daily_task_content: str = Field(min_length=1)
    learning_chapter: str = Field(min_length=1)
    focus_knowledge_points: list[str] = Field(min_length=1, max_length=5)
    estimated_minutes: int = Field(gt=0)
    expected_output: str = Field(min_length=1)
    completion_criteria: str = Field(min_length=1)
    field_anchors: dict[str, list[PlanSourceAnchor]] = Field(default_factory=dict)


ScopedPlanContract = Annotated[
    CompiledLongTermContract
    | CompiledShortTermContract
    | CompiledDailyTaskContract,
    Field(discriminator="scope"),
]


class CompiledPlanContractResult(ContractModel):
    status: Literal["compiled"]
    contract_version: Literal["1.0"] = "1.0"
    contract: ScopedPlanContract


class PlanContractNeedsRevision(ContractModel):
    status: Literal["needs_revision"]
    contract_version: Literal["1.0"] = "1.0"
    issues: list[PlanContractIssue] = Field(min_length=1)


PlanContractCompilerResult = Annotated[
    CompiledPlanContractResult | PlanContractNeedsRevision,
    Field(discriminator="status"),
]


class PlanCompilationEnvelope(ContractModel):
    """Compiler output plus deterministic source-integrity metadata."""

    result: PlanContractCompilerResult
    source_digest: str = Field(min_length=64, max_length=64)
    revision_count: int = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def compiled_contract_has_current_scope(self) -> "PlanCompilationEnvelope":
        if self.result.status == "compiled" and not self.result.contract.field_anchors:
            raise ValueError("compiled plan contract requires field anchors")
        return self

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from competition_app.contracts.base import ContractModel


class TextbookRouteStage(ContractModel):
    stage_id: str = Field(min_length=1)
    order: int = Field(ge=1)
    name: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    books: list[str] = Field(min_length=1)
    exit_evidence: list[str] = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)


class TextbookPrerequisiteRule(ContractModel):
    course: str = Field(min_length=1)
    before_stage_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class TextbookEquivalenceGroup(ContractModel):
    canonical: str = Field(min_length=1)
    alternatives: list[str] = Field(min_length=1)
    policy: str = Field(min_length=1)


class TextbookRouteSource(ContractModel):
    source_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    title: str = Field(min_length=1)


class TextbookLearningRoute(ContractModel):
    route_id: str = Field(min_length=1)
    route_version: int = Field(ge=1)
    status: Literal["approved", "draft", "retired"]
    goal_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    stages: list[TextbookRouteStage] = Field(min_length=1)
    prerequisites: list[TextbookPrerequisiteRule] = Field(default_factory=list)
    equivalence_groups: list[TextbookEquivalenceGroup] = Field(default_factory=list)
    source_refs: list[str] = Field(min_length=1)
    reviewed_by: str = Field(min_length=1)

    @model_validator(mode="after")
    def stages_are_ordered_and_unique(self) -> "TextbookLearningRoute":
        orders = [stage.order for stage in self.stages]
        stage_ids = [stage.stage_id for stage in self.stages]
        if orders != list(range(1, len(self.stages) + 1)):
            raise ValueError("textbook route stage order must be continuous from 1")
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("textbook route stage IDs must be unique")
        known_stage_ids = set(stage_ids)
        if any(
            rule.before_stage_id not in known_stage_ids
            for rule in self.prerequisites
        ):
            raise ValueError("prerequisite must reference a route stage")
        return self


class TextbookRouteBinding(ContractModel):
    exam_route_id: str = Field(min_length=1)
    textbook_route_id: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    default: bool = False


class ResolvedTextbookRoute(ContractModel):
    planning_status: Literal["resolved", "needs_clarification", "unmatched"]
    match_reason: str = Field(min_length=1)
    route: TextbookLearningRoute | None = None
    clarification_questions: list[str] = Field(default_factory=list)


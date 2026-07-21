from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, Field, model_validator

from competition_app.contracts.base import ContractModel
from competition_app.contracts.textbook_route import ResolvedTextbookRoute


class DefaultRouteSource(ContractModel):
    source_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_version: str | None = None


class RouteReviewMetadata(ContractModel):
    reviewed_by: str = Field(min_length=1)
    reviewed_at: datetime
    review_note: str | None = None


class ProjectImportMetadata(ContractModel):
    imported_from: str = Field(min_length=1)
    imported_at: datetime
    import_policy: str = Field(min_length=1)


class DefaultRoutePhase(ContractModel):
    phase_id: str = Field(min_length=1)
    name: str = Field(
        min_length=1,
        validation_alias=AliasChoices("name", "phase_name"),
    )
    objective: str = Field(min_length=1)
    books: list[str] = Field(
        default_factory=list,
        description="本阶段使用的具体教材或当前年度官方指导用书。",
    )
    learning_focus: list[str] = Field(default_factory=list)
    sequence_basis: str | None = None
    exit_evidence: list[str] = Field(min_length=1)
    source_refs: list[str] = Field(default_factory=list)


class DefaultLearningRoute(ContractModel):
    route_id: str = Field(min_length=1)
    route_version: int = Field(ge=1)
    route_status: Literal["approved", "draft", "retired"]
    status: Literal["approved", "draft", "retired"]
    goal_type: str = Field(min_length=1)
    goal_name: str = Field(min_length=1)
    phases: list[DefaultRoutePhase] = Field(min_length=1)
    sources: list[DefaultRouteSource] = Field(default_factory=list)
    review_metadata: RouteReviewMetadata | None = None
    aliases: list[str] = Field(default_factory=list)
    planning_label: str | None = None
    personalization_rules: list[str] = Field(default_factory=list)
    refresh_rule: str | None = None
    runtime_checks: list[str] = Field(default_factory=list)
    project_import_metadata: ProjectImportMetadata | None = None

    @model_validator(mode="after")
    def approved_routes_have_governance_metadata(self) -> "DefaultLearningRoute":
        if self.status == "approved":
            if self.route_status != "approved":
                raise ValueError("approved route must have route_status='approved'")
            if not self.sources:
                raise ValueError("approved route requires sources")
            if self.review_metadata is None:
                raise ValueError("approved route requires review_metadata")
            if not self.planning_label:
                raise ValueError("approved route requires planning_label")
            if not self.personalization_rules:
                raise ValueError("approved route requires personalization_rules")
            if not self.refresh_rule:
                raise ValueError("approved route requires refresh_rule")
            if not self.runtime_checks:
                raise ValueError("approved route requires runtime_checks")
            if self.project_import_metadata is None:
                raise ValueError("approved route requires project_import_metadata")
            if any(not phase.source_refs for phase in self.phases):
                raise ValueError("approved route requires phase source_refs")
        return self


class ResolvedPlanningRoute(ContractModel):
    goal_type: str = Field(min_length=1)
    goal_name: str = Field(min_length=1)
    planning_status: Literal["approved_route", "provisional"]
    match_reason: str = Field(min_length=1)
    route_id: str | None = None
    route_version: int | None = Field(default=None, ge=1)
    route_status: Literal["approved"] | None = None
    planning_label: str | None = None
    phases: list[DefaultRoutePhase] = Field(default_factory=list)
    sources: list[DefaultRouteSource] = Field(default_factory=list)
    runtime_checks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unknowns_to_confirm: list[str] = Field(default_factory=list)
    textbook_route: ResolvedTextbookRoute | None = None

    @model_validator(mode="after")
    def resolution_has_required_route_or_provisional_context(self) -> "ResolvedPlanningRoute":
        if self.planning_status == "approved_route":
            if (
                self.route_id is None
                or self.route_version is None
                or self.route_status != "approved"
            ):
                raise ValueError(
                    "approved_route requires route_id, route_version, and route_status='approved'"
                )
        elif not (self.assumptions or self.unknowns_to_confirm):
            raise ValueError(
                "provisional route requires assumptions or unknowns_to_confirm"
            )
        return self

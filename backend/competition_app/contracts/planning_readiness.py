from __future__ import annotations

from typing import Literal

from pydantic import Field

from competition_app.contracts.base import ContractModel


PlanningReadinessStatus = Literal[
    "ready",
    "needs_profile",
    "needs_long_term_plan",
    "needs_short_term_plan",
    "stale_parent_plan",
]


class PlanningAction(ContractModel):
    action: str = Field(min_length=1)
    method: Literal["GET", "POST"]
    endpoint: str = Field(min_length=1)
    plan_scope: Literal["long_term", "short_term", "daily_task"] | None = None


class PlanningParentState(ContractModel):
    scope: Literal["long_term", "short_term"]
    exists: bool
    valid: bool
    persisted: bool = False
    plan_id: str | None = None
    version: int | None = None
    reason_codes: list[str] = Field(default_factory=list)


class PlanningReadiness(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    requested_scope: Literal["long_term", "short_term", "daily_task"]
    status: PlanningReadinessStatus
    can_generate: bool
    required_action: Literal[
        "none",
        "complete_profile",
        "create_long_term_plan",
        "create_short_term_plan",
        "refresh_parent_plan",
    ]
    reason_codes: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    missing_profile_fields: list[str] = Field(default_factory=list)
    next_profile_field: str | None = None
    parent_states: list[PlanningParentState] = Field(default_factory=list)
    available_actions: list[PlanningAction] = Field(default_factory=list)

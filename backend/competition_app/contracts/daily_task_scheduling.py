from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from competition_app.contracts.base import ContractModel


DailyTaskCandidateKind = Literal[
    "carryover",
    "remediation",
    "due_review",
    "new_learning",
    "maintenance",
]


class DailyTaskScoreTrace(ContractModel):
    """Deterministic, auditable inputs used by the daily-task scheduler."""

    model_config = ConfigDict(extra="forbid")

    mastery_gap: float | None = Field(default=None, ge=0, le=1)
    urgency: float | None = Field(default=None, ge=0, le=1)
    review_benefit: float | None = Field(default=None, ge=0, le=1)
    plan_alignment: float | None = Field(default=None, ge=0, le=1)
    difficulty_fit: float | None = Field(default=None, ge=0, le=1)
    autonomy_support: float | None = Field(default=None, ge=0, le=1)
    time_cost: float = Field(ge=0, le=1)
    risk: float = Field(ge=0, le=1)
    score: float = Field(ge=0, le=1)
    missing_positive_components: list[str] = Field(default_factory=list)


class DailyTaskScheduledCandidate(ContractModel):
    """One formal knowledge-point candidate and its scheduling disposition."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    knowledge_point_name: str = Field(min_length=1)
    kp_id: str = Field(min_length=1)
    task_kind: DailyTaskCandidateKind
    estimated_minutes: float = Field(gt=0)
    required: bool = False
    defer_allowed: bool = True
    source_plan_node: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    score_trace: DailyTaskScoreTrace
    reason: str = Field(min_length=1)


class DailyTaskBlockedCandidate(ContractModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    knowledge_point_name: str = Field(min_length=1)
    kp_id: str | None = None
    reason: str = Field(min_length=1)
    source_refs: list[str] = Field(default_factory=list)


class DailyTaskSchedule(ContractModel):
    """System-owned scheduling result persisted with the published task."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    policy_id: Literal["daily-task-multi-constraint-v1"] = (
        "daily-task-multi-constraint-v1"
    )
    exam_scope_id: str = Field(min_length=1)
    state_digest: str | None = None
    target_minutes: float = Field(gt=0, le=1440)
    allocation: dict[str, float] = Field(default_factory=dict)
    selected: list[DailyTaskScheduledCandidate] = Field(default_factory=list)
    deferred: list[DailyTaskScheduledCandidate] = Field(default_factory=list)
    blocked: list[DailyTaskBlockedCandidate] = Field(default_factory=list)
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_content_conservation(self) -> "DailyTaskSchedule":
        ids = [
            item.candidate_id
            for group in (self.selected, self.deferred)
            for item in group
        ] + [item.candidate_id for item in self.blocked]
        if len(ids) != len(set(ids)):
            raise ValueError("daily-task candidate may have only one disposition")
        selected_minutes = sum(item.estimated_minutes for item in self.selected)
        if selected_minutes > self.target_minutes + 1e-9:
            raise ValueError("selected daily-task candidates exceed target minutes")
        return self

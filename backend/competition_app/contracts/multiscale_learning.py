from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from competition_app.contracts.base import ContractModel


class MetricValue(ContractModel):
    available: bool
    value: float | int | None = None
    unit: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def availability_matches_value(self) -> "MetricValue":
        if not self.available:
            if self.value is not None:
                raise ValueError("unavailable metric value must be null")
            if not str(self.unavailable_reason or "").strip():
                raise ValueError("unavailable metric requires reason")
        elif self.value is None:
            raise ValueError("available metric requires value")
        return self


class HardConstraintResult(ContractModel):
    key: str
    passed: bool
    reason: str
    source_refs: list[str] = Field(default_factory=list)


class PathCandidate(ContractModel):
    candidate_id: str
    scope: Literal["long_term", "short_term", "daily_task"]
    stage: dict[str, Any] = Field(default_factory=dict)
    books: list[dict[str, str]] = Field(default_factory=list)
    knowledge_points: list[dict[str, str]] = Field(default_factory=list)
    estimated_minutes: int = Field(ge=0, le=1440)
    eligible: bool
    blocked_reasons: list[str] = Field(default_factory=list)
    hard_constraint_results: list[HardConstraintResult]
    score: float = Field(ge=0, le=1)
    score_components: dict[str, MetricValue]
    evidence_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    recommended_action: str

    @model_validator(mode="after")
    def score_components_are_normalized(self) -> "PathCandidate":
        if any(
            item.value is not None and not 0 <= float(item.value) <= 1
            for item in self.score_components.values()
        ):
            raise ValueError("score component values must be between 0 and 1")
        return self


class MultiScaleLearningState(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    state_id: str
    learner_id: str
    generated_at: datetime
    macro: dict[str, Any]
    meso: dict[str, Any]
    micro: dict[str, Any]
    data_quality: dict[str, Any]
    hard_constraints: list[HardConstraintResult]
    source_refs: list[dict[str, Any]]
    state_digest: str = Field(pattern=r"^[0-9a-f]{24}$")

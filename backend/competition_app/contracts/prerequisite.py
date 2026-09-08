from __future__ import annotations

from typing import Literal

from pydantic import Field, ConfigDict

from competition_app.contracts.base import ContractModel


class PrerequisiteJudgment(ContractModel):
    """Diagnosis-owned interpretation, never a persisted completion record."""

    model_config = ConfigDict(extra="forbid")
    course: str = Field(min_length=1, max_length=120)
    status: Literal["satisfied", "unmet", "unknown"]
    source_ref: str = Field(min_length=1, max_length=200)
    source_quote: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=1000)


class PrerequisiteEvidenceSnapshot(ContractModel):
    """Legacy candidate snapshot, not proof of semantic prerequisite mastery.

    Long/short planning replaces this snapshot after route resolution with
    source-checked Diagnosis judgments reviewed independently by Audit.
    No judgment writes completion or mastery records.
    """

    route_id: str = ""
    required_courses: list[str] = Field(default_factory=list)
    satisfied_courses: list[str] = Field(default_factory=list)
    unmet_courses: list[str] = Field(default_factory=list)
    unknown_courses: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


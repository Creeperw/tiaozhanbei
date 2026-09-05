from __future__ import annotations

from typing import Literal

from pydantic import Field

from competition_app.contracts.base import ContractModel


class ProfileFieldRequirement(ContractModel):
    field: str = Field(min_length=1)
    question: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ProfileReadiness(ContractModel):
    status: Literal["complete", "incomplete"]
    required_for: Literal["long_term_plan"] = "long_term_plan"
    can_proceed: bool
    missing_fields: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    next_field: str | None = None

from __future__ import annotations

from pydantic import Field

from competition_app.contracts.base import ContractModel


class PrerequisiteEvidenceSnapshot(ContractModel):
    """System-owned prerequisite facts shared by planning components.

    The snapshot is derived deterministically from the approved textbook route,
    persisted profile facts and the learner's latest explicit statements.  It
    is execution evidence, not a model-generated business output.
    """

    route_id: str = ""
    required_courses: list[str] = Field(default_factory=list)
    satisfied_courses: list[str] = Field(default_factory=list)
    unmet_courses: list[str] = Field(default_factory=list)
    unknown_courses: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


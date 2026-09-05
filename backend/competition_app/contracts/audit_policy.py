from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from competition_app.contracts.base import ContractModel


class ResourceProvenance(ContractModel):
    """System-owned provenance for the learner-visible resource materializer."""

    question_origin: Literal["none", "formal_candidate", "system_self_check"] = "none"
    selected_question_ids: list[str] = Field(default_factory=list)
    selected_evidence_ids: list[str] = Field(default_factory=list)
    selected_video_evidence_ids: list[str] = Field(default_factory=list)
    selected_reference_evidence_ids: list[str] = Field(default_factory=list)
    generated_sections: list[str] = Field(default_factory=list)
    materialized_sections: list[str] = Field(default_factory=list)


class AcceptancePolicy(ContractModel):
    """One compact policy shared by the producer, materializer and auditor."""

    policy_id: str
    policy_version: str = "1.0"
    task_type: str
    subject_type: Literal[
        "resource", "long_term_plan", "short_term_plan", "exam_paper"
    ]
    hard_requirements: list[str] = Field(default_factory=list)
    non_blocking_preferences: list[str] = Field(default_factory=list)
    blocking_issue_types: list[
        Literal["missing_evidence", "factual_error", "safety_violation"]
    ] = Field(default_factory=list)
    non_blocking_issue_types: list[
        Literal["content_quality", "conflicting_evidence", "learner_mismatch"]
    ] = Field(default_factory=list)
    decision_policy: dict[
        Literal["pass", "revise", "reject", "needs_human_review"], str
    ] = Field(default_factory=dict)
    allowed_resource_origins: list[str] = Field(default_factory=list)
    formal_learning_task: dict[str, Any] | None = None
    formal_task_available: bool = False
    learner_fit_facts: dict[str, Any] = Field(default_factory=dict)
    repair_ownership: dict[str, str] = Field(default_factory=dict)


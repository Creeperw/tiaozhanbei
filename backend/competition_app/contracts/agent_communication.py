from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from competition_app.contracts.base import ContractModel


class EvidenceReference(ContractModel):
    evidence_id: str
    source_type: str
    source_id: str
    claim: str
    quality_label: str = "unknown"
    retrieved_at: datetime | None = None


class ConfirmedFact(ContractModel):
    fact_id: str
    category: str
    content: str
    learner_id: str
    evidence_refs: list[str] = Field(default_factory=list)
    source_step_id: str
    freshness: str = "unknown"


class UncertaintyItem(ContractModel):
    uncertainty_id: str
    category: str
    description: str
    blocking: bool = False
    resolution_action: str | None = None


class DownstreamNeed(ContractModel):
    field: str
    reason: str
    required: bool = True
    accepted_source_types: list[str] = Field(default_factory=list)


class AgentHandoffBundle(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    handoff_id: str
    trace_id: str
    execution_id: str
    learner_id: str
    source_steps: list[str] = Field(default_factory=list)
    target_agent: str
    purpose: str
    confirmed_facts: list[ConfirmedFact] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    uncertainties: list[UncertaintyItem] = Field(default_factory=list)
    task_constraints: dict[str, Any] = Field(default_factory=dict)
    downstream_needs: list[DownstreamNeed] = Field(default_factory=list)
    omitted_categories: list[str] = Field(default_factory=list)
    generated_at: datetime

    @model_validator(mode="after")
    def facts_belong_to_same_learner(self) -> "AgentHandoffBundle":
        if any(item.learner_id != self.learner_id for item in self.confirmed_facts):
            raise ValueError("handoff facts must belong to the same learner")
        return self


class CognitiveGapResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    target_agent: str
    satisfied_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    blocking_fields: list[str] = Field(default_factory=list)
    omitted_categories: list[str] = Field(default_factory=list)

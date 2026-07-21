from typing import Literal

from pydantic import Field

from competition_app.contracts.base import ArtifactReference, ContractModel


class ConversationContextSummary(ContractModel):
    summary: str
    source_refs: list[ArtifactReference] = Field(min_length=1)
    preserved_facts: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    temporary_constraints: list[str] = Field(default_factory=list)
    compression_version: str = "1.0.0"


class LongTermMemoryCandidate(ContractModel):
    summary: str
    source_refs: list[ArtifactReference] = Field(min_length=1)
    status: Literal["pending_confirmation"] = "pending_confirmation"


class LearnerContextBrief(ContractModel):
    learner_id: str
    confirmed_preferences: dict[str, object] = Field(default_factory=dict)
    relevant_memories: list[str] = Field(default_factory=list)
    temporary_constraints: list[str] = Field(default_factory=list)
    context_summary_ref: ArtifactReference | None = None

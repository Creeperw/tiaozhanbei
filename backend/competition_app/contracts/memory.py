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
    status: Literal["pending_confirmation", "auto_confirmed"] = "pending_confirmation"
    # 可选类别透传：模型输出本身不携带类别，上游可在构造候选时标注；
    # 系统侧 health_memory 会做确定性分类兜底（主观感受/偏好一律不自动沉淀）。
    category: str = "long_term"


class RelevantMemoryReference(ContractModel):
    memory_id: int = Field(gt=0)
    category: str = Field(min_length=1)
    title: str = ""
    content: str = Field(min_length=1)
    similarity: float | None = Field(default=None, ge=-1.0, le=1.0)


class MemoryConflict(ContractModel):
    memory_id: int = Field(gt=0)
    proposed_memory: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class MemoryGovernanceDecision(ContractModel):
    analysis: str = Field(min_length=1)
    memory_candidates: list[LongTermMemoryCandidate] = Field(default_factory=list)
    auto_confirm_memories: list[LongTermMemoryCandidate] = Field(default_factory=list)
    conflicts: list[MemoryConflict] = Field(default_factory=list)
    requires_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    interrupt_type: Literal["memory_conflict"] | None = None
    resolution: Literal[
        "none",
        "keep_existing",
        "use_current_once",
        "replace_existing",
        "needs_clarification",
    ] = "none"

    @property
    def reason(self) -> str:
        return self.analysis

    @property
    def requested_scope(self) -> None:
        return None


class LearnerContextBrief(ContractModel):
    learner_id: str
    confirmed_preferences: dict[str, object] = Field(default_factory=dict)
    relevant_memories: list[str] = Field(default_factory=list)
    temporary_constraints: list[str] = Field(default_factory=list)
    context_summary_ref: ArtifactReference | None = None

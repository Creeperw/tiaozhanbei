from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field


PayloadT = TypeVar("PayloadT")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactReference(ContractModel):
    ref_type: str
    ref_id: str
    version: int = 1
    required: bool = True
    purpose: str | None = None


class WritebackIntent(ContractModel):
    intent_id: str
    source_artifact_id: str
    effect_type: str
    target_service: str
    target_entity_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    preconditions: list[str] = Field(default_factory=list)
    idempotency_key: str
    status: Literal["pending", "applied", "rejected", "failed"] = "pending"


class AgentEnvelope(ContractModel, Generic[PayloadT]):
    artifact_id: str
    artifact_type: str
    case_id: str
    trace_id: str
    request_id: str
    execution_id: str
    step_id: str
    producer: str
    task_type: str
    learner_id: str
    payload: PayloadT
    version: int = 1
    schema_version: str = "1.0.0"
    input_refs: list[ArtifactReference] = Field(default_factory=list)
    evidence_refs: list[ArtifactReference] = Field(default_factory=list)
    writeback_intents: list[WritebackIntent] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: Literal["success", "failed", "needs_human_review"] = "success"
    requires_review: bool = False
    created_at: datetime = Field(default_factory=utc_now)

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


WorkshopModuleKey = Literal["question_training", "knowledge_cards", "paper_workspace"]


class WorkshopModule(BaseModel):
    key: WorkshopModuleKey
    label: str
    description: str
    enabled: bool = True
    recommended: bool = False
    capabilities: list[str] = Field(default_factory=list)


class WorkshopOverview(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    modules: list[WorkshopModule]
    default_module: WorkshopModuleKey = "question_training"
    endpoints: dict[str, str] = Field(default_factory=dict)


class ResourceCoverage(BaseModel):
    knowledge_point: bool = False
    explanation: bool = False
    textbook_slices: bool = False
    videos: bool = False
    questions: bool = False
    fallback_used: list[Literal["video", "question"]] = Field(default_factory=list)


class KnowledgeResourceBundle(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    bundle_id: str
    knowledge_point: dict[str, Any]
    explanation: dict[str, Any]
    textbook_slices: list[dict[str, Any]] = Field(default_factory=list)
    videos: list[dict[str, Any]] = Field(default_factory=list)
    questions: list[dict[str, Any]] = Field(default_factory=list)
    coverage: ResourceCoverage
    provenance: list[dict[str, Any]] = Field(default_factory=list)


class KnowledgeCardSummary(BaseModel):
    card_id: str
    kp_id: str
    title: str
    learning_status: Literal["learned"] = "learned"
    source_execution_id: str = ""
    updated_at: datetime | None = None


class KnowledgeCardDetail(KnowledgeCardSummary):
    schema_version: Literal["1.0"] = "1.0"
    resource_bundle: KnowledgeResourceBundle


class KnowledgeCardPage(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    items: list[KnowledgeCardSummary]
    total: int
    offset: int
    limit: int


class PaperTiming(BaseModel):
    duration_minutes: int = Field(ge=1, le=24 * 60)
    started_at: datetime | None = None
    expires_at: datetime | None = None
    remaining_seconds: int | None = Field(default=None, ge=0)
    expired: bool = False
    paused: bool = False
    paused_at: datetime | None = None


class UiAction(BaseModel):
    action_type: Literal["navigate"] = "navigate"
    label: str
    destination: Literal[
        "workshop.question_training",
        "workshop.knowledge_card",
        "workshop.paper",
    ]
    params: dict[str, str] = Field(default_factory=dict)

from __future__ import annotations

from typing import Literal

from pydantic import Field

from competition_app.contracts.base import ContractModel


LearningPathNodeType = Literal["stage", "book", "knowledge_point"]
LearningPathNodeStatus = Literal[
    "completed",
    "in_progress",
    "next",
    "locked",
    "unassessed",
]


class LearningPathPlanRef(ContractModel):
    plan_id: str = Field(min_length=1)
    plan_version: int = Field(ge=1)
    route_id: str | None = None
    route_version: int | None = Field(default=None, ge=1)


class LearningPathNavigation(ContractModel):
    action: Literal["expand", "open_knowledge_atlas", "open_knowledge_point"]
    parent_id: str | None = None
    route_id: str | None = None
    book: str | None = None
    chapter: str | None = None
    kp_id: str | None = None


class LearningPathNode(ContractModel):
    node_id: str = Field(min_length=1)
    node_type: LearningPathNodeType
    parent_id: str | None = None
    title: str = Field(min_length=1)
    order: int = Field(ge=1)
    status: LearningPathNodeStatus = "unassessed"
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    mastery: float | None = Field(default=None, ge=0.0, le=1.0)
    has_children: bool = False
    child_count: int = Field(default=0, ge=0)
    description: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    navigation: LearningPathNavigation


class LearningPathPage(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    learner_id: str = Field(min_length=1)
    plan_ref: LearningPathPlanRef
    parent_id: str | None = None
    parent_type: LearningPathNodeType | None = None
    current_node_id: str | None = None
    nodes: list[LearningPathNode] = Field(default_factory=list)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1)
    total: int = Field(default=0, ge=0)
    has_more: bool = False

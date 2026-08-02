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
        "workshop.paper",
    ]
    params: dict[str, str] = Field(default_factory=dict)

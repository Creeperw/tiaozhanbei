from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class StepTrace(BaseModel):
    step_id: str
    agent: str
    status: Literal["running", "retrying", "success", "failed"]
    attempt: int = Field(ge=1)
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ToolTrace(BaseModel):
    tool_name: str
    agent: str
    status: Literal["success", "failed"]
    duration_ms: int = Field(ge=0)
    safe_input_summary: dict[str, object] = Field(default_factory=dict)
    safe_output_summary: dict[str, object] = Field(default_factory=dict)
    error_type: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TraceRecorder:
    def __init__(self) -> None:
        self.items: list[StepTrace] = []
        self.tool_items: list[ToolTrace] = []

    def record(self, step_id: str, agent: str, status: str, attempt: int, error: str | None = None) -> None:
        self.items.append(
            StepTrace(step_id=step_id, agent=agent, status=status, attempt=attempt, error=error)
        )

    def record_tool(self, trace: ToolTrace) -> None:
        self.tool_items.append(trace)

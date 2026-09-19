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


class CommunicationTrace(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    handoff_id: str
    step_id: str
    target_agent: str
    fact_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    blocking_field_count: int = Field(ge=0)
    omitted_categories: list[str] = Field(default_factory=list)
    status: Literal["prepared", "blocked", "consumed"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RepairTrace(BaseModel):
    """A safe, durable summary of one bounded audit-triggered repair."""

    repair_id: str
    trigger_step_id: str
    issue_types: list[str] = Field(default_factory=list)
    issue_ids: list[str] = Field(default_factory=list)
    initial_audit_decision: str | None = None
    origin_step_ids: list[str] = Field(default_factory=list)
    owner_step_ids: list[str] = Field(default_factory=list)
    location_labels: list[str] = Field(default_factory=list)
    rerun_step_ids: list[str] = Field(default_factory=list)
    preserved_step_ids: list[str] = Field(default_factory=list)
    round: Literal[1] = 1
    status: Literal["planned", "running", "completed", "stopped"]
    final_audit_decision: str | None = None
    before_digest: str | None = Field(default=None, min_length=64, max_length=64)
    after_digest: str | None = Field(default=None, min_length=64, max_length=64)
    preserved_before_digest: str | None = Field(
        default=None, min_length=64, max_length=64
    )
    preserved_after_digest: str | None = Field(
        default=None, min_length=64, max_length=64
    )
    preserved_outputs_unchanged: bool | None = None
    # 这一轮返修是否真的改变了内容。比较抹掉重新生成的标识符与时间戳之后
    # 的内容摘要：确定性节点（如试卷装配）会逐字复现原产物，此时
    # ``after_digest`` 仍然会变（只换了新生成的 id），不能作为返修生效的
    # 证据。``content_changed`` 为 False 表示这轮返修是空转，剩余问题与
    # 返修前完全相同。
    content_changed: bool | None = None
    unchanged_step_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TraceRecorder:
    def __init__(self) -> None:
        self.items: list[StepTrace] = []
        self.tool_items: list[ToolTrace] = []
        self.communication_items: list[CommunicationTrace] = []

    def record(self, step_id: str, agent: str, status: str, attempt: int, error: str | None = None) -> None:
        self.items.append(
            StepTrace(step_id=step_id, agent=agent, status=status, attempt=attempt, error=error)
        )

    def record_tool(self, trace: ToolTrace) -> None:
        self.tool_items.append(trace)

    def record_communication(self, trace: CommunicationTrace) -> None:
        self.communication_items.append(trace)

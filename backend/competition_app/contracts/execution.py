from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import Field, model_validator

from competition_app.contracts.base import ContractModel


# A model-led step must outlive one complete provider request so cancellation
# cleanup, response parsing and contract validation can finish before the
# orchestrator deadline.  The graph remains the outermost backstop.
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 1800.0
STEP_DEADLINE_CLEANUP_MARGIN_SECONDS = 300.0
DEFAULT_MODEL_STEP_TIMEOUT_SECONDS = (
    DEFAULT_PROVIDER_TIMEOUT_SECONDS + STEP_DEADLINE_CLEANUP_MARGIN_SECONDS
)
DEFAULT_GRAPH_TIMEOUT_SECONDS = 7200.0


def model_step_timeout_seconds(provider_timeout_seconds: float) -> float:
    """Return a step deadline with explicit cleanup headroom."""

    timeout = float(provider_timeout_seconds)
    if timeout <= 0:
        raise ValueError("provider timeout must be positive")
    return timeout + STEP_DEADLINE_CLEANUP_MARGIN_SECONDS


class PlanValidationError(ValueError):
    """Raised when an execution plan is not a valid directed acyclic graph."""


class ExecutionStep(ContractModel):
    step_id: str = Field(min_length=1)
    agent: str = Field(min_length=1)
    action: str | None = None
    plan_scope: Literal["long_term", "short_term", "daily_task"] | None = None
    audit_subject: Literal[
        "long_term_plan", "short_term_plan", "resource"
    ] | None = None
    depends_on: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    # 默认步骤预算 900s：并发场景下（多个 SSE 会话同时跑 workflow）事件
    # 循环被 LangGraph checkpoint 的同步 DB 写占用，单步耗时会被显著放大，
    # 600s 的旧默认值会在合法长任务（thinking 模型 + 多轮修订）完成前
    # 被 wait_for 掐断（见 2026-08-16 组卷 paper_assembly workflow_timeout）。
    timeout_seconds: float = Field(default=900.0, gt=0)
    max_retries: int = Field(default=1, ge=0, le=1)

    @property
    def requires_provider_deadline(self) -> bool:
        """Whether this step can invoke the shared chat provider."""

        return self.agent.endswith("_agent") or self.agent == "default_route_resolver"


class ExecutionPlan(ContractModel):
    plan_id: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    steps: list[ExecutionStep] = Field(min_length=1)
    provider_timeout_seconds: float = Field(
        default=DEFAULT_PROVIDER_TIMEOUT_SECONDS,
        gt=0,
    )
    graph_timeout_seconds: float = Field(
        default=DEFAULT_GRAPH_TIMEOUT_SECONDS,
        gt=0,
    )

    @model_validator(mode="after")
    def validate_unique_steps(self) -> "ExecutionPlan":
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("execution plan contains duplicate step ids")
        return self

    def validate_dag(self) -> None:
        step_ids = {step.step_id for step in self.steps}
        for step in self.steps:
            unknown = set(step.depends_on) - step_ids
            if unknown:
                names = ", ".join(sorted(unknown))
                raise PlanValidationError(f"step {step.step_id} has unknown dependency: {names}")
            if step.step_id in step.depends_on:
                raise PlanValidationError(f"execution plan contains cycle at {step.step_id}")
            if step.timeout_seconds >= self.graph_timeout_seconds:
                raise PlanValidationError(
                    f"step {step.step_id} timeout must be below graph timeout"
                )
        self.topological_levels()

    def validate_deadlines(self, *, provider_timeout_seconds: float | None = None) -> None:
        """Validate provider < model step < graph for model-led steps."""

        provider_timeout = float(
            self.provider_timeout_seconds
            if provider_timeout_seconds is None
            else provider_timeout_seconds
        )
        if provider_timeout <= 0:
            raise PlanValidationError("provider timeout must be positive")
        if provider_timeout >= self.graph_timeout_seconds:
            raise PlanValidationError("provider timeout must be below graph timeout")
        for step in self.steps:
            if not step.requires_provider_deadline:
                continue
            if step.timeout_seconds <= provider_timeout:
                raise PlanValidationError(
                    f"model step {step.step_id} timeout must exceed provider timeout"
                )

    def topological_levels(self) -> list[list[str]]:
        order = {step.step_id: index for index, step in enumerate(self.steps)}
        dependencies = {step.step_id: set(step.depends_on) for step in self.steps}
        step_ids = set(dependencies)
        for step_id, required in dependencies.items():
            unknown = required - step_ids
            if unknown:
                names = ", ".join(sorted(unknown))
                raise PlanValidationError(f"step {step_id} has unknown dependency: {names}")

        dependents: dict[str, set[str]] = defaultdict(set)
        for step_id, required in dependencies.items():
            for dependency in required:
                dependents[dependency].add(step_id)

        remaining = {step_id: len(required) for step_id, required in dependencies.items()}
        ready = sorted((step_id for step_id, count in remaining.items() if count == 0), key=order.get)
        levels: list[list[str]] = []
        visited = 0
        while ready:
            level = ready
            levels.append(level)
            visited += len(level)
            next_ready: list[str] = []
            for step_id in level:
                for dependent in dependents[step_id]:
                    remaining[dependent] -= 1
                    if remaining[dependent] == 0:
                        next_ready.append(dependent)
            ready = sorted(next_ready, key=order.get)

        if visited != len(self.steps):
            raise PlanValidationError("execution plan contains cycle")
        return levels

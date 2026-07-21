from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import Field, model_validator

from competition_app.contracts.base import ContractModel


class PlanValidationError(ValueError):
    """Raised when an execution plan is not a valid directed acyclic graph."""


class ExecutionStep(ContractModel):
    step_id: str = Field(min_length=1)
    agent: str = Field(min_length=1)
    action: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=1, ge=0, le=1)


class ExecutionPlan(ContractModel):
    plan_id: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    steps: list[ExecutionStep] = Field(min_length=1)

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
        self.topological_levels()

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

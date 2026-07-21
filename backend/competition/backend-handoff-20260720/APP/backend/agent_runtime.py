from __future__ import annotations

from typing import Any

from APP.backend.agent_contracts import AgentExecutionPlan
from APP.backend.agent_registry import get_agent_definition


def _step_id(step: dict[str, Any], index: int) -> str:
    return str(step.get("id") or f"step_{index + 1}")


def validate_execution_plan(plan: AgentExecutionPlan, available_tools: list[str] | set[str] | tuple[str, ...]) -> dict[str, Any]:
    allowed_tools = set(available_tools)
    seen_step_ids: set[str] = set()

    for index, raw_step in enumerate(plan.steps):
        if not isinstance(raw_step, dict):
            raise ValueError("AgentExecutionPlan steps must be dictionaries for runtime validation")
        step_id = _step_id(raw_step, index)
        agent_name = str(raw_step.get("agent") or "")
        agent = get_agent_definition(agent_name)
        if agent is None:
            raise ValueError(f"Unknown agent: {agent_name}")

        for dependency in raw_step.get("depends_on") or []:
            if dependency not in seen_step_ids:
                raise ValueError(f"Missing dependency: {dependency}")

        for tool_name in raw_step.get("tools") or []:
            if tool_name not in allowed_tools:
                raise ValueError(f"Unauthorized tool for request: {tool_name}")
            if tool_name not in agent.allowed_tools:
                raise ValueError(f"Tool {tool_name} is not allowed for agent {agent_name}")

        seen_step_ids.add(step_id)

    return {"status": "valid", "step_count": len(plan.steps), "task_type": plan.task_type}


def build_runtime_trace(plan: AgentExecutionPlan) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    for index, raw_step in enumerate(plan.steps):
        if not isinstance(raw_step, dict):
            continue
        agent = get_agent_definition(str(raw_step.get("agent") or ""))
        trace.append(
            {
                "step_id": _step_id(raw_step, index),
                "agent": raw_step.get("agent"),
                "action": raw_step.get("action"),
                "tools": list(raw_step.get("tools") or []),
                "depends_on": list(raw_step.get("depends_on") or []),
                "system_prompt": agent.system_prompt if agent else "",
                "status": "planned",
            }
        )
    return trace

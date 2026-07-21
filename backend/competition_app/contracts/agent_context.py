from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import Field

from competition_app.contracts.base import ContractModel
from competition_app.llm.prompt_skills import PromptSkill


class ModelAgentContext(ContractModel):
    """Uniform model boundary: natural-language instructions plus structured data."""

    context_id: str = Field(min_length=1)
    schema_version: str = "1.0.0"
    trace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    workflow_step_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    source_agent: str = Field(min_length=1)
    target_agent: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    task_instructions: str = Field(min_length=1)
    permission_note: str = Field(min_length=1)
    prompt_skill_id: str = Field(min_length=1)
    prompt_skill_version: str = Field(min_length=1)
    created_at: datetime
    payload: dict[str, Any]


def build_model_context(
    context: dict[str, Any],
    *,
    target_agent: str,
    prompt_skill: PromptSkill,
    payload: dict[str, Any],
    permission_note: str,
) -> dict[str, Any]:
    return ModelAgentContext(
        context_id=f"CTX_{uuid4().hex}",
        trace_id=str(context["trace_id"]),
        task_id=str(context.get("workflow_task_id") or context["request_id"]),
        workflow_step_id=str(context.get("step_id") or target_agent),
        user_id=str(context["learner_id"]),
        source_agent="orchestrator",
        target_agent=target_agent,
        purpose=f"执行受控任务 {prompt_skill.skill_id}",
        task_instructions=prompt_skill.instructions,
        permission_note=permission_note,
        prompt_skill_id=prompt_skill.skill_id,
        prompt_skill_version=prompt_skill.version,
        created_at=context.get("now", datetime.now(timezone.utc)),
        payload=payload,
    ).model_dump(mode="json")
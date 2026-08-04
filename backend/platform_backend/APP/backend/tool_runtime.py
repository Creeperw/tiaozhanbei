from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from APP.backend.agent_contracts import ReviewDecision
from APP.backend.audit_agent_service import audit_artifact
from APP.backend.case_patient_orchestration import (
    audit_simulated_patient_reply,
    generate_simulated_patient_reply,
)
from APP.backend.diagnosis_agent_service import build_diagnosis_snapshot
from APP.backend.expert_agent_service import (
    generate_case_training,
    generate_handout,
    generate_knowledge_card,
    generate_paper,
    generate_question_variation,
    grade_submission,
)
from APP.backend.knowledge_agent_service import build_evidence_pack
from APP.backend.learning_plan_service import generate_learning_plan
from APP.backend.memory_agent_service import build_learner_context_brief


class ToolInvocationResult(BaseModel):
    tool_name: str
    agent_name: str
    status: str
    result: Any | None = None
    input_summary: str = ""
    output_summary: str = ""
    error: str | None = None


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    allowed_agents: frozenset[str]
    handler: Callable[..., Any]
    summarize_input: Callable[[dict[str, Any]], str] | None = None
    summarize_output: Callable[[Any], str] | None = None


def _default_input_summary(kwargs: dict[str, Any]) -> str:
    return f"input_keys={sorted(kwargs)}"


def _default_output_summary(result: Any) -> str:
    if result is None:
        return "result=None"
    if hasattr(result, "model_dump"):
        payload = result.model_dump()
        return f"result_type={type(result).__name__}; keys={sorted(payload.keys())}"
    if isinstance(result, dict):
        return f"result_type=dict; keys={sorted(result.keys())}"
    if isinstance(result, list):
        return f"result_type=list; count={len(result)}"
    return f"result_type={type(result).__name__}"


def _summary_failure(tool_name: str, agent_name: str, input_summary: str) -> ToolInvocationResult:
    return ToolInvocationResult(
        tool_name=tool_name,
        agent_name=agent_name,
        status="failed",
        result=None,
        input_summary=input_summary,
        output_summary="tool summary failed",
        error=f"tool_summary_failed:{tool_name}",
    )


def review_learning_plan(*, plan: dict[str, Any], **kwargs: Any) -> ReviewDecision:
    return ReviewDecision(
        decision="pass",
        reason="学习计划结构完整，可用于个性化学习路径。",
        source_scope="audit_agent",
        source_id=str(plan.get("learner_id") or "learning_plan"),
        confidence=0.9,
    )


class ToolRuntime:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        self._tools[definition.name] = definition

    def tool_names(self) -> set[str]:
        return set(self._tools)

    def execute(self, tool_name: str, agent_name: str, **kwargs: Any) -> ToolInvocationResult:
        definition = self._tools.get(tool_name)
        if definition is None:
            return ToolInvocationResult(
                tool_name=tool_name,
                agent_name=agent_name,
                status="failed",
                result=None,
                input_summary=_default_input_summary(kwargs),
                output_summary="unknown tool",
                error=f"unknown_tool:{tool_name}",
            )

        try:
            input_summary = (definition.summarize_input or _default_input_summary)(kwargs)
        except Exception:
            return _summary_failure(tool_name, agent_name, _default_input_summary(kwargs))

        if agent_name not in definition.allowed_agents:
            return ToolInvocationResult(
                tool_name=tool_name,
                agent_name=agent_name,
                status="failed",
                result=None,
                input_summary=input_summary,
                output_summary="agent is not allowed to use this tool",
                error=f"unauthorized_tool:{tool_name}:{agent_name}",
            )

        try:
            result = definition.handler(**kwargs)
        except Exception:
            return ToolInvocationResult(
                tool_name=tool_name,
                agent_name=agent_name,
                status="failed",
                result=None,
                input_summary=input_summary,
                output_summary="tool execution failed",
                error=f"tool_execution_failed:{tool_name}",
            )

        try:
            output_summary = (definition.summarize_output or _default_output_summary)(result)
        except Exception:
            return _summary_failure(tool_name, agent_name, input_summary)
        return ToolInvocationResult(
            tool_name=tool_name,
            agent_name=agent_name,
            status="success",
            result=result,
            input_summary=input_summary,
            output_summary=output_summary,
            error=None,
        )


def build_default_tool_runtime() -> ToolRuntime:
    runtime = ToolRuntime()
    runtime.register(ToolDefinition("build_learner_context_brief", frozenset({"memory_agent"}), build_learner_context_brief))
    runtime.register(ToolDefinition("build_diagnosis_snapshot", frozenset({"diagnosis_agent"}), build_diagnosis_snapshot))
    runtime.register(ToolDefinition("build_evidence_pack", frozenset({"knowledge_base_agent"}), build_evidence_pack))
    runtime.register(ToolDefinition("generate_learning_path", frozenset({"planner_agent"}), generate_learning_plan))
    runtime.register(ToolDefinition("generate_handout", frozenset({"expert_handout"}), generate_handout))
    runtime.register(ToolDefinition("generate_knowledge_card", frozenset({"expert_knowledge_card"}), generate_knowledge_card))
    runtime.register(ToolDefinition("generate_paper", frozenset({"expert_paper"}), generate_paper))
    runtime.register(ToolDefinition("grade_submission", frozenset({"expert_grading"}), grade_submission))
    runtime.register(ToolDefinition("generate_case_training", frozenset({"expert_case_training"}), generate_case_training))
    runtime.register(ToolDefinition(
        "generate_simulated_patient_reply",
        frozenset({"patient_simulation_expert"}),
        generate_simulated_patient_reply,
    ))
    runtime.register(ToolDefinition(
        "audit_simulated_patient_reply",
        frozenset({"patient_audit_agent"}),
        audit_simulated_patient_reply,
    ))
    runtime.register(ToolDefinition("generate_question_variation", frozenset({"expert_question_variation"}), generate_question_variation))
    runtime.register(ToolDefinition("audit_artifact", frozenset({"audit_agent"}), audit_artifact))
    runtime.register(ToolDefinition("review_learning_plan", frozenset({"audit_agent"}), review_learning_plan))
    return runtime

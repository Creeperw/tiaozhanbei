from __future__ import annotations

from typing import Any
from uuid import uuid4

from competition_app.contracts.base import AgentEnvelope, ArtifactReference


def envelope(context: dict[str, Any], producer: str, artifact_type: str, payload: Any) -> AgentEnvelope[Any]:
    dependency_outputs = context.get("dependency_outputs", {})
    input_refs = [
        ArtifactReference(
            ref_type=str(value.artifact_type),
            ref_id=str(value.artifact_id),
            purpose=f"dependency:{step_id}",
        )
        for step_id, value in dependency_outputs.items()
        if isinstance(value, AgentEnvelope)
    ]
    result = AgentEnvelope[Any](
        artifact_id=f"ART_{uuid4().hex}",
        artifact_type=artifact_type,
        case_id=str(context["case_id"]),
        trace_id=str(context["trace_id"]),
        request_id=str(context["request_id"]),
        execution_id=str(context["execution_id"]),
        step_id=str(context["step_id"]),
        producer=producer,
        task_type=str(context.get("task_type", "unknown")),
        learner_id=str(context["learner_id"]),
        payload=payload,
        input_refs=input_refs,
    )
    terminal_trace = context.get("terminal_trace")
    if terminal_trace:
        terminal_trace.system_output(producer, result.payload)
    return result

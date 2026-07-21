from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import Field

from competition_app.contracts.base import AgentEnvelope, ArtifactReference, ContractModel
from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.memory import (
    ConversationContextSummary,
    LearnerContextBrief,
    LongTermMemoryCandidate,
)
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.schemas import MemoryModelOutput, validate_training_style_output


class MemoryAgentResult(ContractModel):
    context_summary: ConversationContextSummary | None = None
    learner_context: LearnerContextBrief
    memory_candidates: list[LongTermMemoryCandidate] = Field(default_factory=list)


class MemoryAgent:
    def __init__(self, chat_model: ChatModel, compression_threshold_chars: int = 4_000) -> None:
        self.chat_model = chat_model
        self.compression_threshold_chars = compression_threshold_chars

    async def run(self, context: dict[str, Any]) -> AgentEnvelope[MemoryAgentResult]:
        messages = list(context.get("messages", []))
        learner_id = str(context["learner_id"])
        allowed_roles = {"user", "assistant", "system", "tool"}
        for index, item in enumerate(messages):
            if not item.get("message_id"):
                raise ValueError(f"message_id is required for context message {index}")
            if item.get("role") not in allowed_roles:
                raise ValueError(f"unsupported conversation role: {item.get('role')}")
            message_learner = item.get("learner_id")
            if message_learner is not None and str(message_learner) != learner_id:
                raise ValueError("conversation message learner does not match current learner")
        source_refs = [
            ArtifactReference(ref_type="conversation_message", ref_id=item["message_id"])
            for item in messages
        ]
        total_chars = sum(len(str(item.get("content", ""))) for item in messages)
        should_compress = bool(context.get("force_context_compression")) or (
            total_chars > self.compression_threshold_chars
        )
        if not should_compress:
            raise ValueError("memory agent must only run after the compression threshold is exceeded")
        prompt_skill = prompt_skill_registry.load("memory_agent", "conversation_compression")
        try:
            model_output = validate_training_style_output(
                MemoryModelOutput,
                await self.chat_model.complete_json(
                    "memory_agent",
                    build_model_context(
                        context,
                        target_agent="memory_agent",
                        prompt_skill=prompt_skill,
                        payload={
                            "user_profile": {
                                "user_preference": context.get("profile", {}).get(
                                    "confirmed_preferences", {}
                                )
                            },
                            "messages": [
                                {"role": item["role"], "content": item.get("content", "")}
                                for item in messages
                            ],
                            "temporary_constraints": context.get("temporary_constraints", []),
                            "expected_uncertainty": [],
                            "output_schema": MemoryModelOutput.model_json_schema(),
                        },
                        permission_note="只处理当前会话、已确认偏好和临时约束；不得生成掌握度、计划或知识库事实。",
                    ),
                ),
                [],
            )
        except ValueError as exc:
            if context.get("terminal_trace"):
                context["terminal_trace"].validation("memory_agent", valid=False, detail=str(exc))
            raise
        if context.get("terminal_trace"):
            context["terminal_trace"].validation("memory_agent", valid=True, detail="MemoryModelOutput")
        artifact_id = f"MEMCTX_{uuid4().hex}"
        summary = None
        if should_compress and source_refs:
            summary = ConversationContextSummary(
                summary=model_output.summary,
                source_refs=source_refs,
                preserved_facts=model_output.preserved_facts,
                unresolved_questions=model_output.unresolved_questions,
                temporary_constraints=model_output.temporary_constraints,
            )

        profile = dict(context.get("profile", {}))
        learner_context = LearnerContextBrief(
            learner_id=learner_id,
            confirmed_preferences=dict(profile.get("confirmed_preferences", {})),
            relevant_memories=list(context.get("confirmed_memories", [])),
            temporary_constraints=list(context.get("temporary_constraints", [])),
            context_summary_ref=(
                ArtifactReference(ref_type="conversation_context_summary", ref_id=artifact_id)
                if summary is not None
                else None
            ),
        )
        memory_candidates = [
            LongTermMemoryCandidate(summary=item, source_refs=source_refs)
            for item in model_output.memory_candidates
            if source_refs
        ]
        payload = MemoryAgentResult(
            context_summary=summary,
            learner_context=learner_context,
            memory_candidates=memory_candidates,
        )
        return AgentEnvelope[MemoryAgentResult](
            artifact_id=artifact_id,
            artifact_type="memory_agent_result",
            case_id=str(context["case_id"]),
            trace_id=str(context["trace_id"]),
            request_id=str(context["request_id"]),
            execution_id=str(context["execution_id"]),
            step_id=str(context["step_id"]),
            producer="memory_agent",
            task_type="build_learner_context",
            learner_id=learner_id,
            payload=payload,
            input_refs=source_refs,
            confidence=0.9,
        )

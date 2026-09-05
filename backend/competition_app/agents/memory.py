from __future__ import annotations

import asyncio
import re
from typing import Any
from uuid import uuid4

from pydantic import Field

from competition_app.contracts.base import AgentEnvelope, ArtifactReference, ContractModel
from competition_app.contracts.agent_context import build_model_context
from competition_app.services.conversation_history import (
    parse_compressed_dialogue_summary,
    sanitize_compressed_dialogue_summary,
    sanitize_conversation_messages,
)
from competition_app.contracts.memory import (
    ConversationContextSummary,
    LearnerContextBrief,
    LongTermMemoryCandidate,
    MemoryConflict,
    MemoryGovernanceDecision,
)
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.schemas import (
    MemoryGovernanceModelOutput,
    MemoryModelOutput,
    validate_training_style_output,
)


class MemoryAgentResult(ContractModel):
    context_summary: ConversationContextSummary | None = None
    learner_context: LearnerContextBrief
    memory_candidates: list[LongTermMemoryCandidate] = Field(default_factory=list)
    auto_confirm_memories: list[LongTermMemoryCandidate] = Field(default_factory=list)
    governance: MemoryGovernanceDecision | None = None

    @property
    def requires_clarification(self) -> bool:
        return bool(self.governance and self.governance.requires_clarification)

    @property
    def clarification_questions(self) -> list[str]:
        return (
            list(self.governance.clarification_questions)
            if self.governance
            else []
        )

    @property
    def interrupt_type(self) -> str | None:
        return self.governance.interrupt_type if self.governance else None

    @property
    def reason(self) -> str | None:
        return self.governance.analysis if self.governance else None


class MemoryAgent:
    def __init__(self, chat_model: ChatModel, compression_threshold_chars: int = 4_000) -> None:
        self.chat_model = chat_model
        # Kept as a compatibility argument for older callers.  Compression is
        # never decided here: the application computes the authoritative
        # threshold fact and passes it as ``memory_required``.
        self.compression_threshold_chars = compression_threshold_chars

    async def run(self, context: dict[str, Any]) -> AgentEnvelope[MemoryAgentResult]:
        raw_messages = list(context.get("messages", []))
        learner_id = str(context["learner_id"])
        for index, item in enumerate(raw_messages):
            if not item.get("message_id"):
                raise ValueError(f"message_id is required for context message {index}")
            if item.get("role") not in {"user", "assistant"}:
                raise ValueError(f"unsupported conversation role: {item.get('role')}")
            message_learner = item.get("learner_id")
            if message_learner is not None and str(message_learner) != learner_id:
                raise ValueError("conversation message learner does not match current learner")
        messages = sanitize_conversation_messages(raw_messages)
        source_refs = [
            ArtifactReference(ref_type="conversation_message", ref_id=item["message_id"])
            for item in messages
        ]
        if context.get("smart_paper_v2") is True:
            # The workshop request is generated from a validated form and is a
            # one-off operation, not a durable user fact. Memory still runs so
            # downstream work receives the current exam's confirmed profile
            # and retrieved memories, but a model is not asked to reinterpret
            # free-text topic data or manufacture a memory write decision.
            artifact_id = f"MEMCTX_{uuid4().hex}"
            profile = dict(context.get("profile", {}))
            payload = MemoryAgentResult(
                learner_context=LearnerContextBrief(
                    learner_id=learner_id,
                    confirmed_preferences=dict(
                        profile.get("confirmed_preferences", {})
                    ),
                    relevant_memories=list(context.get("confirmed_memories", [])),
                    temporary_constraints=list(
                        context.get("temporary_constraints", [])
                    ),
                    context_summary_ref=None,
                ),
                governance=MemoryGovernanceDecision(
                    analysis=(
                        "已读取当前考试下的用户画像与相关长期记忆；"
                        "本轮结构化组卷请求属于一次性操作，不新增或改写长期记忆。"
                    ),
                    resolution="none",
                ),
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
                task_type="govern_learning_memory",
                learner_id=learner_id,
                payload=payload,
                input_refs=source_refs,
                confidence=1.0,
            )
        # Compression is a system-owned concern.  The fixed threshold is
        # evaluated once by the application before orchestration starts.  The
        # Memory Agent always performs governance below; this flag controls
        # only its optional compression sub-step and must not be inferred from
        # message length or user wording here.
        should_compress = bool(context.get("memory_required", False))
        summary = None
        compression_candidates: list[str] = []
        compression_task = (
            asyncio.create_task(
                self._compress_context(context, messages, source_refs)
            )
            if should_compress
            else None
        )
        governance_skill = prompt_skill_registry.load(
            "memory_agent", "learning_memory_governance"
        )
        try:
            model_context = build_model_context(
                context,
                target_agent="memory_agent",
                prompt_skill=governance_skill,
                payload={
                    "current_user_request": context.get("user_request", ""),
                    "current_user_message": next(
                        (
                            item.get("content", "")
                            for item in reversed(messages)
                            if item.get("role") == "user"
                        ),
                        "",
                    ),
                    "relevant_memories": context.get(
                        "relevant_personalization_memories", []
                    ),
                    "memory_interpretation_rules": [
                        "字段缺失、空数组、未填写或暂无记录只表示没有证据，不表示相反事实。",
                        "用户本轮明确陈述并用于当前规划的学习事实，应直接作为当前事实传递；只有旧记忆明确记录相反事实才构成冲突。",
                    ],
                    "retrieval_degraded": bool(
                        context.get("memory_retrieval_degraded")
                    ),
                    "memory_conflict_answer": context.get(
                        "memory_conflict_answer"
                    ),
                    "temporary_constraints": context.get("temporary_constraints", []),
                    "current_exam_workspace": {
                        "exam_track_id": str(
                            context.get("exam_scope_id")
                            or (context.get("learning_target") or {}).get(
                                "exam_track_id"
                            )
                            or ""
                        ),
                        "exam_name": str(
                            (context.get("learning_target") or {}).get("exam_name")
                            or ""
                        ),
                        "usage_policy": (
                            "这是系统锁定的当前考试工作区；其他考试目标记忆属于并行工作区，"
                            "不得据此中断当前考试流程，也不得要求替换全局考试目标。"
                        ),
                    },
                    "output_schema": MemoryGovernanceModelOutput.model_json_schema(),
                },
                permission_note=(
                    "只提取用户明确陈述并判断相关记忆是否真正冲突；"
                    "空字段或缺失记录不等于相反事实；用户明确陈述并要求据此规划时，"
                    "除非旧记忆明确记录相反事实，不要为二次确认而阻断流程；"
                    "不得覆盖记忆、写画像、生成计划或通过关键词直接下结论。"
                    "系统已锁定当前考试时，其他考试目标记忆属于并行工作区，"
                    "不得把它与当前考试判成需要追问的记忆冲突。"
                ),
            )
            # Validate inside the model call so that a business-schema
            # mismatch (e.g. memory_candidates containing objects instead of
            # strings) triggers the failover candidate instead of failing the
            # whole workflow.
            model_context["_result_validator"] = lambda result: validate_training_style_output(
                MemoryGovernanceModelOutput,
                self._normalize_unsupported_clarification(result),
                [],
            ).model_dump(mode="json")
            raw_output = await self.chat_model.complete_json(
                "memory_agent",
                model_context,
            )
            governance_output = validate_training_style_output(
                MemoryGovernanceModelOutput,
                self._normalize_unsupported_clarification(raw_output),
                [],
            )
            governance_output = self._apply_exam_workspace_policy(
                context, governance_output
            )
        except BaseException as exc:
            if compression_task is not None:
                compression_task.cancel()
                await asyncio.gather(compression_task, return_exceptions=True)
            if isinstance(exc, ValueError) and context.get("terminal_trace"):
                context["terminal_trace"].validation("memory_agent", valid=False, detail=str(exc))
            raise
        if compression_task is not None:
            try:
                summary, compression_candidates = await compression_task
            except BaseException as exc:
                # Compression is an optional side-channel: a model digest that
                # does not keep the pure user/assistant dialogue format (or any
                # other transient failure) must never fail the whole workflow.
                # Degrade to no summary for this turn.
                summary = None
                compression_candidates = []
                if context.get("terminal_trace"):
                    context["terminal_trace"].validation(
                        "memory_agent",
                        valid=False,
                        detail=f"conversation compression degraded: {exc}",
                    )
        if context.get("terminal_trace"):
            context["terminal_trace"].validation(
                "memory_agent", valid=True, detail="MemoryGovernanceModelOutput"
            )
        artifact_id = f"MEMCTX_{uuid4().hex}"

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
            for item in dict.fromkeys(
                [*compression_candidates, *governance_output.memory_candidates]
            )
            if source_refs
        ]
        auto_confirm_memories = [
            LongTermMemoryCandidate(
                summary=item,
                source_refs=source_refs,
                status="auto_confirmed",
            )
            for item in dict.fromkeys(governance_output.auto_confirm_candidates)
            if source_refs
        ]
        valid_memory_ids = {
            int(item["id"])
            for item in context.get("relevant_personalization_memories", [])
            if isinstance(item, dict) and isinstance(item.get("id"), int)
        }
        unknown_conflicts = [
            item.memory_id
            for item in governance_output.conflicts
            if item.memory_id not in valid_memory_ids
        ]
        if unknown_conflicts:
            raise ValueError("memory governance referenced an unknown memory id")
        governance = MemoryGovernanceDecision(
            analysis=governance_output.governance_notes,
            memory_candidates=memory_candidates,
            auto_confirm_memories=auto_confirm_memories,
            conflicts=[
                MemoryConflict(
                    memory_id=item.memory_id,
                    proposed_memory=item.proposed_memory,
                    reason=item.reason,
                )
                for item in governance_output.conflicts
            ],
            requires_clarification=governance_output.requires_clarification,
            clarification_questions=governance_output.clarification_questions,
            interrupt_type=(
                "memory_conflict"
                if governance_output.requires_clarification
                else None
            ),
            resolution=governance_output.resolution,
        )
        payload = MemoryAgentResult(
            context_summary=summary,
            learner_context=learner_context,
            memory_candidates=memory_candidates,
            auto_confirm_memories=auto_confirm_memories,
            governance=governance,
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
            task_type="govern_learning_memory",
            learner_id=learner_id,
            payload=payload,
            input_refs=source_refs,
            confidence=0.9,
        )

    @staticmethod
    def _normalize_unsupported_clarification(
        value: dict[str, Any],
    ) -> dict[str, Any]:
        """Fail open only when the model provides no conflict to clarify.

        A clarification checkpoint is allowed only when the model identifies a
        concrete existing-memory conflict and provides a question.  Some Live
        providers occasionally toggle ``requires_clarification`` without any
        conflict or question.  That output cannot justify interrupting the
        learner, and retrying the same provider can repeat the malformed flag
        and fail an otherwise independent workflow.  Normalize only this
        evidence-free state to the safe no-conflict decision; real conflicts
        with malformed or missing questions still fail strict validation.
        """

        if not isinstance(value, dict):
            return value
        if not bool(value.get("requires_clarification")):
            return value
        if list(value.get("conflicts") or []):
            return value
        normalized = dict(value)
        notes = str(normalized.get("governance_notes") or "").rstrip()
        boundary_note = "没有可定位冲突证据，本轮不触发记忆澄清。"
        normalized["governance_notes"] = (
            f"{notes} {boundary_note}".strip()[:3_000]
        )
        normalized["requires_clarification"] = False
        normalized["clarification_questions"] = []
        normalized["resolution"] = "none"
        return normalized

    @staticmethod
    def _apply_exam_workspace_policy(
        context: dict[str, Any],
        output: MemoryGovernanceModelOutput,
    ) -> MemoryGovernanceModelOutput:
        """Prevent another certificate's memory from blocking this workspace.

        Exam IDs are stable system namespace identifiers, not semantic
        keywords.  When every reported conflict points to a memory explicitly
        scoped to a different exam ID, the records can coexist and no memory
        replacement question is valid.  Ambiguous/global memories and mixed
        conflict sets are left to the model/user rather than guessed here.
        """

        current_exam_id = str(
            context.get("exam_scope_id")
            or (context.get("learning_target") or {}).get("exam_track_id")
            or ""
        ).strip()
        if not current_exam_id or not output.conflicts:
            return output
        memories = {
            int(item["id"]): item
            for item in context.get("relevant_personalization_memories", [])
            if isinstance(item, dict) and isinstance(item.get("id"), int)
        }

        def belongs_to_other_exam(memory_id: int) -> bool:
            item = memories.get(memory_id) or {}
            searchable = "\n".join(
                str(item.get(key) or "")
                for key in ("category", "title", "content")
            )
            exam_ids = set(re.findall(r"EXAM_[A-Z0-9_]+", searchable.upper()))
            return bool(exam_ids) and current_exam_id.upper() not in exam_ids

        if not all(belongs_to_other_exam(item.memory_id) for item in output.conflicts):
            return output
        return output.model_copy(
            update={
                "governance_notes": (
                    str(output.governance_notes).rstrip()
                    + " 系统已按当前考试工作区隔离其他考试目标记忆；"
                    "这些记录可并存，不构成本轮冲突。"
                )[:3_000],
                "conflicts": [],
                "requires_clarification": False,
                "clarification_questions": [],
                "resolution": "none",
            }
        )

    async def _compress_context(
        self,
        context: dict[str, Any],
        messages: list[dict[str, Any]],
        source_refs: list[ArtifactReference],
    ) -> tuple[ConversationContextSummary | None, list[str]]:
        """Compress independently from user-fact extraction/governance."""

        prompt_skill = prompt_skill_registry.load(
            "memory_agent", "conversation_compression"
        )
        try:
            # Incremental compression: when a durable summary already exists,
            # only the messages that it did not cover need to be compressed.
            # The previous digest is parsed back into pure user/assistant
            # messages so the model folds the new dialogue into the existing
            # digest instead of re-reading the entire history.  Historical
            # dialogue never carries system prefixes, evidence or any other
            # non-formal content.
            covered_message_ids = set(
                str(item)
                for item in context.get(
                    "compressed_conversation_covered_message_ids"
                ) or []
                if str(item).strip()
            )
            existing_summary = sanitize_compressed_dialogue_summary(
                context.get("compressed_conversation_summary")
            )
            if existing_summary and covered_message_ids:
                compression_input = [
                    *parse_compressed_dialogue_summary(existing_summary),
                    *[
                        {"role": item["role"], "content": item.get("content", "")}
                        for item in messages
                        if str(item.get("message_id", "")) not in covered_message_ids
                    ],
                ]
            else:
                compression_input = [
                    {"role": item["role"], "content": item.get("content", "")}
                    for item in messages
                ]
            raw_output = await self.chat_model.complete_json(
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
                        "messages": compression_input,
                        "temporary_constraints": context.get("temporary_constraints", []),
                        "expected_uncertainty": [],
                        "output_schema": MemoryModelOutput.model_json_schema(),
                    },
                    permission_note=(
                        "只压缩当前会话、已确认偏好和临时约束；"
                        "不得生成掌握度、计划或知识库事实。"
                    ),
                ),
            )
            if isinstance(raw_output, dict) and isinstance(raw_output.get("summary"), str):
                raw_output = {
                    **raw_output,
                    "summary": sanitize_compressed_dialogue_summary(
                        raw_output["summary"]
                    ),
                }
            model_output = validate_training_style_output(
                MemoryModelOutput,
                raw_output,
                [],
            )
        except ValueError as exc:
            if context.get("terminal_trace"):
                context["terminal_trace"].validation(
                    "memory_agent", valid=False, detail=str(exc)
                )
            raise
        summary = (
            ConversationContextSummary(
                summary=model_output.summary,
                source_refs=source_refs,
                preserved_facts=model_output.preserved_facts,
                unresolved_questions=model_output.unresolved_questions,
                temporary_constraints=model_output.temporary_constraints,
            )
            if source_refs
            else None
        )
        return summary, list(model_output.memory_candidates)

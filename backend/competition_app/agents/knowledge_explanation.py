from __future__ import annotations

import ast
import re
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from competition_app.agents.common import envelope
from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.resource import (
    QuestionConsumptionDecision,
    ResourceClaim,
    ResourceDraft,
)
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.schemas import KnowledgeExplanationModelOutput
from competition_app.llm.stub import StubChatModel


class KnowledgeExplanationAgent:
    """Expert task: explain retrieved domain knowledge without planning or scheduling."""

    def __init__(self, chat_model: ChatModel | None = None) -> None:
        self.chat_model = chat_model or StubChatModel()

    async def run(self, context: dict[str, Any]) -> AgentEnvelope[ResourceDraft]:
        evidence_pack = context["dependency_outputs"]["knowledge"].payload
        if not evidence_pack.evidence_items:
            raise ValueError("knowledge explanation requires textbook evidence")
        skill = prompt_skill_registry.load("expert_agent", "knowledge_explanation")
        preferences = context.get("user_profile", {}).get("user_preference", {})
        semantic_evidence = [
            {
                "text": item.content_summary,
                "authority": item.authority_level,
                "resource_type": item.resource_type,
                "source_url": item.source_url,
            }
            for item in evidence_pack.evidence_items
        ]
        retrieval_summary = str(getattr(evidence_pack, "retrieval_summary", "")).strip()
        memory_output = context.get("dependency_outputs", {}).get("memory")
        memory_payload = getattr(memory_output, "payload", None)
        context_summary = getattr(memory_payload, "context_summary", None)
        compressed_summary = str(getattr(context_summary, "summary", "") or "").strip()
        conversation_messages = list(context.get("messages", []))
        recent_messages = conversation_messages[-1:] if compressed_summary else conversation_messages[-8:]
        try:
            raw_output = await self.chat_model.complete_json(
                    "expert_agent",
                    build_model_context(
                        context,
                        target_agent="expert_agent",
                        prompt_skill=skill,
                        payload={
                            "phase": "knowledge_explanation",
                            "user_request": context.get("user_request", ""),
                            "recent_conversation": [
                                {
                                    "role": str(item.get("role", "")),
                                    "content": str(item.get("content", "")),
                                }
                                for item in recent_messages
                                if isinstance(item, dict) and str(item.get("content", "")).strip()
                            ],
                            "compressed_conversation_summary": compressed_summary,
                            "user_preference": preferences,
                            "topic": evidence_pack.query,
                            "retrieval_summary": retrieval_summary,
                            "semantic_evidence": semantic_evidence if not retrieval_summary else [],
                            "audit_feedback": list(
                                getattr(
                                    getattr(
                                        context.get("audit_feedback"),
                                        "payload",
                                        context.get("audit_feedback"),
                                    ),
                                    "findings",
                                    [],
                                )
                            ),
                            "output_contract": {
                                "content": "直接输出完整自然语言讲解正文。",
                                "title": "可选标题。",
                                "uncertainty": "可选待确认内容。",
                            },
                        },
                        permission_note=(
                            "结合最近对话解析当前问题中的指代，优先依据教材和网络来源生成教学讲解；覆盖不足时允许使用明确标注的"
                            "模型自身知识。不得伪造引用，不得生成现实诊断、处方或剂量建议。"
                        ),
                    ),
                )
            if not isinstance(raw_output, dict):
                raw_output = {}
            uncertainty = self._normalize_uncertainty(
                raw_output.get("uncertainty", raw_output.get("notes", []))
            )
            body = (
                raw_output.get("explanation_content")
                or raw_output.get("explanation")
                or raw_output.get("content")
                or raw_output.get("body")
                or retrieval_summary
            )
            output = KnowledgeExplanationModelOutput.model_validate(
                {
                    "title": raw_output.get("title") or f"{evidence_pack.query}知识讲解",
                    "explanation_content": body,
                    "uncertainty": uncertainty,
                }
            )
        except ValidationError as exc:
            fallback = retrieval_summary or "\n".join(
                item.content_summary for item in evidence_pack.evidence_items[:3]
            )
            output = KnowledgeExplanationModelOutput(
                title=f"{evidence_pack.query}知识讲解",
                explanation_content=fallback,
                uncertainty=["模型讲解格式不可用，系统展示检索总结。"],
            )
        primary = evidence_pack.evidence_items[0]
        content: dict[str, object] = {"知识讲解": output.explanation_content}
        selected_questions = [
            item
            for item in evidence_pack._question_details
            if self._is_safe_practice_question(item.question_type)
        ][:3]
        if selected_questions:
            content["配套练习"] = [
                {
                    "题型": item.question_type,
                    "题目": item.stem,
                    "选项": self._learner_options(item.options),
                }
                for item in selected_questions
            ]
        else:
            # A knowledge explanation should still end with an actionable
            # self-check when the formal question index has no usable match.
            content["配套练习"] = [
                {
                    "题型": "简答题",
                    "题目": f"请用自己的话概括“{evidence_pack.query}”的核心结论，并说明判断依据。",
                    "选项": [],
                },
                {
                    "题型": "辨析题",
                    "题目": "请指出本次讲解中最容易混淆的两个概念，并说明它们的区别。",
                    "选项": [],
                },
            ]
        if output.uncertainty:
            content["待确认项"] = output.uncertainty
        selected_question_ids = [item.question_id for item in selected_questions]
        draft = ResourceDraft(
            resource_draft_id=f"DRAFT_{uuid4().hex}",
            title=output.title,
            content=content,
            target_difficulty=1,
            estimated_minutes=int(context.get("available_minutes", 15)),
            claims=[
                ResourceClaim(
                    claim_id=f"C_{uuid4().hex}",
                    text=primary.content_summary,
                    evidence_ids=[primary.evidence_id],
                )
            ],
            safety_notes=evidence_pack.risk_notes
            or ["仅用于中医药教学，不构成现实诊疗建议。"],
            question_consumption=QuestionConsumptionDecision(
                use_question_candidates=bool(selected_question_ids),
                usage_reason=(
                    "系统从本次检索到的正式候选题中选择配套练习。"
                    if selected_question_ids
                    else "正式题库未检索到安全候选，系统提供不含答案的开放式自测题。"
                ),
                selected_question_ids=selected_question_ids,
                resource_type="practice",
            ),
        )
        return envelope(context, "expert_agent", "knowledge_explanation", draft)

    @staticmethod
    def _normalize_uncertainty(value: Any) -> list[str]:
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, list):
            values = value
        else:
            return []
        placeholders = {
            "待确认",
            "待确认项",
            "暂无",
            "无",
            "没有",
            "无待确认项",
            "暂无待确认项",
            "暂无待确认内容",
            "none",
            "na",
            "null",
        }
        normalized: list[str] = []
        for item in values:
            if not isinstance(item, str):
                continue
            text = " ".join(item.split()).strip()
            token = re.sub(r"[\s，。；：:、.!！?？_\-/]+", "", text).lower()
            if not token or token in placeholders:
                continue
            if text not in normalized:
                normalized.append(text)
        return normalized

    @staticmethod
    def _is_safe_practice_question(question_type: str) -> bool:
        normalized = str(question_type).replace(" ", "")
        return normalized in {
            "单选题",
            "单项选择题",
            "多选题",
            "多项选择题",
            "判断题",
        }

    @staticmethod
    def _learner_options(options: list[str]) -> list[str]:
        normalized: list[str] = []
        for option in options:
            value: object = option
            if isinstance(option, str) and option.strip().startswith("{"):
                try:
                    value = ast.literal_eval(option)
                except (SyntaxError, ValueError):
                    value = option
            if isinstance(value, dict):
                label = str(value.get("option_id") or value.get("label") or "").strip()
                content = str(value.get("content") or value.get("text") or "").strip()
                if not content:
                    continue
                text = f"{label}. {content}" if label else content
            else:
                text = str(value).strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

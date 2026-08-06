from __future__ import annotations

import json
import re
from datetime import date
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
from competition_app.contracts.audit_policy import ResourceProvenance
from competition_app.contracts.knowledge import EvidenceItem
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.schemas import KnowledgeExplanationModelOutput
from competition_app.llm.stub import StubChatModel
from competition_app.services.conversation_history import (
    sanitize_compressed_dialogue_summary,
)


def build_reference_card_markup(
    evidence_items: list[EvidenceItem],
    evidence_refs: list[str],
) -> str:
    """Deterministically build the trailing ``<<REFS:JSON>>`` citation card.

    Only evidence ids present in ``evidence_items`` are honored (order
    preserved, deduplicated); unknown ids are dropped silently so a model can
    never inject a fabricated source.  Textbook evidence maps to the ``rag``
    badge (book · section label), web/video evidence to ``web`` with a clickable
    url.  Returns an empty string when nothing can be cited.
    """
    by_id = {item.evidence_id: item for item in evidence_items}
    references: list[dict[str, object]] = []
    seen: set[str] = set()
    for ref_id in evidence_refs:
        ref_id = str(ref_id).strip()
        if not ref_id or ref_id in seen or ref_id not in by_id:
            continue
        seen.add(ref_id)
        item = by_id[ref_id]
        is_textbook = (
            item.resource_type == "textbook"
            or item.authority_level == "textbook"
        )
        title = (item.source_label or "").strip() or (
            "教材来源" if is_textbook else f"{item.resource_type}来源"
        )
        references.append(
            {
                "type": "rag" if is_textbook else "web",
                "title": title,
                "url": item.source_url or "",
                "evidence_id": item.evidence_id,
            }
        )
    if not references:
        return ""
    return f"<<REFS:{json.dumps(references, ensure_ascii=False)}>>"


class KnowledgeExplanationAgent:
    """Expert task: explain retrieved domain knowledge without planning or scheduling."""

    def __init__(self, chat_model: ChatModel | None = None) -> None:
        self.chat_model = chat_model or StubChatModel()

    async def run(self, context: dict[str, Any]) -> AgentEnvelope[ResourceDraft]:
        evidence_pack = context["dependency_outputs"]["knowledge"].payload
        if not evidence_pack.evidence_items:
            raise ValueError("knowledge explanation requires textbook evidence")
        task_type = str(context.get("task_type") or "knowledge_explanation")
        user_request = str(context.get("user_request") or "")
        external_information_request = bool(
            context.get("external_information_request")
            or any(
                marker in user_request.lower()
                for marker in (
                    "天气", "气温", "降雨", "下雨", "空气质量", "台风",
                    "距离下次", "考试时间", "考试日期", "什么时候考试",
                    "报名时间", "截止日期", "日程", "赛程", "最新消息",
                    "当前时间", "今天几号", "现在几点",
                )
            )
        )
        current_date = date.today().isoformat()
        question_explanation_request = bool(
            context.get("question_explanation_request")
            or any(
                marker in "".join(user_request.split())
                for marker in (
                    "试述", "简述", "论述", "分析题", "这题", "这道题", "题目",
                    "难", "不会", "不懂", "卡住", "看不懂", "怎么答", "答不出来",
                )
            )
        )
        skill_name = (
            "general_learning_support"
            if task_type == "general_learning_support"
            else "question_explanation"
            if question_explanation_request
            else "knowledge_explanation"
        )
        skill = prompt_skill_registry.load("expert_agent", skill_name)
        flexible_support = task_type == "general_learning_support"
        preferences = context.get("user_profile", {}).get("user_preference", {})
        # Full evidence bodies when no summary exists; otherwise the summary
        # carries the retrieval content and only a compact citation manifest
        # (id + short label) is offered so the model can still declare
        # evidence_refs for the trailing reference card without duplicating
        # the whole evidence set in the prompt.
        semantic_evidence = [
            {
                "evidence_id": item.evidence_id,
                "text": item.content_summary,
                "authority": item.authority_level,
                "resource_type": item.resource_type,
                "source_url": item.source_url,
            }
            for item in evidence_pack.evidence_items
        ]
        citation_manifest = [
            {
                "evidence_id": item.evidence_id,
                "label": (
                    item.source_label
                    or item.resource_type
                    or item.evidence_id
                ),
            }
            for item in evidence_pack.evidence_items
        ]
        retrieval_summary = str(getattr(evidence_pack, "retrieval_summary", "")).strip()
        memory_output = context.get("dependency_outputs", {}).get("memory")
        memory_payload = getattr(memory_output, "payload", None)
        context_summary = getattr(memory_payload, "context_summary", None)
        compressed_summary = sanitize_compressed_dialogue_summary(
            getattr(context_summary, "summary", "")
            or context.get("compressed_conversation_summary")
            or ""
        )
        conversation_messages = list(context.get("messages", []))
        recent_messages = conversation_messages[-1:] if compressed_summary else conversation_messages[-8:]
        model_payload = build_model_context(
                        context,
                        target_agent="expert_agent",
                        prompt_skill=skill,
                        payload={
                            "phase": skill_name,
                            "user_request": context.get("user_request", ""),
                            "question_explanation_request": bool(
                                question_explanation_request
                            ),
                            "external_information_request": bool(
                                external_information_request
                            ),
                            "current_date": current_date,
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
                            "semantic_evidence": (
                                semantic_evidence
                                if not retrieval_summary
                                else citation_manifest
                            ),
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
                                "content": (
                                    "直接输出完整自然语言学习支持正文；按用户问题自然组织，"
                                    "不要求固定标题或固定段落。"
                                    if flexible_support
                                    else (
                                        "直接输出完整自然语言题目讲解正文；采用直接讲题结构："
                                        "先一句话说明考查要点，再直接给出答案/思路与依据，"
                                        "逐项辨析选项或展开答题要点，末尾点出易错提示。"
                                        "不要套用知识讲解的启发式引导结构，不要弯弯绕绕，"
                                        "不要写成一篇知识讲解文章。"
                                        if question_explanation_request
                                        else (
                                            "直接输出完整自然语言讲解正文；采用启发式引导式结构："
                                            "先结合用户学情定位说明为什么讲这个点，再讲解核心内容，"
                                            "末尾提出 2-3 个开放式思考问题引导用户先自行思考。"
                                        )
                                    )
                                ),
                                "title": "可选标题。",
                                "thinking_questions": "可选：启发式思考问题列表（2-3 个，只提问不含答案）；题目讲解可不含。",
                                "uncertainty": "可选待确认内容。",
                            },
                            "output_schema": KnowledgeExplanationModelOutput.model_json_schema(),
                        },
                        permission_note=(
                            "结合最近对话解析当前问题中的指代，优先依据教材和网络来源生成教学讲解；覆盖不足时允许使用明确标注的"
                            "模型自身知识。不得伪造引用，不得生成现实诊断、处方或剂量建议。"
                        ),
                    )
        try:
            # Structured output is required so the model can declare
            # evidence_refs for the trailing citation card.  Keep a
            # plain-text fallback for legacy adapters that only implement
            # complete_text (references are then left empty by design).
            if callable(getattr(self.chat_model, "complete_json", None)):
                raw_output = await self.chat_model.complete_json(
                    "expert_agent",
                    model_payload,
                )
            else:
                text_output = await self.chat_model.complete_text("expert_agent", model_payload)
                raw_output = {"content": text_output}
            if not isinstance(raw_output, dict):
                raw_output = {}
            uncertainty = self._normalize_uncertainty(
                raw_output.get("uncertainty", raw_output.get("notes", []))
            )
            thinking_questions = self._normalize_thinking_questions(
                raw_output.get("thinking_questions")
                or raw_output.get("思考问题")
                or raw_output.get("exploration_questions")
            )
            body = (
                raw_output.get("explanation_content")
                or raw_output.get("explanation")
                or raw_output.get("content")
                or raw_output.get("body")
                or retrieval_summary
            )
            if question_explanation_request:
                body = (
                    str(body).rstrip()
                    + "\n\n这道题你主要卡在哪一步：证候识别、治法选择、代表方对应，还是答题组织？"
                )
            evidence_refs = [
                str(item).strip()
                for item in (
                    raw_output.get("evidence_refs")
                    or raw_output.get("references")
                    or []
                )
                if str(item).strip()
            ]
            output = KnowledgeExplanationModelOutput.model_validate(
                {
                    "title": raw_output.get("title") or (
                        f"{evidence_pack.query}学习要点"
                        if flexible_support
                        else f"{evidence_pack.query}知识讲解"
                    ),
                    "explanation_content": body,
                    "thinking_questions": thinking_questions,
                    "uncertainty": uncertainty,
                    "evidence_refs": evidence_refs,
                }
            )
        except ValidationError as exc:
            fallback = retrieval_summary or "\n".join(
                item.content_summary for item in evidence_pack.evidence_items[:3]
            )
            output = KnowledgeExplanationModelOutput(
                title=(
                    f"{evidence_pack.query}学习要点"
                    if flexible_support
                    else f"{evidence_pack.query}知识讲解"
                ),
                explanation_content=fallback,
                uncertainty=["模型讲解格式不可用，系统展示检索总结。"],
            )
        primary = evidence_pack.evidence_items[0]
        content_key = (
            "学习支持"
            if flexible_support
            else "题目讲解"
            if question_explanation_request
            else "知识讲解"
        )
        body_text = str(output.explanation_content)
        reference_markup = build_reference_card_markup(
            evidence_pack.evidence_items,
            output.evidence_refs,
        )
        if reference_markup:
            body_text = f"{body_text.rstrip()}\n\n{reference_markup}"
        content: dict[str, object] = {
            content_key: body_text
        }
        # The prose-producing Expert does not own a structured resource
        # selection contract.  Do not append a question after generation: an
        # Audit repair could not remove or replace that system-added item.
        # Open self-check questions stay in the natural-language explanation.
        if output.uncertainty:
            content["待确认项"] = output.uncertainty
        if output.thinking_questions:
            content["思考问题"] = output.thinking_questions
        draft = ResourceDraft(
            resource_draft_id=f"DRAFT_{uuid4().hex}",
            title=output.title,
            content=content,
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
                use_question_candidates=False,
                usage_reason="知识讲解以自然语言思考问题完成自检，本节点不自动追加正式题目。",
                selected_question_ids=[],
                resource_type="none",
            ),
            provenance=ResourceProvenance(
                question_origin="none",
                selected_evidence_ids=[primary.evidence_id],
                generated_sections=list(content),
            ),
        )
        return envelope(context, "expert_agent", "knowledge_explanation", draft)

    @staticmethod
    def _normalize_thinking_questions(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            values = [line.strip() for line in value.splitlines() if line.strip()]
        elif isinstance(value, (list, tuple)):
            values = [str(item).strip() for item in value]
        else:
            return []
        normalized: list[str] = []
        for item in values:
            text = " ".join(item.split()).strip()
            text = text.strip("·-—•*#").strip()
            if not text:
                continue
            if text not in normalized:
                normalized.append(text)
        return normalized[:3]

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

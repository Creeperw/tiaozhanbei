from __future__ import annotations

import ast
import re
from datetime import date
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from competition_app.agents.common import envelope
from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.knowledge import QuestionDetail
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
            else "knowledge_explanation"
        )
        skill = prompt_skill_registry.load("expert_agent", skill_name)
        flexible_support = task_type == "general_learning_support"
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
        model_payload = build_model_context(
                        context,
                        target_agent="expert_agent",
                        prompt_skill=skill,
                        payload={
                            "phase": skill_name,
                            "user_request": context.get("user_request", ""),
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
                                "content": (
                                    "直接输出完整自然语言学习支持正文；按用户问题自然组织，"
                                    "不要求固定标题或固定段落。"
                                    if flexible_support
                                    else "直接输出完整自然语言讲解正文。"
                                ),
                                "title": "可选标题。",
                                "uncertainty": "可选待确认内容。",
                            },
                        },
                        permission_note=(
                            "结合最近对话解析当前问题中的指代，优先依据教材和网络来源生成教学讲解；覆盖不足时允许使用明确标注的"
                            "模型自身知识。不得伪造引用，不得生成现实诊断、处方或剂量建议。"
                        ),
                    )
        try:
            # Knowledge explanation is a prose-producing business agent. Keep
            # a JSON fallback for test doubles and older model adapters.
            if callable(getattr(self.chat_model, "complete_text", None)):
                text_output = await self.chat_model.complete_text("expert_agent", model_payload)
                raw_output = {"content": text_output}
            else:
                raw_output = await self.chat_model.complete_json(
                    "expert_agent",
                    model_payload,
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
            if question_explanation_request:
                body = (
                    str(body).rstrip()
                    + "\n\n这道题你主要卡在哪一步：证候识别、治法选择、代表方对应，还是答题组织？"
                )
            output = KnowledgeExplanationModelOutput.model_validate(
                {
                    "title": raw_output.get("title") or (
                        f"{evidence_pack.query}学习要点"
                        if flexible_support
                        else f"{evidence_pack.query}知识讲解"
                    ),
                    "explanation_content": body,
                    "uncertainty": uncertainty,
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
        content: dict[str, object] = {
            "学习支持" if flexible_support else "知识讲解":
                output.explanation_content
        }
        selected_questions = [] if external_information_request else [
            item
            for item in evidence_pack._question_details
            if self._is_safe_practice_question(item.question_type)
            and self._is_relevant_practice_question(
                item,
                evidence_pack.query,
                str(context.get("user_request") or ""),
            )
        ][:1]
        if selected_questions:
            content["配套练习"] = [
                {
                    "题型": item.question_type,
                    "题目": item.stem,
                    "选项": self._learner_options(item.options),
                }
                for item in selected_questions
            ]
        elif not external_information_request:
            # A knowledge explanation should still end with an actionable
            # self-check when the formal question index has no usable match.
            content["配套练习"] = [
                {
                    "题型": "简答题",
                    "题目": f"请用自己的话概括“{evidence_pack.query}”的核心结论，并说明判断依据。",
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
    def _is_relevant_practice_question(
        question: QuestionDetail,
        *topic_texts: str,
    ) -> bool:
        """Reject safe-but-off-topic retrieval candidates before publication."""

        source = " ".join(str(value or "") for value in topic_texts)
        source = re.sub(r"《[^》]+》", " ", source)
        for phrase in (
            "你先给我讲讲", "请给我讲讲", "给我讲讲", "请结合教材证据",
            "学习要点", "学习重点", "阅读重点", "知识点", "相关的",
            "相关", "章节", "这一章", "这部分", "帮我梳理", "带我梳理",
            "有哪些", "是什么", "为什么", "怎么学", "如何学习",
        ):
            source = source.replace(phrase, " ")
        anchors: list[str] = []
        for segment in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,16}", source):
            normalized = segment.strip().lower()
            if normalized in {"中医", "教材", "理论", "内容", "介绍"}:
                continue
            anchors.append(normalized)
            for suffix in ("学说", "理论", "证型", "辨析"):
                if normalized.endswith(suffix) and len(normalized) > len(suffix) + 1:
                    anchors.append(normalized[:-len(suffix)])
        anchors = list(dict.fromkeys(anchor for anchor in anchors if len(anchor) >= 2))
        if not anchors:
            return False
        searchable = " ".join(
            [
                str(question.stem or ""),
                *[str(option or "") for option in question.options],
            ]
        ).replace(" ", "").lower()
        return any(anchor in searchable for anchor in anchors)

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

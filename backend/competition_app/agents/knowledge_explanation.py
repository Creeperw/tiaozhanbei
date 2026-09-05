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
from competition_app.llm.openai_compatible import ModelResponseError
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.schemas import KnowledgeExplanationModelOutput
from competition_app.llm.stub import StubChatModel
from competition_app.runtime.event_stream import emit_runtime_event
from competition_app.services.conversation_history import (
    sanitize_compressed_dialogue_summary,
)


KNOWLEDGE_EXPLANATION_FALLBACK_NOTICE = (
    "模型讲解未通过完整性校验，系统仅展示已检索到的可靠摘要。"
)
_EXPERT_EVIDENCE_MAX_ITEMS = 10
_EXPERT_EVIDENCE_MAX_CHARS = 16_000


class KnowledgeExplanationValidationError(ValueError):
    """Safe, non-content-bearing reason for structured repair diagnostics."""

    def __init__(self, validation_code: str) -> None:
        self.validation_code = validation_code
        super().__init__(validation_code)


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

    _INTERNAL_BODY_PATTERN = re.compile(
        r"(?:"
        r"\bevidence_id\b|\bclaim_id\b|\bprompt_skill_id\b|\boutput_schema\b|"
        r"\bE_(?:CHUNK|VIDEO|WEB|TEXTBOOK|REFERENCE)_[A-Za-z0-9_-]+\b|"
        r"\bC_[A-Fa-f0-9]{8,}\b|"
        r"系统提示词|内部评测合同|模型调用轨迹"
        r")",
        re.IGNORECASE,
    )

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
        # 知识库管理智能体对每条检索内容逐条提取（summary_items）：下游优先
        # 使用该结构化结果，每条带 evidence_id + 提取的原文 + 确定性来源，
        # 专家智能体据此在 evidence_refs 中按 evidence_id 声明引用；无逐条
        # 结果时回退到旧逻辑（完整证据体 / 拼接总结文本 + 精简引用清单）。
        all_summary_items = getattr(evidence_pack, "summary_items", None) or []
        summary_items = self._select_summary_items_for_expert(
            all_summary_items,
            external_information_request=external_information_request,
        )
        if summary_items:
            summary_evidence = [
                {
                    "evidence_id": item.evidence_id,
                    "content": item.content,
                    "authority": item.authority_level,
                    "resource_type": item.resource_type,
                    "source_url": item.source_url,
                    "source_label": item.source_label or item.source_id,
                }
                for item in summary_items
            ]
            retrieval_summary = "\n".join(
                f"[{item.evidence_id}｜{item.source_label or item.source_id}] {item.content}"
                for item in summary_items
            )
            citation_manifest = [
                {
                    "evidence_id": item.evidence_id,
                    "label": item.source_label or item.resource_type or item.evidence_id,
                }
                for item in summary_items
            ]
            semantic_evidence = summary_evidence
        else:
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
        d1_evaluation_mode = bool(context.get("d1_evaluation_mode"))
        d1_conflict_binding = context.get("d1_conflict_binding")
        evaluation_contract = None
        if d1_evaluation_mode and isinstance(d1_conflict_binding, dict):
            evaluation_contract = {
                "mode": "semantic_conflict_pair_closure_v1",
                "issue_id": str(d1_conflict_binding.get("issue_id") or ""),
                "claim_location": str(d1_conflict_binding.get("claim_location") or ""),
                "required_evidence_ids": [
                    str(item).strip()
                    for item in (d1_conflict_binding.get("required_evidence_ids") or [])
                    if str(item).strip()
                ],
                "scope": "仅当前主声明；不得扩大到其他声明或步骤",
            }
        audit_feedback = context.get("audit_feedback")
        feedback_payload = getattr(audit_feedback, "payload", audit_feedback)
        feedback_findings = list(
            getattr(feedback_payload, "findings", None)
            or (
                feedback_payload.get("findings", [])
                if isinstance(feedback_payload, dict)
                else []
            )
        )
        repair_instruction = dict(context.get("repair_instruction") or {})
        previous_step_output = context.get("previous_step_output")
        previous_payload = getattr(previous_step_output, "payload", previous_step_output)
        # ``audit_feedback`` can be present on ordinary audit-driven context
        # without an active local-repair action.  Only the orchestrator's
        # explicit repair contract should switch this producer into repair
        # mode; otherwise a failed/legacy audit must not cause a fresh draft
        # to receive a misleading previous-resource request.
        repair_request = self._build_repair_request(
            repair_instruction=repair_instruction,
            feedback_findings=feedback_findings if repair_instruction else [],
            previous_payload=previous_payload,
        )
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
                                if (summary_items or not retrieval_summary)
                                else citation_manifest
                            ),
                            "evaluation_contract": evaluation_contract,
                            "audit_feedback": feedback_findings,
                            **({"repair_request": repair_request} if repair_request else {}),
                            "output_contract": {
                                "content": (
                                    "直接输出完整自然语言学习支持正文；按用户问题自然组织，"
                                    "不要求固定标题或固定段落。正文必须包含实际学习内容，"
                                    "不得只返回标题、目录、问题列表或待确认项；证据不足处明确说明，"
                                    "不得臆造。"
                                    if flexible_support
                                    else (
                                        "完整讲解正文（必须是本 JSON 对象中的 explanation_content 字段值，"
                                        "禁止把正文放在 JSON 包装之外；按讲题逻辑自然组织，"
                                        "小节标题自由拟定，不必套用固定小节标题。"
                                        "要求：开头点明考查要点，接着直接给出答案/思路与依据，"
                                        "逐项辨析选项或展开答题要点，末尾点出易错提示。"
                                        "正文必须包含实际答案、依据和辨析，不得只返回标题或问题列表。"
                                        "可自由使用 markdown 小标题、加粗、列表、表格、引用块排版。"
                                        "讲解紧扣题目展开，直接给出解题路径，讲完即止。"
                                        if question_explanation_request
                                        else (
                                            "完整讲解正文（必须是本 JSON 对象中的 explanation_content 字段值，"
                                            "禁止把正文放在 JSON 包装之外；按自己的逻辑自然组织，"
                                            "小节标题自由拟定，不必套用固定小节标题。"
                                            "要求：开头自然带出为什么讲这个点（结合学情或考纲），"
                                            "正文讲透核心内容，末尾提出 2-3 个开放式思考问题引导用户"
                                            "先自行思考。可自由使用 markdown 小标题、加粗、列表、"
                                            "表格、引用块排版。正文必须包含定义、关键关系或机制等实际讲解，"
                                            "不得只返回标题、目录或思考问题；证据不足处列入待确认项，"
                                            "不得臆造。"
                                        )
                                    )
                                ),
                                "title": "可选标题。",
                                "thinking_questions": "可选：启发式思考问题列表（2-3 个，只提问不含答案）；题目讲解可不含。",
                                "uncertainty": "可选待确认内容。",
                                "conflict_claim_evidence_ids": (
                                    "仅在 evaluation_contract 非空时填写；必须逐字返回 required_evidence_ids 中的两个 ID，"
                                    "表示主声明同时绑定冲突证据。正常任务必须返回空列表。"
                                    if evaluation_contract
                                    else "正常任务必须返回空列表，不要填写内部评测字段。"
                                ),
                            },
                            "output_schema": KnowledgeExplanationModelOutput.model_json_schema(),
                        },
                        permission_note=(
                            "结合最近对话解析当前问题中的指代，优先依据教材和网络来源生成教学讲解；覆盖不足时允许使用明确标注的"
                            "模型自身知识。不得伪造引用，不得生成现实诊断、处方或剂量建议。"
                            + (
                                "当前是局部返修：必须基于上一版产物逐项修正指定问题，不得扩大修改范围。"
                                if repair_request
                                else ""
                            )
                        ),
                    )
        default_title = (
            f"{evidence_pack.query}学习要点"
            if flexible_support
            else f"{evidence_pack.query}知识讲解"
        )
        allowed_evidence_ids = {
            item.evidence_id for item in evidence_pack.evidence_items
        }
        detailed_request = any(
            marker in "".join(user_request.split()).lower()
            for marker in ("详细", "全面", "深入", "讲透", "系统讲解", "完整讲解")
        )
        model_payload["_result_validator"] = lambda result: self._validate_structured_result(
            result,
            default_title=default_title,
            allowed_evidence_ids=allowed_evidence_ids,
            evaluation_contract=evaluation_contract,
            question_explanation_request=question_explanation_request,
            detailed_request=detailed_request,
        ).model_dump(mode="json")
        try:
            # The learner-facing Expert is a prose producer.  In ordinary
            # production turns, request natural language directly and keep
            # structure inside the application boundary.  This avoids making
            # a provider serialize a long Markdown lesson through a strict
            # JSON contract (a frequent source of empty/invalid responses).
            # Isolated evaluations use the same production protocol. Any
            # finite D1 claim/evidence binding is compiled below from the
            # validated, system-owned evaluation contract rather than asking
            # the prose model to repeat a long lesson inside JSON.
            use_natural_language = callable(
                getattr(self.chat_model, "complete_text", None)
            )
            if use_natural_language:
                text_payload = self._natural_language_payload(
                    model_payload,
                    evaluation_contract=evaluation_contract,
                    semantic_evidence=semantic_evidence,
                )
                text_output = await self.chat_model.complete_text(
                    "expert_agent",
                    text_payload,
                )
                self._validate_learner_body(
                    text_output,
                    question_explanation_request=question_explanation_request,
                    detailed_request=detailed_request,
                )
                output = KnowledgeExplanationModelOutput(
                    title=self._natural_language_title(
                        text_output,
                        default_title=default_title,
                    ),
                    explanation_content=text_output,
                    evidence_refs=self._deterministic_evidence_refs(
                        summary_items=summary_items,
                        evidence_items=evidence_pack.evidence_items,
                    ),
                    conflict_claim_evidence_ids=(
                        self._compile_conflict_claim_evidence_ids(
                            evaluation_contract=evaluation_contract,
                            allowed_evidence_ids=allowed_evidence_ids,
                        )
                        if d1_evaluation_mode
                        else []
                    ),
                )
            elif callable(getattr(self.chat_model, "complete_json", None)):
                raw_output = await self.chat_model.complete_json(
                    "expert_agent",
                    model_payload,
                )
                if not isinstance(raw_output, dict):
                    raw_output = {}
                # Apply the same trusted business validator again after the
                # adapter returns. Production adapters also run it inside
                # their repair/failover loop; this protects legacy/test
                # adapters that simply return a dict.
                output = self._validate_structured_result(
                    raw_output,
                    default_title=default_title,
                    allowed_evidence_ids=allowed_evidence_ids,
                    evaluation_contract=evaluation_contract,
                    question_explanation_request=question_explanation_request,
                    detailed_request=detailed_request,
                )
            else:
                raise ModelResponseError(
                    "Chat model has no supported completion method",
                    reason="unsupported_completion_method",
                    failover_eligible=False,
                )
        except (ValidationError, ValueError, ModelResponseError) as exc:
            emit_runtime_event(
                "expert_generation_fallback",
                agent="expert_agent",
                step_id=str(context.get("step_id") or "expert"),
                failure_category=self._safe_failure_category(exc),
                validation_codes=self._safe_validation_codes(exc),
            )
            fallback = self._build_safe_fallback(
                summary_items=all_summary_items,
                evidence_items=evidence_pack.evidence_items,
                retrieval_summary=retrieval_summary,
            )
            output = KnowledgeExplanationModelOutput(
                title=default_title,
                explanation_content=fallback,
                uncertainty=[KNOWLEDGE_EXPLANATION_FALLBACK_NOTICE],
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
        claim_evidence_ids = [primary.evidence_id]
        if d1_evaluation_mode and output.conflict_claim_evidence_ids:
            claim_evidence_ids = list(output.conflict_claim_evidence_ids)
        draft = ResourceDraft(
            resource_draft_id=f"DRAFT_{uuid4().hex}",
            title=output.title,
            content=content,
            estimated_minutes=int(context.get("available_minutes", 15)),
            claims=[
                ResourceClaim(
                    claim_id=f"C_{uuid4().hex}",
                    text=primary.content_summary,
                    evidence_ids=claim_evidence_ids,
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
    def _natural_language_payload(
        model_payload: dict[str, Any],
        *,
        evaluation_contract: dict[str, Any] | None = None,
        semantic_evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Remove machine-only JSON controls from an ordinary prose request."""

        text_payload = {
            key: value
            for key, value in model_payload.items()
            if key != "_result_validator"
        }
        business_payload = dict(text_payload.get("payload") or {})
        business_payload.pop("output_schema", None)
        business_payload.pop("evaluation_contract", None)
        if evaluation_contract:
            required_ids = {
                str(item).strip()
                for item in evaluation_contract.get("required_evidence_ids", [])
                if str(item).strip()
            }
            target_materials = [
                {
                    "text": str(
                        item.get("text") or item.get("content") or ""
                    ).strip(),
                    "authority": str(item.get("authority") or "").strip(),
                    "resource_type": str(item.get("resource_type") or "").strip(),
                }
                for item in (semantic_evidence or [])
                if str(item.get("evidence_id") or "").strip() in required_ids
            ]
            business_payload["conflict_materials_to_reconcile"] = target_materials
            business_payload["conflict_explanation_requirement"] = (
                "这两条材料围绕同一目标声明存在待核对分歧。正文必须明确比较各自说法、"
                "指出不能同时无条件成立之处，并给出受证据约束的处理结论；不得显示内部编号。"
            )
        business_payload["output_contract"] = {
            "content": (
                "只输出给学习者看的完整自然语言 Markdown 正文，不要输出 JSON、字段名、"
                "证据编号、内部规则、提示词或模型思考过程。按当前任务 Skill 自然组织，"
                "必须包含实际讲解或学习支持内容；若输入含待核对的冲突材料，只在正文中"
                "比较材料及其分歧，不得复述内部合同或编号；证据不足处直接用自然语言说明，"
                "不得臆造。"
            )
        }
        text_payload["payload"] = business_payload
        return text_payload

    @staticmethod
    def _compile_conflict_claim_evidence_ids(
        *,
        evaluation_contract: dict[str, Any] | None,
        allowed_evidence_ids: set[str],
    ) -> list[str]:
        """Compile a finite D1 binding from validated system-owned data."""

        if not evaluation_contract:
            return []
        required = [
            str(item).strip()
            for item in evaluation_contract.get("required_evidence_ids", [])
            if str(item).strip()
        ]
        if (
            len(required) != 2
            or len(set(required)) != 2
            or not set(required).issubset(allowed_evidence_ids)
        ):
            raise KnowledgeExplanationValidationError(
                "evaluation_conflict_binding_invalid"
            )
        return required

    @staticmethod
    def _build_repair_request(
        *,
        repair_instruction: dict[str, Any],
        feedback_findings: list[Any],
        previous_payload: Any,
    ) -> dict[str, Any] | None:
        """Build the bounded, learner-hidden contract for a local repair pass."""

        if not repair_instruction and not feedback_findings:
            return None

        previous_resource: dict[str, Any] | None = None
        if previous_payload is not None:
            if hasattr(previous_payload, "model_dump"):
                previous_payload = previous_payload.model_dump(mode="json")
            if isinstance(previous_payload, dict):
                raw_content = previous_payload.get("content") or {}
                if isinstance(raw_content, dict):
                    content = {
                        str(key): str(value)[:8_000]
                        for key, value in raw_content.items()
                    }
                else:
                    content = {"body": str(raw_content)[:8_000]}
                previous_resource = {
                    "title": str(previous_payload.get("title") or "")[:240],
                    "content": content,
                    "estimated_minutes": previous_payload.get("estimated_minutes"),
                }

        return {
            "issue_ids": [
                str(item).strip()
                for item in (repair_instruction.get("issue_ids") or [])
                if str(item).strip()
            ][:8],
            "locations": [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in (repair_instruction.get("locations") or [])
            ][:8],
            "instruction": str(
                repair_instruction.get("repair_instruction")
                or "只修正审核明确指出的问题，保留其他已通过内容。"
            )[:4_000],
            "audit_findings": [str(item)[:600] for item in feedback_findings[:8]],
            "previous_resource": previous_resource,
        }

    @staticmethod
    def _deterministic_evidence_refs(
        *,
        summary_items: list[Any],
        evidence_items: list[EvidenceItem],
    ) -> list[str]:
        """Choose only IDs from evidence actually exposed to the prose model."""

        allowed = {item.evidence_id for item in evidence_items}
        source_ids = (
            [str(getattr(item, "evidence_id", "")).strip() for item in summary_items]
            if summary_items
            else [str(item.evidence_id).strip() for item in evidence_items]
        )
        refs: list[str] = []
        for evidence_id in source_ids:
            if evidence_id and evidence_id in allowed and evidence_id not in refs:
                refs.append(evidence_id)
            if len(refs) >= 3:
                break
        return refs

    @staticmethod
    def _natural_language_title(body: str, *, default_title: str) -> str:
        """Use the prose model's first Markdown heading as the resource title."""

        for raw_line in str(body or "").splitlines():
            match = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", raw_line)
            if not match:
                continue
            title = re.sub(r"[`*_~\[\]]", "", match.group(1)).strip(" ：:。")
            if title:
                return title[:120]
        return default_title

    def _safe_validation_codes(self, exc: BaseException) -> list[str]:
        codes: list[str] = []
        direct_code = str(getattr(exc, "validation_code", "") or "").strip()
        if direct_code:
            codes.append(direct_code)
        current: Any = self.chat_model
        for _ in range(4):
            details = getattr(current, "last_error_details", None)
            if isinstance(details, dict):
                for item in details.get("business_validation_codes", []) or []:
                    code = str(item).strip()
                    if code and code not in codes:
                        codes.append(code)
            current = getattr(current, "inner", None)
            if current is None:
                break
        return [re.sub(r"[^a-zA-Z0-9_:\-]", "", item)[:80] for item in codes[:8]]

    @staticmethod
    def _safe_failure_category(exc: BaseException) -> str:
        if isinstance(exc, ModelResponseError):
            reason = re.sub(
                r"[^a-zA-Z0-9_:\-]", "", str(getattr(exc, "reason", "") or "")
            )[:80]
            return reason or "model_response_error"
        if isinstance(exc, KnowledgeExplanationValidationError):
            return "business_schema_invalid"
        if isinstance(exc, ValidationError):
            return "schema_validation_error"
        return "business_validation_error"

    @staticmethod
    def _select_summary_items_for_expert(
        summary_items: list[Any],
        *,
        external_information_request: bool,
    ) -> list[Any]:
        """Keep Expert context compact without mutating the auditable pack.

        Retrieval order remains the relevance tie-breaker.  Textbook evidence
        is preferred for ordinary teaching requests, while current-fact
        requests prefer web/reference items.  Audit still receives the full
        EvidencePack, so this is context minimization rather than evidence
        deletion.
        """

        indexed = list(enumerate(summary_items))

        def priority(pair: tuple[int, Any]) -> tuple[int, int]:
            index, item = pair
            resource_type = str(getattr(item, "resource_type", "") or "")
            authority = str(getattr(item, "authority_level", "") or "")
            is_textbook = resource_type == "textbook" or authority == "textbook"
            preferred = not is_textbook if external_information_request else is_textbook
            return (0 if preferred else 1, index)

        selected: list[Any] = []
        used_chars = 0
        for _, item in sorted(indexed, key=priority):
            content = str(getattr(item, "content", "") or "").strip()
            if not content:
                continue
            if selected and used_chars + len(content) > _EXPERT_EVIDENCE_MAX_CHARS:
                continue
            selected.append(item)
            used_chars += len(content)
            if len(selected) >= _EXPERT_EVIDENCE_MAX_ITEMS:
                break
        if not selected and summary_items:
            selected.append(summary_items[0])
        return selected

    @classmethod
    def _validate_structured_result(
        cls,
        raw_output: dict[str, Any],
        *,
        default_title: str,
        allowed_evidence_ids: set[str],
        evaluation_contract: dict[str, Any] | None,
        question_explanation_request: bool,
        detailed_request: bool,
    ) -> KnowledgeExplanationModelOutput:
        if not isinstance(raw_output, dict):
            raise KnowledgeExplanationValidationError("expert_output_not_object")
        body = str(
            raw_output.get("explanation_content")
            or raw_output.get("explanation")
            or raw_output.get("content")
            or raw_output.get("body")
            or ""
        ).rstrip()
        cls._validate_learner_body(
            body,
            question_explanation_request=question_explanation_request,
            detailed_request=detailed_request,
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
        # Unknown IDs are deterministically discarded. They cannot reach the
        # learner-facing reference card or claim provenance, while valid IDs
        # remain usable when an otherwise sound answer contains one bad ref.
        evidence_refs = [
            ref for ref in evidence_refs if ref in allowed_evidence_ids
        ]

        conflict_ids = [
            str(item).strip()
            for item in (raw_output.get("conflict_claim_evidence_ids") or [])
            if str(item).strip()
        ]
        required_conflict_ids = {
            str(item).strip()
            for item in (evaluation_contract or {}).get("required_evidence_ids", [])
            if str(item).strip()
        }
        if evaluation_contract:
            if len(conflict_ids) != 2 or set(conflict_ids) != required_conflict_ids:
                raise KnowledgeExplanationValidationError(
                    "evaluation_conflict_binding_incomplete"
                )
        elif conflict_ids:
            raise KnowledgeExplanationValidationError(
                "unexpected_evaluation_control_field"
            )

        return KnowledgeExplanationModelOutput.model_validate(
            {
                "title": raw_output.get("title") or default_title,
                "explanation_content": body,
                "thinking_questions": cls._normalize_thinking_questions(
                    raw_output.get("thinking_questions")
                    or raw_output.get("思考问题")
                    or raw_output.get("exploration_questions")
                ),
                "uncertainty": cls._normalize_uncertainty(
                    raw_output.get("uncertainty", raw_output.get("notes", []))
                ),
                "evidence_refs": evidence_refs,
                "conflict_claim_evidence_ids": conflict_ids,
            }
        )

    @classmethod
    def _validate_learner_body(
        cls,
        body: str,
        *,
        question_explanation_request: bool,
        detailed_request: bool,
    ) -> None:
        if not body.strip():
            raise KnowledgeExplanationValidationError("explanation_body_empty")
        if cls._INTERNAL_BODY_PATTERN.search(body):
            raise KnowledgeExplanationValidationError(
                "internal_control_data_in_body"
            )

        substantive_lines: list[str] = []
        for raw_line in body.splitlines() or [body]:
            line = raw_line.strip()
            if not line or re.match(r"^#{1,6}\s+", line):
                continue
            line = re.sub(r"^(?:[-*+]\s+|\d+[.)、]\s*)", "", line).strip()
            if line:
                substantive_lines.append(line)
        prose = "\n".join(substantive_lines)
        semantic_text = re.sub(r"[`*_>#|\[\](){}\s，。；：、！？!?,.\-—]+", "", prose)
        if len(semantic_text) < 6:
            raise KnowledgeExplanationValidationError(
                "explanation_has_no_substantive_prose"
            )

        declarative_segments = [
            segment.strip()
            for segment in re.split(r"[。；.!]\s*", prose)
            if segment.strip() and not segment.strip().endswith(("？", "?"))
        ]
        if not declarative_segments:
            raise KnowledgeExplanationValidationError("explanation_questions_only")
        if question_explanation_request and len(semantic_text) < 36:
            raise KnowledgeExplanationValidationError(
                "question_answer_or_rationale_missing"
            )
        if detailed_request and (
            len(semantic_text) < 100 or len(declarative_segments) < 2
        ):
            raise KnowledgeExplanationValidationError(
                "detailed_explanation_incomplete"
            )

    @classmethod
    def _build_safe_fallback(
        cls,
        *,
        summary_items: list[Any],
        evidence_items: list[EvidenceItem],
        retrieval_summary: str,
    ) -> str:
        # Per-item extracted content is preferred because retrieval_summary may
        # include transport-only citation IDs such as ``[E_CHUNK_...]``.
        candidates = [
            str(getattr(item, "content", "")).strip()
            for item in summary_items[:3]
            if str(getattr(item, "content", "")).strip()
        ]
        if not candidates:
            candidates = [
                str(item.content_summary).strip()
                for item in evidence_items[:3]
                if str(item.content_summary).strip()
            ]
        fallback = "\n".join(candidates) or str(retrieval_summary).strip()
        fallback = re.sub(r"\[(?:E_[^\]|]+)(?:\|[^\]]*)?\]\s*", "", fallback)
        fallback = cls._INTERNAL_BODY_PATTERN.sub("[内部标识已隐藏]", fallback).strip()
        return fallback or "当前可靠证据不足，暂不生成具体讲解。"

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

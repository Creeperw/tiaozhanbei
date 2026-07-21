from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from competition_app.agents.common import envelope
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.knowledge import to_learner_view
from competition_app.contracts.resource import (
    QuestionConsumptionDecision,
    ResourceClaim,
    ResourceDraft,
)
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.stub import StubChatModel
from competition_app.llm.schemas import ExpertModelOutput, PaperBlueprintModelOutput


class ExpertAgent:
    def __init__(self, chat_model: ChatModel | None = None) -> None:
        self.chat_model = chat_model or StubChatModel()

    async def run(self, context: dict[str, Any]) -> AgentEnvelope[ResourceDraft]:
        evidence_pack = context["dependency_outputs"]["knowledge"].payload
        topic = evidence_pack.query
        task_type = str(context.get("task_type", "personalized_review_card"))
        prompt_skill = prompt_skill_registry.load(
            "expert_agent", task_type
        )
        if not evidence_pack.evidence_items:
            raise ValueError("expert agent requires at least one evidence item")
        primary_evidence = evidence_pack.evidence_items[0]
        semantic_evidence = [
            " ".join(str(item.content_summary).split())[:800]
            for item in evidence_pack.evidence_items[:3]
        ]
        retrieval_summary = str(getattr(evidence_pack, "retrieval_summary", "")).strip()
        dependency_outputs = context["dependency_outputs"]
        memory_payload = getattr(dependency_outputs.get("memory"), "payload", None)
        diagnosis_payload = getattr(dependency_outputs.get("diagnosis"), "payload", None)
        formal_plan = getattr(dependency_outputs.get("learning_plan"), "payload", None)
        review_schedule = getattr(dependency_outputs.get("schedule"), "payload", None)
        learner_preferences = (
            memory_payload.learner_context.confirmed_preferences if memory_payload else {}
        )
        learning_profile = {
            "summary": getattr(diagnosis_payload, "summary", ""),
            "risk_flags": getattr(diagnosis_payload, "risk_flags", []),
            "target_difficulty": getattr(diagnosis_payload, "target_difficulty", 2),
        }
        short_term_plan = (
            formal_plan.short_term_plan.content if formal_plan else ""
        )
        learning_task = (
            formal_plan.learning_task.model_dump(mode="json")
            if formal_plan
            else {
                "task_type": "review_resource",
                "task_content": getattr(diagnosis_payload, "summary", "完成本次复习任务"),
                "estimated_minutes": context.get("available_minutes", 15),
                "expected_output": "完成知识卡片学习与练习反馈。",
                "completion_criteria": "完成知识卡片、自测题和复习反馈。",
            }
        )
        review_schedule_payload = (
            {
                "primary_kp_id": review_schedule.selected_task.primary_kp_id,
                "reason_codes": next(
                    (
                        candidate.reason_codes
                        for candidate in review_schedule.candidates
                        if candidate.kp_id == review_schedule.selected_task.primary_kp_id
                    ),
                    [],
                ),
            }
            if review_schedule and review_schedule.selected_task
            else {}
        )
        if review_schedule and review_schedule.selected_task is None:
            raise ValueError("expert agent requires a selected review task")
        audit_feedback = context.get("audit_feedback")
        feedback_findings = getattr(getattr(audit_feedback, "payload", audit_feedback), "findings", [])
        question_details = list(evidence_pack._question_details)
        candidate_catalog = [
            {
                "question_id": item.question_id,
                "question_type": item.question_type,
                "tags": item.tags,
                "kp_ids": sorted({bridge.kp_id for bridge in item.bridges}),
                "channels": item.retrieval.channels,
                "bridge_layers": sorted({bridge.bridge_layer for bridge in item.bridges}),
            }
            for item in question_details
        ]
        paper_generation = task_type == "paper_generation"
        exam_constraints = context.get("exam_constraints", {}) if paper_generation else {}
        try:
            raw_output = await self.chat_model.complete_json(
                "expert_agent",
                build_model_context(
                    context,
                    target_agent="expert_agent",
                    prompt_skill=prompt_skill,
                    payload=(
                        {
                            "topic": topic,
                            "retrieval_summary": retrieval_summary,
                            "evidence": semantic_evidence if not retrieval_summary else [],
                            "candidate_questions": [
                                {
                                    "question_id": item["question_id"],
                                    "question_type": item["question_type"],
                                    "stem": next(
                                        q.stem for q in question_details
                                        if q.question_id == item["question_id"]
                                    ),
                                }
                                for item in candidate_catalog
                                if self._is_review_question_type(item["question_type"])
                            ][:8],
                            "task": {
                                "available_minutes": context.get("available_minutes", 15),
                                "diagnosis": learning_profile["summary"],
                                "schedule": review_schedule_payload,
                            },
                            "output_contract": {
                                "body": "面向学习者直接生成完整知识卡正文，使用自然语言，不要嵌套结构。",
                                "learning_tip": "可选的一句学习动作提示。",
                                "use_question_candidates": "是否使用候选题。",
                                "selected_question_ids": "只能填写 candidate_questions 中的 ID。",
                                "resource_type": "none 或 practice。",
                            },
                        }
                        if not paper_generation
                        else {
                            "phase": "paper_blueprint",
                            "paper_generation": {"enabled": True},
                            "paper_blueprint": context.get("exam_constraints", {}),
                            "candidate_questions": candidate_catalog,
                            "question_candidate_catalog": candidate_catalog,
                            "output_schema": ExpertModelOutput.model_json_schema(),
                        }
                    ),
                    permission_note=(
                        "只生成证据约束下的试卷蓝图提示和候选题使用策略；不得生成完整题目、答案、解析、评分细则、"
                        "系统ID或修改计划与知识状态。"
                        if paper_generation
                        else "只生成证据约束下的教学提示和候选题使用决策；复习调度是只读系统事实，不得改写掌握度、遗忘系数、保留率、优先级、复习时间、正式计划、任务状态或参考结论。"
                    ),
                ),
            )
            if (
                isinstance(raw_output, dict)
                and raw_output.get("resource_type") in {
                    "review_card",
                    "knowledge_card",
                    "知识卡片",
                    "复习卡",
                }
            ):
                use_candidates = bool(raw_output.get("use_question_candidates"))
                available_ids = [
                    item.question_id
                    for item in question_details
                    if self._is_review_question_type(item.question_type)
                ]
                requested_ids = raw_output.get("selected_question_ids") or []
                raw_output = {
                    **raw_output,
                    "use_question_candidates": use_candidates and bool(available_ids),
                    "selected_question_ids": (
                        [item for item in requested_ids if item in available_ids]
                        or available_ids[:1]
                        if use_candidates and available_ids
                        else []
                    ),
                    "resource_type": "practice" if use_candidates and available_ids else "none",
                }
            elif (
                isinstance(raw_output, dict)
                and raw_output.get("use_question_candidates") is False
                and raw_output.get("resource_type") not in {
                    None,
                    "none",
                    "practice",
                    "variant",
                    "grading_support",
                }
            ):
                raw_output = {**raw_output, "resource_type": "none"}
            model_exp = (
                self._natural_language_body(
                    raw_output.get("body")
                    or raw_output.get("exp")
                    or raw_output.get("content")
                    or raw_output.get("learning_tip")
                )
                if isinstance(raw_output, dict)
                else None
            )
            if isinstance(raw_output, dict) and not paper_generation:
                raw_output = {
                    "learning_tip": (
                        raw_output.get("learning_tip")
                        or raw_output.get("tip")
                        or "请完成主动回忆并对照正文自查。"
                    ),
                    "use_question_candidates": bool(raw_output.get("use_question_candidates", False)),
                    "usage_reason": str(raw_output.get("usage_reason", "")),
                    "selected_question_ids": raw_output.get("selected_question_ids") or [],
                    "resource_type": raw_output.get("resource_type") or "none",
                    "blueprint_content": raw_output.get("blueprint_content"),
                }
            try:
                if paper_generation:
                    blueprint_output = PaperBlueprintModelOutput.model_validate(raw_output)
                    model_output = ExpertModelOutput.model_validate({
                        "learning_tip": "后续落题应严格依据蓝图、教材证据和候选题边界执行。",
                        "use_question_candidates": True,
                        "usage_reason": "蓝图阶段只制定候选题使用策略，不选择具体题目。",
                        "selected_question_ids": [],
                        "resource_type": "practice",
                        "blueprint_content": self._blueprint_text(blueprint_output),
                    })
                else:
                    model_output = ExpertModelOutput.model_validate(raw_output)
            except ValidationError:
                if paper_generation:
                    raise
                raw_dict = raw_output if isinstance(raw_output, dict) else {}
                fallback_tip = self._compact_natural_language(
                    raw_dict.get("learning_tip")
                    or raw_dict.get("exp")
                    or "请完成本次知识点复习。"
                )
                safe_ids = [
                    item.question_id
                    for item in question_details
                    if self._is_review_question_type(item.question_type)
                ][:3]
                model_output = ExpertModelOutput.model_validate({
                    "learning_tip": fallback_tip,
                    "use_question_candidates": bool(safe_ids),
                    "usage_reason": "系统已将检索到的安全题型候选加入复习资源。",
                    "selected_question_ids": safe_ids,
                    "resource_type": "practice" if safe_ids else "none",
                    "blueprint_content": None,
                })
        except ValidationError as exc:
            if context.get("terminal_trace"):
                context["terminal_trace"].validation("expert_agent", valid=False, detail="ExpertModelOutput")
            raise ValueError("expert model output violates protocol") from exc
        if context.get("terminal_trace"):
            context["terminal_trace"].validation("expert_agent", valid=True, detail="ExpertModelOutput")
        candidate_ids = {item.question_id for item in question_details}
        unknown_requested_ids = set(model_output.selected_question_ids) - candidate_ids
        if unknown_requested_ids:
            raise ValueError("selected question is outside candidate catalog")
        safe_review_ids = [
            item.question_id
            for item in question_details
            if self._is_review_question_type(item.question_type)
        ]
        selected_ids = [
            question_id
            for question_id in model_output.selected_question_ids
            if question_id in candidate_ids and question_id in safe_review_ids
        ]
        if not paper_generation and not selected_ids and safe_review_ids:
            selected_ids = safe_review_ids[:3]
        if not paper_generation:
            uses_questions = bool(selected_ids)
            model_output = model_output.model_copy(
                update={
                    "use_question_candidates": uses_questions,
                    "resource_type": "practice" if uses_questions else "none",
                    "selected_question_ids": selected_ids,
                    "usage_reason": (
                        model_output.usage_reason
                        or (
                            "系统根据检索到的正式候选题提供巩固练习。"
                            if uses_questions
                            else "当前没有可安全展示的题目候选。"
                        )
                    ),
                }
            )
        if not paper_generation and question_details and not selected_ids:
            selected_ids = [
                item.question_id
                for item in question_details
                if self._is_review_question_type(item.question_type)
            ][:3]
            model_output = model_output.model_copy(
                update={
                    "use_question_candidates": True,
                    "resource_type": "practice",
                    "usage_reason": (
                        model_output.usage_reason
                        or "系统根据检索到的正式候选题提供巩固练习。"
                    ),
                }
            )
        if paper_generation and model_output.resource_type != "practice":
            raise ValueError("paper blueprint requires resource_type=practice")
        if paper_generation and not model_output.blueprint_content:
            raise ValueError("paper blueprint requires blueprint_content")
        if not paper_generation and model_output.blueprint_content is not None:
            raise ValueError("blueprint_content is only allowed for paper_generation")
        if paper_generation and selected_ids:
            raise ValueError("paper blueprint must not select concrete question IDs")
        unknown_ids = set(selected_ids) - candidate_ids
        if unknown_ids:
            raise ValueError("selected question is outside candidate catalog")
        selected_questions = [
            to_learner_view(item).model_dump(mode="json")
            for item in question_details
            if item.question_id in selected_ids
        ]
        video_resources = [
            {
                "title": item.source_id,
                "summary": item.content_summary,
                "url": item.source_url,
                "resource_type": item.resource_type,
            }
            for item in evidence_pack.evidence_items
            if item.resource_type in {"video", "reference"} and item.source_url
        ]
        consumption = QuestionConsumptionDecision(
            use_question_candidates=model_output.use_question_candidates,
            usage_reason=model_output.usage_reason,
            selected_question_ids=selected_ids,
            resource_type=model_output.resource_type,
        )
        if paper_generation:
            content: dict[str, object] = {
                "试卷蓝图": model_output.blueprint_content,
            }
        else:
            # Evidence is an internal grounding source, not learner-facing
            # copy. The Expert's learning tip is the card content; provenance
            # remains in claims/audit/snapshot boundaries.
            learning_prompt = (
                f"【本次目标】围绕{topic}完成主动回忆。\n"
                "【执行步骤】先闭卷写出组成/结构、功用、适用条件和一个易错点；"
                "再对照知识卡片自查，记录遗漏或不确定内容；最后完成练习资源并提交反馈。"
            )
            content = {
                "知识卡片": {
                    "kp_id": (
                        review_schedule.selected_task.primary_kp_id
                        if review_schedule and review_schedule.selected_task
                        else (evidence_pack.resolved_kp_ids[0] if evidence_pack.resolved_kp_ids else "")
                    ),
                    "kp_name": topic,
                    "exp": model_exp or self._build_knowledge_card(
                        topic,
                        evidence_pack.evidence_items,
                        model_output.learning_tip,
                    ),
                },
                "学习提示": learning_prompt,
                "视频资源": [
                    item for item in video_resources if item["resource_type"] == "video"
                ],
                "参考资料": [
                    item for item in video_resources if item["resource_type"] == "reference"
                ],
                "练习资源": [],
            }
        if selected_questions:
            content["练习资源"] = selected_questions
        draft = ResourceDraft(
            resource_draft_id=f"DRAFT_{uuid4().hex}",
            title=f"{topic}试卷蓝图" if paper_generation else f"{topic}个性化复习卡",
            content=content,
            target_difficulty=int(learning_profile["target_difficulty"]),
            estimated_minutes=int(context.get("available_minutes", 15)),
            claims=[
                ResourceClaim(
                    claim_id=f"C_{uuid4().hex}",
                    text=primary_evidence.content_summary,
                    evidence_ids=[primary_evidence.evidence_id],
                )
            ],
            safety_notes=evidence_pack.risk_notes
            or ["仅用于中医药教学训练，不构成诊疗建议。"],
            question_consumption=consumption,
            target_kp_id=(
                review_schedule.selected_task.primary_kp_id
                if review_schedule and review_schedule.selected_task
                else None
            ),
        )
        return envelope(context, "expert_agent", "resource_draft", draft)

    @staticmethod
    def _build_knowledge_card(topic: str, evidence_items: list[Any], learning_tip: str) -> str:
        summaries = [str(item.content_summary).strip() for item in evidence_items if str(item.content_summary).strip()]
        evidence_text = "；".join(
            "".join(summary.split())[:180] for summary in summaries[:3]
        )
        return (
            f"【知识点解释】{topic}的核心内容如下：\n"
            f"{evidence_text}\n"
            "【核心要点】请从上述教材内容提取知识对象、组成/结构、功用或功能、适用条件和关键辨析。\n"
            "【理解关系】把组成、功能与适用条件联系起来理解，不要只背孤立名词。\n"
            "【易错辨析】对教材未明确或存在口径差异的内容，保留‘待确认’或来源范围说明。\n"
            f"【学习动作】{learning_tip}"
        )

    @staticmethod
    def _compact_natural_language(value: object, limit: int = 500) -> str:
        """Keep the model boundary small; the system builds the resource object."""
        text = " ".join(str(value or "").split())
        return text[:limit].strip() or "请完成本次知识点复习。"

    @staticmethod
    def _natural_language_body(value: object, limit: int = 8_000) -> str | None:
        text = str(value or "").strip()
        return text[:limit] if text else None

    @staticmethod
    def _blueprint_text(output: PaperBlueprintModelOutput) -> str:
        units = "；".join(
            f"{unit.knowledge_module}：{unit.learning_objective}，建议{unit.required_question_count}题"
            for unit in output.units
        )
        return (
            f"【来源与假设】{output.source_status}；{'; '.join(output.assumptions) or '题量和范围按当前输入确定。'}"
            f"【命题目标】{output.scope_summary}"
            f"【蓝图矩阵】{units}"
            "【题型与抽题规则】优先从当前候选题池选择并去重。"
            "【候选题使用策略】蓝图阶段不选择具体题号。"
            f"【发布前验收】{'；'.join(output.acceptance_criteria) or '检查证据覆盖、题目去重和教学安全。'}"
        )

    @staticmethod
    def _is_review_question_type(question_type: str) -> bool:
        normalized = str(question_type).replace(" ", "")
        return normalized in {"单选题", "单项选择题", "多选题", "多项选择题", "判断题"}

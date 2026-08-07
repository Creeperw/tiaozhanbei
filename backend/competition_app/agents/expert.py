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
from competition_app.contracts.audit_policy import ResourceProvenance
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.stub import StubChatModel
from competition_app.llm.schemas import ExpertModelOutput, PaperBlueprintModelOutput
from competition_app.services.audit_policy import build_resource_acceptance_policy


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
        # 知识库管理智能体逐条提取的结果优先：每条带 evidence_id + 原文 + 来源，
        # 专家智能体据此在输出中引用 evidence_id；无逐条结果时回退旧逻辑。
        summary_items = getattr(evidence_pack, "summary_items", None) or []
        if summary_items:
            semantic_evidence = [
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
        else:
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
        profile = context.get("user_profile") if isinstance(context.get("user_profile"), dict) else {}
        learner_preferences = (
            memory_payload.learner_context.confirmed_preferences if memory_payload else {}
        ) or profile.get("preferences") or profile.get("user_preference") or profile
        learning_profile = {
            "summary": getattr(diagnosis_payload, "summary", ""),
            "risk_flags": getattr(diagnosis_payload, "risk_flags", []),
        }
        formal_learning_task = getattr(formal_plan, "learning_task", None)
        acceptance_policy = build_resource_acceptance_policy(
            context,
            formal_learning_task=formal_learning_task,
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
        repair_instruction = dict(context.get("repair_instruction") or {})
        previous_step_output = context.get("previous_step_output")
        previous_payload = getattr(previous_step_output, "payload", previous_step_output)
        previous_resource = None
        if previous_payload is not None:
            previous_resource = {
                "title": getattr(previous_payload, "title", None),
                "content": getattr(previous_payload, "content", None),
                "estimated_minutes": getattr(previous_payload, "estimated_minutes", None),
            }
        question_details = list(evidence_pack._question_details)
        if task_type != "paper_generation" and evidence_pack.resolved_kp_ids:
            resolved_kp_ids = set(evidence_pack.resolved_kp_ids)
            # Broad BM25 retrieval can return a formally indexed question that
            # belongs to an unrelated KP. Exclude it before the model boundary
            # instead of paying for an inevitable Audit rejection and repair.
            question_details = [
                item
                for item in question_details
                if resolved_kp_ids.intersection(
                    bridge.kp_id for bridge in item.bridges if bridge.kp_id
                )
            ]
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
        resource_candidates = [
            item
            for item in evidence_pack.evidence_items
            if item.resource_type in {"video", "reference"} and item.source_url
        ][:8]
        candidate_resources = [
            {
                "candidate_id": f"RESOURCE_CANDIDATE_{index}",
                "title": item.source_id,
                "summary": " ".join(str(item.content_summary).split())[:500],
                "resource_type": item.resource_type,
                "authority": item.authority_level,
            }
            for index, item in enumerate(resource_candidates, start=1)
        ]
        resource_candidate_map = {
            f"RESOURCE_CANDIDATE_{index}": item.evidence_id
            for index, item in enumerate(resource_candidates, start=1)
        }
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
                            "evidence": (
                                semantic_evidence
                                if (summary_items or not retrieval_summary)
                                else []
                            ),
                            "candidate_questions": [
                                {
                                    "question_id": item["question_id"],
                                    "question_type": item["question_type"],
                                    "tags": item["tags"],
                                    "kp_ids": item["kp_ids"],
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
                                "formal_learning_task": (
                                    acceptance_policy.formal_learning_task
                                    if acceptance_policy.formal_task_available
                                    else None
                                ),
                            },
                            "personalization": acceptance_policy.learner_fit_facts,
                            "acceptance_policy": acceptance_policy.model_dump(mode="json"),
                            "candidate_resources": candidate_resources,
                            **(
                                {"repair_request": {
                                    "issue_ids": repair_instruction.get("issue_ids", []),
                                    "locations": repair_instruction.get("locations", []),
                                    "instruction": repair_instruction.get(
                                        "repair_instruction", ""
                                    ),
                                    "audit_findings": [
                                        str(item)[:600]
                                        for item in list(feedback_findings)[:8]
                                    ],
                                    "previous_resource": previous_resource,
                                }}
                                if repair_instruction
                                else {}
                            ),
                            "output_contract": {
                                "body": "面向学习者直接生成完整知识卡正文，使用自然语言，不要嵌套结构。",
                                "learning_tip": "可选的一句学习动作提示。",
                                "use_question_candidates": "是否使用候选题。",
                                "selected_question_ids": "只能填写 candidate_questions 中的 ID。",
                                "selected_resource_candidate_ids": (
                                    "只能填写 candidate_resources 中适合当前用户偏好、学情、任务和时间预算的 candidate_id；"
                                    "没有合适项时返回空数组。"
                                ),
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
                    "selected_resource_candidate_ids": raw_output.get(
                        "selected_resource_candidate_ids"
                    ) or [],
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
                safe_ids: list[str] = []
                model_output = ExpertModelOutput.model_validate({
                    "learning_tip": fallback_tip,
                    "use_question_candidates": False,
                    "usage_reason": "模型选择合同不可用，系统不自动追加候选资源。",
                    "selected_question_ids": safe_ids,
                    "selected_resource_candidate_ids": [],
                    "resource_type": "none",
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
        if not paper_generation:
            # Resource selection belongs to Expert.  The materializer must not
            # silently add candidates the model explicitly declined, otherwise
            # an Audit repair can never remove an unsuitable question.
            uses_questions = bool(model_output.use_question_candidates and selected_ids)
            if not uses_questions:
                selected_ids = []
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
        training_focus = list(
            dict.fromkeys(
                str(tag).strip()
                for item in selected_questions
                for tag in item.get("tags") or []
                if str(tag).strip()
            )
        )[:6]
        selected_resource_ids = [
            resource_candidate_map[item]
            for item in dict.fromkeys(model_output.selected_resource_candidate_ids)
            if item in resource_candidate_map
        ]
        video_resources = [
            {
                "evidence_id": item.evidence_id,
                "title": item.source_id,
                "summary": item.content_summary,
                "url": item.source_url,
                "resource_type": item.resource_type,
            }
            for item in evidence_pack.evidence_items
            if item.evidence_id in selected_resource_ids
            and item.resource_type in {"video", "reference"}
            and item.source_url
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
            learning_prompt = model_output.learning_tip or (
                f"【本次目标】围绕{topic}完成主动回忆。\n"
                "【执行步骤】先闭卷复述，再对照正文自查并提交反馈。"
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
                    {key: value for key, value in item.items() if key != "evidence_id"}
                    for item in video_resources if item["resource_type"] == "video"
                ],
                "参考资料": [
                    {key: value for key, value in item.items() if key != "evidence_id"}
                    for item in video_resources if item["resource_type"] == "reference"
                ],
                "练习资源": [],
            }
        if selected_questions:
            content["练习资源"] = selected_questions
        draft = ResourceDraft(
            resource_draft_id=f"DRAFT_{uuid4().hex}",
            title=(
                f"{topic}试卷蓝图"
                if paper_generation
                else f"{topic}个性化复习卡"
            ),
            content=content,
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
            provenance=ResourceProvenance(
                question_origin=("formal_candidate" if selected_ids else "none"),
                selected_question_ids=selected_ids,
                selected_evidence_ids=[primary_evidence.evidence_id],
                selected_video_evidence_ids=[
                    item["evidence_id"]
                    for item in video_resources
                    if item["resource_type"] == "video"
                ],
                selected_reference_evidence_ids=[
                    item["evidence_id"]
                    for item in video_resources
                    if item["resource_type"] == "reference"
                ],
                generated_sections=["知识卡片", "学习提示"],
                materialized_sections=[
                    *(["练习资源"] if selected_ids else []),
                    *(["视频资源"] if any(item["resource_type"] == "video" for item in video_resources) else []),
                    *(["参考资料"] if any(item["resource_type"] == "reference" for item in video_resources) else []),
                ],
            ),
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

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from competition_app.agents.common import envelope
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.default_route import ResolvedPlanningRoute
from competition_app.contracts.learning_plan import (
    GoalContract,
    LearningPlanProposal,
    LearningTaskProposal,
    LongTermPlanStage,
    PlanMilestone,
    RecommendationTrace,
    RecoveryPolicy,
    ShortTermFocusContext,
    ShortTermLearningPackage,
    TextbookSelectionContext,
)
from competition_app.contracts.review import DailyReviewPolicy
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.stub import StubChatModel
from competition_app.llm.schemas import (
    DailyTaskPlanningModelOutput,
    FORBIDDEN_OBJECTIVE_FIELDS,
    DiagnosisStandardOutput,
    LearningAnalysisModelOutput,
    LongTermPlanningModelOutput,
    NaturalLanguageLearningAnalysisModelOutput,
    ShortTermPlanningModelOutput,
    ThreeLayerPlanningModelOutput,
    validate_training_style_output,
)
from competition_app.services.plan_change_gate import PlanChangeGate
from competition_app.services.planning_validator import PlanningValidator
from competition_app.services.profile_readiness import ProfileReadinessService


class DiagnosisResult(BaseModel):
    summary: str = "系统基于当前任务生成初始学习状态。"
    risk_flags: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    stage_id: str = "T0"
    weak_kp_ids: list[str] = Field(default_factory=lambda: ["KP_FJ_001"])
    target_difficulty: int = 2
    daily_review_policy: DailyReviewPolicy = Field(
        default_factory=lambda: DailyReviewPolicy(capacity=1, target_difficulty=2)
    )
    learning_plan_proposal: LearningPlanProposal | None = None
    requires_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    clarification_reason: str | None = None
    clarification_fields: list[str] = Field(default_factory=list)
    interrupt_type: str | None = None
    plan_scope: str | None = None


class DiagnosisAgent:
    def __init__(self, chat_model: ChatModel | None = None) -> None:
        self.chat_model = chat_model or StubChatModel()

    async def run(self, context: dict[str, Any]) -> AgentEnvelope[DiagnosisResult]:
        dependency_outputs = context.get("dependency_outputs", {})
        knowledge_output = dependency_outputs.get("knowledge")
        knowledge = getattr(knowledge_output, "payload", None)
        knowledge_query = getattr(knowledge, "query", "")
        evidence_items = getattr(knowledge, "evidence_items", [])
        resolved_kp_ids = getattr(knowledge, "resolved_kp_ids", [])
        route_output = dependency_outputs.get("route_resolution")
        resolved_route = getattr(route_output, "payload", None)
        plan_scope = context.get("plan_scope")
        parent_route = self._parent_planning_route(context, plan_scope)
        if parent_route is not None:
            resolved_route = parent_route
        if resolved_route is None:
            resolved_route = self._provisional_route_fallback(context)
        route_context = self._trusted_route_context(resolved_route)
        task_type = str(context.get("task_type", "learning_plan"))
        prompt_skill = prompt_skill_registry.load("diagnosis_agent", task_type)
        user_profile = context.get("user_profile", {})
        learning_profile = context.get("learning_profile", {})
        system_data = context.get("system_data", {})
        learning_goals = user_profile.get("goals") or user_profile.get("learning_goals", [])
        current_status = learning_profile.get("current_status", {})
        behavior_metrics = system_data or learning_profile.get("behavior_metrics", {})
        confirmed_prerequisite_courses = self._confirmed_prerequisite_courses(
            context, route_context
        )
        user_knowledge_states = context.get("user_knowledge_states")
        if user_knowledge_states is None:
            singular_knowledge_state = context.get("user_knowledge_state")
            user_knowledge_states = (
                [singular_knowledge_state]
                if isinstance(singular_knowledge_state, dict)
                else singular_knowledge_state or []
            )
        target_difficulty = int(system_data.get("target_difficulty", 2))
        if task_type == "learning_plan":
            scope_clarification = self._plan_scope_clarification(
                plan_scope,
                context.get("current_long_term_plan"),
                context.get("current_short_term_plan"),
            )
            if scope_clarification is not None:
                questions, reason = scope_clarification
                result = DiagnosisResult(
                    summary=reason,
                    stage_id=str(system_data.get("current_stage_id", "T0")),
                    weak_kp_ids=resolved_kp_ids,
                    target_difficulty=target_difficulty,
                    daily_review_policy=DailyReviewPolicy(
                        capacity=1, target_difficulty=target_difficulty
                    ),
                    requires_clarification=True,
                    clarification_questions=questions,
                    clarification_reason=reason,
                    plan_scope=plan_scope,
                )
                return envelope(context, "diagnosis_agent", "diagnosis_result", result)
            if context.get("enforce_profile_readiness"):
                readiness = ProfileReadinessService().evaluate(context, plan_scope)
                if not readiness.can_proceed:
                    result = DiagnosisResult(
                        summary="制定长期规划前，需要先补齐最少量的个性化信息。",
                        stage_id=str(system_data.get("current_stage_id", "T0")),
                        weak_kp_ids=resolved_kp_ids,
                        target_difficulty=target_difficulty,
                        daily_review_policy=DailyReviewPolicy(
                            capacity=1, target_difficulty=target_difficulty
                        ),
                        requires_clarification=True,
                        clarification_questions=readiness.questions,
                        clarification_fields=[readiness.next_field]
                        if readiness.next_field
                        else [],
                        clarification_reason="长期阶段和教材选择需要与学习基础、目标和可持续时间预算联动。",
                        interrupt_type="profile_completion",
                        plan_scope=plan_scope,
                    )
                    return envelope(context, "diagnosis_agent", "diagnosis_result", result)
        textbook_context = route_context.get("textbook_route") or {}
        if route_context.get("match_reason") == "agent_requires_clarification":
            questions = self._string_list(route_context.get("unknowns_to_confirm"))
            result = DiagnosisResult(
                summary="需要先确认学习目的，再选择长期教材路线。",
                stage_id=str(system_data.get("current_stage_id", "T0")),
                weak_kp_ids=resolved_kp_ids,
                target_difficulty=target_difficulty,
                daily_review_policy=DailyReviewPolicy(
                    capacity=1, target_difficulty=target_difficulty
                ),
                requires_clarification=True,
                clarification_questions=questions,
                clarification_reason="当前信息不足以区分课程、考试、升学或专业方向。",
                plan_scope=plan_scope,
            )
            return envelope(context, "diagnosis_agent", "diagnosis_result", result)
        if textbook_context.get("planning_status") == "needs_clarification":
            questions = self._string_list(
                textbook_context.get("clarification_questions")
            )
            result = DiagnosisResult(
                summary="需要先确认具体考试目标，再绑定教材主线。",
                stage_id=str(system_data.get("current_stage_id", "T0")),
                weak_kp_ids=resolved_kp_ids,
                target_difficulty=target_difficulty,
                daily_review_policy=DailyReviewPolicy(
                    capacity=1, target_difficulty=target_difficulty
                ),
                requires_clarification=True,
                clarification_questions=questions,
                clarification_reason="教材方向已识别，但具体考试身份仍不明确。",
                plan_scope=plan_scope,
            )
            return envelope(context, "diagnosis_agent", "diagnosis_result", result)
        change_decision = PlanChangeGate().decide(
            user_request=str(context.get("user_request", "")),
            current_long_term_plan=context.get("current_long_term_plan"),
            current_short_term_plan=context.get("current_short_term_plan"),
            explicit_long_term_change=bool(context.get("explicit_long_term_change")),
            explicit_short_term_change=bool(context.get("explicit_short_term_change")),
            sustained_learning_change=bool(context.get("sustained_learning_change")),
            route_changed=bool(context.get("route_changed")),
            single_performance_change=bool(context.get("single_performance_change")),
        )
        if task_type == "personalized_review_card":
            change_decision = change_decision.model_copy(update={
                "long_term_action": "reuse",
                "short_term_action": "reuse",
                "daily_task_action": "update",
                "reason": "复习卡请求只生成当日复习任务，不重写长短期计划。",
            })
        if change_decision.requires_clarification:
            result = DiagnosisResult(
                summary="需要先澄清重规划范围和变化事实。",
                stage_id=str(system_data.get("current_stage_id", "T0")),
                weak_kp_ids=resolved_kp_ids,
                target_difficulty=target_difficulty,
                daily_review_policy=DailyReviewPolicy(
                    capacity=1, target_difficulty=target_difficulty
                ),
                requires_clarification=True,
                clarification_questions=change_decision.clarification_questions,
                clarification_reason=change_decision.reason,
                plan_scope=plan_scope,
            )
            return envelope(context, "diagnosis_agent", "diagnosis_result", result)
        if task_type == "learning_plan" and plan_scope in {
            "long_term", "short_term", "daily_task"
        }:
            scoped_actions = {
                "long_term": ("update", "reuse", "reuse"),
                "short_term": ("reuse", "update", "reuse"),
                "daily_task": ("reuse", "reuse", "update"),
            }
            long_action, short_action, daily_action = scoped_actions[plan_scope]
            change_decision = change_decision.model_copy(update={
                "long_term_action": long_action,
                "short_term_action": short_action,
                "daily_task_action": daily_action,
                "reason": f"本次只生成{plan_scope}层，其他层不由模型改写。",
            })

        model_textbook_context = self._model_textbook_context(route_context)
        memory_output = context.get("dependency_outputs", {}).get("memory")
        memory_payload = getattr(memory_output, "payload", None)
        memory_context_summary = getattr(memory_payload, "context_summary", None)
        planning_payload = {
            "plan_scope": plan_scope,
            "user_request": str(context.get("user_request", "")),
            "compressed_conversation_summary": str(
                getattr(memory_context_summary, "summary", "") or ""
            ),
            "goals": learning_goals,
            "time_constraints": {
                "available_minutes_today": context.get("available_minutes"),
                "preferences": user_profile.get("user_preference", {}),
            },
            "learning_evidence": {
                "current_status": current_status,
                "behavior_summary": behavior_metrics,
                "retrieval_summary": getattr(knowledge, "retrieval_summary", ""),
                "evidence_summaries": [item.content_summary for item in evidence_items[:3]],
                "confirmed_prerequisite_courses": sorted(
                    confirmed_prerequisite_courses
                ),
            },
            "default_route": {
                "planning_status": route_context.get("planning_status"),
                "goal_type": route_context.get("goal_type"),
                "goal_name": route_context.get("goal_name"),
                "phases": (
                    []
                    if model_textbook_context
                    else [
                        {
                            key: phase.get(key)
                            for key in (
                                "name", "objective", "books", "learning_focus",
                                "sequence_basis", "exit_evidence",
                            )
                        }
                        for phase in self._planning_phases(route_context)
                    ]
                ),
                "textbook_route": model_textbook_context,
                "assumptions": route_context.get("assumptions", []),
                "unknowns_to_confirm": route_context.get("unknowns_to_confirm", []),
            },
            "existing_plans": self._model_existing_plans(context, plan_scope),
            "plan_actions": change_decision.model_dump(),
            "output_schema": self._planning_output_schema(plan_scope),
        }
        try:
            raw_output = await self.chat_model.complete_json(
                "diagnosis_agent",
                build_model_context(
                    context,
                    target_agent="diagnosis_agent",
                    prompt_skill=prompt_skill,
                    payload=planning_payload,
                    permission_note=(
                        "只可生成 plan_scope 指定的个人规划层及该层必要的结构化语义；"
                        "route_id、route_version、route_status、planning_status 由 Resolver 拥有，"
                        "只能复述且不得修改；不得生成用户事实、知识点ID、系统时间、计划ID或持久化状态。"
                    ),
                ),
            )
            raw_dict = raw_output if isinstance(raw_output, dict) else {}
            three_layer: ThreeLayerPlanningModelOutput | None = None
            if plan_scope in {"long_term", "short_term", "daily_task"}:
                three_layer = self._expand_scoped_planning_output(
                    plan_scope,
                    raw_dict,
                    context,
                    route_context,
                )
            elif "daily_task_content" in raw_dict:
                three_layer = ThreeLayerPlanningModelOutput.model_validate(
                    self._normalize_unscoped_planning_output(raw_dict, route_context)
                )
            if three_layer is not None:
                validation = PlanningValidator().validate(
                    three_layer,
                    resolved_route,
                    available_minutes=context.get("available_minutes"),
                    long_term_action=change_decision.long_term_action,
                    short_term_action=change_decision.short_term_action,
                    daily_task_action=change_decision.daily_task_action,
                    confirmed_prerequisite_courses=confirmed_prerequisite_courses,
                )
                if not validation.valid:
                    revision_payload = {
                        **planning_payload,
                        "previous_output": three_layer.model_dump(),
                        "revision_issues": validation.issues,
                        "revision_instruction": (
                            "只修正列出的问题，并仍然只返回 plan_scope 指定的当前规划层。"
                            if plan_scope in {"long_term", "short_term", "daily_task"}
                            else "只修正列出的问题并返回完整三层输出。"
                        ),
                    }
                    revised_raw = await self.chat_model.complete_json(
                        "diagnosis_agent",
                        build_model_context(
                            context,
                            target_agent="diagnosis_agent",
                            prompt_skill=prompt_skill,
                            payload=revision_payload,
                            permission_note=(
                                "仅修订 plan_scope 指定的当前规划层；不得生成系统ID或修改默认路线。"
                                if plan_scope in {"long_term", "short_term", "daily_task"}
                                else "仅修订三层规划正文；不得生成系统ID或修改默认路线。"
                            ),
                        ),
                    )
                    three_layer = (
                        self._expand_scoped_planning_output(
                            plan_scope,
                            revised_raw if isinstance(revised_raw, dict) else {},
                            context,
                            route_context,
                        )
                        if plan_scope in {"long_term", "short_term", "daily_task"}
                        else ThreeLayerPlanningModelOutput.model_validate(
                            self._normalize_unscoped_planning_output(
                                revised_raw if isinstance(revised_raw, dict) else {},
                                route_context,
                            )
                        )
                    )
                    validation = PlanningValidator().validate(
                        three_layer,
                        resolved_route,
                        available_minutes=context.get("available_minutes"),
                        long_term_action=change_decision.long_term_action,
                        short_term_action=change_decision.short_term_action,
                        daily_task_action=change_decision.daily_task_action,
                        confirmed_prerequisite_courses=confirmed_prerequisite_courses,
                    )
                    if not validation.valid:
                        clarification_questions = self._selection_clarification_questions(
                            validation.issues
                        )
                        if clarification_questions:
                            if context.get("terminal_trace"):
                                context["terminal_trace"].validation(
                                    "diagnosis_agent",
                                    valid=True,
                                    detail="textbook_selection_clarification",
                                )
                            result = DiagnosisResult(
                                summary="需要先确认前置课程掌握情况，再选择当前教材阶段。",
                                stage_id=str(system_data.get("current_stage_id", "T0")),
                                weak_kp_ids=resolved_kp_ids,
                                target_difficulty=target_difficulty,
                                daily_review_policy=DailyReviewPolicy(
                                    capacity=1,
                                    target_difficulty=target_difficulty,
                                ),
                                requires_clarification=True,
                                clarification_questions=clarification_questions,
                                clarification_reason="模型选择的阶段需要尚未确认的强前置课程。",
                                plan_scope=plan_scope,
                            )
                            return envelope(
                                context,
                                "diagnosis_agent",
                                "diagnosis_result",
                                result,
                            )
                        raise ValueError(
                            "三层规划修订后仍未通过校验：" + "; ".join(validation.issues)
                        )
            standard = DiagnosisStandardOutput.model_validate(raw_dict)
            natural_language_keys = {
                "summary",
                "long_term_plan_content",
                "short_term_plan_content",
                "learning_task",
                "long_term_plan_action",
                "short_term_plan_action",
                "priority_mode",
                "adjustment_reason",
            }
            if three_layer is not None:
                output = None
            elif natural_language_keys.intersection(raw_dict):
                natural_language_output = validate_training_style_output(
                    NaturalLanguageLearningAnalysisModelOutput,
                    self._adapt_natural_language_output(raw_dict, context),
                    [],
                )
                output = LearningAnalysisModelOutput.model_validate(
                    natural_language_output.model_dump()
                )
            else:
                output = self._parse_standard_output(standard, context)
        except ValueError as exc:
            if context.get("terminal_trace"):
                context["terminal_trace"].validation("diagnosis_agent", valid=False, detail=str(exc))
            raise
        if context.get("terminal_trace"):
            context["terminal_trace"].validation(
                "diagnosis_agent",
                valid=True,
                detail=(
                    "ThreeLayerPlanningModelOutput"
                    if three_layer is not None
                    else "LearningAnalysisModelOutput"
                ),
            )
        result = DiagnosisResult(
            summary=(
                "三层学习规划已生成并通过校验。"
                if three_layer is not None
                else output.summary
            ),
            risk_flags=[] if three_layer is not None else output.risk_flags,
            recommendations=[] if three_layer is not None else output.recommendations,
            uncertainty=[] if three_layer is not None else output.uncertainty,
            # IDs, stage, and difficulty are system facts; the model only proposes semantic content.
            stage_id=str(system_data.get("current_stage_id", "T0")),
            weak_kp_ids=resolved_kp_ids,
            target_difficulty=target_difficulty,
            daily_review_policy=DailyReviewPolicy(
                capacity=1,
                target_difficulty=target_difficulty,
            ),
            learning_plan_proposal=(
                None
                if task_type == "personalized_review_card"
                and not context.get("requires_learning_plan_output")
                else self._build_three_layer_proposal(
                    three_layer, resolved_route, context, change_decision
                )
                if three_layer is not None
                else self._build_plan_proposal(output, resolved_route, context)
            ),
            plan_scope=plan_scope,
        )
        return envelope(context, "diagnosis_agent", "diagnosis_result", result)

    @staticmethod
    def _planning_output_schema(plan_scope: Any) -> dict[str, Any]:
        schemas = {
            "long_term": LongTermPlanningModelOutput,
            "short_term": ShortTermPlanningModelOutput,
            "daily_task": DailyTaskPlanningModelOutput,
        }
        return schemas.get(plan_scope, ThreeLayerPlanningModelOutput).model_json_schema()

    @classmethod
    def _normalize_unscoped_planning_output(
        cls,
        raw_output: dict[str, Any],
        route_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Repair empty model-owned book arrays from the trusted route before parsing."""

        normalized = dict(raw_output)
        stages = normalized.get("long_term_plan_stages")
        if not isinstance(stages, list):
            return normalized
        trusted = list(cls._planning_phases(route_context))
        repaired = []
        for position, value in enumerate(stages):
            if not isinstance(value, dict):
                repaired.append(value)
                continue
            stage = dict(value)
            try:
                stage_index = max(0, int(stage.get("stage", position + 1)) - 1)
            except (TypeError, ValueError):
                stage_index = position
            source = trusted[stage_index] if stage_index < len(trusted) else {}
            if not cls._string_list(stage.get("book")):
                stage["book"] = cls._string_list(source.get("books")) or ["待确认教材"]
            if not str(stage.get("goal") or "").strip() and source:
                stage["goal"] = str(source.get("objective") or "完成本阶段目标")
            repaired.append(stage)
        normalized["long_term_plan_stages"] = repaired
        return normalized

    @classmethod
    def _expand_scoped_planning_output(
        cls,
        plan_scope: str,
        raw_output: dict[str, Any],
        context: dict[str, Any],
        route_context: dict[str, Any],
    ) -> ThreeLayerPlanningModelOutput:
        current_long = context.get("current_long_term_plan") or {}
        current_short = context.get("current_short_term_plan") or {}
        current_task = context.get("current_learning_task") or {}

        def field(value: Any, name: str, default: Any = None) -> Any:
            if isinstance(value, dict):
                return value.get(name, default)
            return getattr(value, name, default)

        trusted_stages = [
            {
                "stage": index,
                "book": cls._string_list(phase.get("books")) or ["待确认教材"],
                "goal": str(phase.get("objective") or "完成本阶段目标"),
            }
            for index, phase in enumerate(cls._planning_phases(route_context), start=1)
        ] or [{"stage": 1, "book": ["待确认教材"], "goal": "完成当前长期阶段"}]

        selection_source = (
            field(current_short, "textbook_selection")
            or field(current_long, "textbook_selection")
            or {}
        )
        selection = {
            "selected_textbook_route_id": field(selection_source, "route_id"),
            "selected_stage_id": field(selection_source, "stage_id"),
            "selected_books": list(field(selection_source, "books", []) or []),
            "selection_reason": field(selection_source, "reason"),
        }
        textbook_resolution = route_context.get("textbook_route") or {}
        textbook_route = (
            textbook_resolution.get("route")
            if textbook_resolution.get("planning_status") == "resolved"
            else None
        ) or {}
        textbook_stages = list(textbook_route.get("stages") or [])
        if textbook_route.get("route_id") and textbook_stages:
            first_stage = textbook_stages[0]
            selection = {
                "selected_textbook_route_id": selection["selected_textbook_route_id"]
                or textbook_route.get("route_id"),
                "selected_stage_id": selection["selected_stage_id"]
                or first_stage.get("stage_id"),
                "selected_books": selection["selected_books"]
                or list(first_stage.get("books") or [])[:2],
                "selection_reason": selection["selection_reason"]
                or "沿用当前规划层已确认的教材阶段。",
            }

        available_minutes = context.get("available_minutes")
        fallback_minutes = (
            min(10, available_minutes)
            if isinstance(available_minutes, int) and available_minutes > 0
            else 10
        )
        common = {
            "long_term_plan_content": str(
                field(current_long, "content") or "本次未生成长期规划。"
            ),
            "short_term_plan_content": str(
                field(current_short, "content") or "本次未生成短期计划。"
            ),
            "daily_task_content": str(
                field(current_task, "task_content") or "本次未生成当日任务。"
            ),
            "estimated_minutes": int(field(current_task, "estimated_minutes") or fallback_minutes),
            "expected_output": str(field(current_task, "expected_output") or "本层规划结果。"),
            "completion_criteria": str(
                field(current_task, "completion_criteria") or "完成本层规划要求。"
            ),
            "long_term_plan_stages": trusted_stages,
            **selection,
        }
        if plan_scope == "long_term":
            scoped_input = {
                key: value
                for key, value in raw_output.items()
                if key in LongTermPlanningModelOutput.model_fields
            }
            # The field remains visible at the model boundary, but the trusted
            # route is authoritative even when the model omits or alters it.
            scoped_input["long_term_plan_stages"] = trusted_stages
            scoped = LongTermPlanningModelOutput.model_validate(scoped_input)
            common["long_term_plan_content"] = cls._complete_plan_sections(
                scoped.long_term_plan_content,
                (
                    "【最终目标】",
                    "【能力路径与阶段】",
                    "【阶段里程碑】",
                    "【资源预算】",
                    "【重规划条件】",
                    "【保温底线】",
                ),
            )
        elif plan_scope == "short_term":
            scoped = ShortTermPlanningModelOutput.model_validate({
                key: value
                for key, value in raw_output.items()
                if key in ShortTermPlanningModelOutput.model_fields
            })
            common.update(scoped.model_dump())
            common.update({
                "daily_task_content": "本次仅制定短期计划；当日任务需另行安排。",
                "estimated_minutes": fallback_minutes,
            })
        else:
            scoped = DailyTaskPlanningModelOutput.model_validate({
                key: value
                for key, value in raw_output.items()
                if key in DailyTaskPlanningModelOutput.model_fields
            })
            common.update(scoped.model_dump())
        return ThreeLayerPlanningModelOutput.model_validate(common)

    @staticmethod
    def _parent_planning_route(context: dict[str, Any], plan_scope: Any) -> Any:
        parent = (
            context.get("current_long_term_plan")
            if plan_scope == "short_term"
            else context.get("current_short_term_plan")
            if plan_scope == "daily_task"
            else None
        )
        if isinstance(parent, dict):
            return parent.get("planning_route")
        return getattr(parent, "planning_route", None)

    @classmethod
    def _plan_scope_clarification(
        cls,
        plan_scope: Any,
        current_long_term_plan: Any,
        current_short_term_plan: Any,
    ) -> tuple[list[str], str] | None:
        if plan_scope == "unspecified":
            return (
                ["这次需要制定长期规划、短期计划或当日任务中的哪一层？"],
                "学习计划需要按层分别制定。",
            )
        if plan_scope == "short_term" and not cls._has_plan_content(current_long_term_plan):
            return (
                ["当前还没有有效长期规划，是否先建立长期规划？"],
                "短期计划必须基于有效长期规划制定。",
            )
        if plan_scope == "daily_task" and not cls._has_plan_content(current_short_term_plan):
            return (
                ["当前还没有有效短期计划，是否先制定短期计划？"],
                "当日任务必须基于有效短期计划制定。",
            )
        return None

    @classmethod
    def _build_three_layer_proposal(
        cls,
        output: ThreeLayerPlanningModelOutput,
        resolved_route: Any,
        context: dict[str, Any],
        change_decision: Any,
    ) -> LearningPlanProposal:
        route_context = cls._trusted_route_context(resolved_route)
        planning_phases = cls._planning_phases(route_context)
        trusted_route = (
            ResolvedPlanningRoute.model_validate(route_context) if route_context else None
        )
        long_content = cls._trusted_reuse_content(
            change_decision.long_term_action,
            context.get("current_long_term_plan"),
            output.long_term_plan_content,
        )
        if change_decision.long_term_action != "reuse":
            long_content = cls._replace_route_milestone_section(
                long_content, route_context
            )
        short_content = cls._trusted_reuse_content(
            change_decision.short_term_action,
            context.get("current_short_term_plan"),
            output.short_term_plan_content,
        )
        short_content = cls._remove_time_allocation_section(short_content)
        daily_content = cls._trusted_reuse_task_content(
            change_decision.daily_task_action,
            context.get("current_learning_task"),
            output.daily_task_content,
        )
        if context.get("plan_scope") == "short_term":
            daily_content = output.daily_task_content
        if route_context.get("planning_status") == "provisional":
            if change_decision.long_term_action != "reuse" and "临时规划" not in long_content:
                long_content = "【临时规划】" + long_content
            if change_decision.short_term_action != "reuse" and "临时规划" not in short_content:
                short_content = "【临时规划】" + short_content
        available_minutes = context.get("available_minutes")
        estimated_minutes = output.estimated_minutes
        if (
            isinstance(available_minutes, int)
            and not isinstance(available_minutes, bool)
            and available_minutes > 0
        ):
            estimated_minutes = min(estimated_minutes, available_minutes)
        assumptions = cls._string_list(route_context.get("assumptions"))
        unknowns = cls._string_list(route_context.get("unknowns_to_confirm"))
        milestones = [
            PlanMilestone(
                milestone_id=str(phase.get("phase_id") or f"PHASE_{index}"),
                name=str(phase.get("name") or f"阶段{index}"),
                success_criteria=str(phase.get("objective") or "完成本阶段目标"),
                evidence_required=cls._string_list(phase.get("exit_evidence"))
                or ["提交阶段学习证据"],
            )
            for index, phase in enumerate(planning_phases, start=1)
        ]
        trusted_phases = planning_phases
        long_term_stages = (
            [
                LongTermPlanStage(
                    stage=index,
                    book=cls._string_list(phase.get("books")) or ["待确认教材"],
                    goal=str(phase.get("objective") or "完成本阶段目标"),
                )
                for index, phase in enumerate(trusted_phases, start=1)
            ]
            if trusted_phases
            else [
                LongTermPlanStage(
                    stage=1,
                    book=["待确认教材"],
                    goal="完成当前长期学习阶段目标",
                )
            ]
        )
        short_term_package = ShortTermLearningPackage(
            time_window_weeks=cls._short_term_window_weeks(context),
            current_goal=short_content,
            task_blocks=(
                cls._short_term_task_blocks(short_content)
                if context.get("plan_scope") == "short_term"
                else [daily_content]
            ),
            maintenance_plan=(
                "保留一个短时、可验证的长期主线维护动作。"
                if cls._system_priority_mode(context) == "temporary_focus"
                else None
            ),
            expected_output=output.expected_output,
            completion_criteria=output.completion_criteria,
        )
        textbook_selection = cls._textbook_selection(output, route_context)
        return LearningPlanProposal(
            long_term_plan_content=long_content,
            short_term_plan_content=short_content,
            long_term_plan_stages=long_term_stages,
            daily_task_content=daily_content,
            long_term_plan_action=change_decision.long_term_action,
            short_term_plan_action=change_decision.short_term_action,
            daily_task_action=change_decision.daily_task_action,
            priority_mode=cls._system_priority_mode(context),
            adjustment_reason=change_decision.reason,
            task_proposal=LearningTaskProposal(
                task_type="daily_learning",
                task_content=daily_content,
                estimated_minutes=estimated_minutes,
                expected_output=output.expected_output,
                completion_criteria=output.completion_criteria,
            ),
            planning_route=trusted_route,
            milestones=milestones,
            short_term_learning_package=short_term_package,
            recovery_policy=RecoveryPolicy(
                trigger_conditions=["连续任务未完成或阶段验收持续未通过"],
                recovery_actions=["降低负荷并在复盘后恢复长期主线"],
            ),
            assumptions=assumptions,
            unknowns_to_confirm=unknowns,
            short_term_focus=cls._build_short_term_focus(context),
            textbook_selection=textbook_selection,
        )

    @classmethod
    def _build_plan_proposal(
        cls,
        output: LearningAnalysisModelOutput,
        resolved_route: Any,
        context: dict[str, Any],
    ) -> LearningPlanProposal:
        route_context = cls._trusted_route_context(resolved_route)
        knowledge_output = context.get("dependency_outputs", {}).get("knowledge")
        knowledge_topic = str(
            getattr(getattr(knowledge_output, "payload", None), "query", "")
            or context.get("topic")
            or "当前学习主题"
        ).strip()
        model_route = output.route_context
        original_minutes = output.learning_task.estimated_minutes
        estimated_minutes = original_minutes
        available_minutes = context.get("available_minutes")
        if isinstance(available_minutes, int) and not isinstance(available_minutes, bool) and available_minutes > 0:
            estimated_minutes = min(estimated_minutes, available_minutes)

        def bounded_text(value: str) -> str:
            if estimated_minutes == original_minutes:
                return value
            return value.replace(
                f"{original_minutes}分钟",
                f"{estimated_minutes}分钟",
            )

        resolver_assumptions = cls._string_list(route_context.get("assumptions"))
        resolver_unknowns = cls._string_list(route_context.get("unknowns_to_confirm"))
        if route_context.get("planning_status") == "provisional":
            assumptions = cls._ordered_unique([
                *resolver_assumptions,
                *output.assumptions,
                *(model_route.assumptions if model_route else []),
            ])
            unknowns = cls._ordered_unique([
                *resolver_unknowns,
                *output.unknowns_to_confirm,
                *(model_route.unknowns_to_confirm if model_route else []),
            ])
        else:
            assumptions = resolver_assumptions
            unknowns = resolver_unknowns

        trusted_route = None
        if route_context:
            trusted_route = ResolvedPlanningRoute.model_validate({
                **route_context,
                "assumptions": assumptions,
                "unknowns_to_confirm": unknowns,
            })

        model_goal = output.goal_contract
        goal_contract = GoalContract(
            goal_type=str(
                route_context.get("goal_type")
                or (model_goal.goal_type if model_goal else "learning")
            ),
            goal_name=str(
                route_context.get("goal_name")
                or (model_goal.goal_name if model_goal else "当前学习目标")
            ),
            observable_ability=(
                model_goal.observable_ability
                if model_goal
                else f"能够完成当前任务：{output.learning_task.task_content}"
            ),
            acceptance_evidence=(
                model_goal.acceptance_evidence
                if model_goal
                else [output.learning_task.expected_output]
            ),
        )
        trusted_phases = cls._planning_phases(route_context)
        if route_context.get("planning_status") == "approved_route" and trusted_phases:
            milestones = [
                PlanMilestone(
                    milestone_id=str(phase["phase_id"]),
                    name=str(phase["name"]),
                    success_criteria=str(phase["objective"]),
                    evidence_required=cls._string_list(phase.get("exit_evidence", [])),
                )
                for phase in trusted_phases
            ]
        else:
            milestones = [
                PlanMilestone.model_validate(item.model_dump())
                for item in output.milestones
            ] or [
                PlanMilestone(
                    milestone_id="M1",
                    name="完成当前学习任务",
                    success_criteria=output.learning_task.completion_criteria,
                    evidence_required=[output.learning_task.expected_output],
                )
            ]
        short_term_package = (
            ShortTermLearningPackage.model_validate(
                {
                    **output.short_term_learning_package.model_dump(),
                    "current_goal": bounded_text(output.short_term_learning_package.current_goal),
                    "task_blocks": [
                        bounded_text(item)
                        for item in output.short_term_learning_package.task_blocks
                    ],
                    "expected_output": bounded_text(output.short_term_learning_package.expected_output),
                    "completion_criteria": bounded_text(output.short_term_learning_package.completion_criteria),
                }
            )
            if output.short_term_learning_package is not None
            else ShortTermLearningPackage(
                time_window_weeks=1,
                current_goal=output.learning_task.task_content,
                task_blocks=[output.learning_task.task_content],
                maintenance_plan=(
                    "保留一个短时、可验证的长期主线维护动作。"
                    if output.priority_mode == "temporary_focus"
                    else None
                ),
                expected_output=output.learning_task.expected_output,
                completion_criteria=output.learning_task.completion_criteria,
            )
        )
        recovery_policy = (
            RecoveryPolicy.model_validate(output.recovery_policy.model_dump())
            if output.recovery_policy is not None
            else RecoveryPolicy(
                trigger_conditions=["连续两次当前任务未达到完成标准"],
                recovery_actions=["降低单次负荷，复习缺口后恢复长期学习主线"],
            )
        )
        recommendation_trace = (
            RecommendationTrace(
                default_route=output.recommendation_trace.default_route,
                user_state=output.recommendation_trace.user_state,
                time_constraint=(
                    f"当前可用时间预算为{available_minutes}分钟。"
                    if isinstance(available_minutes, int)
                    and not isinstance(available_minutes, bool)
                    and available_minutes > 0
                    else output.recommendation_trace.time_constraint
                ),
                current_task=bounded_text(output.recommendation_trace.current_task),
            )
            if output.recommendation_trace is not None
            else RecommendationTrace(
                default_route=(
                    "遵循 Resolver 提供的已批准路线。"
                    if route_context.get("planning_status") == "approved_route"
                    else "当前没有已批准路线，按临时路径保守安排。"
                    if route_context.get("planning_status") == "provisional"
                    else "当前未提供路线解析结果，保留既有学习方向。"
                ),
                user_state=output.summary,
                time_constraint=(
                    f"当前可用时间预算为{available_minutes}分钟。"
                    if isinstance(available_minutes, int)
                    and not isinstance(available_minutes, bool)
                    and available_minutes > 0
                    else "当前时间预算未知，待用户确认。"
                ),
                current_task=bounded_text(output.learning_task.task_content),
            )
        )
        long_term_content = cls._trusted_reuse_content(
            output.long_term_plan_action,
            context.get("current_long_term_plan"),
            output.long_term_plan_content,
        )
        if output.long_term_plan_action != "reuse":
            long_term_content = cls._replace_route_milestone_section(
                long_term_content, route_context
            )
        short_term_content = cls._trusted_reuse_content(
            output.short_term_plan_action,
            context.get("current_short_term_plan"),
            output.short_term_plan_content,
        )
        has_reusable_long_term = cls._has_plan_content(
            context.get("current_long_term_plan")
        )
        if output.long_term_plan_action != "reuse" or not has_reusable_long_term:
            long_term_content = cls._inject_topic(long_term_content, knowledge_topic)
        if output.short_term_plan_action != "reuse":
            short_term_content = cls._inject_topic(short_term_content, knowledge_topic)
            if "两周" in str(context.get("user_request", "")) and "两周" not in short_term_content:
                short_term_content = short_term_content.replace(
                    "【当前主目标】", "【当前周期】未来两周。\n【当前主目标】", 1
                )
        short_term_package = cls._inject_package_topic(
            short_term_package,
            knowledge_topic,
            output.learning_task.task_content,
        )
        if output.short_term_plan_action != "reuse":
            short_term_content = cls._remove_time_allocation_section(
                bounded_text(short_term_content)
            )
        if route_context.get("planning_status") == "provisional":
            if output.long_term_plan_action != "reuse" and "临时规划" not in long_term_content:
                long_term_content = "【临时规划】" + long_term_content
            if output.short_term_plan_action != "reuse" and "临时规划" not in short_term_content:
                short_term_content = "【临时规划】" + short_term_content
        return LearningPlanProposal(
                long_term_plan_content=long_term_content,
                short_term_plan_content=short_term_content,
                long_term_plan_action=output.long_term_plan_action,
                short_term_plan_action=output.short_term_plan_action,
                priority_mode=output.priority_mode,
                adjustment_reason=output.adjustment_reason,
                task_proposal=LearningTaskProposal(
                    task_type=output.learning_task.task_type,
                    task_content=bounded_text(output.learning_task.task_content),
                    estimated_minutes=estimated_minutes,
                    expected_output=output.learning_task.expected_output,
                    completion_criteria=output.learning_task.completion_criteria,
                ),
                planning_route=trusted_route,
                goal_contract=goal_contract,
                milestones=milestones,
                short_term_learning_package=short_term_package,
                recovery_policy=recovery_policy,
                recommendation_trace=recommendation_trace,
                assumptions=assumptions,
                unknowns_to_confirm=unknowns,
                short_term_focus=cls._build_short_term_focus(context),
            )

    @classmethod
    def _trusted_route_context(cls, route: Any) -> dict[str, Any]:
        if route is None:
            return {}

        def field(name: str, default: Any = None) -> Any:
            if isinstance(route, dict):
                return route.get(name, default)
            return getattr(route, name, default)

        def serializable_list(name: str) -> list[Any]:
            values = field(name, []) or []
            return [
                item.model_dump() if hasattr(item, "model_dump")
                else dict(item) if isinstance(item, dict)
                else {
                    key: value
                    for key, value in vars(item).items()
                    if not key.startswith("_")
                }
                for item in values
            ]

        return {
            "goal_type": str(field("goal_type", "")),
            "goal_name": str(field("goal_name", "")),
            "planning_status": field("planning_status"),
            "match_reason": str(field("match_reason", "")),
            "route_id": field("route_id"),
            "route_version": field("route_version"),
            "route_status": field("route_status"),
            "planning_label": field("planning_label"),
            "phases": serializable_list("phases"),
            "sources": serializable_list("sources"),
            "assumptions": cls._string_list(field("assumptions", [])),
            "unknowns_to_confirm": cls._string_list(field("unknowns_to_confirm", [])),
            "runtime_checks": cls._string_list(field("runtime_checks", [])),
            "textbook_route": cls._serializable_value(field("textbook_route")),
        }

    @staticmethod
    def _serializable_value(value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if isinstance(value, dict):
            return dict(value)
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }

    @classmethod
    def _planning_phases(cls, route_context: dict[str, Any]) -> list[dict[str, Any]]:
        textbook_resolution = route_context.get("textbook_route") or {}
        textbook_route = textbook_resolution.get("route") or {}
        if (
            textbook_resolution.get("planning_status") == "resolved"
            and textbook_route.get("stages")
        ):
            return [
                {
                    "phase_id": stage.get("stage_id"),
                    "name": stage.get("name"),
                    "objective": stage.get("objective"),
                    "books": cls._string_list(stage.get("books")),
                    "learning_focus": [],
                    "sequence_basis": None,
                    "exit_evidence": cls._string_list(stage.get("exit_evidence")),
                }
                for stage in textbook_route.get("stages", [])
            ]
        return list(route_context.get("phases", []))

    @classmethod
    def _model_textbook_context(
        cls, route_context: dict[str, Any]
    ) -> dict[str, Any] | None:
        resolution = route_context.get("textbook_route") or {}
        route = resolution.get("route") or {}
        if resolution.get("planning_status") != "resolved" or not route:
            return None
        return {
            "route_id": route.get("route_id"),
            "route_version": route.get("route_version"),
            "goal_name": route.get("goal_name"),
            "stages": route.get("stages", []),
            "prerequisites": route.get("prerequisites", []),
            "equivalence_groups": route.get("equivalence_groups", []),
            "selection_rule": "结合用户情况选择一个阶段和该阶段内 1—2 本主教材。",
        }

    @classmethod
    def _model_existing_plans(
        cls, context: dict[str, Any], plan_scope: str | None
    ) -> dict[str, Any]:
        """Expose parent semantics, never persistence metadata, to the model."""

        def content_only(value: Any, *, task: bool = False) -> dict[str, Any]:
            if not value:
                return {}
            def field(name: str) -> Any:
                return (
                    value.get(name)
                    if isinstance(value, dict)
                    else getattr(value, name, None)
                )
            fields = (
                ("task_content", "estimated_minutes", "expected_output", "completion_criteria")
                if task
                else ("content",)
            )
            return {
                name: field(name)
                for name in fields
                if field(name) not in (None, "", [], {})
            }

        plans: dict[str, Any] = {}
        if plan_scope in {"long_term", "short_term", "daily_task", None}:
            plans["long_term"] = content_only(
                context.get("current_long_term_plan")
            )
        if plan_scope in {"short_term", "daily_task", None}:
            plans["short_term"] = content_only(
                context.get("current_short_term_plan")
            )
        if plan_scope in {"daily_task", None}:
            plans["daily_task"] = content_only(
                context.get("current_learning_task"), task=True
            )
        return {key: value for key, value in plans.items() if value}

    @classmethod
    def _textbook_selection(
        cls,
        output: ThreeLayerPlanningModelOutput,
        route_context: dict[str, Any],
    ) -> TextbookSelectionContext | None:
        resolution = route_context.get("textbook_route") or {}
        route = resolution.get("route") or {}
        if resolution.get("planning_status") != "resolved" or not route:
            return None
        stage = next(
            (
                item
                for item in route.get("stages", [])
                if item.get("stage_id") == output.selected_stage_id
            ),
            None,
        )
        if stage is None:
            return None
        return TextbookSelectionContext(
            route_id=str(route.get("route_id")),
            route_version=int(route.get("route_version")),
            stage_id=str(stage.get("stage_id")),
            stage_name=str(stage.get("name")),
            books=list(output.selected_books),
            reason=str(output.selection_reason),
        )

    @classmethod
    def _confirmed_prerequisite_courses(
        cls, context: dict[str, Any], route_context: dict[str, Any]
    ) -> set[str]:
        resolution = route_context.get("textbook_route") or {}
        route = resolution.get("route") or {}
        prerequisite_courses = {
            str(rule.get("course") or "").strip()
            for rule in route.get("prerequisites", [])
            if str(rule.get("course") or "").strip()
        }
        if not prerequisite_courses:
            return set()

        confirmed_texts: list[str] = []

        def collect(value: Any, confirmed_scope: bool = False) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    normalized_key = str(key).casefold()
                    child_scope = confirmed_scope or any(
                        marker in normalized_key
                        for marker in (
                            "completed", "passed", "mastered", "已完成", "已通过", "已掌握"
                        )
                    )
                    collect(item, child_scope)
            elif isinstance(value, (list, tuple, set)):
                for item in value:
                    collect(item, confirmed_scope)
            elif confirmed_scope and isinstance(value, str):
                confirmed_texts.append(value)

        for source in (
            context.get("user_profile"),
            context.get("learning_profile"),
            context.get("system_data"),
        ):
            collect(source)

        confirmed: set[str] = set()
        for course in prerequisite_courses:
            if any(course in text for text in confirmed_texts):
                confirmed.add(course)

        positive_markers = (
            "已完成", "已经完成", "已通过", "已经学完", "掌握了", "能够通过", "是的"
        )
        negative_markers = ("忘", "未完成", "没有完成", "没学", "不会", "未通过")

        def apply_statement(text: str, implied_courses: set[str] | None = None) -> None:
            candidates = {
                course for course in prerequisite_courses if course in text
            } | (implied_courses or set())
            for course in candidates:
                position = text.find(course)
                window = (
                    text[max(0, position - 16) : position + len(course) + 16]
                    if position >= 0
                    else text
                )
                if any(marker in window for marker in negative_markers):
                    confirmed.discard(course)
                elif any(marker in window for marker in positive_markers):
                    confirmed.add(course)

        pending_courses: set[str] = set()
        for message in context.get("messages") or []:
            if not isinstance(message, dict):
                continue
            text = str(message.get("content") or "")
            if message.get("role") == "assistant":
                pending_courses = {
                    course for course in prerequisite_courses if course in text
                }
            elif message.get("role") == "user":
                apply_statement(text, pending_courses)
                pending_courses = set()
        apply_statement(str(context.get("user_request") or ""))
        return confirmed

    @staticmethod
    def _short_term_task_blocks(content: str) -> list[str]:
        bracketed = re.search(
            r"【(?:具体任务块|本周期任务|任务块)】(.*?)(?=【[^】]+】|$)",
            content,
            flags=re.DOTALL,
        )
        markdown = re.search(
            r"(?ms)^#{1,6}\s*(?:具体任务块|本周期任务|任务块)\s*$\s*"
            r"(.*?)(?=^#{1,6}\s|\Z)",
            content,
        )
        extracted = (bracketed or markdown)
        task_text = extracted.group(1).strip() if extracted else content.strip()
        return [task_text]

    @staticmethod
    def _selection_clarification_questions(issues: list[str]) -> list[str]:
        questions: list[str] = []
        prefix = "所选阶段的强前置尚未确认："
        for issue in issues:
            if not issue.startswith(prefix):
                continue
            courses = issue.removeprefix(prefix).removesuffix("。").strip()
            if courses:
                questions.append(
                    f"你是否已完成或能够通过以下前置课程验收：{courses}？"
                    "如果学过但忘了，请说明目前能回忆或应用到什么程度。"
                )
        return questions

    @staticmethod
    def _inject_topic(content: str, topic: str) -> str:
        """Prevent a valid-looking but unusably generic plan."""
        normalized_topic = " ".join(topic.split())[:120]
        if not normalized_topic:
            return content
        return content.replace("当前主题", normalized_topic).replace(
            "本主题", normalized_topic
        )

    @staticmethod
    def _remove_time_allocation_section(content: str) -> str:
        cleaned = re.sub(
            r"【时间分配】.*?(?=【[^】]+】|$)",
            "",
            content,
            flags=re.DOTALL,
        )
        return re.sub(
            r"(?ms)^#{1,6}\s*时间分配\s*$.*?(?=^#{1,6}\s|\Z)",
            "",
            cleaned,
        ).strip()

    @classmethod
    def _replace_route_milestone_section(
        cls, content: str, route_context: dict[str, Any]
    ) -> str:
        phases = cls._planning_phases(route_context)
        if not phases:
            return content
        milestone_lines = []
        for index, phase in enumerate(phases, start=1):
            name = str(phase.get("name") or f"阶段{index}")
            objective = str(phase.get("objective") or "完成本阶段目标")
            evidence = "、".join(cls._string_list(phase.get("exit_evidence")))
            evidence = evidence or "提交可核验的阶段学习证据"
            milestone_lines.append(
                f"{index}. {name}：达到“{objective}”；验收证据为{evidence}。"
            )
        replacement = "【阶段里程碑】\n" + "\n".join(milestone_lines)
        bracket_pattern = r"【阶段里程碑】.*?(?=【[^】]+】|$)"
        if re.search(bracket_pattern, content, flags=re.DOTALL):
            return re.sub(
                bracket_pattern,
                replacement,
                content,
                count=1,
                flags=re.DOTALL,
            ).strip()
        markdown_pattern = (
            r"(?ms)^#{1,6}\s*阶段里程碑\s*$.*?(?=^#{1,6}\s|\Z)"
        )
        if re.search(markdown_pattern, content):
            return re.sub(markdown_pattern, replacement, content, count=1).strip()
        route_facts = [
            str(phase.get("name") or "")
            for phase in phases
        ] + [
            evidence
            for phase in phases
            for evidence in cls._string_list(phase.get("exit_evidence"))
        ]
        if all(fact in content for fact in route_facts if fact):
            return content
        return f"{content.rstrip()}\n\n{replacement}"

    @staticmethod
    def _short_term_window_weeks(context: dict[str, Any]) -> int:
        request = str(context.get("user_request") or "")
        return 2 if "两周" in request or "14天" in request else 1

    @classmethod
    def _inject_package_topic(
        cls, package: ShortTermLearningPackage, topic: str, task_content: str
    ) -> ShortTermLearningPackage:
        normalized_topic = " ".join(topic.split())[:120]
        if not normalized_topic:
            return package
        blocks = [
            cls._inject_topic(str(item), normalized_topic)
            for item in package.task_blocks
        ]
        if len(blocks) < 2:
            blocks = [
                task_content,
                f"{normalized_topic}：对照教材纠错，再完成一次复述或练习。",
            ]
        return package.model_copy(update={
            "current_goal": cls._inject_topic(package.current_goal, normalized_topic),
            "task_blocks": blocks,
            "expected_output": cls._inject_topic(package.expected_output, normalized_topic),
            "completion_criteria": cls._inject_topic(package.completion_criteria, normalized_topic),
        })

    @classmethod
    def _build_short_term_focus(
        cls, context: dict[str, Any]
    ) -> ShortTermFocusContext:
        knowledge_output = context.get("dependency_outputs", {}).get("knowledge")
        knowledge = getattr(knowledge_output, "payload", None)
        kp_ids = cls._string_list(getattr(knowledge, "resolved_kp_ids", []))
        query = str(getattr(knowledge, "query", "") or "").strip()
        user_profile = context.get("user_profile", {})
        goals = user_profile.get("goals", {}) if isinstance(user_profile, dict) else {}
        short_goal = (
            str(goals.get("short_term_goal", "")).strip()
            if isinstance(goals, dict)
            else ""
        )
        label = (
            query
            or str(context.get("topic") or "").strip()
            or short_goal
            or str(context.get("user_request") or "").strip()
            or "当前学习重点"
        )
        label = " ".join(label.split())[:120]
        states = context.get("user_knowledge_states") or []
        has_due_review = any(
            isinstance(item, dict)
            and (
                str(item.get("review_status", "")).casefold() == "due"
                or item.get("is_due") is True
            )
            for item in states
        )
        request = str(context.get("user_request") or "")
        if has_due_review:
            focus_type = "due_review"
        elif any(marker in request for marker in ("补弱", "薄弱", "错题", "纠错", "查漏")):
            focus_type = "remediation"
        elif any(marker in label for marker in ("专项", "专题", "类方", "章节", "模块", "体系")):
            focus_type = "special_topic"
        elif len(kp_ids) > 1:
            focus_type = "knowledge_cluster"
        elif len(kp_ids) == 1:
            focus_type = "knowledge_point"
        else:
            focus_type = "special_topic"
        return ShortTermFocusContext(
            focus_type=focus_type,
            focus_label=label,
            knowledge_point_ids=kp_ids,
        )

    @staticmethod
    def _system_priority_mode(context: dict[str, Any]) -> str:
        request = str(context.get("user_request") or "")
        if any(marker in request for marker in ("恢复学习", "恢复计划", "重新开始")):
            return "recovery"
        if any(marker in request for marker in ("集中", "专项", "补弱", "薄弱", "错题", "纠错")):
            return "temporary_focus"
        return "normal"

    @classmethod
    def _provisional_route_fallback(
        cls, context: dict[str, Any]
    ) -> ResolvedPlanningRoute:
        user_profile = context.get("user_profile")
        goals = user_profile.get("goals") if isinstance(user_profile, dict) else None
        first_goal = goals[0] if isinstance(goals, list) and goals else goals
        if not isinstance(first_goal, dict):
            first_goal = {}
        legacy_goals = (
            user_profile.get("learning_goals")
            if isinstance(user_profile, dict)
            else None
        )
        if isinstance(legacy_goals, list):
            legacy_goal = next(
                (str(item).strip() for item in legacy_goals if str(item).strip()),
                "",
            )
        else:
            legacy_goal = str(legacy_goals or "").strip()
        goal_type = str(
            first_goal.get("goal_type") or first_goal.get("type") or "learning"
        ).strip()
        goal_name = str(
            first_goal.get("goal_name")
            or first_goal.get("name")
            or legacy_goal
            or context.get("user_request")
            or context.get("topic")
            or "当前学习目标"
        ).strip()
        return ResolvedPlanningRoute(
            goal_type=goal_type or "learning",
            goal_name=goal_name or "当前学习目标",
            planning_status="provisional",
            match_reason="missing_route_resolution",
            assumptions=["旧调用未提供路线解析结果，暂按当前学习目标生成个人计划。"],
            unknowns_to_confirm=["默认学习路线及其适用版本尚待确认。"],
        )

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    @staticmethod
    def _ordered_unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value.strip()))

    @staticmethod
    def _trusted_reuse_content(action: str, current_plan: Any, proposed: str) -> str:
        if action != "reuse":
            return proposed
        if isinstance(current_plan, dict):
            content = current_plan.get("content")
        else:
            content = getattr(current_plan, "content", None)
        return str(content) if isinstance(content, str) and content else proposed

    @staticmethod
    def _trusted_reuse_task_content(action: str, current_task: Any, proposed: str) -> str:
        if action != "reuse":
            return proposed
        if isinstance(current_task, dict):
            content = current_task.get("task_content") or current_task.get("content")
        else:
            content = getattr(current_task, "task_content", None)
        return str(content) if isinstance(content, str) and content else proposed

    @staticmethod
    def _has_plan_content(plan: Any) -> bool:
        content = plan.get("content") if isinstance(plan, dict) else getattr(plan, "content", None)
        return isinstance(content, str) and bool(content.strip())

    @staticmethod
    def _adapt_model_output(raw_output: Any, context: dict[str, Any]) -> dict[str, Any]:
        raw = dict(raw_output) if isinstance(raw_output, dict) else {}
        allowed = {
            "summary", "diagnosis", "risk_flags", "risks", "recommendations",
            "uncertainty", "uncertainties", "long_term_plan_content", "long_term_plan",
            "short_term_plan_content", "short_term_plan", "long_term_plan_action",
            "short_term_plan_action", "priority_mode", "adjustment_reason",
            "route_context", "goal_contract", "milestones", "short_term_learning_package",
            "recovery_policy", "recommendation_trace", "assumptions",
            "unknowns_to_confirm", "learning_task", "next_task", "task_minutes",
            "expected_output", "completion_standard",
        }
        forbidden = FORBIDDEN_OBJECTIVE_FIELDS.intersection(raw)
        unknown = set(raw) - allowed
        task_raw = raw.get("learning_task")
        if isinstance(task_raw, dict):
            forbidden.update(FORBIDDEN_OBJECTIVE_FIELDS.intersection(task_raw))
        if forbidden:
            raise ValueError("training output contract forbids system-owned fields: " + ", ".join(sorted(forbidden)))
        if unknown:
            raise ValueError("training output contract forbids unknown fields: " + ", ".join(sorted(unknown)))

        def as_list(value: Any) -> list[str]:
            if isinstance(value, list):
                return [str(item) for item in value if str(item).strip()]
            return [value] if isinstance(value, str) and value.strip() else []

        def as_plan_text(value: Any) -> str:
            if isinstance(value, dict):
                return "".join(f"【{key}】{item}" for key, item in value.items())
            return str(value or "待用户确认")

        def normalize_plan_action(value: Any, default: str = "update") -> str:
            aliases = {
                "沿用": "reuse",
                "保持": "reuse",
                "复用": "reuse",
                "不变": "reuse",
                "制定": "update",
                "更新": "update",
                "调整": "update",
                "新建": "update",
            }
            normalized = aliases.get(str(value).strip(), str(value).strip())
            return normalized if normalized in {"reuse", "update"} else default

        def normalize_priority_mode(value: Any) -> str:
            aliases = {
                "首次复习": "temporary_focus",
                "首次主动回忆": "temporary_focus",
                "initial_recall": "temporary_focus",
                "重点关注": "temporary_focus",
                "恢复": "recovery",
                "恢复学习": "recovery",
                "正常": "normal",
            }
            normalized = aliases.get(str(value).strip(), str(value).strip())
            return normalized if normalized in {"normal", "temporary_focus", "recovery"} else "normal"

        task = raw.get("learning_task") if isinstance(raw.get("learning_task"), dict) else {}
        long_term_plan_content = raw.get("long_term_plan_content")
        long_term_plan_content = DiagnosisAgent._complete_plan_sections(
            str(
                long_term_plan_content
                if long_term_plan_content is not None
                else raw.get("long_term_plan", "")
            ),
            ("【最终目标】", "【能力路径与阶段】", "【阶段里程碑】", "【资源预算】", "【重规划条件】", "【保温底线】"),
        )
        short_term_plan_content = raw.get("short_term_plan_content")
        short_term_plan_content = DiagnosisAgent._complete_plan_sections(
            str(
                short_term_plan_content
                if short_term_plan_content is not None
                else raw.get("short_term_plan", "")
            ),
            ("【当前主目标】", "【长期目标保温】", "【具体任务块】", "【复习任务】", "【反馈指标】"),
        )
        return {
            "summary": raw.get("summary") or raw.get("diagnosis") or "系统基于当前学习资料生成学习状态判断。",
            "risk_flags": as_list(raw.get("risk_flags") or raw.get("risks")),
            "recommendations": as_list(raw.get("recommendations")),
            "uncertainty": as_list(raw.get("uncertainty") or raw.get("uncertainties")),
            "long_term_plan_content": as_plan_text(long_term_plan_content),
            "short_term_plan_content": as_plan_text(short_term_plan_content),
            "long_term_plan_action": normalize_plan_action(raw.get("long_term_plan_action", "update")),
            "short_term_plan_action": normalize_plan_action(raw.get("short_term_plan_action", "update")),
            "priority_mode": normalize_priority_mode(raw.get("priority_mode", "normal")),
            "adjustment_reason": raw.get("adjustment_reason") or "根据当前学习资料生成。",
            "route_context": (
                raw.get("route_context")
                if isinstance(raw.get("route_context"), dict)
                else None
            ),
            "goal_contract": (
                raw.get("goal_contract")
                if isinstance(raw.get("goal_contract"), dict)
                else None
            ),
            "milestones": (
                raw.get("milestones", [])
                if isinstance(raw.get("milestones"), list)
                else []
            ),
            "short_term_learning_package": (
                raw.get("short_term_learning_package")
                if isinstance(raw.get("short_term_learning_package"), dict)
                else None
            ),
            "recovery_policy": (
                raw.get("recovery_policy")
                if isinstance(raw.get("recovery_policy"), dict)
                else None
            ),
            "recommendation_trace": (
                raw.get("recommendation_trace")
                if isinstance(raw.get("recommendation_trace"), dict)
                else None
            ),
            "assumptions": as_list(raw.get("assumptions")),
            "unknowns_to_confirm": as_list(raw.get("unknowns_to_confirm")),
            "learning_task": {
                "task_type": task.get("task_type") or "knowledge_review",
                "task_content": task.get("task_content") or task.get("action") or task.get("target_knowledge") or raw.get("next_task") or "复习当前主题的核心知识。",
                "estimated_minutes": task.get("estimated_minutes") or task.get("duration_minutes") or raw.get("task_minutes") or context.get("available_minutes", 15),
                "expected_output": task.get("expected_output") or task.get("output") or task.get("output_format") or raw.get("expected_output") or "完成一份简明学习笔记。",
                "completion_criteria": task.get("completion_criteria") or task.get("acceptance_criteria") or task.get("success_criteria") or raw.get("completion_standard") or "内容准确且能够复述核心要点。",
            },
        }

    @staticmethod
    def _adapt_natural_language_output(
        raw_output: Any, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Accept legacy extra fields but keep the model boundary minimal."""

        adapted = DiagnosisAgent._adapt_model_output(raw_output, context)
        allowed = {
            "summary",
            "risk_flags",
            "recommendations",
            "uncertainty",
            "long_term_plan_content",
            "short_term_plan_content",
            "long_term_plan_action",
            "short_term_plan_action",
            "priority_mode",
            "adjustment_reason",
            "learning_task",
        }
        return {key: value for key, value in adapted.items() if key in allowed}

    @staticmethod
    def _parse_standard_output(value: DiagnosisStandardOutput, context: dict[str, Any]) -> LearningAnalysisModelOutput:
        task = {
            "task_type": "knowledge_review",
            "task_content": value.next_task or "复习当前主题的核心知识。",
            "estimated_minutes": value.task_minutes or context.get("available_minutes", 15),
            "expected_output": value.expected_output or "完成一份简明学习笔记。",
            "completion_criteria": value.completion_standard or "能够复述核心要点。",
        }
        long_term = DiagnosisAgent._complete_plan_sections(
            value.long_term_plan,
            (
                "【最终目标】",
                "【能力路径与阶段】",
                "【阶段里程碑】",
                "【资源预算】",
                "【重规划条件】",
                "【保温底线】",
            ),
        )
        short_term = DiagnosisAgent._complete_plan_sections(
            value.short_term_plan,
            (
                "【当前主目标】",
                "【长期目标保温】",
                "【时间分配】",
                "【具体任务块】",
                "【复习任务】",
                "【反馈指标】",
            ),
        )
        return LearningAnalysisModelOutput.model_validate({
            "summary": value.diagnosis or "系统基于当前学习资料生成学习状态判断。",
            "risk_flags": value.risks,
            "recommendations": value.recommendations,
            "uncertainty": value.uncertainties,
            "long_term_plan_content": long_term,
            "short_term_plan_content": short_term,
            "long_term_plan_action": "update",
            "short_term_plan_action": "update",
            "priority_mode": "normal",
            "adjustment_reason": "根据当前学习资料生成。",
            "learning_task": task,
        })

    @staticmethod
    def _complete_plan_sections(content: str, required_sections: tuple[str, ...]) -> str:
        """Preserve a standard-model plan while making missing required sections explicit.

        Standard diagnosis responses may provide only a short plan.  The strict
        downstream contract still needs every section, so missing sections are
        explicitly marked for user confirmation instead of being fabricated.
        """
        text = content.strip()
        if not text:
            return "".join(f"{section}待用户确认" for section in required_sections)
        if required_sections[0] in text:
            completed = text
        else:
            completed = f"{required_sections[0]}{text}"
        return completed + "".join(
            f"{section}待用户确认"
            for section in required_sections
            if section not in completed
        )

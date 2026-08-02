from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from competition_app.agents.common import envelope
from competition_app.agents.plan_contract_compiler import PlanContractCompilerAgent
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
    PlanChangeDecision,
)
from competition_app.contracts.review import DailyReviewPolicy
from competition_app.contracts.plan_compilation import (
    CompiledLongTermContract,
    CompiledPlanContractResult,
    CompiledShortTermContract,
    PlanCompilationEnvelope,
)
from competition_app.llm.base import ChatModel
from competition_app.llm.openai_compatible import ModelResponseError
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.stub import StubChatModel
from competition_app.llm.schemas import (
    DailyTaskPlanningModelOutput,
    FORBIDDEN_OBJECTIVE_FIELDS,
    DiagnosisStandardOutput,
    LearnerDataResponseModelOutput,
    LearningAnalysisModelOutput,
    LongTermPlanningModelOutput,
    NaturalLanguageLearningAnalysisModelOutput,
    ShortTermPlanningModelOutput,
    ThreeLayerPlanningModelOutput,
    validate_training_style_output,
)
from competition_app.services.plan_change_gate import PlanChangeGate
from competition_app.services.planning_validator import PlanningValidator
from competition_app.services.planning_readiness import PlanningReadinessService


class DiagnosisResult(BaseModel):
    summary: str = "系统基于当前任务生成初始学习状态。"
    risk_flags: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    stage_id: str = "T0"
    weak_kp_ids: list[str] = Field(default_factory=lambda: ["KP_FJ_001"])
    daily_review_policy: DailyReviewPolicy = Field(
        default_factory=lambda: DailyReviewPolicy(capacity=1)
    )
    learning_plan_proposal: LearningPlanProposal | None = None
    compiled_plan_contract: PlanCompilationEnvelope | None = None
    trusted_plan_route: dict[str, Any] = Field(default_factory=dict)
    parent_plan_constraints: dict[str, Any] = Field(default_factory=dict)
    requires_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    clarification_reason: str | None = None
    clarification_fields: list[str] = Field(default_factory=list)
    interrupt_type: str | None = None
    plan_scope: str | None = None
    prerequisite_scope: str | None = None
    learner_data: dict[str, Any] = Field(default_factory=dict)


class DiagnosisAgent:
    def __init__(
        self,
        chat_model: ChatModel | None = None,
        plan_contract_compiler: PlanContractCompilerAgent | None = None,
        learning_plan_service: Any | None = None,
    ) -> None:
        self.chat_model = chat_model or StubChatModel()
        self.plan_contract_compiler = (
            plan_contract_compiler or PlanContractCompilerAgent(self.chat_model)
        )
        # Readiness is still backend-owned, but receives the repository-backed
        # service when the application has one so lower-layer requests can
        # reject stale parent versions.  Direct unit callers may omit it.
        self.learning_plan_service = learning_plan_service

    async def run(self, context: dict[str, Any]) -> AgentEnvelope[DiagnosisResult]:
        dependency_outputs = context.get("dependency_outputs", {})
        task_type = str(context.get("task_type", "learning_plan"))
        if task_type == "learner_data_query":
            return await self._run_learner_data_query(context)
        if task_type == "learning_plan":
            context = await self._with_authorized_planning_context(context)
        knowledge_output = dependency_outputs.get("knowledge")
        knowledge = getattr(knowledge_output, "payload", None)
        knowledge_query = getattr(knowledge, "query", "")
        evidence_items = getattr(knowledge, "evidence_items", [])
        resolved_kp_ids = getattr(knowledge, "resolved_kp_ids", [])
        if context.get("system_operation") == "due_review_dispatch":
            state_kp_ids = [
                str(item.get("kp_id") or "").strip()
                for item in context.get("user_knowledge_states") or []
                if isinstance(item, dict) and str(item.get("kp_id") or "").strip()
            ]
            weak_kp_ids = list(dict.fromkeys([
                *[str(item) for item in resolved_kp_ids if str(item).strip()],
                *state_kp_ids,
            ]))
            return envelope(
                context,
                "diagnosis_agent",
                "diagnosis_result",
                DiagnosisResult(
                    summary=(
                        "该知识点已由完成题目的正式记录进入到期复习队列；"
                        "本次仅生成并绑定复习资源，不重新推断整体学情。"
                    ),
                    stage_id="review_due",
                    weak_kp_ids=weak_kp_ids,
                    daily_review_policy=DailyReviewPolicy(capacity=1),
                ),
            )
        route_output = dependency_outputs.get("route_resolution")
        resolved_route = getattr(route_output, "payload", None)
        plan_scope = context.get("plan_scope")
        parent_route = self._parent_planning_route(context, plan_scope)
        if parent_route is not None:
            resolved_route = parent_route
        if resolved_route is None:
            resolved_route = self._provisional_route_fallback(context)
        route_context = self._trusted_route_context(resolved_route)
        prompt_skill = prompt_skill_registry.load("diagnosis_agent", task_type)
        user_profile = context.get("user_profile", {})
        learning_profile = context.get("learning_profile", {})
        system_data = context.get("system_data", {})
        learning_goals = user_profile.get("goals") or user_profile.get("learning_goals", [])
        current_status = learning_profile.get("current_status", {})
        behavior_metrics = system_data or learning_profile.get("behavior_metrics", {})
        monitoring_snapshot = context.get("learning_monitoring") or {}
        if monitoring_snapshot:
            behavior_metrics = {
                **behavior_metrics,
                "monitoring_evidence_status": monitoring_snapshot.get("evidence_status"),
                "monitoring_freshness_status": monitoring_snapshot.get("freshness_status"),
                "sample_counts": monitoring_snapshot.get("sample_counts") or {},
                "observed_metrics": monitoring_snapshot.get("metrics") or {},
            }
            if monitoring_snapshot.get("evidence_status") in {"insufficient", "unavailable"}:
                current_status = {
                    "status_code": "T0",
                    "status_name": "证据积累中",
                    "confidence": 0.25,
                    "evidence": ["当前没有足够的学习行为样本，暂不作确定性学情判断。"],
                }
        confirmed_prerequisite_courses = self._confirmed_prerequisite_courses(
            context, route_context
        )
        unmet_prerequisite_courses = self._unmet_prerequisite_courses(
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
        if task_type == "learning_plan":
            scope_clarification = (
                await self._plan_scope_clarification(
                    context,
                    plan_scope,
                    context.get("current_long_term_plan"),
                    context.get("current_short_term_plan"),
                )
                if plan_scope == "unspecified"
                else None
                if context.get("enforce_planning_readiness")
                else await self._plan_scope_clarification(
                    context,
                    plan_scope,
                    context.get("current_long_term_plan"),
                    context.get("current_short_term_plan"),
                )
            )
            if scope_clarification is not None:
                questions, reason = scope_clarification
                result = DiagnosisResult(
                    summary=reason,
                    stage_id=str(system_data.get("current_stage_id", "T0")),
                    weak_kp_ids=resolved_kp_ids,
                    daily_review_policy=DailyReviewPolicy(capacity=1),
                    requires_clarification=True,
                    clarification_questions=questions,
                    clarification_reason=reason,
                    interrupt_type=(
                        "plan_scope_resolution"
                        if plan_scope == "unspecified"
                        else "planning_prerequisite"
                    ),
                    plan_scope=plan_scope,
                )
                return envelope(context, "diagnosis_agent", "diagnosis_result", result)
            enforce_scope_readiness = context.get("enforce_planning_readiness") and (
                plan_scope != "long_term" or context.get("enforce_profile_readiness")
            )
            if enforce_scope_readiness and plan_scope in {
                "long_term", "short_term", "daily_task"
            }:
                planning_readiness = PlanningReadinessService(
                    self.learning_plan_service
                ).evaluate(
                    context, plan_scope, learner_id=context.get("learner_id")
                )
                if not planning_readiness.can_generate:
                    clarification_questions = await self._clarification_questions(
                        context, planning_readiness
                    )
                    readiness_summary = {
                        "needs_profile": "制定长期规划前，需要先补齐最少量的个性化信息。",
                        "needs_long_term_plan": "制定短期计划前，需要先建立长期规划。",
                        "needs_short_term_plan": "制定当日任务前，需要先建立短期计划。",
                        "stale_parent_plan": (
                            "当前短期计划已失效，需要先重新制定短期计划。"
                            if plan_scope == "daily_task"
                            else "上层规划已失效，需要先重新制定当前有效版本。"
                        ),
                    }.get(
                        planning_readiness.status,
                        "当前规划前置条件尚未满足。",
                    )
                    result = DiagnosisResult(
                        summary=readiness_summary,
                        stage_id=str(system_data.get("current_stage_id", "T0")),
                        weak_kp_ids=resolved_kp_ids,
                        daily_review_policy=DailyReviewPolicy(capacity=1),
                        requires_clarification=True,
                        clarification_questions=clarification_questions,
                        clarification_fields=[planning_readiness.next_profile_field]
                        if planning_readiness.next_profile_field
                        else [],
                        clarification_reason=readiness_summary,
                        interrupt_type=(
                            "profile_completion"
                            if planning_readiness.status == "needs_profile"
                            else "planning_prerequisite"
                        ),
                        # Keep the user's requested layer stable. Available
                        # prerequisite actions are guidance, not permission to
                        # silently turn a short-term request into a long-term
                        # planning result.
                        plan_scope=plan_scope,
                        prerequisite_scope=(
                            "short_term"
                            if planning_readiness.status == "needs_short_term_plan"
                            else "long_term"
                            if planning_readiness.status == "needs_long_term_plan"
                            else None
                        ),
                    )
                    return envelope(context, "diagnosis_agent", "diagnosis_result", result)
        textbook_context = route_context.get("textbook_route") or {}
        if route_context.get("match_reason") == "agent_requires_clarification":
            questions = self._string_list(
                route_context.get("unknowns_to_confirm")
            )[:1]
            result = DiagnosisResult(
                summary="需要先确认与当前目标匹配的具体学习或报考路线。",
                stage_id=str(system_data.get("current_stage_id", "T0")),
                weak_kp_ids=resolved_kp_ids,
                daily_review_policy=DailyReviewPolicy(capacity=1),
                requires_clarification=True,
                clarification_questions=questions,
                clarification_reason="当前目标与已知背景还不足以唯一确定一条已批准路线。",
                interrupt_type="route_resolution",
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
                daily_review_policy=DailyReviewPolicy(capacity=1),
                requires_clarification=True,
                clarification_questions=questions,
                clarification_reason="教材方向已识别，但具体考试身份仍不明确。",
                interrupt_type="route_resolution",
                plan_scope=plan_scope,
            )
            return envelope(context, "diagnosis_agent", "diagnosis_result", result)
        semantic_change = (
            await self._assess_plan_change(context, plan_scope)
            if (
                context.get("plan_change_context") is not None
                or (
                    # Whenever a plan already exists, Diagnosis owns the
                    # semantic decision for this turn.  Planner's
                    # ``create_or_update`` flag is only a routing hint and
                    # must not suppress a learning-state change such as
                    # “我已经学过《中医学基础》了，重新规划一下”.
                    self._has_plan_content(context.get("current_long_term_plan"))
                    or self._has_plan_content(context.get("current_short_term_plan"))
                )
                or context.get("explicit_long_term_change")
                or context.get("explicit_short_term_change")
                or context.get("sustained_learning_change")
                or context.get("route_changed")
            )
            else None
        )
        # The requested scope is a system-owned execution boundary, not a
        # semantic guess. For an ordinary first-time scoped request, provide a
        # minimal contract so the gate does not treat missing child plans as a
        # reason to generate all three layers in one response.
        if (
            semantic_change is None
            and task_type == "learning_plan"
            and plan_scope in {"long_term", "short_term", "daily_task"}
        ):
            scoped_actions = {
                "long_term": ("update", "reuse", "reuse"),
                "short_term": ("reuse", "update", "reuse"),
                "daily_task": ("reuse", "reuse", "update"),
            }[plan_scope]
            semantic_change = PlanChangeDecision(
                long_term_action=scoped_actions[0],
                short_term_action=scoped_actions[1],
                daily_task_action=scoped_actions[2],
                replan_requested=False,
                reason=f"系统已确认本次只处理{plan_scope}层。",
            )
        change_decision = PlanChangeGate().decide(
            user_request=str(context.get("user_request", "")),
            current_long_term_plan=context.get("current_long_term_plan"),
            current_short_term_plan=context.get("current_short_term_plan"),
            explicit_long_term_change=bool(context.get("explicit_long_term_change")),
            explicit_short_term_change=bool(context.get("explicit_short_term_change")),
            sustained_learning_change=bool(context.get("sustained_learning_change")),
            route_changed=bool(context.get("route_changed")),
            single_performance_change=bool(context.get("single_performance_change")),
            semantic_decision=semantic_change,
            allow_legacy_heuristics=False,
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
                daily_review_policy=DailyReviewPolicy(capacity=1),
                requires_clarification=True,
                clarification_questions=change_decision.clarification_questions,
                clarification_reason=change_decision.reason,
                plan_scope=plan_scope,
            )
            return envelope(context, "diagnosis_agent", "diagnosis_result", result)
        if (
            task_type == "learning_plan"
            and plan_scope in {"long_term", "short_term", "daily_task"}
        ):
            scoped_actions = {
                "long_term": ("update", "reuse", "reuse"),
                "short_term": ("reuse", "update", "reuse"),
                "daily_task": ("reuse", "reuse", "update"),
            }
            # ``plan_scope`` is the execution boundary selected by Planner
            # (or restored from the interrupt checkpoint).  A changed upper
            # layer invalidates lower-layer versions, but those layers are
            # deliberately not materialised in the same Diagnosis call.  In
            # particular, do not let a semantic replan decision turn a
            # short-term rerun into a three-layer update: the scoped output
            # contract would then be validated against fields that are not
            # present in the current model response (for example long-term
            # stage durations while compiling a short-term plan).
            long_action, short_action, daily_action = scoped_actions[plan_scope]
            change_decision = change_decision.model_copy(update={
                "long_term_action": long_action,
                "short_term_action": short_action,
                "daily_task_action": daily_action,
                "reason": f"本次只生成{plan_scope}层，其他层不由模型改写。",
            })
        if (
            task_type == "learning_plan"
            and plan_scope in {"long_term", "short_term", "daily_task"}
            and not self._has_publishable_planning_phases(route_context)
        ):
            route_questions = self._string_list(
                textbook_context.get("clarification_questions")
            ) or self._string_list(route_context.get("unknowns_to_confirm"))
            if plan_scope == "long_term":
                summary = "当前目标还没有绑定到包含明确教材的可信学习路线。"
                fallback_question = (
                    "请说明要参加的具体中医药资格考试官方名称；"
                    "“长期学习中医”或“零基础”不能代替资格考试目标。"
                )
            else:
                summary = "现有长期规划没有绑定完整的教材路线，不能据此继续生成下层计划。"
                fallback_question = "请先重新制定一份已绑定明确教材路线的长期规划。"
            result = DiagnosisResult(
                summary=summary,
                stage_id=str(system_data.get("current_stage_id", "T0")),
                weak_kp_ids=resolved_kp_ids,
                daily_review_policy=DailyReviewPolicy(capacity=1),
                requires_clarification=True,
                clarification_questions=route_questions[:1] or [fallback_question],
                clarification_reason=(
                    "长期规划必须来源于系统已解析且包含真实教材的路线，"
                    "禁止用占位教材生成或落库。"
                ),
                interrupt_type="route_resolution",
                plan_scope=plan_scope,
            )
            return envelope(context, "diagnosis_agent", "diagnosis_result", result)

        model_textbook_context = self._model_textbook_context(route_context)
        memory_output = context.get("dependency_outputs", {}).get("memory")
        memory_payload = getattr(memory_output, "payload", None)
        memory_context_summary = getattr(memory_payload, "context_summary", None)
        audit_feedback = context.get("audit_feedback")
        audit_payload = getattr(audit_feedback, "payload", audit_feedback)
        repair_instruction = context.get("repair_instruction")
        audit_revision = None
        if audit_payload is not None:
            audit_revision = {
                "instruction": (
                    str((repair_instruction or {}).get("repair_instruction") or "")
                    or "这是上一轮审核的强制修订项。只修正这些问题，"
                    "保留已经通过的内容，并确保自然语言正文与结构化合同一致。"
                ),
                "issue_ids": list((repair_instruction or {}).get("issue_ids") or []),
                "locations": list((repair_instruction or {}).get("locations") or []),
                "findings": [
                    str(item)[:600]
                    for item in list(
                        getattr(audit_payload, "findings", []) or []
                    )[:8]
                    if str(item).strip()
                ],
                "audit_report": str(
                    getattr(audit_payload, "audit_report", "") or ""
                )[:2400],
            }
        planning_payload = {
            "plan_scope": plan_scope,
            "user_request": str(context.get("user_request", "")),
            "compressed_conversation_summary": str(
                getattr(memory_context_summary, "summary", "") or ""
            ),
            "goals": learning_goals,
            "learner_context": {
                "learning_goal": user_profile.get("learning_goal"),
                "learning_background": user_profile.get("learning_background"),
                "completed_courses": user_profile.get("completed_courses"),
                "learner_group": user_profile.get("learner_group") or user_profile.get("user_group"),
            },
            "time_constraints": {
                "available_minutes_today": context.get("available_minutes"),
                "sustainable_schedule": user_profile.get("time_constraints"),
                "daily_available_minutes": user_profile.get("daily_available_minutes"),
                "weekly_available_minutes": user_profile.get("weekly_available_minutes"),
                "explicit_request": str(context.get("user_request", "")),
                "preferences": user_profile.get("user_preference", {}),
            },
            "learning_evidence": {
                "current_status": current_status,
                "behavior_summary": behavior_metrics,
                "evidence_status": monitoring_snapshot.get("evidence_status") or "unknown",
                "freshness_status": monitoring_snapshot.get("freshness_status") or "unknown",
                "precision_policy": (
                    "只有 evidence_status=sufficient 且 freshness_status 不是 stale 时，"
                    "才能在用户正文中断言精确掌握度、错误次数或薄弱知识点总数；"
                    "否则应明确证据仍在积累，并以待验证的学习重点表述。"
                ),
                "retrieval_summary": getattr(knowledge, "retrieval_summary", ""),
                "evidence_summaries": [item.content_summary for item in evidence_items[:3]],
                "confirmed_prerequisite_courses": sorted(
                    confirmed_prerequisite_courses
                ),
                "unmet_prerequisite_courses": sorted(
                    unmet_prerequisite_courses
                ),
            },
            "learning_state": self._model_learning_state(
                context.get("multi_scale_learning_state")
            ),
            "learning_path_progress": self._model_learning_path_progress(
                context.get("learning_path_progress")
            ),
            "learning_path_progress_instruction": (
                "当 plan_scope=daily_task 且 learning_path_progress 可用时，"
                "当日任务正文必须具体到当前应学的小节："
                "写明“观看《教材》第X章第X节视频《视频标题》”并绑定该小节的题目训练；"
                "小节、章节和视频必须来自 learning_path_progress，不得虚构章节或链接。"
                "没有已验证视频的小节只描述章节学习，不虚构视频。"
            ),
            "task_load_policy": {
                key: value
                for key, value in dict(context.get("task_load_policy") or {}).items()
                if key
                in {
                    "policy_id",
                    "baseline_minutes",
                    "recommended_minutes",
                    "direction",
                    "allocation",
                    "evidence",
                    "reasons",
                    "constraints",
                }
            },
            "task_load_policy_instruction": (
                "当 plan_scope=daily_task 时，estimated_minutes 应采用系统给出的 "
                "task_load_policy.recommended_minutes；自然语言只解释原因，不重新计算指标。"
            ),
            "path_candidates": self._model_path_candidates(
                context.get("path_candidates")
            ),
            "path_candidate_policy": (
                "selected_path_candidate_id 只能从 eligible 中选择；"
                "blocked 仅用于理解不可选原因。若 eligible 为空，"
                "selected_path_candidate_id 必须留空并沿用当前已批准规划路线。"
            ),
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
            **(
                {"audit_revision": audit_revision}
                if audit_revision is not None
                else {}
            ),
            "output_schema": self._planning_draft_schema(plan_scope),
        }
        try:
            raw_dict = await self._complete_plan_draft(
                context,
                planning_payload,
                prompt_skill,
                permission_note=(
                    "业务智能体只生成当前规划层的详细自然语言计划文档；"
                    "仅在确有必要时返回 selected_path_candidate_id；"
                    "不得输出执行合同字段、系统ID、路线事实或持久化字段。"
                ),
            )
            compiled_plan_contract: PlanCompilationEnvelope | None = None
            legacy_structured_output = "plan_document" not in raw_dict
            if plan_scope in {"long_term", "short_term", "daily_task"}:
                compiled_plan_contract = await self.plan_contract_compiler.compile(
                    context,
                    plan_scope=plan_scope,
                    diagnosis_output=raw_dict,
                    trusted_route=self._compiler_route_context(route_context),
                    parent_plan_constraints=self._parent_plan_constraints(
                        context, plan_scope
                    ),
                )
                if (
                    compiled_plan_contract.result.status != "compiled"
                    and not legacy_structured_output
                ):
                    revision_payload = {
                        **planning_payload,
                        "previous_plan_document": raw_dict.get("plan_document", ""),
                        "compiler_revision_issues": [
                            issue.model_dump()
                            for issue in compiled_plan_contract.result.issues
                        ],
                        "revision_instruction": (
                            "只根据编译器列出的缺失或冲突修订自然语言计划文档；"
                            "不要直接输出合同字段。"
                        ),
                    }
                    raw_dict = await self._complete_plan_draft(
                        context,
                        revision_payload,
                        prompt_skill,
                        permission_note=(
                            "只修订自然语言计划文档以满足编译器指出的来源要求；"
                            "不得补造事实、系统ID或执行合同字段。"
                        ),
                    )
                    compiled_plan_contract = await self.plan_contract_compiler.compile(
                        context,
                        plan_scope=plan_scope,
                        diagnosis_output=raw_dict,
                        trusted_route=self._compiler_route_context(route_context),
                        parent_plan_constraints=self._parent_plan_constraints(
                            context, plan_scope
                        ),
                    )
                    if compiled_plan_contract.result.status != "compiled":
                        raise ValueError(
                            "规划自然语言文档经一次受控修订后仍未能编译为合同："
                            + "; ".join(
                                f"{issue.code}@{issue.field_path}"
                                for issue in compiled_plan_contract.result.issues
                            )
                        )
                if compiled_plan_contract.result.status == "compiled":
                    raw_dict = self._apply_compiled_contract(
                        raw_dict,
                        compiled_plan_contract.result,
                    )
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
                    user_time_constraints=str(user_profile.get("time_constraints") or ""),
                    explicit_user_request=str(context.get("user_request") or ""),
                    evidence_status=str(
                        monitoring_snapshot.get("evidence_status") or "unknown"
                    ),
                    evidence_freshness=str(
                        monitoring_snapshot.get("freshness_status") or "unknown"
                    ),
                    long_term_action=change_decision.long_term_action,
                    short_term_action=change_decision.short_term_action,
                    daily_task_action=change_decision.daily_task_action,
                    confirmed_prerequisite_courses=confirmed_prerequisite_courses,
                    unmet_prerequisite_courses=unmet_prerequisite_courses,
                    path_candidates=context.get("path_candidates"),
                    parent_stage_duration_days=self._parent_plan_constraints(
                        context, plan_scope
                    ).get("current_stage_duration_days"),
                    active_scope=plan_scope,
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
                    revised_raw = await self._complete_plan_draft(
                        context,
                        {
                            **revision_payload,
                            "output_schema": self._planning_draft_schema(plan_scope),
                        },
                        prompt_skill,
                        permission_note=(
                            "仅修订当前规划层的自然语言计划文档；不得生成系统ID、"
                            "路线事实或执行合同字段。"
                        ),
                    )
                    if plan_scope in {"long_term", "short_term", "daily_task"}:
                        if not legacy_structured_output:
                            revised_compilation = await self.plan_contract_compiler.compile(
                                context,
                                plan_scope=plan_scope,
                                diagnosis_output=revised_raw,
                                trusted_route=self._compiler_route_context(route_context),
                                parent_plan_constraints=self._parent_plan_constraints(
                                    context, plan_scope
                                ),
                            )
                            if revised_compilation.result.status != "compiled":
                                raise ValueError(
                                    "规划修订后的自然语言文档未能编译为合同："
                                    + "; ".join(
                                        f"{issue.code}@{issue.field_path}"
                                        for issue in revised_compilation.result.issues
                                    )
                                )
                            compiled_plan_contract = revised_compilation
                            revised_raw = self._apply_compiled_contract(
                                revised_raw,
                                revised_compilation.result,
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
                        user_time_constraints=str(user_profile.get("time_constraints") or ""),
                        explicit_user_request=str(context.get("user_request") or ""),
                        evidence_status=str(
                            monitoring_snapshot.get("evidence_status") or "unknown"
                        ),
                        evidence_freshness=str(
                            monitoring_snapshot.get("freshness_status") or "unknown"
                        ),
                        long_term_action=change_decision.long_term_action,
                        short_term_action=change_decision.short_term_action,
                        daily_task_action=change_decision.daily_task_action,
                        confirmed_prerequisite_courses=confirmed_prerequisite_courses,
                        unmet_prerequisite_courses=unmet_prerequisite_courses,
                        path_candidates=context.get("path_candidates"),
                        parent_stage_duration_days=self._parent_plan_constraints(
                            context, plan_scope
                        ).get("current_stage_duration_days"),
                        active_scope=plan_scope,
                    )
                    if not validation.valid:
                        if any(
                            "占位教材" in issue or "缺少系统可信路线阶段" in issue
                            for issue in validation.issues
                        ):
                            result = DiagnosisResult(
                                summary="当前目标还没有绑定到包含明确教材的可信学习路线。",
                                stage_id=str(system_data.get("current_stage_id", "T0")),
                                weak_kp_ids=resolved_kp_ids,
                                daily_review_policy=DailyReviewPolicy(capacity=1),
                                requires_clarification=True,
                                clarification_questions=(
                                    self._string_list(
                                        route_context.get("unknowns_to_confirm")
                                    )[:1]
                                    or [
                                        "请说明要参加的具体中医药资格考试官方名称；"
                                        "“长期学习中医”或“零基础”不能代替资格考试目标。"
                                    ]
                                ),
                                clarification_reason=(
                                    "长期规划必须先绑定包含真实教材的系统可信路线，"
                                    "占位阶段不会发布或落库。"
                                ),
                                interrupt_type="route_resolution",
                                plan_scope=plan_scope,
                            )
                            return envelope(
                                context,
                                "diagnosis_agent",
                                "diagnosis_result",
                                result,
                            )
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
                                daily_review_policy=DailyReviewPolicy(capacity=1),
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
                if legacy_structured_output and plan_scope in {
                    "long_term", "short_term", "daily_task"
                }:
                    # Compatibility only for old test doubles/integrations
                    # that still return field-by-field JSON. Production uses
                    # plan_document and has already compiled that document.
                    final_scoped_output = self._scoped_output_for_compilation(
                        plan_scope,
                        three_layer,
                    )
                    final_compilation = await self.plan_contract_compiler.compile(
                        context,
                        plan_scope=plan_scope,
                        diagnosis_output=final_scoped_output,
                        trusted_route=self._compiler_route_context(route_context),
                        parent_plan_constraints=self._parent_plan_constraints(
                            context, plan_scope
                        ),
                    )
                    if final_compilation.result.status == "compiled":
                        compiled_plan_contract = final_compilation
                        raw_dict = self._apply_compiled_contract(
                            final_scoped_output,
                            compiled_plan_contract.result,
                        )
                        three_layer = self._expand_scoped_planning_output(
                            plan_scope,
                            raw_dict,
                            context,
                            route_context,
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
            # IDs and stage are system facts; the model only proposes semantic content.
            stage_id=str(system_data.get("current_stage_id", "T0")),
            weak_kp_ids=resolved_kp_ids,
            daily_review_policy=DailyReviewPolicy(capacity=1),
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
            compiled_plan_contract=compiled_plan_contract,
            trusted_plan_route=self._compiler_route_context(route_context),
            parent_plan_constraints=self._parent_plan_constraints(context, plan_scope),
        )
        return envelope(context, "diagnosis_agent", "diagnosis_result", result)

    async def _with_authorized_planning_context(
        self, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Fetch planning evidence only after Planner selected Diagnosis."""

        registry = context.get("tool_registry")
        if registry is None:
            return context
        try:
            planning_context = await registry.invoke(
                "get_learning_planning_context",
                "diagnosis_agent",
                trace_recorder=context.get("trace_recorder"),
                safe_input_summary={
                    "current_learner": True,
                    "scope": str(context.get("plan_scope") or "unspecified"),
                },
                safe_output_summary_factory=lambda result: {
                    "source": str(result.get("source") or "unknown")
                    if isinstance(result, dict)
                    else "unknown",
                    "has_long_term_plan": bool(
                        isinstance(result, dict)
                        and result.get("current_long_term_plan")
                    ),
                    "has_short_term_plan": bool(
                        isinstance(result, dict)
                        and result.get("current_short_term_plan")
                    ),
                    "has_daily_task": bool(
                        isinstance(result, dict)
                        and result.get("current_learning_task")
                    ),
                },
                external_user_id=str(context.get("learner_id") or ""),
                scope=str(context.get("plan_scope") or "unspecified"),
                available_minutes=int(context.get("available_minutes") or 60),
            )
        except (KeyError, PermissionError):
            # Unit callers and older deployments may not register the new
            # bounded tool yet. Their explicitly supplied context remains a
            # compatible fallback; live orchestration always registers it.
            return context
        if not isinstance(planning_context, dict):
            return context
        enriched = dict(context)
        for key in (
            "learning_profile",
            "system_data",
            "user_knowledge_states",
            "question_attempts",
            "question_learning_stats",
            "learning_monitoring",
            "current_long_term_plan",
            "current_short_term_plan",
            "current_learning_task",
            "multi_scale_learning_state",
            "path_candidates",
            "task_load_policy",
        ):
            if key in planning_context:
                candidate = planning_context[key]
                # The bounded tool is authoritative for persisted data, but
                # an older adapter may legitimately omit a slice already
                # loaded by the application (plans, multi-scale state, path
                # candidates, monitoring). Never replace useful authorized
                # evidence with an empty compatibility response.
                candidate_is_empty = not candidate or (
                    isinstance(candidate, dict)
                    and not any(
                        value not in (None, "", [], {})
                        for value in candidate.values()
                    )
                )
                if candidate_is_empty and enriched.get(key):
                    continue
                enriched[key] = candidate
        enriched["planning_context_source"] = planning_context.get("source")
        path_progress = await self._load_learning_path_progress(context, registry)
        if path_progress is not None:
            enriched["learning_path_progress"] = path_progress
        return enriched

    async def _load_learning_path_progress(
        self,
        context: dict[str, Any],
        registry: Any,
    ) -> dict[str, Any] | None:
        """Fetch the stage→book→chapter→section projection with bound videos.

        The tool is read-only and Diagnosis-only. Older deployments or unit
        callers that do not register it simply fall back to the enriched
        planning context already present on ``context``.
        """

        try:
            path_progress = await registry.invoke(
                "get_learning_path_progress",
                "diagnosis_agent",
                trace_recorder=context.get("trace_recorder"),
                safe_input_summary={"current_learner": True},
                safe_output_summary_factory=lambda result: {
                    "availability": str(result.get("availability") or "unknown")
                    if isinstance(result, dict)
                    else "unknown",
                    "stage_count": (
                        len(result.get("stages") or [])
                        if isinstance(result, dict)
                        else 0
                    ),
                    "has_current_section": bool(
                        isinstance(result, dict)
                        and result.get("current_section")
                    ),
                },
                external_user_id=str(context.get("learner_id") or ""),
            )
        except (KeyError, PermissionError):
            return None
        if not isinstance(path_progress, dict):
            return None
        return path_progress

    async def _assess_plan_change(
        self, context: dict[str, Any], plan_scope: str | None
    ) -> PlanChangeDecision:
        """Ask Diagnosis to interpret replanning semantics.

        Wording is intentionally not parsed here.  The model receives the
        current plans, learner evidence and conversation answer, then returns
        only the small mutation contract.  The gate below applies dependency
        propagation and rejects unsafe combinations.
        """
        current_request = str(context.get("user_request") or "").strip()
        existing = {
            "long_term": context.get("current_long_term_plan") or {},
            "short_term": context.get("current_short_term_plan") or {},
            "daily_task": context.get("current_learning_task") or {},
        }
        skill = prompt_skill_registry.load("diagnosis_agent", "plan_change")
        payload = {
            "user_request": current_request,
            "plan_scope": plan_scope,
            "existing_plans": existing,
            "learner_profile": context.get("user_profile") or {},
            "learning_state": context.get("multi_scale_learning_state") or {},
            "learning_monitoring": context.get("learning_monitoring") or {},
            "plan_change_context": context.get("plan_change_context"),
            "explicit_flags": {
                "long_term": bool(context.get("explicit_long_term_change")),
                "short_term": bool(context.get("explicit_short_term_change")),
                "sustained_learning_change": bool(
                    context.get("sustained_learning_change")
                ),
                "route_changed": bool(context.get("route_changed")),
            },
            "output_schema": PlanChangeDecision.model_json_schema(),
        }
        try:
            raw = await self.chat_model.complete_json(
                "diagnosis_plan_change",
                build_model_context(
                    context,
                    target_agent="diagnosis_agent",
                    prompt_skill=skill,
                    payload=payload,
                    permission_note=(
                        "只判断本次是否需要重规划、涉及哪些层和事实变化；"
                        "不得生成规划正文、系统ID、路线ID或持久化状态。"
                    ),
                ),
            )
            return PlanChangeDecision.model_validate(raw)
        except Exception:
            # A model failure must not manufacture a change from wording. Keep
            # explicit system facts only and let the normal gate decide.
            return PlanChangeDecision(
                long_term_action="update"
                if context.get("explicit_long_term_change")
                else "reuse",
                short_term_action="update"
                if context.get("explicit_short_term_change")
                or context.get("sustained_learning_change")
                else "reuse",
                daily_task_action="update",
                replan_requested=bool(
                    context.get("explicit_long_term_change")
                    or context.get("explicit_short_term_change")
                    or context.get("sustained_learning_change")
                    or context.get("route_changed")
                ),
                reason="由系统已确认的规划事实决定；未使用词法推断。",
            )


    async def _run_learner_data_query(
        self,
        context: dict[str, Any],
    ) -> AgentEnvelope[DiagnosisResult]:
        query_kind = str(context.get("learner_data_query_kind") or "")
        tools_by_kind = {
            "recent_learning": ["get_recent_learning_summary"],
            "next_learning": [
                "get_current_plan_progress",
                "get_mastery_snapshot",
                "get_recent_learning_summary",
            ],
            "progress_summary": ["get_learning_progress"],
            "mastery_status": ["get_mastery_snapshot"],
            "review_status": ["get_review_status"],
            "plan_progress": ["get_current_plan_progress"],
        }
        if query_kind not in tools_by_kind:
            query_kind = "progress_summary"
        request = str(context.get("user_request") or "")
        window_days = self._learner_query_window(request, query_kind)
        registry = context.get("tool_registry")
        if registry is None:
            evidence: dict[str, Any] = {
                "evidence_status": "unavailable",
                "reason": "learner data tools are unavailable",
            }
        else:
            evidence_by_source: dict[str, Any] = {}
            for tool_name in tools_by_kind[query_kind]:
                tool_args: dict[str, Any] = {
                    "external_user_id": str(context.get("learner_id") or ""),
                }
                if tool_name in {
                    "get_recent_learning_summary",
                    "get_learning_progress",
                }:
                    tool_args["days"] = window_days
                if tool_name == "get_recent_learning_summary":
                    tool_args["recent_limit"] = 20
                if tool_name in {"get_mastery_snapshot", "get_review_status"}:
                    tool_args["history_limit"] = 100
                evidence_by_source[tool_name] = await registry.invoke(
                    tool_name,
                    "diagnosis_agent",
                    trace_recorder=context.get("trace_recorder"),
                    safe_input_summary={
                        "current_learner": True,
                        "window_days": window_days,
                    },
                    safe_output_summary_factory=lambda result: {
                        "evidence_status": str(
                            result.get("evidence_status") or "observed"
                        )
                        if isinstance(result, dict)
                        else "unknown",
                        "record_count": self._learner_record_count(result),
                    },
                    **tool_args,
                )
            evidence = (
                {
                    "evidence_status": "observed",
                    "sources": evidence_by_source,
                }
                if query_kind == "next_learning"
                else evidence_by_source[tools_by_kind[query_kind][0]]
            )
        compact_evidence = self._compact_learner_evidence(query_kind, evidence)
        if query_kind == "plan_progress":
            compact_evidence["requested_scope"] = self._requested_plan_scope(request)
        prompt_skill = prompt_skill_registry.load(
            "diagnosis_agent", "learner_data_query"
        )
        try:
            raw = await self.chat_model.complete_json(
                "diagnosis_agent",
                build_model_context(
                    context,
                    target_agent="diagnosis_agent",
                    prompt_skill=prompt_skill,
                    payload={
                        "user_request": request,
                        "query_kind": query_kind,
                        "window_days": window_days,
                        "learner_evidence": compact_evidence,
                        "output_schema": (
                            LearnerDataResponseModelOutput.model_json_schema()
                        ),
                    },
                    permission_note=(
                        "只可依据系统提供的当前用户只读证据生成自然语言回答；"
                        "不得调用其他工具、生成资源、修改计划、输出系统ID或推断缺失事实。"
                    ),
                ),
            )
            answer = LearnerDataResponseModelOutput.model_validate(raw).answer
        except Exception:
            answer = self._learner_data_fallback(
                query_kind,
                window_days,
                compact_evidence,
            )
        result = DiagnosisResult(
            summary=answer,
            stage_id=str(context.get("system_data", {}).get("current_stage_id", "T0")),
            weak_kp_ids=[],
            daily_review_policy=DailyReviewPolicy(capacity=1),
            learner_data={
                "schema_version": "1.0",
                "query_kind": query_kind,
                "window_days": window_days,
                "evidence_status": compact_evidence.get(
                    "evidence_status", "observed"
                ),
                "source": (
                    tools_by_kind[query_kind][0]
                    if len(tools_by_kind[query_kind]) == 1
                    else "combined_learner_evidence"
                ),
                "sources": tools_by_kind[query_kind],
                "snapshot": compact_evidence,
            },
        )
        return envelope(context, "diagnosis_agent", "learner_data_result", result)

    @staticmethod
    def _learner_query_window(request: str, query_kind: str) -> int:
        text = "".join(str(request or "").split())
        if any(marker in text for marker in ("近三个月", "最近三个月", "90天")):
            return 90
        if any(
            marker in text
            for marker in ("近一个月", "最近一个月", "近30天", "本月", "这个月")
        ):
            return 30
        if any(marker in text for marker in ("近7天", "最近7天", "本周", "这周")):
            return 7
        return 7 if query_kind == "recent_learning" else 30

    @staticmethod
    def _requested_plan_scope(request: str) -> str | None:
        """Select which already-loaded plan slice should be presented.

        This does not route or mutate a plan. Diagnosis has already been
        authorized for a read-only plan query; the helper only avoids returning
        a short-term summary when the user explicitly asked to see the long-term
        document (and vice versa).
        """

        text = "".join(str(request or "").split())
        if "长期" in text:
            return "long_term"
        if "短期" in text:
            return "short_term"
        if any(marker in text for marker in ("今日", "今天", "当日")):
            return "daily_task"
        return None

    @staticmethod
    def _learner_record_count(value: Any) -> int:
        if not isinstance(value, dict):
            return 0
        for key in (
            "verified_event_count",
            "mastery",
            "review_states",
            "review_tasks",
        ):
            item = value.get(key)
            if isinstance(item, int):
                return item
            if isinstance(item, list):
                return len(item)
        current = value.get("current_window")
        if isinstance(current, dict):
            return int(current.get("questions_completed") or 0)
        return 0

    @classmethod
    def _compact_learner_evidence(
        cls,
        query_kind: str,
        evidence: Any,
    ) -> dict[str, Any]:
        if not isinstance(evidence, dict):
            return {"evidence_status": "unavailable"}
        if evidence.get("evidence_status") == "unavailable":
            return {
                "evidence_status": "unavailable",
                "reason": str(evidence.get("reason") or ""),
            }
        if query_kind == "next_learning":
            sources = dict(evidence.get("sources") or {})
            plan = cls._compact_learner_evidence(
                "plan_progress",
                sources.get("get_current_plan_progress"),
            )
            mastery = cls._compact_learner_evidence(
                "mastery_status",
                sources.get("get_mastery_snapshot"),
            )
            recent = cls._compact_learner_evidence(
                "recent_learning",
                sources.get("get_recent_learning_summary"),
            )
            statuses = {
                plan.get("evidence_status"),
                mastery.get("evidence_status"),
                recent.get("evidence_status"),
            }
            return {
                "evidence_status": (
                    "observed"
                    if "observed" in statuses
                    else "no_verified_records"
                ),
                "plan_progress": plan,
                "mastery_and_review": mastery,
                "recent_learning": recent,
            }
        if query_kind == "recent_learning":
            return {
                "evidence_status": evidence.get(
                    "evidence_status", "no_verified_records"
                ),
                "verified_event_count": int(
                    evidence.get("verified_event_count") or 0
                ),
                "verified_learning_events": list(
                    evidence.get("verified_learning_events") or []
                )[:20],
                "task_completion": dict(evidence.get("task_completion") or {}),
                "focus": dict(evidence.get("focus") or {}),
                "evidence_rule": str(evidence.get("evidence_rule") or ""),
            }
        if query_kind == "progress_summary":
            return {
                "evidence_status": "observed",
                "window": dict(evidence.get("window") or {}),
                "current_window": dict(evidence.get("current_window") or {}),
                "lifetime": dict(evidence.get("lifetime") or {}),
            }
        if query_kind in {"mastery_status", "review_status"}:
            mastery = list(evidence.get("mastery") or [])
            mastery.sort(
                key=lambda item: (
                    float(item.get("mastery_score") or 0),
                    str(item.get("kp_name") or ""),
                )
            )
            return {
                "evidence_status": "observed" if mastery or evidence.get(
                    "review_states"
                ) else "no_verified_records",
                "mastery": mastery[:20],
                "review_states": list(evidence.get("review_states") or [])[:30],
                "review_tasks": list(evidence.get("review_tasks") or [])[:30],
            }
        long_term = dict(evidence.get("long_term") or {})
        short_term = dict(evidence.get("short_term") or {})
        daily_task = dict(evidence.get("daily_task") or {})
        return {
            "evidence_status": (
                "observed"
                if any((long_term, short_term, daily_task))
                else "no_verified_records"
            ),
            "long_term": {
                "status": long_term.get("status"),
                "content": long_term.get("content"),
                "structured": dict(long_term.get("structured") or {}),
                "stage_progress": list(long_term.get("stage_progress") or []),
            }
            if long_term
            else None,
            "short_term": {
                "status": short_term.get("status"),
                "content": short_term.get("content"),
                "structured": dict(short_term.get("structured") or {}),
                "acceptance_gate": short_term.get("acceptance_gate"),
            }
            if short_term
            else None,
            "daily_task": {
                "status": daily_task.get("status"),
                "learning_chapter": daily_task.get("learning_chapter"),
                "focus_knowledge_points": daily_task.get(
                    "focus_knowledge_points"
                ),
                "acceptance_gate": daily_task.get("acceptance_gate"),
            }
            if daily_task
            else None,
        }

    @staticmethod
    def _learner_data_fallback(
        query_kind: str,
        window_days: int,
        evidence: dict[str, Any],
    ) -> str:
        if evidence.get("evidence_status") == "unavailable":
            return "我暂时无法读取你的学习记录，请稍后再试。"
        if evidence.get("evidence_status") == "no_verified_records":
            return (
                f"近{window_days}天还没有查到可确认的学习完成记录。"
                "推荐过、打开过或仅生成过的资源不会被算作已经学习。"
            )
        if query_kind == "recent_learning":
            events = list(evidence.get("verified_learning_events") or [])
            labels = []
            type_labels = {
                "question_attempt": "题目练习",
                "paper_submission": "试卷练习",
                "case_training": "案例训练",
                "resource_complete": "学习资源",
                "textbook_section_completed": "教材小节",
                "training_workspace_task": "训练任务",
                "practice": "题目练习",
            }
            for item in events[:8]:
                title = str(item.get("title") or "").strip()
                knowledge_points = [
                    str(value).strip()
                    for value in item.get("knowledge_points") or []
                    if str(value).strip()
                    and not re.fullmatch(
                        r"(?:KP[_-]?)?\d{3,}|[A-Z]{2,}[_-][A-Z0-9_-]+",
                        str(value).strip(),
                        flags=re.IGNORECASE,
                    )
                ]
                label = title or "、".join(knowledge_points[:3])
                labels.append(
                    label or type_labels.get(item.get("activity_type"), "学习任务")
                )
            unique_labels = list(dict.fromkeys(labels))
            return (
                f"近{window_days}天有{len(events)}条可确认的学习完成记录，"
                f"主要包括：{'、'.join(unique_labels) if unique_labels else '已完成学习任务'}。"
            )
        if query_kind == "next_learning":
            plan = dict(evidence.get("plan_progress") or {})
            daily = dict(plan.get("daily_task") or {})
            if daily and str(daily.get("status") or "") not in {
                "completed", "done", "passed"
            }:
                chapter = str(daily.get("learning_chapter") or "").strip()
                focus = [
                    str(value).strip()
                    for value in daily.get("focus_knowledge_points") or []
                    if str(value).strip()
                ]
                target = chapter or "、".join(focus[:3]) or "当前当日任务"
                return (
                    f"接下来优先继续“{target}”，先完成当前任务的验收要求。"
                    "这是现有计划内尚未完成的内容，系统没有因此新建短期计划。"
                )
            mastery = dict(evidence.get("mastery_and_review") or {})
            review_tasks = [
                item
                for item in mastery.get("review_tasks") or []
                if str(item.get("status") or "") in {"pending", "due", "active"}
            ]
            if review_tasks:
                names = [
                    str(item.get("kp_name") or item.get("title") or "").strip()
                    for item in review_tasks[:3]
                    if str(item.get("kp_name") or item.get("title") or "").strip()
                ]
                return (
                    "接下来优先处理已到期复习"
                    f"{'：' + '、'.join(names) if names else ''}，完成对应复习题后再推进新内容。"
                )
            weak_names = [
                str(item.get("kp_name") or "").strip()
                for item in mastery.get("mastery") or []
                if str(item.get("kp_name") or "").strip()
            ][:3]
            if weak_names:
                return (
                    f"接下来优先巩固{'、'.join(weak_names)}。"
                    "这些知识点在已完成题目形成的掌握证据中相对薄弱。"
                )
            return (
                "当前没有足够的已完成任务、复习或掌握度证据来可靠判断下一步重点。"
                "先完成现有计划中的一个可核验任务，系统再据此给出建议。"
            )
        if query_kind == "progress_summary":
            current = dict(evidence.get("current_window") or {})
            return (
                f"近{window_days}天已完成{int(current.get('questions_completed') or 0)}题，"
                f"其中答对{int(current.get('correct_answers') or 0)}题、"
                f"答错{int(current.get('incorrect_answers') or 0)}题；"
                f"记录到的有效专注时长为{int(current.get('focus_minutes') or 0)}分钟。"
            )
        if query_kind == "mastery_status":
            mastery = list(evidence.get("mastery") or [])
            names = [
                str(item.get("kp_name") or "").strip()
                for item in mastery[:5]
                if str(item.get("kp_name") or "").strip()
            ]
            return (
                "当前有掌握度证据的知识点中，优先需要关注"
                f"{'、'.join(names)}。具体掌握结论只依据已完成题目的服务端记录。"
            )
        if query_kind == "review_status":
            tasks = list(evidence.get("review_tasks") or [])
            pending = sum(
                str(item.get("status") or "") in {"pending", "due", "active"}
                for item in tasks
            )
            return f"当前复习记录中有{pending}项待处理任务。复习队列本身不代表已经完成复习。"
        requested_scope = str(evidence.get("requested_scope") or "")
        if requested_scope == "long_term":
            long_term = dict(evidence.get("long_term") or {})
            content = str(long_term.get("content") or "").strip()
            return content or "当前还没有有效的长期学习计划。"
        if requested_scope == "short_term":
            short_term = dict(evidence.get("short_term") or {})
            content = str(short_term.get("content") or "").strip()
            return content or "当前还没有有效的短期学习计划。"
        if requested_scope == "daily_task":
            daily_task = dict(evidence.get("daily_task") or {})
            content = str(
                daily_task.get("task_content")
                or daily_task.get("content")
                or ""
            ).strip()
            return content or "当前还没有有效的今日任务。"
        long_term = evidence.get("long_term") or {}
        stages = list(long_term.get("stage_progress") or [])
        current = next(
            (item for item in stages if item.get("status") == "in_progress"),
            None,
        )
        if current:
            return (
                f"当前长期规划推进到“{current.get('name') or '当前阶段'}”。"
                "只有阶段通过指标取得服务端核验证据后，系统才会推进下一阶段。"
            )
        return "当前已有规划记录，但还没有可确认的阶段推进证据。"

    @staticmethod
    def _planning_draft_schema(plan_scope: Any) -> dict[str, Any]:
        """Tiny business-agent envelope: prose is the only planning source."""

        return {
            "type": "object",
            "required": ["plan_document"],
            "properties": {
                "plan_document": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "详细自然语言计划文档。必须把当前层的期限、阶段/节点、教材、"
                        "章节、知识点、预期产出和完成标准写在正文中，供 Compiler 提取。"
                    ),
                },
                "selected_path_candidate_id": {
                    "type": ["string", "null"],
                    "description": "仅在需要从系统候选中选择路径时填写；不得生成新ID。",
                },
            },
            "additionalProperties": False,
        }

    async def _complete_plan_draft(
        self,
        context: dict[str, Any],
        payload: dict[str, Any],
        prompt_skill: str,
        *,
        permission_note: str,
    ) -> dict[str, Any]:
        """Ask Diagnosis for a tiny envelope whose only content is prose.

        ``selected_path_candidate_id`` is the sole optional decision field.
        All executable plan fields remain owned by PlanContractCompiler.
        """

        model_context = build_model_context(
            context,
            target_agent="diagnosis_agent",
            prompt_skill=prompt_skill,
            payload=payload,
            permission_note=permission_note,
        )
        # An omitted scope is a legacy full-output caller.  Keep that explicit
        # compatibility mode until the caller supplies one of the three
        # compiler-owned scopes; scoped production planning never enters it.
        try:
            raw = await self.chat_model.complete_json("diagnosis_agent", model_context)
        except ModelResponseError as exc:
            # Both prose and structured paths hit a persistent empty stream.
            # Do not let it escape as a raw model failure: return an empty
            # envelope so the caller's compile gate reports a bounded error.
            if exc.reason in {"empty_stream", "empty_response"}:
                return {}
            raise
        if not isinstance(raw, dict):
            return {}
        if isinstance(raw.get("plan_document"), str) and raw["plan_document"].strip():
            return {
                "plan_document": raw["plan_document"].strip(),
                **({"selected_path_candidate_id": raw["selected_path_candidate_id"]}
                   if raw.get("selected_path_candidate_id") else {}),
            }
        return raw

    @staticmethod
    def _scoped_output_for_compilation(
        plan_scope: str,
        output: ThreeLayerPlanningModelOutput,
    ) -> dict[str, Any]:
        value = output.model_dump(mode="json")
        if plan_scope == "short_term":
            value["duration_days"] = value.get("short_term_duration_days")
            value["progression_nodes"] = value.get(
                "short_term_progression_nodes"
            )
        fields = {
            "long_term": (
                "long_term_plan_content",
                "total_duration_days",
                "long_term_plan_stages",
            ),
            "short_term": (
                "short_term_plan_content",
                "duration_days",
                "progression_nodes",
                "expected_output",
                "completion_criteria",
                "selected_textbook_route_id",
                "selected_stage_id",
                "selected_books",
                "selection_reason",
            ),
            "daily_task": (
                "daily_task_content",
                "learning_chapter",
                "focus_knowledge_points",
                "estimated_minutes",
                "expected_output",
                "completion_criteria",
            ),
        }[plan_scope]
        return {field: value[field] for field in fields if value.get(field) is not None}

    @classmethod
    def _compiler_route_context(cls, route_context: dict[str, Any]) -> dict[str, Any]:
        textbook_resolution = route_context.get("textbook_route") or {}
        textbook_route = (
            textbook_resolution.get("route")
            if textbook_resolution.get("planning_status") == "resolved"
            else None
        ) or {}
        stages = list(textbook_route.get("stages") or [])
        if not stages:
            stages = cls._planning_phases(route_context)
        return {
            "planning_status": route_context.get("planning_status"),
            "stages": [
                {
                    "stage_id": stage.get("stage_id") or stage.get("phase_id"),
                    "name": stage.get("name"),
                    "books": cls._string_list(stage.get("books")),
                    "goal": stage.get("objective"),
                    "exit_evidence": cls._string_list(stage.get("exit_evidence")),
                }
                for stage in stages
            ],
        }

    @staticmethod
    def _parent_plan_constraints(
        context: dict[str, Any], plan_scope: Any
    ) -> dict[str, Any]:
        if plan_scope != "short_term":
            return {}
        parent = context.get("current_long_term_plan") or {}
        stages = (
            parent.get("stages", [])
            if isinstance(parent, dict)
            else getattr(parent, "stages", [])
        ) or []
        selection = (
            parent.get("textbook_selection")
            if isinstance(parent, dict)
            else getattr(parent, "textbook_selection", None)
        ) or {}
        selected_stage_id = (
            selection.get("stage_id")
            if isinstance(selection, dict)
            else getattr(selection, "stage_id", None)
        )
        selected_stage = next(
            (
                stage
                for stage in stages
                if str(
                    stage.get("stage_id")
                    if isinstance(stage, dict)
                    else getattr(stage, "stage_id", "")
                )
                == str(selected_stage_id or "")
            ),
            stages[0] if stages else None,
        )
        duration_days = (
            selected_stage.get("duration_days")
            if isinstance(selected_stage, dict)
            else getattr(selected_stage, "duration_days", None)
            if selected_stage is not None
            else None
        )
        return {
            "current_stage_id": selected_stage_id,
            "current_stage_duration_days": duration_days,
        }

    @staticmethod
    def _apply_compiled_contract(
        raw_output: dict[str, Any],
        compiled: CompiledPlanContractResult,
    ) -> dict[str, Any]:
        normalized = dict(raw_output)
        contract = compiled.contract
        if isinstance(contract, CompiledLongTermContract):
            normalized.update(
                long_term_plan_content=contract.long_term_plan_content,
                total_duration_days=contract.total_duration_days,
                long_term_plan_stages=[
                    {
                        "stage": stage.stage,
                        "stage_name": stage.stage_name,
                        "book": stage.books,
                        "goal": stage.goal,
                        "duration_days": stage.duration_days,
                        "schedule_summary": stage.schedule_summary,
                    }
                    for stage in contract.stages
                ],
            )
        elif isinstance(contract, CompiledShortTermContract):
            normalized.update(
                short_term_plan_content=contract.short_term_plan_content,
                duration_days=contract.duration_days,
                progression_nodes=contract.progression_nodes,
                expected_output=contract.expected_output,
                completion_criteria=contract.completion_criteria,
                selected_stage_id=contract.selected_stage_id,
                selected_books=contract.selected_books,
            )
        else:
            normalized.update(contract.model_dump(exclude={"scope", "field_anchors"}))
        return normalized

    async def _clarification_questions(
        self,
        context: dict[str, Any],
        planning_readiness: Any,
    ) -> list[str]:
        """Let Diagnosis phrase a system-bounded prerequisite question."""

        fallback = [
            str(item).strip()
            for item in getattr(planning_readiness, "questions", [])[:1]
            if str(item).strip()
        ]
        if not fallback:
            return ["请补充当前规划所需的关键信息。"]
        field = str(getattr(planning_readiness, "next_profile_field", "") or "")
        field_goals = {
            "learning_goal": "确认用户要参加的具体考试、学习课程或希望形成的能力",
            "learning_background": "确认起点水平、专业背景以及已经学过的相关内容",
            "time_constraints": "确认可持续的每周学习天数、单次或每日可用时长",
        }
        profile = context.get("user_profile") if isinstance(context.get("user_profile"), dict) else {}
        known_profile = {
            key: value
            for key, value in profile.items()
            if key in {"learning_goal", "learning_background", "time_constraints", "completed_courses", "education", "learner_group"}
            and value not in (None, "", [], {})
            and not any(token in str(value).lower() for token in ("未填写", "未选择", "待确认", "unknown"))
        }
        try:
            skill = prompt_skill_registry.load("diagnosis_agent", "clarification")
            raw = await self.chat_model.complete_json(
                "diagnosis_clarification",
                build_model_context(
                    context,
                    target_agent="diagnosis_agent",
                    prompt_skill=skill,
                    payload={
                        "missing_field": field,
                        "question_goal": field_goals.get(field, "确认当前规划缺少的必要信息"),
                        "known_profile": known_profile,
                        "current_user_request": str(context.get("user_request") or ""),
                        "fallback_template": fallback[0],
                        "output_schema": {
                            "type": "object",
                            "required": ["question"],
                            "properties": {"question": {"type": "string"}},
                        },
                    },
                    permission_note=(
                        "只可改写当前一个追问的自然语言措辞；不得增加缺失字段、"
                        "推断用户事实、修改规划状态或输出系统字段。"
                    ),
                ),
            )
            question = str(raw.get("question") or "").strip() if isinstance(raw, dict) else ""
            if question and len(question) <= 220:
                return [question]
        except Exception:
            pass
        return fallback

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
        if "duration_days" in normalized:
            normalized.setdefault(
                "short_term_duration_days", normalized.pop("duration_days")
            )
        if "progression_nodes" in normalized:
            normalized.setdefault(
                "short_term_progression_nodes",
                normalized.pop("progression_nodes"),
            )
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
                stage["book"] = cls._string_list(source.get("books"))
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
        raw_output = dict(raw_output)
        if not raw_output.get("selected_path_candidate_id"):
            raw_output["selected_path_candidate_id"] = (
                raw_output.get("selected_candidate_id")
                or raw_output.get("candidate_id")
            )
        path_candidates = context.get("path_candidates")
        if isinstance(path_candidates, dict):
            eligible = [
                item
                for item in path_candidates.get("eligible", [])
                if isinstance(item, dict)
            ]
            # A candidate is optional.  When every generated candidate is
            # blocked, the model must continue along the already-approved
            # parent plan instead of turning a harmless repeated planning
            # request into an execution failure.  If at least one eligible
            # choice exists, the validator still rejects a blocked selection.
            if not eligible and raw_output.get("selected_path_candidate_id"):
                raw_output["selected_path_candidate_id"] = None
        current_long = context.get("current_long_term_plan") or {}
        current_short = context.get("current_short_term_plan") or {}
        current_task = context.get("current_learning_task") or {}

        def field(value: Any, name: str, default: Any = None) -> Any:
            if isinstance(value, dict):
                return value.get(name, default)
            return getattr(value, name, default)

        model_stages = raw_output.get("long_term_plan_stages")
        model_stages = model_stages if isinstance(model_stages, list) else []
        trusted_stages = []
        for index, phase in enumerate(cls._planning_phases(route_context), start=1):
            proposed = (
                model_stages[index - 1]
                if index <= len(model_stages)
                and isinstance(model_stages[index - 1], dict)
                else {}
            )
            trusted_stages.append(
                {
                    "stage": index,
                    "stage_name": str(phase.get("name") or f"阶段{index}"),
                    "book": cls._string_list(phase.get("books")),
                    "goal": str(phase.get("objective") or "完成本阶段目标"),
                    "duration_days": int(proposed.get("duration_days") or 0),
                    "schedule_summary": str(proposed.get("schedule_summary") or ""),
                }
            )
        if not trusted_stages or any(not stage["book"] for stage in trusted_stages):
            raise ValueError("长期规划缺少包含明确教材的系统可信路线，禁止生成占位阶段。")

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
            trusted_route_id = str(textbook_route.get("route_id"))
            stages_by_id = {
                str(stage.get("stage_id")): stage
                for stage in textbook_stages
                if stage.get("stage_id")
            }
            selected_stage = stages_by_id.get(str(selection["selected_stage_id"] or ""))
            if (
                str(selection["selected_textbook_route_id"] or "")
                != trusted_route_id
                or selected_stage is None
            ):
                selection = {
                    "selected_textbook_route_id": None,
                    "selected_stage_id": None,
                    "selected_books": [],
                    "selection_reason": None,
                }
            elif selection["selected_books"]:
                trusted_books = {
                    cls._normalized_book_name(book)
                    for book in selected_stage.get("books", [])
                }
                if any(
                    cls._normalized_book_name(book) not in trusted_books
                    for book in selection["selected_books"]
                ):
                    selection["selected_books"] = []
            first_stage = textbook_stages[0]
            selection = {
                "selected_textbook_route_id": selection["selected_textbook_route_id"]
                or trusted_route_id,
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
            "selected_path_candidate_id": field(
                current_task,
                "selected_path_candidate_id",
            ),
            "long_term_plan_content": str(
                field(current_long, "content") or "本次未生成长期规划。"
            ),
            "short_term_plan_content": str(
                field(current_short, "content") or "本次未生成短期计划。"
            ),
            "daily_task_content": str(
                field(current_task, "task_content") or "本次未生成当日任务。"
            ),
            "learning_chapter": str(field(current_task, "learning_chapter") or ""),
            "focus_knowledge_points": cls._string_list(
                field(current_task, "focus_knowledge_points")
            ),
            "estimated_minutes": int(field(current_task, "estimated_minutes") or fallback_minutes),
            "expected_output": str(field(current_task, "expected_output") or "本层规划结果。"),
            "completion_criteria": str(
                field(current_task, "completion_criteria") or "完成本层规划要求。"
            ),
            "long_term_plan_stages": trusted_stages,
            "total_duration_days": 0,
            "short_term_duration_days": 0,
            "short_term_progression_nodes": [],
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
            common["selected_path_candidate_id"] = (
                scoped.selected_path_candidate_id
            )
            common["total_duration_days"] = scoped.total_duration_days
            common["long_term_plan_stages"] = [
                stage.model_dump() for stage in scoped.long_term_plan_stages
            ]
        elif plan_scope == "short_term":
            scoped = ShortTermPlanningModelOutput.model_validate({
                key: value
                for key, value in raw_output.items()
                if key in ShortTermPlanningModelOutput.model_fields
            })
            scoped_dump = scoped.model_dump(
                exclude={"duration_days", "progression_nodes"}
            )
            # The four textbook-selection fields are system-owned.  Production
            # models only write plan_document, so ShortTermPlanning defaults
            # (None/[]) must not clobber the trusted-route fallback already
            # placed in ``common`` above.  But a model that explicitly selects
            # a stage (legacy structured output) keeps its choice so
            # prerequisite and stage checks still apply to it.
            for selection_field in (
                "selected_textbook_route_id",
                "selected_stage_id",
                "selected_books",
                "selection_reason",
            ):
                if scoped_dump.get(selection_field) in (None, "", []):
                    scoped_dump.pop(selection_field, None)
            common.update(scoped_dump)
            common["short_term_duration_days"] = scoped.duration_days
            common["short_term_progression_nodes"] = scoped.progression_nodes
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
    def _repair_short_term_plan_constraints(
        content: str,
        user_time_constraints: str,
    ) -> str:
        """Repair presentation-only violations without changing learning content."""

        repaired = re.sub(
            r"(?:具体)?(?:复习)?时间(?:点)?(?:待|由)系统[^。；\n]*(?:调度|安排)[^。；\n]*[。；]?",
            "",
            str(content or ""),
        )
        repaired = re.sub(
            r"系统根据[^。；\n]*(?:调度|安排)[^。；\n]*[。；]?",
            "",
            repaired,
        )
        repaired = re.sub(
            r"(?:建议)?在(?:每次|每个)[^，。；\n]{0,30}"
            r"(?:开始前|结束前|开始时|结束时|结束后)，?"
            r"(?:花|用)\s*[0-9一二两三四五六七八九十]+\s*分钟",
            "",
            repaired,
        )
        weekly_days_match = re.search(
            r"每周[^\d]{0,12}(\d+)\s*天",
            str(user_time_constraints or ""),
        )
        if weekly_days_match and int(weekly_days_match.group(1)) < 7:
            repaired = re.sub(r"每日|每天", "每个学习日", repaired)
            labels = iter(("前段", "中段", "验收段"))

            def replace_day_range(_: re.Match[str]) -> str:
                return next(labels, "验收段")

            repaired = re.sub(
                r"第\s*\d+\s*[-—至~]\s*\d+\s*天",
                replace_day_range,
                repaired,
            )

        cycle_markers = re.findall(
            r"第[一二12]周|周初|周中|周末|前半周?|后半周?|"
            r"本周|本周期|首个节点|下个节点|第二个节点|周期末|"
            r"第?[一二12](?:个)?节点|第一阶段|第二阶段|阶段[一二12]|"
            r"前段|中段|验收段",
            repaired,
        )
        if len(set(cycle_markers)) < 2:
            repaired = (
                repaired.rstrip()
                + "\n\n推进节奏按前段建立理解框架、中段完成辨析练习、周期末依据上述验收标准检查结果。"
            )
        return re.sub(r"\n{3,}", "\n\n", repaired).strip()

    @staticmethod
    def _normalized_book_name(value: Any) -> str:
        return re.sub(r"[《》\s·•（）()\-—_:：]", "", str(value or "")).lower()

    @staticmethod
    def _repair_full_capacity_mandate(
        content: str,
        user_time_constraints: str,
    ) -> str:
        """Keep available capacity from becoming a mandatory full-load schedule."""

        weekly = re.search(
            r"每周[^0-9]{0,12}(\d+)\s*天",
            str(user_time_constraints or ""),
        )
        per_session = re.search(
            r"(?:每次|每天)[^0-9]{0,12}(\d+(?:\.\d+)?)\s*(小时|分钟)",
            str(user_time_constraints or ""),
        )
        if weekly is None or per_session is None:
            return str(content or "")

        max_days = max(1, int(weekly.group(1)))
        value = float(per_session.group(1))
        max_minutes = int(round(value * 60)) if per_session.group(2) == "小时" else int(round(value))
        if max_minutes <= 0:
            return str(content or "")
        recommended_days = max_days - 1 if max_days > 1 else 1
        lower_minutes = max(15, int(round(max_minutes * 0.5 / 5) * 5))
        upper_minutes = max(lower_minutes, int(round(max_minutes * 0.75 / 5) * 5))
        recommendation = (
            f"建议初期每周安排{recommended_days}个学习日、每次"
            f"{lower_minutes}—{upper_minutes}分钟，其余可用容量用于休息、反馈和机动；"
            "后续依据真实完成率再调整。"
        )

        parts = re.split(r"(?<=。)|\n", str(content or ""))
        repaired: list[str] = []
        replaced = False
        for part in parts:
            has_mandate = any(
                token in part for token in ("保持", "必须", "确保", "固定", "充分利用")
            )
            has_full_days = bool(
                re.search(rf"每周[^。；\n]{{0,25}}{max_days}\s*(?:天|次)", part)
            )
            session_value = per_session.group(1)
            session_unit = per_session.group(2)
            has_full_session = bool(
                re.search(
                    rf"每次[^。；\n]{{0,25}}{re.escape(session_value)}\s*{session_unit}",
                    part,
                )
            )
            if has_mandate and has_full_days and has_full_session:
                if not replaced:
                    repaired.append(recommendation)
                    replaced = True
                continue
            repaired.append(part)
        normalized = "\n".join(
            line.strip() for line in repaired if line.strip()
        ).strip()
        session_ranges = [
            (int(start), int(end))
            for start, end in re.findall(
                r"第\s*(\d+)\s*[-—至~]\s*(\d+)\s*次",
                normalized,
            )
        ]
        standalone_sessions = [
            int(value)
            for value in re.findall(r"第\s*(\d+)\s*次", normalized)
        ]
        max_planned_session = max(
            [end for _, end in session_ranges] + standalone_sessions,
            default=0,
        )
        if max_planned_session > recommended_days and max_days > recommended_days:
            cycle_count = max(1, (max_planned_session + max_days - 1) // max_days)
            recommended_total = recommended_days * cycle_count
            if recommended_total < max_planned_session:
                def remap(index: int) -> int:
                    return max(
                        1,
                        min(
                            recommended_total,
                            (index * recommended_total + max_planned_session - 1)
                            // max_planned_session,
                        ),
                    )

                normalized = re.sub(
                    r"第\s*(\d+)\s*[-—至~]\s*(\d+)\s*次",
                    lambda match: (
                        f"第{remap(int(match.group(1)))}-"
                        f"{remap(int(match.group(2)))}次"
                    ),
                    normalized,
                )
                normalized = re.sub(
                    r"第\s*(\d+)\s*次",
                    lambda match: f"第{remap(int(match.group(1)))}次",
                    normalized,
                )
                normalized = re.sub(
                    rf"(?<!\d){max_planned_session}\s*次(?=计划学习|学习计划)",
                    f"{recommended_total}次",
                    normalized,
                )
        return normalized

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

    async def _plan_scope_clarification(
        self,
        context: dict[str, Any],
        plan_scope: Any,
        current_long_term_plan: Any,
        current_short_term_plan: Any,
    ) -> tuple[list[str], str] | None:
        if plan_scope == "unspecified":
            planner_question = str(
                context.get("planner_clarification_question") or ""
            ).strip()
            questions = (
                [planner_question]
                if planner_question
                else await self._scope_clarification_questions(context)
            )
            return (
                questions,
                "为了按正确的时间范围安排学习，我需要先确认你想制定哪一层计划。",
            )
        if plan_scope == "short_term" and not self._has_plan_content(current_long_term_plan):
            return (
                ["当前还没有有效长期规划，是否先建立长期规划？"],
                "短期计划必须基于有效长期规划制定。",
            )
        if plan_scope == "daily_task" and not self._has_plan_content(current_short_term_plan):
            return (
                ["当前还没有有效短期计划，是否先制定短期计划？"],
                "当日任务必须基于有效短期计划制定。",
            )
        return None

    async def _scope_clarification_questions(
        self,
        context: dict[str, Any],
    ) -> list[str]:
        fallback = "你这次希望先制定长期规划、短期计划，还是安排当日任务？"
        try:
            skill = prompt_skill_registry.load("diagnosis_agent", "clarification")
            raw = await self.chat_model.complete_json(
                "diagnosis_clarification",
                build_model_context(
                    context,
                    target_agent="diagnosis_agent",
                    prompt_skill=skill,
                    payload={
                        "missing_field": "plan_scope",
                        "question_goal": (
                            "结合用户已有规划和本轮诉求，确认本次真正要制定的规划层级"
                        ),
                        "known_profile": {
                            "has_long_term_plan": self._has_plan_content(
                                context.get("current_long_term_plan")
                            ),
                            "has_short_term_plan": self._has_plan_content(
                                context.get("current_short_term_plan")
                            ),
                        },
                        "current_user_request": str(
                            context.get("user_request") or ""
                        ),
                        "fallback_template": fallback,
                        "output_schema": {
                            "type": "object",
                            "required": ["question"],
                            "properties": {"question": {"type": "string"}},
                        },
                    },
                    permission_note=(
                        "由Diagnosis自然追问本次规划层级；一次只问一个问题，"
                        "不得生成规划内容、系统字段或假设用户选择。"
                    ),
                ),
            )
            question = (
                str(raw.get("question") or "").strip()
                if isinstance(raw, dict)
                else ""
            )
            if question and len(question) <= 220:
                return [question]
        except Exception:
            pass
        return [fallback]

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
        short_content = cls._trusted_reuse_content(
            change_decision.short_term_action,
            context.get("current_short_term_plan"),
            output.short_term_plan_content,
        )
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
        reusing_existing_long_term = change_decision.long_term_action == "reuse"
        if (
            not cls._has_publishable_planning_phases(route_context)
            and not reusing_existing_long_term
        ):
            raise ValueError("长期规划缺少包含明确教材的系统可信路线，禁止生成占位阶段。")
        if trusted_phases:
            long_term_stages = [
                LongTermPlanStage(
                    stage=index,
                    stage_name=str(phase.get("name") or f"阶段{index}"),
                    book=cls._string_list(phase.get("books")),
                    goal=str(phase.get("objective") or "完成本阶段目标"),
                    duration_days=(
                        output.long_term_plan_stages[index - 1].duration_days
                        if index <= len(output.long_term_plan_stages)
                        else 0
                    ),
                    schedule_summary=(
                        output.long_term_plan_stages[index - 1].schedule_summary
                        if index <= len(output.long_term_plan_stages)
                        else ""
                    ),
                )
                for index, phase in enumerate(trusted_phases, start=1)
            ]
        else:
            current_long = context.get("current_long_term_plan") or {}
            existing_stages = (
                current_long.get("stages", [])
                if isinstance(current_long, dict)
                else getattr(current_long, "stages", [])
            )
            long_term_stages = [
                LongTermPlanStage.model_validate(stage)
                for stage in (existing_stages or [])
            ]
        short_term_package = ShortTermLearningPackage(
            time_window_weeks=(
                max(1, (output.short_term_duration_days + 6) // 7)
                if output.short_term_duration_days > 0
                else None
            ),
            duration_days=(
                output.short_term_duration_days
                if output.short_term_duration_days > 0
                else None
            ),
            progression_nodes=output.short_term_progression_nodes,
            current_goal=short_content,
            task_blocks=(
                output.short_term_progression_nodes or [short_content]
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
                learning_chapter=output.learning_chapter,
                focus_knowledge_points=output.focus_knowledge_points,
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
                    learning_chapter=output.learning_task.learning_chapter,
                    focus_knowledge_points=output.learning_task.focus_knowledge_points,
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
    def _has_publishable_planning_phases(
        cls, route_context: dict[str, Any]
    ) -> bool:
        phases = cls._planning_phases(route_context)
        if not phases:
            return False
        placeholder_tokens = (
            "待确认", "未确认", "unknown", "tbd", "不可发布", "路线解析失败"
        )
        for phase in phases:
            books = cls._string_list(phase.get("books"))
            if not books or any(
                any(token in book.lower() for token in placeholder_tokens)
                for book in books
            ):
                return False
        return True

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

        def compact_plan(
            value: Any,
            *,
            layer: str,
        ) -> dict[str, Any]:
            if not value:
                return {}

            def field(name: str) -> Any:
                return (
                    value.get(name)
                    if isinstance(value, dict)
                    else getattr(value, name, None)
                )

            def serialized(item: Any) -> Any:
                return (
                    item.model_dump(mode="json")
                    if hasattr(item, "model_dump")
                    else item
                )

            fields = (
                ("task_content", "estimated_minutes", "expected_output", "completion_criteria")
                if layer == "daily_task"
                else ("content",)
            )
            result = {
                name: field(name)
                for name in fields
                if field(name) not in (None, "", [], {})
            }
            if layer == "long_term":
                stages = field("stages") or field("long_term_plan_stages")
                milestones = field("milestones")
                selection = field("textbook_selection")
                if stages:
                    result["stages"] = [serialized(item) for item in stages]
                if milestones:
                    result["milestones"] = [serialized(item) for item in milestones]
                if selection:
                    result["textbook_selection"] = serialized(selection)
            elif layer == "short_term":
                for name in (
                    "short_term_learning_package",
                    "short_term_focus",
                    "textbook_selection",
                ):
                    item = field(name)
                    if item not in (None, "", [], {}):
                        result[name] = serialized(item)
            return result

        plans: dict[str, Any] = {}
        if plan_scope in {"long_term", "short_term", "daily_task", None}:
            plans["long_term"] = compact_plan(
                context.get("current_long_term_plan"), layer="long_term"
            )
        if plan_scope in {"short_term", "daily_task", None}:
            plans["short_term"] = compact_plan(
                context.get("current_short_term_plan"), layer="short_term"
            )
        if plan_scope in {"daily_task", None}:
            plans["daily_task"] = compact_plan(
                context.get("current_learning_task"), layer="daily_task"
            )
        return {key: value for key, value in plans.items() if value}

    @classmethod
    def _model_learning_path_progress(cls, value: Any) -> dict[str, Any]:
        """Expose the path hierarchy in a model-sized, video-bound slice."""

        if not isinstance(value, dict) or not value:
            return {}
        stages = list(value.get("stages") or [])
        books = list(value.get("books") or [])
        compact_books = []
        for book in books[:4]:
            if not isinstance(book, dict):
                continue
            sections = list(book.get("sections") or [])
            compact_sections = []
            for section in sections[:12]:
                if not isinstance(section, dict):
                    continue
                video = section.get("video") if isinstance(section.get("video"), dict) else None
                compact_sections.append(
                    {
                        key: cls._bounded_model_value(section.get(key), depth=0)
                        for key in ("name", "chapter", "status", "mastery")
                        if section.get(key) not in (None, "", [], {})
                    }
                    | (
                        {
                            "video": {
                                key: video.get(key)
                                for key in (
                                    "bvid",
                                    "video_title",
                                    "part_title",
                                    "start_seconds",
                                    "end_seconds",
                                    "duration_seconds",
                                )
                                if video.get(key) not in (None, "", [])
                            }
                        }
                        if video
                        else {}
                    )
                )
            compact_books.append(
                {
                    "book": book.get("book"),
                    "status": book.get("status"),
                    "sections": compact_sections,
                }
            )
        current_section_value = value.get("current_section")
        current_section = {}
        if isinstance(current_section_value, dict):
            current_section = {
                key: cls._bounded_model_value(
                    current_section_value.get(key), depth=0
                )
                for key in ("name", "chapter", "status", "mastery")
                if current_section_value.get(key) not in (None, "", [], {})
            }
            current_video = current_section_value.get("video")
            if isinstance(current_video, dict):
                current_section["video"] = {
                    key: current_video.get(key)
                    for key in (
                        "bvid",
                        "video_title",
                        "part_title",
                        "start_seconds",
                        "end_seconds",
                        "duration_seconds",
                    )
                    if current_video.get(key) not in (None, "", [])
                }
        return {
            "availability": value.get("availability"),
            "current_stage_name": (
                str(value.get("current_stage_name") or "") or None
            ),
            "stages": [
                {
                    key: cls._bounded_model_value(stage.get(key), depth=0)
                    for key in ("name", "status", "order", "books")
                    if stage.get(key) not in (None, "", [], {})
                }
                for stage in stages[:6]
            ],
            "books": compact_books,
            "current_section": current_section,
        }

    @classmethod
    def _model_learning_state(cls, value: Any) -> dict[str, Any]:
        """Keep learning semantics while dropping persistence and trace bulk."""

        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        if not isinstance(value, dict):
            return {}
        return {
            key: cls._bounded_model_value(value.get(key), depth=0)
            for key in (
                "macro",
                "meso",
                "micro",
                "data_quality",
                "hard_constraints",
                "state_digest",
            )
            if value.get(key) not in (None, "", [], {})
        }

    @classmethod
    def _model_path_candidates(cls, value: Any) -> dict[str, list[dict[str, Any]]]:
        """Expose only fields needed for a model recommendation decision."""

        source = value if isinstance(value, dict) else {}

        def compact(item: Any) -> dict[str, Any]:
            if hasattr(item, "model_dump"):
                item = item.model_dump(mode="json")
            if not isinstance(item, dict):
                return {}
            return {
                key: cls._bounded_model_value(item.get(key), depth=0)
                for key in (
                    "candidate_id",
                    "scope",
                    "stage",
                    "books",
                    "knowledge_points",
                    "estimated_minutes",
                    "eligible",
                    "blocked_reasons",
                    "score",
                    "recommended_action",
                )
                if item.get(key) not in (None, "", [], {})
            }

        return {
            "eligible": [
                candidate
                for candidate in (
                    compact(item) for item in list(source.get("eligible") or [])[:5]
                )
                if candidate
            ],
            "blocked": [
                candidate
                for candidate in (
                    compact(item) for item in list(source.get("blocked") or [])[:3]
                )
                if candidate
            ],
        }

    @classmethod
    def _bounded_model_value(cls, value: Any, *, depth: int) -> Any:
        if depth >= 3:
            if isinstance(value, str):
                return value[:500]
            return value if isinstance(value, (int, float, bool)) else None
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        if isinstance(value, dict):
            ignored = {
                "learner_id",
                "generated_at",
                "source_refs",
                "evidence_refs",
                "state_digest",
            }
            return {
                str(key): cls._bounded_model_value(item, depth=depth + 1)
                for key, item in list(value.items())[:30]
                if str(key) not in ignored
            }
        if isinstance(value, list):
            return [
                cls._bounded_model_value(item, depth=depth + 1)
                for item in value[:12]
            ]
        if isinstance(value, str):
            return value[:500]
        return value

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
            reason=(
                f"依据已批准教材路线，当前处于“{stage.get('name')}”阶段，"
                f"本周期选择{'、'.join(str(book) for book in output.selected_books)}"
                "承接该阶段目标；具体学习时长以用户已登记的可持续时间安排为准。"
            ),
        )

    @classmethod
    def _confirmed_prerequisite_courses(
        cls, context: dict[str, Any], route_context: dict[str, Any]
    ) -> set[str]:
        confirmed, _ = cls._prerequisite_course_evidence(context, route_context)
        return confirmed

    @classmethod
    def _unmet_prerequisite_courses(
        cls, context: dict[str, Any], route_context: dict[str, Any]
    ) -> set[str]:
        _, unmet = cls._prerequisite_course_evidence(context, route_context)
        return unmet

    @classmethod
    def _prerequisite_course_evidence(
        cls, context: dict[str, Any], route_context: dict[str, Any]
    ) -> tuple[set[str], set[str]]:
        resolution = route_context.get("textbook_route") or {}
        route = resolution.get("route") or {}
        prerequisite_courses = {
            str(rule.get("course") or "").strip()
            for rule in route.get("prerequisites", [])
            if str(rule.get("course") or "").strip()
        }
        if not prerequisite_courses:
            return set(), set()

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
        unmet: set[str] = set()
        for course in prerequisite_courses:
            if any(course in text for text in confirmed_texts):
                confirmed.add(course)

        positive_markers = (
            "已完成", "已经完成", "已通过", "已经学完", "掌握了", "能够通过", "是的"
        )
        negative_markers = (
            "忘",
            "未完成",
            "没有完成",
            "没有学过",
            "没有学",
            "没学",
            "未学",
            "不会",
            "未通过",
        )

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
                    unmet.add(course)
                elif any(marker in window for marker in positive_markers):
                    confirmed.add(course)
                    unmet.discard(course)

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
        return confirmed, unmet

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

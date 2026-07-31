from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from competition_app.agents.common import envelope
from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.execution import ExecutionPlan, ExecutionStep
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.schemas import PlannerModelOutput
from competition_app.llm.stub import StubChatModel


AGENT_DEPENDENCIES: dict[str, list[str]] = {
    "memory_agent": [],
    "knowledge_base_agent": [],
    "default_route_resolver": [],
    "diagnosis_agent": ["default_route_resolver"],
    "learning_plan_service": ["diagnosis_agent"],
    "review_scheduler": ["diagnosis_agent", "knowledge_base_agent"],
    "expert_agent": [
        "knowledge_base_agent",
        "diagnosis_agent",
        "review_scheduler",
    ],
    "audit_agent": [
        "knowledge_base_agent",
        "diagnosis_agent",
        "review_scheduler",
        "expert_agent",
    ],
}

# Optional data-flow edges order selected agents without making the upstream
# agent mandatory. Diagnosis can run from learner data alone, but when Planner
# also selects Knowledge it must consume that evidence after retrieval finishes.
OPTIONAL_AGENT_DEPENDENCIES: dict[str, list[str]] = {
    "diagnosis_agent": ["knowledge_base_agent"],
    "expert_agent": ["learning_plan_service"],
}

AGENT_CAPABILITIES = {
    "memory_agent": "读取当前会话、确认偏好和临时约束，生成学习者上下文",
    "knowledge_base_agent": "解析知识查询并检索教材证据或候选题",
    "default_route_resolver": "根据学习目标解析已批准的默认学习路线或保守暂定路线",
    "diagnosis_agent": "分析学情、查询本人学习数据，并生成规划及任务建议",
    "learning_plan_service": "将规划建议转成带系统ID、版本和状态的正式计划与任务",
    "review_scheduler": "为需要立即生成复习资源的任务建立复习调度壳",
    "expert_agent": "根据正式学习任务和证据生成教学资源",
    "audit_agent": "审核长期/短期规划或专家资源的事实、约束、适配性和安全边界",
}

KNOWLEDGE_EXPLANATION_AGENTS = (
    "memory_agent",
    "knowledge_base_agent",
    "expert_agent",
    "audit_agent",
)

GENERAL_LEARNING_SUPPORT_AGENTS = KNOWLEDGE_EXPLANATION_AGENTS

PERSONALIZED_REVIEW_CARD_AGENTS = (
    "knowledge_base_agent",
    "default_route_resolver",
    "diagnosis_agent",
    "review_scheduler",
    "expert_agent",
    "audit_agent",
)


class PlannerDecision(BaseModel):
    task_type: str
    plan_scope: Literal["long_term", "short_term", "daily_task", "unspecified"] | None = None
    plan_action: Literal["reuse", "create_or_update", "clarify"] | None = None
    query_kind: Literal[
        "recent_learning",
        "next_learning",
        "progress_summary",
        "mastery_status",
        "review_status",
        "plan_progress",
    ] | None = None
    selected_agents: list[str] = Field(default_factory=list)
    routing_reason: str
    risk_level: str = "low"
    requires_audit: bool = True
    requires_learning_plan_output: bool = False
    requires_clarification: bool = False
    clarification_question: str | None = None
    casual_response: str | None = None


class PlannerAgent:
    def __init__(self, chat_model: ChatModel | None = None) -> None:
        self.chat_model = chat_model or StubChatModel()

    async def run(self, context: dict[str, Any]) -> AgentEnvelope[PlannerDecision]:
        routing_skill = prompt_skill_registry.load("planner_agent", "route_request")
        skills = prompt_skill_registry.load_many(
            [
                ("planner_agent", "learning_plan"),
                ("planner_agent", "personalized_review_card"),
                ("planner_agent", "paper_generation"),
                ("planner_agent", "knowledge_explanation"),
                ("planner_agent", "general_learning_support"),
                ("planner_agent", "learner_data_query"),
            ]
        )
        try:
            model_context = build_model_context(
                context,
                target_agent="planner_agent",
                prompt_skill=routing_skill,
                payload={
                    "user_request": context.get("user_request", ""),
                    "plan_scope": context.get("plan_scope"),
                    "plan_scope_hint": context.get("plan_scope_hint"),
                    "continued_plan_scope": context.get("continued_plan_scope"),
                    "available_minutes": context.get("available_minutes"),
                    "user_syllabus": context.get("user_syllabus"),
                    "syllabus_requirements": context.get("syllabus_requirements", []),
                    "syllabus_knowledge_points": context.get("syllabus_knowledge_points", []),
                    "existing_plan_state": {
                        "has_long_term_plan": bool(
                            context.get("current_long_term_plan", {}).get("content")
                        ),
                        "has_short_term_plan": bool(
                            context.get("current_short_term_plan", {}).get("content")
                        ),
                        "has_daily_task": bool(
                            context.get("current_learning_task", {}).get("task_content")
                        ),
                    },
                    "multi_scale_learning_state": context.get(
                        "planner_multiscale_summary", {}
                    ),
                    "conversation_context": {
                        "message_count": len(context.get("messages", [])),
                        "total_characters": sum(
                            len(str(item.get("content", "")))
                            for item in context.get("messages", [])
                        ),
                        "requires_compression": bool(
                            context.get("conversation_requires_compression", False)
                        ),
                        "recent_turns": [
                            {
                                "role": str(item.get("role", "")),
                                "content": str(item.get("content", ""))[:1200],
                            }
                            for item in context.get("messages", [])[-8:]
                            if isinstance(item, dict)
                        ],
                    },
                    "agent_capability_catalog": AGENT_CAPABILITIES,
                    "hard_routing_rules": [
                        "只选择完成当前任务所必需的Agent，不要求所有Agent参与。",
                        "纯问候、感谢、告别、询问助教能力等不包含学习任务的输入使用casual_conversation，不选择任何下游Agent。",
                        "casual_conversation必须结合本轮话语和最近对话生成casual_response；问候与真实任务并存时必须处理真实任务。",
                        "长期或短期规划必须选择audit_agent并在审核通过后才能由learning_plan_service发布；当日任务无需选择audit_agent。",
                        "plan_scope 是明确指定，有值时必须原样保留并路由为 learning_plan。",
                        "plan_scope_hint 只是规则提示，必须结合用户本轮语义和最近对话独立判断，可以改写。",
                        "continued_plan_scope 表示当前话语是上一轮规划调研的补充或纠正；有值时必须延续 learning_plan 和该层级。",
                        "制定或修改计划时必须输出 long_term、short_term、daily_task 或 unspecified 之一；纯学情查询可返回 null。",
                        "已有对应层级的当前有效计划时，除非用户明确说强制修改、重新制定、调整、更新或表达不满意，否则plan_action必须为reuse，不得重新生成。",
                        "用户只泛化地说“制定学习计划”且已有计划时，不追问层级：优先复用当前短期计划；没有短期计划时复用长期规划。只有明确提到今天或当日任务时才复用当日任务。",
                        "复用计划时不选择Diagnosis和Audit，只由LearningPlanService读取正式版本；系统会另行根据学习监控数据执行规划复盘。",
                        "是否需要追问由Planner结合本轮语义和最近对话判断；只有无法判断规划层级时才使用unspecified，并给出一条自然、可直接回答的clarification_question。",
                        "用户同时要求学习计划和学习卡片、复习卡或可直接学习资源时，交付物属于资源生成链路；该链路仍会先生成并落地学习计划。",
                        "用户要求组卷、试卷、模拟卷、测试卷或试卷蓝图时使用paper_generation；该链路只需要Knowledge、Expert、Audit，不强制生成学习计划或复习调度任务。",
                        "用户要求讲解、解释、介绍某个知识点或询问是什么、为什么、原理、区别时使用knowledge_explanation；只运行Knowledge、Expert、Audit，不生成学习计划、学习任务或复习调度。",
                        "用户要求梳理某教材章节的学习要点、阅读重点、学习方法或开放式学习支持，而不是解释单个概念时，使用general_learning_support；允许Expert用自然语言灵活组织，不强制套知识讲解五段式。",
                        "用户询问自己最近学了什么、接下来需要学什么、完成了多少题、学习进度、掌握情况、薄弱点、复习状态或现有计划进展时使用learner_data_query；只选择Diagnosis读取本人只读数据，不生成资源或改写计划。",
                        "“最近需要学习些什么、接下来该学什么、下一步学什么”是在查询下一步学习重点，不等于要求创建短期计划；除非用户明确要求制定、生成、安排或修改计划，否则使用learner_data_query。",
                        "learner_data_query必须返回query_kind：近期学习recent_learning、下一步重点next_learning、统计进度progress_summary、掌握情况mastery_status、复习状态review_status、计划进展plan_progress。",
                        "只有生成教学资源时才选择expert_agent；选择expert_agent时必须选择audit_agent。",
                        "Planner只负责编排，不生成学习规划内容、工具参数或系统ID。",
                    ],
                    "routing_skills": [skill.as_model_input() for skill in skills],
                    "output_schema": PlannerModelOutput.model_json_schema(),
                },
                permission_note="只能输出任务类型、参与Agent、路由理由、编排风险、审核需求，以及纯闲聊时面向用户的自然回复；不得生成检索表达、学习规划、调用工具或写业务状态。",
            )
            model_context["_result_validator"] = lambda result: PlannerModelOutput.model_validate(
                self._normalize_output(result, context)
            ).model_dump(mode="json")
            raw_output = await self.chat_model.complete_json(
                    "planner_agent",
                    model_context,
                )
            model_output = PlannerModelOutput.model_validate(
                self._normalize_output(raw_output, context)
            )
        except (ValidationError, ValueError) as exc:
            if context.get("terminal_trace"):
                context["terminal_trace"].validation(
                    "planner_agent", valid=False, detail="PlannerModelOutput"
                )
            raise ValueError("planner output validation failed") from exc
        if (
            context.get("conversation_requires_compression", False)
            and "memory_agent" not in model_output.selected_agents
        ):
            model_output = model_output.model_copy(
                update={
                    "selected_agents": ["memory_agent", *model_output.selected_agents],
                    "routing_reason": (
                        model_output.routing_reason
                        + " 会话已超过上下文阈值，系统强制先由记忆管理智能体压缩。"
                    )[:500],
                }
            )
        if (
            model_output.task_type != "casual_conversation"
            and "memory_agent" not in model_output.selected_agents
        ):
            model_output = model_output.model_copy(
                update={
                    "selected_agents": ["memory_agent", *model_output.selected_agents],
                    "routing_reason": (
                        model_output.routing_reason
                        + " 系统强制先由记忆管理智能体检索相关学习记忆、提取候选并检查冲突。"
                    )[:500],
                }
            )
        model_output = self.complete_required_selection(model_output)
        try:
            self.validate_selection(model_output)
        except ValueError as exc:
            if context.get("terminal_trace"):
                context["terminal_trace"].validation(
                    "planner_agent", valid=False, detail=str(exc)
                )
            raise ValueError("planner output validation failed") from exc
        if context.get("terminal_trace"):
            context["terminal_trace"].validation(
                "planner_agent", valid=True, detail="PlannerModelOutput"
            )
        return envelope(
            context,
            "planner_agent",
            "planner_decision",
            PlannerDecision(
                task_type=model_output.task_type,
                plan_scope=model_output.plan_scope,
                plan_action=model_output.plan_action,
                query_kind=model_output.query_kind,
                selected_agents=model_output.selected_agents,
                routing_reason=model_output.routing_reason,
                risk_level=model_output.risk_level,
                requires_audit=model_output.requires_audit,
                requires_learning_plan_output=bool(
                    context.get("requires_learning_plan_output")
                ),
                requires_clarification=model_output.requires_clarification,
                clarification_question=model_output.clarification_question,
                casual_response=model_output.casual_response,
            ),
        )

    @staticmethod
    def _normalize_output(raw_output: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Keep orchestration resilient when the model answers naturally.

        The model may omit routing boilerplate or use a Chinese task label. The
        system, not the model, owns dependency completion and safe defaults.
        """
        raw = dict(raw_output) if isinstance(raw_output, dict) else {}
        forbidden_system_fields = {
            "tools",
            "tool_calls",
            "steps",
            "depends_on",
            "plan_id",
            "task_id",
        }.intersection(raw)
        if forbidden_system_fields:
            raise ValueError(
                "planner output contains system-owned fields: "
                + ", ".join(sorted(forbidden_system_fields))
            )
        request = str(context.get("user_request", ""))
        task_aliases = {
            "组卷": "paper_generation", "试卷": "paper_generation", "模拟卷": "paper_generation",
            "讲解": "knowledge_explanation", "解释": "knowledge_explanation",
            "学习计划": "learning_plan", "复习计划": "learning_plan",
            "学习数据查询": "learner_data_query", "学习进度查询": "learner_data_query",
            "综合学习支持": "general_learning_support", "其他学习支持": "general_learning_support",
        }
        task_type = str(raw.get("task_type", "")).strip()
        task_type = task_aliases.get(task_type, task_type)
        personalized_resource_request = PlannerAgent._requests_personalized_resource(
            request
        )
        if PlannerAgent._is_external_information_request(request):
            context["external_information_request"] = True
            task_type = "general_learning_support"
        if (
            PlannerAgent._is_question_explanation_request(request)
            and not personalized_resource_request
        ):
            context["question_explanation_request"] = True
        if (
            PlannerAgent._is_emotional_support_request(request)
            and not PlannerAgent._has_explicit_business_delivery_request(request)
        ):
            context["emotional_support_request"] = True
        query_kind = (
            None
            if personalized_resource_request
            else PlannerAgent._learner_query_kind(
                request,
                raw.get("query_kind"),
            )
        )
        status_only_request = query_kind is not None
        explicit_planning_scope = context.get("plan_scope") in {
            "long_term", "short_term", "daily_task", "unspecified"
        }
        valid_scopes = {"long_term", "short_term", "daily_task", "unspecified"}
        continued_plan_scope = context.get("continued_plan_scope")
        continued_planning_request = continued_plan_scope in valid_scopes
        scoped_planning_request = explicit_planning_scope or continued_planning_request
        authoritative_plan_scope = (
            context.get("plan_scope")
            if explicit_planning_scope
            else continued_plan_scope
        )
        model_plan_scope = raw.get("plan_scope")
        plan_scope = (
            authoritative_plan_scope
            if scoped_planning_request
            else model_plan_scope
        )
        if scoped_planning_request:
            task_type = "learning_plan"
            query_kind = None
        elif context.get("question_explanation_request") and task_type in {
            "", "learner_data_query", "learning_plan", "personalized_review_card"
        }:
            task_type = "knowledge_explanation"
            query_kind = None
        elif context.get("emotional_support_request") and task_type in {
            "", "learner_data_query", "learning_plan", "personalized_review_card"
        }:
            task_type = "casual_conversation"
            query_kind = None
        elif (
            PlannerAgent._is_general_learning_support_request(request)
            and task_type in {"", "knowledge_explanation", "learning_plan"}
        ):
            task_type = "general_learning_support"
            query_kind = None
        elif personalized_resource_request and task_type in {
            "",
            "learner_data_query",
            "learning_plan",
        }:
            # A request for concrete questions/resources is not satisfied by a
            # status answer or by publishing a new plan. The model remains free
            # to choose another resource-producing route; this closure only
            # repairs routes that cannot deliver the explicitly requested item.
            task_type = "personalized_review_card"
            query_kind = None
        elif query_kind is not None:
            # The model owns semantic routing. This closure prevents a clear,
            # read-only learner-data request from falling into the legacy
            # review-card default when a model omits or mislabels task_type.
            task_type = "learner_data_query"
        if task_type not in {
            "casual_conversation", "general_learning_support",
            "knowledge_explanation", "learner_data_query", "learning_plan",
            "personalized_review_card", "paper_generation"
        }:
            task_type = (
                "paper_generation" if any(word in request for word in ("组卷", "试卷", "模拟卷", "测试卷"))
                else "knowledge_explanation" if any(word in request for word in ("讲解", "解释", "介绍", "为什么"))
                else "learning_plan" if any(word in request for word in ("学习计划", "复习计划", "制定计划"))
                else "learner_data_query" if query_kind is not None
                else "personalized_review_card"
            )
        clear_business_signals = (
            explicit_planning_scope
            or continued_planning_request
            or context.get("plan_scope_hint") in valid_scopes
            or any(
                phrase in request
                for phrase in (
                    "制定计划", "学习计划", "学习规划", "复习计划", "长期规划", "短期计划",
                    "今天任务", "今日任务", "安排任务", "安排一下学习", "组卷", "试卷",
                    "模拟卷", "测试卷", "讲解", "解释", "介绍", "为什么", "学习卡",
                    "复习卡", "学习资源", "直接学习",
                    "最近学", "学习进度", "完成了多少题", "做了多少题", "掌握情况",
                    "接下来学", "下一步学", "需要学习些什么", "应该学什么",
                    "薄弱点", "复习状态", "到期复习", "计划进展",
                )
            )
        )
        if task_type == "casual_conversation" and clear_business_signals:
            raise ValueError(
                "planner classified an explicit business request as casual conversation"
            )
        if task_type == "casual_conversation":
            plan_scope = None
            query_kind = None
        elif task_type == "learning_plan":
            # Priority: explicit caller choice > Planner semantics > deterministic
            # hint > clarification. This prevents a missing model field from
            # silently falling back to the legacy three-layer planning path.
            if status_only_request and not scoped_planning_request:
                plan_scope = None
            elif scoped_planning_request:
                plan_scope = authoritative_plan_scope
            elif model_plan_scope in valid_scopes:
                plan_scope = model_plan_scope
            elif context.get("plan_scope_hint") in valid_scopes:
                plan_scope = context.get("plan_scope_hint")
            else:
                plan_scope = "unspecified"
        else:
            plan_scope = None
            if task_type != "learner_data_query":
                query_kind = None
        existing_state = {
            "long_term": PlannerAgent._has_current_plan(
                context.get("current_long_term_plan"), "content"
            ),
            "short_term": PlannerAgent._has_current_plan(
                context.get("current_short_term_plan"), "content"
            ),
            "daily_task": PlannerAgent._has_current_plan(
                context.get("current_learning_task"), "task_content"
            ),
        }
        explicit_mutation = PlannerAgent._explicit_plan_mutation(request)
        plan_action = None
        if task_type == "learning_plan":
            # A vague planning request must not discard an already approved
            # plan. Planner semantics remain authoritative for explicit scopes;
            # this deterministic closure protects persistence from accidental
            # regeneration when the model omits or contradicts plan_action.
            if (
                not scoped_planning_request
                and context.get("plan_scope_hint") not in valid_scopes
                and not explicit_mutation
            ):
                if existing_state["short_term"]:
                    plan_scope = "short_term"
                elif existing_state["long_term"]:
                    plan_scope = "long_term"
            if explicit_mutation:
                plan_action = "create_or_update"
            elif (
                plan_scope in {"long_term", "short_term", "daily_task"}
                and existing_state[plan_scope]
            ):
                plan_action = "reuse"
            elif plan_scope == "unspecified":
                plan_action = "clarify"
            else:
                plan_action = "create_or_update"
        requires_clarification = bool(raw.get("requires_clarification"))
        clarification_question = str(raw.get("clarification_question") or "").strip() or None
        casual_response = str(raw.get("casual_response") or "").strip() or None
        if (
            task_type == "casual_conversation"
            and context.get("emotional_support_request")
            and (
                not casual_response
                or casual_response in {
                    "本次处理已经完成。你可以继续补充目标或提出下一步需求。",
                    "本次处理已经完成。",
                }
            )
        ):
            casual_response = (
                "我能理解你明天就要考试时的焦虑，紧张并不等于准备得不好。现在先不要试图把所有内容重学一遍："
                "先用10分钟列出最不稳的3个知识点，接着做一轮闭卷回忆或错题复盘，"
                "把每个点只补到‘能说出核心结论和辨析依据’；然后留出时间吃饭、休息并准备考试用品。"
                "如果愿意，把考试科目或最担心的题型告诉我，我可以继续帮你把剩余时间拆成更具体的冲刺安排。"
            )
        if (
            task_type == "learning_plan"
            and (plan_scope == "unspecified" or plan_action == "clarify")
        ):
            requires_clarification = True
        else:
            requires_clarification = False
            clarification_question = None
        if task_type != "casual_conversation":
            casual_response = None
        known_agents = set(AGENT_DEPENDENCIES)
        selected = [
            item for item in (raw.get("selected_agents") or raw.get("agents") or [])
            if item in known_agents
        ]
        if task_type == "casual_conversation":
            selected = []
        elif task_type == "learner_data_query":
            selected = ["diagnosis_agent"]
        elif scoped_planning_request:
            selected = [
                "diagnosis_agent",
                *(
                    ["audit_agent"]
                    if plan_scope in {"long_term", "short_term", "unspecified"}
                    else []
                ),
                "learning_plan_service",
            ]
        if task_type == "learner_data_query":
            selected = ["diagnosis_agent"]
        elif task_type == "paper_generation":
            selected = ["knowledge_base_agent", "expert_agent", "audit_agent"]
        elif task_type == "knowledge_explanation":
            selected = ["knowledge_base_agent", "expert_agent", "audit_agent"]
        elif task_type == "general_learning_support":
            selected = ["knowledge_base_agent", "expert_agent", "audit_agent"]
        elif task_type == "learning_plan":
            # Preserve the Planner's semantic choice. Knowledge is optional for
            # planning and must not be injected merely because the task is a
            # learning plan. Dependency completion below only adds true backend
            # requirements such as DefaultRouteResolver for Diagnosis.
            selected = (
                ["learning_plan_service"]
                if plan_action == "reuse"
                else selected or ["diagnosis_agent", "learning_plan_service"]
            )
            if (
                plan_action != "reuse"
                and plan_scope in {"long_term", "short_term", "unspecified"}
                and "audit_agent" not in selected
            ):
                selected.insert(
                    selected.index("learning_plan_service")
                    if "learning_plan_service" in selected
                    else len(selected),
                    "audit_agent",
                )
        elif task_type != "learner_data_query":
            selected = selected or [
                "knowledge_base_agent", "diagnosis_agent",
                "review_scheduler", "expert_agent", "audit_agent",
            ]
        if task_type == "personalized_review_card":
            asks_for_plan = any(
                word in request for word in ("学习计划", "复习计划", "制定计划", "规划")
            )
            if not asks_for_plan:
                selected = [agent for agent in selected if agent != "learning_plan_service"]
        routing_reason = (
            "用户本轮仅进行普通对话，不启动学习规划、知识检索、资源生成或审核流程。"
            if task_type == "casual_conversation"
            else str(raw.get("routing_reason") or "根据用户请求选择最小可执行流程。")
        )
        if task_type == "learner_data_query":
            routing_reason = (
                "用户在查询自己的学习记录或学习状态。Diagnosis 只调用当前用户的"
                "只读学习数据工具并形成自然语言回答；不检索教材、不生成资源、"
                "不修改计划，也不触发审核。"
            )
        if task_type == "general_learning_support":
            routing_reason = (
                "用户需要开放式学习支持而不是正式规划或单一概念释义。Knowledge 提供"
                "教材依据，Expert 依据用户表述灵活组织自然语言学习要点，Audit 只审核"
                "事实与教学安全；不创建计划、任务或复习调度。"
            )
        if task_type == "personalized_review_card" and personalized_resource_request:
            routing_reason = (
                "用户需要基于本人学习状态获得可直接使用的题目或学习资源。"
                "系统先读取学情并检索匹配的教材与题目证据，再由 Expert 形成"
                "个性化练习资源并由 Audit 审核；不因本次建议而重写学习计划。"
            )
        if task_type == "learning_plan" and plan_scope in {
            "long_term", "short_term", "daily_task"
        }:
            scope_label = {
                "long_term": "长期规划",
                "short_term": "短期计划",
                "daily_task": "当日任务",
            }[plan_scope]
            routing_reason = (
                f"当前已有有效{scope_label}，用户未明确要求强制修改；"
                "LearningPlanService 直接读取并复用正式版本，同时由学习监控执行规划复盘。"
                if plan_action == "reuse"
                else (
                    f"用户要的是{scope_label}。Diagnosis 基于现有计划、学习状态和可用时间"
                    f"生成{scope_label}建议，"
                    + (
                        "Audit 先审核该规划，审核通过后由 LearningPlanService 落地；"
                        if plan_scope in {"long_term", "short_term"}
                        else "LearningPlanService 将其落地为正式结果；"
                    )
                    + "当前不生成教学资源，因此不选择 Expert 或 ReviewScheduler。"
                )
            )
        return {
            "task_type": task_type,
            "plan_scope": plan_scope,
            "plan_action": plan_action,
            "query_kind": query_kind,
            "requires_clarification": requires_clarification,
            "clarification_question": clarification_question,
            "casual_response": casual_response,
            "selected_agents": list(dict.fromkeys(selected)),
            "routing_reason": routing_reason,
            "risk_level": (
                "low"
                if task_type == "casual_conversation"
                else raw.get("risk_level")
                if raw.get("risk_level") in {"low", "medium", "high"}
                else "medium"
            ),
            "requires_audit": (
                False
                if task_type in {"casual_conversation", "learner_data_query"}
                or plan_action == "reuse"
                else True
                if task_type == "personalized_review_card"
                else True
                if task_type == "learning_plan"
                and plan_scope in {"long_term", "short_term"}
                else bool(raw.get("requires_audit", True))
            ),
            "fallback_policy": raw.get("fallback_policy", "fail_closed"),
        }

    @staticmethod
    def _has_current_plan(value: Any, content_field: str) -> bool:
        if not isinstance(value, dict):
            return False
        content = value.get(content_field)
        status = str(value.get("status") or "active")
        return bool(str(content or "").strip()) and status not in {
            "invalid", "expired", "retired", "completed"
        }

    @staticmethod
    def _explicit_plan_mutation(request: str) -> bool:
        normalized = "".join(str(request or "").split())
        exact = any(
            marker in normalized
            for marker in (
                "强制修改", "强制更新", "重新制定", "重新规划", "重新计划",
                "修改计划", "调整计划", "更新计划", "重做计划",
                "规划不满意", "计划不满意", "不符合预期",
            )
        )
        separated = (
            any(
                action in normalized
                for action in ("强制", "重新", "修改", "调整", "更新", "重做", "不满意")
            )
            and any(layer in normalized for layer in ("计划", "规划", "任务"))
        )
        return exact or separated

    @staticmethod
    def _requests_personalized_resource(request: str) -> bool:
        """Recognize an explicit deliverable, not a broad learning-status phrase.

        Planner still decides semantics. This narrow closure only prevents a
        request that explicitly asks for concrete questions/resources from
        being reduced to a read-only status answer.
        """
        text = "".join(str(request or "").split())
        personalized_context = any(
            marker in text
            for marker in (
                "薄弱点", "没掌握", "掌握情况", "学习状态", "学习进度",
                "错题", "需要巩固", "需要加强", "针对我", "适合我",
            )
        )
        concrete_deliverable = any(
            marker in text
            for marker in (
                "做哪些题", "该做什么题", "该做哪些题", "需要做什么题",
                "需要做哪些题", "推荐题目", "推荐练习", "练习题",
                "推荐资源", "哪些资源",
            )
        )
        return personalized_context and concrete_deliverable

    @staticmethod
    def _is_general_learning_support_request(request: str) -> bool:
        """Keep open-ended study support separate from plans and concept Q&A."""
        text = "".join(str(request or "").split())
        if any(
            marker in text
            for marker in (
                "制定计划", "学习计划", "学习规划", "安排任务", "今日任务",
                "今天任务", "组卷", "试卷", "推荐题目", "做哪些题",
            )
        ):
            return False
        open_support = any(
            marker in text
            for marker in (
                "学习要点", "学习重点", "阅读重点", "复习思路", "学习方法",
                "怎么学习", "应该怎么学", "帮我梳理", "带我梳理",
                "章节重点", "教材重点",
            )
        )
        learning_object = any(
            marker in text
            for marker in ("章节", "教材", "课本", "单元", "这一章", "这部分")
        )
        return open_support and learning_object

    @staticmethod
    def _is_external_information_request(request: str) -> bool:
        text = str(request or "").strip().lower()
        return any(
            marker in text
            for marker in (
                "天气", "气温", "降雨", "下雨", "空气质量", "台风",
                "距离下次", "考试时间", "考试日期", "什么时候考试",
                "报名时间", "截止日期", "日程", "赛程", "最新消息",
                "当前时间", "今天几号", "现在几点",
            )
        )

    @staticmethod
    def _is_question_explanation_request(request: str) -> bool:
        text = "".join(str(request or "").split())
        asks_to_explain = any(
            marker in text
            for marker in ("试述", "简述", "论述", "分析题", "这题", "这道题")
        )
        asks_for_difficulty_help = any(
            marker in text
            for marker in ("难", "不会", "不懂", "卡住", "看不懂", "怎么答", "答不出来")
        )
        has_question_context = any(
            marker in text
            for marker in ("题", "怎么答", "答不出来")
        )
        return asks_to_explain or (asks_for_difficulty_help and has_question_context)

    @staticmethod
    def _has_explicit_business_delivery_request(request: str) -> bool:
        text = "".join(str(request or "").split())
        return any(
            marker in text
            for marker in (
                "制定", "计划", "规划", "安排", "生成", "组卷", "试卷",
                "讲解", "解释", "复习卡", "学习卡", "学习资源",
            )
        )

    @staticmethod
    def _is_emotional_support_request(request: str) -> bool:
        text = "".join(str(request or "").split())
        return any(
            marker in text
            for marker in ("焦虑", "紧张", "害怕", "慌", "压力大", "崩溃", "没信心", "来不及")
        )

    @staticmethod
    def _learner_query_kind(request: str, model_value: Any = None) -> str | None:
        valid = {
            "recent_learning",
            "next_learning",
            "progress_summary",
            "mastery_status",
            "review_status",
            "plan_progress",
        }
        text = "".join(str(request or "").split())
        if any(
            marker in text
            for marker in (
                "制定", "生成", "安排", "修改", "调整", "重新规划", "重新计划",
                "讲解", "解释", "介绍", "出题", "组卷", "试卷", "复习卡", "学习卡",
            )
        ):
            return None
        signal_map = (
            (
                "next_learning",
                (
                    "最近需要学习", "近期需要学习", "接下来学什么",
                    "接下来该学", "接下来应该学", "下一步学什么",
                    "下一步该学", "下一步应该学", "现在该学什么",
                    "现在应该学什么", "需要学习些什么",
                ),
            ),
            ("plan_progress", ("计划进展", "规划进展", "计划进度", "规划进度", "阶段进展")),
            (
                "review_status",
                ("复习状态", "复习情况", "最近复习", "到期复习", "复习到期", "复习队列"),
            ),
            ("mastery_status", ("掌握情况", "掌握得怎么样", "薄弱点", "没掌握", "学情")),
            (
                "progress_summary",
                (
                    "学习进度", "学习状态", "学习情况", "完成了多少题", "做了多少题",
                    "学习了多久", "专注了多久", "任务完成率", "完成情况",
                ),
            ),
            (
                "recent_learning",
                ("最近学", "近期学", "这周学", "本周学", "学了些什么", "学过什么"),
            ),
        )
        if (
            any(marker in text for marker in ("接下来", "下一步", "最近需要", "近期需要"))
            and "学" in text
            and any(marker in text for marker in ("什么", "哪些", "重点", "方向"))
        ):
            return "next_learning"
        for kind, signals in signal_map:
            if any(signal in text for signal in signals):
                return kind
        model_kind = str(model_value or "").strip()
        return model_kind if model_kind in valid else None

    @staticmethod
    def validate_selection(output: PlannerModelOutput) -> None:
        dependencies = AGENT_DEPENDENCIES
        selected = set(output.selected_agents)
        if output.task_type == "casual_conversation":
            if selected:
                raise ValueError("casual conversation must not select downstream agents")
            return
        if output.task_type == "learning_plan" and output.plan_action == "reuse":
            if selected != {"learning_plan_service"}:
                raise ValueError(
                    "reusing a learning plan only requires learning_plan_service"
                )
            if output.requires_audit:
                raise ValueError("reusing an approved plan must not trigger a new audit")
            return
        if output.task_type == "learner_data_query":
            if selected - {"memory_agent", "diagnosis_agent"}:
                raise ValueError("learner data query selected unrelated agents")
            if "diagnosis_agent" not in selected:
                raise ValueError("learner data query requires diagnosis_agent")
            if output.requires_audit:
                raise ValueError("read-only learner data query must not require audit")
            return
        if not selected:
            raise ValueError("non-casual task requires at least one downstream agent")
        missing: dict[str, list[str]] = {}
        for agent in output.selected_agents:
            if output.task_type in {
                "paper_generation",
                "knowledge_explanation",
                "general_learning_support",
            }:
                continue
            if output.task_type == "learning_plan" and agent == "audit_agent":
                required_dependencies = ["diagnosis_agent"]
            elif output.task_type == "learning_plan" and agent == "learning_plan_service":
                required_dependencies = [
                    "diagnosis_agent",
                    *(
                        ["audit_agent"]
                        if output.plan_scope in {"long_term", "short_term"}
                        else []
                    ),
                ]
            else:
                required_dependencies = dependencies[agent]
            required = [name for name in required_dependencies if name not in selected]
            if required:
                missing[agent] = required
        if missing:
            detail = "; ".join(
                f"{agent} requires {','.join(required)}" for agent, required in missing.items()
            )
            raise ValueError(f"planner selected invalid agent dependencies: {detail}")
        if output.task_type == "learning_plan" and "learning_plan_service" not in selected:
            raise ValueError("learning_plan task requires learning_plan_service")
        if output.task_type == "learning_plan" and selected.intersection(
            {"review_scheduler", "expert_agent"}
        ):
            raise ValueError("learning_plan task selected unnecessary resource-generation agents")
        if (
            output.task_type == "learning_plan"
            and output.plan_scope in {"long_term", "short_term"}
            and "audit_agent" not in selected
        ):
            raise ValueError("long/short-term learning plan requires audit_agent")
        if output.task_type == "personalized_review_card" and not set(
            PERSONALIZED_REVIEW_CARD_AGENTS
        ).issubset(selected):
            raise ValueError(
                "personalized_review_card requires the complete delivery chain"
            )
        if "expert_agent" in selected and "audit_agent" not in selected:
            raise ValueError("expert output requires audit_agent")
        if output.task_type == "paper_generation" and not {
            "knowledge_base_agent", "expert_agent", "audit_agent"
        }.issubset(selected):
            raise ValueError("paper_generation requires knowledge, expert and audit")
        if output.task_type == "knowledge_explanation" and not {
            "knowledge_base_agent", "expert_agent", "audit_agent"
        }.issubset(selected):
            raise ValueError("knowledge_explanation requires knowledge, expert and audit")
        if output.task_type == "general_learning_support" and not {
            "knowledge_base_agent", "expert_agent", "audit_agent"
        }.issubset(selected):
            raise ValueError(
                "general_learning_support requires knowledge, expert and audit"
            )
        if output.task_type == "knowledge_explanation" and selected.intersection(
            {"diagnosis_agent", "learning_plan_service", "review_scheduler"}
        ):
            raise ValueError("knowledge_explanation selected planning or scheduling agents")
        if output.task_type == "general_learning_support" and selected.intersection(
            {"diagnosis_agent", "learning_plan_service", "review_scheduler"}
        ):
            raise ValueError(
                "general_learning_support selected planning or scheduling agents"
            )

    @staticmethod
    def complete_required_selection(output: PlannerModelOutput) -> PlannerModelOutput:
        """Close model routing over deterministic dependencies and delivery invariants.

        The model selects capabilities; the backend adds mandatory providers so a
        valid user request never fails merely because the model omitted a known
        delivery dependency. Knowledge is not a mandatory Diagnosis dependency;
        it is selected only when the task needs教材 evidence or resource generation.
        """
        if output.task_type == "casual_conversation":
            return output.model_copy(
                update={
                    "selected_agents": [],
                    "requires_audit": False,
                }
            )
        if output.task_type == "paper_generation":
            required = {"knowledge_base_agent", "expert_agent", "audit_agent"}
            selected_set = set(output.selected_agents) | required
            selected = [
                agent
                for agent in (
                    "memory_agent", "knowledge_base_agent", "expert_agent", "audit_agent"
                )
                if agent in selected_set
            ]
            return output.model_copy(
                update={
                    "selected_agents": selected,
                    "routing_reason": (
                        output.routing_reason
                        + " 系统将组卷能力展开为蓝图、分单元检索、整卷组装和审核步骤。"
                    )[:500],
                }
            )
        if output.task_type == "learner_data_query":
            selected_set = set(output.selected_agents) | {"diagnosis_agent"}
            return output.model_copy(
                update={
                    "selected_agents": [
                        agent
                        for agent in ("memory_agent", "diagnosis_agent")
                        if agent in selected_set
                    ],
                    "requires_audit": False,
                }
            )
        if output.task_type == "knowledge_explanation":
            selected_set = set(output.selected_agents) | {
                "knowledge_base_agent", "expert_agent", "audit_agent"
            }
            selected = [
                agent for agent in KNOWLEDGE_EXPLANATION_AGENTS if agent in selected_set
            ]
            return output.model_copy(
                update={
                    "selected_agents": selected,
                    "routing_reason": (
                        output.routing_reason
                        + " 系统已约束为知识检索、专家讲解和审核链路，不创建学习计划或复习任务。"
                    )[:500],
                }
            )
        if output.task_type == "general_learning_support":
            selected_set = set(output.selected_agents) | {
                "knowledge_base_agent", "expert_agent", "audit_agent"
            }
            selected = [
                agent
                for agent in GENERAL_LEARNING_SUPPORT_AGENTS
                if agent in selected_set
            ]
            return output.model_copy(
                update={
                    "selected_agents": selected,
                    "routing_reason": (
                        output.routing_reason
                        + " 系统保留证据检索与审核边界，正文由专家智能体自由组织。"
                    )[:500],
                }
            )
        if output.task_type == "personalized_review_card":
            selected_set = set(output.selected_agents) | set(PERSONALIZED_REVIEW_CARD_AGENTS)
            selected = [
                agent
                for agent in AGENT_DEPENDENCIES
                if agent in selected_set
            ]
            if selected == output.selected_agents:
                return output
            return output.model_copy(
                update={
                    "selected_agents": selected,
                    "routing_reason": (
                        output.routing_reason
                        + " 系统已补全个性化复习卡的确定性交付链。"
                    )[:500],
                }
            )
        if output.task_type == "learning_plan":
            if output.plan_action == "reuse":
                return output.model_copy(
                    update={
                        "selected_agents": ["learning_plan_service"],
                        "requires_audit": False,
                    }
                )
            selected_set = set(output.selected_agents) | {
                "default_route_resolver",
                "diagnosis_agent",
                "learning_plan_service",
            }
            if output.plan_scope in {"long_term", "short_term", "unspecified"}:
                selected_set.add("audit_agent")
            else:
                selected_set.discard("audit_agent")
            selected = [
                agent
                for agent in (
                    "memory_agent",
                    "knowledge_base_agent",
                    "default_route_resolver",
                    "diagnosis_agent",
                    "audit_agent",
                    "learning_plan_service",
                )
                if agent in selected_set
            ]
            return output.model_copy(
                update={
                    "selected_agents": selected,
                    "requires_audit": output.plan_scope
                    in {"long_term", "short_term", "unspecified"},
                }
            )
        dependencies = AGENT_DEPENDENCIES
        selected = list(output.selected_agents)
        changed = True
        while changed:
            changed = False
            for agent in list(selected):
                if agent not in dependencies:
                    continue
                for dependency in dependencies[agent]:
                    if dependency not in selected:
                        selected.append(dependency)
                        changed = True
        if output.task_type == "learning_plan" and "learning_plan_service" not in selected:
            selected.append("learning_plan_service")
            changed = True
        if (
            output.task_type == "learning_plan"
            and output.plan_scope in {"long_term", "short_term"}
            and "audit_agent" not in selected
        ):
            selected.append("audit_agent")
            changed = True
        if changed:
            # learning_plan_service itself requires Diagnosis; close once more.
            for dependency in AGENT_DEPENDENCIES["learning_plan_service"]:
                if dependency not in selected:
                    selected.append(dependency)
        ordered = [agent for agent in dependencies if agent in selected]
        if ordered == output.selected_agents:
            return output
        return output.model_copy(
            update={
                "selected_agents": ordered,
                "routing_reason": (
                    output.routing_reason
                    + " 系统已补全该任务的确定性依赖节点。"
                )[:500],
            }
        )

    @staticmethod
    def build_plan(decision: PlannerDecision) -> ExecutionPlan:
        # The planner model schema intentionally excludes backend-owned agents.
        # Dependency completion may add them, so validate the equivalent decision
        # shape directly instead of parsing it back through that model schema.
        PlannerAgent.validate_selection(decision)  # type: ignore[arg-type]
        if decision.task_type == "casual_conversation":
            raise ValueError("casual conversation does not require an execution plan")
        selected = set(decision.selected_agents)
        if decision.task_type == "learner_data_query":
            steps = []
            if "memory_agent" in selected:
                steps.append(ExecutionStep(step_id="memory", agent="memory_agent"))
            steps.append(
                ExecutionStep(
                    step_id="diagnosis",
                    agent="diagnosis_agent",
                    action="query_learner_data",
                    depends_on=["memory"] if "memory_agent" in selected else [],
                    timeout_seconds=300.0,
                )
            )
            return ExecutionPlan(
                plan_id="PLAN_DYNAMIC_LEARNER_DATA_QUERY",
                task_type=decision.task_type,
                steps=steps,
            )
        if decision.task_type == "paper_generation":
            steps = []
            if "memory_agent" in selected:
                steps.append(ExecutionStep(step_id="memory", agent="memory_agent"))
            steps.extend(
                [
                    ExecutionStep(
                        step_id="paper_blueprint",
                        agent="paper_blueprint_agent",
                        action="create_blueprint",
                        depends_on=["memory"] if "memory_agent" in selected else [],
                        # The business author and compiler each make a bounded
                        # model request; the step budget must cover both.
                        timeout_seconds=420.0,
                    ),
                    ExecutionStep(
                        step_id="question_pool",
                        agent="knowledge_base_agent",
                        action="retrieve_questions_by_blueprint",
                        depends_on=["paper_blueprint"],
                        # A paper can contain several blueprint units, and each
                        # unit may perform one formal pass plus one controlled
                        # expansion pass.  The generic 60-second agent timeout
                        # cancels the whole pool before those sequential,
                        # independently bounded lookups can finish.
                        timeout_seconds=600.0,
                    ),
                    ExecutionStep(
                        step_id="paper_assembly",
                        agent="paper_assembly_agent",
                        action="assemble_exam_paper",
                        depends_on=["paper_blueprint", "question_pool"],
                        timeout_seconds=900.0,
                    ),
                    ExecutionStep(
                        step_id="audit",
                        agent="audit_agent",
                        action="review_exam_paper",
                        depends_on=["paper_blueprint", "question_pool", "paper_assembly"],
                        timeout_seconds=420.0,
                    ),
                ]
            )
            return ExecutionPlan(
                plan_id="PLAN_DYNAMIC_PAPER_GENERATION",
                task_type=decision.task_type,
                steps=steps,
            )
        if decision.task_type in {
            "knowledge_explanation",
            "general_learning_support",
        }:
            steps = []
            if "memory_agent" in selected:
                steps.append(ExecutionStep(step_id="memory", agent="memory_agent"))
            steps.extend(
                [
                    ExecutionStep(
                        step_id="knowledge",
                        agent="knowledge_base_agent",
                        depends_on=["memory"] if "memory_agent" in selected else [],
                        timeout_seconds=300.0,
                    ),
                    ExecutionStep(
                        step_id="expert",
                        agent="knowledge_explanation_agent",
                        depends_on=(
                            ["memory", "knowledge"]
                            if "memory_agent" in selected
                            else ["knowledge"]
                        ),
                        timeout_seconds=360.0,
                    ),
                    ExecutionStep(
                        step_id="audit",
                        agent="audit_agent",
                        depends_on=["knowledge", "expert"],
                        timeout_seconds=300.0,
                    ),
                ]
            )
            return ExecutionPlan(
                plan_id=(
                    "PLAN_DYNAMIC_GENERAL_LEARNING_SUPPORT"
                    if decision.task_type == "general_learning_support"
                    else "PLAN_DYNAMIC_KNOWLEDGE_EXPLANATION"
                ),
                task_type=decision.task_type,
                steps=steps,
            )
        if decision.task_type == "personalized_review_card":
            if decision.requires_learning_plan_output:
                memory_dependencies = ["memory"] if "memory_agent" in selected else []
                steps = []
                if "memory_agent" in selected:
                    steps.append(ExecutionStep(step_id="memory", agent="memory_agent"))
                steps.extend([
                    ExecutionStep(
                        step_id="knowledge", agent="knowledge_base_agent",
                        depends_on=memory_dependencies,
                    ),
                    ExecutionStep(
                        step_id="route_resolution", agent="default_route_resolver",
                        depends_on=memory_dependencies,
                    ),
                    ExecutionStep(
                        step_id="diagnosis_long", agent="diagnosis_agent",
                        plan_scope="long_term",
                        depends_on=[*memory_dependencies, "knowledge", "route_resolution"],
                        timeout_seconds=720.0,
                    ),
                    ExecutionStep(
                        step_id="audit_long", agent="audit_agent",
                        action="review_learning_plan", plan_scope="long_term",
                        audit_subject="long_term_plan", depends_on=["diagnosis_long"],
                        timeout_seconds=300.0,
                    ),
                    ExecutionStep(
                        step_id="diagnosis_short", agent="diagnosis_agent",
                        plan_scope="short_term",
                        depends_on=[*memory_dependencies, "knowledge", "route_resolution", "diagnosis_long", "audit_long"],
                        timeout_seconds=720.0,
                    ),
                    ExecutionStep(
                        step_id="audit_short", agent="audit_agent",
                        action="review_learning_plan", plan_scope="short_term",
                        audit_subject="short_term_plan", depends_on=["diagnosis_short", "audit_long"],
                        timeout_seconds=300.0,
                    ),
                    ExecutionStep(
                        step_id="learning_plan", agent="learning_plan_service",
                        action="materialize_combined_plan",
                        depends_on=["diagnosis_long", "audit_long", "diagnosis_short", "audit_short"],
                    ),
                    ExecutionStep(
                        step_id="schedule", agent="review_scheduler",
                        depends_on=["knowledge", "diagnosis_short"],
                    ),
                    ExecutionStep(
                        step_id="expert", agent="expert_agent",
                        depends_on=["knowledge", "diagnosis_short", "learning_plan", "schedule"],
                    ),
                    ExecutionStep(
                        step_id="audit", agent="audit_agent",
                        audit_subject="resource",
                        depends_on=["knowledge", "diagnosis_short", "schedule", "expert", "audit_long", "audit_short"],
                    ),
                ])
                return ExecutionPlan(
                    plan_id="PLAN_DYNAMIC_COMBINED_REVIEW_CARD",
                    task_type=decision.task_type,
                    steps=steps,
                )
            if not decision.requires_learning_plan_output:
                selected.discard("learning_plan_service")
            step_id_by_agent = {
                "memory_agent": "memory",
                "knowledge_base_agent": "knowledge",
                "default_route_resolver": "route_resolution",
                "diagnosis_agent": "diagnosis",
                "learning_plan_service": "learning_plan",
                "review_scheduler": "schedule",
                "expert_agent": "expert",
                "audit_agent": "audit",
            }
            ordered_agents = [
                agent for agent in (
                    "memory_agent",
                    "knowledge_base_agent",
                    "default_route_resolver",
                    "diagnosis_agent",
                    "learning_plan_service",
                    "review_scheduler",
                    "expert_agent",
                    "audit_agent",
                ) if agent in selected
            ]
            steps = [
                ExecutionStep(
                    step_id=step_id_by_agent[agent],
                    agent=agent,
                    depends_on=(
                        (
                            ["memory"]
                            if "memory_agent" in selected and agent != "memory_agent"
                            else []
                        )
                        + [
                        step_id_by_agent[dependency]
                        for dependency in AGENT_DEPENDENCIES[agent]
                        + OPTIONAL_AGENT_DEPENDENCIES.get(agent, [])
                        if dependency in selected
                        ]
                    ),
                )
                for agent in ordered_agents
            ]
            return ExecutionPlan(
                plan_id="PLAN_DYNAMIC_PERSONALIZED_REVIEW_CARD",
                task_type=decision.task_type,
                steps=steps,
            )
        dependencies = AGENT_DEPENDENCIES
        step_id_by_agent = {
            "memory_agent": "memory",
            "knowledge_base_agent": "knowledge",
            "default_route_resolver": "route_resolution",
            "diagnosis_agent": "diagnosis",
            "learning_plan_service": "learning_plan",
            "review_scheduler": "schedule",
            "expert_agent": "expert",
            "audit_agent": "audit",
        }
        ordered_agents = [name for name in dependencies if name in selected]
        if decision.task_type == "learning_plan":
            ordered_agents = [
                name
                for name in (
                    "memory_agent",
                    "knowledge_base_agent",
                    "default_route_resolver",
                    "diagnosis_agent",
                    "audit_agent",
                    "learning_plan_service",
                )
                if name in selected
            ]
        steps = [
            ExecutionStep(
                step_id=step_id_by_agent[agent],
                agent=agent,
                # Diagnosis may need one initial structured generation plus one
                # validator-guided revision.  Each model request has its own
                # bounded timeout, so the generic 60-second step deadline would
                # otherwise cancel a valid failover/revision transaction early.
                timeout_seconds=720.0 if agent == "diagnosis_agent" else 300.0,
                depends_on=(
                    (
                        ["memory"]
                        if "memory_agent" in selected and agent != "memory_agent"
                        else []
                    )
                    + (
                        ["diagnosis"]
                        if decision.task_type == "learning_plan" and agent == "audit_agent"
                        else ["diagnosis", "audit"]
                        if decision.task_type == "learning_plan" and agent == "learning_plan_service" and "audit_agent" in selected
                        else [
                    step_id_by_agent[dependency]
                    for dependency in [
                        *dependencies[agent],
                        *(
                            OPTIONAL_AGENT_DEPENDENCIES.get(agent, [])
                            if decision.task_type != "paper_generation"
                            else []
                        ),
                    ]
                    if dependency in selected
                        ]
                    )
                ),
            )
            for agent in ordered_agents
        ]
        return ExecutionPlan(
            plan_id=f"PLAN_DYNAMIC_{decision.task_type.upper()}",
            task_type=decision.task_type,
            steps=steps,
        )

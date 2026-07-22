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
    "diagnosis_agent": "分析学情并生成长短期学习规划及任务建议",
    "learning_plan_service": "将规划建议转成带系统ID、版本和状态的正式计划与任务",
    "review_scheduler": "为需要立即生成复习资源的任务建立复习调度壳",
    "expert_agent": "根据正式学习任务和证据生成教学资源",
    "audit_agent": "审核专家资源的事实、适配性和安全边界",
}

KNOWLEDGE_EXPLANATION_AGENTS = (
    "memory_agent",
    "knowledge_base_agent",
    "expert_agent",
    "audit_agent",
)

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
    selected_agents: list[str] = Field(min_length=1)
    routing_reason: str
    risk_level: str = "low"
    requires_audit: bool = True
    requires_learning_plan_output: bool = False


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
            ]
        )
        try:
            raw_output = await self.chat_model.complete_json(
                    "planner_agent",
                    build_model_context(
                        context,
                        target_agent="planner_agent",
                        prompt_skill=routing_skill,
                        payload={
                            "user_request": context.get("user_request", ""),
                            "plan_scope": context.get("plan_scope"),
                            "plan_scope_hint": context.get("plan_scope_hint"),
                            "continued_plan_scope": context.get("continued_plan_scope"),
                            "available_minutes": context.get("available_minutes"),
                            "existing_plan_state": {
                                "has_long_term_plan": bool(
                                    context.get("current_long_term_plan", {}).get("content")
                                ),
                                "has_short_term_plan": bool(
                                    context.get("current_short_term_plan", {}).get("content")
                                ),
                            },
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
                                "仅制定学习或复习计划、且用户没有要求学习卡片或教学资源时，不需要review_scheduler、expert_agent、audit_agent。",
                                "plan_scope 是明确指定，有值时必须原样保留并路由为 learning_plan。",
                                "plan_scope_hint 只是规则提示，必须结合用户本轮语义和最近对话独立判断，可以改写。",
                                "continued_plan_scope 表示当前话语是上一轮规划调研的补充或纠正；有值时必须延续 learning_plan 和该层级。",
                                "制定或修改计划时必须输出 long_term、short_term、daily_task 或 unspecified 之一；纯学情查询可返回 null。",
                                "用户同时要求学习计划和学习卡片、复习卡或可直接学习资源时，交付物属于资源生成链路；该链路仍会先生成并落地学习计划。",
                                "用户要求组卷、试卷、模拟卷、测试卷或试卷蓝图时使用paper_generation；该链路只需要Knowledge、Expert、Audit，不强制生成学习计划或复习调度任务。",
                                "用户要求讲解、解释、介绍某个知识点或询问是什么、为什么、原理、区别时使用knowledge_explanation；只运行Knowledge、Expert、Audit，不生成学习计划、学习任务或复习调度。",
                                "只有生成教学资源时才选择expert_agent；选择expert_agent时必须选择audit_agent。",
                                "Planner只负责编排，不生成学习规划内容、工具参数或系统ID。",
                            ],
                            "routing_skills": [skill.as_model_input() for skill in skills],
                            "output_schema": PlannerModelOutput.model_json_schema(),
                        },
                        permission_note="只能输出任务类型、参与Agent、路由理由、编排风险和审核需求；不得生成检索表达、学习规划、调用工具或写业务状态。",
                    ),
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
        model_output = self.complete_required_selection(model_output)
        try:
            self.validate_selection(model_output)
        except ValueError as exc:
            if context.get("terminal_trace"):
                context["terminal_trace"].validation(
                    "planner_agent", valid=False, detail=str(exc)
                )
            raise ValueError("planner output validation failed") from exc
        if (
            "memory_agent" in model_output.selected_agents
            and not context.get("conversation_requires_compression", False)
        ):
            raise ValueError("planner selected memory_agent below the compression threshold")
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
                selected_agents=model_output.selected_agents,
                routing_reason=model_output.routing_reason,
                risk_level=model_output.risk_level,
                requires_audit=model_output.requires_audit,
                requires_learning_plan_output=bool(
                    context.get("requires_learning_plan_output")
                ),
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
        }
        task_type = str(raw.get("task_type", "")).strip()
        task_type = task_aliases.get(task_type, task_type)
        status_only_request = any(
            phrase in request for phrase in ("学习状态", "状态如何", "学情", "掌握情况")
        ) and not any(
            phrase in request
            for phrase in ("制定", "调整", "修改", "重做", "重新", "计划", "规划", "安排任务")
        )
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
        if task_type not in {
            "knowledge_explanation", "learning_plan", "personalized_review_card", "paper_generation"
        }:
            task_type = (
                "paper_generation" if any(word in request for word in ("组卷", "试卷", "模拟卷", "测试卷"))
                else "knowledge_explanation" if any(word in request for word in ("讲解", "解释", "介绍", "为什么"))
                else "learning_plan" if any(word in request for word in ("学习计划", "复习计划", "制定计划"))
                else "personalized_review_card"
            )
        if task_type == "learning_plan":
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
        known_agents = set(AGENT_DEPENDENCIES)
        selected = [
            item for item in (raw.get("selected_agents") or raw.get("agents") or [])
            if item in known_agents
        ]
        if scoped_planning_request:
            selected = ["diagnosis_agent", "learning_plan_service"]
        if task_type == "paper_generation":
            selected = ["knowledge_base_agent", "expert_agent", "audit_agent"]
        elif task_type == "knowledge_explanation":
            selected = ["knowledge_base_agent", "expert_agent", "audit_agent"]
        elif task_type == "learning_plan":
            # Preserve the Planner's semantic choice. Knowledge is optional for
            # planning and must not be injected merely because the task is a
            # learning plan. Dependency completion below only adds true backend
            # requirements such as DefaultRouteResolver for Diagnosis.
            selected = selected or ["diagnosis_agent", "learning_plan_service"]
        else:
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
        routing_reason = str(
            raw.get("routing_reason") or "根据用户请求选择最小可执行流程。"
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
                f"用户要的是{scope_label}。Diagnosis 基于现有计划、学习状态和可用时间"
                f"生成{scope_label}建议，LearningPlanService 将其落地为正式结果；"
                "当前不生成学习资源，因此不选择 Expert、Audit 或 ReviewScheduler。"
            )
        return {
            "task_type": task_type,
            "plan_scope": plan_scope,
            "selected_agents": list(dict.fromkeys(selected)),
            "routing_reason": routing_reason,
            "risk_level": raw.get("risk_level") if raw.get("risk_level") in {"low", "medium", "high"} else "medium",
            "requires_audit": bool(raw.get("requires_audit", True)),
            "fallback_policy": raw.get("fallback_policy", "fail_closed"),
        }

    @staticmethod
    def validate_selection(output: PlannerModelOutput) -> None:
        dependencies = AGENT_DEPENDENCIES
        selected = set(output.selected_agents)
        missing: dict[str, list[str]] = {}
        for agent in output.selected_agents:
            if output.task_type in {"paper_generation", "knowledge_explanation"}:
                continue
            required = [name for name in dependencies[agent] if name not in selected]
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
            {"review_scheduler", "expert_agent", "audit_agent"}
        ):
            raise ValueError("learning_plan task selected unnecessary resource-generation agents")
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
        if output.task_type == "knowledge_explanation" and selected.intersection(
            {"diagnosis_agent", "learning_plan_service", "review_scheduler"}
        ):
            raise ValueError("knowledge_explanation selected planning or scheduling agents")

    @staticmethod
    def complete_required_selection(output: PlannerModelOutput) -> PlannerModelOutput:
        """Close model routing over deterministic dependencies and delivery invariants.

        The model selects capabilities; the backend adds mandatory providers so a
        valid user request never fails merely because the model omitted a known
        delivery dependency. Knowledge is not a mandatory Diagnosis dependency;
        it is selected only when the task needs教材 evidence or resource generation.
        """
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
        selected = set(decision.selected_agents)
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
                        timeout_seconds=300.0,
                    ),
                    ExecutionStep(
                        step_id="paper_assembly",
                        agent="paper_assembly_agent",
                        action="assemble_exam_paper",
                        depends_on=["paper_blueprint", "question_pool"],
                        timeout_seconds=180.0,
                    ),
                    ExecutionStep(
                        step_id="audit",
                        agent="audit_agent",
                        action="review_exam_paper",
                        depends_on=["paper_blueprint", "question_pool", "paper_assembly"],
                        timeout_seconds=120.0,
                    ),
                ]
            )
            return ExecutionPlan(
                plan_id="PLAN_DYNAMIC_PAPER_GENERATION",
                task_type=decision.task_type,
                steps=steps,
            )
        if decision.task_type == "knowledge_explanation":
            steps = []
            if "memory_agent" in selected:
                steps.append(ExecutionStep(step_id="memory", agent="memory_agent"))
            steps.extend(
                [
                    ExecutionStep(
                        step_id="knowledge",
                        agent="knowledge_base_agent",
                        depends_on=["memory"] if "memory_agent" in selected else [],
                    ),
                    ExecutionStep(
                        step_id="expert",
                        agent="knowledge_explanation_agent",
                        depends_on=(
                            ["memory", "knowledge"]
                            if "memory_agent" in selected
                            else ["knowledge"]
                        ),
                    ),
                    ExecutionStep(
                        step_id="audit",
                        agent="audit_agent",
                        depends_on=["knowledge", "expert"],
                    ),
                ]
            )
            return ExecutionPlan(
                plan_id="PLAN_DYNAMIC_KNOWLEDGE_EXPLANATION",
                task_type=decision.task_type,
                steps=steps,
            )
        if decision.task_type == "personalized_review_card":
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
                ),
            )
            for agent in ordered_agents
        ]
        return ExecutionPlan(
            plan_id=f"PLAN_DYNAMIC_{decision.task_type.upper()}",
            task_type=decision.task_type,
            steps=steps,
        )

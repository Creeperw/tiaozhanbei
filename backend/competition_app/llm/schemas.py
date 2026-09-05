from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class PlannerRouteSelectionOutput(BaseModel):
    """Minimal semantic classification contract for Planner stage one.

    The classifier can choose only a task enum and cannot name a prompt skill,
    file, path, agent, tool, or workflow field.  The quoted text anchors the
    decision in the current user message rather than untrusted page/history
    content.
    """

    model_config = ConfigDict(extra="forbid")

    task_type: Literal[
        "casual_conversation",
        "general_learning_support",
        "knowledge_explanation",
        "learner_data_query",
        "learning_plan",
        "personalized_review_card",
        "paper_generation",
        "review_task_adjustment",
    ]
    task_source_quote: str = Field(
        min_length=1,
        max_length=160,
        description=(
            "逐字引用当前用户消息中最能支持该交付物判断的最短片段；"
            "不得引用历史、页面、画像或外部信息。"
        ),
    )
    reason: str = Field(
        min_length=1,
        max_length=500,
        description="简要说明用户本轮最终希望获得的交付物。",
    )


class PlannerModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: Literal[
        "casual_conversation",
        "general_learning_support",
        "knowledge_explanation",
        "learner_data_query",
        "learning_plan",
        "personalized_review_card",
        "paper_generation",
        "review_task_adjustment",
    ]
    review_adjustment: Literal[
        "reduce_capacity",
        "increase_capacity",
        "cancel_tasks",
        "snooze_tasks",
    ] | None = Field(
        default=None,
        description=(
            "仅在task_type为review_task_adjustment时使用，表示用户对复习任务"
            "安排的确定性执行意图：reduce_capacity=减少每日复习任务数量（如"
            "“复习任务太多了，少安排一点”）；increase_capacity=增加每日复习"
            "任务数量；cancel_tasks=取消指定复习任务；snooze_tasks=推迟指定"
            "复习任务。其他任务类型必须返回null。"
        ),
    )
    review_adjustment_source_quote: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        description=(
            "仅在task_type为review_task_adjustment时使用，逐字引用【当前用户消息】"
            "中明确要求减少、增加、取消或推迟复习任务的最短片段。只询问已有任务"
            "或学习状态时没有合法引文，绝不能路由为review_task_adjustment。"
            "其他任务类型返回null。"
        ),
    )
    query_kind: Literal[
        "recent_learning",
        "next_learning",
        "progress_summary",
        "mastery_status",
        "review_status",
        "plan_progress",
    ] | None = Field(
        default=None,
        description=(
            "仅在task_type为learner_data_query时标记要读取的学习者数据类别；"
            "其他任务返回null。"
        ),
    )
    plan_scope: Literal["long_term", "short_term", "daily_task", "unspecified"] | None = Field(
        default=None,
        description=(
            "学习规划的目标层级；输入已提供本次规划层级时必须原样返回，"
            "其中daily_task表示当日任务而非短期计划。制定或修改计划时"
            "必须返四个枚举值之一；纯学情查询和非规划任务返回null。"
        ),
    )
    plan_action: Literal["reuse", "create_or_update", "clarify"] | None = Field(
        default=None,
        description=(
            "学习规划的处理方式。用户查看或沿用某一明确层级且该层已有当前有效版本时返回reuse，"
            "此时plan_scope必须是long_term、short_term或daily_task，不能是unspecified；"
            "缺少对应计划或用户明确要求重新制定、修改、调整时返回create_or_update；"
            "用户要求制定或调整计划但结合上下文仍无法确定目标层级时返回clarify；即使系统已有"
            "一个或多个层级的计划，也不能擅自猜测要修改哪一层。非规划任务返回null。"
        ),
    )
    current_turn_available_minutes: int | None = Field(
        default=None,
        ge=1,
        le=24 * 60,
        description=(
            "仅当【当前用户消息】明确给出本轮/今日任务的可用分钟数时返回该整数；"
            "不得从历史对话、用户画像、当前页面或外部信息提取，不得把持续性的每周/每日偏好"
            "误当成本轮预算，也不得推测或换算含糊时间。没有明确值时返回null。"
        ),
    )
    current_turn_available_minutes_source_quote: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        description=(
            "填写current_turn_available_minutes时必须同时给出【当前用户消息】中的逐字短引文作为来源锚点；"
            "没有预算值时返回null。不得引用历史、画像、页面或外部信息。"
        ),
    )
    current_turn_available_minutes_scope: Literal[
        "daily_recurring", "today_only"
    ] | None = Field(
        default=None,
        description=(
            "分钟约束的适用范围：daily_recurring表示今后每天/规划每日容量，"
            "today_only表示仅本轮或今天；没有分钟值时返回null。"
        ),
    )
    requires_clarification: bool = Field(
        default=False,
        description="仅当用户要求规划、但结合上下文仍无法判断规划层级时为true。",
    )
    clarification_question: str | None = Field(
        default=None,
        max_length=220,
        description="需要澄清规划层级时给用户的一条自然语言追问；否则为null。",
    )
    casual_response: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description=(
            "仅在task_type为casual_conversation时生成面向用户的自然回复；"
            "应结合当前话语和最近对话，不得返回固定占位模板。"
        ),
    )
    selected_agents: list[
        Literal[
            "memory_agent",
            "knowledge_base_agent",
            "default_route_resolver",
            "diagnosis_agent",
            "learning_plan_service",
            "review_scheduler",
            "expert_agent",
            "audit_agent",
        ]
    ] = Field(
        default_factory=list,
        description="完成当前交付物所需的最小充分Agent集合，必须满足能力目录中的依赖关系。"
        "default_route_resolver 由系统在学情诊断前自动注入，通常无需模型选择。",
    )
    routing_reason: str = Field(
        min_length=1,
        max_length=1500,
        description="说明用户交付目标、所选Agent的必要性、未选资源Agent的原因以及审核需求。",
    )
    risk_level: Literal["low", "medium", "high"] = Field(
        description="仅表示编排风险；知识对象含糊、诉求冲突或安全边界不清时不得标为low。"
    )
    requires_audit: bool = True
    requires_learning_plan_output: bool = Field(
        default=False,
        description=(
            "仅当用户同一请求同时要求创建/调整学习计划和生成学习卡、复习卡或可直接学习资源时为true；"
            "必须依据完整语义判断，不得由代码关键词匹配。"
        ),
    )
    external_information_request: bool = Field(
        default=False,
        description="完整语义是否要求检索天气、日期、时效政策等外部当前事实。",
    )
    question_explanation_request: bool = Field(
        default=False,
        description=(
            "完整语义是否是在处理一份具体题目或定位答题卡点。必须有题干、选项、作答内容"
            "或对当前页面具体题目的明确指代作为依据；仅要求讲解某个知识点、学说或概念时必须为false。"
        ),
    )
    emotional_support_request: bool = Field(
        default=False,
        description="完整语义是否主要需要情绪支持而非启动学习业务写入流程。",
    )
    requires_memory_governance: bool = Field(
        default=False,
        description=(
            "仅当casual_conversation当前消息明确要求记录、更新或删除可持久复用的个人事实时为true。"
        ),
    )
    requires_knowledge_support: bool = Field(
        default=False,
        description=(
            "仅当learning_plan的规划内容依赖特定教材、章节、知识对象或教材证据时为true。"
        ),
    )
    fallback_policy: Literal["fail_closed", "needs_human_review"] = "fail_closed"

    @field_validator("selected_agents")
    @classmethod
    def selected_agents_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("selected_agents must be unique")
        return value

    @model_validator(mode="after")
    def casual_response_matches_task(self) -> "PlannerModelOutput":
        if bool(self.current_turn_available_minutes is not None) != bool(
            self.current_turn_available_minutes_source_quote
        ):
            raise ValueError(
                "current-turn minutes and source quote must be provided together"
            )
        if bool(self.current_turn_available_minutes is not None) != bool(
            self.current_turn_available_minutes_scope
        ):
            raise ValueError(
                "current-turn minutes and scope must be provided together"
            )
        if self.task_type == "casual_conversation" and not self.casual_response:
            raise ValueError("casual conversation requires an agent-generated response")
        if self.task_type != "casual_conversation" and self.casual_response is not None:
            raise ValueError("non-casual task must not include casual_response")
        if self.task_type == "learner_data_query" and self.query_kind is None:
            raise ValueError("learner data query requires query_kind")
        if self.task_type != "learner_data_query" and self.query_kind is not None:
            raise ValueError("query_kind is only valid for learner data queries")
        if self.task_type != "review_task_adjustment" and self.review_adjustment is not None:
            raise ValueError("review_adjustment is only valid for review task adjustments")
        if self.task_type == "review_task_adjustment" and self.review_adjustment is None:
            raise ValueError("review task adjustment requires an execution intent")
        if (
            self.task_type == "review_task_adjustment"
            and not self.review_adjustment_source_quote
        ):
            raise ValueError("review task adjustment requires a current-message quote")
        if (
            self.task_type != "review_task_adjustment"
            and self.review_adjustment_source_quote is not None
        ):
            raise ValueError(
                "review adjustment quote is only valid for review task adjustments"
            )
        if self.requires_learning_plan_output and self.task_type != "personalized_review_card":
            raise ValueError(
                "requires_learning_plan_output is only valid for personalized review cards"
            )
        if self.external_information_request and self.task_type != "general_learning_support":
            raise ValueError(
                "external_information_request requires general_learning_support"
            )
        if self.question_explanation_request and self.task_type != "knowledge_explanation":
            raise ValueError(
                "question_explanation_request requires knowledge_explanation"
            )
        if self.emotional_support_request and self.task_type != "casual_conversation":
            raise ValueError(
                "emotional_support_request requires casual_conversation"
            )
        if self.requires_memory_governance and self.task_type != "casual_conversation":
            raise ValueError(
                "requires_memory_governance requires casual_conversation"
            )
        if self.requires_knowledge_support and self.task_type != "learning_plan":
            raise ValueError(
                "requires_knowledge_support requires learning_plan"
            )
        return self


class PlannerBranchOutput(BaseModel):
    """Base for the minimal, branch-local Planner provider contracts.

    These models are deliberately independent from ``PlannerModelOutput``.
    The latter is an internal canonical representation and contains fields
    owned by several different workflows; exposing it to a provider makes
    cross-task fields appear valid even when ``extra=forbid`` is enabled.
    """

    model_config = ConfigDict(extra="forbid")


class PlannerCasualConversationOutput(PlannerBranchOutput):
    task_type: Literal["casual_conversation"]
    casual_response: str = Field(
        min_length=1,
        max_length=500,
        description="面向用户的简短自然语言回复；仅用于本轮纯对话交付物。",
    )
    emotional_support_request: bool = Field(
        default=False,
        description="本轮是否主要需要情绪支持而非学习业务处理。",
    )
    requires_memory_governance: bool = Field(
        default=False,
        description=(
            "当前用户消息是否明确要求记录、更新或删除一条可在未来复用的个人事实；"
            "普通问候、情绪支持、页面问答和仅询问既有记忆时为false。"
        ),
    )
    routing_reason: str = Field(
        min_length=1,
        max_length=1500,
        description="简要说明为何本轮只需要对话回复。",
    )

    @model_validator(mode="after")
    def response_is_present(self) -> "PlannerCasualConversationOutput":
        if not self.casual_response:
            raise ValueError("casual conversation requires an agent-generated response")
        return self


class PlannerGeneralLearningSupportOutput(PlannerBranchOutput):
    task_type: Literal["general_learning_support"]
    external_information_request: bool = Field(
        default=False,
        description="本轮是否需要查询外部当前事实。",
    )
    routing_reason: str = Field(
        min_length=1,
        max_length=1500,
        description="简要说明为何本轮需要开放式学习支持。",
    )


class PlannerKnowledgeExplanationOutput(PlannerBranchOutput):
    task_type: Literal["knowledge_explanation"]
    current_turn_available_minutes: int | None = Field(
        default=None,
        ge=1,
        le=24 * 60,
        description="仅当当前用户消息明确给出本轮可用分钟数时填写，否则为null。",
    )
    current_turn_available_minutes_source_quote: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        description="填写分钟数时，逐字引用当前用户消息中的预算依据，否则为null。",
    )
    current_turn_available_minutes_scope: Literal[
        "daily_recurring", "today_only"
    ] | None = None
    question_explanation_request: bool = Field(
        default=False,
        description="本轮是否是在处理一份具体题目或答题卡点。",
    )
    routing_reason: str = Field(
        min_length=1,
        max_length=1500,
        description="简要说明为何本轮需要知识讲解，而不是规划或学情查询。",
    )

    @model_validator(mode="after")
    def budget_fields_match(self) -> "PlannerKnowledgeExplanationOutput":
        if bool(self.current_turn_available_minutes) != bool(
            self.current_turn_available_minutes_source_quote
        ):
            raise ValueError(
                "current-turn minutes and source quote must be provided together"
            )
        if bool(self.current_turn_available_minutes) != bool(
            self.current_turn_available_minutes_scope
        ):
            raise ValueError("current-turn minutes and scope must be provided together")
        return self


class PlannerLearnerDataQueryOutput(PlannerBranchOutput):
    task_type: Literal["learner_data_query"]
    query_kind: Literal[
        "recent_learning",
        "next_learning",
        "progress_summary",
        "mastery_status",
        "review_status",
        "plan_progress",
    ]
    routing_reason: str = Field(
        min_length=1,
        max_length=1500,
        description="简要说明本轮要读取哪类学习者只读数据。",
    )


class PlannerLearningPlanOutput(PlannerBranchOutput):
    task_type: Literal["learning_plan"]
    plan_scope: Literal["long_term", "short_term", "daily_task", "unspecified"] | None = None
    plan_action: Literal["reuse", "create_or_update", "clarify"] | None = None
    current_turn_available_minutes: int | None = Field(
        default=None,
        ge=1,
        le=24 * 60,
        description="仅当当前用户消息明确给出本轮可用分钟数时填写，否则为null。",
    )
    current_turn_available_minutes_source_quote: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        description="填写分钟数时，逐字引用当前用户消息中的预算依据，否则为null。",
    )
    current_turn_available_minutes_scope: Literal[
        "daily_recurring", "today_only"
    ] | None = Field(
        default=None,
        description=(
            "daily_recurring用于每天/长期重复容量，today_only仅用于今天或本轮。"
        ),
    )
    requires_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=220)
    requires_knowledge_support: bool = Field(
        default=False,
        description=(
            "规划内容是否依赖当前消息指定的教材、章节、知识对象或教材证据；"
            "仅按时间、目标或学情安排且不需要教材依据时为false。"
        ),
    )
    routing_reason: str = Field(
        min_length=1,
        max_length=1500,
        description="简要说明本轮规划层级及读取、创建或澄清动作。",
    )

    @model_validator(mode="after")
    def plan_fields_match(self) -> "PlannerLearningPlanOutput":
        if bool(self.current_turn_available_minutes) != bool(
            self.current_turn_available_minutes_source_quote
        ):
            raise ValueError(
                "current-turn minutes and source quote must be provided together"
            )
        if bool(self.current_turn_available_minutes) != bool(
            self.current_turn_available_minutes_scope
        ):
            raise ValueError("current-turn minutes and scope must be provided together")
        if self.plan_scope == "unspecified" or self.plan_action == "clarify":
            if not self.requires_clarification:
                raise ValueError(
                    "an unspecified or clarify plan requires clarification"
                )
        elif self.plan_scope is not None and self.plan_action is not None and (
            self.requires_clarification or self.clarification_question
        ):
            raise ValueError(
                "an executable plan must not include clarification fields"
            )
        return self


class PlannerPersonalizedReviewCardOutput(PlannerBranchOutput):
    task_type: Literal["personalized_review_card"]
    requires_learning_plan_output: bool = Field(
        default=False,
        description="本轮是否同时要求创建或调整学习计划并生成可直接学习的资源。",
    )
    routing_reason: str = Field(
        min_length=1,
        max_length=1500,
        description="简要说明为何需要基于学习状态生成个性化复习资源。",
    )


class PlannerPaperGenerationOutput(PlannerBranchOutput):
    task_type: Literal["paper_generation"]
    routing_reason: str = Field(
        min_length=1,
        max_length=1500,
        description="简要说明为何本轮需要生成练习试卷。",
    )


class PlannerReviewTaskAdjustmentOutput(PlannerBranchOutput):
    task_type: Literal["review_task_adjustment"]
    review_adjustment: Literal[
        "reduce_capacity",
        "increase_capacity",
        "cancel_tasks",
        "snooze_tasks",
    ]
    review_adjustment_source_quote: str = Field(
        min_length=1,
        max_length=120,
        description="逐字引用当前用户消息中直接表达复习任务变更授权的最短片段。",
    )
    routing_reason: str = Field(
        min_length=1,
        max_length=1500,
        description="简要说明本轮复习任务调整意图。",
    )


PLANNER_BRANCH_OUTPUT_MODELS: dict[str, type[PlannerBranchOutput]] = {
    "casual_conversation": PlannerCasualConversationOutput,
    "general_learning_support": PlannerGeneralLearningSupportOutput,
    "knowledge_explanation": PlannerKnowledgeExplanationOutput,
    "learner_data_query": PlannerLearnerDataQueryOutput,
    "learning_plan": PlannerLearningPlanOutput,
    "personalized_review_card": PlannerPersonalizedReviewCardOutput,
    "paper_generation": PlannerPaperGenerationOutput,
    "review_task_adjustment": PlannerReviewTaskAdjustmentOutput,
}


class PlannerScopeResolutionOutput(BaseModel):
    """Compact semantic repair contract for an unresolved planning layer.

    This is used only after the main Planner returned ``unspecified`` without
    an authoritative caller scope.  It keeps the retry narrowly focused and
    prevents application code from replacing semantic routing with a keyword
    table.
    """

    model_config = ConfigDict(extra="forbid")

    task_type: Literal["learning_plan"]
    task_source_quote: str = Field(
        min_length=1,
        max_length=160,
        description=(
            "逐字引用当前用户消息中能够证明最终交付物的最短原文。"
        ),
    )
    plan_scope: Literal[
        "long_term", "short_term", "daily_task", "unspecified"
    ] | None = None
    plan_action: Literal["reuse", "create_or_update", "clarify"] | None = None
    scope_source_quote: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        description=(
            "plan_scope明确时，逐字引用当前用户消息中能证明该层级的最短片段；"
            "plan_scope=unspecified时必须为null。"
        ),
    )
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def scope_and_action_are_consistent(self) -> "PlannerScopeResolutionOutput":
        if self.plan_scope is None or self.plan_action is None:
            raise ValueError("learning plan resolution requires plan fields")
        if self.plan_scope == "unspecified":
            if self.plan_action != "clarify":
                raise ValueError("unspecified scope requires clarification")
            if self.scope_source_quote is not None:
                raise ValueError("unspecified scope must not include a source quote")
        else:
            if self.plan_action == "clarify":
                raise ValueError("an exact scope must have an executable action")
            if not self.scope_source_quote:
                raise ValueError("an exact scope requires a current-message quote")
        return self


class PlannerStandardOutput(BaseModel):
    task_type: str
    agents: list[str] = Field(default_factory=list)
    reason: str = ""
    risk: str = "medium"


class DiagnosisStandardOutput(BaseModel):
    diagnosis: str = ""
    risks: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    long_term_plan: str = ""
    short_term_plan: str = ""
    next_task: str = ""
    task_minutes: int = 15
    expected_output: str = ""
    completion_standard: str = ""
    uncertainties: list[str] = Field(default_factory=list)


class BlueprintUnitStandardOutput(BaseModel):
    topic: str
    objective: str
    query: str
    question_types: list[str] = Field(default_factory=list)
    question_count: int = 1
    score: float | None = None
    selection_note: str = ""


class PaperBlueprintStandardOutput(BaseModel):
    title: str
    scope: str
    duration_minutes: int | None = None
    total_score: float | None = None
    units: list[BlueprintUnitStandardOutput] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class StrictModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RouteSelectionModelOutput(StrictModelOutput):
    decision: Literal["select", "clarify"]
    selected_route_id: str | None = Field(
        default=None,
        description="只能从输入的已批准路线目录中选择；追问时必须为空。",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=1_000)
    clarification_question: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def decision_fields_are_consistent(self) -> "RouteSelectionModelOutput":
        if self.decision == "select" and not self.selected_route_id:
            raise ValueError("route selection requires selected_route_id")
        if self.decision == "clarify" and not self.clarification_question:
            raise ValueError("route clarification requires clarification_question")
        return self


class LearningTaskModelOutput(StrictModelOutput):
    task_type: str = Field(min_length=1, max_length=100, description="描述学习动作类型，不是系统状态。")
    task_content: str = Field(
        min_length=1,
        max_length=2_000,
        description="一个可直接执行的原子任务，写清学习对象、动作、顺序和允许使用的材料。",
    )
    learning_chapter: str = Field(
        default="",
        max_length=500,
        description="今日实际学习的教材章节或小节；必须来自已有短期计划与教材证据。",
    )
    focus_knowledge_points: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="今日重点学习的1至5个知识点名称；只写名称，不生成系统ID。",
    )
    estimated_minutes: int = Field(gt=0, description="完成任务的合理分钟数，不得超过已知可用时间。")
    expected_output: str = Field(
        min_length=1,
        max_length=1_000,
        description="学习者完成后应提交或留下的可观察产出。",
    )
    completion_criteria: str = Field(
        min_length=1,
        max_length=1_000,
        description="可客观判断是否完成的标准，避免‘基本掌握’等模糊措辞。",
    )


class LongTermPlanStageModelOutput(StrictModelOutput):
    stage: int = Field(ge=1, description="从 1 开始且连续的长期学习阶段编号。")
    stage_name: str = Field(
        default="",
        max_length=300,
        description="可信路线中的阶段名称；不得改名或自行生成阶段。",
    )
    book: list[str] = Field(min_length=1, description="该阶段逐本学习的具体书目。")
    goal: str = Field(min_length=1, max_length=1_000, description="该阶段需要达成的学习目标。")
    duration_days: int = Field(
        default=0,
        ge=0,
        le=3_650,
        description=(
            "该阶段连续覆盖的自然日数量。制定新的长期规划时必须大于0；"
            "用于约束下级短期计划，不得仅把期限写在正文中。"
        ),
    )
    schedule_summary: str = Field(
        default="",
        max_length=2_000,
        description=(
            "该阶段的详细自然语言安排，必须明确阶段名称、具体书名、学习重点、"
            "阶段产出和验收条件。"
        ),
    )


class LongTermPlanningModelOutput(StrictModelOutput):
    selected_path_candidate_id: str | None = Field(
        default=None,
        description="从系统提供的路径候选中选择；不得生成候选ID。",
    )
    long_term_plan_content: str = Field(
        min_length=1,
        max_length=12_000,
        description=(
            "长期规划自然语言正文；依次包含【最终目标】【能力路径与阶段】"
            "【阶段里程碑】【资源预算】【重规划条件】【保温底线】。"
        ),
    )
    total_duration_days: int = Field(
        default=0,
        ge=0,
        le=3_650,
        description=(
            "长期规划覆盖的总自然日数。用户给出期限时必须换算后填写；"
            "各阶段 duration_days 之和必须等于该值。"
        ),
    )
    long_term_plan_stages: list[LongTermPlanStageModelOutput] = Field(
        min_length=1,
        description=(
            "长期教材阶段的最小结构化表示；仅逐项映射系统提供的可信路线。"
            "最终业务值由系统重新生成并覆盖，模型不得改写路线。"
        ),
    )


class ShortTermPlanningModelOutput(StrictModelOutput):
    selected_path_candidate_id: str | None = Field(
        default=None,
        description="从系统提供的路径候选中选择；不得生成候选ID。",
    )
    short_term_plan_content: str = Field(min_length=1, max_length=12_000)
    duration_days: int = Field(
        default=0,
        ge=0,
        le=365,
        description=(
            "短期计划覆盖的自然日数。制定新短期计划时必须大于0，"
            "并且不得超过所属长期阶段的 duration_days。"
        ),
    )
    progression_nodes: list[str] = Field(
        default_factory=list,
        min_length=0,
        max_length=12,
        description="至少两个覆盖完整周期的推进或验收节点，按发生顺序输出。",
    )
    expected_output: str = Field(min_length=1, max_length=1_000)
    completion_criteria: str = Field(min_length=1, max_length=1_000)
    selected_textbook_route_id: str | None = None
    selected_stage_id: str | None = None
    selected_books: list[str] = Field(default_factory=list)
    selection_reason: str | None = Field(default=None, max_length=1_000)

    @field_validator("progression_nodes", mode="before")
    @classmethod
    def normalize_progression_nodes(cls, value: Any) -> Any:
        return _normalize_progression_nodes(value)

    @field_validator("selected_books", mode="before")
    @classmethod
    def normalize_absent_textbook_selection(cls, value: Any) -> Any:
        return [] if value is None else value


class DailyTaskPlanningModelOutput(StrictModelOutput):
    selected_path_candidate_id: str | None = Field(
        default=None,
        description="从系统提供的路径候选中选择；不得生成候选ID。",
    )
    daily_task_content: str = Field(min_length=1, max_length=6_000)
    learning_chapter: str = Field(
        default="",
        max_length=500,
        description="今日实际学习的教材章节或小节；必须来自已有短期计划与教材证据。",
    )
    focus_knowledge_points: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="今日重点学习的1至5个知识点名称；只写名称，不生成系统ID。",
    )
    estimated_minutes: int = Field(gt=0)
    expected_output: str = Field(min_length=1, max_length=1_000)
    completion_criteria: str = Field(min_length=1, max_length=1_000)


class ThreeLayerPlanningModelOutput(StrictModelOutput):
    """Natural-language-first model boundary for three planning artifacts.

    Route metadata remains system-owned. The four optional selection fields are
    the minimal structured decision needed to validate the model's flexible
    choice inside a trusted textbook route.
    """

    selected_path_candidate_id: str | None = Field(
        default=None,
        description="从系统提供的路径候选中选择；不得生成候选ID。",
    )
    total_duration_days: int = Field(default=0, ge=0, le=3_650)
    long_term_plan_content: str = Field(min_length=1, max_length=12_000)
    short_term_plan_content: str = Field(min_length=1, max_length=12_000)
    short_term_duration_days: int = Field(default=0, ge=0, le=365)
    short_term_progression_nodes: list[str] = Field(
        default_factory=list,
        max_length=12,
    )
    daily_task_content: str = Field(min_length=1, max_length=6_000)
    learning_chapter: str = Field(default="", max_length=500)
    focus_knowledge_points: list[str] = Field(default_factory=list, max_length=5)
    estimated_minutes: int = Field(gt=0)
    expected_output: str = Field(min_length=1, max_length=1_000)
    completion_criteria: str = Field(min_length=1, max_length=1_000)
    long_term_plan_stages: list[LongTermPlanStageModelOutput] = Field(
        min_length=1,
        description="长期教材阶段的少量结构化输出；每个阶段只包含序号、书目和目标。",
    )
    selected_textbook_route_id: str | None = None
    selected_stage_id: str | None = None
    selected_books: list[str] = Field(default_factory=list)
    selection_reason: str | None = Field(default=None, max_length=1_000)

    @field_validator("short_term_progression_nodes", mode="before")
    @classmethod
    def normalize_progression_nodes(cls, value: Any) -> Any:
        return _normalize_progression_nodes(value)

    @field_validator("selected_books", mode="before")
    @classmethod
    def normalize_absent_textbook_selection(cls, value: Any) -> Any:
        return [] if value is None else value

class NaturalLanguageLearningAnalysisModelOutput(StrictModelOutput):
    """Minimal model boundary for personal planning.

    Route metadata and derived planning records are system-owned. The model
    only writes the diagnosis, natural-language plans, and one executable task.
    """

    summary: str = Field(min_length=1, max_length=1_000)

    risk_flags: list[str]
    recommendations: list[str]
    uncertainty: list[str]
    long_term_plan_content: str = Field(min_length=1, max_length=4_000)
    short_term_plan_content: str = Field(min_length=1, max_length=4_000)
    long_term_plan_action: Literal["reuse", "update"] = "update"
    short_term_plan_action: Literal["reuse", "update"] = "update"
    priority_mode: Literal["normal", "temporary_focus", "recovery"] = "normal"
    adjustment_reason: str = Field(min_length=1, max_length=1_000)
    learning_task: LearningTaskModelOutput

    @model_validator(mode="after")
    def require_updated_plan_sections(
        self,
    ) -> "NaturalLanguageLearningAnalysisModelOutput":
        requirements = (
            (
                "long_term_plan_content",
                self.long_term_plan_content,
                self.long_term_plan_action,
                (
                    "【最终目标】",
                    "【能力路径与阶段】",
                    "【阶段里程碑】",
                    "【资源预算】",
                    "【重规划条件】",
                    "【保温底线】",
                ),
            ),
            (
                "short_term_plan_content",
                self.short_term_plan_content,
                self.short_term_plan_action,
                (
                    "【当前主目标】",
                    "【长期目标保温】",
                    "【具体任务块】",
                    "【复习任务】",
                    "【反馈指标】",
                ),
            ),
        )
        for field_name, content, action, required in requirements:
            if action == "reuse":
                continue
            missing = [section for section in required if section not in content]
            if missing:
                raise ValueError(
                    f"{field_name} missing sections: " + ", ".join(missing)
                )
        return self


class LearnerDataResponseModelOutput(StrictModelOutput):
    answer: str = Field(
        min_length=1,
        max_length=3_000,
        description=(
            "只依据系统提供的只读学习证据，用自然语言直接回答用户；"
            "不得把推荐、浏览、生成资源或进入复习队列表述为已经学会。"
        ),
    )


class PlanningRoutePhaseModelOutput(StrictModelOutput):
    phase_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    objective: str = Field(
        min_length=1,
        description="阶段目标必须来自给定路线；不得改写 approved route 的全局阶段定义。",
    )
    exit_evidence: list[str] = Field(
        min_length=1,
        description="阶段晋级所需的可观察证据，不得把用户自述直接当作稳定掌握。",
    )
    source_refs: list[str] = Field(default_factory=list)


class PlanningRouteSourceModelOutput(StrictModelOutput):
    source_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_version: str | None = None


class PlanningRouteContextModelOutput(StrictModelOutput):
    goal_type: str = Field(min_length=1)
    goal_name: str = Field(min_length=1)
    planning_status: Literal["approved_route", "provisional"] = Field(
        description="只能复述输入状态；最终值由 Resolver 覆盖，模型无权批准或降级路线。"
    )
    match_reason: str = Field(min_length=1)
    route_id: str | None = Field(
        default=None,
        description="只能复述输入 route_id；不得生成或替换系统路线 ID。",
    )
    route_version: int | None = Field(
        default=None,
        ge=1,
        description="只能复述输入版本；最终版本由 Resolver 覆盖。",
    )
    route_status: str | None = Field(
        default=None,
        description="只能复述输入状态；模型不得将 provisional 标为 approved。",
    )
    phases: list[PlanningRoutePhaseModelOutput] = Field(default_factory=list)
    sources: list[PlanningRouteSourceModelOutput] = Field(default_factory=list)
    assumptions: list[str] = Field(
        default_factory=list,
        description="仅记录形成 provisional 个人计划所需的显式假设，不得编造用户事实。",
    )
    unknowns_to_confirm: list[str] = Field(
        default_factory=list,
        description="记录需由用户、导师或外部规则确认的信息，不得用推测补齐。",
    )
    runtime_checks: list[str] = Field(default_factory=list)


class GoalContractModelOutput(StrictModelOutput):
    goal_type: str = Field(min_length=1)
    goal_name: str = Field(min_length=1)
    observable_ability: str = Field(
        min_length=1,
        description="可观察的目标能力；不得从请求中虚构期限、资源或既有能力。",
    )
    acceptance_evidence: list[str] = Field(
        min_length=1,
        description="能够提交、演示或由正式评价验证的达标证据。",
    )


class PlanMilestoneModelOutput(StrictModelOutput):
    milestone_id: str = Field(min_length=1, description="模型内局部里程碑标签，不是系统持久化 ID。")
    name: str = Field(min_length=1)
    success_criteria: str = Field(min_length=1)
    evidence_required: list[str] = Field(
        min_length=1,
        description="每个里程碑都必须包含可观察的掌握证据。",
    )


class ShortTermLearningPackageModelOutput(StrictModelOutput):
    time_window_weeks: Literal[1, 2] = Field(description="当前短期学习包只能覆盖 1–2 周。")
    current_goal: str = Field(min_length=1)
    task_blocks: list[str] = Field(min_length=1)
    expected_output: str = Field(min_length=1)
    completion_criteria: str = Field(min_length=1)


class RecoveryPolicyModelOutput(StrictModelOutput):
    trigger_conditions: list[str] = Field(min_length=1)
    recovery_actions: list[str] = Field(
        min_length=1,
        description="包含路径偏差修复与回到长期主线的具体动作。",
    )


class RecommendationTraceModelOutput(StrictModelOutput):
    default_route: str = Field(min_length=1, description="说明默认路线或 provisional 路径如何约束建议。")
    user_state: str = Field(min_length=1, description="说明可信学情证据如何影响建议，不得编造用户事实。")
    time_constraint: str = Field(min_length=1, description="只使用输入中已知的时间预算；未知时明确待确认。")
    current_task: str = Field(min_length=1, description="说明前三层如何收敛为当前原子任务。")


class LearningAnalysisModelOutput(StrictModelOutput):
    summary: str = Field(
        min_length=1,
        max_length=1000,
        description="基于输入事实说明当前学习状态、主要依据、关键缺口和本轮优先方向。",
    )
    risk_flags: list[str] = Field(description="会影响目标或执行的具体风险；无可靠依据时不要添加。")
    recommendations: list[str] = Field(description="与诊断依据和风险逐条对应、可落实到计划或任务的建议。")
    uncertainty: list[str] = Field(description="明确记录缺失、冲突或无法确认的信息，不得用推测填补。")
    long_term_plan_content: str = Field(
        min_length=1,
        max_length=2_000,
        description=(
            "可执行、可验收的自然语言长期战略计划，必须按顺序包含："
            "【最终目标】【能力路径与阶段】【阶段里程碑】【资源预算】"
            "【重规划条件】【保温底线】。"
            "每栏须落实输入依据、行动或能力、可观察产出和确认边界；"
            "信息不足时明确写‘待用户确认’，不得编造用户事实、期限或资源。"
        ),
    )
    short_term_plan_content: str = Field(
        min_length=1,
        max_length=2_000,
        description=(
            "可在当前预算内执行的自然语言短期计划，必须按顺序包含："
            "【当前主目标】【长期目标保温】【时间分配】【具体任务块】"
            "【复习任务】【反馈指标】。"
            "具体任务块必须与 learning_task 的动作、时长、产出和完成标准一致；"
            "时间分配不得超过已知预算，信息不足时明确写‘待用户确认’。"
        ),
    )
    long_term_plan_action: Literal["reuse", "update"] = Field(
        default="update",
        description="已有有效长期计划且用户未明确要求创建或调整长期目标时使用reuse；否则使用update。",
    )
    short_term_plan_action: Literal["reuse", "update"] = Field(
        default="update",
        description="已有有效短期计划、当前诉求未改变且用户画像短期目标未变化时使用reuse；否则使用update。",
    )
    priority_mode: Literal["normal", "temporary_focus", "recovery"]
    adjustment_reason: str = Field(min_length=1, max_length=1_000)
    route_context: PlanningRouteContextModelOutput | None = Field(
        default=None,
        description="复述路线上下文供映射；系统会恢复 Resolver 拥有的 ID、版本和状态。",
    )
    goal_contract: GoalContractModelOutput | None = None
    milestones: list[PlanMilestoneModelOutput] = Field(default_factory=list)
    short_term_learning_package: ShortTermLearningPackageModelOutput | None = None
    recovery_policy: RecoveryPolicyModelOutput | None = None
    recommendation_trace: RecommendationTraceModelOutput | None = None
    assumptions: list[str] = Field(
        default_factory=list,
        description="个人计划的显式假设；不得伪造用户期限、能力、资源或医疗事实。",
    )
    unknowns_to_confirm: list[str] = Field(
        default_factory=list,
        description="影响计划且仍需确认的信息。",
    )
    learning_task: LearningTaskModelOutput

    @model_validator(mode="after")
    def require_updated_plan_sections(self) -> "LearningAnalysisModelOutput":
        requirements = (
            (
                "long_term_plan_content",
                self.long_term_plan_content,
                self.long_term_plan_action,
                ("【最终目标】", "【能力路径与阶段】", "【阶段里程碑】", "【资源预算】", "【重规划条件】", "【保温底线】"),
            ),
            (
                "short_term_plan_content",
                self.short_term_plan_content,
                self.short_term_plan_action,
                ("【当前主目标】", "【长期目标保温】", "【具体任务块】", "【复习任务】", "【反馈指标】"),
            ),
        )
        for field_name, content, action, required in requirements:
            if action == "reuse":
                continue
            missing = [section for section in required if section not in content]
            if missing:
                raise ValueError(f"{field_name} missing sections: " + ", ".join(missing))
        return self


class KnowledgeRetrievalPlanModelOutput(StrictModelOutput):
    kp_query: str = Field(
        min_length=1,
        max_length=300,
        description="供 get_kp_with_content 使用的知识点检索语句，只保留知识对象、范围和必要限定词。",
    )
    kp_concepts: list[str] = Field(
        default_factory=list,
        description=(
            "从题干与每个选项中抽取的 2-8 个核心概念清单（医学实体/术语，如"
            "「观察性研究」「原始数据」「异质性」）；每个选项的辨析概念都必须覆盖。"
            "系统会把每个概念当作一路独立检索并行执行（本地教材切片 + 网络概念"
            "定义，RQ-RAG 多路分解思想），首轮就按概念覆盖证据，并用它校验总结"
            "覆盖与驱动补充检索。概念必须是可独立检索的教材术语且互不重叠，"
            "不得是「说法是否正确」这类流程性描述。"
        ),
    )
    question_query: str = Field(
        min_length=1,
        max_length=300,
        description="供 get_question_with_content 使用的题目检索语句；Knowledge Agent 每次执行都必须提供。",
    )
    retrieval_reason: str = Field(
        min_length=1,
        max_length=500,
        description="说明知识点和题目两条检索语句如何由用户诉求得到，以及各自服务什么下游任务。",
    )


class KnowledgeSupplementDecisionModelOutput(StrictModelOutput):
    """组卷检索后的有限补充检索决策。

    模型只能判断证据/候选题是否足够并提出检索短语；题目、ID、证据和
    工具调用均由系统持有和执行，不能由该契约承载。
    """

    decision: Literal["enough", "continue", "stop"]
    reason: str = Field(min_length=1, max_length=500)
    supplemental_queries: list[str] = Field(default_factory=list, max_length=3)
    missing_requirements: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def queries_only_when_continuing(self) -> "KnowledgeSupplementDecisionModelOutput":
        if self.decision != "continue" and self.supplemental_queries:
            raise ValueError("supplemental queries require continue decision")
        if self.decision == "continue" and not self.supplemental_queries:
            raise ValueError("continue decision requires supplemental queries")
        return self


class KnowledgeSummaryItemOutput(StrictModelOutput):
    evidence_id: str = Field(
        min_length=1,
        max_length=200,
        description="对应输入 evidence 中某一条的 evidence_id；系统会校验该 id 必须存在于本次检索到的证据中，不得自造。",
    )
    content: str = Field(
        min_length=1,
        max_length=2_000,
        description="从该条证据原始切片中提取的规范化原文内容；可轻微裁剪（删除无关句子、截断过长片段），但必须保留原文表述，不得自由概括或改写原文。",
    )


class KnowledgeLearningFocusItemOutput(StrictModelOutput):
    name: str = Field(
        min_length=2,
        max_length=120,
        description=(
            "用户明确要求纳入本次学习结果的一个具体知识对象名称；必须逐字出现在"
            "对应教材 evidence 的原始 text 中，不得概括、拆分、扩写或补造名称。"
        ),
    )
    evidence_id: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "直接出现该名称的教材 evidence_id；必须来自本轮输入 evidence。"
        ),
    )


class KnowledgeModelOutput(StrictModelOutput):
    retrieval_summary: str = Field(
        default="",
        max_length=8_000,
        description=(
            "仅在最终提取阶段填写的兼容性文本字段：多条提取内容按证据顺序拼接的自然语言正文，"
            "仅作展示与兜底；need_more_retrieval=true 时必须为空。"
        ),
    )
    summary_items: list[KnowledgeSummaryItemOutput] = Field(
        default_factory=list,
        description=(
            "对每一条检索到的内容逐条提取并规范化：每条一个对象，evidence_id 指明取自哪条证据，"
            "content 是提取的原文内容；来源信息由系统按 evidence_id 从证据集确定性补全，模型不输出来源字段。"
            "全部条目合并后即为本阶段交付物，供下游专家按 evidence_id 引用；"
            "need_more_retrieval=true 时必须为空数组，不得生成中间提取。"
        ),
    )
    learning_focus_status: Literal[
        "not_requested", "supported", "unsupported", "undetermined"
    ] = Field(
        default="undetermined",
        description=(
            "用户未点名具体学习对象时为 not_requested；用户点名的全部对象都能逐项绑定"
            "教材证据时为 supported；至少一项缺少教材证据时为 unsupported；无法可靠"
            "判断时为 undetermined。不得把流程词、教材名、能力维度或模型自行推荐内容"
            "当作用户点名的学习对象。"
        ),
    )
    learning_focus_items: list[KnowledgeLearningFocusItemOutput] = Field(
        default_factory=list,
        max_length=12,
        description=(
            "仅 learning_focus_status=supported 时填写，且必须完整覆盖用户点名的全部具体"
            "学习对象；每项只含原名称及其教材 evidence_id。其他状态必须为空数组。"
        ),
    )
    quality_labels: list[str] = Field(
        default_factory=list,
        description="对相关性、权威性、覆盖度和一致性的简短评价。",
    )
    uncertainty: list[str] = Field(
        default_factory=list,
        description="会影响后续教学结论的证据缺口、冲突或范围歧义。",
    )
    need_more_retrieval: bool = Field(
        default=False,
        description=(
            "当前已检索证据是否不足：召回冲突、覆盖不足、无法映射正式知识点、"
            "用户具体问题点缺失或证据空白时为 true，系统将执行最多 2 轮补充检索；"
            "证据足以支撑回答时为 false。true 时不得同时输出 retrieval_summary 或 summary_items。"
        ),
    )
    supplemental_queries: list[str] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "need_more_retrieval=true 时给出 1-3 条聚焦证据缺口的补充知识点检索语句，"
            "每句不超过 200 字，必须具体可独立检索，不得重复已有检索词，"
            "不得泛化为“中医基础知识点”之类空泛词；need_more_retrieval=false 时为空。"
        ),
    )

    @model_validator(mode="after")
    def validate_retrieval_phase_exclusivity(self) -> "KnowledgeModelOutput":
        """Keep gap assessment and final extraction as mutually exclusive phases."""

        if self.need_more_retrieval:
            if (
                self.retrieval_summary.strip()
                or self.summary_items
                or self.learning_focus_items
            ):
                raise ValueError(
                    "an insufficient-evidence decision must not contain a final summary"
                )
        elif self.supplemental_queries:
            raise ValueError(
                "a finalized knowledge output must not request supplemental retrieval"
            )
        if self.learning_focus_status == "supported":
            if not self.learning_focus_items:
                raise ValueError("supported learning focus requires evidence items")
        elif self.learning_focus_items:
            raise ValueError(
                "learning focus evidence items require supported status"
            )
        return self


def _normalize_progression_nodes(value: Any) -> Any:
    """Normalize a narrow set of semantically equivalent Live model shapes."""

    if value is None:
        return []
    if not isinstance(value, list):
        return value
    normalized: list[Any] = []
    for item in value:
        if isinstance(item, str):
            normalized.append(item.strip())
            continue
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        title = str(item.get("title") or "").strip()
        detail = next(
            (
                str(item.get(key) or "").strip()
                for key in ("content", "text", "description", "node", "milestone")
                if str(item.get(key) or "").strip()
            ),
            "",
        )
        if title and detail and title != detail:
            normalized.append(f"{title}：{detail}")
        elif detail or title:
            normalized.append(detail or title)
        else:
            normalized.append(item)
    return normalized


class MemoryModelOutput(StrictModelOutput):
    summary: str = Field(min_length=1, max_length=2_000, description="只概括与当前任务相关的会话事实、约束和未决问题。")
    preserved_facts: list[str] = Field(
        default_factory=list,
        description="用户明确表达或已确认的稳定事实，不得推断。",
    )
    unresolved_questions: list[str] = Field(
        default_factory=list,
        description="影响后续执行且需用户补充的具体问题。",
    )
    temporary_constraints: list[str] = Field(
        default_factory=list,
        description="仅在本轮或明确时间窗口内生效的限制。",
    )
    memory_candidates: list[str] = Field(
        default_factory=list,
        description="可能值得长期保存但仍待确认的候选，不是正式记忆。",
    )


class MemoryConflictModelOutput(StrictModelOutput):
    memory_id: int = Field(gt=0, description="只能引用系统提供的记忆ID。")
    proposed_memory: str = Field(min_length=1, max_length=1_000)
    reason: str = Field(min_length=1, max_length=1_000)


class MemoryGovernanceModelOutput(StrictModelOutput):
    governance_notes: str = Field(
        min_length=1,
        max_length=3_000,
        description="详细说明本轮可持久化信息、相关旧记忆及是否存在真实语义冲突。",
    )
    memory_candidates: list[str] = Field(
        default_factory=list,
        description="仅提取用户明确表达、值得保存但尚未确认的信息，需要用户确认后沉淀。",
    )
    auto_confirm_candidates: list[str] = Field(
        default_factory=list,
        description="用户明确陈述、确定性高、无歧义、且不会与既有记忆冲突的个人事实，"
        "可直接沉淀为正式记忆，无需用户逐条确认；"
        "对既有记忆的更新或替换、模糊或可能变化的信息不得放入此字段。",
    )
    conflicts: list[MemoryConflictModelOutput] = Field(default_factory=list)
    requires_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list, max_length=3)
    resolution: Literal[
        "none",
        "keep_existing",
        "use_current_once",
        "replace_existing",
        "needs_clarification",
    ] = "none"

    @model_validator(mode="after")
    def clarification_matches_conflicts(self) -> "MemoryGovernanceModelOutput":
        if self.requires_clarification:
            if not self.conflicts or not self.clarification_questions:
                raise ValueError("memory clarification requires conflicts and questions")
            if self.resolution != "needs_clarification":
                raise ValueError("unresolved memory conflict requires needs_clarification")
        if self.resolution in {
            "keep_existing",
            "use_current_once",
            "replace_existing",
        } and not self.conflicts:
            raise ValueError("memory resolution requires a referenced conflict")
        return self


class ExpertModelOutput(StrictModelOutput):
    learning_tip: str = Field(
        min_length=1,
        max_length=8_000,
        description="基于正式任务和证据，说明执行动作、自检方法、预期产出与完成标准的教学提示。",
    )
    use_question_candidates: bool = False
    usage_reason: str = Field(default="", max_length=500)
    selected_question_ids: list[str] = Field(default_factory=list)
    selected_resource_candidate_ids: list[str] = Field(
        default_factory=list,
        description=(
            "只选择输入 candidate_resources 中确实适合当前学习者、任务和时间预算的候选ID；"
            "没有合适候选时保持空数组。"
        ),
    )
    resource_type: Literal["none", "practice", "variant", "grading_support"] = "none"
    blueprint_content: str | None = Field(
        default=None,
        max_length=12_000,
        description=(
            "仅供paper_generation使用的完整试卷蓝图正文，必须包含"
            "【来源与假设】【命题目标】【蓝图矩阵】【题型与抽题规则】"
            "【候选题使用策略】【发布前验收】；其他任务保持null。"
        ),
    )

    @field_validator("blueprint_content")
    @classmethod
    def validate_blueprint_sections(cls, value: str | None) -> str | None:
        if value is None:
            return value
        required = (
            "【来源与假设】",
            "【命题目标】",
            "【蓝图矩阵】",
            "【题型与抽题规则】",
            "【候选题使用策略】",
            "【发布前验收】",
        )
        missing = [section for section in required if section not in value]
        if missing:
            raise ValueError("blueprint_content missing sections: " + ", ".join(missing))
        return value


class KnowledgeExplanationModelOutput(StrictModelOutput):
    title: str = Field(min_length=1, max_length=300)
    explanation_content: str = Field(
        min_length=1,
        max_length=8_000,
        description=(
            "面向学习者的完整自然语言讲解正文（Markdown 格式，可自由使用小标题、加粗、"
            "列表、表格、引用块排版，小节标题自由拟定）。知识讲解采用启发式引导："
            "自然带出学情/考纲定位，讲透核心内容，末尾提出开放式思考问题；题目讲解采用"
            "直接讲题：点明考查要点，直接给出答案/思路与依据，辨析选项或展开要点，末尾点出"
            "易错提示。不得生成学习计划或复习任务。"
        ),
    )
    thinking_questions: list[str] = Field(
        default_factory=list,
        max_length=8_000,
        description="启发式引导的开放式思考问题（2-3 个，只提问不含答案），题目讲解可为空，与配套练习题目不重复。",
    )
    uncertainty: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(
        default_factory=list,
        max_length=32,
        description=(
            "本次回答实际引用的证据 id 列表，只能从输入 semantic_evidence 给出的 evidence_id 中选取，"
            "不得自创、不得编造，可留空。正文中可以提及教材名等出处名称（如《预防医学》），"
            "但不得出现 evidence_id 等内部标识（如 E_CHUNK_xxx、E_VIDEO_xxx），来源由系统按 evidence_id 自动标注。"
        ),
    )
    # Evaluation-only opt-in field. It is never rendered to the learner and
    # remains empty on the normal Expert path. When a paired evaluation
    # explicitly supplies a conflict contract, the model may bind the primary
    # claim to the two contract-provided evidence IDs. Runtime code validates
    # the allow-list before it can affect ResourceDraft.claims.
    conflict_claim_evidence_ids: list[str] = Field(
        default_factory=list,
        max_length=2,
        description=(
            "仅当输入明确包含评测冲突合同且开启评测模式时使用；返回主声明同时绑定的两个 evidence_id。"
            "只能逐字选择冲突合同提供的两个 ID；正常用户任务必须返回空列表；不得创造 ID。"
        ),
    )

    @field_validator("thinking_questions", mode="before")
    @classmethod
    def normalize_thinking_questions(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            return [line.strip(" -·\t") for line in value.splitlines() if line.strip()]
        if isinstance(value, (list, tuple)):
            return [
                str(item).strip(" -·\t")
                for item in value
                if str(item).strip(" -·\t")
            ]
        return value

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def normalize_evidence_refs(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        return value


class BlueprintUnitModelOutput(StrictModelOutput):
    knowledge_module: str = Field(min_length=1, max_length=300)
    learning_objective: str = Field(min_length=1, max_length=500)
    retrieval_query: str = Field(min_length=1, max_length=300)
    question_type_preferences: list[str] = Field(default_factory=list)
    required_question_count: int = Field(gt=0, le=100)
    score_total: float | None = Field(default=None, gt=0)
    candidate_limit: int = Field(default=10, ge=1, le=50)
    selection_rules: list[str] = Field(default_factory=list)
    assessment_dimensions: list[Literal[
        "concept_definition", "composition", "efficacy_indication",
        "mechanism_pathogenesis", "compatibility_role", "application_selection",
        "comparison_differentiation", "modification_extension", "case_analysis",
        "other",
    ]] = Field(default_factory=list)
    excluded_dimensions: list[Literal[
        "concept_definition", "composition", "efficacy_indication",
        "mechanism_pathogenesis", "compatibility_role", "application_selection",
        "comparison_differentiation", "modification_extension", "case_analysis",
        "other",
    ]] = Field(default_factory=list)
    target_difficulty: int | None = Field(default=None, ge=1, le=5)
    difficulty_is_hard_constraint: bool = False

    @field_validator("required_question_count", "candidate_limit", mode="before")
    @classmethod
    def normalize_integer(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return int(value.strip().replace("题", ""))
            except ValueError:
                return value
        return value

    @field_validator("score_total", mode="before")
    @classmethod
    def normalize_score(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return float(value.strip().replace("分", ""))
            except ValueError:
                return value
        return value

    @field_validator("target_difficulty", mode="before")
    @classmethod
    def normalize_difficulty(cls, value: object) -> object:
        if isinstance(value, bool):
            return None
        if isinstance(value, str):
            chinese = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
            match = re.search(r"[1-5一二三四五]", value)
            if not match:
                return None
            digit = match.group()
            return chinese.get(digit, int(digit))
        return value


class PaperBlueprintModelOutput(StrictModelOutput):
    title: str = Field(min_length=1, max_length=300)
    source_status: Literal[
        "official",
        "user_provided_unverified",
        "practice_sample",
        "pending_confirmation",
    ]
    scope_summary: str = Field(min_length=1, max_length=1_000)
    duration_minutes: int | None = Field(default=None, gt=0)
    total_score: float | None = Field(default=None, gt=0)
    units: list[BlueprintUnitModelOutput] = Field(min_length=1, max_length=20)
    assumptions: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)

    @field_validator("source_status", mode="before")
    @classmethod
    def normalize_source_status(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().replace("-", "_")
        return value

    @field_validator("duration_minutes", "total_score", mode="before")
    @classmethod
    def normalize_numbers(cls, value: object) -> object:
        if isinstance(value, str):
            cleaned = value.strip().replace("分钟", "").replace("分", "")
            try:
                return float(cleaned) if "." in cleaned else int(cleaned)
            except ValueError:
                return value
        return value


class SelectedPaperItemModelOutput(StrictModelOutput):
    unit_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    score: float | None = Field(default=None, gt=0)
    selection_rationale: str = Field(min_length=1, max_length=500)


class PaperAssemblyCandidateChoiceModelOutput(StrictModelOutput):
    """One run-local candidate number selected by the business model."""

    candidate_no: int = Field(
        ge=1,
        description=(
            "只能选择当前候选目录中存在且selectable=true的candidate_no；"
            "不得输出题目ID、单元ID或来源字段。"
        ),
    )
    rationale: str = Field(
        min_length=1,
        max_length=500,
        description="面向业务追踪的自然语言选题理由；不参与题目身份或来源解析。",
    )


class PaperAssemblySelectionModelOutput(StrictModelOutput):
    """Minimal business-agent contract for system-bound paper selection.

    The system resolves every integer against an immutable candidate snapshot.
    Natural-language fields are explanatory only and never become execution
    identifiers or provenance.
    """

    selection_summary: str = Field(
        min_length=1,
        max_length=2_000,
        description="详细说明覆盖思路、题型平衡和未覆盖约束，不得包含内部来源字段。",
    )
    selected_candidates: list[PaperAssemblyCandidateChoiceModelOutput] = Field(
        default_factory=list,
        max_length=100,
        description="按期望卷面顺序列出候选序号；只能引用当前候选目录。",
    )


class SystemBoundGeneratedPaperItemModelOutput(StrictModelOutput):
    """Content-only gap item; identity and provenance are system-owned."""

    question_type: str = Field(min_length=1, max_length=100)
    stem: str = Field(min_length=1, max_length=2_000)
    options: list[str] = Field(default_factory=list, max_length=8)
    reference_answer: str = Field(min_length=1, max_length=500)
    analysis: str = Field(min_length=1, max_length=2_000)
    rationale: str = Field(min_length=1, max_length=500)
    evidence_nos: list[int] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "仅可选择本次依据目录中的证据序号；无可用依据时保持空数组，"
            "不得自造来源或证据ID。"
        ),
    )

    @field_validator("reference_answer", mode="before")
    @classmethod
    def normalize_system_bound_reference_answer(cls, value: object) -> object:
        if isinstance(value, list):
            answers = [str(item).strip() for item in value if str(item).strip()]
            return ", ".join(answers)
        return value

    @model_validator(mode="after")
    def system_bound_choice_question_has_options(
        self,
    ) -> "SystemBoundGeneratedPaperItemModelOutput":
        normalized = self.question_type.strip().replace(" ", "")
        if normalized in {
            "选择题",
            "单选题",
            "单项选择",
            "单项选择题",
            "多选题",
            "多项选择",
            "多项选择题",
        } and len(self.options) < 2:
            raise ValueError("generated choice question requires at least two options")
        return self


class PaperGapGenerationModelOutput(StrictModelOutput):
    generation_summary: str = Field(min_length=1, max_length=1_000)
    generated_items: list[SystemBoundGeneratedPaperItemModelOutput] = Field(
        default_factory=list,
        max_length=5,
    )


class GeneratedPaperItemModelOutput(StrictModelOutput):
    unit_id: str = Field(min_length=1)
    question_type: str = Field(
        min_length=1,
        max_length=100,
        description="必须匹配所属蓝图单元允许的题型。",
    )
    stem: str = Field(min_length=1, max_length=2_000)
    options: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="选择题至少两个选项；简答题等非选择题使用空列表。",
    )
    reference_answer: str = Field(min_length=1, max_length=500)
    analysis: str = Field(min_length=1, max_length=2_000)
    selection_rationale: str = Field(min_length=1, max_length=500)
    source_tier: Literal["textbook", "web_reference", "model_knowledge"]

    @field_validator("reference_answer", mode="before")
    @classmethod
    def normalize_reference_answer(cls, value: object) -> object:
        if isinstance(value, list):
            answers = [str(item).strip() for item in value if str(item).strip()]
            return ", ".join(answers)
        return value

    @model_validator(mode="after")
    def require_options_for_choice_questions(self) -> "GeneratedPaperItemModelOutput":
        normalized = self.question_type.strip().replace(" ", "")
        if normalized in {
            "选择题",
            "单选题",
            "单项选择",
            "单项选择题",
            "多选题",
            "多项选择",
            "多项选择题",
        } and len(self.options) < 2:
            raise ValueError("generated choice question requires at least two options")
        return self


class ExamAssemblyModelOutput(StrictModelOutput):
    title: str = Field(min_length=1, max_length=300)
    instructions: str = Field(min_length=1, max_length=2_000)
    selected_items: list[SelectedPaperItemModelOutput] = Field(default_factory=list)
    generated_items: list[GeneratedPaperItemModelOutput] = Field(default_factory=list)
    coverage_summary: dict[str, Any] = Field(default_factory=dict)
    unresolved_constraints: list[str] = Field(default_factory=list)


class PaperUnitAuditIssueModelOutput(StrictModelOutput):
    issue_type: Literal[
        "factual_error",
        "content_quality",
        "paper_blueprint_mismatch",
        "paper_item_invalid",
        "answer_or_explanation_invalid",
        "safety_violation",
    ]
    message: str = Field(min_length=1, max_length=2_000)
    blocking: bool = True
    location_keys: list[str] = Field(min_length=1, max_length=8)


class AuditModelOutput(StrictModelOutput):
    decision: Literal["pass", "revise", "reject", "needs_human_review"] = Field(
        description=(
            "按当前任务指令及输入中的验收策略裁决：无阻断类型问题时直接pass，"
            "非阻断质量建议不得改判revise；可定位、可修复的阻断问题用revise；"
            "安全越界或无法可靠裁定时转人工；仅整体不可修复时reject。"
        )
    )
    findings: list[str] = Field(
        default_factory=list,
        description=(
            "逐项指出问题位置、证据或缺口、影响和修改要求；通过时默认保持空数组，"
            "只有任务验收策略明确允许时才保留带‘非阻断建议’标识的建议。"
            "非阻断建议不得与pass决定冲突；已核验维度写入audit_report。"
        ),
    )
    structured_findings: list[PaperUnitAuditIssueModelOutput] | None = Field(
        default=None,
        description=(
            "仅试卷单元审核使用。问题类型必须取受限枚举，location_keys 必须从"
            "系统提供的 allowed_location_keys 中选择；pass 时返回空数组。"
        ),
    )
    audit_report: str = Field(
        default="审核模型未提供额外说明，系统将以确定性门禁结果为准。",
        min_length=1,
        max_length=8_000,
        description=(
            "面向业务人员的详细自然语言审核报告；结论必须与decision一致，pass时不得声称"
            "需要返修、再次送审或不可发布。"
        ),
    )
    contract_check: dict[str, Any] | None = Field(
        default=None,
        description=(
            "可选：模型回显的已核验合同摘要（如 scope、total_duration_days、stages），"
            "仅用于审核留痕与追溯，不参与审核决定。"
        ),
    )


FORBIDDEN_OBJECTIVE_FIELDS = {
    "evidence", "items", "kp_id", "kp_ids", "question_id", "chunk_uid",
    "bridge_layer", "evidence_strength", "review_required", "mastery_score",
    "lambda_per_day", "retention_estimate", "next_review_at", "attempt_count",
    "learner_id", "execution_id", "artifact_id", "resource_id", "audit_result_id",
    "tools", "tool_calls", "steps", "depends_on", "agent", "plan_id",
    "task_id", "short_term_plan_id", "user_id", "created_at", "updated_at",
    "due_at", "status", "version",
    "tool_name", "tool_names", "tool_parameters", "question_content",
    "reference_answer", "analysis",
}


def validate_training_style_output(
    model: type[StrictModelOutput],
    value: dict[str, Any],
    expected_uncertainty: list[str],
) -> StrictModelOutput:
    forbidden = FORBIDDEN_OBJECTIVE_FIELDS.intersection(value)
    if forbidden:
        raise ValueError(
            "training output contract forbids objective fields: " + ", ".join(sorted(forbidden))
        )
    try:
        parsed = model.model_validate(value)
    except ValidationError as exc:
        first_error = exc.errors()[0]
        location = ".".join(str(part) for part in first_error["loc"]) or model.__name__
        message = first_error["msg"]
        raise ValueError(
            f"training output contract validation failed: {location}; {message}"
        ) from exc
    uncertainty = getattr(parsed, "uncertainty", [])
    missing = set(expected_uncertainty) - set(uncertainty)
    if missing:
        raise ValueError(
            "training output contract missing expected uncertainty: " + ", ".join(sorted(missing))
        )
    return parsed

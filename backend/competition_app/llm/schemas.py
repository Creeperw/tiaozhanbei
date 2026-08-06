from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


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
    ]
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
            "学习规划的处理方式。已有当前有效计划且用户未明确要求强制修改时返回reuse；"
            "缺少对应计划或用户明确要求重新制定、修改、调整时返回create_or_update；"
            "只有确实无法确定层级且没有可复用计划时返回clarify。非规划任务返回null。"
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
        description="完整语义是否是在请求讲解当前题目或定位答题卡点。",
    )
    emotional_support_request: bool = Field(
        default=False,
        description="完整语义是否主要需要情绪支持而非启动学习业务写入流程。",
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
        if self.task_type == "casual_conversation" and not self.casual_response:
            raise ValueError("casual conversation requires an agent-generated response")
        if self.task_type != "casual_conversation" and self.casual_response is not None:
            raise ValueError("non-casual task must not include casual_response")
        if self.task_type == "learner_data_query" and self.query_kind is None:
            raise ValueError("learner data query requires query_kind")
        if self.task_type != "learner_data_query" and self.query_kind is not None:
            raise ValueError("query_kind is only valid for learner data queries")
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


class KnowledgeModelOutput(StrictModelOutput):
    retrieval_summary: str = Field(
        default="",
        max_length=8_000,
        description="围绕用户问题整理检索依据，直接写有用结论，不复述检索过程。",
    )
    quality_labels: list[str] = Field(
        default_factory=list,
        description="对相关性、权威性、覆盖度和一致性的简短评价。",
    )
    uncertainty: list[str] = Field(
        default_factory=list,
        description="会影响后续教学结论的证据缺口、冲突或范围歧义。",
    )


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
        description="仅提取用户明确表达、值得保存但尚未确认的信息。",
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
            "面向学习者的完整自然语言内容：知识讲解采用启发式引导式结构（先结合学情定位，"
            "再讲解核心，末尾提出开放式思考问题）；题目讲解采用直接讲题结构（考查要点→"
            "直接作答→选项/要点辨析→易错提示）。不得生成学习计划或复习任务。"
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
            "不得自创、不得编造，可留空。正文中不要书写具体出处（书名、章节、URL、页码），"
            "来源由系统按 evidence_id 自动标注。"
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


class AuditModelOutput(StrictModelOutput):
    decision: Literal["pass", "revise", "reject", "needs_human_review"] = Field(
        description="全部关键检查通过才pass；可修正用revise；核心错误或严重越界用reject；无法可靠判断则转人工。"
    )
    findings: list[str] = Field(
        default_factory=list,
        description=(
            "逐项指出问题位置、证据或缺口、影响和修改要求；通过时默认保持空数组，"
            "只有任务验收策略明确允许时才保留带‘非阻断建议’标识的建议。已核验维度写入audit_report。"
        ),
    )
    audit_report: str = Field(
        default="审核模型未提供额外说明，系统将以确定性门禁结果为准。",
        min_length=1,
        max_length=8_000,
        description="面向业务人员的详细自然语言审核报告。",
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

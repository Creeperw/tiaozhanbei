from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class PlannerModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: Literal[
        "knowledge_explanation",
        "learning_plan",
        "personalized_review_card",
        "paper_generation",
    ]
    plan_scope: Literal["long_term", "short_term", "daily_task", "unspecified"] | None = Field(
        default=None,
        description=(
            "学习规划的目标层级；输入已提供本次规划层级时必须原样返回，"
            "其中daily_task表示当日任务而非短期计划。制定或修改计划时"
            "必须返四个枚举值之一；纯学情查询和非规划任务返回null。"
        ),
    )
    selected_agents: list[
        Literal[
            "memory_agent",
            "knowledge_base_agent",
            "diagnosis_agent",
            "learning_plan_service",
            "review_scheduler",
            "expert_agent",
            "audit_agent",
        ]
    ] = Field(
        min_length=1,
        description="完成当前交付物所需的最小充分Agent集合，必须满足能力目录中的依赖关系。",
    )
    routing_reason: str = Field(
        min_length=1,
        max_length=500,
        description="说明用户交付目标、所选Agent的必要性、未选资源Agent的原因以及审核需求。",
    )
    risk_level: Literal["low", "medium", "high"] = Field(
        description="仅表示编排风险；知识对象含糊、诉求冲突或安全边界不清时不得标为low。"
    )
    requires_audit: bool = True
    fallback_policy: Literal["fail_closed", "needs_human_review"] = "fail_closed"

    @field_validator("selected_agents")
    @classmethod
    def selected_agents_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("selected_agents must be unique")
        return value


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
    book: list[str] = Field(min_length=1, description="该阶段逐本学习的具体书目。")
    goal: str = Field(min_length=1, max_length=1_000, description="该阶段需要达成的学习目标。")


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
    expected_output: str = Field(min_length=1, max_length=1_000)
    completion_criteria: str = Field(min_length=1, max_length=1_000)
    selected_textbook_route_id: str | None = None
    selected_stage_id: str | None = None
    selected_books: list[str] = Field(default_factory=list)
    selection_reason: str | None = Field(default=None, max_length=1_000)

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
    long_term_plan_content: str = Field(min_length=1, max_length=12_000)
    short_term_plan_content: str = Field(min_length=1, max_length=12_000)
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


class MemoryModelOutput(StrictModelOutput):
    summary: str = Field(min_length=1, max_length=2_000, description="只概括与当前任务相关的会话事实、约束和未决问题。")
    preserved_facts: list[str] = Field(description="用户明确表达或已确认的稳定事实，不得推断。")
    unresolved_questions: list[str] = Field(description="影响后续执行且需用户补充的具体问题。")
    temporary_constraints: list[str] = Field(description="仅在本轮或明确时间窗口内生效的限制。")
    memory_candidates: list[str] = Field(description="可能值得长期保存但仍待确认的候选，不是正式记忆。")


class ExpertModelOutput(StrictModelOutput):
    learning_tip: str = Field(
        min_length=1,
        max_length=8_000,
        description="基于正式任务和证据，说明执行动作、自检方法、预期产出与完成标准的教学提示。",
    )
    use_question_candidates: bool = False
    usage_reason: str = Field(default="", max_length=500)
    selected_question_ids: list[str] = Field(default_factory=list)
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
        description="面向学习者的完整自然语言知识讲解；不得生成学习计划或复习任务。",
    )
    uncertainty: list[str] = Field(default_factory=list)


class BlueprintUnitModelOutput(StrictModelOutput):
    knowledge_module: str = Field(min_length=1, max_length=300)
    learning_objective: str = Field(min_length=1, max_length=500)
    retrieval_query: str = Field(min_length=1, max_length=300)
    question_type_preferences: list[str] = Field(default_factory=list)
    required_question_count: int = Field(gt=0, le=100)
    score_total: float | None = Field(default=None, gt=0)
    candidate_limit: int = Field(default=10, ge=1, le=50)
    selection_rules: list[str] = Field(default_factory=list)
    difficulty_preference: str | None = Field(
        default=None,
        max_length=100,
        description="可选命题偏好；题库无标准难度字段时必须允许为空，且不能成为检索硬过滤条件。",
    )

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
        description="逐项指出问题位置、证据或缺口、影响和修改要求；通过时概括已核验维度。",
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

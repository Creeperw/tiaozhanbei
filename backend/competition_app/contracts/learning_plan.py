from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypeAlias

from pydantic import ConfigDict, Field, model_validator

from competition_app.contracts.base import ContractModel
from competition_app.contracts.default_route import ResolvedPlanningRoute
from competition_app.contracts.daily_task_scheduling import DailyTaskSchedule

# 由其他功能写入今日任务的原子项来源标记。这些项无法从任务正文推导出来，
# 因此「按正文重建任务」的修复流程必须原样保留它们，否则会把用户已经确认
# 的安排静默删除。新增此类来源时在此登记。
LEARNING_INTERVENTION_ITEM_SOURCE = "learning_intervention"
EXTERNAL_ITEM_SOURCES: frozenset[str] = frozenset(
    {LEARNING_INTERVENTION_ITEM_SOURCE}
)


def is_externally_owned_item(item: Any) -> bool:
    """该原子项是否由其他功能写入。

    同时接受契约对象与未校验的 dict：``model_copy(update=...)`` 不触发校验，
    内部调用方可能持有没有转换过的载荷，而定时刷新链路不能因此抛错。
    """

    resource_ref = (
        item.get("resource_ref") if isinstance(item, dict)
        else getattr(item, "resource_ref", None)
    )
    if not isinstance(resource_ref, dict):
        return False
    return str(resource_ref.get("source") or "") in EXTERNAL_ITEM_SOURCES


# 执行层真正提供完成路径的原子项类型：knowledge_practice 走冻结题组，
# video_section 走视频证据。``reading`` / ``recall`` 只是模型在计划正文里的
# 标签，执行层没有任何完成入口，因此不得成为线上每日任务的原子项。
# 该集合必须与 platform_backend 的
# ``daily_task_progress_service.supported_item_kinds`` 保持一致。
EXECUTABLE_ITEM_TYPES: frozenset[str] = frozenset(
    {"knowledge_practice", "video_section"}
)


def item_type_of(item: Any) -> str:
    """读取原子项类型，同时接受契约对象与未校验的 dict。"""

    if isinstance(item, dict):
        return str(item.get("item_type") or "")
    return str(getattr(item, "item_type", "") or "")


def is_executable_item(item: Any) -> bool:
    """该原子项是否具备可验证的完成路径。"""

    return item_type_of(item) in EXECUTABLE_ITEM_TYPES


PlanScope: TypeAlias = Literal["long_term", "short_term", "daily_task"]


class GoalContract(ContractModel):
    goal_type: str = Field(min_length=1)
    goal_name: str = Field(min_length=1)
    observable_ability: str = Field(min_length=1)
    acceptance_evidence: list[str] = Field(min_length=1)


class PlanMilestone(ContractModel):
    milestone_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    success_criteria: str = Field(min_length=1)
    evidence_required: list[str] = Field(min_length=1)


class LongTermPlanStage(ContractModel):
    stage: int = Field(ge=1)
    stage_name: str = ""
    book: list[str] = Field(min_length=1)
    goal: str = Field(min_length=1)
    duration_days: int = Field(default=0, ge=0, le=3_650)
    schedule_summary: str = ""
    acceptance: list[str] = Field(
        default_factory=list,
        description="该阶段的用户化验收条款（可为空，空时仅要求路线验收证据）。",
    )


class StageEvidenceRecord(ContractModel):
    """System-verified evidence used by a long-term stage progression gate."""

    evidence_id: str = Field(min_length=1)
    stage: int = Field(ge=1)
    requirement: str = Field(min_length=1)
    source_type: Literal["completed_daily_task", "audited_assessment"]
    source_id: str = Field(min_length=1)
    verified_by: str = Field(min_length=1)
    verified_at: datetime


class ShortTermTaskBlock(ContractModel):
    content: str = Field(min_length=1)
    estimated_minutes: int = Field(gt=0)
    item_type: Literal["video_section", "knowledge_practice", "reading", "recall"] | None = None
    knowledge_point_name: str | None = None
    kp_id: str | None = None
    required_question_count: int | None = Field(default=None, ge=1)
    resource_ref: dict[str, Any] = Field(default_factory=dict)
    completion_policy: dict[str, Any] = Field(default_factory=dict)


class ShortTermFocusEvidenceAnchor(ContractModel):
    name: str = Field(min_length=2, max_length=120)
    evidence_id: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1)
    source_label: str = Field(min_length=1)


class ShortTermFocusContext(ContractModel):
    """Small, system-owned header for the current short-term selection."""

    focus_type: Literal[
        "special_topic",
        "knowledge_cluster",
        "knowledge_point",
        "remediation",
        "due_review",
    ]
    focus_label: str = Field(min_length=1)
    knowledge_point_ids: list[str] = Field(default_factory=list)
    mode: Literal["temporary_cross_stage"] | None = None
    progression_stage_id: str | None = None
    focus_stage_id: str | None = None
    focus_books: list[str] = Field(default_factory=list, max_length=2)
    focus_names: list[str] = Field(default_factory=list, max_length=12)
    focus_evidence: list[ShortTermFocusEvidenceAnchor] = Field(default_factory=list)
    prerequisite_mode: Literal["introductory_preview"] | None = None


class TextbookSelectionContext(ContractModel):
    route_id: str = Field(min_length=1)
    route_version: int = Field(ge=1)
    stage_id: str = Field(min_length=1)
    stage_name: str = Field(min_length=1)
    books: list[str] = Field(min_length=1, max_length=2)
    reason: str = Field(min_length=1)
    selection_mode: Literal["new_learning", "review", "diagnostic"] | None = None


class ShortTermLearningPackage(ContractModel):
    # Retained for API compatibility. New planning logic uses duration_days as
    # the authoritative value and derives this display-oriented week count.
    time_window_weeks: int | None = Field(default=None, ge=1, le=53)
    duration_days: int | None = Field(default=None, ge=1, le=365)
    progression_nodes: list[str] = Field(default_factory=list)
    current_goal: str = Field(min_length=1)
    task_blocks: list[str | ShortTermTaskBlock] = Field(min_length=1)
    review_minutes: int | None = Field(default=None, ge=0)
    maintenance_minutes: int | None = Field(default=None, ge=0)
    buffer_minutes: int | None = Field(default=None, ge=0)
    maintenance_plan: str | None = None
    maintenance_unavailable_reason: str | None = None
    expected_output: str = Field(min_length=1)
    completion_criteria: str = Field(min_length=1)


class RecoveryPolicy(ContractModel):
    trigger_conditions: list[str] = Field(min_length=1)
    recovery_actions: list[str] = Field(min_length=1)


class RecommendationTrace(ContractModel):
    default_route: str = Field(min_length=1)
    user_state: str = Field(min_length=1)
    time_constraint: str = Field(min_length=1)
    current_task: str = Field(min_length=1)


class LearningTaskProposal(ContractModel):
    model_config = ConfigDict(extra="forbid")

    task_type: str
    task_content: str
    learning_chapter: str = ""
    focus_knowledge_points: list[str] = Field(default_factory=list)
    estimated_minutes: int = Field(gt=0)
    expected_output: str
    completion_criteria: str


class DailyTaskItemSpec(ContractModel):
    task_item_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
    item_type: Literal["video_section", "knowledge_practice", "reading", "recall"]
    title: str = Field(min_length=1)
    estimated_minutes: float = Field(gt=0)
    per_question_estimated_minutes: float | None = Field(default=None, gt=0)
    knowledge_point_name: str | None = None
    kp_id: str | None = None
    required_question_count: int | None = Field(default=None, ge=1)
    resource_ref: dict[str, Any] = Field(default_factory=dict)
    completion_policy: dict[str, Any] = Field(default_factory=dict)

    @property
    def externally_owned(self) -> bool:
        """该原子项是否由其他功能写入。

        此类项无法从任务正文或短期计划推导出来，任何「按正文重建任务」
        的流程都必须显式保留它们，否则会把用户已确认的安排静默删除。
        """

        return is_externally_owned_item(self)

    @model_validator(mode="after")
    def validate_execution_policy(self) -> "DailyTaskItemSpec":
        policy = self.completion_policy.get("policy")
        if self.item_type == "knowledge_practice":
            if not self.kp_id or self.required_question_count is None:
                raise ValueError(
                    "knowledge_practice requires kp_id and required_question_count"
                )
            if policy != "frozen_question_set":
                raise ValueError(
                    "knowledge_practice requires policy=frozen_question_set"
                )
        elif self.kp_id is not None:
            raise ValueError("only knowledge_practice may carry a formal kp_id")
        if self.item_type == "video_section":
            start = self.resource_ref.get("start_seconds")
            end = self.resource_ref.get("end_seconds")
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
                or start < 0
                or start >= end
            ):
                raise ValueError(
                    "video_section requires start_seconds < end_seconds"
                )
            source_keys = {"source", "provider", "url", "video_id", "bvid"}
            if not any(self.resource_ref.get(key) for key in source_keys):
                raise ValueError("video_section requires a video source")
            if policy not in {
                "html5_coverage",
                "iframe_focus_and_confirmation",
            }:
                raise ValueError("video_section requires the verified video A policy")
        return self


class LearningPlanProposal(ContractModel):
    model_config = ConfigDict(extra="forbid")

    prerequisite_assessment: dict[str, Any] | None = None

    long_term_plan_content: str = Field(min_length=1)
    short_term_plan_content: str = Field(min_length=1)
    long_term_plan_stages: list[LongTermPlanStage] = Field(default_factory=list)
    daily_task_content: str | None = None
    long_term_plan_action: Literal["reuse", "update"] = "update"
    short_term_plan_action: Literal["reuse", "update"] = "update"
    daily_task_action: Literal["reuse", "update"] = "update"
    priority_mode: Literal["normal", "temporary_focus", "recovery"] = "normal"
    adjustment_reason: str = Field(min_length=1)
    task_proposal: LearningTaskProposal
    planning_route: ResolvedPlanningRoute | None = None
    goal_contract: GoalContract | None = None
    milestones: list[PlanMilestone] = Field(default_factory=list)
    short_term_learning_package: ShortTermLearningPackage | None = None
    recovery_policy: RecoveryPolicy | None = None
    recommendation_trace: RecommendationTrace | None = None
    short_term_focus: ShortTermFocusContext | None = None
    textbook_selection: TextbookSelectionContext | None = None
    assumptions: list[str] = Field(default_factory=list)
    unknowns_to_confirm: list[str] = Field(default_factory=list)


class LongTermPlan(ContractModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1)
    learner_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    stages: list[LongTermPlanStage] = Field(default_factory=list)
    stage_evidence: list[StageEvidenceRecord] = Field(default_factory=list)
    planning_route: ResolvedPlanningRoute | None = None
    goal_contract: GoalContract | None = None
    milestones: list[PlanMilestone] = Field(default_factory=list)
    recovery_policy: RecoveryPolicy | None = None
    recommendation_trace: RecommendationTrace | None = None
    assumptions: list[str] = Field(default_factory=list)
    unknowns_to_confirm: list[str] = Field(default_factory=list)
    textbook_selection: TextbookSelectionContext | None = None


class ShortTermPlan(ContractModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1)
    learner_id: str = Field(min_length=1)
    long_term_plan_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    planning_route: ResolvedPlanningRoute | None = None
    goal_contract: GoalContract | None = None
    short_term_learning_package: ShortTermLearningPackage | None = None
    recovery_policy: RecoveryPolicy | None = None
    recommendation_trace: RecommendationTrace | None = None
    short_term_focus: ShortTermFocusContext | None = None
    textbook_selection: TextbookSelectionContext | None = None


class LearningTask(ContractModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    learner_id: str = Field(min_length=1)
    short_term_plan_id: str = Field(min_length=1)
    task_type: str
    task_content: str
    learning_chapter: str = ""
    focus_knowledge_points: list[str] = Field(default_factory=list)
    # 任务展示时长 = 实际资源项 estimated_minutes 之和 T（可含小数）。
    estimated_minutes: float = Field(gt=0)
    expected_output: str
    completion_criteria: str
    version: int = Field(ge=1)
    status: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    refresh_started_at: datetime | None = None
    refresh_due_at: datetime | None = None
    items: list[DailyTaskItemSpec] = Field(default_factory=list)
    daily_task_schedule: DailyTaskSchedule | None = None

    @model_validator(mode="after")
    def validate_atomic_items(self) -> "LearningTask":
        item_ids = [item.task_item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("daily task item IDs must be unique")
        ordinals = [item.ordinal for item in self.items]
        if ordinals != list(range(1, len(self.items) + 1)):
            raise ValueError("daily task item ordinals must be consecutive from 1")
        total = sum(item.estimated_minutes for item in self.items)
        if total > self.estimated_minutes + 1e-9:
            raise ValueError("daily task item budget exceeds parent task budget")
        return self


class LearningPlanResult(ContractModel):
    model_config = ConfigDict(extra="forbid")

    long_term_plan: LongTermPlan | None = None
    short_term_plan: ShortTermPlan | None = None
    learning_task: LearningTask | None = None
    generated_scope: PlanScope | Literal["full"] = "full"
    invalidated_layers: list[PlanScope] = Field(default_factory=list)
    reused_existing: bool = False
    replan_review: dict[str, Any] = Field(default_factory=dict)
    force_replan_prompt: str | None = None


class PlanChangeDecision(ContractModel):
    long_term_action: Literal["reuse", "update"]
    short_term_action: Literal["reuse", "update"]
    daily_task_action: Literal["reuse", "update"]
    requires_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    replan_requested: bool = False
    changed_facts: list[str] = Field(default_factory=list)
    decision_mode: Literal[
        "reuse_fast_path",
        "bounded_update_fast_path",
        "full_replan",
        "clarify",
    ] = "full_replan"


class LearningPlanClarificationResult(ContractModel):
    requires_clarification: Literal[True] = True
    clarification_questions: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)
    requested_scope: PlanScope | Literal["unspecified"] | None = None
    prerequisite_scope: PlanScope | None = None
    prerequisite_kind: Literal[
        "parent_plan_missing",
        "course_status_confirmation",
    ] | None = None
    required_prerequisite_courses: list[str] = Field(default_factory=list)

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypeAlias

from pydantic import Field, model_validator

from competition_app.contracts.base import ContractModel
from competition_app.contracts.default_route import ResolvedPlanningRoute


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
    book: list[str] = Field(min_length=1)
    goal: str = Field(min_length=1)


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


class TextbookSelectionContext(ContractModel):
    route_id: str = Field(min_length=1)
    route_version: int = Field(ge=1)
    stage_id: str = Field(min_length=1)
    stage_name: str = Field(min_length=1)
    books: list[str] = Field(min_length=1, max_length=2)
    reason: str = Field(min_length=1)


class ShortTermLearningPackage(ContractModel):
    time_window_weeks: Literal[1, 2] | None = None
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
    estimated_minutes: int = Field(gt=0)
    knowledge_point_name: str | None = None
    kp_id: str | None = None
    required_question_count: int | None = Field(default=None, ge=1)
    resource_ref: dict[str, Any] = Field(default_factory=dict)
    completion_policy: dict[str, Any] = Field(default_factory=dict)

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
    task_id: str = Field(min_length=1)
    learner_id: str = Field(min_length=1)
    short_term_plan_id: str = Field(min_length=1)
    task_type: str
    task_content: str
    learning_chapter: str = ""
    focus_knowledge_points: list[str] = Field(default_factory=list)
    estimated_minutes: int = Field(gt=0)
    expected_output: str
    completion_criteria: str
    version: int = Field(ge=1)
    status: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    refresh_started_at: datetime | None = None
    refresh_due_at: datetime | None = None
    items: list[DailyTaskItemSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_atomic_items(self) -> "LearningTask":
        item_ids = [item.task_item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("daily task item IDs must be unique")
        ordinals = [item.ordinal for item in self.items]
        if ordinals != list(range(1, len(self.items) + 1)):
            raise ValueError("daily task item ordinals must be consecutive from 1")
        if sum(item.estimated_minutes for item in self.items) > self.estimated_minutes:
            raise ValueError("daily task item budget exceeds parent task budget")
        return self


class LearningPlanResult(ContractModel):
    long_term_plan: LongTermPlan | None = None
    short_term_plan: ShortTermPlan | None = None
    learning_task: LearningTask | None = None
    generated_scope: PlanScope | Literal["full"] = "full"
    invalidated_layers: list[PlanScope] = Field(default_factory=list)


class PlanChangeDecision(ContractModel):
    long_term_action: Literal["reuse", "update"]
    short_term_action: Literal["reuse", "update"]
    daily_task_action: Literal["reuse", "update"]
    requires_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class LearningPlanClarificationResult(ContractModel):
    requires_clarification: Literal[True] = True
    clarification_questions: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)
    requested_scope: PlanScope | Literal["unspecified"] | None = None

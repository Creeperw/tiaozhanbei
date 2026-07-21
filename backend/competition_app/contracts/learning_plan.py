from __future__ import annotations

from datetime import datetime
from typing import Literal, TypeAlias

from pydantic import Field

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


class ShortTermTaskBlock(ContractModel):
    content: str = Field(min_length=1)
    estimated_minutes: int = Field(gt=0)


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
    estimated_minutes: int = Field(gt=0)
    expected_output: str
    completion_criteria: str


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
    estimated_minutes: int = Field(gt=0)
    expected_output: str
    completion_criteria: str
    version: int = Field(ge=1)
    status: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime


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

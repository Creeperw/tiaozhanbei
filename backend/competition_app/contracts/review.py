from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, Field, model_validator

from competition_app.contracts.base import ContractModel


class LearnerKPReviewState(ContractModel):
    """Legacy stability-based state kept for compatibility with existing callers."""

    learner_id: str
    kp_id: str
    review_stage: int = Field(default=0, ge=0, le=7)
    stability_seconds: float = Field(gt=0)
    last_review_at: datetime | None = None
    next_review_at: datetime | None = None
    status: str = "new"


class DailyReviewPolicy(ContractModel):
    capacity: int = Field(default=1, ge=1)
    target_difficulty: int = Field(default=1, ge=1, le=5)
    allowed_resource_types: list[str] = Field(default_factory=lambda: ["review_card"])


UnitInterval = Annotated[float, Field(ge=0.0, le=1.0)]


class UserKnowledgeState(ContractModel):
    """Authoritative user-to-knowledge-point state from testttt.json."""

    user_id: str = Field(min_length=1)
    kp_id: str = Field(min_length=1)
    knowledge_mastery: UnitInterval = Field(
        validation_alias=AliasChoices("knowledge_mastery", "knowledge_mastery（依据）")
    )
    answer_accuracy: UnitInterval
    forgetting_coefficient: float = Field(
        gt=0.0,
        validation_alias=AliasChoices(
            "forgetting_coefficient", "forgetting_coefficient（依据）"
        ),
        description="Per-day forgetting coefficient lambda.",
    )
    kp_review_status: str
    calculated_at: datetime


class ReviewFormulaPolicy(ContractModel):
    formula_version: Literal["ebbinghaus-review-v1"] = "ebbinghaus-review-v1"
    lambda_unit: Literal["per_day"] = "per_day"
    mastery_unit: Literal["probability"] = "probability"
    min_retention_threshold: float = Field(default=0.70, gt=0.0, lt=1.0)
    min_review_interval_minutes: int = Field(default=1, ge=1)
    max_review_interval_minutes: int = Field(default=525_600, ge=1)
    urgency_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    due_weight: float = Field(default=0.6, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> "ReviewFormulaPolicy":
        if abs(self.urgency_weight + self.due_weight - 1.0) > 1e-9:
            raise ValueError("review priority weights must sum to 1")
        return self


class ReviewCandidate(ContractModel):
    kp_id: str
    state_found: bool
    input_mastery: UnitInterval | None = None
    input_forgetting_coefficient: float | None = Field(default=None, gt=0.0)
    state_calculated_at: datetime | None = None
    elapsed_days: float = Field(default=0.0, ge=0.0)
    retention_estimate: UnitInterval
    next_interval_minutes: int = Field(ge=1)
    next_review_at: datetime
    is_due: bool
    urgency: UnitInterval
    priority_score: UnitInterval
    can_skip: bool
    reason_codes: list[str] = Field(default_factory=list)


class ReviewTask(ContractModel):
    review_task_id: str
    learner_id: str
    primary_kp_id: str
    source_type: Literal["system_recommended", "user_requested", "initial_recall"]
    review_type: str = "review_card"
    priority_score: float = Field(default=0.0, ge=0.0, le=1.0)
    status: Literal[
        "awaiting_attempt",
        "pending",
        "bound",
        "completed",
        "skipped",
        "overdue",
        "cancelled",
    ] = "pending"


class ReviewSchedule(ContractModel):
    schedule_id: str
    learner_id: str
    calculated_at: datetime
    formula_policy: ReviewFormulaPolicy
    candidates: list[ReviewCandidate] = Field(default_factory=list)
    selected_task: ReviewTask | None = None
    selection_summary: str

    @model_validator(mode="after")
    def selected_task_comes_from_candidates(self) -> "ReviewSchedule":
        if self.selected_task is not None and self.selected_task.primary_kp_id not in {
            item.kp_id for item in self.candidates
        }:
            raise ValueError("selected review task must come from the candidate pool")
        return self


class ReviewResourceBinding(ContractModel):
    binding_id: str
    review_task_id: str
    resource_id: str
    resource_version: int = Field(default=1, ge=1)
    audit_result_id: str
    role: Literal["primary"] = "primary"


ReviewOutcome = Literal[
    "independent_correct", "hinted_correct", "wrong", "skipped"
]


class ReviewMemoryUnit(ContractModel):
    memory_unit_id: str
    learner_id: str
    kp_id: str
    prompt_abstract: str
    mastery_score: float = Field(ge=0.0, le=100.0)
    lambda_per_day: float = Field(ge=0.03, le=0.20)
    review_stage: int = Field(default=0, ge=0, le=7)
    stability_seconds: float = Field(default=1_200.0, gt=0.0)
    consecutive_correct: int = Field(default=0, ge=0)
    consecutive_wrong: int = Field(default=0, ge=0)
    last_review_at: datetime | None = None
    next_review_at: datetime
    requires_remediation: bool = False
    formula_version: Literal["ebbinghaus_classic_hybrid_v1"] = (
        "ebbinghaus_classic_hybrid_v1"
    )
    source_calculated_at: datetime | None = None
    source_attempt_id: str | None = None
    activation_source: Literal["graded_question_attempt"] | None = None
    activated_at: datetime | None = None
    version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime


class ReviewAttempt(ContractModel):
    attempt_id: str
    review_task_id: str
    learner_id: str
    kp_id: str
    outcome: ReviewOutcome
    score: UnitInterval
    hint_used: bool = False
    answered_at: datetime
    memory_version_before: int = Field(ge=1)
    memory_version_after: int = Field(ge=1)
    mastery_before: float = Field(ge=0.0, le=100.0)
    mastery_after: float = Field(ge=0.0, le=100.0)
    stability_before: float = Field(gt=0.0)
    stability_after: float = Field(gt=0.0)
    next_review_at: datetime


class ReviewAttemptSubmission(ContractModel):
    learner_id: str
    outcome: ReviewOutcome
    hint_used: bool = False
    answered_at: datetime | None = None
    attempt_id: str | None = None


class ReviewQueueEntry(ContractModel):
    memory_unit: ReviewMemoryUnit
    retention_estimate: UnitInterval
    is_due: bool
    reason_codes: list[str] = Field(default_factory=list)
    task: ReviewTask | None = None
    resource: dict[str, Any] | None = None


class ReviewQueue(ContractModel):
    schema_version: Literal["1.1"] = "1.1"
    learner_id: str
    calculated_at: datetime
    admission_policy: Literal["completed_graded_kp_question_v1"] = (
        "completed_graded_kp_question_v1"
    )
    projection_source: Literal["canonical_review_memory"] = "canonical_review_memory"
    entries: list[ReviewQueueEntry] = Field(default_factory=list)
    due_count: int = Field(default=0, ge=0)
    active_task_count: int = Field(default=0, ge=0)
    awaiting_resource_count: int = Field(default=0, ge=0)

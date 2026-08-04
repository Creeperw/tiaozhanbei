from __future__ import annotations

from typing import Literal

from pydantic import Field

from competition_app.contracts.base import ContractModel
from competition_app.contracts.knowledge import EvidenceItem, LearnerQuestionView, QuestionDetail


class BlueprintUnit(ContractModel):
    unit_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    knowledge_module: str = Field(min_length=1)
    learning_objective: str = Field(min_length=1)
    retrieval_query: str = Field(min_length=1)
    question_type_preferences: list[str] = Field(default_factory=list)
    required_question_count: int = Field(gt=0)
    score_total: float | None = Field(default=None, gt=0)
    candidate_limit: int = Field(default=10, ge=1, le=50)
    selection_rules: list[str] = Field(default_factory=list)
    # 难度约束：target_difficulty 为用户明确的 1-5；
    # difficulty_is_hard_constraint=True 时严格匹配，缺口按 fallback 降级。
    target_difficulty: int | None = Field(default=None, ge=1, le=5)
    difficulty_is_hard_constraint: bool = False
    difficulty_fallback_policy: Literal[
        "strict", "unlabeled_official", "web_reference", "generated"
    ] = "strict"


class PaperBlueprint(ContractModel):
    blueprint_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_status: Literal[
        "official",
        "user_provided_unverified",
        "practice_sample",
        "pending_confirmation",
    ]
    scope_summary: str = Field(min_length=1)
    duration_minutes: int | None = Field(default=None, gt=0)
    total_score: float | None = Field(default=None, gt=0)
    required_total_question_count: int | None = Field(default=None, gt=0)
    required_question_type_distribution: dict[str, int] = Field(default_factory=dict)
    question_count_is_hard_constraint: bool = False
    units: list[BlueprintUnit] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class UnitQuestionCandidates(ContractModel):
    unit_id: str = Field(min_length=1)
    retrieval_query: str = Field(min_length=1)
    resolved_kp_ids: list[str] = Field(default_factory=list)
    requested_limit: int = Field(gt=0)
    required_question_count: int = Field(gt=0)
    items: list[QuestionDetail] = Field(default_factory=list)
    external_question_references: list[EvidenceItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # 难度与来源统计（Phase 3 降级链路）
    target_difficulty: int | None = Field(default=None, ge=1, le=5)
    difficulty_is_hard_constraint: bool = False
    exact_difficulty_count: int = Field(default=0, ge=0)
    unlabeled_official_count: int = Field(default=0, ge=0)
    web_reference_count: int = Field(default=0, ge=0)
    unmet_required_count: int = Field(default=0, ge=0)


class QuestionCandidatePool(ContractModel):
    pool_id: str = Field(min_length=1)
    blueprint_id: str = Field(min_length=1)
    units: list[UnitQuestionCandidates] = Field(min_length=1)
    retrieval_round: int = Field(default=1, ge=1, le=2)
    retrieval_summary: list[str] = Field(default_factory=list)


class PaperDifficultySourceSummary(ContractModel):
    """整卷的难度与来源统计，用于向用户透明说明补题来源。"""

    target_difficulty: int | None = Field(default=None, ge=1, le=5)
    difficulty_is_hard_constraint: bool = False
    total_questions: int = Field(default=0, ge=0)
    exact_difficulty_count: int = Field(default=0, ge=0)
    unlabeled_official_count: int = Field(default=0, ge=0)
    web_reference_count: int = Field(default=0, ge=0)
    generated_count: int = Field(default=0, ge=0)
    unmet_count: int = Field(default=0, ge=0)
    notice: str = ""


class SelectedPaperItem(ContractModel):
    sequence: int = Field(ge=1)
    unit_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    score: float | None = Field(default=None, gt=0)
    selection_rationale: str = Field(min_length=1)


class ExamPaperItem(ContractModel):
    sequence: int = Field(ge=1)
    unit_id: str = Field(min_length=1)
    score: float | None = Field(default=None, gt=0)
    question: QuestionDetail
    selection_rationale: str = Field(min_length=1)


class ExamPaperDraft(ContractModel):
    paper_draft_id: str = Field(min_length=1)
    blueprint_id: str = Field(min_length=1)
    candidate_pool_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    duration_minutes: int | None = Field(default=None, gt=0)
    total_score: float | None = Field(default=None, gt=0)
    items: list[ExamPaperItem] = Field(min_length=1)
    answer_key: dict[str, str]
    explanations: dict[str, str | None]
    coverage_summary: dict[str, object] = Field(default_factory=dict)
    unresolved_constraints: list[str] = Field(default_factory=list)
    difficulty_source_summary: PaperDifficultySourceSummary | None = None
    status: Literal["pending_review"] = "pending_review"

    def learner_questions(self) -> list[LearnerQuestionView]:
        return [
            LearnerQuestionView(
                question_id=item.question.question_id,
                question_type=item.question.question_type,
                stem=item.question.stem,
                options=item.question.options,
                tags=item.question.tags,
                kp_ids=sorted({bridge.kp_id for bridge in item.question.bridges}),
            )
            for item in self.items
        ]

from __future__ import annotations

from typing import Literal

from pydantic import Field

from competition_app.contracts.base import ContractModel
from competition_app.contracts.knowledge import EvidenceItem, LearnerQuestionView, QuestionDetail

AssessmentDimension = Literal[
    "concept_definition",
    "composition",
    "efficacy_indication",
    "mechanism_pathogenesis",
    "compatibility_role",
    "application_selection",
    "comparison_differentiation",
    "modification_extension",
    "case_analysis",
    "other",
]


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
    assessment_dimensions: list[AssessmentDimension] = Field(default_factory=list)
    excluded_dimensions: list[AssessmentDimension] = Field(default_factory=list)
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
    # 本轮是否要求逐题解析（“每题都要有详细解析”这类交付条件）。它由蓝图
    # 原稿写明、编译器逐字锚定后带入，是这一交付条件的唯一结构化来源：
    # 系统不猜用户原话，也不在组卷阶段用关键词重新判断。
    requires_explanation: bool = False
    units: list[BlueprintUnit] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class UnitQuestionCandidates(ContractModel):
    unit_id: str = Field(min_length=1)
    retrieval_query: str = Field(min_length=1)
    resolved_kp_ids: list[str] = Field(default_factory=list)
    # 本单元的准入范围，由知识库智能体依据单元声明的范围判定，不是检索命中
    # 列表：``resolved_kp_ids`` 是按知识点名称召回的结果，既含同名或泛化的
    # 无关知识点，也漏掉同章节里未被命中的知识点，不能当范围边界用。
    scope_kp_ids: list[str] = Field(default_factory=list)
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
    # 候选不足时按缺口降级掺入、并最终进入候选池的题目数。这些题只有次要
    # 桥接落在本单元范围内，主知识点在别的章节——多为学习者已经学过的内容。
    # 系统不隐藏这件事：组卷说明会把它讲给学习者听。
    borrowed_question_count: int = Field(default=0, ge=0)
    eligible_count: int = Field(default=0, ge=0)
    uncertain_count: int = Field(default=0, ge=0)
    rejected_count: int = Field(default=0, ge=0)
    admission_notes: list[str] = Field(default_factory=list)
    semantic_eligible_count: int = Field(default=0, ge=0)
    semantic_uncertain_count: int = Field(default=0, ge=0)
    semantic_rejected_count: int = Field(default=0, ge=0)
    rerank_applied: bool = False
    rerank_degraded: bool = False
    relevance_score_summary: dict[str, float] = Field(default_factory=dict)


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
    # 检索到的网络参考材料条数。每条是一份检索证据，可能一道题都没带。
    web_reference_count: int = Field(default=0, ge=0)
    # 真正进了卷面的网络检索题数。与上面的材料条数是两回事：材料只供出题
    # 参考，这些题是学习者要做、要判分的题，必须分开报。
    web_in_paper_count: int = Field(default=0, ge=0)
    generated_count: int = Field(default=0, ge=0)
    unmet_count: int = Field(default=0, ge=0)
    notice: str = ""


class PaperFinalCoverageSummary(ContractModel):
    """System-computed facts for the final assembled paper.

    Model-authored candidate-selection prose is deliberately excluded.  Audit
    and publication code can therefore treat this object as authoritative.
    """

    total_questions: int = Field(default=0, ge=0)
    question_type_counts: dict[str, int] = Field(default_factory=dict)
    unit_question_counts: dict[str, int] = Field(default_factory=dict)
    official_count: int = Field(default=0, ge=0)
    generated_count: int = Field(default=0, ge=0)
    missing_unit_counts: dict[str, int] = Field(default_factory=dict)
    hard_constraints_satisfied: bool = True


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
    items: list[ExamPaperItem] = Field(default_factory=list)
    # 空态标记：候选池无合规题目且模型未能补足时，组卷返回明确的空态反馈而
    # 不是抛校验错误；empty_reason 非空表示本卷为占位空卷，不应发布到学习工坊。
    empty_reason: str | None = None
    answer_key: dict[str, str]
    explanations: dict[str, str | None]
    final_coverage_summary: PaperFinalCoverageSummary = Field(
        default_factory=PaperFinalCoverageSummary
    )
    # Non-authoritative selection/retrieval history retained for diagnostics.
    # It must never be interpreted as the final state of the assembled paper.
    assembly_notes: list[str] = Field(default_factory=list)
    coverage_summary: dict[str, object] = Field(default_factory=dict)
    unresolved_constraints: list[str] = Field(default_factory=list)
    difficulty_source_summary: PaperDifficultySourceSummary | None = None
    # 题目来源说明：可用题目不足时系统是怎么补足的。面向学习者的系统文案，
    # 由组卷阶段确定性生成，模型不得改写。空串表示本卷没有需要说明的补足
    # 行为。
    supply_notice: str = ""
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

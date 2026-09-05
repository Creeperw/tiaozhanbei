from typing import Literal

from pydantic import Field, PrivateAttr

from competition_app.contracts.base import ContractModel


class EvidenceItem(ContractModel):
    evidence_id: str
    source_id: str
    content_summary: str
    source_scope: str = "public"
    authority_level: str
    confidence: float = Field(ge=0.0, le=1.0)
    bridge_layer: str | None = None
    source_url: str | None = None
    resource_type: Literal["textbook", "question", "video", "reference", "web"] = "textbook"
    # 展示用确定性来源标签（如《中医临床护理学》· 第一节…），由检索层填充；
    # 引用卡片据此生成，模型不得自行编造出处。
    source_label: str | None = None


class RetrievalSummaryItem(ContractModel):
    """知识库管理智能体对单条检索内容的逐条提取结果。

    每条对应一条 evidence：content 是从该条原始切片中提取的规范化原文
    （可轻微裁剪，不得自由概括改写）；来源字段由系统从 EvidenceItem
    确定性填充，模型不得自造来源。
    """

    evidence_id: str
    source_id: str
    authority_level: str = "textbook"
    resource_type: Literal["textbook", "question", "video", "reference", "web"] = "textbook"
    source_url: str | None = None
    source_label: str | None = None
    content: str = ""


class LearningFocusEvidence(ContractModel):
    """A concrete requested learning object anchored to formal evidence."""

    name: str = Field(min_length=2, max_length=120)
    evidence_id: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1)
    source_label: str = Field(min_length=1)


class EvidencePack(ContractModel):
    evidence_pack_id: str
    query: str
    resolved_kp_ids: list[str] = Field(default_factory=list)
    # kp_id -> 知识点名称（kp_lv3 优先，回退 kp_lv2/kp_id）。供主题锚点
    # 判断使用：蓝图 knowledge_module 的主题词与这些名称匹配，避免依赖
    # resolve_topic 对长查询的不稳定排序（首位可能被干扰知识点占据）。
    resolved_kp_names: dict[str, str] = Field(default_factory=dict)
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    retrieval_summary: str = ""
    summary_items: list[RetrievalSummaryItem] = Field(default_factory=list)
    summary_evidence_ids: list[str] = Field(default_factory=list)
    # Historical packs did not carry focus semantics. Treat those as requests
    # without a separately asserted focus; every newly generated pack sets an
    # explicit status after the semantic evidence-processing pass.
    learning_focus_status: Literal[
        "not_requested", "supported", "unsupported", "undetermined"
    ] = "not_requested"
    learning_focus_items: list[LearningFocusEvidence] = Field(default_factory=list)
    conflict_evidence: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    question_search_decision: "QuestionSearchDecision | None" = None
    question_candidates: list["QuestionCandidateReference"] = Field(default_factory=list)
    _question_details: list["QuestionDetail"] = PrivateAttr(default_factory=list)


class QuestionBridge(ContractModel):
    kp_id: str
    bridge_layer: Literal["strict", "llm", "similarity"]
    relation: str
    confidence: float = Field(ge=0.0, le=1.0)
    rank: int = Field(ge=1)
    evidence_chunk_uid: str
    match_method: str


class QuestionRetrievalMetadata(ContractModel):
    channels: list[Literal["bridge", "bm25", "vector"]]
    channel_scores: dict[str, float]
    fusion_score: float
    channel_ranks: dict[str, int] = Field(default_factory=dict)
    fusion_strategy: Literal["legacy_max", "rrf_v1"] = "legacy_max"
    legacy_max_score: float | None = None
    semantic_score: float | None = Field(default=None, ge=0.0, le=1.0)
    semantic_rank: int | None = Field(default=None, ge=1)
    semantic_status: Literal[
        "not_evaluated", "shadow", "eligible", "uncertain", "rejected"
    ] = "not_evaluated"
    semantic_model: str | None = None
    semantic_query_version: str | None = None
    rerank_status: Literal["disabled", "success", "degraded", "failed"] = "disabled"
    rerank_error: str | None = None


class QuestionDetail(ContractModel):
    question_id: str
    question_type: str
    stem: str
    reference_answer: str
    analysis: str | None
    options: list[str] = Field(default_factory=list)
    origin: Literal["retrieved", "generated"] = "retrieved"
    source_tier: Literal["textbook", "web_reference", "model_knowledge"] = "textbook"
    # 仅真实标注难度：1-5；无标注时保持 None，绝不推断默认值。
    difficulty: int | None = Field(default=None, ge=1, le=5)
    difficulty_source: str | None = None
    tags: list[str]
    source_metadata: dict[str, object]
    bridges: list[QuestionBridge]
    retrieval: QuestionRetrievalMetadata


class LearnerQuestionView(ContractModel):
    question_id: str
    question_type: str
    stem: str
    options: list[str] = Field(default_factory=list)
    tags: list[str]
    kp_ids: list[str]


class QuestionSearchResult(ContractModel):
    query: str
    resolved_kp_ids: list[str]
    embedding_model: str
    vector_index_path: str
    items: list[QuestionDetail]
    fusion_strategy: Literal["legacy_max", "rrf_v1"] = "legacy_max"
    vector_degraded: bool = False
    rerank_mode: Literal["disabled", "shadow", "sort", "gate"] = "disabled"
    rerank_model: str | None = None
    rerank_degraded: bool = False


class QuestionCandidateReference(ContractModel):
    question_id: str
    channels: list[Literal["bridge", "bm25", "vector"]]
    bridge_layers: list[Literal["strict", "llm", "similarity"]]


class QuestionSearchDecision(ContractModel):
    rule_question_search_needed: bool
    rule_reasons: list[str]
    model_question_search_needed: bool
    model_question_search_reason: str
    final_question_search_needed: bool
    merge_strategy: Literal["conservative_union"] = "conservative_union"
    candidate_count: int = Field(default=0, ge=0)
    channel_summary: list[str] = Field(default_factory=list)


def to_learner_view(question: QuestionDetail) -> LearnerQuestionView:
    return LearnerQuestionView(
        question_id=question.question_id,
        question_type=question.question_type,
        stem=question.stem,
        options=question.options,
        tags=question.tags,
        kp_ids=sorted({bridge.kp_id for bridge in question.bridges}),
    )

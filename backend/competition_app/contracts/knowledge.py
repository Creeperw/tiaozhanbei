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
    # 题目桥接召回范围。``resolved_kp_ids`` 是证据用的高质量头部命中，只覆盖同
    # 章节的一小部分知识点；题目桥接按知识点取题，用这么窄的集合会让整个单元的
    # 候选池缺题（线上实测：某单元头部命中 10 个知识点、桥接只召回 4 道题，而
    # 单元需要 40 道；同一次检索放宽到 200 个知识点后召回 297 道，其中 69 道的
    # 主知识点在单元范围内）。本字段是同一次检索的宽召回结果，只用于扩大题目
    # 桥接，不参与证据生成与学习状态写入。
    bridge_kp_ids: list[str] = Field(default_factory=list)
    # kp_id -> 知识点名称（kp_lv3 优先，回退 kp_lv2/kp_id）。仅供诊断与展示；
    # 候选准入不得用它做文本比对：拿模型生成的 knowledge_module 去匹配知识点
    # 名称属于对自然语言做模式匹配，不是可靠的业务判定（见
    # KnowledgeBaseAgent._question_scope_status）。
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


# 组卷在填空缺口上合成题目时，会把该蓝图单元检索到的宽召回知识点整体挂成
# ``match_method="resolved_blueprint_unit"`` 的桥接。那是「这道题从哪个范围里
# 找出来」的检索范围，不是「这道题考了哪些知识点」。两者混同，一份卷子里就会
# 出现几百个学习者没学过的知识点，并顺着卷面快照写进掌握度与复习排期。
SCOPE_BRIDGE_MATCH_METHOD = "resolved_blueprint_unit"


def question_kp_ids(question: QuestionDetail) -> list[str]:
    """题目自身考的知识点；不含组卷补题挂上的蓝图单元范围桥接。"""

    return sorted({
        bridge.kp_id
        for bridge in question.bridges
        if bridge.match_method != SCOPE_BRIDGE_MATCH_METHOD
    })


def to_learner_view(question: QuestionDetail) -> LearnerQuestionView:
    return LearnerQuestionView(
        question_id=question.question_id,
        question_type=question.question_type,
        stem=question.stem,
        options=question.options,
        tags=question.tags,
        kp_ids=question_kp_ids(question),
    )

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


class EvidencePack(ContractModel):
    evidence_pack_id: str
    query: str
    resolved_kp_ids: list[str] = Field(default_factory=list)
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    retrieval_summary: str = ""
    summary_evidence_ids: list[str] = Field(default_factory=list)
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


class QuestionDetail(ContractModel):
    question_id: str
    question_type: str
    stem: str
    reference_answer: str
    analysis: str | None
    options: list[str] = Field(default_factory=list)
    origin: Literal["retrieved", "generated"] = "retrieved"
    source_tier: Literal["textbook", "web_reference", "model_knowledge"] = "textbook"
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

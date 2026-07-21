from __future__ import annotations

from competition_app.contracts.knowledge import (
    QuestionBridge,
    QuestionDetail,
    QuestionRetrievalMetadata,
    QuestionSearchResult,
)


class StubQuestionRetriever:
    """Deterministic offline candidate source for exercising the question-search flow."""

    async def search(
        self, query: str, resolved_kp_ids: list[str], limit: int
    ) -> QuestionSearchResult:
        kp_id = resolved_kp_ids[0] if resolved_kp_ids else "KP_DEMO_001"
        item = QuestionDetail(
            question_id="Q_DEMO_FJ_001",
            question_type="单项选择题",
            stem="四君子汤的功效是？",
            reference_answer="益气健脾",
            analysis="四君子汤为补气基础方。",
            options=["A. 益气健脾", "B. 温中散寒", "C. 清热解毒", "D. 滋阴润燥"],
            tags=["方剂学", "补益剂"],
            source_metadata={"source": "stub"},
            bridges=[
                QuestionBridge(
                    kp_id=kp_id,
                    bridge_layer="strict",
                    relation="primary",
                    confidence=1.0,
                    rank=1,
                    evidence_chunk_uid="DEMO_FJ:00001",
                    match_method="stub",
                )
            ],
            retrieval=QuestionRetrievalMetadata(
                channels=["bridge", "bm25", "vector"],
                channel_scores={"bridge": 1.0, "bm25": 0.75, "vector": 0.85},
                fusion_score=1.0,
            ),
        )
        return QuestionSearchResult(
            query=query,
            resolved_kp_ids=resolved_kp_ids,
            embedding_model="stub",
            vector_index_path="stub://question-index",
            items=[item][:limit],
        )
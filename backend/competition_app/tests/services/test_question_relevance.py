from __future__ import annotations

import pytest

from competition_app.contracts.knowledge import (
    QuestionDetail,
    QuestionRetrievalMetadata,
)
from competition_app.services.question_relevance import QuestionRelevanceService


class FixedReranker:
    model = "test-reranker"

    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.queries: list[str] = []
        self.documents: list[str] = []

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.queries.append(query)
        self.documents.extend(documents)
        return self.scores[: len(documents)]


def question(question_id: str, stem: str) -> QuestionDetail:
    return QuestionDetail(
        question_id=question_id,
        question_type="单项选择题",
        stem=stem,
        options=["A. 甲", "B. 乙"],
        reference_answer="A",
        analysis="教材解析",
        tags=["四君子汤"],
        source_metadata={},
        bridges=[],
        retrieval=QuestionRetrievalMetadata(
            channels=["bridge"],
            channel_scores={"bridge": 1.0},
            fusion_score=1.0,
        ),
    )


@pytest.mark.asyncio
async def test_shadow_rerank_records_scores_without_rejecting() -> None:
    client = FixedReranker([0.1, 0.9])
    service = QuestionRelevanceService(client, mode="shadow")

    batch = await service.score(
        knowledge_module="四君子汤配伍意义",
        retrieval_query="四君子汤 君臣佐使 配伍意义",
        assessment_dimensions=[],
        excluded_dimensions=[],
        items=[question("Q1", "与哪一方病机接近？"), question("Q2", "何药为君药？")],
    )

    assert batch.scores["Q1"].status == "shadow"
    assert batch.scores["Q2"].rank == 1
    assert "直接考查" in client.queries[0]
    assert "答案：A" in client.documents[0]


@pytest.mark.asyncio
async def test_gate_rerank_assigns_three_state_admission() -> None:
    service = QuestionRelevanceService(
        FixedReranker([0.2, 0.5, 0.8]),
        mode="gate",
        eligible_threshold=0.65,
        reject_threshold=0.3,
    )

    batch = await service.score(
        knowledge_module="模块",
        retrieval_query="目标",
        assessment_dimensions=[],
        excluded_dimensions=[],
        items=[question("Q1", "一"), question("Q2", "二"), question("Q3", "三")],
    )

    assert [batch.scores[f"Q{i}"].status for i in range(1, 4)] == [
        "rejected",
        "uncertain",
        "eligible",
    ]

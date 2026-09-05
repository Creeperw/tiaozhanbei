from __future__ import annotations

import json
from pathlib import Path

import faiss
import numpy as np
import pytest

from competition_app.embeddings.base import EmbeddingModel
from competition_app.tools.knowledge_repository import KnowledgeRepository, KnowledgeRepositoryPaths
from competition_app.tools.question_channel_reservation import reserve_question_details
from competition_app.tools.question_retrieval import QuestionHybridRetriever, QuestionVectorIndexError
from competition_app.contracts.knowledge import QuestionDetail, QuestionRetrievalMetadata


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "knowledge_delivery"


class FixedEmbeddingModel(EmbeddingModel):
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.vector for _ in texts]


def build_repository() -> KnowledgeRepository:
    return KnowledgeRepository(
        KnowledgeRepositoryPaths(
            knowledge_points=FIXTURE_ROOT / "knowledge_points.json",
            kp_chunk_links=FIXTURE_ROOT / "kp_chunk_links.jsonl",
            source_chunks=FIXTURE_ROOT / "source_chunks.jsonl",
            questions=FIXTURE_ROOT / "questions.json",
            question_kp_matches=FIXTURE_ROOT / "question_kp_matches.jsonl",
        )
    )


def write_faiss_fixture(root: Path, *, dimension: int, question_id: str) -> None:
    index_root = root / "indexes" / "题库"
    index_root.mkdir(parents=True)
    index = faiss.IndexFlatIP(dimension)
    vector = np.zeros((1, dimension), dtype="float32")
    vector[0, 0] = 1.0
    index.add(vector)
    faiss.write_index(index, str(index_root / "index.faiss"))
    (index_root / "metadata.jsonl").write_text(
        json.dumps({"original": {"题目id": question_id}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def build_retriever(vector_store_root: Path) -> QuestionHybridRetriever:
    return QuestionHybridRetriever(
        build_repository(),
        FixedEmbeddingModel([1.0, 0.0, 0.0, 0.0]),
        embedding_model_name="Qwen/Qwen3-Embedding-4B",
        vector_store_root=vector_store_root,
    )


@pytest.mark.asyncio
async def test_hybrid_retrieval_merges_bridge_bm25_and_vector_channels(tmp_path: Path) -> None:
    write_faiss_fixture(tmp_path, dimension=4, question_id="Q_FJ_1")

    result = await build_retriever(tmp_path).search("四君子汤", ["KP_FJ_001"], limit=3)

    item = result.items[0]
    assert item.question_id == "Q_FJ_1"
    assert item.retrieval.channels == ["bridge", "bm25", "vector"]
    assert item.retrieval.fusion_score > 0
    assert item.retrieval.fusion_strategy == "rrf_v1"
    assert item.retrieval.channel_ranks == {"bridge": 1, "bm25": 1, "vector": 1}
    assert item.retrieval.channel_scores == {"bridge": 1.0, "bm25": 1.0, "vector": 1.0}
    assert result.fusion_strategy == "rrf_v1"
    assert item.options


@pytest.mark.asyncio
async def test_hybrid_retrieval_rejects_vector_dimension_mismatch(tmp_path: Path) -> None:
    write_faiss_fixture(tmp_path, dimension=3, question_id="Q_FJ_1")

    with pytest.raises(QuestionVectorIndexError, match="dimension"):
        await build_retriever(tmp_path).search("四君子汤", ["KP_FJ_001"], limit=3)


@pytest.mark.asyncio
async def test_hybrid_retrieval_rejects_missing_vector_index(tmp_path: Path) -> None:
    with pytest.raises(QuestionVectorIndexError, match="missing"):
        await build_retriever(tmp_path).search("四君子汤", ["KP_FJ_001"], limit=3)


def test_channel_reservation_keeps_bm25_and_vector_inside_limit() -> None:
    def detail(question_id: str, channels: list[str], score: float) -> QuestionDetail:
        return QuestionDetail(
            question_id=question_id,
            question_type="single_choice",
            stem=question_id,
            reference_answer="A",
            analysis=None,
            options=[],
            tags=[],
            source_metadata={},
            bridges=[],
            retrieval=QuestionRetrievalMetadata(
                channels=channels,
                channel_scores={channel: score for channel in channels},
                fusion_score=score,
            ),
        )

    ranked = [
        detail("BRIDGE_1", ["bridge"], 1.0),
        detail("BRIDGE_2", ["bridge"], 0.99),
        detail("BRIDGE_3", ["bridge"], 0.98),
        detail("BM25", ["bm25"], 0.7),
        detail("VECTOR", ["vector"], 0.6),
    ]

    selected = reserve_question_details(ranked, limit=3)

    assert [item.question_id for item in selected] == ["BRIDGE_1", "BM25", "VECTOR"]

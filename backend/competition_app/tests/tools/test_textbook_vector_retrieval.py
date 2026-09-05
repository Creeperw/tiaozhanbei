import json

import numpy as np
import pytest

from competition_app.embeddings.base import EmbeddingModel
from competition_app.tools.textbook_vector_retrieval import TextbookVectorRetriever


class FixedEmbeddingModel(EmbeddingModel):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


@pytest.mark.asyncio
async def test_textbook_vector_retriever_returns_ranked_metadata(tmp_path) -> None:
    faiss = pytest.importorskip("faiss")
    directory = tmp_path / "indexes" / "方剂学.jsonl"
    directory.mkdir(parents=True)
    vectors = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype="float32")
    faiss.normalize_L2(vectors)
    index = faiss.IndexFlatIP(2)
    index.add(vectors)
    faiss.write_index(index, str(directory / "index.faiss"))
    rows = [
        {
            "source": "方剂学.jsonl",
            "record_id": 0,
            "content": "四君子汤由人参、白术、茯苓、甘草组成。",
            "original": {"chunk_id": "00001", "metadata": {"book": "方剂学"}},
        },
        {
            "source": "方剂学.jsonl",
            "record_id": 1,
            "content": "理中丸相关内容。",
            "original": {"chunk_id": "00002", "metadata": {"book": "方剂学"}},
        },
    ]
    (directory / "metadata.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8"
    )
    retriever = TextbookVectorRetriever(
        FixedEmbeddingModel(), tmp_path, embedding_model_name="test-model", similarity_threshold=0.5
    )

    hits = await retriever.search("四君子汤", limit=2)

    assert len(hits) == 1
    assert hits[0].source_id == "方剂学:00001"
    assert "人参" in hits[0].content
    assert hits[0].metadata["embedding_model"] == "test-model"


@pytest.mark.asyncio
async def test_textbook_vector_retriever_rejects_dimension_mismatch(tmp_path) -> None:
    faiss = pytest.importorskip("faiss")
    directory = tmp_path / "indexes" / "方剂学.jsonl"
    directory.mkdir(parents=True)
    index = faiss.IndexFlatIP(3)
    index.add(np.asarray([[1.0, 0.0, 0.0]], dtype="float32"))
    faiss.write_index(index, str(directory / "index.faiss"))
    (directory / "metadata.jsonl").write_text(
        json.dumps({"content": "教材", "original": {"chunk_id": "1"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    retriever = TextbookVectorRetriever(
        FixedEmbeddingModel(), tmp_path, embedding_model_name="test-model"
    )

    with pytest.raises(ValueError, match="embedding dimension"):
        await retriever.search("四君子汤")

import pytest

from competition_app.services.memory_retrieval import MemoryRetrievalService


class FixedEmbeddingModel:
    async def embed(self, texts):
        vectors = {
            "每天安排一小时学习": [1.0, 0.0],
            "[preference] 每日时长\n每天最多学习二十分钟。": [0.95, 0.05],
            "[preference] 学习资源\n喜欢先看对比表。": [0.0, 1.0],
        }
        return [vectors[text] for text in texts]


class FailingEmbeddingModel:
    async def embed(self, texts):
        raise RuntimeError("provider unavailable")


def memories(_learner_id):
    return [
        {
            "id": 1,
            "category": "preference",
            "importance": "normal",
            "title": "每日时长",
            "content": "每天最多学习二十分钟。",
            "updated_at": "2026-07-20T00:00:00+00:00",
        },
        {
            "id": 2,
            "category": "preference",
            "importance": "important",
            "title": "学习资源",
            "content": "喜欢先看对比表。",
            "updated_at": "2026-07-21T00:00:00+00:00",
        },
    ]


@pytest.mark.asyncio
async def test_retrieval_ranks_semantically_related_memory_first() -> None:
    service = MemoryRetrievalService(FixedEmbeddingModel(), memories)

    result = await service.retrieve("learner-1", "每天安排一小时学习")

    assert result["degraded"] is False
    assert [item["id"] for item in result["items"]] == [1, 2]
    assert result["items"][0]["similarity"] > result["items"][1]["similarity"]


@pytest.mark.asyncio
async def test_retrieval_falls_back_explicitly_when_embedding_is_unavailable() -> None:
    service = MemoryRetrievalService(FailingEmbeddingModel(), memories)

    result = await service.retrieve("learner-1", "每天安排一小时学习")

    assert result["degraded"] is True
    assert result["error"] == "RuntimeError"
    assert result["items"][0]["id"] == 2
    assert all(item["similarity"] is None for item in result["items"])

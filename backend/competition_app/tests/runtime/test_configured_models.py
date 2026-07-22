import asyncio

from competition_app.embeddings.configurable import UserConfiguredEmbeddingModel
from competition_app.llm.configurable import UserConfiguredChatModel
from competition_app.runtime.model_credentials import (
    RuntimeModelCredentials,
    bind_model_credentials,
    reset_model_credentials,
)


class FallbackChatModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {"provider": "fallback"}


class FallbackEmbeddingModel:
    async def embed(self, texts):
        return [[0.0] for _ in texts]


def test_chat_model_uses_request_scoped_deepseek_key(monkeypatch) -> None:
    created = []

    class FakeDeepSeekModel:
        def __init__(self, base_url, api_key, model):
            created.append((base_url, api_key, model))

        async def complete_json(self, role, payload, on_delta=None):
            return {"provider": "deepseek"}

    monkeypatch.setattr(
        "competition_app.llm.configurable.OpenAICompatibleChatModel",
        FakeDeepSeekModel,
    )
    model = UserConfiguredChatModel(FallbackChatModel())

    async def exercise():
        assert await model.complete_json("agent", {}) == {"provider": "fallback"}
        token = bind_model_credentials(
            RuntimeModelCredentials(deepseek_api_key="ds-key")
        )
        try:
            assert await model.complete_json("agent", {}) == {"provider": "deepseek"}
        finally:
            reset_model_credentials(token)
        assert await model.complete_json("agent", {}) == {"provider": "fallback"}

    asyncio.run(exercise())

    assert created == [
        ("https://api.deepseek.com", "ds-key", "deepseek-v4-flash")
    ]


def test_embedding_model_uses_request_scoped_siliconflow_key(monkeypatch) -> None:
    created = []

    class FakeSiliconFlowModel:
        def __init__(self, base_url, api_key, model):
            created.append((base_url, api_key, model))

        async def embed(self, texts):
            return [[1.0] for _ in texts]

    monkeypatch.setattr(
        "competition_app.embeddings.configurable.SiliconFlowEmbeddingModel",
        FakeSiliconFlowModel,
    )
    model = UserConfiguredEmbeddingModel(FallbackEmbeddingModel())

    async def exercise():
        assert await model.embed(["a"]) == [[0.0]]
        token = bind_model_credentials(
            RuntimeModelCredentials(siliconflow_api_key="sf-key")
        )
        try:
            assert await model.embed(["a"]) == [[1.0]]
        finally:
            reset_model_credentials(token)
        assert await model.embed(["a"]) == [[0.0]]

    asyncio.run(exercise())

    assert created == [
        ("https://api.siliconflow.cn/v1", "sf-key", "Qwen/Qwen3-Embedding-4B")
    ]

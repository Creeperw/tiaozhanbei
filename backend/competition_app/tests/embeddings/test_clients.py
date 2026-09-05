import httpx
import pytest

from competition_app.embeddings.siliconflow import SiliconFlowEmbeddingModel
from competition_app.embeddings.stub import StubEmbeddingModel


@pytest.mark.asyncio
async def test_siliconflow_client_calls_embeddings_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})

    model = SiliconFlowEmbeddingModel(
        base_url="https://api.siliconflow.cn/v1",
        api_key="secret-value",
        model="Qwen/Qwen3-Embedding-4B",
        transport=httpx.MockTransport(handler),
    )

    assert await model.embed(["四君子汤"]) == [[0.1, 0.2]]
    assert requests[0].url == "https://api.siliconflow.cn/v1/embeddings"
    assert b'"model":"Qwen/Qwen3-Embedding-4B"' in requests[0].content


@pytest.mark.asyncio
async def test_stub_embeddings_are_deterministic() -> None:
    model = StubEmbeddingModel(dimensions=8)
    first = await model.embed(["四君子汤"])
    second = await model.embed(["四君子汤"])

    assert first == second
    assert len(first[0]) == 8

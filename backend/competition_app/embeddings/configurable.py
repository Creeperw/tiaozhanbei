from __future__ import annotations

from competition_app.embeddings.base import EmbeddingModel
from competition_app.embeddings.siliconflow import SiliconFlowEmbeddingModel
from competition_app.runtime.model_credentials import current_model_credentials


class UserConfiguredEmbeddingModel(EmbeddingModel):
    """Use the request owner's SiliconFlow key for semantic retrieval."""

    def __init__(self, fallback: EmbeddingModel) -> None:
        self.fallback = fallback
        self._clients: dict[str, SiliconFlowEmbeddingModel] = {}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        credentials = current_model_credentials()
        key = credentials.siliconflow_api_key if credentials else ""
        if not key:
            return await self.fallback.embed(texts)
        client = self._clients.get(key)
        if client is None:
            client = SiliconFlowEmbeddingModel(
                "https://api.siliconflow.cn/v1",
                key,
                "Qwen/Qwen3-Embedding-4B",
            )
            self._clients[key] = client
        return await client.embed(texts)

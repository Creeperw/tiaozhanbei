from __future__ import annotations

import httpx

from competition_app.embeddings.base import EmbeddingModel


class EmbeddingResponseError(RuntimeError):
    """Raised when the embedding provider cannot return valid vectors."""


class SiliconFlowEmbeddingModel(EmbeddingModel):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.post(
                    f"{self.base_url}/embeddings",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={"model": self.model, "input": texts},
                )
                response.raise_for_status()
                data = response.json()["data"]
                return [[float(value) for value in item["embedding"]] for item in data]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmbeddingResponseError(
                f"Embedding model request failed: {type(exc).__name__}"
            ) from exc

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable

from competition_app.embeddings.base import EmbeddingModel


class MemoryRetrievalService:
    """Retrieve relevant learner memories without making semantic decisions."""

    def __init__(
        self,
        embedding_model: EmbeddingModel,
        memory_loader: Callable[[str], list[dict[str, Any]]],
        *,
        default_limit: int = 8,
    ) -> None:
        self.embedding_model = embedding_model
        self.memory_loader = memory_loader
        self.default_limit = default_limit

    async def retrieve(
        self,
        learner_id: str,
        query: str,
        *,
        limit: int | None = None,
    ) -> dict[str, Any]:
        memories = list(self.memory_loader(learner_id))
        if not query.strip() or not memories:
            return {"items": [], "degraded": False, "error": None}
        texts = [query, *[self._memory_text(item) for item in memories]]
        try:
            vectors = await self.embedding_model.embed(texts)
            if len(vectors) != len(texts):
                raise ValueError("embedding response count mismatch")
            query_vector = vectors[0]
            ranked = sorted(
                (
                    {
                        **item,
                        "similarity": self._cosine(query_vector, vector),
                    }
                    for item, vector in zip(memories, vectors[1:])
                ),
                key=lambda item: (
                    float(item["similarity"]),
                    self._importance_score(item),
                    self._updated_timestamp(item),
                ),
                reverse=True,
            )
            return {
                "items": ranked[: max(1, limit or self.default_limit)],
                "degraded": False,
                "error": None,
            }
        except Exception as exc:
            ranked = sorted(
                memories,
                key=lambda item: (
                    self._importance_score(item),
                    self._updated_timestamp(item),
                ),
                reverse=True,
            )
            return {
                "items": [
                    {**item, "similarity": None}
                    for item in ranked[: max(1, limit or self.default_limit)]
                ],
                "degraded": True,
                "error": type(exc).__name__,
            }

    @staticmethod
    def _memory_text(item: dict[str, Any]) -> str:
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        category = str(item.get("category") or "note").strip()
        return f"[{category}] {title}\n{content}".strip()

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if not left or len(left) != len(right):
            raise ValueError("embedding vectors have incompatible dimensions")
        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        return numerator / (left_norm * right_norm)

    @staticmethod
    def _importance_score(item: dict[str, Any]) -> int:
        return 1 if item.get("importance") == "important" else 0

    @staticmethod
    def _updated_timestamp(item: dict[str, Any]) -> float:
        value = item.get("updated_at")
        if not value:
            return 0.0
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            return 0.0
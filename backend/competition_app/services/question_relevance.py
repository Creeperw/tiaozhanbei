from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal, Protocol

from competition_app.contracts.knowledge import QuestionDetail

RerankMode = Literal["disabled", "shadow", "sort", "gate"]
SemanticStatus = Literal["not_evaluated", "shadow", "eligible", "uncertain", "rejected"]


@dataclass(frozen=True)
class RelevanceScore:
    question_id: str
    score: float
    rank: int
    status: SemanticStatus


@dataclass(frozen=True)
class RelevanceBatch:
    scores: dict[str, RelevanceScore]
    model: str | None
    query_version: str
    degraded: bool = False
    error: str | None = None


class RerankClient(Protocol):
    model: str

    async def rerank(self, query: str, documents: list[str]) -> list[float]: ...


class OpenAICompatibleRerankClient:
    """Minimal `/rerank` client compatible with SiliconFlow-style responses."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.url = base_url.rstrip("/") + "/rerank"
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        return await asyncio.to_thread(self._rerank_sync, query, documents)

    def _rerank_sync(self, query: str, documents: list[str]) -> list[float]:
        body = json.dumps(
            {"model": self.model, "query": query, "documents": documents}
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"rerank request failed: {type(exc).__name__}") from exc
        scores = [0.0] * len(documents)
        rows = payload.get("results") or payload.get("data") or []
        for fallback_index, row in enumerate(rows):
            index = int(row.get("index", fallback_index))
            if 0 <= index < len(scores):
                scores[index] = float(
                    row.get("relevance_score", row.get("score", 0.0))
                )
        return scores


class QuestionRelevanceService:
    QUERY_VERSION = "blueprint-question-v1"

    def __init__(
        self,
        client: RerankClient | None,
        *,
        mode: RerankMode = "disabled",
        top_n: int = 30,
        batch_size: int = 30,
        eligible_threshold: float = 0.65,
        reject_threshold: float = 0.30,
    ) -> None:
        if reject_threshold > eligible_threshold:
            raise ValueError("reject_threshold must not exceed eligible_threshold")
        self.client = client
        self.mode = mode
        self.top_n = max(1, top_n)
        self.batch_size = max(1, batch_size)
        self.eligible_threshold = eligible_threshold
        self.reject_threshold = reject_threshold

    async def score(
        self,
        *,
        knowledge_module: str,
        retrieval_query: str,
        assessment_dimensions: list[str],
        excluded_dimensions: list[str],
        items: list[QuestionDetail],
    ) -> RelevanceBatch:
        if self.mode == "disabled" or self.client is None or not items:
            return RelevanceBatch(
                scores={},
                model=getattr(self.client, "model", None),
                query_version=self.QUERY_VERSION,
            )
        selected = items[: self.top_n]
        query = self._query_text(
            knowledge_module,
            retrieval_query,
            assessment_dimensions,
            excluded_dimensions,
        )
        try:
            values: list[float] = []
            for offset in range(0, len(selected), self.batch_size):
                batch = selected[offset : offset + self.batch_size]
                batch_scores = await self.client.rerank(
                    query, [self._document_text(item) for item in batch]
                )
                if len(batch_scores) != len(batch):
                    raise RuntimeError("rerank response count mismatch")
                values.extend(batch_scores)
        except (LookupError, RuntimeError, TimeoutError, ValueError) as exc:
            return RelevanceBatch(
                scores={},
                model=getattr(self.client, "model", None),
                query_version=self.QUERY_VERSION,
                degraded=True,
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
            )
        normalized = [max(0.0, min(1.0, float(value))) for value in values]
        ranked_indices = sorted(
            range(len(selected)), key=lambda index: (-normalized[index], index)
        )
        ranks = {index: rank for rank, index in enumerate(ranked_indices, 1)}
        scores: dict[str, RelevanceScore] = {}
        for index, item in enumerate(selected):
            score = normalized[index]
            if self.mode in {"shadow", "sort"}:
                status: SemanticStatus = "shadow"
            elif score >= self.eligible_threshold:
                status = "eligible"
            elif score < self.reject_threshold:
                status = "rejected"
            else:
                status = "uncertain"
            scores[item.question_id] = RelevanceScore(
                question_id=item.question_id,
                score=score,
                rank=ranks[index],
                status=status,
            )
        return RelevanceBatch(
            scores=scores,
            model=self.client.model,
            query_version=self.QUERY_VERSION,
        )

    @staticmethod
    def _query_text(
        knowledge_module: str,
        retrieval_query: str,
        assessment_dimensions: list[str],
        excluded_dimensions: list[str],
    ) -> str:
        parts = [
            f"知识模块：{knowledge_module}",
            f"组卷目标：{retrieval_query}",
        ]
        if assessment_dimensions:
            parts.append("应考查：" + "、".join(assessment_dimensions))
        if excluded_dimensions:
            parts.append("不应考查：" + "、".join(excluded_dimensions))
        parts.append("判断候选题是否直接考查该组卷目标，而非仅在答案或解析中提到相关实体。")
        return "\n".join(parts)

    @staticmethod
    def _document_text(item: QuestionDetail) -> str:
        kp_names = [
            str(value)
            for value in item.tags
            if str(value).strip()
        ]
        parts = [
            f"题型：{item.question_type}",
            f"题干：{item.stem}",
        ]
        if item.options:
            parts.append("选项：" + " | ".join(item.options))
        if item.reference_answer:
            parts.append(f"答案：{item.reference_answer}")
        if item.analysis:
            parts.append(f"解析：{item.analysis}")
        if kp_names:
            parts.append("知识点：" + "、".join(kp_names))
        return "\n".join(parts)

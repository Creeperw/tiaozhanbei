# -*- coding: utf-8 -*-
"""学习者记忆混合检索：向量 + BM25 + 时间衰减。

设计目标：从用户的 PersonalizationMemory 中挑出与当前请求最相关的记忆，
而不是把全部记忆一股脑塞进智能体上下文。

通道说明：
- 向量通道：复用 RAGService 单例的 embedding 模型（EMBEDDING_MODE=enabled 且
  模型可用时）。余弦相似度直接作为相关度。
- BM25 通道：中文 bigram + 英文词 tokenize 的轻量实现，零外部依赖，恒可用。
- 时间通道：指数衰减（半衰期可配），越新的记忆权重越高。
- 重要性加成：importance=important 的记忆获得额外加分，保证重要记忆不因
  检索噪声被挤掉。

任一内容通道（向量/BM25）不可用时自动降级，不会抛错。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Sequence

from APP.backend.config import MEMORY_RETRIEVAL_LIMIT

# 通道权重（可用通道之间会重新归一化，保证总分落在合理区间）
_VECTOR_WEIGHT = 0.45
_BM25_WEIGHT = 0.30
_RECENCY_WEIGHT = 0.25
# importance=important 的额外加分（0~1 的绝对增量）
_IMPORTANCE_BONUS = 0.15
# 时间半衰期（天）：记忆越旧，时间分越低
_TIME_HALF_LIFE_DAYS = 14.0
# 向量余弦相似度低于该值视为“未命中内容通道”
_VECTOR_HIT_THRESHOLD = 0.20
# BM25 粗筛阶段保留的候选数上限（向量精排只对粗筛候选做）
_BM25_CANDIDATE_MULTIPLIER = 4


def _tokens(text: str) -> list[str]:
    """中文按双字 bigram + 英文/数字按词切分，与题库 BM25 检索保持一致。"""
    normalized = re.sub(r"\s+", "", (text or "").lower())
    chinese = [
        normalized[index : index + 2]
        for index in range(max(0, len(normalized) - 1))
        if "\u4e00" <= normalized[index] <= "\u9fff"
    ]
    return chinese + re.findall(r"[a-z0-9_]{2,}", normalized)


class _BM25:
    """轻量 BM25（k1=1.2, b=0.75, 平滑 idf）。"""

    def __init__(self, corpus: list[list[str]]) -> None:
        self._frequencies = [Counter(row) for row in corpus]
        self._lengths = [len(row) for row in corpus]
        self._average = sum(self._lengths) / len(self._lengths) if self._lengths else 0.0
        document_frequency: Counter[str] = Counter()
        for row in self._frequencies:
            document_frequency.update(row)
        count = len(corpus)
        self._idf = {
            term: math.log(1 + (count - value + 0.5) / (value + 0.5))
            for term, value in document_frequency.items()
        }

    def scores(self, query: list[str]) -> list[float]:
        values = [0.0] * len(self._frequencies)
        if not self._average:
            return values
        for term in set(query):
            if term not in self._idf:
                continue
            for index, frequency in enumerate(self._frequencies):
                tf = frequency.get(term, 0)
                denominator = tf + 1.2 * (1 - 0.75 + 0.75 * self._lengths[index] / self._average)
                values[index] += self._idf[term] * tf * 2.2 / max(denominator, 1e-9)
        return values


def _parse_dt(value: Any) -> datetime | None:
    """兼容 datetime 对象与 ISO 字符串（含 Z 后缀）。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _recency_score(updated_at: Any, now: datetime | None = None) -> float:
    ts = _parse_dt(updated_at)
    if ts is None:
        return 0.0
    now = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
    return 2.0 ** (-age_days / _TIME_HALF_LIFE_DAYS)


def _embedding_model() -> Any | None:
    """返回可用的 embedding 模型；不可用时返回 None（向量通道降级）。"""
    try:
        from APP.backend.config import EMBEDDING_MODE
        from APP.backend.rag_core import RAGService
    except Exception:
        return None
    if EMBEDDING_MODE != "enabled":
        return None
    try:
        service = RAGService()
        if getattr(service, "embedding_state", "") != "ready" or service.model is None:
            return None
        return service.model
    except Exception:
        return None


def _embedding_similarity(model: Any, left: str, right: str) -> float | None:
    """L2 归一化后的余弦相似度；失败返回 None。"""
    if model is None:
        return None
    try:
        import numpy as np

        vectors = model.encode([left, right], convert_to_numpy=True)
        first = np.asarray(vectors[0], dtype="float32")
        second = np.asarray(vectors[1], dtype="float32")
        if first.ndim != 1 or second.ndim != 1 or first.size == 0 or second.size == 0:
            return None
        denominator = float(np.linalg.norm(first) * np.linalg.norm(second))
        if denominator <= 1e-12:
            return None
        return float(np.dot(first, second) / denominator)
    except Exception:
        return None


def _memory_text(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "").strip()
    content = str(item.get("content") or "").strip()
    return f"{title}：{content}" if title else content


def _rank_with_query(
    query: str,
    memories: Sequence[dict[str, Any]],
    *,
    top_n: int,
) -> list[dict[str, Any]]:
    """有查询：BM25 粗筛 → 向量精排 → 时间/重要性融合排序。"""
    corpus = [_tokens(_memory_text(memory)) for memory in memories]
    bm25 = _BM25(corpus).scores(_tokens(query))
    max_bm25 = max(bm25, default=0.0)

    # 粗筛候选：BM25 top-K（含 bm25=0 的候选会在精排中被过滤）
    candidate_limit = max(top_n * _BM25_CANDIDATE_MULTIPLIER, 20)
    candidate_indexes = sorted(range(len(memories)), key=lambda i: bm25[i], reverse=True)[:candidate_limit]

    model = _embedding_model()

    scored: list[dict[str, Any]] = []
    for index in candidate_indexes:
        memory = memories[index]
        vector_score = None
        if model is not None:
            vector_score = _embedding_similarity(model, query, _memory_text(memory))
            if vector_score is not None and vector_score < 0.0:
                vector_score = 0.0

        hit_content_channel = (bm25[index] > 0) or (
            vector_score is not None and vector_score >= _VECTOR_HIT_THRESHOLD
        )
        if not hit_content_channel:
            # 与查询无任何内容关联的记忆不注入，避免上下文噪音
            continue

        weights: list[float] = []
        components: list[float] = []
        if vector_score is not None:
            weights.append(_VECTOR_WEIGHT)
            components.append(min(1.0, vector_score))
        if bm25[index] > 0:
            weights.append(_BM25_WEIGHT)
            components.append(bm25[index] / max_bm25 if max_bm25 > 0 else 0.0)
        weights.append(_RECENCY_WEIGHT)
        components.append(_recency_score(memory.get("updated_at")))

        score = sum(w * c for w, c in zip(weights, components)) / sum(weights)
        if (memory.get("importance") or "normal") == "important":
            score += _IMPORTANCE_BONUS

        item = dict(memory)
        item["retrieval_score"] = round(score, 6)
        item["retrieval_channels"] = {
            "vector": vector_score is not None,
            "bm25": bm25[index] > 0,
        }
        scored.append(item)

    scored.sort(key=lambda item: item["retrieval_score"], reverse=True)
    return scored[:top_n]


def rank_memories(
    query: str,
    memories: Sequence[dict[str, Any]],
    *,
    top_n: int | None = None,
) -> list[dict[str, Any]]:
    """混合检索：向量 + BM25 + 时间衰减 + 重要性。

    参数：
        query: 当前用户请求文本；为空时按“时间新 + 重要”排序（不做内容筛选）。
        memories: 记忆条目列表，每项至少含 title/content/category/importance/updated_at。
        top_n: 返回条数上限，默认取 MEMORY_RETRIEVAL_LIMIT。

    返回：
        带 retrieval_score 的记忆副本列表（按相关度降序）。
    """
    if not memories:
        return []
    top_n = top_n or MEMORY_RETRIEVAL_LIMIT
    query_text = (query or "").strip()

    if not query_text:
        ranked = sorted(
            memories,
            key=lambda item: (
                _recency_score(item.get("updated_at")),
                1.0 if (item.get("importance") or "normal") == "important" else 0.0,
            ),
            reverse=True,
        )
        result = []
        for item in ranked:
            copy = dict(item)
            copy["retrieval_score"] = round(_recency_score(item.get("updated_at")), 6)
            copy["retrieval_channels"] = {"vector": False, "bm25": False}
            result.append(copy)
        return result[:top_n]

    return _rank_with_query(query_text, memories, top_n=top_n)

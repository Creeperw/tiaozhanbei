"""Shared hybrid question retrieval primitives.

The module deliberately knows nothing about SQLAlchemy, Atlas file layouts, or
FastAPI. Data-source adapters provide complete question dictionaries and
optional vector hits; this layer performs independent Bridge/KP and BM25
recall, reciprocal-rank fusion, optional Cross-Encoder reranking, and stable
constraint filtering.
"""

from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

RerankMode = Literal["disabled", "shadow", "sort", "gate"]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [_text(item) for item in value if _text(item)]
    return [_text(value)] if _text(value) else []


def question_id(item: Mapping[str, Any]) -> str:
    return _text(item.get("question_id") or item.get("题目id"))


def question_kp_ids(item: Mapping[str, Any]) -> list[str]:
    value = item.get("kp_ids", item.get("标签", []))
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            decoded = []
        value = decoded
    return _list(value)


def searchable_text(item: Mapping[str, Any]) -> str:
    options = item.get("options") or []
    if isinstance(options, str):
        try:
            options = json.loads(options)
        except (TypeError, ValueError):
            options = [options]
    return "\n".join(
        part for part in (
            _text(item.get("stem") or item.get("question_content") or item.get("题目内容")),
            json.dumps(options, ensure_ascii=False) if options else "",
            _text(item.get("answer") or item.get("题目答案")),
            _text(item.get("analysis") or item.get("explanation") or item.get("题目答案解析")),
            " ".join(question_kp_ids(item)),
        ) if part
    )


def tokenize(text: str) -> list[str]:
    normalized = re.sub(r"\s+", "", _text(text).lower())
    chinese = [
        normalized[index:index + 2]
        for index in range(max(0, len(normalized) - 1))
        if "\u4e00" <= normalized[index] <= "\u9fff"
    ]
    return chinese + re.findall(r"[a-z0-9_]{2,}", normalized)


class BM25:
    def __init__(self, corpus: Sequence[Sequence[str]]) -> None:
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

    def scores(self, query: Sequence[str]) -> list[float]:
        values = [0.0] * len(self._frequencies)
        if not self._average:
            return values
        for term in set(query):
            if term not in self._idf:
                continue
            for index, frequency in enumerate(self._frequencies):
                tf = frequency.get(term, 0)
                denominator = tf + 1.2 * (
                    1 - 0.75 + 0.75 * self._lengths[index] / self._average
                )
                values[index] += self._idf[term] * tf * 2.2 / max(denominator, 1e-9)
        return values


@dataclass(frozen=True)
class FusionResult:
    score: float
    channel_ranks: dict[str, int]
    channel_scores: dict[str, float]


def reciprocal_rank_fusion(
    channels: Mapping[str, Sequence[tuple[str, float]]],
    *,
    rank_constant: int = 60,
) -> dict[str, FusionResult]:
    ranks: dict[str, dict[str, int]] = {}
    scores: dict[str, dict[str, float]] = {}
    active = 0
    for channel, rows in channels.items():
        seen: set[str] = set()
        rank = 0
        if rows:
            active += 1
        for raw_id, raw_score in rows:
            qid = _text(raw_id)
            if not qid or qid in seen:
                continue
            seen.add(qid)
            rank += 1
            ranks.setdefault(qid, {})[channel] = rank
            scores.setdefault(qid, {})[channel] = float(raw_score)
    maximum = active / (rank_constant + 1) if active else 0.0
    return {
        qid: FusionResult(
            score=(sum(1 / (rank_constant + rank) for rank in row.values()) / maximum)
            if maximum else 0.0,
            channel_ranks=dict(row),
            channel_scores=dict(scores.get(qid, {})),
        )
        for qid, row in ranks.items()
    }


def _rerank(
    query: str,
    rows: list[dict[str, Any]],
    *,
    mode: RerankMode,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    base_url = os.getenv("QUESTION_RERANK_BASE_URL", "").strip()
    api_key = os.getenv("QUESTION_RERANK_API_KEY", "").strip()
    model = os.getenv("QUESTION_RERANK_MODEL", "Qwen/Qwen3-Reranker-8B").strip()
    if mode == "disabled" or not base_url or not rows:
        return rows, False, None
    body = json.dumps({
        "model": model,
        "query": query,
        "documents": [searchable_text(row) for row in rows],
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        base_url.rstrip("/") + "/rerank", data=body, headers=headers, method="POST"
    )
    try:
        timeout = float(os.getenv("QUESTION_RERANK_TIMEOUT_SECONDS", "30"))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        values = [0.0] * len(rows)
        for fallback, result in enumerate(payload.get("results") or payload.get("data") or []):
            index = int(result.get("index", fallback))
            if 0 <= index < len(values):
                values[index] = max(0.0, min(1.0, float(
                    result.get("relevance_score", result.get("score", 0.0))
                )))
    except (OSError, TimeoutError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return rows, True, f"{type(exc).__name__}: {str(exc)[:200]}"
    eligible = float(os.getenv("QUESTION_RERANK_ELIGIBLE_THRESHOLD", "0.65"))
    reject = float(os.getenv("QUESTION_RERANK_REJECT_THRESHOLD", "0.30"))
    ranked = sorted(range(len(rows)), key=lambda index: (-values[index], index))
    semantic_ranks = {index: rank for rank, index in enumerate(ranked, 1)}
    enriched: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        score = values[index]
        status = (
            "shadow" if mode in {"shadow", "sort"}
            else "eligible" if score >= eligible
            else "rejected" if score < reject
            else "uncertain"
        )
        enriched.append({
            **row,
            "semantic_score": score,
            "semantic_rank": semantic_ranks[index],
            "semantic_status": status,
            "rerank_model": model,
        })
    if mode in {"sort", "gate"}:
        enriched.sort(key=lambda row: (
            row.get("semantic_status") == "rejected",
            -float(row.get("semantic_score") or 0.0),
            -float(row.get("fusion_score") or 0.0),
            question_id(row),
        ))
    if mode == "gate":
        enriched = [row for row in enriched if row.get("semantic_status") != "rejected"]
    return enriched, False, None


def hybrid_rank_questions(
    candidates: Iterable[Mapping[str, Any]],
    *,
    query: str = "",
    kp_ids: Iterable[str] = (),
    vector_hits: Sequence[tuple[str, float]] = (),
    limit: int = 20,
    rerank_mode: RerankMode | None = None,
) -> list[dict[str, Any]]:
    """Rank complete question rows using independent recall channels."""

    unique: dict[str, dict[str, Any]] = {}
    for raw in candidates:
        row = dict(raw)
        qid = question_id(row)
        if qid and qid not in unique:
            unique[qid] = row
    targets = {_text(value) for value in kp_ids if _text(value)}
    bridge = [
        (qid, len(targets.intersection(question_kp_ids(row))) / max(1, len(targets)))
        for qid, row in unique.items()
        if targets.intersection(question_kp_ids(row))
    ]
    bridge.sort(key=lambda pair: (-pair[1], pair[0]))
    bm25: list[tuple[str, float]] = []
    query_tokens = tokenize(query)
    if query_tokens and unique:
        ids = list(unique)
        values = BM25([tokenize(searchable_text(unique[qid])) for qid in ids]).scores(query_tokens)
        maximum = max(values, default=0.0)
        bm25 = sorted(
            ((qid, score / maximum) for qid, score in zip(ids, values) if score > 0 and maximum > 0),
            key=lambda pair: (-pair[1], pair[0]),
        )
    valid_vector = [(qid, float(score)) for qid, score in vector_hits if qid in unique]
    fusion = reciprocal_rank_fusion({"bridge": bridge, "bm25": bm25, "vector": valid_vector})
    rows: list[dict[str, Any]] = []
    for qid, result in fusion.items():
        row = unique[qid]
        channels = list(result.channel_ranks)
        rows.append({
            **row,
            # ``score`` remains the legacy display score when an adapter has
            # one; the comparable cross-channel score is ``fusion_score``.
            "score": float(row.get("score", result.score) or 0.0),
            "fusion_score": result.score,
            "fusion_strategy": "rrf_v1",
            "channels": _list(row.get("channels")) or channels,
            "retrieval_channels": list(dict.fromkeys([*_list(row.get("channels")), *channels])),
            "channel_scores": result.channel_scores,
            "channel_ranks": result.channel_ranks,
        })
    # Blank query/KP calls are deterministic list operations, not semantic search.
    if not fusion:
        rows = [{**row, "score": 0.0, "fusion_score": 0.0, "fusion_strategy": "rrf_v1", "channels": _list(row.get("channels")), "retrieval_channels": _list(row.get("channels")), "channel_scores": {}, "channel_ranks": {}} for row in unique.values()]
    rows.sort(key=lambda row: (-float(row.get("fusion_score") or 0.0), question_id(row)))
    mode = rerank_mode or os.getenv("QUESTION_RERANK_MODE", "disabled").strip().lower()
    if mode not in {"disabled", "shadow", "sort", "gate"}:
        mode = "disabled"
    top_n = max(1, int(os.getenv("QUESTION_RERANK_TOP_N", "30")))
    reranked, degraded, error = _rerank(query, rows[:top_n], mode=mode)  # type: ignore[arg-type]
    tail_ids = {question_id(row) for row in reranked}
    result = [*reranked, *(row for row in rows[top_n:] if question_id(row) not in tail_ids)]
    if degraded:
        result = [{**row, "rerank_degraded": True, "rerank_error": error} for row in result]
    return result[: max(1, min(500, int(limit)))]

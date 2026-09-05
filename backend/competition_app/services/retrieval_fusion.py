from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class FusionResult:
    score: float
    channel_ranks: dict[str, int]
    channel_scores: dict[str, float]
    legacy_max_score: float


def reciprocal_rank_fusion(
    ranked_channels: Mapping[str, Sequence[tuple[str, float]]],
    *,
    rank_constant: int = 60,
    weights: Mapping[str, float] | None = None,
) -> dict[str, FusionResult]:
    """Fuse heterogeneous retrieval lists by rank instead of raw score.

    Raw channel scores are retained for diagnostics, but never compared across
    channels. Returned scores are normalized by the maximum possible weighted
    RRF score so they remain bounded in ``[0, 1]`` for existing contracts.
    """

    if rank_constant < 1:
        raise ValueError("rank_constant must be positive")
    configured_weights = {str(key): float(value) for key, value in (weights or {}).items()}
    if any(value < 0 for value in configured_weights.values()):
        raise ValueError("RRF channel weights must be non-negative")

    ranks_by_question: dict[str, dict[str, int]] = {}
    scores_by_question: dict[str, dict[str, float]] = {}
    for channel, rows in ranked_channels.items():
        seen: set[str] = set()
        rank = 0
        for question_id, raw_score in rows:
            normalized_id = str(question_id)
            if not normalized_id or normalized_id in seen:
                continue
            seen.add(normalized_id)
            rank += 1
            ranks_by_question.setdefault(normalized_id, {})[channel] = rank
            scores_by_question.setdefault(normalized_id, {})[channel] = float(raw_score)

    active_channels = [
        channel
        for channel, rows in ranked_channels.items()
        if rows and configured_weights.get(channel, 1.0) > 0
    ]
    maximum = sum(
        configured_weights.get(channel, 1.0) / (rank_constant + 1)
        for channel in active_channels
    )
    results: dict[str, FusionResult] = {}
    for question_id, channel_ranks in ranks_by_question.items():
        raw_rrf = sum(
            configured_weights.get(channel, 1.0) / (rank_constant + rank)
            for channel, rank in channel_ranks.items()
        )
        channel_scores = scores_by_question.get(question_id, {})
        results[question_id] = FusionResult(
            score=raw_rrf / maximum if maximum else 0.0,
            channel_ranks=dict(channel_ranks),
            channel_scores=dict(channel_scores),
            legacy_max_score=max(channel_scores.values(), default=0.0),
        )
    return results

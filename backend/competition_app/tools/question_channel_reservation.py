from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from competition_app.contracts.knowledge import QuestionDetail


T = TypeVar("T")

_REQUIRED_CHANNEL_GROUPS: tuple[tuple[str, ...], ...] = (
    ("bm25",),
    ("vector", "runtime_vector"),
)


def _reserve_ranked_items(
    items: Sequence[T],
    *,
    limit: int,
    channels_of: Callable[[T], Sequence[str]],
) -> list[T]:
    """Keep channel diversity without changing the relative ranking order."""

    if limit < 1:
        return []
    ranked = list(items)
    required_indexes: list[int] = []
    for channel_group in _REQUIRED_CHANNEL_GROUPS:
        candidate_index = next(
            (
                index
                for index, item in enumerate(ranked)
                if any(channel in channels_of(item) for channel in channel_group)
            ),
            None,
        )
        if candidate_index is not None and candidate_index not in required_indexes:
            required_indexes.append(candidate_index)

    selected_indexes = set(required_indexes[:limit])
    for index in range(len(ranked)):
        if len(selected_indexes) >= limit:
            break
        selected_indexes.add(index)
    return [
        item
        for index, item in enumerate(ranked)
        if index in selected_indexes
    ][:limit]


def reserve_question_details(
    items: Sequence[QuestionDetail],
    *,
    limit: int,
) -> list[QuestionDetail]:
    return _reserve_ranked_items(
        items,
        limit=limit,
        channels_of=lambda item: item.retrieval.channels,
    )


def reserve_raw_question_items(
    items: Sequence[dict],
    *,
    limit: int,
) -> list[dict]:
    return _reserve_ranked_items(
        items,
        limit=limit,
        channels_of=lambda item: tuple(
            str(channel)
            for channel in (item.get("retrieval") or {}).get("channels") or []
        ),
    )

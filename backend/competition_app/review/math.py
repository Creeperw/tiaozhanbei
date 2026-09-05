from __future__ import annotations

import math
from datetime import datetime, timedelta

from competition_app.contracts.review import ReviewFormulaPolicy


REVIEW_INTERVALS: tuple[timedelta, ...] = (
    timedelta(0),
    timedelta(minutes=20),
    timedelta(hours=1),
    timedelta(hours=9),
    timedelta(days=1),
    timedelta(days=2),
    timedelta(days=6),
    timedelta(days=31),
)


def _require_compatible_datetimes(now: datetime, timestamp: datetime, field_name: str) -> None:
    if (now.tzinfo is None) != (timestamp.tzinfo is None):
        raise ValueError(f"now and {field_name} must use compatible timezone awareness")


def retention_estimate(
    now: datetime,
    last_review_at: datetime,
    stability_seconds: float,
) -> float:
    if stability_seconds <= 0:
        raise ValueError("stability_seconds must be positive")
    _require_compatible_datetimes(now, last_review_at, "last_review_at")
    elapsed_seconds = max(0.0, (now - last_review_at).total_seconds())
    return math.exp(-elapsed_seconds / stability_seconds)


def is_due(now: datetime, next_review_at: datetime | None) -> bool:
    if next_review_at is None:
        raise ValueError("next_review_at is required for an existing review state")
    _require_compatible_datetimes(now, next_review_at, "next_review_at")
    return now >= next_review_at


def predict_mastery_retention(
    mastery: float,
    forgetting_coefficient: float,
    elapsed_days: float,
) -> float:
    """Predict retention from an authoritative mastery snapshot and per-day lambda."""
    if not 0.0 <= mastery <= 1.0:
        raise ValueError("mastery must be between 0 and 1")
    if forgetting_coefficient <= 0.0:
        raise ValueError("forgetting_coefficient must be positive")
    retention = mastery * math.exp(-forgetting_coefficient * max(0.0, elapsed_days))
    return max(0.0, min(1.0, retention))


def compute_next_review_interval_minutes(
    mastery: float,
    forgetting_coefficient: float,
    policy: ReviewFormulaPolicy,
) -> int:
    """Calculate the minute when mastery is expected to cross the retention threshold."""
    if not 0.0 <= mastery <= 1.0:
        raise ValueError("mastery must be between 0 and 1")
    if forgetting_coefficient <= 0.0:
        raise ValueError("forgetting_coefficient must be positive")
    if mastery <= policy.min_retention_threshold:
        return policy.min_review_interval_minutes
    days = -math.log(policy.min_retention_threshold / mastery) / forgetting_coefficient
    minutes = math.ceil(days * 24 * 60)
    return max(
        policy.min_review_interval_minutes,
        min(policy.max_review_interval_minutes, minutes),
    )


def compute_urgency(retention: float, threshold: float) -> float:
    if not 0.0 <= retention <= 1.0:
        raise ValueError("retention must be between 0 and 1")
    if not 0.0 < threshold < 1.0:
        raise ValueError("threshold must be between 0 and 1")
    if retention <= threshold:
        return 1.0
    return max(0.0, min(1.0, (1.0 - retention) / (1.0 - threshold)))


def compute_priority(
    *,
    due: bool,
    urgency: float,
    policy: ReviewFormulaPolicy,
) -> float:
    if not 0.0 <= urgency <= 1.0:
        raise ValueError("urgency must be between 0 and 1")
    score = policy.due_weight * (1.0 if due else 0.0) + policy.urgency_weight * urgency
    return max(0.0, min(1.0, score))

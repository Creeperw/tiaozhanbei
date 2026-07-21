from datetime import datetime, timedelta, timezone

import pytest

from competition_app.contracts.review import ReviewFormulaPolicy
from competition_app.review.math import (
    compute_next_review_interval_minutes,
    compute_priority,
    compute_urgency,
    is_due,
    predict_mastery_retention,
    retention_estimate,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def test_retention_uses_elapsed_time_since_last_review() -> None:
    retention = retention_estimate(
        now=NOW,
        last_review_at=NOW - timedelta(days=1),
        stability_seconds=86_400,
    )

    assert retention == pytest.approx(0.367879, rel=1e-5)


def test_retention_rejects_non_positive_stability() -> None:
    with pytest.raises(ValueError, match="stability_seconds"):
        retention_estimate(NOW, NOW - timedelta(hours=1), 0)


def test_due_status_requires_a_valid_timestamp() -> None:
    assert is_due(NOW, NOW - timedelta(seconds=1)) is True
    assert is_due(NOW, NOW + timedelta(seconds=1)) is False
    with pytest.raises(ValueError, match="next_review_at"):
        is_due(NOW, None)


def test_mastery_retention_uses_probability_and_per_day_lambda() -> None:
    assert predict_mastery_retention(0.8, 0.08, 3) == pytest.approx(0.6293, rel=1e-4)


def test_minute_interval_is_immediate_below_threshold() -> None:
    policy = ReviewFormulaPolicy(min_review_interval_minutes=1)
    assert compute_next_review_interval_minutes(0.7, 0.08, policy) == 1
    assert compute_next_review_interval_minutes(0.8, 0.08, policy) == 2404


def test_urgency_and_priority_are_bounded() -> None:
    policy = ReviewFormulaPolicy()
    urgency = compute_urgency(0.6, policy.min_retention_threshold)
    assert urgency == 1.0
    assert compute_priority(due=True, urgency=urgency, policy=policy) == 1.0

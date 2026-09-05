from competition_app.services.learning_monitoring import LearningMonitoringService


def test_empty_monitoring_context_is_not_reported_as_healthy() -> None:
    result = LearningMonitoringService().build_snapshot("U1", {})

    assert result.evidence_status == "insufficient"
    assert result.metrics["task_completion_rate"] is None
    assert result.metrics["question_accuracy"] is None
    assert "no_observed_learning_behavior" in result.reason_codes


def test_monitoring_snapshot_exposes_real_sample_counts() -> None:
    result = LearningMonitoringService().build_snapshot(
        "U1",
        {
            "learning_profile": {
                "question_accuracy": 0.5,
                "sample_counts": {"question_attempts": 2, "mastery_records": 1},
                "behavior_metrics": {
                    "task_completion_rate": 0.75,
                    "sample_counts": {"activities_current_window": 4},
                },
            }
        },
    )

    assert result.evidence_status == "sufficient"
    assert result.sample_counts.activities == 4
    assert result.metrics["task_completion_rate"] == 0.75
    assert result.metrics["question_accuracy"] == 0.5


def test_monitoring_snapshot_prefers_canonical_audited_metrics() -> None:
    result = LearningMonitoringService().build_snapshot(
        "U1",
        {
            "monitoring_metrics": {
                "task_completion_rate": 0.25,
                "question_accuracy": 0.8,
                "review_stability": None,
                "retry_count": 2,
                "sample_counts": {
                    "activities": 6,
                    "question_attempts": 5,
                    "mastery_records": 3,
                },
            },
            "learning_profile": {
                "question_accuracy": 0.1,
                "review_stability": 0.9,
                "behavior_metrics": {"task_completion_rate": 1.0},
            },
        },
    )

    assert result.sample_counts.activities == 6
    assert result.sample_counts.question_attempts == 5
    assert result.metrics["task_completion_rate"] == 0.25
    assert result.metrics["question_accuracy"] == 0.8
    assert result.metrics["review_stability"] is None
    assert result.metrics["retry_count"] == 2

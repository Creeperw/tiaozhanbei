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

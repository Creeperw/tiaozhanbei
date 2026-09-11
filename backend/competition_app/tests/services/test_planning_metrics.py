from copy import deepcopy
from datetime import datetime, timezone

import pytest

from competition_app.services.planning_metrics import (
    build_planning_metric_evidence,
    planning_behavior_summary,
    planning_model_context,
)


def behavior():
    now = datetime.now(timezone.utc).isoformat()
    return {
        "system_data": {
            "question_accuracy": {"value": 0.95},
            "review_stability": {"value": 0.9},
            "time_data": {"active_days": {"value": 4}},
            "daily_atomic_task_completion_rate": {
                "value": 1.0, "available": True, "window_start": now, "window_end": now,
            },
            "behavior_window": {
                "retry_count": 7, "focus_time_change": -0.2,
                "metric_availability": {"retry_count": False},
            },
        },
        "learning_statistics": {
            "window": {"days": 7, "start_at": now, "end_at": now},
            "current_window": {
                "questions_completed": 16, "correct_answers": 15, "incorrect_answers": 1,
                "audited_question_items_completed": 0, "retry_count": 7,
                "attempts_by_type": {"practice": 16},
            },
        },
    }


def test_planning_metrics_use_window_statistics_without_mutating_shared_data():
    raw = behavior()
    before = deepcopy(raw)
    evidence = build_planning_metric_evidence(raw)
    metrics = evidence["metrics"]
    assert metrics["question_accuracy"]["value"] == 0.9375
    assert metrics["question_accuracy"]["sample_count"] == 16
    assert metrics["question_accuracy"]["window"]["days"] == 7
    assert metrics["review_stability"]["value"] is None
    assert metrics["review_stability"]["available"] is False
    # Unified retries include legacy rows; audited count alone is not the denominator.
    assert metrics["retry_count"]["value"] == 7
    assert metrics["retry_count"]["available"] is True
    summary = planning_behavior_summary(raw["system_data"], evidence)
    assert summary["question_accuracy"]["value"] == 0.9375
    assert summary["review_stability"]["value"] is None
    assert "retry_count" not in summary["behavior_window"]
    assert summary["behavior_window"]["focus_time_change"] == -0.2
    assert summary["time_data"] == raw["system_data"]["time_data"]
    assert raw == before


def test_zero_is_available_but_missing_denominator_is_not():
    raw = behavior()
    raw["learning_statistics"]["current_window"].update(correct_answers=0, retry_count=0)
    metrics = build_planning_metric_evidence(raw)["metrics"]
    assert metrics["question_accuracy"]["value"] == 0
    assert metrics["question_accuracy"]["available"] is True
    assert metrics["retry_count"]["value"] == 0
    raw["learning_statistics"]["current_window"] = {}
    metrics = build_planning_metric_evidence(raw)["metrics"]
    assert metrics["question_accuracy"]["value"] is None
    assert metrics["retry_count"]["value"] is None
    assert metrics["task_completion_rate"]["available"] is True


def test_missing_or_stale_window_does_not_borrow_profile_values():
    raw = behavior()
    raw["learning_statistics"]["window"]["end_at"] = "2000-01-01T00:00:00Z"
    metric = build_planning_metric_evidence(raw)["metrics"]["question_accuracy"]
    assert metric["available"] is False
    assert metric["value"] is None
    raw["learning_statistics"] = {}
    assert build_planning_metric_evidence(raw)["metrics"]["question_accuracy"]["available"] is False


def test_missing_planning_contract_preserves_legacy_summary():
    raw = behavior()["system_data"]
    assert planning_behavior_summary(raw, None) == raw


@pytest.mark.parametrize("task_type,scope", [
    ("learning_plan", "daily_task"), ("learning_plan", "unspecified"),
    ("paper", "long_term"), ("knowledge_explanation", "short_term"),
    ("learner_data_query", "long_term"), ("case", "short_term"),
])
def test_other_tasks_and_daily_planning_keep_exact_original_context(task_type, scope):
    raw = behavior()
    context = {
        "task_type": task_type, "plan_scope": scope, "system_data": raw["system_data"],
        "planning_metric_evidence": build_planning_metric_evidence(raw),
    }
    before = deepcopy(context)
    assert planning_model_context(context) is context
    assert context == before


def test_local_model_portrait_removes_only_conflicting_metrics():
    raw = behavior()
    evidence = build_planning_metric_evidence(raw)
    context = {
        "task_type": "learning_plan", "plan_scope": "long_term",
        "system_data": raw["system_data"], "planning_metric_evidence": evidence,
        "learning_profile": {
            "question_accuracy": 0.95, "review_stability": 0.9,
            "current_status": {"status_code": "T1"}, "mastery_by_kp": {"KP1": 0.8},
            "behavior_metrics": raw["system_data"]["behavior_window"],
        },
        "learning_monitoring": {"metrics": {"question_accuracy": 0.95}},
        "available_minutes": 90, "current_long_term_plan": {"version": 2},
    }
    before = deepcopy(context)
    projected = planning_model_context(context)
    profile = projected["learning_profile"]
    assert "question_accuracy" not in profile
    assert "review_stability" not in profile
    assert profile["mastery_by_kp"] == {"KP1": 0.8}
    assert profile["current_status"] == {"status_code": "T1"}
    assert profile["behavior_metrics"]["retry_count"]["value"] == 7
    assert "retry_count" not in profile["behavior_metrics"]["metric_availability"]
    assert projected["available_minutes"] == 90
    assert projected["current_long_term_plan"] == {"version": 2}
    assert context == before


def test_retry_population_includes_case_rows_and_does_not_invent_missing_samples():
    raw = behavior()
    outcomes = raw["learning_statistics"]["current_window"]
    outcomes.update(questions_completed=0, attempts_by_type={"case": 3}, retry_count=2)
    metric = build_planning_metric_evidence(raw)["metrics"]["retry_count"]
    assert metric["sample_count"] == 3
    assert metric["value"] == 2
    outcomes.pop("attempts_by_type")
    assert build_planning_metric_evidence(raw)["metrics"]["retry_count"]["value"] is None


def test_score_rate_and_other_monitoring_fields_are_not_lost():
    raw = behavior()
    raw["learning_statistics"]["current_window"].update(score_points=0, available_points=10, score_rate=0)
    raw["system_data"]["observed_metrics"] = {"question_accuracy": 0.95, "focus_minutes": 80}
    evidence = build_planning_metric_evidence(raw)
    assert evidence["metrics"]["question_score_rate"]["value"] == 0
    summary = planning_behavior_summary(raw["system_data"], evidence)
    assert summary["observed_metrics"] == {"focus_minutes": 80}
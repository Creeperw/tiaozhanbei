"""Read-only metric evidence for long/short planning, not shared monitoring."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from typing import Any


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if isfinite(value) and value >= 0 else None


def _freshness(window: dict[str, Any]) -> str:
    try:
        end = datetime.fromisoformat(str(window.get("end_at") or "").replace("Z", "+00:00"))
        start = datetime.fromisoformat(str(window.get("start_at") or "").replace("Z", "+00:00"))
        if end.tzinfo is None or start.tzinfo is None or start > end:
            return "unknown"
        days = _number(window.get("days"))
        age = (datetime.now(timezone.utc) - end).total_seconds() / 86400
        if age < 0 or not window.get("start_at"):
            return "unknown"
        return "fresh" if age <= max(1, days or 1) else "stale"
    except (ValueError, TypeError):
        return "unknown"


def build_planning_metric_evidence(behavior: dict[str, Any]) -> dict[str, Any]:
    """Use existing server statistics without queries, writes or legacy fallback.

    Unified correctness/retries include deduplicated legacy attempts. The
    audited-only count is not their denominator. Shared L3 policy is unchanged.
    """
    statistics = behavior.get("learning_statistics") or {}
    outcomes = statistics.get("current_window") or {}
    window = dict(statistics.get("window") or {})
    system = behavior.get("system_data") or {}
    completion = system.get("daily_atomic_task_completion_rate") or {}
    completion_window = {
        "start_at": completion.get("window_start"),
        "end_at": completion.get("window_end"),
    }
    try:
        start = datetime.fromisoformat(str(completion_window["start_at"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(completion_window["end_at"]).replace("Z", "+00:00"))
        completion_window["days"] = max(1, (end - start).total_seconds() / 86400)
    except (ValueError, TypeError):
        pass

    def metric(value, *, label, unit, source, definition, scope, available,
               sample_count=None, reason="该统计窗口缺少有效样本或分母。"):
        freshness = _freshness(scope)
        usable = available and _number(value) is not None and freshness == "fresh"
        return {
            "label": label, "value": value if usable else None,
            "available": bool(usable), "unit": unit, "window": dict(scope),
            "sample_count": sample_count, "source": source, "definition": definition,
            "freshness_status": freshness,
            "unavailable_reason": None if usable else (
                "统计窗口过期或不明确，不作为当前数值。" if freshness != "fresh" else reason
            ),
        }

    correct = _number(outcomes.get("correct_answers"))
    incorrect = _number(outcomes.get("incorrect_answers"))
    denominator = correct + incorrect if correct is not None and incorrect is not None else 0
    retry = _number(outcomes.get("retry_count"))
    attempt_counts = outcomes.get("attempts_by_type") or {}
    retry_samples = (
        sum(value for value in attempt_counts.values() if _number(value) is not None)
        if attempt_counts else None
    )
    points = _number(outcomes.get("score_points"))
    max_points = _number(outcomes.get("available_points"))
    completion_value = _number(completion.get("value"))
    return {
        "schema_version": "1.0", "source": "authorized_planning_metrics",
        "calculated_at": statistics.get("calculated_at"),
        "population": {
            "attempts_by_source": deepcopy(outcomes.get("attempts_by_source") or {}),
            "attempts_by_type": deepcopy(attempt_counts),
            "correct_answers": correct, "incorrect_answers": incorrect,
            "score_points": points, "available_points": max_points,
            "counting_policy": deepcopy(statistics.get("counting_policy") or {}),
        },
        "metrics": {
            "question_accuracy": metric(
                correct / denominator if denominator else None,
                label="窗口内作答正确率", unit="ratio", source="learning_statistics.current_window",
                definition="去重统一题项中正确数/(正确数+错误数)，含正式、历史及试卷来源；不含正误未知题项。",
                scope=window, available=bool(denominator), sample_count=denominator,
            ),
            "retry_count": metric(
                retry, label="窗口内同题版本重复作答次数", unit="count",
                source="learning_statistics.current_window",
                definition="统一统计按非空题目版本标识计数，累加每个版本超过首次的作答次数；不是模型调用重试或错误次数。",
                scope=window, available=bool(retry_samples), sample_count=retry_samples,
            ),
            "question_score_rate": metric(
                outcomes.get("score_rate"), label="窗口内题项得分率", unit="ratio",
                source="learning_statistics.current_window",
                definition="统一统计中有评分的非病例题项得分总和/对应满分总和，不等于作答正确率。",
                scope=window, available=points is not None and bool(max_points),
            ),
            "review_stability": metric(
                None, label="复习稳定性", unit="ratio", source="learning_statistics",
                definition="当前正式统计尚未提供复习稳定性；掌握度达标知识点占比不等于复习稳定性。",
                scope=window, available=False, reason="尚无正式复习稳定性指标。",
            ),
            "task_completion_rate": metric(
                completion_value, label="窗口内每日原子任务完成率", unit="ratio",
                source="system_data.daily_atomic_task_completion_rate",
                definition="采用原子任务完成统计的窗口与有效分母，不代表教材完成率或掌握率。",
                scope=completion_window,
                available=completion.get("available") is True and completion_value is not None and completion_value <= 1,
            ),
        },
    }


def planning_behavior_summary(summary: dict[str, Any], evidence: Any) -> dict[str, Any]:
    """Replace fixed metric fields only; never classify natural language."""
    result = deepcopy(summary)
    if not isinstance(evidence, dict) or evidence.get("source") != "authorized_planning_metrics":
        return result
    metrics = evidence.get("metrics") or {}
    for key in metrics:
        result.pop(key, None)
    observed = result.get("observed_metrics")
    if isinstance(observed, dict):
        for key in metrics:
            observed.pop(key, None)
        if not observed:
            result.pop("observed_metrics", None)
    result.pop("daily_atomic_task_completion_rate", None)
    availability = result.get("metric_availability")
    if isinstance(availability, dict):
        for key in metrics:
            availability.pop(key, None)
    behavior = result.get("behavior_window")
    if isinstance(behavior, dict):
        for key in metrics:
            behavior.pop(key, None)
        availability = behavior.get("metric_availability")
        if isinstance(availability, dict):
            for key in metrics:
                availability.pop(key, None)
    result.update(deepcopy(metrics))
    return result


def planning_model_context(context: dict[str, Any]) -> dict[str, Any]:
    """A local model view only; do not mutate shared state or non-planning tasks."""
    evidence = context.get("planning_metric_evidence")
    if (
        context.get("task_type", "learning_plan") != "learning_plan"
        or context.get("plan_scope") not in {"long_term", "short_term"}
        or not isinstance(evidence, dict)
        or evidence.get("source") != "authorized_planning_metrics"
    ):
        return context
    result = dict(context)
    profile = deepcopy(context.get("learning_profile") or {})
    for key in evidence.get("metrics") or {}:
        profile.pop(key, None)
    if isinstance(profile.get("behavior_metrics"), dict):
        profile["behavior_metrics"] = planning_behavior_summary(profile["behavior_metrics"], evidence)
    result["learning_profile"] = profile
    result["system_data"] = planning_behavior_summary(context.get("system_data") or {}, evidence)
    monitoring = deepcopy(context.get("learning_monitoring") or {})
    if isinstance(monitoring.get("behavior_summary"), dict):
        monitoring["behavior_summary"] = planning_behavior_summary(monitoring["behavior_summary"], evidence)
    result["learning_monitoring"] = monitoring
    return result
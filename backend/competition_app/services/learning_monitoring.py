from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from competition_app.contracts.learning_monitoring import (
    LearningMonitoringSnapshot,
    MonitoringSampleCounts,
)


class LearningMonitoringService:
    """Normalize persisted behavior into evidence-aware Diagnosis input."""

    def build_snapshot(
        self,
        learner_id: str,
        context: dict[str, Any] | None,
        *,
        window_days: int = 7,
    ) -> LearningMonitoringSnapshot:
        raw = context or {}
        profile = raw.get("learning_profile") or {}
        has_canonical_metrics = isinstance(raw.get("monitoring_metrics"), dict)
        canonical = raw.get("monitoring_metrics") or {}
        behavior = (profile.get("behavior_metrics") or {}) if isinstance(profile, dict) else {}
        behavior_counts = behavior.get("sample_counts") or {}
        profile_counts = profile.get("sample_counts") or {}
        canonical_counts = (
            canonical.get("sample_counts")
            if isinstance(canonical, dict)
            else {}
        ) or {}
        attempts = raw.get("question_attempt") or []
        mastery = raw.get("mastery") or []
        counts = (
            MonitoringSampleCounts(
                activities=int(canonical_counts.get("activities") or 0),
                question_attempts=int(
                    canonical_counts.get("question_attempts") or 0
                ),
                mastery_records=int(
                    canonical_counts.get("mastery_records") or 0
                ),
            )
            if has_canonical_metrics
            else MonitoringSampleCounts(
                activities=int(
                    behavior_counts.get("activities_current_window") or 0
                ),
                question_attempts=max(
                    len(attempts),
                    int(
                        behavior_counts.get(
                            "question_attempts_current_window"
                        )
                        or 0
                    ),
                    int(profile_counts.get("question_attempts") or 0),
                ),
                mastery_records=max(
                    len(mastery),
                    int(profile_counts.get("mastery_records") or 0),
                ),
            )
        )
        total = counts.activities + counts.question_attempts + counts.mastery_records
        evidence_status = "sufficient" if total >= 3 else "limited" if total else "insufficient"
        calculated_at = self._datetime(raw.get("calculated_at"))
        freshness = "unknown"
        if calculated_at is not None:
            age_days = (datetime.now(timezone.utc) - calculated_at).total_seconds() / 86_400
            freshness = "fresh" if age_days <= max(1, window_days) else "stale"
        canonical_task_rate = self._number(
            canonical.get("task_completion_rate")
        )
        canonical_accuracy = self._number(canonical.get("question_accuracy"))
        canonical_review_stability = self._number(
            canonical.get("review_stability")
        )
        return LearningMonitoringSnapshot(
            learner_id=learner_id,
            window_days=window_days,
            evidence_status=evidence_status,
            freshness_status=freshness,
            calculated_at=calculated_at,
            source=str(raw.get("source") or "learning_monitoring"),
            sample_counts=counts,
            metrics={
                "task_completion_rate": (
                    canonical_task_rate
                    if has_canonical_metrics
                    else self._number(behavior.get("task_completion_rate"))
                    if counts.activities
                    else None
                ),
                "question_accuracy": (
                    canonical_accuracy
                    if has_canonical_metrics
                    else self._number(profile.get("question_accuracy"))
                    if counts.question_attempts
                    else None
                ),
                "review_stability": (
                    canonical_review_stability
                    if has_canonical_metrics
                    else self._number(profile.get("review_stability"))
                    if counts.mastery_records
                    else None
                ),
                "retry_count": int(
                    (
                        canonical.get("retry_count")
                        if isinstance(canonical, dict)
                        else None
                    )
                    or behavior.get("retry_count")
                    or 0
                ),
            },
            reason_codes=(
                ["no_observed_learning_behavior"]
                if evidence_status == "insufficient"
                else ["small_behavior_sample"]
                if evidence_status == "limited"
                else []
            ) + (["monitoring_snapshot_stale"] if freshness == "stale" else []),
        )

    @staticmethod
    def _number(value: Any) -> float | None:
        if isinstance(value, dict):
            value = value.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    @staticmethod
    def _datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

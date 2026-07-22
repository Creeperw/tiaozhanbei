from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from competition_app.contracts.base import ContractModel


class MonitoringSampleCounts(ContractModel):
    activities: int = Field(default=0, ge=0)
    question_attempts: int = Field(default=0, ge=0)
    mastery_records: int = Field(default=0, ge=0)


class LearningMonitoringSnapshot(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    learner_id: str
    window_days: int = Field(default=7, ge=1, le=90)
    evidence_status: Literal["sufficient", "limited", "insufficient", "unavailable"]
    freshness_status: Literal["fresh", "stale", "unknown"]
    calculated_at: datetime | None = None
    source: str = "learning_monitoring"
    sample_counts: MonitoringSampleCounts
    metrics: dict[str, float | int | None] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)

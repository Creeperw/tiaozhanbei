from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from competition_app.contracts.base import ContractModel


class PreferenceSample(ContractModel):
    sample_id: str
    source_type: Literal["user_feedback", "audit_repair", "human_review", "evaluation"]
    source_id: str
    task_type: str
    prompt: str = Field(min_length=1, max_length=12000)
    chosen: str = Field(min_length=1, max_length=30000)
    rejected: str = Field(min_length=1, max_length=30000)
    rationale: str = Field(default="", max_length=3000)
    status: Literal["pending", "approved", "rejected"] = "pending"
    content_hash: str
    contains_sensitive_data: bool = False
    reviewed_by: str | None = None
    created_at: datetime | None = None
    reviewed_at: datetime | None = None

    @model_validator(mode="after")
    def chosen_must_differ(self):
        if self.chosen.strip() == self.rejected.strip():
            raise ValueError("chosen and rejected must differ")
        return self


class PreferenceSampleCreate(ContractModel):
    source_type: Literal["user_feedback", "audit_repair", "human_review", "evaluation"]
    source_id: str
    task_type: str
    prompt: str = Field(min_length=1, max_length=12000)
    chosen: str = Field(min_length=1, max_length=30000)
    rejected: str = Field(min_length=1, max_length=30000)
    rationale: str = Field(default="", max_length=3000)


class PreferenceSampleReview(ContractModel):
    status: Literal["approved", "rejected"]


class PreferenceDatasetCreate(ContractModel):
    name: str = Field(min_length=1, max_length=200)
    sample_ids: list[str] = Field(min_length=1, max_length=5000)


class PreferenceDataset(ContractModel):
    dataset_id: str
    version: int = Field(ge=1)
    name: str
    status: Literal["draft", "frozen"] = "draft"
    sample_ids: list[str] = Field(default_factory=list)
    sample_count: int = Field(default=0, ge=0)
    sha256: str = ""
    relative_path: str = ""
    data_card_path: str = ""
    created_by: str
    created_at: datetime | None = None
    frozen_at: datetime | None = None


class TrainingJob(ContractModel):
    job_id: str
    dataset_id: str
    dataset_sha256: str
    backend: Literal["dry_run", "trl_dpo"] = "dry_run"
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "blocked"] = "queued"
    base_model: str
    config: dict[str, object] = Field(default_factory=dict)
    log_path: str = ""
    artifact_path: str = ""
    error_summary: str = ""
    created_by: str
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TrainingJobCreate(ContractModel):
    dataset_id: str
    backend: Literal["dry_run", "trl_dpo"] = "dry_run"
    base_model: str = Field(min_length=1, max_length=300)
    epochs: int = Field(default=1, ge=1, le=10)
    learning_rate: float = Field(default=5e-6, gt=0, le=0.01)
    batch_size: int = Field(default=1, ge=1, le=64)
    lora_rank: int = Field(default=8, ge=1, le=256)

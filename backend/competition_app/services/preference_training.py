from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from competition_app.contracts.preference_training import (
    PreferenceDataset,
    PreferenceDatasetCreate,
    PreferenceSample,
    PreferenceSampleCreate,
    TrainingJob,
    TrainingJobCreate,
)
from competition_app.repositories.preference_training import PreferenceTrainingRepository
from competition_app.services.feedback_governance import sanitize_feedback_text


_SENSITIVE_RE = re.compile(
    r"(?i)(?:\b1[3-9]\d{9}\b|\b\d{17}[0-9x]\b|api[_ -]?key\s*[:=]|authorization\s*:)"
)


class PreferenceTrainingService:
    """Audited dataset freezer and restricted one-click training job facade."""

    def __init__(
        self,
        repository: PreferenceTrainingRepository,
        data_root: Path,
        *,
        enabled: bool = False,
        allow_trl_dpo: bool = False,
    ) -> None:
        self.repository = repository
        self.data_root = data_root.resolve()
        self.enabled = enabled
        self.allow_trl_dpo = allow_trl_dpo

    def create_sample(self, request: PreferenceSampleCreate) -> PreferenceSample:
        self._require_enabled()
        prompt = sanitize_feedback_text(request.prompt, max_chars=12000)
        chosen = sanitize_feedback_text(request.chosen, max_chars=30000)
        rejected = sanitize_feedback_text(request.rejected, max_chars=30000)
        if not prompt or not chosen or not rejected or chosen == rejected:
            raise ValueError("preference sample requires distinct non-empty content")
        canonical = json.dumps(
            {"prompt": prompt, "chosen": chosen, "rejected": rejected},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        sensitive = bool(_SENSITIVE_RE.search(canonical))
        return self.repository.save_sample(PreferenceSample(
            sample_id=f"PS_{uuid4().hex}",
            source_type=request.source_type,
            source_id=request.source_id,
            task_type=request.task_type,
            prompt=prompt,
            chosen=chosen,
            rejected=rejected,
            rationale=sanitize_feedback_text(request.rationale, max_chars=3000),
            content_hash=content_hash,
            contains_sensitive_data=sensitive,
        ))

    def review_sample(self, sample_id: str, *, status: str, reviewer_id: str) -> PreferenceSample:
        self._require_enabled()
        sample = self.repository.get_sample(sample_id)
        if sample is None:
            raise KeyError(sample_id)
        if status == "approved" and sample.contains_sensitive_data:
            raise ValueError("sensitive sample must be de-identified before approval")
        if status not in {"approved", "rejected"}:
            raise ValueError("invalid sample review status")
        return self.repository.save_sample(sample.model_copy(update={
            "status": status,
            "reviewed_by": reviewer_id,
            "reviewed_at": datetime.now(timezone.utc),
        }))

    def freeze_dataset(self, request: PreferenceDatasetCreate, *, creator_id: str) -> PreferenceDataset:
        self._require_enabled()
        samples: list[PreferenceSample] = []
        for sample_id in dict.fromkeys(request.sample_ids):
            sample = self.repository.get_sample(sample_id)
            if sample is None:
                raise KeyError(sample_id)
            if sample.status != "approved" or sample.contains_sensitive_data:
                raise ValueError("dataset may contain approved, de-identified samples only")
            samples.append(sample)
        dataset_id = f"PDS_{uuid4().hex}"
        version = 1
        relative_dir = Path("datasets") / dataset_id / f"v{version}"
        target_dir = self._safe_path(relative_dir)
        target_dir.mkdir(parents=True, exist_ok=False)
        records = [
            {
                "sample_id": item.sample_id,
                "source_type": item.source_type,
                "source_id": item.source_id,
                "task_type": item.task_type,
                "prompt": item.prompt,
                "chosen": item.chosen,
                "rejected": item.rejected,
                "rationale": item.rationale,
            }
            for item in sorted(samples, key=lambda row: row.sample_id)
        ]
        content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        data_file = target_dir / "train.jsonl"
        data_file.write_text(content, encoding="utf-8")
        card_file = target_dir / "DATA_CARD.md"
        card_file.write_text(
            "# 偏好数据集卡\n\n"
            f"- 名称：{request.name}\n- 版本：{version}\n- 样本数：{len(records)}\n"
            f"- SHA-256：`{digest}`\n- 创建人：{creator_id}\n"
            "- 边界：仅包含人工批准且通过敏感信息检查的偏好对；冻结后不可原地修改。\n",
            encoding="utf-8",
        )
        return self.repository.save_dataset(PreferenceDataset(
            dataset_id=dataset_id,
            version=version,
            name=request.name,
            status="frozen",
            sample_ids=[item.sample_id for item in samples],
            sample_count=len(samples),
            sha256=digest,
            relative_path=str(data_file.relative_to(self.data_root)),
            data_card_path=str(card_file.relative_to(self.data_root)),
            created_by=creator_id,
            frozen_at=datetime.now(timezone.utc),
        ))

    def create_job(self, request: TrainingJobCreate, *, creator_id: str) -> TrainingJob:
        self._require_enabled()
        dataset = self.repository.get_dataset(request.dataset_id)
        if dataset is None or dataset.status != "frozen" or not dataset.sha256:
            raise ValueError("training requires a frozen dataset")
        data_file = self._safe_path(Path(dataset.relative_path))
        if not data_file.is_file():
            raise ValueError("frozen dataset file is missing")
        actual_hash = hashlib.sha256(data_file.read_bytes()).hexdigest()
        if actual_hash != dataset.sha256:
            raise ValueError("frozen dataset integrity check failed")
        backend = request.backend
        status = "queued"
        error = ""
        if backend == "trl_dpo" and not self.allow_trl_dpo:
            status = "blocked"
            error = "TRL DPO backend is disabled by deployment policy"
        job_id = f"PTJ_{uuid4().hex}"
        relative_log = Path("jobs") / job_id / "job.log"
        log_file = self._safe_path(relative_log)
        log_file.parent.mkdir(parents=True, exist_ok=False)
        job = TrainingJob(
            job_id=job_id,
            dataset_id=dataset.dataset_id,
            dataset_sha256=dataset.sha256,
            backend=backend,
            status=status,
            base_model=request.base_model,
            config={
                "epochs": request.epochs,
                "learning_rate": request.learning_rate,
                "batch_size": request.batch_size,
                "lora_rank": request.lora_rank,
            },
            log_path=str(relative_log),
            error_summary=error,
            created_by=creator_id,
        )
        saved = self.repository.save_job(job)
        if backend == "dry_run" and status == "queued":
            now = datetime.now(timezone.utc)
            log_file.write_text(
                "Dry-run succeeded: dataset hash, sample schema and restricted configuration validated.\n"
                "No model weights were loaded or modified.\n",
                encoding="utf-8",
            )
            saved = self.repository.save_job(saved.model_copy(update={
                "status": "succeeded",
                "started_at": now,
                "finished_at": now,
            }))
        # The optional trl_dpo worker intentionally remains queued for a
        # separately deployed, allowlisted worker. The web process never runs
        # arbitrary shell commands or swaps the production model.
        return saved

    def _safe_path(self, relative: Path) -> Path:
        target = (self.data_root / relative).resolve()
        if target != self.data_root and self.data_root not in target.parents:
            raise ValueError("path escapes preference training data root")
        return target

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise RuntimeError("preference training platform is disabled")

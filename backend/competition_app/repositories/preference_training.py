from __future__ import annotations

import json
from datetime import datetime, timezone
from threading import RLock
from typing import Protocol

from sqlalchemy import Engine, text

from competition_app.contracts.preference_training import (
    PreferenceDataset,
    PreferenceSample,
    TrainingJob,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value, default):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


class PreferenceTrainingRepository(Protocol):
    def save_sample(self, sample: PreferenceSample) -> PreferenceSample: ...
    def get_sample(self, sample_id: str) -> PreferenceSample | None: ...
    def list_samples(self, *, status: str | None = None, limit: int = 200) -> list[PreferenceSample]: ...
    def save_dataset(self, dataset: PreferenceDataset) -> PreferenceDataset: ...
    def get_dataset(self, dataset_id: str, version: int | None = None) -> PreferenceDataset | None: ...
    def list_datasets(self, limit: int = 100) -> list[PreferenceDataset]: ...
    def save_job(self, job: TrainingJob) -> TrainingJob: ...
    def get_job(self, job_id: str) -> TrainingJob | None: ...
    def list_jobs(self, limit: int = 100) -> list[TrainingJob]: ...


class InMemoryPreferenceTrainingRepository:
    def __init__(self) -> None:
        self._samples: dict[str, PreferenceSample] = {}
        self._datasets: dict[tuple[str, int], PreferenceDataset] = {}
        self._jobs: dict[str, TrainingJob] = {}
        self._lock = RLock()

    def save_sample(self, sample: PreferenceSample) -> PreferenceSample:
        item = sample.model_copy(update={"created_at": sample.created_at or _now()})
        with self._lock:
            duplicate = next((row for row in self._samples.values() if row.content_hash == item.content_hash), None)
            if duplicate and duplicate.sample_id != item.sample_id:
                return duplicate
            self._samples[item.sample_id] = item
        return item

    def get_sample(self, sample_id: str) -> PreferenceSample | None:
        with self._lock:
            return self._samples.get(sample_id)

    def list_samples(self, *, status: str | None = None, limit: int = 200) -> list[PreferenceSample]:
        with self._lock:
            rows = list(self._samples.values())
        if status:
            rows = [row for row in rows if row.status == status]
        return sorted(rows, key=lambda row: row.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:limit]

    def save_dataset(self, dataset: PreferenceDataset) -> PreferenceDataset:
        item = dataset.model_copy(update={"created_at": dataset.created_at or _now()})
        with self._lock:
            self._datasets[(item.dataset_id, item.version)] = item
        return item

    def get_dataset(self, dataset_id: str, version: int | None = None) -> PreferenceDataset | None:
        with self._lock:
            rows = [row for (did, _), row in self._datasets.items() if did == dataset_id]
        if version is not None:
            return next((row for row in rows if row.version == version), None)
        return max(rows, key=lambda row: row.version) if rows else None

    def list_datasets(self, limit: int = 100) -> list[PreferenceDataset]:
        with self._lock:
            rows = list(self._datasets.values())
        return sorted(rows, key=lambda row: row.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:limit]

    def save_job(self, job: TrainingJob) -> TrainingJob:
        item = job.model_copy(update={"created_at": job.created_at or _now()})
        with self._lock:
            self._jobs[item.job_id] = item
        return item

    def get_job(self, job_id: str) -> TrainingJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 100) -> list[TrainingJob]:
        with self._lock:
            rows = list(self._jobs.values())
        return sorted(rows, key=lambda row: row.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:limit]


class SqlPreferenceTrainingRepository(InMemoryPreferenceTrainingRepository):
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def save_sample(self, sample: PreferenceSample) -> PreferenceSample:
        existing = self._one("SELECT * FROM preference_samples WHERE content_hash=:hash", {"hash": sample.content_hash})
        if existing and str(existing.get("sample_id")) != sample.sample_id:
            return PreferenceSample.model_validate(existing)
        with self.engine.begin() as connection:
            payload = {**sample.model_dump(mode="python", exclude={"created_at"}), "contains_sensitive_data": 1 if sample.contains_sensitive_data else 0}
            if existing:
                connection.execute(text(
                    "UPDATE preference_samples SET status=:status,contains_sensitive_data=:contains_sensitive_data,reviewed_by=:reviewed_by,reviewed_at=:reviewed_at,rationale=:rationale WHERE sample_id=:sample_id"
                ), payload)
            else:
                connection.execute(text(
                    "INSERT INTO preference_samples (sample_id,source_type,source_id,task_type,prompt,chosen,rejected,rationale,status,content_hash,contains_sensitive_data,reviewed_by,reviewed_at) VALUES (:sample_id,:source_type,:source_id,:task_type,:prompt,:chosen,:rejected,:rationale,:status,:content_hash,:contains_sensitive_data,:reviewed_by,:reviewed_at)"
                ), payload)
        return self.get_sample(sample.sample_id) or sample

    def get_sample(self, sample_id: str) -> PreferenceSample | None:
        row = self._one("SELECT * FROM preference_samples WHERE sample_id=:id", {"id": sample_id})
        if row:
            row["contains_sensitive_data"] = bool(row.get("contains_sensitive_data"))
            return PreferenceSample.model_validate(row)
        return None

    def list_samples(self, *, status: str | None = None, limit: int = 200) -> list[PreferenceSample]:
        where = " WHERE status=:status" if status else ""
        params = {"limit": limit, **({"status": status} if status else {})}
        rows = self._all(f"SELECT * FROM preference_samples{where} ORDER BY created_at DESC LIMIT :limit", params)
        for row in rows:
            row["contains_sensitive_data"] = bool(row.get("contains_sensitive_data"))
        return [PreferenceSample.model_validate(row) for row in rows]

    def save_dataset(self, dataset: PreferenceDataset) -> PreferenceDataset:
        payload = dataset.model_dump(mode="python", exclude={"created_at", "frozen_at"})
        payload["sample_ids"] = json.dumps(dataset.sample_ids, ensure_ascii=False)
        existing = self.get_dataset(dataset.dataset_id, dataset.version)
        with self.engine.begin() as connection:
            if existing:
                connection.execute(text(
                    "UPDATE preference_datasets SET name=:name,status=:status,sample_ids=:sample_ids,sample_count=:sample_count,sha256=:sha256,relative_path=:relative_path,data_card_path=:data_card_path,frozen_at=:frozen_at WHERE dataset_id=:dataset_id AND version=:version"
                ), {**payload, "frozen_at": dataset.frozen_at})
            else:
                connection.execute(text(
                    "INSERT INTO preference_datasets (dataset_id,version,name,status,sample_ids,sample_count,sha256,relative_path,data_card_path,created_by,frozen_at) VALUES (:dataset_id,:version,:name,:status,:sample_ids,:sample_count,:sha256,:relative_path,:data_card_path,:created_by,:frozen_at)"
                ), {**payload, "frozen_at": dataset.frozen_at})
        return self.get_dataset(dataset.dataset_id, dataset.version) or dataset

    def get_dataset(self, dataset_id: str, version: int | None = None) -> PreferenceDataset | None:
        if version is None:
            row = self._one("SELECT * FROM preference_datasets WHERE dataset_id=:id ORDER BY version DESC LIMIT 1", {"id": dataset_id})
        else:
            row = self._one("SELECT * FROM preference_datasets WHERE dataset_id=:id AND version=:version", {"id": dataset_id, "version": version})
        if not row:
            return None
        row["sample_ids"] = _json(row.get("sample_ids"), [])
        return PreferenceDataset.model_validate(row)

    def list_datasets(self, limit: int = 100) -> list[PreferenceDataset]:
        rows = self._all("SELECT * FROM preference_datasets ORDER BY created_at DESC LIMIT :limit", {"limit": limit})
        for row in rows:
            row["sample_ids"] = _json(row.get("sample_ids"), [])
        return [PreferenceDataset.model_validate(row) for row in rows]

    def save_job(self, job: TrainingJob) -> TrainingJob:
        payload = job.model_dump(mode="python", exclude={"created_at"})
        payload["config_json"] = json.dumps(job.config, ensure_ascii=False)
        existing = self.get_job(job.job_id)
        with self.engine.begin() as connection:
            if existing:
                connection.execute(text(
                    "UPDATE preference_training_jobs SET status=:status,config_json=:config_json,log_path=:log_path,artifact_path=:artifact_path,error_summary=:error_summary,started_at=:started_at,finished_at=:finished_at WHERE job_id=:job_id"
                ), payload)
            else:
                connection.execute(text(
                    "INSERT INTO preference_training_jobs (job_id,dataset_id,dataset_sha256,backend,status,base_model,config_json,log_path,artifact_path,error_summary,created_by,started_at,finished_at) VALUES (:job_id,:dataset_id,:dataset_sha256,:backend,:status,:base_model,:config_json,:log_path,:artifact_path,:error_summary,:created_by,:started_at,:finished_at)"
                ), payload)
        return self.get_job(job.job_id) or job

    def get_job(self, job_id: str) -> TrainingJob | None:
        row = self._one("SELECT * FROM preference_training_jobs WHERE job_id=:id", {"id": job_id})
        if not row:
            return None
        row["config"] = _json(row.pop("config_json", None), {})
        return TrainingJob.model_validate(row)

    def list_jobs(self, limit: int = 100) -> list[TrainingJob]:
        rows = self._all("SELECT * FROM preference_training_jobs ORDER BY created_at DESC LIMIT :limit", {"limit": limit})
        for row in rows:
            row["config"] = _json(row.pop("config_json", None), {})
        return [TrainingJob.model_validate(row) for row in rows]

    def _one(self, sql: str, params: dict) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(text(sql), params).mappings().first()
        return dict(row) if row else None

    def _all(self, sql: str, params: dict) -> list[dict]:
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(text(sql), params).mappings()]

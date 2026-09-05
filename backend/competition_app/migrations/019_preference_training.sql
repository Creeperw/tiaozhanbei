CREATE TABLE IF NOT EXISTS preference_samples (
    sample_id VARCHAR(128) PRIMARY KEY,
    source_type VARCHAR(32) NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    task_type VARCHAR(64) NOT NULL,
    prompt LONGTEXT NOT NULL,
    chosen LONGTEXT NOT NULL,
    rejected LONGTEXT NOT NULL,
    rationale TEXT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    content_hash VARCHAR(64) NOT NULL UNIQUE,
    contains_sensitive_data TINYINT(1) NOT NULL DEFAULT 0,
    reviewed_by VARCHAR(128) NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    reviewed_at TIMESTAMP(6) NULL,
    INDEX idx_preference_samples_status (status),
    INDEX idx_preference_samples_source (source_type, source_id)
);

CREATE TABLE IF NOT EXISTS preference_datasets (
    dataset_id VARCHAR(128) NOT NULL,
    version INT NOT NULL,
    name VARCHAR(200) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    sample_ids JSON NOT NULL,
    sample_count INT NOT NULL DEFAULT 0,
    sha256 VARCHAR(64) NOT NULL DEFAULT '',
    relative_path VARCHAR(512) NOT NULL DEFAULT '',
    data_card_path VARCHAR(512) NOT NULL DEFAULT '',
    created_by VARCHAR(128) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    frozen_at TIMESTAMP(6) NULL,
    PRIMARY KEY (dataset_id, version),
    INDEX idx_preference_datasets_status (status)
);

CREATE TABLE IF NOT EXISTS preference_training_jobs (
    job_id VARCHAR(128) PRIMARY KEY,
    dataset_id VARCHAR(128) NOT NULL,
    dataset_sha256 VARCHAR(64) NOT NULL,
    backend VARCHAR(24) NOT NULL DEFAULT 'dry_run',
    status VARCHAR(16) NOT NULL DEFAULT 'queued',
    base_model VARCHAR(300) NOT NULL,
    config_json JSON NOT NULL,
    log_path VARCHAR(512) NOT NULL DEFAULT '',
    artifact_path VARCHAR(512) NOT NULL DEFAULT '',
    error_summary TEXT NOT NULL,
    created_by VARCHAR(128) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    started_at TIMESTAMP(6) NULL,
    finished_at TIMESTAMP(6) NULL,
    INDEX idx_preference_jobs_status (status),
    INDEX idx_preference_jobs_dataset (dataset_id)
);

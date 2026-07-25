CREATE TABLE IF NOT EXISTS learning_task_sync_outbox (
    event_id VARCHAR(128) PRIMARY KEY,
    learner_id VARCHAR(128) NOT NULL,
    task_id VARCHAR(128) NOT NULL,
    task_version INT NOT NULL,
    event_type VARCHAR(32) NOT NULL,
    payload_json JSON NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    attempt_count INT NOT NULL DEFAULT 0,
    last_error TEXT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    delivered_at TIMESTAMP(6) NULL,
    UNIQUE KEY uq_task_sync_event (task_id, task_version, event_type),
    INDEX idx_task_sync_pending (learner_id, status, created_at)
);

CREATE TABLE IF NOT EXISTS learning_task_refresh_claims (
    learner_id VARCHAR(128) NOT NULL,
    prior_task_id VARCHAR(128) NOT NULL,
    prior_task_version INT NOT NULL,
    replacement_task_id VARCHAR(128) NOT NULL,
    replacement_task_version INT NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (learner_id, prior_task_id, prior_task_version)
);

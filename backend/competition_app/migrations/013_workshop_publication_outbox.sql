CREATE TABLE IF NOT EXISTS workshop_publication_outbox (
    operation_id VARCHAR(128) PRIMARY KEY,
    artifact_type VARCHAR(64) NOT NULL,
    learner_id VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    payload_json JSON NOT NULL,
    attempt_count INT NOT NULL DEFAULT 0,
    last_error VARCHAR(255) NULL,
    delivered_at TIMESTAMP(6) NULL
);
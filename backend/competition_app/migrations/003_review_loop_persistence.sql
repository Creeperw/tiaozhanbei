CREATE TABLE IF NOT EXISTS review_memory_units (
    memory_unit_id VARCHAR(128) NOT NULL UNIQUE,
    learner_id VARCHAR(128) NOT NULL,
    kp_id VARCHAR(128) NOT NULL,
    next_review_at TIMESTAMP(6) NOT NULL,
    version INT NOT NULL,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (learner_id, kp_id),
    INDEX idx_review_memory_due (learner_id, next_review_at)
);

CREATE TABLE IF NOT EXISTS review_schedules (
    schedule_id VARCHAR(128) PRIMARY KEY,
    learner_id VARCHAR(128) NOT NULL,
    calculated_at TIMESTAMP(6) NOT NULL,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    INDEX idx_review_schedules_learner_time (learner_id, calculated_at)
);

CREATE TABLE IF NOT EXISTS review_attempts (
    attempt_id VARCHAR(128) PRIMARY KEY,
    review_task_id VARCHAR(128) NOT NULL,
    learner_id VARCHAR(128) NOT NULL,
    kp_id VARCHAR(128) NOT NULL,
    outcome VARCHAR(32) NOT NULL,
    answered_at TIMESTAMP(6) NOT NULL,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    INDEX idx_review_attempts_unit_time (learner_id, kp_id, answered_at),
    INDEX idx_review_attempts_task (review_task_id)
);

CREATE TABLE IF NOT EXISTS review_state_events (
    event_id VARCHAR(128) PRIMARY KEY,
    learner_id VARCHAR(128) NOT NULL,
    kp_id VARCHAR(128) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    INDEX idx_review_state_events_unit_time (learner_id, kp_id, created_at)
);

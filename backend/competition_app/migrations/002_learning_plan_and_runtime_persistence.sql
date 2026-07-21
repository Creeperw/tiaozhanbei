CREATE TABLE IF NOT EXISTS long_term_plan_versions (
    plan_id VARCHAR(128) NOT NULL,
    learner_id VARCHAR(128) NOT NULL,
    version INT NOT NULL,
    status VARCHAR(32) NOT NULL,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (plan_id, version),
    INDEX idx_long_term_plan_learner (learner_id, created_at)
);

CREATE TABLE IF NOT EXISTS short_term_plan_versions (
    plan_id VARCHAR(128) NOT NULL,
    learner_id VARCHAR(128) NOT NULL,
    version INT NOT NULL,
    status VARCHAR(32) NOT NULL,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (plan_id, version),
    INDEX idx_short_term_plan_learner (learner_id, created_at)
);

CREATE TABLE IF NOT EXISTS learning_task_versions (
    task_id VARCHAR(128) NOT NULL,
    learner_id VARCHAR(128) NOT NULL,
    version INT NOT NULL,
    status VARCHAR(32) NOT NULL,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (task_id, version),
    INDEX idx_learning_task_learner (learner_id, created_at)
);

CREATE TABLE IF NOT EXISTS learner_plan_states (
    learner_id VARCHAR(128) PRIMARY KEY,
    payload_json JSON NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6)
);

CREATE TABLE IF NOT EXISTS plan_invalidation_events (
    event_id VARCHAR(128) PRIMARY KEY,
    learner_id VARCHAR(128) NOT NULL,
    invalidated_layer VARCHAR(32) NOT NULL,
    reason VARCHAR(128) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    INDEX idx_plan_invalidation_learner (learner_id, created_at)
);

CREATE TABLE IF NOT EXISTS workflow_run_states (
    thread_id VARCHAR(128) PRIMARY KEY,
    execution_id VARCHAR(128) NULL,
    case_id VARCHAR(128) NULL,
    learner_id VARCHAR(128) NULL,
    status VARCHAR(32) NOT NULL,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    INDEX idx_workflow_run_learner (learner_id, updated_at),
    INDEX idx_workflow_run_status (status, updated_at)
);

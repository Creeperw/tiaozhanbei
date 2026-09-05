CREATE TABLE IF NOT EXISTS audit_failure_cases (
    case_id VARCHAR(128) PRIMARY KEY,
    execution_id VARCHAR(128) NOT NULL,
    learner_id VARCHAR(128) NOT NULL,
    task_type VARCHAR(64) NOT NULL,
    audit_result_id VARCHAR(128) NOT NULL,
    decision VARCHAR(32) NOT NULL,
    released TINYINT(1) NOT NULL DEFAULT 0,
    issue_types JSON NOT NULL,
    findings JSON NOT NULL,
    repair_json JSON NULL,
    input_digest VARCHAR(64) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    INDEX idx_audit_failure_cases_created (created_at),
    INDEX idx_audit_failure_cases_released (released),
    INDEX idx_audit_failure_cases_decision (decision)
);

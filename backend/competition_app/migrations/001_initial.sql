CREATE TABLE IF NOT EXISTS learners (
    learner_id VARCHAR(128) PRIMARY KEY,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
);

CREATE TABLE IF NOT EXISTS learner_profiles (
    learner_id VARCHAR(128) NOT NULL,
    version INT NOT NULL,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (learner_id, version)
);

CREATE TABLE IF NOT EXISTS learner_memories (
    memory_id VARCHAR(128) PRIMARY KEY,
    learner_id VARCHAR(128) NOT NULL,
    version INT NOT NULL,
    summary TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
);

CREATE TABLE IF NOT EXISTS memory_candidates (
    candidate_id VARCHAR(128) PRIMARY KEY,
    learner_id VARCHAR(128) NOT NULL,
    summary TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending_confirmation',
    source_refs_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
);

CREATE TABLE IF NOT EXISTS conversation_sessions (
    session_id VARCHAR(128) PRIMARY KEY,
    learner_id VARCHAR(128) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    message_id VARCHAR(128) PRIMARY KEY,
    session_id VARCHAR(128) NOT NULL,
    role VARCHAR(32) NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
);

CREATE TABLE IF NOT EXISTS context_summaries (
    summary_id VARCHAR(128) PRIMARY KEY,
    session_id VARCHAR(128) NOT NULL,
    execution_id VARCHAR(128) NOT NULL,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
);

CREATE TABLE IF NOT EXISTS knowledge_mastery_states (
    learner_id VARCHAR(128) NOT NULL,
    kp_id VARCHAR(128) NOT NULL,
    mastery_score DECIMAL(6,3) NOT NULL,
    version INT NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (learner_id, kp_id)
);

CREATE TABLE IF NOT EXISTS learner_kp_review_states (
    learner_id VARCHAR(128) NOT NULL,
    kp_id VARCHAR(128) NOT NULL,
    payload_json JSON NOT NULL,
    version INT NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (learner_id, kp_id)
);

CREATE TABLE IF NOT EXISTS execution_runs (
    execution_id VARCHAR(128) PRIMARY KEY,
    case_id VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
);

CREATE TABLE IF NOT EXISTS execution_steps (
    execution_id VARCHAR(128) NOT NULL,
    step_id VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    payload_json JSON NULL,
    PRIMARY KEY (execution_id, step_id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id VARCHAR(128) PRIMARY KEY,
    execution_id VARCHAR(128) NOT NULL,
    artifact_type VARCHAR(128) NOT NULL,
    payload_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    tool_call_id VARCHAR(128) PRIMARY KEY,
    execution_id VARCHAR(128) NOT NULL,
    tool_name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    payload_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS writeback_intents (
    idempotency_key VARCHAR(255) PRIMARY KEY,
    intent_id VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    payload_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_packs (
    evidence_pack_id VARCHAR(128) PRIMARY KEY,
    execution_id VARCHAR(128) NOT NULL,
    payload_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS review_tasks (
    review_task_id VARCHAR(128) PRIMARY KEY,
    learner_id VARCHAR(128) NOT NULL,
    primary_kp_id VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    payload_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_versions (
    resource_id VARCHAR(128) NOT NULL,
    version INT NOT NULL,
    status VARCHAR(32) NOT NULL,
    payload_json JSON NOT NULL,
    PRIMARY KEY (resource_id, version)
);

CREATE TABLE IF NOT EXISTS audit_results (
    audit_result_id VARCHAR(128) PRIMARY KEY,
    resource_id VARCHAR(128) NOT NULL,
    decision VARCHAR(32) NOT NULL,
    payload_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS review_resource_bindings (
    binding_id VARCHAR(128) PRIMARY KEY,
    review_task_id VARCHAR(128) NOT NULL,
    resource_id VARCHAR(128) NOT NULL,
    resource_version INT NOT NULL,
    audit_result_id VARCHAR(128) NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshot_exports (
    export_id VARCHAR(128) PRIMARY KEY,
    execution_id VARCHAR(128) NOT NULL,
    file_path TEXT NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
);

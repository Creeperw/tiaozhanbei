CREATE TABLE IF NOT EXISTS workflow_active_run_claims (
    learner_id VARCHAR(128) NOT NULL,
    product_surface VARCHAR(64) NOT NULL,
    thread_id VARCHAR(128) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (learner_id, product_surface),
    UNIQUE KEY uq_workflow_active_thread (thread_id)
);
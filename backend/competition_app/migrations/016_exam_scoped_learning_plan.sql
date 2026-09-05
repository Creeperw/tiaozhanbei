CREATE TABLE IF NOT EXISTS learner_exam_plan_states (
    learner_id VARCHAR(128) NOT NULL,
    exam_track_id VARCHAR(128) NOT NULL,
    payload_json JSON NOT NULL,
    migrated_from_legacy BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (learner_id, exam_track_id),
    INDEX idx_exam_plan_scope_updated (exam_track_id, updated_at)
);

ALTER TABLE conversation_sessions
    ADD COLUMN exam_track_id VARCHAR(128) NULL;

CREATE INDEX idx_conversation_sessions_exam_scope
    ON conversation_sessions (learner_id, exam_track_id, created_at);

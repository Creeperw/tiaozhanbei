CREATE TABLE IF NOT EXISTS langgraph_checkpoints (
    thread_id VARCHAR(128) NOT NULL,
    checkpoint_ns VARCHAR(255) NOT NULL,
    checkpoint_id VARCHAR(128) NOT NULL,
    parent_checkpoint_id VARCHAR(128) NULL,
    checkpoint_type VARCHAR(64) NOT NULL,
    checkpoint_blob LONGBLOB NOT NULL,
    metadata_type VARCHAR(64) NOT NULL,
    metadata_blob LONGBLOB NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id),
    INDEX idx_langgraph_checkpoint_latest (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS langgraph_checkpoint_blobs (
    thread_id VARCHAR(128) NOT NULL,
    checkpoint_ns VARCHAR(255) NOT NULL,
    channel_name VARCHAR(255) NOT NULL,
    channel_version VARCHAR(128) NOT NULL,
    value_type VARCHAR(64) NOT NULL,
    value_blob LONGBLOB NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, channel_name, channel_version)
);

CREATE TABLE IF NOT EXISTS langgraph_checkpoint_writes (
    thread_id VARCHAR(128) NOT NULL,
    checkpoint_ns VARCHAR(255) NOT NULL,
    checkpoint_id VARCHAR(128) NOT NULL,
    task_id VARCHAR(128) NOT NULL,
    write_index INT NOT NULL,
    channel_name VARCHAR(255) NOT NULL,
    value_type VARCHAR(64) NOT NULL,
    value_blob LONGBLOB NOT NULL,
    task_path VARCHAR(500) NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, write_index),
    INDEX idx_langgraph_writes_checkpoint (thread_id, checkpoint_ns, checkpoint_id)
);

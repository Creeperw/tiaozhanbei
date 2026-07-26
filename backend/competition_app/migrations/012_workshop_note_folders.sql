CREATE TABLE IF NOT EXISTS workshop_note_folders (
    folder_id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    name VARCHAR(80) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_workshop_note_folder_name (user_id, name),
    INDEX idx_workshop_note_folders_user (user_id, updated_at)
);

CREATE TABLE IF NOT EXISTS workshop_favorite_folders (
    folder_id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    name VARCHAR(80) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_workshop_favorite_folder_name (user_id, name),
    INDEX idx_workshop_favorite_folders_user (user_id, updated_at)
);

CREATE TABLE IF NOT EXISTS workshop_favorites (
    favorite_id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    folder_id VARCHAR(128) NOT NULL,
    resource_type VARCHAR(32) NOT NULL,
    resource_id VARCHAR(255) NOT NULL,
    title TEXT NOT NULL,
    content_json JSON NOT NULL,
    source VARCHAR(128) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_workshop_favorite_resource (user_id, folder_id, resource_type, resource_id),
    INDEX idx_workshop_favorites_user (user_id, updated_at),
    INDEX idx_workshop_favorites_folder (folder_id, updated_at)
);

CREATE TABLE IF NOT EXISTS workshop_notes (
    note_id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    title VARCHAR(200) NOT NULL,
    content TEXT NOT NULL,
    note_type VARCHAR(64) NOT NULL,
    source VARCHAR(128) NOT NULL,
    resource_type VARCHAR(32) NULL,
    resource_id VARCHAR(255) NULL,
    context_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    INDEX idx_workshop_notes_user (user_id, updated_at),
    INDEX idx_workshop_notes_resource (user_id, resource_type, resource_id)
);

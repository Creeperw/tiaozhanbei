ALTER TABLE app_users
    ADD COLUMN email VARCHAR(320) NULL;

CREATE UNIQUE INDEX idx_app_users_email ON app_users (email);

CREATE TABLE IF NOT EXISTS auth_verification_codes (
    id VARCHAR(128) PRIMARY KEY,
    email VARCHAR(320) NOT NULL,
    code VARCHAR(6) NOT NULL,
    purpose VARCHAR(32) NOT NULL,
    expires_at TIMESTAMP(6) NOT NULL,
    used_at TIMESTAMP(6) NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    INDEX idx_auth_verification_lookup (email, purpose, expires_at, used_at)
);
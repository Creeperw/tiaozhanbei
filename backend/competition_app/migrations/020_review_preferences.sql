CREATE TABLE IF NOT EXISTS review_preferences (
    learner_id VARCHAR(128) PRIMARY KEY,
    daily_capacity INT NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
);

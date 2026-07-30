CREATE TABLE IF NOT EXISTS textbook_pdf_annotations (
    user_id VARCHAR(128) NOT NULL,
    book_id VARCHAR(128) NOT NULL,
    page_number INTEGER NOT NULL,
    annotations_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (user_id, book_id, page_number),
    INDEX idx_textbook_pdf_annotations_user (user_id, updated_at)
);

CREATE TABLE IF NOT EXISTS textbook_pdf_reading_state (
    user_id VARCHAR(128) NOT NULL,
    book_id VARCHAR(128) NOT NULL,
    page_number INTEGER NOT NULL DEFAULT 1,
    zoom DECIMAL(6, 3) NOT NULL DEFAULT 1.000,
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (user_id, book_id),
    INDEX idx_textbook_pdf_reading_state_user (user_id, updated_at)
);

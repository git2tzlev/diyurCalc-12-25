-- טבלת לוג שליחות מייל - מעקב ואודיט על כל שליחה
CREATE TABLE IF NOT EXISTS email_logs (
    id SERIAL PRIMARY KEY,
    recipient_id INTEGER,                    -- מזהה הנמען (person_id)
    recipient_email VARCHAR(255),
    recipient_name VARCHAR(255),
    email_type VARCHAR(50) NOT NULL,         -- 'shifts_report', 'chains_report', 'combined_report', 'test'
    subject VARCHAR(500),
    status VARCHAR(20) NOT NULL,             -- 'sent', 'failed', 'skipped'
    error_message TEXT,                      -- סיבת כישלון/דילוג
    month INTEGER,
    year INTEGER,
    sent_by INTEGER,                         -- מי שלח (user ID)
    sent_at TIMESTAMP DEFAULT NOW(),
    batch_id VARCHAR(100)                    -- מקשר שליחות מרוכזות לקבוצה
);

-- אינדקסים לחיפוש מהיר
CREATE INDEX IF NOT EXISTS idx_email_logs_batch_id ON email_logs(batch_id);
CREATE INDEX IF NOT EXISTS idx_email_logs_recipient_id ON email_logs(recipient_id);
CREATE INDEX IF NOT EXISTS idx_email_logs_sent_at ON email_logs(sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_logs_status ON email_logs(status);

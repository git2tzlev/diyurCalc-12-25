-- טבלת ימים מיוחדים עם תעריף פרימיום (פורים/עצמאות/בחירות/מותאם)
-- משלימה את shabbat_times שממשיכה להחזיק שבתות וחגים יהודיים
CREATE TABLE IF NOT EXISTS special_days (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,                  -- שם תצוגה ("פורים תשפ"ז")
    start_date      DATE        NOT NULL,                   -- תאריך תחילת החלון
    start_time      TIME        NOT NULL,                   -- שעת תחילה ב-start_date
    end_date        DATE        NOT NULL,                   -- תאריך סיום (שונה מ-start_date לעצמאות)
    end_time        TIME        NOT NULL,                   -- שעת סיום ב-end_date
    rate_pct        INTEGER     NOT NULL CHECK (rate_pct > 100 AND rate_pct <= 300),
    standby_mode    VARCHAR(16) NOT NULL DEFAULT 'none'
                      CHECK (standby_mode IN ('shabbat', 'none')),
    city_filter     TEXT[],                                 -- רק ערים אלה (NULL = כל הערים)
    is_active       BOOLEAN     NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_date >= start_date)
);

-- אינדקס לשליפה לפי טווח תאריכים (השאילתה הנפוצה ביותר)
CREATE INDEX IF NOT EXISTS idx_special_days_range
    ON special_days (start_date, end_date) WHERE is_active;

-- Trigger לעדכון updated_at אוטומטית
CREATE OR REPLACE FUNCTION update_special_days_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_special_days_updated_at ON special_days;
CREATE TRIGGER trg_special_days_updated_at
    BEFORE UPDATE ON special_days
    FOR EACH ROW
    EXECUTE FUNCTION update_special_days_updated_at();

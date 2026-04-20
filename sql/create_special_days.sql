-- טבלת ימים מיוחדים עם תעריף פרימיום (פורים/עצמאות/בחירות/מותאם)
-- משלימה את shabbat_times שממשיכה להחזיק שבתות וחגים יהודיים
CREATE TABLE IF NOT EXISTS special_days (
    id              SERIAL PRIMARY KEY,
    day_type        VARCHAR(32) NOT NULL
                      CHECK (day_type IN ('purim', 'independence', 'elections', 'custom')),
    name            VARCHAR(255) NOT NULL,                  -- שם תצוגה ("יום העצמאות תשפ"ו")
    start_date      DATE        NOT NULL,                   -- תאריך תחילת החלון
    start_time      TIME        NOT NULL,                   -- שעת תחילה ב-start_date
    end_date        DATE        NOT NULL,                   -- תאריך סיום (שונה מ-start_date לעצמאות)
    end_time        TIME        NOT NULL,                   -- שעת סיום ב-end_date
    rate_pct        INTEGER     NOT NULL CHECK (rate_pct > 100 AND rate_pct <= 300),
    standby_mode    VARCHAR(16) NOT NULL DEFAULT 'none'
                      CHECK (standby_mode IN ('shabbat', 'none')),
    city_filter     TEXT[],                                 -- whitelist (NULL = כל הערים)
    city_exclude    TEXT[],                                 -- blacklist (NULL = לא מחריג אף עיר)
    is_active       BOOLEAN     NOT NULL DEFAULT true,
    source          VARCHAR(32),                            -- 'auto_seeded' / 'manual'
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (NOT (city_filter IS NOT NULL AND city_exclude IS NOT NULL)),
    CHECK (end_date >= start_date)
);

-- אינדקס לשליפה לפי טווח תאריכים (השאילתה הנפוצה ביותר)
CREATE INDEX IF NOT EXISTS idx_special_days_range
    ON special_days (start_date, end_date) WHERE is_active;

-- אינדקס לשליפה/ניהול לפי סוג יום (לאדמין ולסקריפטי seeding)
CREATE INDEX IF NOT EXISTS idx_special_days_day_type
    ON special_days (day_type, start_date) WHERE is_active;

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

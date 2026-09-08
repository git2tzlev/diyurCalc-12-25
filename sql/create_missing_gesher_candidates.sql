-- החלטות על עובדים שנעדרו לחלוטין מקובץ הגשר הסופי.
-- הסכמה מותקנת גם באופן idempotent דרך services/missing_gesher_candidates.py.
CREATE TABLE IF NOT EXISTS missing_gesher_candidates (
    id BIGSERIAL PRIMARY KEY,
    payment_year INTEGER NOT NULL,
    payment_month INTEGER NOT NULL CHECK (payment_month BETWEEN 1 AND 12),
    work_year INTEGER NOT NULL,
    work_month INTEGER NOT NULL CHECK (work_month BETWEEN 1 AND 12),
    company_code TEXT NOT NULL,
    housing_array_id INTEGER NULL REFERENCES housing_arrays(id) ON DELETE SET NULL,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    employee_code TEXT NOT NULL,
    final_file_id BIGINT NOT NULL REFERENCES gesher_export_files(id) ON DELETE CASCADE,
    final_filename TEXT NOT NULL DEFAULT '',
    values_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    total_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
    snapshot_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending','approved','rejected','included_in_export','cancelled')
    ),
    block_reason TEXT NULL,
    original_payment_periods JSONB NOT NULL DEFAULT '[]'::jsonb,
    approved_at TIMESTAMP NULL,
    approved_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL,
    rejected_at TIMESTAMP NULL,
    rejected_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL,
    included_at TIMESTAMP NULL,
    included_export_file_id BIGINT NULL REFERENCES gesher_export_files(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (payment_year, payment_month, work_year, work_month,
            company_code, housing_array_id, person_id)
);

CREATE INDEX IF NOT EXISTS idx_missing_gesher_candidates_period_status
ON missing_gesher_candidates (payment_year, payment_month, housing_array_id, status);

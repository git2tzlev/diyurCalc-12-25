CREATE TABLE IF NOT EXISTS gesher_export_files (
    id SERIAL PRIMARY KEY,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    company_code TEXT NULL,
    housing_array_id INTEGER NULL REFERENCES housing_arrays(id),
    export_scope TEXT NOT NULL,
    person_ids INTEGER[] NULL,
    filename TEXT NOT NULL,
    content TEXT NOT NULL,
    encoding TEXT NOT NULL DEFAULT 'ascii',
    line_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT NULL,
    is_cancelled BOOLEAN NOT NULL DEFAULT false,
    cancelled_at TIMESTAMP NULL,
    cancelled_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL,
    is_final BOOLEAN NOT NULL DEFAULT false,
    finalized_at TIMESTAMP NULL,
    finalized_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL,
    notes_updated_at TIMESTAMP NULL,
    notes_updated_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL
);

ALTER TABLE gesher_export_files
    ADD COLUMN IF NOT EXISTS is_cancelled BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE gesher_export_files
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMP NULL;

ALTER TABLE gesher_export_files
    ADD COLUMN IF NOT EXISTS cancelled_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL;

ALTER TABLE gesher_export_files
    ADD COLUMN IF NOT EXISTS is_final BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE gesher_export_files
    ADD COLUMN IF NOT EXISTS finalized_at TIMESTAMP NULL;

ALTER TABLE gesher_export_files
    ADD COLUMN IF NOT EXISTS finalized_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_gesher_export_files_period
    ON gesher_export_files (year, month);

CREATE INDEX IF NOT EXISTS idx_gesher_export_files_housing
    ON gesher_export_files (housing_array_id);

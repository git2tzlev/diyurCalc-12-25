CREATE TABLE IF NOT EXISTS shift_time_overrides_history (
    id SERIAL PRIMARY KEY,
    original_override_id INTEGER NULL,
    shift_type_id INTEGER NOT NULL,
    apartment_id INTEGER NULL,
    housing_array_id INTEGER NULL,
    start_time TIME NULL,
    end_time TIME NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_shift_time_overrides_history_original_month
    ON shift_time_overrides_history (original_override_id, year, month)
    WHERE original_override_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_shift_time_overrides_history_apartment_month
    ON shift_time_overrides_history (shift_type_id, apartment_id, year, month)
    WHERE original_override_id IS NULL AND apartment_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_shift_time_overrides_history_housing_month
    ON shift_time_overrides_history (shift_type_id, housing_array_id, year, month)
    WHERE original_override_id IS NULL AND housing_array_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_shift_time_overrides_history_lookup
    ON shift_time_overrides_history
        (shift_type_id, year, month, original_override_id, apartment_id, housing_array_id);

ALTER TABLE time_reports
ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NULL;

ALTER TABLE time_reports
ADD COLUMN IF NOT EXISTS updated_by INTEGER NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_time_reports_updated_by'
          AND conrelid = 'time_reports'::regclass
    ) THEN
        ALTER TABLE time_reports
        ADD CONSTRAINT fk_time_reports_updated_by
        FOREIGN KEY (updated_by) REFERENCES people(id)
        ON DELETE SET NULL;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION set_time_reports_updated_audit()
RETURNS trigger AS $$
DECLARE
    actor_id_text text;
BEGIN
    NEW.updated_at = NOW();

    actor_id_text := current_setting('app.current_user_id', true);
    IF (NEW.updated_by IS NOT DISTINCT FROM OLD.updated_by)
       AND actor_id_text IS NOT NULL
       AND actor_id_text <> '' THEN
        NEW.updated_by = actor_id_text::integer;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_time_reports_updated_audit ON time_reports;

CREATE TRIGGER trg_time_reports_updated_audit
BEFORE UPDATE ON time_reports
FOR EACH ROW
EXECUTE FUNCTION set_time_reports_updated_audit();

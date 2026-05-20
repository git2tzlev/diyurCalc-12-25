"""Schema defaults for auditing time_reports updates."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def ensure_time_reports_audit_columns(conn) -> None:
    """Add update audit columns and trigger to time_reports if missing."""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            ALTER TABLE time_reports
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NULL
        """)
        cursor.execute("""
            ALTER TABLE time_reports
            ADD COLUMN IF NOT EXISTS updated_by INTEGER NULL
        """)
        cursor.execute("""
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
        """)
        cursor.execute("""
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
        """)
        cursor.execute("""
            DROP TRIGGER IF EXISTS trg_time_reports_updated_audit ON time_reports
        """)
        cursor.execute("""
            CREATE TRIGGER trg_time_reports_updated_audit
            BEFORE UPDATE ON time_reports
            FOR EACH ROW
            EXECUTE FUNCTION set_time_reports_updated_audit()
        """)
        conn.commit()
    except Exception:
        conn.rollback()
        logger.warning("Could not ensure time_reports audit columns", exc_info=True)
        raise
    finally:
        cursor.close()

"""Database audit defaults for salary-sensitive tables."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


AUDITED_TABLES = ("payment_components", "guide_fixed_payments")


def ensure_salary_audit_schema(conn) -> None:
    """Create generic audit log and triggers for salary-sensitive tables."""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id BIGSERIAL PRIMARY KEY,
                table_name TEXT NOT NULL,
                record_id INTEGER NULL,
                action TEXT NOT NULL CHECK (action IN ('INSERT', 'UPDATE', 'DELETE')),
                old_data JSONB NULL,
                new_data JSONB NULL,
                changed_fields JSONB NULL,
                actor_person_id INTEGER NULL REFERENCES people(id) ON DELETE SET NULL,
                changed_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_log_table_record
            ON audit_log (table_name, record_id, changed_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_log_actor
            ON audit_log (actor_person_id, changed_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_log_changed_at
            ON audit_log (changed_at DESC)
        """)

        _ensure_actor_columns(cursor, "payment_components", include_timestamps=False)
        _ensure_actor_columns(cursor, "guide_fixed_payments", include_timestamps=True)
        _ensure_audit_functions(cursor)
        for table_name in AUDITED_TABLES:
            _ensure_audit_trigger(cursor, table_name)

        conn.commit()
    except Exception:
        conn.rollback()
        logger.warning("Could not ensure salary audit schema", exc_info=True)
        raise
    finally:
        cursor.close()


def _ensure_actor_columns(cursor, table_name: str, *, include_timestamps: bool) -> None:
    if include_timestamps:
        cursor.execute(f"""
            ALTER TABLE {table_name}
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NULL DEFAULT NOW()
        """)
        cursor.execute(f"""
            ALTER TABLE {table_name}
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NULL
        """)

    cursor.execute(f"""
        ALTER TABLE {table_name}
        ADD COLUMN IF NOT EXISTS created_by INTEGER NULL
    """)
    cursor.execute(f"""
        ALTER TABLE {table_name}
        ADD COLUMN IF NOT EXISTS updated_by INTEGER NULL
    """)
    for column_name in ("created_by", "updated_by"):
        constraint_name = f"fk_{table_name}_{column_name}"
        cursor.execute(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = '{constraint_name}'
                      AND conrelid = '{table_name}'::regclass
                ) THEN
                    ALTER TABLE {table_name}
                    ADD CONSTRAINT {constraint_name}
                    FOREIGN KEY ({column_name}) REFERENCES people(id)
                    ON DELETE SET NULL;
                END IF;
            END $$;
        """)


def _ensure_audit_functions(cursor) -> None:
    cursor.execute("""
        CREATE OR REPLACE FUNCTION audit_changed_fields(old_row jsonb, new_row jsonb)
        RETURNS jsonb AS $$
        SELECT COALESCE(jsonb_agg(key ORDER BY key), '[]'::jsonb)
        FROM (
            SELECT key
            FROM jsonb_each(old_row)
            WHERE old_row -> key IS DISTINCT FROM new_row -> key
        ) changed;
        $$ LANGUAGE sql IMMUTABLE;
    """)
    cursor.execute("""
        CREATE OR REPLACE FUNCTION audit_salary_sensitive_row()
        RETURNS trigger AS $$
        DECLARE
            actor_id_text text;
            actor_id integer;
            old_json jsonb;
            new_json jsonb;
            changed jsonb;
        BEGIN
            actor_id_text := current_setting('app.current_user_id', true);
            IF actor_id_text IS NOT NULL AND actor_id_text <> '' THEN
                actor_id := actor_id_text::integer;
            ELSE
                actor_id := NULL;
            END IF;

            IF TG_OP = 'INSERT' THEN
                IF NEW.created_by IS NULL THEN
                    NEW.created_by := actor_id;
                END IF;
                IF NEW.updated_by IS NULL THEN
                    NEW.updated_by := actor_id;
                END IF;
                IF NEW.updated_at IS NULL THEN
                    NEW.updated_at := NOW();
                END IF;

                new_json := to_jsonb(NEW);
                INSERT INTO audit_log (
                    table_name, record_id, action, old_data, new_data,
                    changed_fields, actor_person_id
                )
                VALUES (
                    TG_TABLE_NAME, NEW.id, TG_OP, NULL, new_json,
                    to_jsonb(ARRAY(SELECT jsonb_object_keys(new_json))),
                    actor_id
                );
                RETURN NEW;
            ELSIF TG_OP = 'UPDATE' THEN
                IF NEW.updated_by IS NULL OR NEW.updated_by IS NOT DISTINCT FROM OLD.updated_by THEN
                    NEW.updated_by := actor_id;
                END IF;
                NEW.updated_at := NOW();

                old_json := to_jsonb(OLD);
                new_json := to_jsonb(NEW);
                changed := audit_changed_fields(old_json, new_json);
                INSERT INTO audit_log (
                    table_name, record_id, action, old_data, new_data,
                    changed_fields, actor_person_id
                )
                VALUES (
                    TG_TABLE_NAME, NEW.id, TG_OP, old_json, new_json,
                    changed, actor_id
                );
                RETURN NEW;
            ELSIF TG_OP = 'DELETE' THEN
                old_json := to_jsonb(OLD);
                INSERT INTO audit_log (
                    table_name, record_id, action, old_data, new_data,
                    changed_fields, actor_person_id
                )
                VALUES (
                    TG_TABLE_NAME, OLD.id, TG_OP, old_json, NULL,
                    to_jsonb(ARRAY(SELECT jsonb_object_keys(old_json))),
                    actor_id
                );
                RETURN OLD;
            END IF;

            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
    """)


def _ensure_audit_trigger(cursor, table_name: str) -> None:
    trigger_name = f"trg_audit_{table_name}"
    cursor.execute(f"""
        DROP TRIGGER IF EXISTS {trigger_name} ON {table_name}
    """)
    cursor.execute(f"""
        CREATE TRIGGER {trigger_name}
        BEFORE INSERT OR UPDATE OR DELETE ON {table_name}
        FOR EACH ROW
        EXECUTE FUNCTION audit_salary_sensitive_row()
    """)

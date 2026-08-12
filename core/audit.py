"""Database audit defaults for salary-sensitive tables."""
from __future__ import annotations

import logging

from core.constants import MANUAL_COMPLETION_COMPONENT_TYPE_IDS

logger = logging.getLogger(__name__)


AUDITED_TABLES = (
    "payment_components",
    "guide_fixed_payments",
    "time_reports",
    "people",
    "person_status_history",
    "payment_codes",
    "shift_type_housing_rates",
    "shift_type_housing_rates_history",
    "minimum_wage_rates",
    "special_days",
    "shabbat_times",
    "apartments",
    "apartment_status_history",
    "shift_time_segments",
    "shift_time_overrides",
    "shift_time_overrides_history",
    "standby_rates",
    "standby_rates_history",
    "holiday_payment_apartment_guides",
    "payment_component_types",
    "salary_impact_events",
)


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
                changed_at TIMESTAMP NOT NULL DEFAULT NOW(),
                actor_kind TEXT NOT NULL DEFAULT 'system',
                actor_label TEXT NULL
            )
        """)
        cursor.execute("""
            ALTER TABLE audit_log
            ADD COLUMN IF NOT EXISTS actor_kind TEXT NOT NULL DEFAULT 'system'
        """)
        cursor.execute("""
            ALTER TABLE audit_log
            ADD COLUMN IF NOT EXISTS actor_label TEXT NULL
        """)
        cursor.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'chk_audit_log_actor_kind'
                      AND conrelid = 'audit_log'::regclass
                ) THEN
                    ALTER TABLE audit_log
                    ADD CONSTRAINT chk_audit_log_actor_kind
                    CHECK (actor_kind IN ('user', 'automatic', 'script', 'system'));
                END IF;
            END $$;
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

        _ensure_salary_impact_events(cursor)
        _ensure_audit_functions(cursor)
        _ensure_salary_impact_capture(cursor)
        for table_name in AUDITED_TABLES:
            if not _table_exists(cursor, table_name):
                logger.info("Skipping audit trigger for missing table %s", table_name)
                continue
            _ensure_actor_columns(cursor, table_name, include_timestamps=True)
            _ensure_audit_trigger(cursor, table_name)

        conn.commit()
    except Exception:
        conn.rollback()
        logger.warning("Could not ensure salary audit schema", exc_info=True)
        raise
    finally:
        cursor.close()


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
    row = cursor.fetchone()
    return bool(row and row[0])


def _ensure_salary_impact_events(cursor) -> None:
    """Ensure the event ledger used for retroactive completion calculation."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS salary_impact_events (
            id BIGSERIAL PRIMARY KEY,
            event_domain TEXT NOT NULL,
            event_type TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_id INTEGER NULL,
            source_action TEXT NOT NULL,
            person_id INTEGER NULL REFERENCES people(id) ON DELETE SET NULL,
            apartment_id INTEGER NULL REFERENCES apartments(id) ON DELETE SET NULL,
            housing_array_id INTEGER NULL REFERENCES housing_arrays(id) ON DELETE SET NULL,
            work_date DATE NULL,
            work_year INTEGER NULL,
            work_month INTEGER NULL CHECK (work_month BETWEEN 1 AND 12),
            payment_year INTEGER NOT NULL,
            payment_month INTEGER NOT NULL CHECK (payment_month BETWEEN 1 AND 12),
            effective_from DATE NULL,
            effective_to DATE NULL,
            old_data JSONB NULL,
            new_data JSONB NULL,
            changed_fields JSONB NULL,
            reason TEXT NULL,
            note TEXT NULL,
            audit_log_id BIGINT NULL REFERENCES audit_log(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'open' CHECK (
                status IN ('open', 'ignored', 'included_in_export', 'exported', 'cancelled', 'superseded')
            ),
            actor_person_id INTEGER NULL REFERENCES people(id) ON DELETE SET NULL,
            actor_kind TEXT NOT NULL DEFAULT 'system',
            actor_label TEXT NULL,
            export_batch_id BIGINT NULL,
            exported_at TIMESTAMP NULL,
            exported_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            created_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL,
            updated_at TIMESTAMP NULL,
            updated_by INTEGER NULL REFERENCES people(id) ON DELETE SET NULL
        )
    """)
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_salary_impact_events_audit_log
        ON salary_impact_events (audit_log_id)
        WHERE audit_log_id IS NOT NULL
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_salary_impact_events_payment_month
        ON salary_impact_events (payment_year, payment_month, status)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_salary_impact_events_person_payment
        ON salary_impact_events (person_id, payment_year, payment_month)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_salary_impact_events_work_month
        ON salary_impact_events (work_year, work_month)
    """)


def _ensure_salary_impact_capture(cursor) -> None:
    """Capture explicitly marked late report/component mutations from audit rows."""
    manual_component_ids = ", ".join(
        str(component_type_id)
        for component_type_id in sorted(MANUAL_COMPLETION_COMPONENT_TYPE_IDS)
    )
    cursor.execute(f"""
        CREATE OR REPLACE FUNCTION capture_salary_impact_from_audit()
        RETURNS trigger AS $$
        DECLARE
            status_value text;
            row_data jsonb;
            work_date_value date;
            payment_year_value integer;
            payment_month_value integer;
            apartment_value integer;
            housing_value integer;
            source_value integer;
            event_name text;
            domain_name text;
        BEGIN
            IF NEW.table_name NOT IN ('time_reports', 'payment_components') THEN
                RETURN NEW;
            END IF;
            IF NEW.action = 'UPDATE' AND NOT (
                COALESCE(NEW.changed_fields, '[]'::jsonb) ?| CASE
                    WHEN NEW.table_name = 'time_reports' THEN
                        ARRAY['date','person_id','apartment_id','start_time','end_time','shift_type_id','rate_apartment_type_id','asd_night_marking','exclude_standby']
                    ELSE
                        ARRAY['date','person_id','apartment_id','component_type_id','quantity','rate','for_pension']
                END
            ) THEN
                RETURN NEW;
            END IF;
            row_data := COALESCE(NEW.new_data, NEW.old_data);
            IF row_data IS NULL OR COALESCE(row_data ->> 'date', '') = '' THEN
                RETURN NEW;
            END IF;
            work_date_value := (row_data ->> 'date')::date;
            payment_year_value := COALESCE(
                NULLIF(COALESCE(NEW.new_data ->> 'payment_year', NEW.old_data ->> 'payment_year'), '')::integer,
                NULLIF(current_setting('app.payment_year', true), '')::integer
            );
            payment_month_value := COALESCE(
                NULLIF(COALESCE(NEW.new_data ->> 'payment_month', NEW.old_data ->> 'payment_month'), '')::integer,
                NULLIF(current_setting('app.payment_month', true), '')::integer
            );
            IF payment_year_value IS NULL OR payment_month_value IS NULL
               OR payment_year_value * 100 + payment_month_value <=
                  EXTRACT(YEAR FROM work_date_value)::integer * 100 + EXTRACT(MONTH FROM work_date_value)::integer THEN
                RETURN NEW;
            END IF;

            source_value := COALESCE(NEW.record_id, NULLIF(row_data ->> 'id', '')::integer);
            apartment_value := NULLIF(row_data ->> 'apartment_id', '')::integer;
            SELECT housing_array_id INTO housing_value FROM apartments WHERE id = apartment_value;
            event_name := CASE NEW.action WHEN 'INSERT' THEN 'created' WHEN 'UPDATE' THEN 'updated' ELSE 'deleted' END;
            domain_name := CASE NEW.table_name WHEN 'time_reports' THEN 'report' ELSE 'payment_component' END;
            -- רכיבים שההשלמה שלהם משולמת ידנית אינם יוצאים לגשר, ולכן אין מה לאשר
            status_value := CASE
                WHEN NEW.table_name = 'payment_components'
                     AND NULLIF(row_data ->> 'component_type_id', '')::integer
                         IN ({manual_component_ids})
                THEN 'included_in_export'
                ELSE 'open'
            END;

            INSERT INTO salary_impact_events (
                status,
                event_domain, event_type, source_table, source_id, source_action,
                person_id, apartment_id, housing_array_id, work_date, work_year, work_month,
                payment_year, payment_month, old_data, new_data, changed_fields,
                reason, audit_log_id, actor_person_id, actor_kind, actor_label, created_by
            ) VALUES (
                status_value,
                domain_name, event_name, NEW.table_name, source_value, NEW.action,
                NULLIF(row_data ->> 'person_id', '')::integer,
                apartment_value, housing_value, work_date_value,
                EXTRACT(YEAR FROM work_date_value)::integer,
                EXTRACT(MONTH FROM work_date_value)::integer,
                payment_year_value, payment_month_value, NEW.old_data, NEW.new_data,
                NEW.changed_fields, 'payment_period_record_' || event_name,
                NEW.id, NEW.actor_person_id, NEW.actor_kind, NEW.actor_label, NEW.actor_person_id
            )
            ON CONFLICT (audit_log_id) WHERE audit_log_id IS NOT NULL DO NOTHING;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    cursor.execute("DROP TRIGGER IF EXISTS trg_capture_salary_impact ON audit_log")
    cursor.execute("""
        CREATE TRIGGER trg_capture_salary_impact
        AFTER INSERT ON audit_log
        FOR EACH ROW EXECUTE FUNCTION capture_salary_impact_from_audit()
    """)


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
                    ON DELETE SET NULL
                    NOT VALID;
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
            actor_kind text;
            actor_label text;
            old_json jsonb;
            new_json jsonb;
            changed jsonb;
            row_id integer;
        BEGIN
            actor_id_text := current_setting('app.current_user_id', true);
            IF actor_id_text IS NOT NULL AND actor_id_text ~ '^[0-9]+$' THEN
                actor_id := actor_id_text::integer;
            ELSE
                actor_id := NULL;
            END IF;

            actor_kind := current_setting('app.audit_actor_kind', true);
            IF actor_kind IS NULL OR actor_kind = '' THEN
                actor_kind := CASE WHEN actor_id IS NULL THEN 'system' ELSE 'user' END;
            ELSIF actor_kind NOT IN ('user', 'automatic', 'script', 'system') THEN
                actor_kind := 'system';
            END IF;

            actor_label := current_setting('app.audit_actor_label', true);
            IF actor_label = '' THEN
                actor_label := NULL;
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
                IF (new_json ->> 'id') ~ '^[0-9]+$' THEN
                    row_id := (new_json ->> 'id')::integer;
                ELSE
                    row_id := NULL;
                END IF;
                INSERT INTO audit_log (
                    table_name, record_id, action, old_data, new_data,
                    changed_fields, actor_person_id, actor_kind, actor_label
                )
                VALUES (
                    TG_TABLE_NAME, row_id, TG_OP, NULL, new_json,
                    to_jsonb(ARRAY(SELECT jsonb_object_keys(new_json))),
                    actor_id, actor_kind, actor_label
                );
                RETURN NEW;
            ELSIF TG_OP = 'UPDATE' THEN
                IF NEW.updated_by IS NULL OR NEW.updated_by IS NOT DISTINCT FROM OLD.updated_by THEN
                    NEW.updated_by := actor_id;
                END IF;
                NEW.updated_at := NOW();

                old_json := to_jsonb(OLD);
                new_json := to_jsonb(NEW);
                IF (new_json ->> 'id') ~ '^[0-9]+$' THEN
                    row_id := (new_json ->> 'id')::integer;
                ELSE
                    row_id := NULL;
                END IF;
                changed := audit_changed_fields(old_json, new_json);
                INSERT INTO audit_log (
                    table_name, record_id, action, old_data, new_data,
                    changed_fields, actor_person_id, actor_kind, actor_label
                )
                VALUES (
                    TG_TABLE_NAME, row_id, TG_OP, old_json, new_json,
                    changed, actor_id, actor_kind, actor_label
                );
                RETURN NEW;
            ELSIF TG_OP = 'DELETE' THEN
                old_json := to_jsonb(OLD);
                IF (old_json ->> 'id') ~ '^[0-9]+$' THEN
                    row_id := (old_json ->> 'id')::integer;
                ELSE
                    row_id := NULL;
                END IF;
                INSERT INTO audit_log (
                    table_name, record_id, action, old_data, new_data,
                    changed_fields, actor_person_id, actor_kind, actor_label
                )
                VALUES (
                    TG_TABLE_NAME, row_id, TG_OP, old_json, NULL,
                    to_jsonb(ARRAY(SELECT jsonb_object_keys(old_json))),
                    actor_id, actor_kind, actor_label
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

"""Payment-period helpers for retroactive completions."""
from __future__ import annotations

from datetime import date
from typing import Any, Optional


PAYMENT_PERIOD_COLUMNS_SQL = (
    ("payment_year", "INTEGER NULL"),
    ("payment_month", "INTEGER NULL"),
    ("payment_note", "TEXT NULL"),
    ("payment_marked_at", "TIMESTAMP NULL"),
    ("payment_marked_by", "INTEGER NULL REFERENCES people(id) ON DELETE SET NULL"),
)


def ensure_payment_period_columns(conn) -> None:
    """Add payment-period marker columns to reports and payment components."""
    cursor = conn.cursor()
    try:
        for table_name in ("time_reports", "payment_components"):
            for column_name, column_def in PAYMENT_PERIOD_COLUMNS_SQL:
                cursor.execute(f"""
                    ALTER TABLE {table_name}
                    ADD COLUMN IF NOT EXISTS {column_name} {column_def}
                """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_time_reports_payment_period
            ON time_reports (payment_year, payment_month)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_payment_components_payment_period
            ON payment_components (payment_year, payment_month)
        """)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def month_key(year: int, month: int) -> int:
    return year * 100 + month


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return value.date() if hasattr(value, "date") else None


def get_payment_period_completions(
    conn,
    payment_year: int,
    payment_month: int,
    *,
    housing_array_id: Optional[int] = None,
) -> dict[str, Any]:
    """Fetch reports/components marked for a later payment month."""
    ensure_payment_period_columns(conn.conn if hasattr(conn, "conn") else conn)
    params: list[Any] = [payment_year, payment_month]
    housing_sql = ""
    if housing_array_id is not None:
        housing_sql = "AND ap.housing_array_id = %s"
        params.append(housing_array_id)

    time_reports = conn.execute(f"""
        SELECT 'time_report' AS item_type,
               tr.id, tr.person_id, p.name AS person_name, p.meirav_code,
               tr.date, tr.start_time, tr.end_time, tr.shift_type_id,
               st.name AS shift_name,
               tr.apartment_id, ap.name AS apartment_name, ap.housing_array_id,
               ha.name AS housing_array_name,
               tr.payment_year, tr.payment_month, tr.payment_note,
               tr.payment_marked_at, marker.name AS payment_marked_by_name
        FROM time_reports tr
        JOIN people p ON p.id = tr.person_id
        JOIN apartments ap ON ap.id = tr.apartment_id
        LEFT JOIN housing_arrays ha ON ha.id = ap.housing_array_id
        LEFT JOIN shift_types st ON st.id = tr.shift_type_id
        LEFT JOIN people marker ON marker.id = tr.payment_marked_by
        WHERE tr.payment_year = %s AND tr.payment_month = %s
          AND (EXTRACT(YEAR FROM tr.date)::int * 100 + EXTRACT(MONTH FROM tr.date)::int) < (%s * 100 + %s)
          {housing_sql}
        ORDER BY tr.date, p.name, tr.start_time
    """, tuple(params[:2] + [payment_year, payment_month] + params[2:])).fetchall()

    component_params: list[Any] = [payment_year, payment_month]
    if housing_array_id is not None:
        component_params.append(housing_array_id)
    payment_components = conn.execute(f"""
        SELECT 'payment_component' AS item_type,
               pc.id, pc.person_id, p.name AS person_name, p.meirav_code,
               pc.date, pc.quantity, pc.rate, pc.component_type_id,
               pct.name AS component_name,
               pc.apartment_id, ap.name AS apartment_name, ap.housing_array_id,
               ha.name AS housing_array_name,
               pc.payment_year, pc.payment_month, pc.payment_note,
               pc.payment_marked_at, marker.name AS payment_marked_by_name
        FROM payment_components pc
        JOIN people p ON p.id = pc.person_id
        JOIN apartments ap ON ap.id = pc.apartment_id
        LEFT JOIN housing_arrays ha ON ha.id = ap.housing_array_id
        LEFT JOIN payment_component_types pct ON pct.id = pc.component_type_id
        LEFT JOIN people marker ON marker.id = pc.payment_marked_by
        WHERE pc.payment_year = %s AND pc.payment_month = %s
          AND (EXTRACT(YEAR FROM pc.date)::int * 100 + EXTRACT(MONTH FROM pc.date)::int) < (%s * 100 + %s)
          {housing_sql}
        ORDER BY pc.date, p.name, pc.id
    """, tuple(component_params[:2] + [payment_year, payment_month] + component_params[2:])).fetchall()

    items = [dict(row) for row in time_reports] + [dict(row) for row in payment_components]
    by_work_month: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for item in items:
        item_date = _as_date(item.get("date"))
        if not item_date:
            continue
        key = (item_date.year, item_date.month)
        item["work_year"] = item_date.year
        item["work_month"] = item_date.month
        by_work_month.setdefault(key, []).append(item)

    return {
        "items": items,
        "by_work_month": by_work_month,
        "time_report_ids": [row["id"] for row in time_reports],
        "payment_component_ids": [row["id"] for row in payment_components],
    }

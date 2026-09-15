"""
Core business logic for DiyurCalc application.
Contains public API functions for calculating monthly totals and summaries.

Import directly from submodules for specific functionality:
- core.time_utils: Time conversion and Shabbat detection
- app_utils: Wage calculation (single source of truth)
- core.constants: Shift IDs and constants
"""
import logging
import psycopg2
import psycopg2.extras
from datetime import date
from typing import List, Tuple, Dict, Any, Optional

from utils.cache_manager import cached
from core.time_utils import get_shabbat_times_cache
from core.database import get_housing_array_filter
from core.constants import should_exclude_asd_completion_report

# =============================================================================
# Configure logging
# =============================================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =============================================================================
# Data Access Functions (with caching)
# =============================================================================


@cached(ttl=1800)  # Cache for 30 minutes
def get_active_guides(housing_array_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    שליפת מדריכים פעילים.

    Args:
        housing_array_id: מזהה מערך דיור לסינון. אם None - מחזיר את כל המדריכים.

    Returns:
        רשימת מדריכים פעילים.
    """
    from core.database import get_pooled_connection, return_connection
    conn = get_pooled_connection()
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if housing_array_id is not None:
            # סינון מדריכים לפי מערך דיור שלהם
            cursor.execute(
                """
                SELECT id, name, type, is_active, start_date, email, meirav_code, id_number
                FROM people
                WHERE is_active::integer = 1
                  AND housing_array_id = %s
                ORDER BY name
                """,
                (housing_array_id,)
            )
        else:
            cursor.execute(
                """
                SELECT id, name, type, is_active, start_date, email, meirav_code, id_number
                FROM people
                WHERE is_active::integer = 1
                ORDER BY name
                """
            )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        return_connection(conn)

    return [dict(row) for row in rows]


def get_available_months_for_person(conn, person_id: int) -> List[Tuple[int, int]]:
    """Fetch distinct months for a specific person efficiently using SQL.

    כולל חודשים עם משמרות (time_reports) או רכיבי תשלום (payment_components).
    מסנן לפי מערך דיור אם הוגדר פילטר.
    """
    cursor = conn.cursor()
    housing_filter = get_housing_array_filter()

    try:
        if housing_filter is not None:
            # סינון לפי מערך דיור
            cursor.execute("""
                SELECT DISTINCT year, month FROM (
                    SELECT
                        CAST(EXTRACT(YEAR FROM tr.date) AS INTEGER) as year,
                        CAST(EXTRACT(MONTH FROM tr.date) AS INTEGER) as month
                    FROM time_reports tr
                    JOIN apartments ap ON ap.id = tr.apartment_id
                    WHERE tr.person_id = %s AND ap.housing_array_id = %s
                    UNION
                    SELECT
                        CAST(EXTRACT(YEAR FROM pc.date) AS INTEGER) as year,
                        CAST(EXTRACT(MONTH FROM pc.date) AS INTEGER) as month
                    FROM payment_components pc
                    JOIN apartments ap ON ap.id = pc.apartment_id
                    WHERE pc.person_id = %s AND ap.housing_array_id = %s
                    UNION
                    SELECT tr.payment_year AS year, tr.payment_month AS month
                    FROM time_reports tr
                    JOIN apartments ap ON ap.id = tr.apartment_id
                    WHERE tr.person_id = %s AND ap.housing_array_id = %s
                      AND tr.payment_year IS NOT NULL AND tr.payment_month IS NOT NULL
                    UNION
                    SELECT pc.payment_year AS year, pc.payment_month AS month
                    FROM payment_components pc
                    JOIN apartments ap ON ap.id = pc.apartment_id
                    WHERE pc.person_id = %s AND ap.housing_array_id = %s
                      AND pc.payment_year IS NOT NULL AND pc.payment_month IS NOT NULL
                ) combined
                ORDER BY year DESC, month DESC
            """, (
                person_id, housing_filter, person_id, housing_filter,
                person_id, housing_filter, person_id, housing_filter,
            ))
        else:
            # ללא סינון
            cursor.execute("""
                SELECT DISTINCT year, month FROM (
                    SELECT
                        CAST(EXTRACT(YEAR FROM date) AS INTEGER) as year,
                        CAST(EXTRACT(MONTH FROM date) AS INTEGER) as month
                    FROM time_reports
                    WHERE person_id = %s
                    UNION
                    SELECT
                        CAST(EXTRACT(YEAR FROM date) AS INTEGER) as year,
                        CAST(EXTRACT(MONTH FROM date) AS INTEGER) as month
                    FROM payment_components
                    WHERE person_id = %s
                    UNION
                    SELECT payment_year AS year, payment_month AS month
                    FROM time_reports
                    WHERE person_id = %s
                      AND payment_year IS NOT NULL AND payment_month IS NOT NULL
                    UNION
                    SELECT payment_year AS year, payment_month AS month
                    FROM payment_components
                    WHERE person_id = %s
                      AND payment_year IS NOT NULL AND payment_month IS NOT NULL
                ) combined
                ORDER BY year DESC, month DESC
            """, (person_id, person_id, person_id, person_id))
        rows = cursor.fetchall()
        return [(r[0], r[1]) for r in rows]
    except Exception as e:
        logger.warning(f"Error fetching months for person {person_id}: {e}")
        return []
    finally:
        cursor.close()


def get_payment_codes(conn):
    """Fetch payment codes sorted by display_order."""
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute(r"""
            SELECT * FROM payment_codes
            ORDER BY
              CASE WHEN merav_code ~ '^\d+$' THEN CAST(merav_code AS INTEGER) ELSE 999999 END ASC,
              display_order ASC NULLS LAST
        """)
        result = cursor.fetchall()
        cursor.close()
        return result
    except Exception as e:
        logger.error(f"Error fetching payment codes: {e}")
        return []


def ensure_sick_payment_code(conn):
    """
    מוודא שקוד מירב 319 לתשלום מחלה קיים בטבלת payment_codes.
    אם לא קיים, מוסיף אותו.
    """
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # בדיקה אם הקוד כבר קיים
        cursor.execute("""
            SELECT id FROM payment_codes WHERE internal_key = 'sick_payment'
        """)
        existing = cursor.fetchone()

        if not existing:
            # הוספת קוד מחלה חדש
            cursor.execute("""
                INSERT INTO payment_codes (internal_key, display_name, merav_code, display_order)
                VALUES ('sick_payment', 'תשלום מחלה', '319', 175)
            """)
            conn.commit()
            logger.info("Added sick_payment code (319) to payment_codes table")

        cursor.close()
    except Exception as e:
        logger.error(f"Error ensuring sick payment code: {e}")


def ensure_holiday_payment_code(conn):
    """
    מוודא שקוד מירב 254 לתשלום חג קיים בטבלת payment_codes.
    אם לא קיים, מוסיף אותו.
    """
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        cursor.execute("""
            SELECT id FROM payment_codes WHERE internal_key = 'holiday_payment'
        """)
        existing = cursor.fetchone()

        if not existing:
            cursor.execute("""
                INSERT INTO payment_codes (internal_key, display_name, merav_code, display_order)
                VALUES ('holiday_payment', 'תשלום חג', '254', 176)
            """)
            conn.commit()
            logger.info("Added holiday_payment code (254) to payment_codes table")

        cursor.close()
    except Exception as e:
        logger.error(f"Error ensuring holiday payment code: {e}")


def ensure_recovery_pay_code(conn):
    """
    מוודא שקוד מירב 38 לדמי הבראה קיים בטבלת payment_codes.
    אם לא קיים, מוסיף אותו.
    """
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        cursor.execute("""
            SELECT id FROM payment_codes WHERE internal_key = 'recovery_pay'
        """)
        existing = cursor.fetchone()

        if not existing:
            cursor.execute("""
                INSERT INTO payment_codes (internal_key, display_name, merav_code, display_order)
                VALUES ('recovery_pay', 'דמי הבראה', '38', 177)
            """)
            conn.commit()
            logger.info("Added recovery_pay code (38) to payment_codes table")

        cursor.close()
    except Exception as e:
        logger.error(f"Error ensuring recovery pay code: {e}")


def ensure_clothing_pay_code(conn):
    """מוודא שקוד מירב 107 לדמי ביגוד קיים בטבלת payment_codes."""
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT id FROM payment_codes WHERE internal_key = 'clothing_pay'")
        existing = cursor.fetchone()
        if not existing:
            cursor.execute("""
                INSERT INTO payment_codes (internal_key, display_name, merav_code, display_order)
                VALUES ('clothing_pay', 'דמי ביגוד', '107', 178)
            """)
            conn.commit()
            logger.info("Added clothing_pay code (107) to payment_codes table")
        cursor.close()
    except Exception as e:
        logger.error(f"Error ensuring clothing pay code: {e}")


def ensure_professional_support_code(conn):
    """
    מוודא שקוד מירב 243 לתומך מקצועי קיים בטבלת payment_codes.
    אם לא קיים, מוסיף אותו.
    """
    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        cursor.execute("""
            SELECT id FROM payment_codes WHERE internal_key = 'professional_support'
        """)
        existing = cursor.fetchone()

        if not existing:
            cursor.execute("""
                INSERT INTO payment_codes (internal_key, display_name, merav_code, display_order)
                VALUES ('professional_support', 'תומך מקצועי', '243', 180)
            """)
            conn.commit()
            logger.info("Added professional_support code (243) to payment_codes table")

        cursor.close()
    except Exception as e:
        logger.error(f"Error ensuring professional support code: {e}")


# =============================================================================
# Auto-Approval Functions
# =============================================================================


def auto_approve_substitute_travel(conn, person_id: int, start_date, end_date) -> int:
    """
    אישור אוטומטי של נסיעות מדריך מחליף.

    אם כל המשמרות של אותו מדריך באותו יום מאושרות,
    גם רכיב התשלום "נסיעות מדריך מחליף" לאותו יום מאושר אוטומטית.

    Args:
        conn: חיבור psycopg2 (raw, לא wrapper)
        person_id: מזהה המדריך
        start_date: תחילת טווח (date)
        end_date: סוף טווח (date)

    Returns:
        מספר רכיבי התשלום שאושרו
    """
    from core.constants import SUBSTITUTE_TRAVEL_TYPE_ID

    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        # שלב 1: מצא רכיבי "נסיעות מדריך מחליף" שטרם אושרו
        cursor.execute("""
            SELECT pc.id, pc.date
            FROM payment_components pc
            WHERE pc.person_id = %s
              AND pc.date >= %s AND pc.date < %s
              AND pc.component_type_id = %s
              AND pc.is_approved = false
        """, (person_id, start_date, end_date, SUBSTITUTE_TRAVEL_TYPE_ID))
        unapproved = cursor.fetchall()

        if not unapproved:
            return 0

        # שלב 2: לכל תאריך, בדוק אם כל המשמרות מאושרות
        approved_count = 0
        for pc in unapproved:
            cursor.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN is_approved THEN 1 ELSE 0 END) as approved
                FROM time_reports
                WHERE person_id = %s AND date = %s
            """, (person_id, pc["date"]))
            check = cursor.fetchone()

            if check["total"] > 0 and check["total"] == check["approved"]:
                cursor.execute("""
                    UPDATE payment_components
                    SET is_approved = true, approved_at = NOW()
                    WHERE id = %s
                """, (pc["id"],))
                approved_count += 1

        if approved_count > 0:
            conn.commit()
            logger.info(
                f"Auto-approved {approved_count} substitute travel payments "
                f"for person_id={person_id}"
            )

        return approved_count
    except Exception as e:
        logger.error(f"Error in auto_approve_substitute_travel: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return 0
    finally:
        cursor.close()


# =============================================================================
# Main Calculation Functions
# =============================================================================

def calculate_person_monthly_totals(
    conn,
    person_id: int,
    year: int,
    month: int,
    shabbat_cache: Dict[str, Dict[str, str]],
    minimum_wage: float = None
) -> Dict:
    """
    חישוב מדויק של סיכומים חודשיים לעובד.

    Uses the unified calculation logic from app_utils (get_daily_segments_data +
    aggregate_daily_segments_to_monthly) which is the source of truth for wage calculation.
    """
    from core.history import get_minimum_wage_for_month
    from core.database import PostgresConnection
    from app_utils import (
        aggregate_daily_segments_to_monthly,
        get_daily_segments_data,
    )

    # Get minimum wage for the specific month (historical)
    if minimum_wage is None:
        minimum_wage = get_minimum_wage_for_month(conn, year, month)

    # Wrap the raw psycopg2 connection in PostgresConnection for app_utils compatibility
    conn_wrapper = PostgresConnection(conn, use_pool=False)

    # Use the unified calculation from app_utils (source of truth)
    daily_segments, _ = get_daily_segments_data(
        conn_wrapper, person_id, year, month, shabbat_cache, minimum_wage
    )

    monthly_totals = aggregate_daily_segments_to_monthly(
        conn_wrapper, daily_segments, person_id, year, month, minimum_wage
    )

    return monthly_totals


# NOTE: _calculate_totals_from_data was removed as dead code.
# The calculation is now done exclusively through app_utils.get_daily_segments_data
# and app_utils.aggregate_daily_segments_to_monthly (source of truth).


def _apply_time_report_overrides(
    conn,
    rows,
    overrides: Dict[int, Optional[Dict]],
    *,
    housing_filter: Optional[int],
) -> List[Dict]:
    """Apply an in-memory report state without changing the database."""
    by_id = {int(row["id"]): dict(row) for row in rows}
    snapshots = [dict(value) for value in overrides.values() if value]
    apartment_ids = {int(row["apartment_id"]) for row in snapshots if row.get("apartment_id")}
    shift_ids = {int(row["shift_type_id"]) for row in snapshots if row.get("shift_type_id")}
    rate_type_ids = {
        int(row["rate_apartment_type_id"])
        for row in snapshots
        if row.get("rate_apartment_type_id")
    }

    apartments = {}
    if apartment_ids:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("""
            SELECT ap.id, ap.name AS apartment_name, ap.apartment_type_id,
                   ap.housing_array_id, at.hourly_wage_supplement,
                   at.name AS apartment_type_name, ha.name AS housing_array_name
            FROM apartments ap
            LEFT JOIN apartment_types at ON at.id = ap.apartment_type_id
            LEFT JOIN housing_arrays ha ON ha.id = ap.housing_array_id
            WHERE ap.id = ANY(%s)
        """, (list(apartment_ids),))
        apartments = {int(row["id"]): dict(row) for row in cursor.fetchall()}
        cursor.close()

    shifts = {}
    if shift_ids:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("""
            SELECT id, name AS shift_name, color AS shift_color,
                   is_special_hourly AS shift_is_special_hourly
            FROM shift_types WHERE id = ANY(%s)
        """, (list(shift_ids),))
        shifts = {int(row["id"]): dict(row) for row in cursor.fetchall()}
        cursor.close()

    rate_types = {}
    if rate_type_ids:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("""
            SELECT id, name AS rate_apartment_type_name,
                   hourly_wage_supplement AS rate_hourly_wage_supplement
            FROM apartment_types WHERE id = ANY(%s)
        """, (list(rate_type_ids),))
        rate_types = {int(row["id"]): dict(row) for row in cursor.fetchall()}
        cursor.close()

    for report_id, snapshot in overrides.items():
        report_id = int(report_id)
        if snapshot is None:
            by_id.pop(report_id, None)
            continue
        report = dict(snapshot)
        apartment = apartments.get(int(report.get("apartment_id") or 0), {})
        if housing_filter is not None and apartment.get("housing_array_id") != housing_filter:
            by_id.pop(report_id, None)
            continue
        report.update(apartment)
        report.update(shifts.get(int(report.get("shift_type_id") or 0), {}))
        report.update(rate_types.get(int(report.get("rate_apartment_type_id") or 0), {}))
        report["id"] = report_id
        by_id[report_id] = report

    return sorted(
        by_id.values(),
        key=lambda row: (row.get("person_id") or 0, row.get("date"), row.get("start_time") or ""),
    )


def _apply_payment_component_overrides(
    conn,
    rows,
    overrides: Dict[int, Optional[Dict]],
    *,
    housing_filter: Optional[int],
) -> List[Dict]:
    """Apply in-memory payment-component snapshots in the bulk summary shape."""
    by_id = {int(row["id"]): dict(row) for row in rows}
    snapshots = [dict(value) for value in overrides.values() if value]
    type_ids = {int(row["component_type_id"]) for row in snapshots if row.get("component_type_id")}
    apartment_ids = {int(row["apartment_id"]) for row in snapshots if row.get("apartment_id")}

    pension_by_type = {}
    if type_ids:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute(
            "SELECT id, for_pension FROM payment_component_types WHERE id = ANY(%s)",
            (list(type_ids),),
        )
        pension_by_type = {int(row["id"]): bool(row["for_pension"]) for row in cursor.fetchall()}
        cursor.close()

    housing_by_apartment = {}
    if apartment_ids:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute(
            "SELECT id, housing_array_id FROM apartments WHERE id = ANY(%s)",
            (list(apartment_ids),),
        )
        housing_by_apartment = {int(row["id"]): row["housing_array_id"] for row in cursor.fetchall()}
        cursor.close()

    for component_id, snapshot in overrides.items():
        component_id = int(component_id)
        if snapshot is None:
            by_id.pop(component_id, None)
            continue
        component = dict(snapshot)
        apartment_id = int(component.get("apartment_id") or 0)
        if housing_filter is not None and housing_by_apartment.get(apartment_id) != housing_filter:
            by_id.pop(component_id, None)
            continue
        quantity = float(component.get("quantity") or 0)
        rate = float(component.get("rate") or 0)
        type_id = int(component.get("component_type_id") or 0)
        by_id[component_id] = {
            "id": component_id,
            "person_id": component.get("person_id"),
            "total_amount": quantity * rate,
            "component_type_id": type_id,
            "payment_year": component.get("payment_year"),
            "payment_month": component.get("payment_month"),
            "for_pension": pension_by_type.get(type_id, bool(component.get("for_pension"))),
        }
    return list(by_id.values())


def calculate_monthly_summary(
    conn,
    year: int,
    month: int,
    *,
    person_ids: Optional[set[int]] = None,
    excluded_time_report_ids: Optional[set[int]] = None,
    excluded_payment_component_ids: Optional[set[int]] = None,
    time_report_overrides: Optional[Dict[int, Optional[Dict]]] = None,
    payment_component_overrides: Optional[Dict[int, Optional[Dict]]] = None,
    include_deferred_payment_items: bool = False,
    deferred_payment_period: Optional[tuple[int, int]] = None,
) -> Tuple[List[Dict], Dict]:
    """
    Calculate monthly summary for all active people.

    Uses the unified calculation logic from app_utils (get_daily_segments_data +
    aggregate_daily_segments_to_monthly) which is the source of truth for wage calculation.

    Optimized with bulk loading: all data is loaded in a few queries instead of per-person.
    """
    from core.history import (
        get_minimum_wage_for_month,
        get_all_person_statuses_for_month,
        get_all_person_statuses_for_dates,
        get_all_apartment_types_for_month,
        get_all_housing_rates_for_month,
    )
    from core.database import PostgresConnection
    from core.payment_period import filter_items_for_work_month
    from app_utils import (
        _fetch_weekday_overrides_for_month,
        aggregate_daily_segments_to_monthly,
        get_daily_segments_data,
    )
    from utils.utils import month_range_ts

    payment_codes = get_payment_codes(conn)
    housing_filter = get_housing_array_filter()
    excluded_time_report_ids = excluded_time_report_ids or set()
    excluded_payment_component_ids = excluded_payment_component_ids or set()
    requested_person_ids = {int(person_id) for person_id in (person_ids or set())}
    time_report_overrides = time_report_overrides or {}
    payment_component_overrides = payment_component_overrides or {}

    # שליפת אנשים - פעילים או בעלי פעילות בחודש, עם סינון מערך כשנדרש.
    activity_start = date(year, month, 1)
    activity_end = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    if housing_filter is not None and requested_person_ids:
        cursor.execute("""
            SELECT id, name, start_date, is_married, meirav_code, id_number, type
            FROM people
            WHERE housing_array_id = %s
              AND id = ANY(%s)
            ORDER BY name
        """, (housing_filter, list(requested_person_ids)))
    elif housing_filter is not None:
        cursor.execute("""
            SELECT id, name, start_date, is_married, meirav_code, id_number, type
            FROM people p
            WHERE p.housing_array_id = %s
              AND (
                p.is_active::integer = 1
                OR EXISTS (
                    SELECT 1 FROM time_reports tr
                    JOIN apartments ap ON ap.id = tr.apartment_id
                    WHERE tr.person_id = p.id AND tr.date >= %s AND tr.date < %s
                      AND ap.housing_array_id = %s
                )
                OR EXISTS (
                    SELECT 1 FROM payment_components pc
                    JOIN apartments ap ON ap.id = pc.apartment_id
                    WHERE pc.person_id = p.id AND pc.date >= %s AND pc.date < %s
                      AND ap.housing_array_id = %s
                )
              )
            ORDER BY name
        """, (
            housing_filter,
            activity_start, activity_end, housing_filter,
            activity_start, activity_end, housing_filter,
        ))
    elif requested_person_ids:
        cursor.execute("""
            SELECT id, name, start_date, is_married, meirav_code, id_number, type
            FROM people
            WHERE id = ANY(%s)
            ORDER BY name
        """, (list(requested_person_ids),))
    else:
        cursor.execute("""
            SELECT id, name, start_date, is_married, meirav_code, id_number, type
            FROM people p
            WHERE p.is_active::integer = 1
               OR EXISTS (
                    SELECT 1 FROM time_reports tr
                    WHERE tr.person_id = p.id AND tr.date >= %s AND tr.date < %s
               )
               OR EXISTS (
                    SELECT 1 FROM payment_components pc
                    WHERE pc.person_id = p.id AND pc.date >= %s AND pc.date < %s
               )
            ORDER BY name
        """, (activity_start, activity_end, activity_start, activity_end))
    people = cursor.fetchall()
    cursor.close()

    shabbat_cache = get_shabbat_times_cache(conn)
    minimum_wage = get_minimum_wage_for_month(conn, year, month)

    # Wrap the raw psycopg2 connection in PostgresConnection for app_utils compatibility
    conn_wrapper = PostgresConnection(conn, use_pool=False)

    # Pre-load all caches ONCE for the entire month (optimization)
    person_ids = [p["id"] for p in people]
    start_dt, end_dt = month_range_ts(year, month)
    start_date = start_dt.date()
    end_date = end_dt.date()
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    prev_start_dt, prev_end_dt = month_range_ts(prev_year, prev_month)
    prev_start_date = prev_start_dt.date()
    prev_end_date = prev_end_dt.date()

    person_status_cache = get_all_person_statuses_for_month(conn, person_ids, year, month)

    # ============================================================
    # BULK LOADING OPTIMIZATION - Load all data in single queries
    # ============================================================

    # 1. Load ALL time_reports for all people at once
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    if housing_filter is not None:
        cursor.execute("""
            SELECT tr.*,
                   st.name AS shift_name,
                   st.color AS shift_color,
                   st.is_special_hourly AS shift_is_special_hourly,
                   ap.name AS apartment_name,
                   ap.apartment_type_id,
                   ap.housing_array_id,
                   at.hourly_wage_supplement,
                   at.name AS apartment_type_name,
                   rate_at.name AS rate_apartment_type_name,
                   rate_at.hourly_wage_supplement AS rate_hourly_wage_supplement,
                   ha.name AS housing_array_name,
                   p.is_married,
                   p.name as person_name
            FROM time_reports tr
            LEFT JOIN shift_types st ON st.id = tr.shift_type_id
            JOIN apartments ap ON ap.id = tr.apartment_id
            LEFT JOIN apartment_types at ON at.id = ap.apartment_type_id
            LEFT JOIN apartment_types rate_at ON rate_at.id = tr.rate_apartment_type_id
            LEFT JOIN housing_arrays ha ON ha.id = ap.housing_array_id
            LEFT JOIN people p ON p.id = tr.person_id
            WHERE tr.person_id = ANY(%s) AND tr.date >= %s AND tr.date < %s
              AND ap.housing_array_id = %s
            ORDER BY tr.person_id, tr.date, tr.start_time
        """, (person_ids, start_date, end_date, housing_filter))
    else:
        cursor.execute("""
            SELECT tr.*,
                   st.name AS shift_name,
                   st.color AS shift_color,
                   st.is_special_hourly AS shift_is_special_hourly,
                   ap.name AS apartment_name,
                   ap.apartment_type_id,
                   ap.housing_array_id,
                   at.hourly_wage_supplement,
                   at.name AS apartment_type_name,
                   rate_at.name AS rate_apartment_type_name,
                   rate_at.hourly_wage_supplement AS rate_hourly_wage_supplement,
                   ha.name AS housing_array_name,
                   p.is_married,
                   p.name as person_name
            FROM time_reports tr
            LEFT JOIN shift_types st ON st.id = tr.shift_type_id
            LEFT JOIN apartments ap ON ap.id = tr.apartment_id
            LEFT JOIN apartment_types at ON at.id = ap.apartment_type_id
            LEFT JOIN apartment_types rate_at ON rate_at.id = tr.rate_apartment_type_id
            LEFT JOIN housing_arrays ha ON ha.id = ap.housing_array_id
            LEFT JOIN people p ON p.id = tr.person_id
            WHERE tr.person_id = ANY(%s) AND tr.date >= %s AND tr.date < %s
            ORDER BY tr.person_id, tr.date, tr.start_time
        """, (person_ids, start_date, end_date))
    all_reports = cursor.fetchall()
    if time_report_overrides:
        all_reports = _apply_time_report_overrides(
            conn,
            all_reports,
            time_report_overrides,
            housing_filter=housing_filter,
        )
    if excluded_time_report_ids:
        all_reports = [
            report for report in all_reports
            if report.get("id") not in excluded_time_report_ids
        ]
    all_reports = filter_items_for_work_month(
        all_reports,
        year,
        month,
        include_deferred=include_deferred_payment_items,
        deferred_payment_period=deferred_payment_period,
    )
    all_reports = [
        report for report in all_reports
        if not should_exclude_asd_completion_report(
            year,
            month,
            report.get("housing_array_id"),
            report.get("apartment_id"),
        )
    ]

    # Group reports by person_id
    reports_by_person = {}
    all_shift_ids = set()
    all_apartment_ids = set()
    all_report_dates = set()
    for r in all_reports:
        reports_by_person.setdefault(r["person_id"], []).append(r)
        if r.get("date"):
            all_report_dates.add(r["date"])
        if r["shift_type_id"]:
            all_shift_ids.add(r["shift_type_id"])
        if r["apartment_id"]:
            all_apartment_ids.add(r["apartment_id"])
    person_status_date_cache = get_all_person_statuses_for_dates(
        conn,
        person_ids,
        list(all_report_dates),
    )

    # 1b. Load previous-month reports once for carryover + sick continuity
    prev_reports_by_person = {}
    prev_month_sick_dates_by_person = {}
    if person_ids:
        if housing_filter is not None:
            cursor.execute("""
                SELECT tr.person_id, tr.date, tr.start_time, tr.end_time, tr.shift_type_id, tr.apartment_id,
                       tr.payment_year, tr.payment_month,
                       tr.rate_apartment_type_id,
                       st.name AS shift_name,
                       ap.housing_array_id, at.hourly_wage_supplement,
                       rate_at.name AS rate_apartment_type_name,
                       rate_at.hourly_wage_supplement AS rate_hourly_wage_supplement,
                       p.is_married
                FROM time_reports tr
                LEFT JOIN shift_types st ON st.id = tr.shift_type_id
                JOIN apartments ap ON ap.id = tr.apartment_id
                LEFT JOIN apartment_types at ON at.id = ap.apartment_type_id
                LEFT JOIN apartment_types rate_at ON rate_at.id = tr.rate_apartment_type_id
                LEFT JOIN people p ON p.id = tr.person_id
                WHERE tr.person_id = ANY(%s) AND tr.date >= %s AND tr.date < %s
                  AND ap.housing_array_id = %s
                ORDER BY tr.person_id, tr.date, tr.start_time
            """, (person_ids, prev_start_date, prev_end_date, housing_filter))
        else:
            cursor.execute("""
                SELECT tr.person_id, tr.date, tr.start_time, tr.end_time, tr.shift_type_id, tr.apartment_id,
                       tr.payment_year, tr.payment_month,
                       tr.rate_apartment_type_id,
                       st.name AS shift_name,
                       ap.housing_array_id, at.hourly_wage_supplement,
                       rate_at.name AS rate_apartment_type_name,
                       rate_at.hourly_wage_supplement AS rate_hourly_wage_supplement,
                       p.is_married
                FROM time_reports tr
                LEFT JOIN shift_types st ON st.id = tr.shift_type_id
                LEFT JOIN apartments ap ON ap.id = tr.apartment_id
                LEFT JOIN apartment_types at ON at.id = ap.apartment_type_id
                LEFT JOIN apartment_types rate_at ON rate_at.id = tr.rate_apartment_type_id
                LEFT JOIN people p ON p.id = tr.person_id
                WHERE tr.person_id = ANY(%s) AND tr.date >= %s AND tr.date < %s
                ORDER BY tr.person_id, tr.date, tr.start_time
            """, (person_ids, prev_start_date, prev_end_date))

        previous_reports = filter_items_for_work_month(
            cursor.fetchall(),
            prev_year,
            prev_month,
            include_deferred=include_deferred_payment_items,
            deferred_payment_period=deferred_payment_period,
        )
        for r in previous_reports:
            prev_reports_by_person.setdefault(r["person_id"], []).append(r)
            if r["shift_type_id"]:
                all_shift_ids.add(r["shift_type_id"])
            if "מחלה" in (r["shift_name"] or ""):
                prev_month_sick_dates_by_person.setdefault(r["person_id"], []).append(r["date"])

    # 2. Load ALL shift_time_segments for all used shifts
    segments_by_shift = {}
    if all_shift_ids:
        placeholders = ",".join(["%s"] * len(all_shift_ids))
        cursor.execute(f"""
            SELECT seg.*, st.name AS shift_name
            FROM shift_time_segments seg
            JOIN shift_types st ON st.id = seg.shift_type_id
            WHERE seg.shift_type_id IN ({placeholders})
            ORDER BY seg.shift_type_id, seg.order_index, seg.id
        """, tuple(all_shift_ids))
        for seg in cursor.fetchall():
            segments_by_shift.setdefault(seg["shift_type_id"], []).append(seg)

    # 3. Load ALL payment_components for all people at once
    month_start = start_dt
    month_end = end_dt
    if housing_filter is not None:
        cursor.execute("""
            SELECT pc.id, pc.person_id, (pc.quantity * pc.rate) as total_amount, pc.component_type_id,
                   pc.payment_year, pc.payment_month,
                   COALESCE(pct.for_pension, FALSE) as for_pension
            FROM payment_components pc
            JOIN apartments ap ON ap.id = pc.apartment_id
            LEFT JOIN payment_component_types pct ON pc.component_type_id = pct.id
            WHERE pc.person_id = ANY(%s) AND pc.date >= %s AND pc.date < %s
              AND ap.housing_array_id = %s
        """, (person_ids, month_start, month_end, housing_filter))
    else:
        cursor.execute("""
            SELECT pc.id, pc.person_id, (pc.quantity * pc.rate) as total_amount, pc.component_type_id,
                   pc.payment_year, pc.payment_month,
                   COALESCE(pct.for_pension, FALSE) as for_pension
            FROM payment_components pc
            LEFT JOIN payment_component_types pct ON pc.component_type_id = pct.id
            WHERE pc.person_id = ANY(%s) AND pc.date >= %s AND pc.date < %s
        """, (person_ids, month_start, month_end))
    all_payment_comps = cursor.fetchall()
    if payment_component_overrides:
        all_payment_comps = _apply_payment_component_overrides(
            conn,
            all_payment_comps,
            payment_component_overrides,
            housing_filter=housing_filter,
        )
    if excluded_payment_component_ids:
        all_payment_comps = [
            pc for pc in all_payment_comps
            if pc.get("id") not in excluded_payment_component_ids
        ]
    all_payment_comps = filter_items_for_work_month(
        all_payment_comps,
        year,
        month,
        include_deferred=include_deferred_payment_items,
        deferred_payment_period=deferred_payment_period,
    )

    # Group payment_components by person_id
    payment_comps_by_person = {}
    for pc in all_payment_comps:
        payment_comps_by_person.setdefault(pc["person_id"], []).append(pc)

    cursor.close()

    # 4. Load apartment type cache and housing rates cache
    apartment_type_cache = get_all_apartment_types_for_month(conn, list(all_apartment_ids), year, month)
    housing_rates_cache = get_all_housing_rates_for_month(conn, year, month)
    prev_month_housing_rates_cache = get_all_housing_rates_for_month(conn, prev_year, prev_month)
    weekday_overrides = (
        _fetch_weekday_overrides_for_month(conn, year, month)
        if (year, month) >= (2026, 2)
        else None
    )

    # Build person start_date map (already have this data from people query)
    person_start_dates = {p["id"]: p["start_date"] for p in people}
    person_types = {p["id"]: p["type"] for p in people}
    person_is_married = {p["id"]: bool(p["is_married"]) for p in people}

    from core.holiday_payment import calculate_holiday_payments
    from core.recovery_pay import (
        apply_recovery_pay_to_totals,
        calculate_recovery_pay_for_person,
    )
    from core.clothing_pay import (
        apply_clothing_pay_to_totals,
        calculate_clothing_pay_for_person,
    )

    holiday_payments = calculate_holiday_payments(
        conn, year, month, shabbat_cache, minimum_wage,
        all_reports=all_reports,
        person_types=person_types,
        person_start_dates=person_start_dates,
        person_is_married=person_is_married,
        housing_filter=housing_filter,
    )

    # ============================================================
    # END BULK LOADING - Now process each person with cached data
    # ============================================================

    summary_data = []
    grand_totals = {code["internal_key"]: 0 for code in payment_codes}
    grand_totals.update({
        "payment": 0, "standby_payment": 0, "travel": 0, "professional_support": 0, "extras": 0, "total_payment": 0,
        "calc150_shabbat_100": 0, "calc150_shabbat_50": 0,
        "holiday_payment": 0,
        "recovery_pay": 0,
        "clothing_pay": 0,
        "vacation_payment": 0, "vacation_minutes": 0,
        "sick_payment": 0, "sick_minutes": 0,  # מחלה
        "rounded_total": 0  # סה"כ מעוגל - סכום השורות עם עיגול
    })

    for p in people:
        pid = p["id"]

        # Use the unified calculation from app_utils with ALL pre-loaded data
        daily_segments, _ = get_daily_segments_data(
            conn_wrapper, pid, year, month, shabbat_cache, minimum_wage,
            person_status_cache=person_status_cache,
            person_status_date_cache=person_status_date_cache.get(pid, {}),
            apartment_type_cache=apartment_type_cache,
            housing_rates_cache=housing_rates_cache,
            preloaded_reports=reports_by_person.get(pid, []),
            preloaded_segments=segments_by_shift,
            preloaded_weekday_overrides=weekday_overrides,
            preloaded_prev_month_sick_dates=prev_month_sick_dates_by_person.get(pid, []),
            preloaded_prev_month_reports=prev_reports_by_person.get(pid, []),
            preloaded_prev_month_housing_rates_cache=prev_month_housing_rates_cache,
            include_deferred_payment_items=include_deferred_payment_items,
            deferred_payment_period=deferred_payment_period,
        )

        monthly_totals = aggregate_daily_segments_to_monthly(
            conn_wrapper, daily_segments, pid, year, month, minimum_wage,
            preloaded_payment_comps=payment_comps_by_person.get(pid, []),
            person_start_date=person_start_dates.get(pid),
            housing_filter=housing_filter,
            include_deferred_payment_items=include_deferred_payment_items,
            deferred_payment_period=deferred_payment_period,
        )

        hp_data = holiday_payments.get(pid)
        if hp_data and hp_data["amount"] > 0:
            hp = hp_data["amount"]
            monthly_totals["holiday_payment"] = hp
            monthly_totals["holiday_payment_count"] = hp_data["count"]
            monthly_totals["holiday_payment_rate"] = hp_data["rate"]
            hp_details = hp_data.get("details", []) or []
            monthly_totals["holiday_payment_details"] = hp_details
            monthly_totals["holiday_payment_hours"] = (
                round(sum(float(item.get("hours") or 0) for item in hp_details), 2)
                if hp_details
                else (round(hp / round(minimum_wage, 2), 2) if minimum_wage else 0)
            )
            hp_rounded = round(round(hp, 2), 1)
            monthly_totals["total_payment"] += hp_rounded
            monthly_totals["gesher_total"] += hp_rounded
            monthly_totals["display_total"] += hp_rounded
            monthly_totals["rounded_total"] += hp_rounded

        recovery_data = calculate_recovery_pay_for_person(
            conn_wrapper,
            pid,
            year,
            month,
            current_month_totals=monthly_totals,
            housing_filter=housing_filter,
        )
        apply_recovery_pay_to_totals(monthly_totals, recovery_data)

        clothing_data = calculate_clothing_pay_for_person(
            conn_wrapper,
            pid,
            year,
            month,
            current_month_totals=monthly_totals,
            housing_filter=housing_filter,
        )
        apply_clothing_pay_to_totals(monthly_totals, clothing_data)

        # הצג מדריכים עם שעות עבודה או תשלום כלשהו
        # (כשיש סינון לפי מערך דיור, גם השעות וגם רכיבי התשלום כבר מסוננים)
        should_include = monthly_totals.get("total_payment", 0) > 0 or monthly_totals.get("total_hours", 0) > 0

        if should_include:
            summary_data.append({
                "name": p["name"],
                "person_id": p["id"],
                "merav_code": p["meirav_code"],
                "id_number": p.get("id_number"),
                "totals": monthly_totals,
            })

            grand_totals["payment"] += monthly_totals.get("payment", 0)
            grand_totals["total_payment"] += monthly_totals.get("total_payment", 0)
            grand_totals["rounded_total"] += monthly_totals.get("rounded_total", 0)

            for k, v in monthly_totals.items():
                if k in grand_totals and isinstance(v, (int, float)) and k not in ("payment", "total_payment", "rounded_total"):
                    grand_totals[k] += v

    # עיגול סה"כ כללי למניעת שגיאות floating point
    grand_totals["rounded_total"] = round(grand_totals["rounded_total"], 2)
    grand_totals["total_payment"] = round(grand_totals["total_payment"], 2)
    grand_totals["payment"] = round(grand_totals["payment"], 2)

    return summary_data, grand_totals

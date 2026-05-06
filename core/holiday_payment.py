"""
חישוב תשלום חג למדריכים קבועים.

מדריך קבוע שעבד בחודש של החג ולא עבד בחג עצמו זכאי לתשלום חג.
- מדריך קבוע אחד בדירה → משמרת חול שלמה
- 2+ מדריכים קבועים בדירה → כל אחד חוץ מזה שעבד בחג → חצי משמרת חול
- דירות ASD (תפקוד גבוה/נמוך) → תמיד משמרת שלמה, גם עם 2+ מדריכים
- שעות משמרת חול = לפי shift_time_overrides של הדירה (מ-02/2026), fallback לסגמנטים של 103
"""

import logging
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Set, Tuple

import psycopg2.extras

from core.constants import (
    BERESHIT_APT_TYPE,
    HOLIDAY_PAY_MIN_SENIORITY_MONTHS,
    KALANIYOT_APT_TYPE,
    MAX_CANCELLED_STANDBY_DEDUCTION,
    NIGHT_SHIFT_ID,
    PERMANENT_EMPLOYEE_TYPE,
    SPECIAL_ABSENCE_PAYMENT_APT_TYPES,
    WEEKDAY_SHIFT_TYPE_ID,
    is_asd_housing_array,
)
from core.time_utils import span_minutes

logger = logging.getLogger(__name__)

# Cache for global weekday shift work minutes (fallback when no override)
_weekday_shift_work_minutes_cache: int | None = None


def ensure_holiday_payment_assignments_table(conn) -> None:
    """יצירת טבלת שיוך מדריכים קבועים לתשלום חג לפי חודש ודירה."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS holiday_payment_apartment_guides (
            id SERIAL PRIMARY KEY,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
            apartment_id INTEGER NOT NULL REFERENCES apartments(id),
            guide_1_id INTEGER NULL REFERENCES people(id),
            guide_2_id INTEGER NULL REFERENCES people(id),
            guide_2_no_holiday_payment BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE (year, month, apartment_id),
            CHECK (guide_1_id IS NULL OR guide_2_id IS NULL OR guide_1_id <> guide_2_id)
        )
    """)
    cursor.execute("""
        ALTER TABLE holiday_payment_apartment_guides
        ADD COLUMN IF NOT EXISTS guide_2_no_holiday_payment BOOLEAN NOT NULL DEFAULT false
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_holiday_payment_apartment_guides_period
        ON holiday_payment_apartment_guides (year, month)
    """)
    conn.commit()
    cursor.close()


def ensure_special_days_holiday_payment_column(conn) -> None:
    """הוספת סימון ב-special_days לימים שנספרים גם כרכיב תשלום חג 254."""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            ALTER TABLE special_days
            ADD COLUMN IF NOT EXISTS counts_as_holiday_payment BOOLEAN NOT NULL DEFAULT false
        """)
        conn.commit()
    except Exception:
        conn.rollback()
        logger.debug("Could not ensure special_days.counts_as_holiday_payment", exc_info=True)
    finally:
        cursor.close()


def _dict_rows(rows) -> list[dict]:
    return [dict(row) for row in rows]


def _get_relevant_apartments(
    conn, year: int, month: int, housing_filter: int | None = None
) -> list[dict]:
    """כל הדירות לתצוגת ניהול, עם סינון מערך דיור אם קיים."""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    if housing_filter is not None:
        cursor.execute("""
            SELECT id, name, housing_array_id
            FROM apartments
            WHERE housing_array_id = %s
            ORDER BY name
        """, (housing_filter,))
    else:
        cursor.execute("""
            SELECT id, name, housing_array_id
            FROM apartments
            ORDER BY name
        """)
    rows = _dict_rows(cursor.fetchall())
    cursor.close()
    return rows


def _load_saved_assignments(
    conn, year: int, month: int, housing_filter: int | None = None
) -> dict[int, dict]:
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    if housing_filter is not None:
        cursor.execute("""
            SELECT hpag.apartment_id, hpag.guide_1_id, hpag.guide_2_id,
                   hpag.guide_2_no_holiday_payment
            FROM holiday_payment_apartment_guides hpag
            JOIN apartments ap ON ap.id = hpag.apartment_id
            WHERE hpag.year = %s AND hpag.month = %s AND ap.housing_array_id = %s
        """, (year, month, housing_filter))
    else:
        cursor.execute("""
            SELECT apartment_id, guide_1_id, guide_2_id, guide_2_no_holiday_payment
            FROM holiday_payment_apartment_guides
            WHERE year = %s AND month = %s
        """, (year, month))
    result = {row["apartment_id"]: dict(row) for row in cursor.fetchall()}
    cursor.close()
    return result


def _load_holiday_payment_suggestions(
    conn, year: int, month: int, housing_filter: int | None = None
) -> dict[int, list[dict]]:
    """הצעת מדריכים לפי מספר ימי עבודה בדירה בחודש, בלי חופשה/מחלה."""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    start_date = date(year, month, 1)
    end_date = date(year, month, monthrange(year, month)[1]) + timedelta(days=1)
    params: list[Any] = [start_date, end_date]
    housing_sql = ""
    if housing_filter is not None:
        housing_sql = "AND ap.housing_array_id = %s"
        params.append(housing_filter)
    cursor.execute(f"""
        SELECT tr.apartment_id, tr.person_id, p.name AS person_name,
               COUNT(DISTINCT tr.date) AS work_days,
               COUNT(*) AS shifts_count
        FROM time_reports tr
        JOIN people p ON p.id = tr.person_id
        JOIN apartments ap ON ap.id = tr.apartment_id
        LEFT JOIN shift_types st ON st.id = tr.shift_type_id
        WHERE tr.date >= %s AND tr.date < %s
          AND p.type = %s
          AND COALESCE(st.name, '') NOT ILIKE '%%חופשה%%'
          AND COALESCE(st.name, '') NOT ILIKE '%%מחלה%%'
          {housing_sql}
        GROUP BY tr.apartment_id, tr.person_id, p.name
        ORDER BY tr.apartment_id, work_days DESC, shifts_count DESC, p.name
    """, (*params[:2], PERMANENT_EMPLOYEE_TYPE, *params[2:]))
    suggestions: dict[int, list[dict]] = {}
    for row in cursor.fetchall():
        suggestions.setdefault(row["apartment_id"], []).append({
            "person_id": row["person_id"],
            "person_name": row["person_name"],
            "work_days": int(row["work_days"] or 0),
            "shifts_count": int(row["shifts_count"] or 0),
        })
    cursor.close()
    return suggestions


def get_holiday_payment_setup(
    conn,
    year: int,
    month: int,
    shabbat_cache: Dict[str, Dict[str, str]],
    housing_filter: int | None = None,
) -> dict:
    """נתוני החלון לניהול תשלום חג לפי דירות."""
    ensure_holiday_payment_assignments_table(conn)
    holiday_dates, _holiday_work_dates = get_holiday_payment_dates_in_month(conn, year, month, shabbat_cache)
    if not holiday_dates:
        return {
            "has_holidays": False,
            "is_configured": True,
            "holiday_dates": [],
            "rows": [],
            "guides": [],
        }

    apartments = _get_relevant_apartments(conn, year, month, housing_filter)
    saved = _load_saved_assignments(conn, year, month, housing_filter)
    suggestions = _load_holiday_payment_suggestions(conn, year, month, housing_filter)

    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    if housing_filter is not None:
        cursor.execute("""
            SELECT id, name
            FROM people
            WHERE is_active::integer = 1
              AND type = %s
              AND housing_array_id = %s
            ORDER BY name
        """, (PERMANENT_EMPLOYEE_TYPE, housing_filter))
    else:
        cursor.execute("""
            SELECT id, name
            FROM people
            WHERE is_active::integer = 1
              AND type = %s
            ORDER BY name
        """, (PERMANENT_EMPLOYEE_TYPE,))
    guides = _dict_rows(cursor.fetchall())
    cursor.close()

    rows = []
    for apt in apartments:
        apt_id = apt["id"]
        row = saved.get(apt_id)
        suggested = suggestions.get(apt_id, [])
        default_guide_1 = suggested[0]["person_id"] if suggested else None
        default_guide_2 = (
            suggested[1]["person_id"]
            if len(suggested) > 1 and suggested[1].get("work_days", 0) >= 7
            else None
        )
        rows.append({
            "apartment_id": apt_id,
            "apartment_name": apt["name"],
            "housing_array_id": apt.get("housing_array_id"),
            "guide_1_id": row["guide_1_id"] if row else default_guide_1,
            "guide_2_id": row["guide_2_id"] if row else default_guide_2,
            "guide_2_no_holiday_payment": bool(row.get("guide_2_no_holiday_payment")) if row else False,
            "is_saved": row is not None,
            "suggestions": suggested[:4],
        })

    return {
        "has_holidays": True,
        "is_configured": len(saved) >= len(apartments) if apartments else True,
        "holiday_dates": [d.isoformat() for d in holiday_dates],
        "rows": rows,
        "guides": guides,
    }


def save_holiday_payment_setup(
    conn,
    year: int,
    month: int,
    rows: list[dict],
    housing_filter: int | None = None,
) -> None:
    """שמירת שיוך מדריכים קבועים לתשלום חג לכל דירה בחודש."""
    ensure_holiday_payment_assignments_table(conn)
    allowed_apartments = {r["id"] for r in _get_relevant_apartments(conn, year, month, housing_filter)}
    selected_guide_ids: Set[int] = set()
    for row in rows:
        for key in ("guide_1_id", "guide_2_id"):
            guide_id = row.get(key)
            if guide_id:
                selected_guide_ids.add(int(guide_id))
    if selected_guide_ids:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if housing_filter is not None:
            cursor.execute("""
                SELECT id
                FROM people
                WHERE id = ANY(%s)
                  AND is_active::integer = 1
                  AND type = %s
                  AND housing_array_id = %s
            """, (list(selected_guide_ids), PERMANENT_EMPLOYEE_TYPE, housing_filter))
        else:
            cursor.execute("""
                SELECT id
                FROM people
                WHERE id = ANY(%s)
                  AND is_active::integer = 1
                  AND type = %s
            """, (list(selected_guide_ids), PERMANENT_EMPLOYEE_TYPE))
        allowed_guides = {row["id"] for row in cursor.fetchall()}
        cursor.close()
        if selected_guide_ids - allowed_guides:
            raise ValueError("נבחר מדריך שלא שייך למערך הדיור או אינו מדריך קבוע פעיל")

    cursor = conn.cursor()
    for row in rows:
        apartment_id = int(row.get("apartment_id") or 0)
        if apartment_id not in allowed_apartments:
            continue
        guide_1_id = row.get("guide_1_id") or None
        guide_2_id = row.get("guide_2_id") or None
        guide_2_no_holiday_payment = bool(row.get("guide_2_no_holiday_payment"))
        guide_1_id = int(guide_1_id) if guide_1_id else None
        guide_2_id = int(guide_2_id) if guide_2_id else None
        if guide_1_id is None:
            guide_2_id = None
            guide_2_no_holiday_payment = False
        if guide_2_id is not None:
            guide_2_no_holiday_payment = False
        if guide_1_id is not None and guide_1_id == guide_2_id:
            raise ValueError("לא ניתן לבחור אותו מדריך פעמיים באותה דירה")
        cursor.execute("""
            INSERT INTO holiday_payment_apartment_guides
                (year, month, apartment_id, guide_1_id, guide_2_id, guide_2_no_holiday_payment)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (year, month, apartment_id)
            DO UPDATE SET
                guide_1_id = EXCLUDED.guide_1_id,
                guide_2_id = EXCLUDED.guide_2_id,
                guide_2_no_holiday_payment = EXCLUDED.guide_2_no_holiday_payment,
                updated_at = NOW()
        """, (year, month, apartment_id, guide_1_id, guide_2_id, guide_2_no_holiday_payment))
    conn.commit()
    cursor.close()


def _get_weekday_shift_work_minutes(conn) -> int:
    """
    סכום דקות העבודה של משמרת חול (103) מטבלת shift_time_segments.

    Returns:
        סך דקות work (ללא כוננות). ברירת מחדל 480 (8 שעות) אם אין סגמנטים.
    """
    global _weekday_shift_work_minutes_cache
    if _weekday_shift_work_minutes_cache is not None:
        return _weekday_shift_work_minutes_cache

    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("""
        SELECT start_time, end_time
        FROM shift_time_segments
        WHERE shift_type_id = %s AND segment_type = 'work'
        ORDER BY order_index
    """, (WEEKDAY_SHIFT_TYPE_ID,))
    rows = cursor.fetchall()
    cursor.close()

    total = 0
    for row in rows:
        start, end = span_minutes(row["start_time"], row["end_time"])
        total += end - start

    _weekday_shift_work_minutes_cache = total if total > 0 else 480
    return _weekday_shift_work_minutes_cache


def _get_apartment_work_minutes(
    conn, year: int, month: int, apartment_ids: Set[int],
    apartment_housing_map: Dict[int, int | None],
) -> Dict[int, int]:
    """
    חישוב דקות עבודה לכל דירה — לפי override ספציפי או fallback גלובלי.

    מ-02/2026: שימוש ב-shift_time_overrides (דירה > מערך דיור > גלובלי).
    לפני כן: ערך גלובלי לכולם.

    Returns:
        {apartment_id: work_minutes}
    """
    global_minutes = _get_weekday_shift_work_minutes(conn)

    if (year, month) < (2026, 2):
        return {apt_id: global_minutes for apt_id in apartment_ids}

    from app_utils import _fetch_weekday_overrides, _build_sick_vacation_segments

    apt_overrides, ha_defaults = _fetch_weekday_overrides(conn)

    result: Dict[int, int] = {}
    for apt_id in apartment_ids:
        # עדיפות: דירה ספציפית > מערך דיור > גלובלי
        override = apt_overrides.get(apt_id)
        if override is None:
            ha_id = apartment_housing_map.get(apt_id)
            if ha_id:
                override = ha_defaults.get(ha_id)

        if override:
            segs = _build_sick_vacation_segments(override[0], override[1])
            total = 0
            for s in segs:
                s_min, e_min = span_minutes(s["start_time"], s["end_time"])
                total += e_min - s_min
            result[apt_id] = total if total > 0 else global_minutes
        else:
            result[apt_id] = global_minutes

    return result


def _get_shift_segments(conn, shift_type_id: int) -> list[dict]:
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("""
        SELECT id, start_time, end_time, segment_type
        FROM shift_time_segments
        WHERE shift_type_id = %s
        ORDER BY order_index, id
    """, (shift_type_id,))
    rows = _dict_rows(cursor.fetchall())
    cursor.close()
    return rows


def _get_special_absence_shift_id(apartment_type_id: int | None) -> int | None:
    if apartment_type_id == KALANIYOT_APT_TYPE:
        return WEEKDAY_SHIFT_TYPE_ID
    if apartment_type_id == BERESHIT_APT_TYPE:
        return NIGHT_SHIFT_ID
    return None


def _calculate_special_holiday_day_pay(
    conn,
    *,
    apartment_type_id: int,
    housing_array_id: int | None,
    is_married: bool,
    minimum_wage: float,
    year: int,
    month: int,
    apartment_id: int | None = None,
    apartment_housing_map: Dict[int, int | None] | None = None,
) -> float:
    """תשלום חג מיוחד לסוגי דירה כלניות/בראשית: עבודה + כוננות פחות 70."""
    shift_type_id = _get_special_absence_shift_id(apartment_type_id)
    if shift_type_id is None:
        return 0.0

    from app_utils import (
        _fetch_weekday_overrides,
        _build_weekday_shift_overrides,
        _calculate_special_absence_segment_payment,
    )
    from core.history import get_all_housing_rates_for_month

    segments = None
    if shift_type_id == WEEKDAY_SHIFT_TYPE_ID and apartment_id is not None and (year, month) >= (2026, 2):
        apt_overrides, ha_defaults = _fetch_weekday_overrides(conn)
        base_segments = _get_shift_segments(conn, WEEKDAY_SHIFT_TYPE_ID)
        full_overrides = _build_weekday_shift_overrides(
            {apartment_id},
            apartment_housing_map or {},
            apt_overrides,
            ha_defaults,
            base_segments,
        )
        segments = full_overrides.get(apartment_id)
    if not segments:
        segments = _get_shift_segments(conn, shift_type_id)

    housing_rates_cache = get_all_housing_rates_for_month(conn, year, month)
    total = 0.0
    for seg in segments:
        start, end = span_minutes(seg["start_time"], seg["end_time"])
        pay, _paid_minutes, _rate = _calculate_special_absence_segment_payment(
            conn,
            segment_type=seg.get("segment_type") or "work",
            duration=end - start,
            shift_type_id=shift_type_id,
            segment_id=seg.get("id"),
            apartment_type_id=apartment_type_id,
            housing_array_id=housing_array_id,
            is_married=is_married,
            minimum_wage=minimum_wage,
            year=year,
            month=month,
            housing_rates_cache=housing_rates_cache,
        )
        total += pay
    return round(total, 2)


def get_holiday_dates_in_month(
    year: int, month: int, shabbat_cache: Dict[str, Dict[str, str]]
) -> List[date]:
    """
    מציאת כל ימי החג בחודש נתון (ללא שבתות רגילות).

    חג מזוהה לפי שדה holiday ב-shabbat_cache.
    כל יום חג חייב להיות עם רשומה ישירה ב-shabbat_times (כולל חגים דו-יומיים כמו ר"ה).
    """
    holidays: List[date] = []
    days_in_month = monthrange(year, month)[1]

    for day_num in range(1, days_in_month + 1):
        d = date(year, month, day_num)
        day_str = d.strftime("%Y-%m-%d")
        day_info = shabbat_cache.get(day_str)

        if day_info and day_info.get("holiday"):
            # שבת חול המועד - לא יו"ט, לא זכאי לתשלום חג
            if d.weekday() == 5 and "חול המועד" in day_info["holiday"]:
                continue
            holidays.append(d)

    return holidays


def _get_special_holiday_payment_windows(conn, year: int, month: int) -> dict[date, Set[date]]:
    """
    ימים מ-special_days שמסומנים כנספרים לתשלום חג.

    מפתח המפה הוא תאריך הזכאות לתשלום. עבור חלון שחוצה חצות (לדוגמה יום העצמאות
    20:00-20:00) תאריך הזכאות הוא end_date, אך עבודה בכל אחד מתאריכי החלון מונעת
    תשלום חג כפול.
    """
    windows: dict[date, Set[date]] = {}
    for pay_date, details in _get_special_holiday_payment_window_details(conn, year, month).items():
        for row in details:
            start_date = row["start_date"]
            end_date = row["end_date"]
            work_dates = {
                start_date + timedelta(days=offset)
                for offset in range((end_date - start_date).days + 1)
            }
            windows.setdefault(pay_date, set()).update(work_dates)

    return windows


def _get_special_holiday_payment_window_details(conn, year: int, month: int) -> dict[date, list[dict[str, Any]]]:
    """חלונות special_days שנספרים לתשלום חג, כולל שעות התחלה וסיום."""
    start_month = date(year, month, 1)
    end_month = date(year, month, monthrange(year, month)[1])
    windows: dict[date, list[dict[str, Any]]] = {}

    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        try:
            cursor.execute("""
                SELECT start_date, start_time, end_date, end_time
                FROM special_days
                WHERE is_active = true
                  AND counts_as_holiday_payment = true
                  AND start_date <= %s
                  AND end_date >= %s
            """, (end_month, start_month))
        except (psycopg2.errors.UndefinedTable, psycopg2.errors.UndefinedColumn):
            conn.rollback()
            return {}

        for row in cursor.fetchall():
            start_date = row.get("start_date") if hasattr(row, "get") else row["start_date"]
            end_date = row.get("end_date") if hasattr(row, "get") else row["end_date"]
            start_time = row.get("start_time") if hasattr(row, "get") else row["start_time"]
            end_time = row.get("end_time") if hasattr(row, "get") else row["end_time"]
            if hasattr(start_date, "date"):
                start_date = start_date.date()
            if hasattr(end_date, "date"):
                end_date = end_date.date()
            if not isinstance(start_date, date) or not isinstance(end_date, date):
                continue
            if start_time is None:
                start_time = time.min
            if end_time is None:
                end_time = time.max

            pay_date = end_date if start_date != end_date else start_date
            if pay_date.year != year or pay_date.month != month:
                continue

            windows.setdefault(pay_date, []).append({
                "start_date": start_date,
                "start_time": start_time,
                "end_date": end_date,
                "end_time": end_time,
            })
    finally:
        cursor.close()

    return windows


def _normal_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _parse_report_time(value: Any) -> time | None:
    if value is None:
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        try:
            hour, minute = value.split(":", 1)
            return time(int(hour), int(minute[:2]))
        except (TypeError, ValueError):
            return None
    return None


def _shift_defined_end_minutes(conn, shift_type_id: int | None, report_start: str | None) -> int | None:
    """סוף המשמרת המוגדר לפי מקטעי המשמרת, מנורמל לציר הדיווח."""
    if not shift_type_id or not report_start:
        return None
    segments = _get_shift_segments(conn, shift_type_id)
    if not segments:
        return None

    report_start_min, _ = span_minutes(report_start, report_start)
    last_end = None
    for seg in segments:
        seg_start, seg_end = span_minutes(seg["start_time"], seg["end_time"])
        if seg_start < report_start_min and report_start_min >= 12 * 60 and seg_start < 8 * 60:
            seg_start += 24 * 60
            seg_end += 24 * 60
        if last_end is not None:
            while seg_start < last_end:
                seg_start += 24 * 60
                seg_end += 24 * 60
        last_end = seg_end if last_end is None else max(last_end, seg_end)
    return last_end


def _previous_day_carryover_within_defined_shift(conn, report: dict, pay_date: date, window_start: datetime) -> bool:
    """עבודה מהיום שלפני החג לא מבטלת חג אם היא רק המשך רגיל של המשמרת."""
    report_date = _normal_date(report.get("date"))
    start_time = report.get("start_time")
    end_time = report.get("end_time")
    if report_date is None or report_date >= pay_date or not start_time or not end_time:
        return False

    start_obj = _parse_report_time(start_time)
    if start_obj is None:
        return False
    report_start_dt = datetime.combine(report_date, start_obj)
    if report_start_dt >= window_start:
        return False

    report_start_min, report_end_min = span_minutes(start_time, end_time)
    defined_end = _shift_defined_end_minutes(conn, report.get("shift_type_id"), start_time)
    if defined_end is None:
        return False
    return report_end_min <= defined_end


def _report_overlaps_special_holiday_window(conn, report: dict, pay_date: date, window: dict[str, Any]) -> bool:
    """האם דיווח עבודה אמור לבטל תשלום חג מיוחד לפי חפיפה שעותית."""
    report_date = _normal_date(report.get("date"))
    start_obj = _parse_report_time(report.get("start_time"))
    end_obj = _parse_report_time(report.get("end_time"))
    if report_date is None or start_obj is None or end_obj is None:
        return report_date in {
            window["start_date"] + timedelta(days=offset)
            for offset in range((window["end_date"] - window["start_date"]).days + 1)
        }

    report_start = datetime.combine(report_date, start_obj)
    report_end = datetime.combine(report_date, end_obj)
    if report_end <= report_start:
        report_end += timedelta(days=1)

    window_start = datetime.combine(window["start_date"], window["start_time"])
    window_end = datetime.combine(window["end_date"], window["end_time"])
    if window_end <= window_start:
        window_end += timedelta(days=1)

    if report_start >= window_end or report_end <= window_start:
        return False

    if _previous_day_carryover_within_defined_shift(conn, report, pay_date, window_start):
        return False

    return True


def get_holiday_payment_dates_in_month(
    conn,
    year: int,
    month: int,
    shabbat_cache: Dict[str, Dict[str, str]],
) -> tuple[List[date], dict[date, Set[date]]]:
    """
    תאריכי זכאות לתשלום חג, כולל חגים מ-shabbat_times וימים מיוחדים שסומנו לכך.

    Returns:
        (holiday_dates, work_dates_by_holiday_date)
    """
    work_dates_by_holiday_date: dict[date, Set[date]] = {}
    holiday_dates = get_holiday_dates_in_month(year, month, shabbat_cache)
    for holiday_date in holiday_dates:
        work_dates_by_holiday_date[holiday_date] = {holiday_date}

    special_windows = _get_special_holiday_payment_windows(conn, year, month)
    for pay_date, work_dates in special_windows.items():
        if pay_date not in work_dates_by_holiday_date:
            holiday_dates.append(pay_date)
            work_dates_by_holiday_date[pay_date] = set()
        work_dates_by_holiday_date[pay_date].update(work_dates)

    holiday_dates = sorted(set(holiday_dates))
    return holiday_dates, work_dates_by_holiday_date


def _has_sufficient_seniority(
    start_date: date | None, year: int, month: int,
) -> bool:
    """בדיקה שלמדריך יש ותק של 3+ חודשים נכון לתחילת חודש הדיווח."""
    if start_date is None:
        return False
    if hasattr(start_date, "date"):
        start_date = start_date.date()
    ref_month = month - HOLIDAY_PAY_MIN_SENIORITY_MONTHS
    ref_year = year
    if ref_month <= 0:
        ref_month += 12
        ref_year -= 1
    cutoff = date(ref_year, ref_month, 1)
    return start_date <= cutoff


def calculate_holiday_payments(
    conn,
    year: int,
    month: int,
    shabbat_cache: Dict[str, Dict[str, str]],
    minimum_wage: float,
    all_reports: list | None = None,
    person_types: Dict[int, str] | None = None,
    person_start_dates: Dict[int, date] | None = None,
    person_is_married: Dict[int, bool] | None = None,
    housing_filter: int | None = None,
) -> Dict[int, float]:
    """
    חישוב תשלום חג לכל המדריכים הקבועים.

    Args:
        conn: חיבור DB (raw psycopg2)
        year, month: חודש החישוב
        shabbat_cache: מטמון זמני שבת/חג
        minimum_wage: שכר מינימום לשעה
        all_reports: רשימת דיווחים (מ-batch path), או None לשליפה מ-DB
        person_types: {person_id: "permanent"/"substitute"}, או None לשליפה מ-DB
        person_start_dates: {person_id: start_date}, או None לשליפה מ-DB
        housing_filter: סינון לפי מערך דיור

    Returns:
        {person_id: {"amount": סכום, "count": מספר ימי חג, "rate": תעריף ליום}}
    """
    holiday_dates, holiday_work_dates = get_holiday_payment_dates_in_month(conn, year, month, shabbat_cache)
    if not holiday_dates:
        return {}
    regular_holiday_dates = set(get_holiday_dates_in_month(year, month, shabbat_cache))
    special_holiday_windows = _get_special_holiday_payment_window_details(conn, year, month)

    # שליפת דיווחים ונתוני מדריכים אם לא סופקו
    if all_reports is None or person_types is None or person_start_dates is None:
        all_reports, person_types, person_start_dates, person_is_married = _load_reports_and_types(
            conn, year, month, housing_filter
        )
    if person_is_married is None:
        person_is_married = {}

    saved_assignments: dict[int, dict] = {}
    try:
        saved_assignments = _load_saved_assignments(conn, year, month, housing_filter)
    except Exception as exc:
        logger.debug("Could not load holiday payment assignments: %s", exc)

    # בניית מיפויים
    # {apartment_id: set of permanent person_ids who worked this month}
    apt_permanent_guides: Dict[int, Set[int]] = {}
    # {(apartment_id, holiday_date): set of person_ids who worked that day}
    apt_holiday_workers: Dict[Tuple[int, date], Set[int]] = {}
    # מיפוי דירה -> מערך דיור (לצורך overrides + זיהוי ASD)
    apartment_housing_map: Dict[int, int | None] = {}
    apartment_type_map: Dict[int, int | None] = {}

    holiday_dates_set = set(holiday_dates)

    for r in all_reports:
        person_id = r["person_id"]
        apartment_id = r.get("apartment_id")
        report_date = r.get("date")

        if not apartment_id or not report_date:
            continue

        # Ensure report_date is a date object
        if hasattr(report_date, "date"):
            report_date = report_date.date()

        # מיפוי דירה -> מערך דיור
        ha_id = r.get("housing_array_id")
        if apartment_id not in apartment_housing_map and ha_id:
            apartment_housing_map[apartment_id] = ha_id
        if apartment_id not in apartment_type_map:
            apartment_type_map[apartment_id] = r.get("rate_apartment_type_id") or r.get("apartment_type_id")

        # רק מדריכים קבועים
        if person_types.get(person_id) != PERMANENT_EMPLOYEE_TYPE:
            continue

        # מיפוי: קבועים שעבדו בדירה החודש
        apt_permanent_guides.setdefault(apartment_id, set()).add(person_id)

        # מיפוי: מי עבד ביום חג
        for holiday_date in holiday_dates_set:
            worked_on_regular_holiday = (
                holiday_date in regular_holiday_dates and report_date == holiday_date
            )
            worked_in_special_window = any(
                _report_overlaps_special_holiday_window(conn, r, holiday_date, window)
                for window in special_holiday_windows.get(holiday_date, [])
            )
            worked_on_legacy_holiday_date = (
                not special_holiday_windows.get(holiday_date)
                and report_date in holiday_work_dates.get(holiday_date, {holiday_date})
            )
            if worked_on_regular_holiday or worked_in_special_window or worked_on_legacy_holiday_date:
                key = (apartment_id, holiday_date)
                apt_holiday_workers.setdefault(key, set()).add(person_id)

    unpaid_slot_by_apartment: Dict[int, bool] = {}
    if saved_assignments:
        assigned_guides: Dict[int, Set[int]] = {}
        assigned_person_ids: Set[int] = set()
        for apartment_id, row in saved_assignments.items():
            guide_ids = {
                gid for gid in (row.get("guide_1_id"), row.get("guide_2_id"))
                if gid
            }
            assigned_guides[apartment_id] = set(guide_ids)
            unpaid_slot_by_apartment[apartment_id] = bool(
                row.get("guide_1_id") and row.get("guide_2_no_holiday_payment")
            )
            assigned_person_ids.update(guide_ids)

        missing_people = [
            pid for pid in assigned_person_ids
            if pid not in person_types or pid not in person_start_dates
        ]
        if missing_people:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute("""
                SELECT id, type, start_date, is_married
                FROM people
                WHERE id = ANY(%s)
            """, (missing_people,))
            for row in cursor.fetchall():
                person_types[row["id"]] = row["type"]
                person_is_married[row["id"]] = bool(row["is_married"])
                if row["start_date"]:
                    person_start_dates[row["id"]] = row["start_date"]
            cursor.close()

        # הגדרה שמורה היא המקור הקובע רק לדירה הספציפית שלה.
        # דירה בלי שורה שמורה ממשיכה להשתמש בזיהוי אוטומטי לפי דיווחים.
        # דירה עם שורה שמורה ושני שדות ריקים לא תשלם חג.
        for apartment_id, guide_ids in assigned_guides.items():
            apt_permanent_guides[apartment_id] = {
                pid for pid in guide_ids
                if person_types.get(pid) == PERMANENT_EMPLOYEE_TYPE
            }

        missing_apartment_ids = [
            apt_id for apt_id in apt_permanent_guides
            if apt_id not in apartment_housing_map
        ]
        if missing_apartment_ids:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cursor.execute("""
                SELECT id, housing_array_id, apartment_type_id
                FROM apartments
                WHERE id = ANY(%s)
            """, (missing_apartment_ids,))
            for row in cursor.fetchall():
                apartment_housing_map[row["id"]] = row["housing_array_id"]
                apartment_type_map[row["id"]] = row["apartment_type_id"]
            cursor.close()

    if not apt_permanent_guides:
        return {}

    # חישוב דקות עבודה לכל דירה (לפי override / fallback גלובלי)
    all_apt_ids = set(apt_permanent_guides.keys())
    apt_work_minutes = _get_apartment_work_minutes(
        conn, year, month, all_apt_ids, apartment_housing_map
    )

    # חישוב תשלום חג
    result: Dict[int, dict] = {}

    for apartment_id, permanent_guides in apt_permanent_guides.items():
        num_permanent = len(permanent_guides) + (1 if unpaid_slot_by_apartment.get(apartment_id) else 0)
        if num_permanent == 0:
            continue

        work_minutes = apt_work_minutes.get(apartment_id, 480)
        apartment_type_id = apartment_type_map.get(apartment_id)
        full_shift_pay = round(work_minutes / 60, 2) * round(minimum_wage, 2)
        half_shift_pay = round(work_minutes / 2 / 60, 2) * round(minimum_wage, 2)

        for holiday_date in holiday_dates:
            workers_on_holiday = apt_holiday_workers.get(
                (apartment_id, holiday_date), set()
            )
            eligible = permanent_guides - workers_on_holiday

            if not eligible:
                continue

            # מערך דיור ASD → תמיד משמרת שלמה
            is_asd = is_asd_housing_array(apartment_housing_map.get(apartment_id))
            pay = full_shift_pay if num_permanent == 1 or is_asd else half_shift_pay

            for person_id in eligible:
                if not _has_sufficient_seniority(
                    person_start_dates.get(person_id), year, month,
                ):
                    continue
                if apartment_type_id in SPECIAL_ABSENCE_PAYMENT_APT_TYPES:
                    special_full_pay = _calculate_special_holiday_day_pay(
                        conn,
                        apartment_type_id=apartment_type_id,
                        housing_array_id=apartment_housing_map.get(apartment_id),
                        is_married=bool(person_is_married.get(person_id)),
                        minimum_wage=minimum_wage,
                        year=year,
                        month=month,
                        apartment_id=apartment_id,
                        apartment_housing_map=apartment_housing_map,
                    )
                    pay = special_full_pay if num_permanent == 1 or is_asd else round(special_full_pay / 2, 2)
                if person_id not in result:
                    result[person_id] = {"amount": 0.0, "count": 0, "rate": pay}
                result[person_id]["amount"] += pay
                result[person_id]["count"] += 1

    return result


def _load_reports_and_types(
    conn, year: int, month: int, housing_filter: int | None
) -> Tuple[list, Dict[int, str], Dict[int, date], Dict[int, bool]]:
    """שליפת דיווחים, סוגי מדריכים ותאריכי התחלה מה-DB (ל-single-guide path)."""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    start_date = date(year, month, 1)
    days_in_month = monthrange(year, month)[1]
    end_date = date(year, month, days_in_month) + timedelta(days=1)

    if housing_filter is not None:
        cursor.execute("""
            SELECT tr.person_id, tr.apartment_id, tr.date,
                   tr.start_time, tr.end_time, tr.shift_type_id,
                   ap.housing_array_id, ap.apartment_type_id,
                   tr.rate_apartment_type_id
            FROM time_reports tr
            JOIN apartments ap ON ap.id = tr.apartment_id
            WHERE tr.date >= %s AND tr.date < %s
              AND ap.housing_array_id = %s
        """, (start_date, end_date, housing_filter))
    else:
        cursor.execute("""
            SELECT tr.person_id, tr.apartment_id, tr.date,
                   tr.start_time, tr.end_time, tr.shift_type_id,
                   ap.housing_array_id, ap.apartment_type_id,
                   tr.rate_apartment_type_id
            FROM time_reports tr
            LEFT JOIN apartments ap ON ap.id = tr.apartment_id
            WHERE tr.date >= %s AND tr.date < %s
        """, (start_date, end_date))
    reports = cursor.fetchall()

    # שליפת סוגי מדריכים ותאריכי התחלה
    person_ids = list({r["person_id"] for r in reports})
    person_types: Dict[int, str] = {}
    person_start_dates: Dict[int, date] = {}
    person_is_married: Dict[int, bool] = {}
    if person_ids:
        cursor.execute("""
            SELECT id, type, start_date, is_married FROM people WHERE id = ANY(%s)
        """, (person_ids,))
        for row in cursor.fetchall():
            person_types[row["id"]] = row["type"]
            person_is_married[row["id"]] = bool(row["is_married"])
            if row["start_date"]:
                person_start_dates[row["id"]] = row["start_date"]


    cursor.close()
    return reports, person_types, person_start_dates, person_is_married

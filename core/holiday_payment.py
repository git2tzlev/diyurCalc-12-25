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
from datetime import date, timedelta
from typing import Dict, List, Set, Tuple

import psycopg2.extras

from core.constants import (
    HOLIDAY_PAY_MIN_SENIORITY_MONTHS,
    PERMANENT_EMPLOYEE_TYPE,
    WEEKDAY_SHIFT_TYPE_ID,
    is_asd_housing_array,
)
from core.time_utils import span_minutes

logger = logging.getLogger(__name__)

# Cache for global weekday shift work minutes (fallback when no override)
_weekday_shift_work_minutes_cache: int | None = None


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
            holidays.append(d)

    return holidays


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
    holiday_dates = get_holiday_dates_in_month(year, month, shabbat_cache)
    if not holiday_dates:
        return {}

    # שליפת דיווחים ונתוני מדריכים אם לא סופקו
    if all_reports is None or person_types is None or person_start_dates is None:
        all_reports, person_types, person_start_dates = _load_reports_and_types(
            conn, year, month, housing_filter
        )

    # בניית מיפויים
    # {apartment_id: set of permanent person_ids who worked this month}
    apt_permanent_guides: Dict[int, Set[int]] = {}
    # {(apartment_id, holiday_date): set of person_ids who worked that day}
    apt_holiday_workers: Dict[Tuple[int, date], Set[int]] = {}
    # מיפוי דירה -> מערך דיור (לצורך overrides + זיהוי ASD)
    apartment_housing_map: Dict[int, int | None] = {}

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

        # רק מדריכים קבועים
        if person_types.get(person_id) != PERMANENT_EMPLOYEE_TYPE:
            continue

        # מיפוי: קבועים שעבדו בדירה החודש
        apt_permanent_guides.setdefault(apartment_id, set()).add(person_id)

        # מיפוי: מי עבד ביום חג
        if report_date in holiday_dates_set:
            key = (apartment_id, report_date)
            apt_holiday_workers.setdefault(key, set()).add(person_id)

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
        num_permanent = len(permanent_guides)
        if num_permanent == 0:
            continue

        work_minutes = apt_work_minutes.get(apartment_id, 480)
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
                if person_id not in result:
                    result[person_id] = {"amount": 0.0, "count": 0, "rate": pay}
                result[person_id]["amount"] += pay
                result[person_id]["count"] += 1

    return result


def _load_reports_and_types(
    conn, year: int, month: int, housing_filter: int | None
) -> Tuple[list, Dict[int, str], Dict[int, date]]:
    """שליפת דיווחים, סוגי מדריכים ותאריכי התחלה מה-DB (ל-single-guide path)."""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    start_date = date(year, month, 1)
    days_in_month = monthrange(year, month)[1]
    end_date = date(year, month, days_in_month) + timedelta(days=1)

    if housing_filter is not None:
        cursor.execute("""
            SELECT tr.person_id, tr.apartment_id, tr.date,
                   ap.housing_array_id, ap.apartment_type_id
            FROM time_reports tr
            JOIN apartments ap ON ap.id = tr.apartment_id
            WHERE tr.date >= %s AND tr.date < %s
              AND ap.housing_array_id = %s
        """, (start_date, end_date, housing_filter))
    else:
        cursor.execute("""
            SELECT tr.person_id, tr.apartment_id, tr.date,
                   ap.housing_array_id, ap.apartment_type_id
            FROM time_reports tr
            LEFT JOIN apartments ap ON ap.id = tr.apartment_id
            WHERE tr.date >= %s AND tr.date < %s
        """, (start_date, end_date))
    reports = cursor.fetchall()

    # שליפת סוגי מדריכים ותאריכי התחלה
    person_ids = list({r["person_id"] for r in reports})
    person_types: Dict[int, str] = {}
    person_start_dates: Dict[int, date] = {}
    if person_ids:
        cursor.execute("""
            SELECT id, type, start_date FROM people WHERE id = ANY(%s)
        """, (person_ids,))
        for row in cursor.fetchall():
            person_types[row["id"]] = row["type"]
            if row["start_date"]:
                person_start_dates[row["id"]] = row["start_date"]

    cursor.close()
    return reports, person_types, person_start_dates

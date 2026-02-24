"""
חישוב תשלום חג למדריכים קבועים.

מדריך קבוע שעבד בחודש של החג ולא עבד בחג עצמו זכאי לתשלום חג.
- מדריך קבוע אחד בדירה → משמרת חול שלמה
- 2+ מדריכים קבועים בדירה → כל אחד חוץ מזה שעבד בחג → חצי משמרת חול
- שעות משמרת חול = סכום דקות work מסגמנטים של shift_type_id=103
"""

import logging
from calendar import monthrange
from datetime import date, timedelta
from typing import Dict, List, Set, Tuple

import psycopg2.extras

from core.constants import (
    PERMANENT_EMPLOYEE_TYPE,
    WEEKDAY_SHIFT_TYPE_ID,
)
from core.time_utils import span_minutes

logger = logging.getLogger(__name__)

# Cache for weekday shift work minutes (loaded once per process)
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


def get_holiday_dates_in_month(
    year: int, month: int, shabbat_cache: Dict[str, Dict[str, str]]
) -> List[date]:
    """
    מציאת כל ימי החג בחודש נתון (ללא שבתות רגילות).

    חג מזוהה לפי שדה holiday ב-shabbat_cache.
    חגים דו-יומיים: גם היום הראשון (שאין לו רשומה ישירה) נכלל.
    """
    holidays: List[date] = []
    days_in_month = monthrange(year, month)[1]

    for day_num in range(1, days_in_month + 1):
        d = date(year, month, day_num)
        day_str = d.strftime("%Y-%m-%d")
        day_info = shabbat_cache.get(day_str)

        # בדיקה ישירה: יש רשומה עם holiday ליום הזה
        if day_info and day_info.get("holiday"):
            holidays.append(d)
            continue

        # בדיקה לחג דו-יומי: היום הראשון אין לו רשומה,
        # אבל למחר יש רשומה עם holiday ו-enter (ה-enter מכסה את אתמול)
        tomorrow = d + timedelta(days=1)
        tomorrow_str = tomorrow.strftime("%Y-%m-%d")
        tomorrow_info = shabbat_cache.get(tomorrow_str)

        if (
            tomorrow_info
            and tomorrow_info.get("holiday")
            and tomorrow_info.get("enter")
            and d.weekday() != 4  # לא שישי (שזה ערב שבת רגיל)
            and d.weekday() != 5  # לא שבת
        ):
            # מחר הוא חג עם enter → היום הוא יום חג ראשון (או ערב חג)
            # נבדוק: אם מחר הוא היום האחרון של חג דו-יומי,
            # היום הוא היום הראשון של החג
            # נוודא שהיום עצמו הוא לא ערב חג (לפני הכניסה)
            # ע"י בדיקה שאין רשומה עם exit ליום הזה (כלומר הוא לא חג בפני עצמו)
            if not (day_info and day_info.get("exit")):
                holidays.append(d)

    return holidays


def calculate_holiday_payments(
    conn,
    year: int,
    month: int,
    shabbat_cache: Dict[str, Dict[str, str]],
    minimum_wage: float,
    all_reports: list | None = None,
    person_types: Dict[int, str] | None = None,
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
        housing_filter: סינון לפי מערך דיור

    Returns:
        {person_id: סכום תשלום חג}
    """
    holiday_dates = get_holiday_dates_in_month(year, month, shabbat_cache)
    if not holiday_dates:
        return {}

    shift_work_minutes = _get_weekday_shift_work_minutes(conn)
    full_shift_pay = round(shift_work_minutes / 60, 2) * round(minimum_wage, 2)
    half_shift_pay = round(shift_work_minutes / 2 / 60, 2) * round(minimum_wage, 2)

    # שליפת דיווחים ונתוני מדריכים אם לא סופקו
    if all_reports is None or person_types is None:
        all_reports, person_types = _load_reports_and_types(
            conn, year, month, housing_filter
        )

    # בניית מיפויים
    # {apartment_id: set of permanent person_ids who worked this month}
    apt_permanent_guides: Dict[int, Set[int]] = {}
    # {(apartment_id, holiday_date): set of person_ids who worked that day}
    apt_holiday_workers: Dict[Tuple[int, date], Set[int]] = {}

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

        # רק מדריכים קבועים
        if person_types.get(person_id) != PERMANENT_EMPLOYEE_TYPE:
            continue

        # מיפוי: קבועים שעבדו בדירה החודש
        apt_permanent_guides.setdefault(apartment_id, set()).add(person_id)

        # מיפוי: מי עבד ביום חג
        if report_date in holiday_dates_set:
            key = (apartment_id, report_date)
            apt_holiday_workers.setdefault(key, set()).add(person_id)

    # חישוב תשלום חג
    result: Dict[int, float] = {}

    for apartment_id, permanent_guides in apt_permanent_guides.items():
        num_permanent = len(permanent_guides)
        if num_permanent == 0:
            continue

        for holiday_date in holiday_dates:
            workers_on_holiday = apt_holiday_workers.get(
                (apartment_id, holiday_date), set()
            )
            eligible = permanent_guides - workers_on_holiday

            if not eligible:
                continue

            pay = full_shift_pay if num_permanent == 1 else half_shift_pay

            for person_id in eligible:
                result[person_id] = result.get(person_id, 0) + pay

    return result


def _load_reports_and_types(
    conn, year: int, month: int, housing_filter: int | None
) -> Tuple[list, Dict[int, str]]:
    """שליפת דיווחים וסוגי מדריכים מה-DB (ל-single-guide path)."""
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    start_date = date(year, month, 1)
    days_in_month = monthrange(year, month)[1]
    end_date = date(year, month, days_in_month) + timedelta(days=1)

    if housing_filter is not None:
        cursor.execute("""
            SELECT tr.person_id, tr.apartment_id, tr.date
            FROM time_reports tr
            JOIN apartments ap ON ap.id = tr.apartment_id
            WHERE tr.date >= %s AND tr.date < %s
              AND ap.housing_array_id = %s
        """, (start_date, end_date, housing_filter))
    else:
        cursor.execute("""
            SELECT person_id, apartment_id, date
            FROM time_reports
            WHERE date >= %s AND date < %s
        """, (start_date, end_date))
    reports = cursor.fetchall()

    # שליפת סוגי מדריכים
    person_ids = list({r["person_id"] for r in reports})
    person_types: Dict[int, str] = {}
    if person_ids:
        cursor.execute("""
            SELECT id, type FROM people WHERE id = ANY(%s)
        """, (person_ids,))
        for row in cursor.fetchall():
            person_types[row["id"]] = row["type"]

    cursor.close()
    return reports, person_types

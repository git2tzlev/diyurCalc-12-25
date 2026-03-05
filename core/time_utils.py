"""
Time utilities and Shabbat logic for DiyurCalc application.
Contains time conversion functions, Shabbat time detection, and related constants.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, date
from typing import Tuple, Dict, Any

import psycopg2.extras

from core.config import config
from utils.cache_manager import cache

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

# Time constants (in minutes)
MINUTES_PER_HOUR = 60
MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR  # 1440

# Work hour thresholds (in minutes)
REGULAR_HOURS_LIMIT = 8 * MINUTES_PER_HOUR   # 480 - First 8 hours at 100%
OVERTIME_125_LIMIT = 10 * MINUTES_PER_HOUR   # 600 - Hours 9-10 at 125%
# Beyond 600 minutes = 150%

# Work day boundaries
WORK_DAY_START_MINUTES = 8 * MINUTES_PER_HOUR  # 480 = 08:00

# Shabbat defaults (when not found in DB)
SHABBAT_ENTER_DEFAULT = 16 * MINUTES_PER_HOUR  # 960 = 16:00 on Friday
SHABBAT_EXIT_DEFAULT = 22 * MINUTES_PER_HOUR   # 1320 = 22:00 on Saturday

# Weekday indices (Python's weekday())
FRIDAY = 4
SATURDAY = 5

# Use LOCAL_TZ from config
LOCAL_TZ = config.LOCAL_TZ


# =============================================================================
# Date/Time Conversion Functions
# =============================================================================

def to_local_date(ts: int | datetime | date) -> date:
    """Convert epoch timestamp, datetime, or date object to local date."""
    from zoneinfo import ZoneInfo

    if isinstance(ts, date) and not isinstance(ts, datetime):
        # Already a date object (PostgreSQL can return date directly)
        return ts
    if isinstance(ts, datetime):
        # PostgreSQL returns datetime objects directly
        if ts.tzinfo is None:
            # Assume UTC if no timezone
            return ts.replace(tzinfo=ZoneInfo("UTC")).astimezone(LOCAL_TZ).date()
        return ts.astimezone(LOCAL_TZ).date()
    # SQLite returns epoch timestamps
    return datetime.fromtimestamp(ts, LOCAL_TZ).date()


def parse_hhmm(value: str) -> Tuple[int, int]:
    """Return (hours, minutes) integers from 'HH:MM'."""
    h, m = value.split(":")
    return int(h), int(m)


def span_minutes(start_str: str, end_str: str) -> Tuple[int, int]:
    """Return start/end minutes-from-midnight, handling overnight end < start."""
    sh, sm = parse_hhmm(start_str)
    eh, em = parse_hhmm(end_str)
    start = sh * MINUTES_PER_HOUR + sm
    end = eh * MINUTES_PER_HOUR + em
    if end <= start:
        end += MINUTES_PER_DAY
    return start, end


def minutes_to_time_str(minutes: int) -> str:
    """Convert minutes from midnight to HH:MM format (handles >24h wrapping)."""
    day_minutes = minutes % MINUTES_PER_DAY
    h = day_minutes // MINUTES_PER_HOUR
    m = day_minutes % MINUTES_PER_HOUR
    return f"{h:02d}:{m:02d}"


# =============================================================================
# Shabbat Cache and Detection
# =============================================================================

SHABBAT_CACHE_KEY = "shabbat_times_cache"
SHABBAT_CACHE_TTL = 86400  # 24 hours


def get_shabbat_times_cache(conn) -> Dict[str, Dict[str, Any]]:
    """
    Load Shabbat times from DB into a dictionary with 24-hour caching.
    Key: Date string (YYYY-MM-DD) representing the day.
    Value: {'enter': HH:MM, 'exit': HH:MM, 'parsha': str, 'holiday': str}
    """
    # Check cache first
    cached_result = cache.get(SHABBAT_CACHE_KEY)
    if cached_result is not None:
        return cached_result

    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT shabbat_date, candle_lighting, havdalah, parsha, holiday_name FROM shabbat_times")
        rows = cursor.fetchall()
        result = {}
        for r in rows:
            if r["shabbat_date"]:
                result[r["shabbat_date"]] = {
                    "enter": r["candle_lighting"],
                    "exit": r["havdalah"],
                    "parsha": r["parsha"],
                    "holiday": r["holiday_name"]
                }
        cursor.close()

        # Store in cache
        cache.set(SHABBAT_CACHE_KEY, result, SHABBAT_CACHE_TTL)
        return result
    except Exception as e:
        logger.warning(f"Failed to load shabbat times cache: {e}")
        return {}


def _find_holiday_record_for_date(day_date: date, shabbat_cache: Dict[str, Dict[str, str]]) -> Tuple[date | None, Dict[str, str] | None]:
    """
    חיפוש רשומת חג/שבת שמכסה את התאריך הנתון.

    בחגים דו-יומיים (כמו ראש השנה), רק היום האחרון יש לו רשומה בטבלה.
    למשל: ראש השנה 23-24.9 → רק ל-24.9 יש רשומה עם enter (שמתייחס לערב 22.9) ו-exit.

    הפונקציה מחפשת עד 3 ימים קדימה כדי למצוא רשומה שה-enter שלה מכסה את היום הנתון.
    מדלגת על שבתות רגילות (שבת = היום עם הרשומה הוא Saturday).

    Returns:
        (target_date, target_info) או (None, None) אם לא נמצא
    """
    # חיפוש עד 3 ימים קדימה לרשומת חג
    for days_ahead in range(4):
        check_date = day_date + timedelta(days=days_ahead)
        check_str = check_date.strftime("%Y-%m-%d")
        check_info = shabbat_cache.get(check_str)

        if check_info and check_info.get("enter"):
            # מצאנו רשומה עם enter
            # נדלג על שבתות רגילות (היום עם הרשומה הוא שבת = שבת רגילה)
            if check_date.weekday() == SATURDAY:
                continue  # זו שבת רגילה, לא חג
            # מצאנו חג
            return (check_date, check_info)

    return (None, None)


def _is_two_day_yom_tov(holiday_date: date) -> bool:
    """
    בדיקה אם החג בתאריך הנתון הוא חג דו-יומי (בישראל).

    בישראל, רק ראש השנה (א-ב תשרי) הוא חג דו-יומי.
    רשומת החג הדו-יומי בטבלה היא ליום האחרון (ב תשרי).
    """
    try:
        from convertdate import hebrew
        hy, hm, hd = hebrew.from_gregorian(
            holiday_date.year, holiday_date.month, holiday_date.day
        )
        # ראש השנה יום ב = ב תשרי (חודש 7, יום 2)
        return hm == 7 and hd == 2
    except Exception:
        return False


def _get_shabbat_boundaries(day_date: date, shabbat_cache: Dict[str, Dict[str, str]]) -> Tuple[int, int]:
    """
    קבלת זמני כניסה/יציאה של שבת או חג בדקות מחצות הערב.

    הלוגיקה:
    - אם יש enter (candle_lighting) ליום → זה ערב שבת או ערב חג
    - אם יש exit (havdalah) ליום → זה שבת או חג
    - לחגים דו-יומיים: הרשומה היא רק ליום האחרון, אבל ה-enter מתייחס לערב הראשון

    Returns:
        (enter_minute, exit_minute) כאשר exit יחסי לחצות הערב (יכול להיות >1440).
        מחזיר (-1, -1) אם היום אינו שבת/חג/ערב שבת/ערב חג.
    """
    weekday = day_date.weekday()
    day_str = day_date.strftime("%Y-%m-%d")
    day_info = shabbat_cache.get(day_str)

    # בדיקה אם יש נתונים בטבלה ליום הזה
    has_enter = day_info and day_info.get("enter")
    has_exit = day_info and day_info.get("exit")

    target_day = None

    # קביעת סוג היום ומציאת היום המקודש (שבת/חג)
    if weekday == FRIDAY:
        if day_info and day_info.get("holiday"):
            # יום שישי שהוא חג - היום המקודש הוא היום עצמו
            target_day = day_date
        else:
            # יום שישי רגיל - היום המקודש הוא מחר (שבת)
            target_day = day_date + timedelta(days=1)
    elif weekday == SATURDAY or has_exit:
        # שבת או חג (יש לו havdalah) - היום המקודש הוא היום עצמו
        target_day = day_date
    elif has_enter:
        # ערב חג (יש candle_lighting אבל לא יום שישי) - היום המקודש הוא מחר
        target_day = day_date + timedelta(days=1)
    else:
        # בדיקה אם מחר יש חג (היום הוא ערב חג)
        tomorrow = day_date + timedelta(days=1)
        tomorrow_str = tomorrow.strftime("%Y-%m-%d")
        tomorrow_info = shabbat_cache.get(tomorrow_str)
        if tomorrow_info and tomorrow_info.get("enter"):
            # מחר יש רשומה עם enter - היום הוא ערב חג
            # נבדוק אם זה חג חד-יומי או דו-יומי
            # אם יש גם exit לרשומה של מחר - החג מסתיים מחר (חד-יומי)
            # אם אין exit לרשומה של מחר - צריך לחפש את ה-exit ביום אחר (דו-יומי)
            target_day = tomorrow
        else:
            # בדיקה לחג דו-יומי:
            # חגים דו-יומיים ידועים: ראש השנה
            # בחג דו-יומי יש רשומה אחת ליום האחרון שה-enter שלה מתייחס לערב הראשון
            day_plus_2 = day_date + timedelta(days=2)
            day_plus_2_str = day_plus_2.strftime("%Y-%m-%d")
            day_plus_2_info = shabbat_cache.get(day_plus_2_str)

            # בדיקה אם יש חג דו-יומי במרחק 2 ימים
            # חג דו-יומי = יש holiday ברשומה ואין רשומה נפרדת למחר
            # בישראל, רק ראש השנה (א-ב תשרי) הוא חג דו-יומי
            is_two_day_holiday = (
                day_plus_2_info and
                day_plus_2_info.get("enter") and
                day_plus_2_info.get("holiday") and  # חייב להיות שדה holiday
                day_plus_2.weekday() != SATURDAY and
                not (tomorrow_info and tomorrow_info.get("enter")) and  # אין רשומה נפרדת למחר
                _is_two_day_yom_tov(day_plus_2)  # אימות מלוח עברי
            )

            if is_two_day_holiday:
                # מצאנו חג דו-יומי - היום הוא ערב
                target_day = day_plus_2
            elif tomorrow_info and tomorrow_info.get("exit") and tomorrow.weekday() != SATURDAY:
                # מחר יש exit - נבדוק אם היום הוא יום ביניים בחג דו-יומי
                # יום ביניים = אתמול היה ערב (יש רשומה לאתמול עם enter או holiday)
                yesterday = day_date - timedelta(days=1)
                yesterday_str = yesterday.strftime("%Y-%m-%d")
                yesterday_info = shabbat_cache.get(yesterday_str)

                if yesterday.weekday() == FRIDAY:
                    # אתמול היה יום שישי - היום הוא שבת
                    target_day = day_date
                elif yesterday_info and (yesterday_info.get("enter") or yesterday_info.get("holiday")):
                    # אתמול היה ערב חג - היום הוא יום ביניים
                    target_day = tomorrow
                # אחרת - לא חג

            if target_day is None:
                # לא שבת ולא חג
                return (-1, -1)

    # מציאת זמני כניסה ויציאה מהרשומה של היום המקודש
    target_str = target_day.strftime("%Y-%m-%d")
    target_info = shabbat_cache.get(target_str)

    enter_minutes = SHABBAT_ENTER_DEFAULT
    exit_minutes = SHABBAT_EXIT_DEFAULT + MINUTES_PER_DAY

    if target_info:
        # זמן כניסה (candle_lighting) - מתרחש בערב
        enter_source = target_info.get("enter") or (day_info and day_info.get("enter"))
        if enter_source:
            try:
                eh, em = map(int, enter_source.split(":"))
                enter_minutes = eh * MINUTES_PER_HOUR + em
            except (ValueError, AttributeError):
                pass

        # זמן יציאה (havdalah) - מתרחש ביום המקודש
        if target_info.get("exit"):
            try:
                xh, xm = map(int, target_info["exit"].split(":"))
                if xh == 0 and xm == 0:
                    # exit=00:00 = החג/שבת ממשיך ליום הבא, כל היום קדוש
                    exit_minutes = 2 * MINUTES_PER_DAY
                else:
                    exit_minutes = xh * MINUTES_PER_HOUR + xm + MINUTES_PER_DAY
            except (ValueError, AttributeError):
                pass

    return (enter_minutes, exit_minutes)


def classify_day_type(day_date: date, shabbat_cache: Dict[str, Dict[str, str]]) -> str:
    """
    סיווג סוג היום ביחס לשבת/חג.

    Returns:
        "holy" - יום שבת/חג (כל היום קדוש עד הבדלה)
        "eve" - ערב שבת/חג (יום חול, חג מתחיל בהדלקת נרות)
        "weekday" - יום חול רגיל
    """
    weekday = day_date.weekday()
    shabbat_enter, _ = _get_shabbat_boundaries(day_date, shabbat_cache)

    if shabbat_enter < 0:
        return "weekday"

    day_str = day_date.strftime("%Y-%m-%d")
    day_info = shabbat_cache.get(day_str)

    if weekday == SATURDAY:
        return "holy"

    if weekday == FRIDAY:
        if day_info and day_info.get("holiday"):
            return "holy"
        return "eve"

    # ימי חול - בדיקה אם ערב חג או יום חג
    # בדיקה ישירה: יום עם exit (הבדלה) = יום חג
    if day_info and day_info.get("exit"):
        return "holy"

    holiday_date, holiday_info = _find_holiday_record_for_date(day_date, shabbat_cache)
    if not holiday_date:
        return "weekday"

    days_to_holiday_record = (holiday_date - day_date).days

    if days_to_holiday_record == 0:
        # הרשומה היא להיום
        if holiday_info and not holiday_info.get("exit"):
            return "eve"  # רק הדלקת נרות = ערב חג
        return "holy"  # יש הבדלה = יום חג

    if days_to_holiday_record == 1:
        # הרשומה היא למחר
        if day_info:
            return "holy"  # יש רשומה להיום = יום חג
        # אין רשומה להיום - נבדוק אם אתמול היה חלק מאותו חג
        yesterday = day_date - timedelta(days=1)
        yesterday_enter, _ = _get_shabbat_boundaries(yesterday, shabbat_cache)
        if yesterday_enter > 0:
            return "holy"  # אתמול היה חג = היום גם חג
        return "eve"  # אתמול לא היה חג = היום ערב חג

    # מרחק 2+ ימים לרשומת החג
    yesterday = day_date - timedelta(days=1)
    yesterday_enter, _ = _get_shabbat_boundaries(yesterday, shabbat_cache)
    if yesterday_enter > 0:
        return "holy"  # אתמול היה חג = היום יום ביניים (חג)
    return "eve"


# =============================================================================
# Purim Boundary Detection
# =============================================================================

def _get_purim_date(day_date: date, is_jerusalem: bool) -> date:
    """
    חישוב תאריך פורים מלוח עברי.

    פורים = י"ד אדר (או אדר ב' בשנה מעוברת).
    שושן פורים (ירושלים) = ט"ו אדר.
    """
    from convertdate import hebrew

    hebrew_year, _, _ = hebrew.from_gregorian(day_date.year, day_date.month, day_date.day)
    # בשנה מעוברת פורים באדר ב' (חודש 13), בשנה רגילה באדר (חודש 12)
    adar = 13 if hebrew.leap(hebrew_year) else 12
    purim_day = 15 if is_jerusalem else 14
    g_year, g_month, g_day = hebrew.to_gregorian(hebrew_year, adar, purim_day)
    return date(g_year, g_month, g_day)


def _get_purim_boundaries(
    day_date: date,
    is_jerusalem: bool
) -> Tuple[int, int]:
    """
    גבולות פורים ליום נתון.

    פורים: תעריף 150% מ-08:00 ביום הפורים עד 08:00 למחרת בבוקר.
    - ביום הפורים עצמו: (480, 1440) = מ-08:00 עד חצות
    - ביום שלמחרת: (0, 480) = מחצות עד 08:00

    Returns:
        (enter, exit) בדקות מחצות, או (-1, -1) אם היום לא פורים
    """
    from core.constants import PURIM_ENTER_MINUTES, PURIM_EXIT_MINUTES

    purim_date = _get_purim_date(day_date, is_jerusalem)
    if purim_date is None:
        return (-1, -1)

    if day_date == purim_date:
        # יום הפורים: 08:00 עד חצות
        return (PURIM_ENTER_MINUTES, MINUTES_PER_DAY)

    if day_date == purim_date + timedelta(days=1):
        # בוקר למחרת: חצות עד 08:00
        return (0, PURIM_EXIT_MINUTES)

    return (-1, -1)


def _is_purim_time(
    actual_date: date,
    start_min: int,
    is_jerusalem: bool
) -> bool:
    """
    בדיקה אם זמן נתון חל בשעות פורים.

    Args:
        actual_date: התאריך בפועל
        start_min: דקות מחצות (יכול להיות >1440 למשמרות לילה)
        is_jerusalem: האם הדירה בירושלים
    """
    purim_enter, purim_exit = _get_purim_boundaries(actual_date, is_jerusalem)
    if purim_enter < 0:
        return False
    actual_minute = start_min % MINUTES_PER_DAY
    return purim_enter <= actual_minute < purim_exit

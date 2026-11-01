"""
שירות מאוחד לחלונות פרימיום — שבת, חג, פורים, יום העצמאות, בחירות, ומותאם.

נקודת הכניסה היחידה למנוע החישוב לזיהוי "האם לזמן X חל תעריף מיוחד".
מאחד שני מקורות נתונים:
- shabbat_times: שבת וחגים יהודיים (מעודכן ידנית/Hebcal)
- special_days: ימים מיוחדים חוקיים/אזרחיים (פורים, עצמאות, בחירות)

כל יום מופיע בטבלה אחת בלבד — אין חפיפה בפועל.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional

import psycopg2
import psycopg2.extras

from core.time_utils import (
    MINUTES_PER_DAY,
    SHABBAT_ENTER_DEFAULT,
    SHABBAT_EXIT_DEFAULT,
    SHABBAT_TIMES_CITY,
    _is_two_day_yom_tov,
)

logger = logging.getLogger(__name__)

# דגל חד-פעמי לאזהרה על טבלה חסרה (לא להציף לוגים)
_WARNED_MISSING_TABLE = {"flag": False}


# =============================================================================
# Data Model
# =============================================================================

@dataclass(frozen=True)
class PremiumWindow:
    """
    חלון פרימיום — טווח זמן שבו חל תעריף מיוחד.

    Attributes:
        start_date: תאריך תחילת החלון.
        start_min: דקות מחצות start_date לתחילת החלון.
        end_date: תאריך סיום החלון (יכול להיות שונה מ-start_date בחציית חצות).
        end_min: דקות מחצות end_date לסיום החלון.
        rate_pct: התעריף באחוזים (150 לשבת/פורים/עצמאות, 200 לבחירות).
        origin: מקור החלון — 'shabbat'/'holiday'/'purim'/'independence'/'elections'/'custom'.
        standby_mode: מדיניות כוננות — 'shabbat' (תעריף כוננות שבת) או 'none' (רגיל).
        source_id: FK לרשומת המקור (shabbat_times אין id נפרד, special_days.id).
        city_filter: רק ערים אלה (NULL = כולם). להחלה ב-filter_windows_by_city.
        name: שם היום המיוחד לתצוגה (למשל "פורים תשפ״ז", "יום העצמאות").
    """
    start_date: date
    start_min: int
    end_date: date
    end_min: int
    rate_pct: int
    origin: str
    standby_mode: str
    source_id: Optional[int]
    city_filter: Optional[tuple] = None
    name: Optional[str] = None


# =============================================================================
# Helpers
# =============================================================================

def _time_to_minutes(t: time) -> int:
    """המרת אובייקט time לדקות מחצות."""
    return t.hour * 60 + t.minute


def _hhmm_to_minutes(value: str | None) -> int | None:
    """המרת מחרוזת 'HH:MM' לדקות מחצות. מחזיר None אם ריק/לא תקין."""
    if not value:
        return None
    try:
        hh, mm = value.split(":")
        hours = int(hh)
        minutes = int(mm)
        if not 0 <= hours <= 23 or not 0 <= minutes <= 59:
            return None
        return hours * 60 + minutes
    except (ValueError, AttributeError):
        return None


def _parse_iso_date(value: str | date | None) -> date | None:
    """המרת מחרוזת 'YYYY-MM-DD' ל-date. אם כבר date — מחזיר as-is. None אם לא תקין."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _city_matches(window_filter: list[str] | None,
                  apt_city: str | None) -> bool:
    """
    בדיקה אם הדירה מתאימה לפילטר הערים של החלון.

    - window_filter=None → החלון חל על כולם
    - window_filter=[..] → החלון חל רק אם apt_city ברשימה
    """
    if window_filter is None:
        return True
    normalized_city = (apt_city or "").strip()
    return normalized_city in window_filter


def build_sacred_windows_from_cache(
    shabbat_cache: dict[str, dict[str, str]],
    relevant_dates: Iterable[date] | None = None,
) -> list[PremiumWindow]:
    """Build absolute Shabbat/holiday windows from the legacy cache.

    The production table normally stores entry and exit on the sacred date.
    Older tests and imports may store entry on the eve and exit on the next
    date, so that representation is supported as well.  A window is always
    half-open: entry is included and exit is excluded.
    """
    parsed_rows: list[tuple[date, dict[str, str]]] = []
    for raw_date, info in shabbat_cache.items():
        parsed_date = _parse_iso_date(raw_date)
        if parsed_date is not None and info:
            parsed_rows.append((parsed_date, info))
    parsed_rows.sort(key=lambda item: item[0])
    rows_by_date = dict(parsed_rows)

    windows: list[PremiumWindow] = []
    for row_date, info in parsed_rows:
        enter_min = _hhmm_to_minutes(info.get("enter") or info.get("start"))
        exit_min = _hhmm_to_minutes(info.get("exit") or info.get("end"))
        holiday_name = info.get("holiday")
        entry_row_is_holiday = bool(holiday_name)

        if enter_min is None:
            continues_previous = any(
                window.end_date == row_date and window.end_min == 0
                for window in windows
            )
            if exit_min is not None and exit_min > 0 and continues_previous:
                windows.append(PremiumWindow(
                    start_date=row_date,
                    start_min=0,
                    end_date=row_date,
                    end_min=exit_min,
                    rate_pct=150,
                    origin="holiday" if holiday_name else "shabbat",
                    standby_mode="shabbat",
                    source_id=None,
                    name=holiday_name or "שבת",
                ))
            continue

        if enter_min == 0 and exit_min == 0:
            continue

        if exit_min is not None:
            if row_date.weekday() == 4 and not holiday_name:
                start_date = row_date
                end_date = row_date + timedelta(days=1)
            else:
                days_before = 2 if holiday_name and _is_two_day_yom_tov(row_date) else 1
                start_date = row_date - timedelta(days=days_before)
                end_date = row_date
        else:
            end_date = None
            for days_ahead in range(1, 4):
                candidate_date = row_date + timedelta(days=days_ahead)
                candidate = rows_by_date.get(candidate_date)
                candidate_exit = _hhmm_to_minutes(candidate.get("exit")) if candidate else None
                if candidate_exit is not None:
                    end_date = candidate_date
                    exit_min = candidate_exit
                    holiday_name = holiday_name or candidate.get("holiday")
                    break
            if end_date is None or exit_min is None:
                continue
            # Split holiday imports put candle lighting on the first sacred
            # date; the actual entry is its eve. Split Shabbat imports put
            # the entry on Friday itself.
            start_date = row_date - timedelta(days=1) if entry_row_is_holiday else row_date

        if exit_min == 0:
            end_date += timedelta(days=1)

        windows.append(PremiumWindow(
            start_date=start_date,
            start_min=enter_min,
            end_date=end_date,
            end_min=exit_min,
            rate_pct=150,
            origin="holiday" if holiday_name else "shabbat",
            standby_mode="shabbat",
            source_id=None,
            name=holiday_name or "שבת",
        ))

    fallback_fridays: set[date] = set()
    for relevant_date in relevant_dates or ():
        if relevant_date.weekday() == 4:
            fallback_fridays.add(relevant_date)
        elif relevant_date.weekday() == 5:
            fallback_fridays.add(relevant_date - timedelta(days=1))

    for friday in fallback_fridays:
        saturday = friday + timedelta(days=1)
        has_explicit_window = any(
            window.start_date <= friday and window.end_date >= saturday
            for window in windows
        )
        if not has_explicit_window:
            windows.append(PremiumWindow(
                start_date=friday,
                start_min=SHABBAT_ENTER_DEFAULT,
                end_date=saturday,
                end_min=SHABBAT_EXIT_DEFAULT,
                rate_pct=150,
                origin="shabbat",
                standby_mode="shabbat",
                source_id=None,
                name="שבת",
            ))

    windows.sort(key=lambda window: (window.start_date, window.start_min))
    return windows


# =============================================================================
# Data Loading
# =============================================================================

def _load_shabbat_windows(conn, start_date: date, end_date: date) -> list[PremiumWindow]:
    """
    טעינת חלונות שבת/חג מ-shabbat_times לטווח תאריכים נתון.

    טבלת shabbat_times מחזיקה עבור כל יום שבת/חג (כל השדות text):
    - shabbat_date: תאריך היום המקודש (YYYY-MM-DD)
    - candle_lighting: שעת כניסה בערב שלפני shabbat_date (HH:MM)
    - havdalah: שעת יציאה ביום shabbat_date עצמו (HH:MM)

    עבור חלון שבת: start_date = ערב (shabbat_date-1), end_date = היום המקודש (shabbat_date).
    """
    # טוענים עם hysteresis של יום אחד כדי לתפוס חלונות שחוצים את הגבולות.
    # shabbat_date מאוחסן כ-text, לכן משווים כמחרוזות בפורמט YYYY-MM-DD.
    query_start = (start_date - timedelta(days=1)).strftime("%Y-%m-%d")
    query_end = (end_date + timedelta(days=1)).strftime("%Y-%m-%d")

    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute("""
            SELECT city, shabbat_date, candle_lighting, havdalah, holiday_name
            FROM shabbat_times
            WHERE shabbat_date BETWEEN %s AND %s
              AND city = %s
              AND (candle_lighting IS NOT NULL OR havdalah IS NOT NULL)
            ORDER BY shabbat_date,
                     candle_lighting NULLS LAST,
                     havdalah NULLS LAST,
                     holiday_name NULLS LAST
        """, (query_start, query_end, SHABBAT_TIMES_CITY))
        rows = cursor.fetchall()
    finally:
        cursor.close()

    cache_rows: dict[str, dict[str, str]] = {}
    seen_dates: set[date] = set()
    for row in rows:
        shabbat_date = _parse_iso_date(row["shabbat_date"])
        enter_min = _hhmm_to_minutes(row["candle_lighting"])
        exit_min = _hhmm_to_minutes(row["havdalah"])

        if row["city"] != SHABBAT_TIMES_CITY:
            continue
        if shabbat_date is None or (enter_min is None and exit_min is None):
            continue
        if enter_min == 0 and exit_min == 0:
            continue
        if shabbat_date in seen_dates:
            existing = cache_rows[shabbat_date.isoformat()]
            conflict = False
            for field, candidate in (
                ("enter", row["candle_lighting"] if enter_min is not None else None),
                ("exit", row["havdalah"] if exit_min is not None else None),
                ("holiday", row["holiday_name"]),
            ):
                if candidate is None:
                    continue
                if existing.get(field) is None:
                    existing[field] = candidate
                elif existing[field] != candidate:
                    conflict = True
            if conflict:
                logger.warning(
                    "Conflicting shabbat_times rows for %s/%s; using deterministic first row",
                    SHABBAT_TIMES_CITY,
                    shabbat_date,
                )
            continue
        seen_dates.add(shabbat_date)
        cache_rows[shabbat_date.isoformat()] = {
            "enter": row["candle_lighting"] if enter_min is not None else None,
            "exit": row["havdalah"] if exit_min is not None else None,
            "holiday": row["holiday_name"],
        }

    return build_sacred_windows_from_cache(cache_rows)


def _load_special_day_windows(
    conn, start_date: date, end_date: date,
) -> list[PremiumWindow]:
    """
    טעינת חלונות ימים מיוחדים (פורים/עצמאות/בחירות/מותאם) מ-special_days.

    לא מסנן לפי עיר — הסינון מתבצע בצד הקורא דרך filter_windows_by_city,
    כדי לאפשר pre-loading יעיל לחודש שלם ו-reuse על פני דירות שונות.

    אם הטבלה special_days לא קיימת (מיגרציה לא רצה), מחזיר רשימה ריקה
    ומדפיס אזהרה אחת — כך שהאפליקציה תמשיך לעבוד ללא פורים/עצמאות/בחירות
    עד שתרוץ המיגרציה.
    """
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        try:
            cursor.execute("""
                SELECT id, name, start_date, start_time, end_date, end_time,
                       rate_pct, standby_mode, city_filter
                FROM special_days
                WHERE is_active = true
                  AND start_date <= %s
                  AND end_date >= %s
            """, (end_date, start_date))
            rows = cursor.fetchall()
        except psycopg2.errors.UndefinedTable:
            conn.rollback()
            if not _WARNED_MISSING_TABLE["flag"]:
                logger.warning(
                    "special_days table not found — run sql/create_special_days.sql. "
                    "Purim/Independence/Elections premium rates disabled until migration is applied."
                )
                _WARNED_MISSING_TABLE["flag"] = True
            return []
    finally:
        cursor.close()

    windows: list[PremiumWindow] = []
    for row in rows:
        windows.append(PremiumWindow(
            start_date=row["start_date"],
            start_min=_time_to_minutes(row["start_time"]),
            end_date=row["end_date"],
            end_min=_time_to_minutes(row["end_time"]),
            rate_pct=row["rate_pct"],
            origin="premium",
            standby_mode=row["standby_mode"],
            source_id=row["id"],
            city_filter=tuple(row["city_filter"]) if row["city_filter"] else None,
            name=row["name"],
        ))

    return windows


def filter_windows_by_city(
    windows: list[PremiumWindow], apt_city: str | None,
) -> list[PremiumWindow]:
    """סינון חלונות לפי city_filter של כל חלון ביחס לעיר הדירה."""
    return [
        w for w in windows
        if _city_matches(
            list(w.city_filter) if w.city_filter else None,
            apt_city,
        )
    ]


# =============================================================================
# Public API
# =============================================================================

def get_premium_windows_for_range(
    conn,
    start_date: date,
    end_date: date,
) -> list[PremiumWindow]:
    """
    נקודת הכניסה היחידה — מחזירה את כל חלונות הפרימיום שחופפים לטווח.

    מאחדת שני מקורות (shabbat_times + special_days) ללא סינון עיר.
    סינון לפי עיר מתבצע ע"י filter_windows_by_city (בצד הקורא).

    Args:
        conn: חיבור DB.
        start_date: תחילת טווח החיפוש.
        end_date: סוף טווח החיפוש (כולל).

    Returns:
        רשימת חלונות ממוינת לפי (start_date, start_min).
    """
    shabbat_windows = _load_shabbat_windows(conn, start_date, end_date)
    special_windows = _load_special_day_windows(conn, start_date, end_date)
    all_windows = shabbat_windows + special_windows
    all_windows.sort(key=lambda w: (w.start_date, w.start_min))
    return all_windows


def get_window_at(
    windows: list[PremiumWindow],
    check_date: date,
    check_min: int,
) -> Optional[PremiumWindow]:
    """
    מחזיר את החלון בעל התעריף הגבוה ביותר החופף לרגע נתון.

    אם אין חלון חופף — מחזיר None (= זמן חול רגיל).
    אם יש מספר חלונות חופפים (נדיר) — מחזיר את זה עם rate_pct הגבוה ביותר.

    Args:
        windows: רשימת חלונות (מ-get_premium_windows_for_range).
        check_date: תאריך הבדיקה.
        check_min: דקות מחצות check_date.

    Returns:
        PremiumWindow חופף (הגבוה ביותר) או None.
    """
    matching: list[PremiumWindow] = []
    for w in windows:
        if _is_within_window(w, check_date, check_min):
            matching.append(w)
    if not matching:
        return None
    return max(matching, key=lambda w: w.rate_pct)


def _is_within_window(w: PremiumWindow, check_date: date, check_min: int) -> bool:
    """בדיקה אם (check_date, check_min) בתוך החלון."""
    # מנרמל לדקות-מוחלטות מתאריך בסיס (start_date של החלון)
    check_abs = (check_date - w.start_date).days * MINUTES_PER_DAY + check_min
    end_abs = (w.end_date - w.start_date).days * MINUTES_PER_DAY + w.end_min
    return w.start_min <= check_abs < end_abs


def minutes_until_state_change(
    windows: list[PremiumWindow],
    check_date: date,
    check_min: int,
    max_distance: int,
) -> int:
    """
    מחשב כמה דקות עד שמצב הפרימיום משתנה (כניסה/יציאה לחלון).

    שימושי לחיתוך בלוקים במנוע החישוב כך שכל בלוק יהיה כולו בתוך חלון אחד
    (עם origin ו-rate_pct קבועים) או כולו מחוץ לחלונות.

    Args:
        windows: רשימת חלונות (ממוינת כפי ש-get_premium_windows_for_range מחזיר).
        check_date: תאריך ההתחלה לבדיקה.
        check_min: דקות מחצות check_date.
        max_distance: מקסימום דקות לחיפוש קדימה (גבול מלאכותי).

    Returns:
        מספר דקות עד לשינוי המצב הבא, או max_distance אם אין שינוי בטווח.
    """
    current_window = get_window_at(windows, check_date, check_min)

    if current_window is not None:
        # אנחנו בתוך חלון — הגבול הבא הוא סוף החלון
        end_abs = (current_window.end_date - check_date).days * MINUTES_PER_DAY + current_window.end_min
        distance_to_end = end_abs - check_min
        return min(distance_to_end, max_distance) if distance_to_end > 0 else max_distance

    # אנחנו מחוץ לחלון — הגבול הבא הוא תחילת החלון הבא
    next_start: int | None = None
    for w in windows:
        w_start_abs = (w.start_date - check_date).days * MINUTES_PER_DAY + w.start_min
        distance_to_start = w_start_abs - check_min
        if 0 < distance_to_start <= max_distance:
            if next_start is None or distance_to_start < next_start:
                next_start = distance_to_start
    return next_start if next_start is not None else max_distance

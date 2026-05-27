
from typing import Dict, List, Tuple, Any, Optional
from datetime import datetime, timedelta, date
from decimal import Decimal, ROUND_HALF_UP
from core.time_utils import (
    MINUTES_PER_DAY, LOCAL_TZ,
    REGULAR_HOURS_LIMIT, OVERTIME_125_LIMIT,
    FRIDAY, SATURDAY,
    span_minutes, to_local_date, _get_shabbat_boundaries,
    classify_day_type,
)
from core.premium_windows import (
    PremiumWindow,
    get_premium_windows_for_range,
    get_window_at,
    minutes_until_state_change,
    filter_windows_by_city,
)
from utils.utils import overlap_minutes, to_gematria, month_range_ts, merge_intervals, find_uncovered_intervals
from convertdate import hebrew
import logging
import psycopg2.extras

from core.history import (
    get_apartment_type_for_month, get_person_status_for_month,
    get_all_housing_rates_for_month, get_all_apartment_type_change_dates
)
from core.database import get_housing_array_filter
from core.sick_days import _identify_sick_day_sequences, get_sick_payment_rate

# =============================================================================
# Import constants from single source of truth (core/constants.py)
# =============================================================================
from core.constants import (
    # Shift IDs
    FRIDAY_SHIFT_ID,
    SHABBAT_SHIFT_ID,
    NIGHT_SHIFT_ID,
    TAGBUR_FRIDAY_SHIFT_ID,
    TAGBUR_SHABBAT_SHIFT_ID,
    HOSPITAL_ESCORT_SHIFT_ID,
    WORK_HOUR_SHIFT_ID,
    # Shift ID groups
    SHABBAT_SHIFT_IDS,
    TAGBUR_SHIFT_IDS,
    # Apartment types
    REGULAR_APT_TYPE,
    THERAPEUTIC_APT_TYPE,
    BERESHIT_APT_TYPE,
    KALANIYOT_APT_TYPE,
    HIGH_FUNCTIONING_APT_TYPE,
    LOW_FUNCTIONING_APT_TYPE,
    SPECIAL_ABSENCE_PAYMENT_APT_TYPES,
    APT_TYPE_NAMES,
    ASD_SENIORITY_SUPPLEMENT,
    ASD_SENIORITY_YEARS_THRESHOLD,
    is_asd_housing_array,
    # Standby constants
    MAX_CANCELLED_STANDBY_DEDUCTION,
    STANDBY_CANCEL_OVERLAP_THRESHOLD,
    DEFAULT_STANDBY_RATE,
    ASD_NIGHT_STANDBY_RATE,
    # Break/Chain constants
    BREAK_THRESHOLD_MINUTES,
    # Night shift overtime thresholds
    NIGHT_REGULAR_HOURS_LIMIT,
    NIGHT_OVERTIME_125_LIMIT,
    # Night shift time constants
    NIGHT_SHIFT_WORK_FIRST_MINUTES,
    NIGHT_SHIFT_STANDBY_END,
    NIGHT_SHIFT_MORNING_END,
    NOON_MINUTES,
    # Helper functions
    is_tagbur_shift,
    is_night_shift,
    is_shabbat_shift,
    is_implicit_tagbur,
    qualifies_as_night_shift,
    calculate_night_hours_in_segment,
    # Night hours threshold
    NIGHT_HOURS_THRESHOLD,
    # Sick/Vacation constants
    WEEKDAY_SHIFT_TYPE_ID,
    WEEKDAY_STANDBY_START,
    WEEKDAY_STANDBY_END,
    calculate_weekday_work_minutes,
    should_exclude_asd_completion_report,
)

logger = logging.getLogger(__name__)

_NIGHT_FIRST_WORK_SEGMENT_MARKER = "__night_first_work__"


def _apply_tagbur_dynamic_boundaries(
    shift_type_id: int,
    seg_list: list[dict],
    report_date: date,
    report_start_min: int,
    report_end_min: int,
    year: int,
    month: int,
    shabbat_cache: Dict[str, Dict[str, str]],
) -> list[dict]:
    from core.shift_hours import apply_tagbur_dynamic_boundaries

    return apply_tagbur_dynamic_boundaries(
        shift_type_id,
        seg_list,
        report_date,
        report_start_min,
        report_end_min,
        year,
        month,
        shabbat_cache,
    )


def _filter_asd_completion_reports_for_one_time_exclusion(
    reports: list,
    year: int,
    month: int,
) -> list:
    """החרגה חד-פעמית: דירת השלמות ב-ASD לא נכנסת לשכר 04/2026."""
    if (year, month) != (2026, 4):
        return reports
    return [
        report for report in reports
        if not should_exclude_asd_completion_report(
            year,
            month,
            report.get("housing_array_id"),
            report.get("apartment_id"),
        )
    ]


def _subtract_intervals_from_range(
    start: int,
    end: int,
    blockers: List[Tuple[int, int]],
) -> List[Tuple[int, int]]:
    """Return the pieces of start-end that are not covered by blocker intervals."""
    remaining = [(start, end)]
    for block_start, block_end in merge_intervals(blockers):
        new_remaining = []
        for part_start, part_end in remaining:
            overlap_start = max(part_start, block_start)
            overlap_end = min(part_end, block_end)
            if overlap_start < overlap_end:
                if part_start < overlap_start:
                    new_remaining.append((part_start, overlap_start))
                if overlap_end < part_end:
                    new_remaining.append((overlap_end, part_end))
            else:
                new_remaining.append((part_start, part_end))
        remaining = new_remaining
        if not remaining:
            break
    return remaining


def _trim_night_first_work_overlaps(segments: List[Tuple]) -> List[Tuple]:
    """
    Remove overlap between the first two paid hours of a night shift and other work reports.

    Night-shift first hours are a fallback payment. If another work report covers part of
    that window, only the uncovered part remains payable as night-shift work.
    """
    other_work_intervals = [
        (seg[0], seg[1])
        for seg in segments
        if len(seg) > 5
        and seg[2] == "work"
        and seg[5] != _NIGHT_FIRST_WORK_SEGMENT_MARKER
        and seg[1] > seg[0]
    ]
    if not other_work_intervals:
        return segments

    trimmed: List[Tuple] = []
    for seg in segments:
        if len(seg) <= 5 or seg[2] != "work" or seg[5] != _NIGHT_FIRST_WORK_SEGMENT_MARKER:
            trimmed.append(seg)
            continue

        remaining_parts = _subtract_intervals_from_range(seg[0], seg[1], other_work_intervals)
        for part_start, part_end in remaining_parts:
            if part_end > part_start:
                trimmed.append((part_start, part_end, *seg[2:]))

    return trimmed


def _resolve_work_segment_overlaps(
    work_segments: List[Tuple],
    shift_rates: dict,
    minimum_wage: float,
) -> tuple[List[Tuple], list[dict[str, Any]]]:
    """
    Cut overlapping work segments and keep the segment with the highest hourly rate.

    work_segments tuple shape:
    (start, end, label, shift_id, apartment_name, actual_date, apt_type,
     actual_apt_type, rate_apt_type, housing_array_id, apt_type_name, ha_name,
     rate_apt_type_name, apt_type_change_date, apt_city)
    """
    if len(work_segments) < 2:
        return work_segments, []

    boundaries = sorted({
        point
        for seg in work_segments
        for point in (seg[0], seg[1])
        if seg[1] > seg[0]
    })
    if len(boundaries) < 2:
        return work_segments, []

    def segment_rate(seg: Tuple) -> float:
        rate_key = (seg[3], seg[9], seg[8])
        rates = shift_rates.get(rate_key, {"weekday": minimum_wage})
        return float(rates.get("weekday") or minimum_wage)

    def fmt_minutes(value: int) -> str:
        return f"{value // 60 % 24:02d}:{value % 60:02d}"

    resolved: list[Tuple] = []
    warnings_by_key: dict[tuple[int, int], dict[str, Any]] = {}

    for start, end in zip(boundaries, boundaries[1:]):
        if end <= start:
            continue
        covering = [seg for seg in work_segments if seg[0] < end and seg[1] > start]
        if not covering:
            continue

        if len(covering) > 1:
            # Highest rate wins; if rates tie, prefer the more specific/shorter report.
            selected = max(
                covering,
                key=lambda seg: (
                    segment_rate(seg),
                    -(seg[1] - seg[0]),
                    seg[0],
                ),
            )
            warning_key = (start, end)
            selected_rate = segment_rate(selected)
            dropped = [seg for seg in covering if seg is not selected]
            warnings_by_key[warning_key] = {
                "start": start,
                "end": end,
                "start_time": fmt_minutes(start),
                "end_time": fmt_minutes(end),
                "selected_rate": selected_rate,
                "selected_shift_name": selected[2],
                "selected_apartment": selected[4],
                "rates": sorted({round(segment_rate(seg), 2) for seg in covering}, reverse=True),
                "apartments": sorted({seg[4] for seg in covering if seg[4]}),
                "cut_segments": [
                    {
                        "rate": round(segment_rate(seg), 2),
                        "shift_name": seg[2],
                        "apartment": seg[4],
                    }
                    for seg in dropped
                ],
            }
        else:
            selected = covering[0]

        resolved.append((start, end, *selected[2:]))

    merged: list[Tuple] = []
    for seg in resolved:
        if (
            merged
            and merged[-1][1] == seg[0]
            and merged[-1][2:] == seg[2:]
        ):
            merged[-1] = (merged[-1][0], seg[1], *seg[2:])
        else:
            merged.append(seg)

    warnings = list(warnings_by_key.values())
    return merged, warnings


# =============================================================================
# Data Access Functions (moved from core/logic.py to fix circular dependency)
# =============================================================================

def get_standby_rate(conn, segment_id: int, apartment_type_id: int | None, is_married: bool, year: int = None, month: int = None) -> float:
    """
    Get standby rate from standby_rates table.
    Priority: specific apartment_type (priority=10) > general (priority=0)
    If year/month provided, checks historical rates first.
    """
    marital_status = "married" if is_married else "single"

    # If year/month provided, try historical rates first
    if year is not None and month is not None:
        from core.history import get_standby_rate_for_month
        historical_amount = get_standby_rate_for_month(
            conn, segment_id, apartment_type_id, marital_status, year, month
        )
        if historical_amount is not None:
            return float(historical_amount) / 100

    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # First try specific rate for apartment type (priority=10)
    if apartment_type_id is not None:
        cursor.execute("""
            SELECT amount FROM standby_rates
            WHERE segment_id = %s AND apartment_type_id = %s AND marital_status = %s AND priority = 10
            LIMIT 1
        """, (segment_id, apartment_type_id, marital_status))
        row = cursor.fetchone()
        if row:
            cursor.close()
            return float(row["amount"]) / 100

    # Fallback to general rate (priority=0)
    cursor.execute("""
        SELECT amount FROM standby_rates
        WHERE segment_id = %s AND apartment_type_id IS NULL AND marital_status = %s AND priority = 0
        LIMIT 1
    """, (segment_id, marital_status))
    row = cursor.fetchone()
    cursor.close()

    if row:
        return float(row["amount"]) / 100

    return DEFAULT_STANDBY_RATE


# Cache for Shabbat standby segment_id (shared across calls within same request)
_shabbat_standby_seg_id_cache: Dict[str, int | None] = {}


def _get_shabbat_standby_segment_id(conn) -> int | None:
    """מציאת ה-segment_id של כוננות שבת מטבלת shift_time_segments."""
    cache_key = "shabbat_standby_seg_id"
    if cache_key in _shabbat_standby_seg_id_cache:
        return _shabbat_standby_seg_id_cache[cache_key]

    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("""
        SELECT id FROM shift_time_segments
        WHERE shift_type_id = %s AND segment_type = 'standby'
        LIMIT 1
    """, (SHABBAT_SHIFT_ID,))
    row = cursor.fetchone()
    cursor.close()

    result = row["id"] if row else None
    _shabbat_standby_seg_id_cache[cache_key] = result
    return result


def _get_premium_standby_rate(
    conn, apt_type: int | None, married: bool,
    actual_date: date, start_min: int,
    premium_windows: Optional[List[PremiumWindow]],
    year: int = None, month: int = None,
) -> float | None:
    """
    אם הכוננות חלה בחלון פרימיום עם standby_mode='shabbat' (למשל פורים),
    מחזיר תעריף כוננות שבת. אחרת מחזיר None (להשתמש בתעריף הרגיל).
    """
    if not premium_windows:
        return None
    window = get_window_at(premium_windows, actual_date, start_min)
    if window is None or window.standby_mode != "shabbat":
        return None

    shabbat_seg_id = _get_shabbat_standby_segment_id(conn)
    if shabbat_seg_id:
        return get_standby_rate(conn, shabbat_seg_id, apt_type, married, year, month)
    return None


# =============================================================================
# Sick/Vacation Work Hours from Overrides
# =============================================================================


def _build_sick_vacation_segments(start_time: str, end_time: str) -> list[dict]:
    """
    בניית סגמנטי עבודה לחופשה/מחלה מתוך שעות override של משמרת חול.

    מחזיר סגמנטים של עבודה בלבד (ללא כוננות 22:00-06:30).
    לדוגמה: override 15:00-08:00 → [{15:00-22:00, work}, {06:30-08:00, work}]

    Args:
        start_time: שעת תחילת משמרת (HH:MM)
        end_time: שעת סיום משמרת (HH:MM)

    Returns:
        רשימת סגמנטים בפורמט התואם ל-seg_list
    """
    start_min, end_min = span_minutes(start_time, end_time)

    standby_start = WEEKDAY_STANDBY_START  # 1320 (22:00)
    standby_end = WEEKDAY_STANDBY_END + MINUTES_PER_DAY  # 1830 (06:30 למחרת)

    segments = []

    # עבודה לפני כוננות (תחילת משמרת עד 22:00)
    if start_min < standby_start:
        seg_end = min(end_min, standby_start)
        if seg_end > start_min:
            segments.append({
                "start_time": _minutes_to_hhmm(start_min),
                "end_time": _minutes_to_hhmm(seg_end),
                "segment_type": "work",
                "id": None,
            })

    # עבודה אחרי כוננות (06:30 עד סיום משמרת)
    if end_min > standby_end:
        seg_start = max(start_min, standby_end)
        if end_min > seg_start:
            segments.append({
                "start_time": _minutes_to_hhmm(seg_start),
                "end_time": _minutes_to_hhmm(end_min),
                "segment_type": "work",
                "id": None,
            })

    return segments


def _minutes_to_hhmm(minutes: int) -> str:
    """המרת דקות מחצות למחרוזת HH:MM."""
    m = minutes % MINUTES_PER_DAY
    return f"{m // 60:02d}:{m % 60:02d}"


def _round_pay(value: float, decimals: int = 1) -> float:
    """עיגול תשלום בשיטת מירב (round half up, לא banker's rounding של Python)."""
    return float(Decimal(str(value)).quantize(Decimal(10) ** -decimals, rounding=ROUND_HALF_UP))


def _mul_pay(hours: float, rate: float) -> float:
    """מכפלת שעות×תעריף ועיגול לעשרון בשיטת מירב (Decimal למניעת שגיאות float)."""
    return float((Decimal(str(hours)) * Decimal(str(rate))).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))


def _absence_payment_shift_id(apartment_type_id: int | None) -> int | None:
    """משמרת מקור לתשלום חג/חופשה/מחלה בסוגי דירה מיוחדים."""
    if apartment_type_id == KALANIYOT_APT_TYPE:
        return WEEKDAY_SHIFT_TYPE_ID
    if apartment_type_id == BERESHIT_APT_TYPE:
        return NIGHT_SHIFT_ID
    return None


def _calculate_special_absence_segment_payment(
    conn,
    *,
    segment_type: str,
    duration: int,
    shift_type_id: int,
    segment_id: int | None,
    apartment_type_id: int | None,
    housing_array_id: int | None,
    is_married: bool,
    minimum_wage: float,
    year: int,
    month: int,
    housing_rates_cache: dict | None,
) -> tuple[float, int, float]:
    """
    חישוב סגמנט חופשה/מחלה מיוחד: עבודה לפי תעריף משמרת, כוננות כתשלום כוננות פחות 70.

    Returns:
        (payment, paid_minutes, effective_rate)
        paid_minutes הוא 0 בסגמנט כוננות כדי שהכוננות לא תיספר כשעות עבודה/חופשה/מחלה.
    """
    if duration <= 0:
        return 0.0, 0, minimum_wage

    if segment_type == "standby":
        standby_rate = (
            get_standby_rate(conn, segment_id or 0, apartment_type_id, is_married, year, month)
            if segment_id else DEFAULT_STANDBY_RATE
        )
        return _round_pay(max(0.0, standby_rate - MAX_CANCELLED_STANDBY_DEDUCTION)), 0, standby_rate

    report = {
        "shift_type_id": shift_type_id,
        "housing_array_id": housing_array_id,
        "is_married": is_married,
        "hourly_wage_supplement": 0,
    }
    rate = get_effective_hourly_rate(
        report,
        minimum_wage,
        is_shabbat=False,
        housing_rates_cache=housing_rates_cache,
    )
    return _mul_pay(round(duration / 60, 2), round(rate, 2)), duration, rate


def _display_base_hourly(
    seg_rate: float,
    minimum_wage: float,
    rate_supplement_agorot: int | float,
    actual_supplement_agorot: int | float,
) -> float:
    """
    תעריף שעתי לעמודת «בסיס» בטבלה: תעריף מהמערך + תוספת שעתית לפי סוג דירה בפועל.

    כאשר התעריף כבר כולל את תוספת סוג הדירה לתשלום (שכר מינימום + תוספת), לא מכפילים.
    """
    rate_supp_nis = float(rate_supplement_agorot or 0) / 100.0
    actual_supp_nis = float(actual_supplement_agorot or 0) / 100.0
    if rate_supp_nis > 0:
        if abs(seg_rate - minimum_wage - rate_supp_nis) < 0.02:
            return round(minimum_wage + actual_supp_nis, 2)
        return round(seg_rate, 2)
    return round(seg_rate + actual_supp_nis, 2)


def _should_show_hourly_supplements_in_basis(
    seg_rate: float,
    minimum_wage: float,
    rate_supplement_agorot: int | float,
) -> bool:
    """True when the displayed base is built from minimum wage plus supplements."""
    rate_supp_nis = float(rate_supplement_agorot or 0) / 100.0
    if rate_supp_nis <= 0:
        return False
    return abs(seg_rate - minimum_wage - rate_supp_nis) < 0.02


def _register_asd_night_labels(
    entry: dict,
    r: dict,
    has_asd_night: bool,
    actual_apt_id_for_asd: int | None,
) -> None:
    """רישום תוויות תצוגה לכוננות ASD לילה לפי סוג תפקוד (דירה)."""
    if not has_asd_night or actual_apt_id_for_asd is None:
        return
    apt_nm = (r.get("apartment_name") or "").strip()
    if actual_apt_id_for_asd == HIGH_FUNCTIONING_APT_TYPE:
        if apt_nm:
            entry.setdefault("asd_night_label_by_apt", {})[apt_nm] = "שינה בסלון"
        entry.setdefault("asd_night_label_by_apt_type", {})[HIGH_FUNCTIONING_APT_TYPE] = "שינה בסלון"
    elif actual_apt_id_for_asd == LOW_FUNCTIONING_APT_TYPE:
        if apt_nm:
            entry.setdefault("asd_night_label_by_apt", {})[apt_nm] = "ערות בלילה"
        entry.setdefault("asd_night_label_by_apt_type", {})[LOW_FUNCTIONING_APT_TYPE] = "ערות בלילה"


def _asd_night_label_for_row(
    label_by_apt: dict[str, str] | None,
    label_by_type: dict[int, str] | None,
    apt_name: str,
    apt_type_id: int | None,
) -> str:
    """תווית טקסט לשורה בטבלה כשיש סימון asd_night_marking בדיווח."""
    if label_by_apt and apt_name:
        apt_name = apt_name.strip()
        if apt_name in label_by_apt:
            return label_by_apt[apt_name]
        for part in apt_name.split(","):
            p = part.strip()
            if p in label_by_apt:
                return label_by_apt[p]
    if label_by_type and apt_type_id is not None:
        return label_by_type.get(apt_type_id, "")
    return ""


def _get_asd_seniority_supplement(
    employee_type: Optional[str],
    start_date_val: Optional[Any],
    year: int,
    month: int,
) -> int:
    """תוספת ותק ASD באגורות - למדריך קבוע עם שנה+ ותק במערך ASD."""
    if employee_type != "permanent" or not start_date_val:
        return 0
    if isinstance(start_date_val, datetime):
        start_dt = start_date_val.date()
    elif isinstance(start_date_val, date):
        start_dt = start_date_val
    else:
        try:
            start_dt = datetime.fromtimestamp(start_date_val, LOCAL_TZ).date()
        except (ValueError, TypeError, OSError):
            return 0
    report_dt = date(year, month, 1)
    try:
        threshold_date = start_dt.replace(
            year=start_dt.year + ASD_SENIORITY_YEARS_THRESHOLD
        )
    except ValueError:
        # Leap-day starts reach a full calendar year on Feb 28 in non-leap years.
        threshold_date = start_dt.replace(
            year=start_dt.year + ASD_SENIORITY_YEARS_THRESHOLD,
            day=28,
        )
    if report_dt >= threshold_date:
        return ASD_SENIORITY_SUPPLEMENT
    return 0


def _fetch_weekday_overrides(conn) -> tuple[dict[int, tuple[str, str]], dict[int, tuple[str, str]]]:
    """
    שאילתה אחת ל-shift_time_overrides עבור משמרת חול.

    Returns:
        (apt_overrides, ha_defaults) — מיפוי דירה/מערך -> (start_time, end_time)
    """
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute("""
            SELECT apartment_id, housing_array_id, start_time, end_time
            FROM shift_time_overrides
            WHERE shift_type_id = %s AND is_active = true
        """, (WEEKDAY_SHIFT_TYPE_ID,))

        apt_overrides: dict[int, tuple[str, str]] = {}
        ha_defaults: dict[int, tuple[str, str]] = {}
        for row in cursor.fetchall():
            if row["apartment_id"] is not None:
                apt_overrides[row["apartment_id"]] = (row["start_time"], row["end_time"])
            elif row["housing_array_id"] is not None:
                ha_defaults[row["housing_array_id"]] = (row["start_time"], row["end_time"])

        return apt_overrides, ha_defaults
    finally:
        cursor.close()


def _override_identity(row: dict) -> tuple:
    original_id = row.get("original_override_id")
    if original_id is not None:
        return ("id", original_id)
    if row.get("apartment_id") is not None:
        return ("apartment", row.get("shift_type_id"), row.get("apartment_id"))
    if row.get("housing_array_id") is not None:
        return ("housing", row.get("shift_type_id"), row.get("housing_array_id"))
    return ("global", row.get("shift_type_id"))


def _time_to_hhmm(value: Any) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return str(value)[:5]


def _rows_to_weekday_override_maps(
    rows: list[dict],
) -> tuple[dict[int, tuple[str, str]], dict[int, tuple[str, str]]]:
    apt_overrides: dict[int, tuple[str, str]] = {}
    ha_defaults: dict[int, tuple[str, str]] = {}
    for row in rows:
        if not row.get("is_active"):
            continue
        if row.get("start_time") is None or row.get("end_time") is None:
            continue
        value = (_time_to_hhmm(row["start_time"]), _time_to_hhmm(row["end_time"]))
        if row.get("apartment_id") is not None:
            apt_overrides[row["apartment_id"]] = value
        elif row.get("housing_array_id") is not None:
            ha_defaults[row["housing_array_id"]] = value
    return apt_overrides, ha_defaults


def _fetch_weekday_overrides_for_month(
    conn,
    year: int,
    month: int,
) -> tuple[dict[int, tuple[str, str]], dict[int, tuple[str, str]]]:
    """
    טעינת override שעות חול לפי חודש החישוב.

    טבלת ההיסטוריה שומרת את הערך הישן עם חודש שממנו הוא הפסיק להיות תקף.
    לכן בחישוב חודש מוקדם יותר משתמשים ברשומת ההיסטוריה העתידית הקרובה ביותר.
    """
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute("""
            SELECT id AS original_override_id, shift_type_id, apartment_id,
                   housing_array_id, start_time, end_time, is_active
            FROM shift_time_overrides
            WHERE shift_type_id = %s
            ORDER BY id
        """, (WEEKDAY_SHIFT_TYPE_ID,))
        rows_by_identity = {
            _override_identity(dict(row)): dict(row)
            for row in cursor.fetchall()
        }

        cursor.execute("""
            SELECT DISTINCT ON (
                COALESCE(original_override_id, 0),
                shift_type_id,
                COALESCE(apartment_id, 0),
                COALESCE(housing_array_id, 0)
            )
                original_override_id, shift_type_id, apartment_id, housing_array_id,
                start_time, end_time, is_active, year, month, created_at, id
            FROM shift_time_overrides_history
            WHERE shift_type_id = %s
              AND (year > %s OR (year = %s AND month > %s))
            ORDER BY
                COALESCE(original_override_id, 0),
                shift_type_id,
                COALESCE(apartment_id, 0),
                COALESCE(housing_array_id, 0),
                year ASC,
                month ASC,
                created_at DESC,
                id DESC
        """, (WEEKDAY_SHIFT_TYPE_ID, year, year, month))
        for row in cursor.fetchall():
            row_dict = dict(row)
            rows_by_identity[_override_identity(row_dict)] = row_dict

        return _rows_to_weekday_override_maps(list(rows_by_identity.values()))
    finally:
        cursor.close()


def _resolve_override_for_apartment(
    apt_id: int,
    apt_overrides: dict[int, tuple[str, str]],
    ha_defaults: dict[int, tuple[str, str]],
    apartment_housing_map: dict[int, int | None],
) -> tuple[str, str] | None:
    """מציאת override לדירה — עדיפות: דירה ספציפית > מערך דיור."""
    override = apt_overrides.get(apt_id)
    if override is None:
        ha_id = apartment_housing_map.get(apt_id)
        if ha_id:
            override = ha_defaults.get(ha_id)
    return override


def _build_weekday_work_overrides(
    apartment_ids: set[int],
    apartment_housing_map: dict[int, int | None],
    apt_overrides: dict[int, tuple[str, str]],
    ha_defaults: dict[int, tuple[str, str]],
) -> dict[int, list[dict]]:
    """
    בניית מפת סגמנטי עבודה לחופשה/מחלה לכל דירה (ללא כוננות).

    Returns:
        מיפוי apartment_id -> רשימת סגמנטי עבודה (work בלבד)
    """
    result: dict[int, list[dict]] = {}
    for apt_id in apartment_ids:
        override = _resolve_override_for_apartment(apt_id, apt_overrides, ha_defaults, apartment_housing_map)
        if override:
            result[apt_id] = _build_sick_vacation_segments(override[0], override[1])
    return result


def _build_weekday_shift_overrides(
    apartment_ids: set[int],
    apartment_housing_map: dict[int, int | None],
    apt_overrides: dict[int, tuple[str, str]],
    ha_defaults: dict[int, tuple[str, str]],
    base_segments: list[dict],
) -> dict[int, list[dict]]:
    """
    בניית מפת סגמנטים מלאים (work + standby) למשמרת חול לפי override.

    הכוננות נשמרת כמו שהיא (עם segment_id המקורי).
    רק סגמנטי העבודה משתנים לפי override.

    Returns:
        מיפוי apartment_id -> רשימת סגמנטים [work, standby, work]
    """
    # מציאת סגמנט הכוננות המקורי
    standby_seg = None
    for seg in base_segments:
        if seg.get("segment_type") == "standby":
            standby_seg = dict(seg)
            break

    if not standby_seg:
        return {}

    standby_start_str = standby_seg["start_time"]
    standby_end_str = standby_seg["end_time"]

    result: dict[int, list[dict]] = {}
    for apt_id in apartment_ids:
        override = _resolve_override_for_apartment(apt_id, apt_overrides, ha_defaults, apartment_housing_map)
        if not override:
            continue

        override_start, override_end = override
        segments = [
            {"start_time": override_start, "end_time": standby_start_str, "segment_type": "work", "id": None},
            standby_seg,
            {"start_time": standby_end_str, "end_time": override_end, "segment_type": "work", "id": None},
        ]
        result[apt_id] = segments

    return result


# =============================================================================
# Wage Rate Calculation (moved from core/wage_calculator.py)
# =============================================================================

def calculate_wage_rate(
    minutes_in_chain: int,
    is_shabbat: bool,
    is_night_shift: bool = False
) -> str:
    """
    Determine the wage rate label based on hours worked in chain and Shabbat status.

    Args:
        minutes_in_chain: Total minutes worked so far in the current chain
        is_shabbat: Whether this minute falls within Shabbat hours
        is_night_shift: Whether this is a night shift (uses 7-hour day instead of 8)

    Returns:
        Rate label: "100%", "125%", "150%", "175%", or "200%"
    """
    # Use night shift thresholds if applicable (7 hours instead of 8)
    regular_limit = NIGHT_REGULAR_HOURS_LIMIT if is_night_shift else REGULAR_HOURS_LIMIT
    overtime_limit = NIGHT_OVERTIME_125_LIMIT if is_night_shift else OVERTIME_125_LIMIT

    if minutes_in_chain <= regular_limit:
        return "150%" if is_shabbat else "100%"
    elif minutes_in_chain <= overtime_limit:
        return "175%" if is_shabbat else "125%"
    else:
        return "200%" if is_shabbat else "150%"


# =============================================================================
# Chain Wage Calculation (moved from core/wage_calculator.py)
# =============================================================================

def _calculate_chain_wages(
    chain_segments: List[Tuple[int, int, int, date]],
    shabbat_cache: Dict[str, Dict[str, str]],
    minutes_offset: int = 0,
    is_night_shift: bool = False,
    premium_windows: Optional[List[PremiumWindow]] = None,
) -> Dict[str, Any]:
    """
    חישוב שכר לרצף עבודה (chain) בשיטת בלוקים.

    במקום לעבור דקה-דקה, מחשב בלוקים לפי גבולות:
    - 480 דקות (מעבר 100% -> 125%) - או 420 למשמרת לילה
    - 600 דקות (מעבר 125% -> 150%) - או 540 למשמרת לילה
    - גבולות שבת/חג (כניסה/יציאה מ-shabbat_cache)
    - גבולות חלונות פרימיום (פורים/עצמאות/בחירות מ-premium_windows)

    Args:
        chain_segments: List of (start_min, end_min, shift_id, actual_date) tuples
        shabbat_cache: Cache of Shabbat times
        minutes_offset: Minutes already worked in this chain (from previous day's carryover)
        is_night_shift: Whether this is a night shift (uses 7-hour day instead of 8)
        premium_windows: רשימת חלונות פרימיום (פורים/עצמאות/בחירות). None = אין.

    Returns:
        Dict with calc100, calc125, calc150, calc175, calc200,
        calc150_shabbat, calc150_overtime, calc150_shabbat_100, calc150_shabbat_50,
        calc150_premium, calc175_premium, calc200_premium — שדות פרימיום,
        segments_detail - list of (start_min, end_min, label, is_shabbat) for display.
    """
    windows = premium_windows or []
    result = {
        "calc100": 0, "calc125": 0, "calc150": 0, "calc175": 0, "calc200": 0,
        "calc150_shabbat": 0, "calc150_overtime": 0,
        "calc150_shabbat_100": 0, "calc150_shabbat_50": 0,
        "calc150_premium": 0, "calc175_premium": 0, "calc200_premium": 0,
        "segments_detail": []
    }

    if not chain_segments:
        return result

    # Flatten all segments into a list of (abs_start, abs_end, actual_date) in continuous minutes
    # and calculate total chain minutes
    total_chain_minutes = 0
    flat_segments = []

    for seg_start, seg_end, seg_shift_id, seg_actual_date in chain_segments:
        flat_segments.append((seg_start, seg_end, seg_actual_date))
        total_chain_minutes += (seg_end - seg_start)

    # Process in blocks based on overtime thresholds
    # Use night shift thresholds if applicable (7 hours instead of 8)
    regular_limit = NIGHT_REGULAR_HOURS_LIMIT if is_night_shift else REGULAR_HOURS_LIMIT
    overtime_limit = NIGHT_OVERTIME_125_LIMIT if is_night_shift else OVERTIME_125_LIMIT
    # Start from offset if this chain continues from previous day
    minutes_processed = minutes_offset

    _PREMIUM_FALLBACK = "פרימיום"

    for seg_start, seg_end, seg_actual_date in flat_segments:
        seg_duration = seg_end - seg_start
        seg_offset = 0

        # Get Shabbat/Holiday boundaries for THIS segment's actual date
        shabbat_enter, shabbat_exit = _get_shabbat_boundaries(seg_actual_date, shabbat_cache)
        seg_is_shabbat_or_holiday = (shabbat_enter > 0)

        # סיווג סוג היום: ערב חג/שבת, יום חג/שבת, או חול
        day_type = classify_day_type(seg_actual_date, shabbat_cache)
        seg_is_holy_day = (day_type == "holy")

        while seg_offset < seg_duration:
            current_abs_minute = seg_start + seg_offset
            current_chain_minute = minutes_processed + 1  # 1-based for wage calculation

            # Determine which overtime tier we're in
            if current_chain_minute <= regular_limit:
                tier_end = regular_limit
                base_rate = "100%"
                shabbat_rate = "150%"
            elif current_chain_minute <= overtime_limit:
                tier_end = overtime_limit
                base_rate = "125%"
                shabbat_rate = "175%"
            else:
                tier_end = float('inf')
                base_rate = "150%"
                shabbat_rate = "200%"

            # How many minutes until we hit the next tier?
            minutes_until_tier_change = tier_end - minutes_processed

            # How many minutes left in this segment?
            minutes_left_in_seg = seg_duration - seg_offset

            # Take the minimum
            block_size = min(minutes_until_tier_change, minutes_left_in_seg)

            # חיתוך בגבולות חלון פרימיום — כל בלוק יהיה כולו בתוך חלון אחד או כולו מחוץ לכולם
            # seg_actual_date כבר מכיל את התאריך הפיזי של הסגמנט, ו-seg_start
            # על הציר המורחב (0-1920) כבר מקודד את ימי חציית-חצות.
            # לכן מחשבים רק ימים *נוספים* מעבר למה ש-seg_start כבר מייצג.
            seg_base_days = seg_start // MINUTES_PER_DAY
            days_over = current_abs_minute // MINUTES_PER_DAY - seg_base_days
            physical_date = seg_actual_date + timedelta(days=days_over)
            physical_min = current_abs_minute % MINUTES_PER_DAY
            current_premium = get_window_at(windows, physical_date, physical_min) if windows else None
            if windows:
                block_size = min(
                    block_size,
                    minutes_until_state_change(windows, physical_date, physical_min, block_size),
                )

            # Helper to add segment detail
            def add_segment_detail(start_min, end_min, rate_label, is_shabbat):
                result["segments_detail"].append((start_min, end_min, rate_label, is_shabbat))

            if current_premium is not None:
                # נמצאים בחלון פרימיום (יום מיוחד)
                block_abs_start = current_abs_minute
                block_abs_end = current_abs_minute + block_size
                premium_label = current_premium.name or _PREMIUM_FALLBACK

                if current_premium.rate_pct == 150:
                    # 150% — tier stacking כמו שבת: 150%/175%/200% לפי שעת הרצף
                    if shabbat_rate == "150%":
                        result["calc150"] += block_size
                        result["calc150_shabbat"] += block_size
                        result["calc150_shabbat_100"] += block_size
                        result["calc150_shabbat_50"] += block_size
                        result["calc150_premium"] += block_size
                        add_segment_detail(block_abs_start, block_abs_end, f"150% {premium_label}", True)
                    elif shabbat_rate == "175%":
                        result["calc175"] += block_size
                        result["calc175_premium"] += block_size
                        add_segment_detail(block_abs_start, block_abs_end, f"175% {premium_label}", True)
                    else:
                        result["calc200"] += block_size
                        result["calc200_premium"] += block_size
                        add_segment_detail(block_abs_start, block_abs_end, f"200% {premium_label}", True)
                else:
                    # Flat rate (למשל 200%) — ללא tier stacking
                    rate_label = f"{current_premium.rate_pct}% {premium_label}"
                    if current_premium.rate_pct >= 200:
                        result["calc200"] += block_size
                        result["calc200_premium"] += block_size
                    else:
                        result["calc150"] += block_size
                        result["calc150_premium"] += block_size
                    add_segment_detail(block_abs_start, block_abs_end, rate_label, True)

            # Now check Shabbat/Holiday boundaries within this block
            elif seg_is_shabbat_or_holiday:
                block_abs_start = current_abs_minute
                block_abs_end = current_abs_minute + block_size

                # נרמול זמנים - זמנים מעל 1440 הם בבוקר (אחרי חצות)
                # לדוגמה: 1830 = 06:30 בבוקר של היום הבא
                actual_block_start = block_abs_start % MINUTES_PER_DAY
                actual_block_end = block_abs_end % MINUTES_PER_DAY
                # אם הסגמנט חוצה חצות, end יהיה קטן מ-start
                if actual_block_end <= actual_block_start and block_abs_end > block_abs_start:
                    actual_block_end = block_abs_end % MINUTES_PER_DAY or MINUTES_PER_DAY

                # Adjust for day offset (if segment crosses midnight)
                # day_offset מייצג את המרחק מחצות יום שישי
                # - יום שישי: offset = 0 (כל הזמנים ביום שישי הם לפני או אחרי כניסת שבת)
                # - יום שבת: offset = 1440 (כל הזמנים הם ביחס לחצות שישי + 1440)
                #
                # חשוב: משתמשים ב-actual_block_start/end (הזמן האמיתי ביום 0-1440)
                # ולא ב-block_abs_start/end (הזמן המנורמל שיכול להיות 1440+)
                # כי אנחנו רוצים לדעת מה השעה בפועל ביום הספציפי
                day_offset_start = 0
                day_offset_end = 0
                if seg_is_holy_day:
                    # ביום שבת/חג, כל הזמנים הם ביחס לחצות הערב + 1440
                    # זמנים בבוקר (00:00-08:00) עדיין שייכים לשבת/חג
                    # הבדיקה אם זה אחרי צאת שבת/חג תתבצע מול shabbat_exit
                    day_offset_start = MINUTES_PER_DAY
                    day_offset_end = MINUTES_PER_DAY
                # עבור ערב שבת/חג - לא צריך offset, הזמנים כבר ביחס לחצות הערב

                abs_start_from_fri = actual_block_start + day_offset_start
                abs_end_from_fri = actual_block_end + day_offset_end

                # Split block at Shabbat boundaries
                # Case 1: Entirely before Shabbat
                if abs_end_from_fri <= shabbat_enter:
                    if base_rate == "100%":
                        result["calc100"] += block_size
                        add_segment_detail(block_abs_start, block_abs_end, "100%", False)
                    elif base_rate == "125%":
                        result["calc125"] += block_size
                        add_segment_detail(block_abs_start, block_abs_end, "125%", False)
                    else:
                        result["calc150"] += block_size
                        result["calc150_overtime"] += block_size
                        add_segment_detail(block_abs_start, block_abs_end, "150%", False)

                # Case 2: Entirely during Shabbat
                elif abs_start_from_fri >= shabbat_enter and abs_end_from_fri <= shabbat_exit:
                    if shabbat_rate == "150%":
                        result["calc150"] += block_size
                        result["calc150_shabbat"] += block_size
                        result["calc150_shabbat_100"] += block_size
                        result["calc150_shabbat_50"] += block_size
                        add_segment_detail(block_abs_start, block_abs_end, "150% שבת", True)
                    elif shabbat_rate == "175%":
                        result["calc175"] += block_size
                        add_segment_detail(block_abs_start, block_abs_end, "175% שבת", True)
                    else:
                        result["calc200"] += block_size
                        add_segment_detail(block_abs_start, block_abs_end, "200% שבת", True)

                # Case 3: Entirely after Shabbat
                elif abs_start_from_fri >= shabbat_exit:
                    if base_rate == "100%":
                        result["calc100"] += block_size
                        add_segment_detail(block_abs_start, block_abs_end, "100%", False)
                    elif base_rate == "125%":
                        result["calc125"] += block_size
                        add_segment_detail(block_abs_start, block_abs_end, "125%", False)
                    else:
                        result["calc150"] += block_size
                        result["calc150_overtime"] += block_size
                        add_segment_detail(block_abs_start, block_abs_end, "150%", False)

                # Case 4: Block crosses Shabbat start
                elif abs_start_from_fri < shabbat_enter < abs_end_from_fri:
                    before_shabbat = shabbat_enter - abs_start_from_fri
                    during_shabbat = abs_end_from_fri - shabbat_enter

                    # Before Shabbat part
                    if base_rate == "100%":
                        result["calc100"] += before_shabbat
                        add_segment_detail(block_abs_start, block_abs_start + before_shabbat, "100%", False)
                    elif base_rate == "125%":
                        result["calc125"] += before_shabbat
                        add_segment_detail(block_abs_start, block_abs_start + before_shabbat, "125%", False)
                    else:
                        result["calc150"] += before_shabbat
                        result["calc150_overtime"] += before_shabbat
                        add_segment_detail(block_abs_start, block_abs_start + before_shabbat, "150%", False)

                    # During Shabbat part
                    shabbat_start_abs = block_abs_start + before_shabbat
                    if shabbat_rate == "150%":
                        result["calc150"] += during_shabbat
                        result["calc150_shabbat"] += during_shabbat
                        result["calc150_shabbat_100"] += during_shabbat
                        result["calc150_shabbat_50"] += during_shabbat
                        add_segment_detail(shabbat_start_abs, block_abs_end, "150% שבת", True)
                    elif shabbat_rate == "175%":
                        result["calc175"] += during_shabbat
                        add_segment_detail(shabbat_start_abs, block_abs_end, "175% שבת", True)
                    else:
                        result["calc200"] += during_shabbat
                        add_segment_detail(shabbat_start_abs, block_abs_end, "200% שבת", True)

                # Case 5: Block crosses Shabbat end
                elif abs_start_from_fri < shabbat_exit < abs_end_from_fri:
                    during_shabbat = shabbat_exit - abs_start_from_fri
                    after_shabbat = abs_end_from_fri - shabbat_exit

                    # During Shabbat part
                    if shabbat_rate == "150%":
                        result["calc150"] += during_shabbat
                        result["calc150_shabbat"] += during_shabbat
                        result["calc150_shabbat_100"] += during_shabbat
                        result["calc150_shabbat_50"] += during_shabbat
                        add_segment_detail(block_abs_start, block_abs_start + during_shabbat, "150% שבת", True)
                    elif shabbat_rate == "175%":
                        result["calc175"] += during_shabbat
                        add_segment_detail(block_abs_start, block_abs_start + during_shabbat, "175% שבת", True)
                    else:
                        result["calc200"] += during_shabbat
                        add_segment_detail(block_abs_start, block_abs_start + during_shabbat, "200% שבת", True)

                    # After Shabbat part
                    after_start_abs = block_abs_start + during_shabbat
                    if base_rate == "100%":
                        result["calc100"] += after_shabbat
                        add_segment_detail(after_start_abs, block_abs_end, "100%", False)
                    elif base_rate == "125%":
                        result["calc125"] += after_shabbat
                        add_segment_detail(after_start_abs, block_abs_end, "125%", False)
                    else:
                        result["calc150"] += after_shabbat
                        result["calc150_overtime"] += after_shabbat
                        add_segment_detail(after_start_abs, block_abs_end, "150%", False)

                else:
                    # Fallback - shouldn't happen but just in case
                    if base_rate == "100%":
                        result["calc100"] += block_size
                        add_segment_detail(block_abs_start, block_abs_end, "100%", False)
                    elif base_rate == "125%":
                        result["calc125"] += block_size
                        add_segment_detail(block_abs_start, block_abs_end, "125%", False)
                    else:
                        result["calc150"] += block_size
                        result["calc150_overtime"] += block_size
                        add_segment_detail(block_abs_start, block_abs_end, "150%", False)
            else:
                # Not Friday or Saturday - simple calculation
                if base_rate == "100%":
                    result["calc100"] += block_size
                    result["segments_detail"].append((current_abs_minute, current_abs_minute + block_size, "100%", False))
                elif base_rate == "125%":
                    result["calc125"] += block_size
                    result["segments_detail"].append((current_abs_minute, current_abs_minute + block_size, "125%", False))
                else:
                    result["calc150"] += block_size
                    result["calc150_overtime"] += block_size
                    result["segments_detail"].append((current_abs_minute, current_abs_minute + block_size, "150%", False))

            seg_offset += block_size
            minutes_processed += block_size

    # Merge adjacent segments with the same label for cleaner display
    merged_segments = []
    for seg in result["segments_detail"]:
        if merged_segments and merged_segments[-1][2] == seg[2] and merged_segments[-1][1] == seg[0]:
            # Merge with previous segment
            merged_segments[-1] = (merged_segments[-1][0], seg[1], seg[2], seg[3])
        else:
            merged_segments.append(seg)
    result["segments_detail"] = merged_segments

    return result


# =============================================================================
# Helper Functions
# =============================================================================

def calculate_rate_from_housing_rates(
    rate_row: dict,
    is_married: bool,
    is_shabbat: bool,
    minimum_wage: float,
    hourly_wage_supplement: float = 0
) -> float:
    """
    חישוב תעריף שעתי מתוך נתוני shift_type_housing_rates.

    Args:
        rate_row: שורה מטבלת shift_type_housing_rates עם כל שדות התעריף
        is_married: האם העובד נשוי
        is_shabbat: האם זו משמרת בזמן שבת/חג
        minimum_wage: שכר מינימום שעתי
        hourly_wage_supplement: תוספת שעתית מסוג הדירה (באגורות)

    Returns:
        התעריף השעתי בשקלים
    """
    if is_shabbat:
        rate = rate_row.get('shabbat_rate')
        pct = rate_row.get('shabbat_wage_percentage')
    elif is_married:
        rate = rate_row.get('weekday_married_rate')
        pct = rate_row.get('weekday_married_wage_percentage')
    else:
        rate = rate_row.get('weekday_single_rate')
        pct = rate_row.get('weekday_single_wage_percentage')

    if rate:
        return float(rate) / 100  # המרה מאגורות לשקלים
    if pct:
        return minimum_wage * float(pct) / 100

    # אין תעריף ספציפי - שכר מינימום + תוספת סוג דירה
    supplement = float(hourly_wage_supplement) / 100 if hourly_wage_supplement else 0
    return minimum_wage + supplement


def get_effective_hourly_rate(
    report: dict,
    minimum_wage: float,
    is_shabbat: bool = False,
    housing_rates_cache: dict = None
) -> float:
    """
    קבלת תעריף שעתי אפקטיבי למשמרת.

    סדר עדיפויות:
    1. אם יש housing_rates_cache ויש רשומה מתאימה - חישוב לפי מערך דיור
    2. אחרת - שכר מינימום + תוספת סוג דירה (אם יש)

    Args:
        report: dict עם shift_type_id, housing_array_id, is_married, hourly_wage_supplement
        minimum_wage: שכר מינימום שעתי
        is_shabbat: האם המשמרת בזמן שבת/חג
        housing_rates_cache: cache של תעריפי מערכי דיור

    Returns:
        התעריף השעתי בשקלים
    """
    shift_type_id = report.get('shift_type_id')
    housing_array_id = report.get('housing_array_id')
    is_married = report.get('is_married', False)
    hourly_wage_supplement = report.get('hourly_wage_supplement') or 0

    # חיפוש בתעריפי מערכי דיור
    if housing_rates_cache and shift_type_id and housing_array_id:
        key = (shift_type_id, housing_array_id)
        if key in housing_rates_cache:
            return calculate_rate_from_housing_rates(
                housing_rates_cache[key],
                is_married,
                is_shabbat,
                minimum_wage,
                hourly_wage_supplement
            )

    # ברירת מחדל - שכר מינימום + תוספת סוג דירה
    supplement = float(hourly_wage_supplement) / 100 if hourly_wage_supplement else 0
    return minimum_wage + supplement


def _fetch_prev_month_sick_dates(conn, person_id: int, year: int, month: int) -> list[date]:
    """
    שליפת תאריכי מחלה מהחודש הקודם לצורך המשכיות רצף חוצה חודשים.

    Args:
        conn: חיבור DB
        person_id: מזהה עובד
        year: שנה של החודש הנוכחי
        month: חודש נוכחי

    Returns:
        רשימת תאריכים עם דיווחי מחלה מהחודש הקודם
    """
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    prev_start, prev_end = month_range_ts(prev_year, prev_month)
    rows = conn.execute("""
        SELECT DISTINCT tr.date
        FROM time_reports tr
        JOIN shift_types st ON st.id = tr.shift_type_id
        WHERE tr.person_id = %s AND tr.date >= %s AND tr.date < %s
          AND st.name LIKE %s
        ORDER BY tr.date
    """, (person_id, prev_start.date(), prev_end.date(), "%מחלה%")).fetchall()
    return [r["date"] if isinstance(r["date"], date) else r["date"].date() for r in rows]


def _filter_previous_month_carryover_reports(all_reports: list[dict[str, Any]], person_id: int) -> list[dict[str, Any]]:
    """
    מסנן דיווחים לחישוב carryover מחודש קודם.

    דיווחי מחלה/חופשה ללא שעות אינם חלק מרצף עבודה, אבל כן שוברים אותו.
    לכן שומרים רק דיווחים מתוזמנים שמופיעים אחרי יום השבירה האחרון.
    """
    filtered_reports: list[dict[str, Any]] = []
    latest_break_date: date | None = None

    for report in all_reports:
        report_start = report.get("start_time")
        report_end = report.get("end_time")
        report_date = report["date"]
        if isinstance(report_date, datetime):
            report_date = report_date.date()

        if report_start and report_end:
            filtered_reports.append(report)
            continue

        shift_name = (report.get("shift_name") or "").strip()
        if "מחלה" in shift_name or "חופשה" in shift_name:
            if latest_break_date is None or report_date > latest_break_date:
                latest_break_date = report_date
            continue

        logger.warning(
            "Skipping previous-month carryover report with missing times for person_id=%s on %s (shift_id=%s, shift_name=%s)",
            person_id,
            report_date,
            report.get("shift_type_id"),
            shift_name or "?",
        )

    if latest_break_date is None:
        return filtered_reports

    return [
        report
        for report in filtered_reports
        if (report["date"].date() if isinstance(report["date"], datetime) else report["date"]) > latest_break_date
    ]


def _calculate_previous_month_carryover(
    conn,
    person_id: int,
    year: int,
    month: int,
    minimum_wage: float = 0,
    preloaded_reports: list[dict[str, Any]] | None = None,
    preloaded_segments: dict[int, list[dict]] | None = None,
    preloaded_housing_rates_cache: dict | None = None,
) -> tuple[int, int, int | None, int, int | None]:
    """
    חישוב carryover מהחודש הקודם - חיפוש איטרטיבי אחורה עד שבירת רצף.

    הפונקציה מחפשת אחורה מהיום האחרון של החודש הקודם, יום אחר יום,
    עד שמוצאת יום ללא דיווחים (שבירת רצף).

    מחזיר את סך הדקות שנצברו ברצף האחרון, זמן הסיום שלו, shift_id של הרצף,
    שעות הלילה ברצף, ו-housing_array_id של הרצף.

    חשוב: הלוגיקה זהה ללוגיקת אמצע החודש:
    - כוננות שוברת רצף רק אם אין עבודה שחופפת לה
    - הפסקה >= 60 דקות שוברת רצף
    - שינוי תעריף בין משמרות שוברת רצף (אבל מעביר offset)

    Args:
        conn: חיבור לDB
        person_id: מזהה העובד
        year: שנה נוכחית
        month: חודש נוכחי
        minimum_wage: שכר מינימום לחישוב תעריפים

    Returns:
        tuple של (דקות ברצף, זמן סיום, shift_id, דקות לילה, housing_array_id)
        או (0, 0, None, 0, None) אם אין carryover
    """
    # חישוב היום האחרון של החודש הקודם
    if month == 1:
        prev_year = year - 1
        prev_month = 12
    else:
        prev_year = year
        prev_month = month - 1

    # מציאת היום האחרון של החודש הקודם
    if prev_month == 12:
        last_day = 31
    elif prev_month in (4, 6, 9, 11):
        last_day = 30
    elif prev_month == 2:
        # בדיקת שנה מעוברת
        if (prev_year % 4 == 0 and prev_year % 100 != 0) or (prev_year % 400 == 0):
            last_day = 29
        else:
            last_day = 28
    else:
        last_day = 31

    last_day_date = date(prev_year, prev_month, last_day)

    cursor = None

    # חיפוש איטרטיבי אחורה - מוצאים את היום הראשון עם דיווחים
    # והולכים אחורה עד שמוצאים יום ללא דיווחים (שבירת רצף)
    # מגבלת בטיחות: מקסימום 31 ימים (חודש שלם)
    MAX_LOOKBACK_DAYS = 31

    if preloaded_reports is None:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        earliest_date = last_day_date

        # Get housing array filter
        housing_filter = get_housing_array_filter()

        for days_back in range(MAX_LOOKBACK_DAYS):
            check_date = last_day_date - timedelta(days=days_back)

            # בדיקה אם יש דיווחים ביום הזה (עם סינון לפי מערך דיור אם נדרש)
            if housing_filter is not None:
                cursor.execute("""
                    SELECT COUNT(*) as cnt FROM time_reports tr
                    JOIN apartments ap ON ap.id = tr.apartment_id
                    WHERE tr.person_id = %s AND tr.date = %s AND ap.housing_array_id = %s
                """, (person_id, check_date, housing_filter))
            else:
                cursor.execute("""
                    SELECT COUNT(*) as cnt FROM time_reports
                    WHERE person_id = %s AND date = %s
                """, (person_id, check_date))
            row = cursor.fetchone()

            if row["cnt"] == 0:
                # אין דיווחים ביום הזה - זה גבול הרצף (יום ללא עבודה)
                break
            earliest_date = check_date

        # שליפת כל הדיווחים מהטווח שמצאנו (עם סינון לפי מערך דיור אם נדרש)
        if housing_filter is not None:
            cursor.execute("""
                SELECT tr.date, tr.start_time, tr.end_time, tr.shift_type_id, tr.apartment_id,
                       st.name AS shift_name,
                       ap.housing_array_id, at.hourly_wage_supplement, p.is_married
                FROM time_reports tr
                LEFT JOIN shift_types st ON st.id = tr.shift_type_id
                JOIN apartments ap ON ap.id = tr.apartment_id
                LEFT JOIN apartment_types at ON at.id = ap.apartment_type_id
                LEFT JOIN people p ON p.id = tr.person_id
                WHERE tr.person_id = %s AND tr.date >= %s AND tr.date <= %s
                  AND ap.housing_array_id = %s
                ORDER BY tr.date, tr.start_time
            """, (person_id, earliest_date, last_day_date, housing_filter))
        else:
            cursor.execute("""
                SELECT tr.date, tr.start_time, tr.end_time, tr.shift_type_id, tr.apartment_id,
                       st.name AS shift_name,
                       ap.housing_array_id, at.hourly_wage_supplement, p.is_married
                FROM time_reports tr
                LEFT JOIN shift_types st ON st.id = tr.shift_type_id
                LEFT JOIN apartments ap ON ap.id = tr.apartment_id
                LEFT JOIN apartment_types at ON at.id = ap.apartment_type_id
                LEFT JOIN people p ON p.id = tr.person_id
                WHERE tr.person_id = %s AND tr.date >= %s AND tr.date <= %s
                ORDER BY tr.date, tr.start_time
            """, (person_id, earliest_date, last_day_date))
        all_reports = cursor.fetchall()
    else:
        report_dates = set()
        for report in preloaded_reports:
            report_date = report["date"]
            if isinstance(report_date, datetime):
                report_date = report_date.date()
            if report_date <= last_day_date:
                report_dates.add(report_date)

        if last_day_date not in report_dates:
            return (0, 0, None, 0, None)

        earliest_date = last_day_date
        for days_back in range(MAX_LOOKBACK_DAYS):
            check_date = last_day_date - timedelta(days=days_back)
            if check_date not in report_dates:
                break
            earliest_date = check_date

        all_reports = []
        for report in preloaded_reports:
            report_date = report["date"]
            if isinstance(report_date, datetime):
                report_date = report_date.date()
            if earliest_date <= report_date <= last_day_date:
                all_reports.append(report)

    all_reports = _filter_previous_month_carryover_reports(all_reports, person_id)

    if not all_reports:
        if cursor is not None:
            cursor.close()
        return (0, 0, None, 0, None)

    # שליפת סגמנטים של כל סוגי המשמרות הרלוונטיים
    shift_ids = list({r["shift_type_id"] for r in all_reports if r["shift_type_id"]})
    if not shift_ids:
        if cursor is not None:
            cursor.close()
        return (0, 0, None, 0, None)

    # בניית מפה של סגמנטים לפי סוג משמרת
    segments_by_shift = {}
    if preloaded_segments is not None:
        for shift_id in shift_ids:
            for seg in preloaded_segments.get(shift_id, []):
                segments_by_shift.setdefault(shift_id, []).append({
                    "type": seg.get("segment_type") or seg.get("type"),
                    "start": seg.get("start_time") or seg.get("start"),
                    "end": seg.get("end_time") or seg.get("end")
                })
        if cursor is not None:
            cursor.close()
    else:
        placeholders = ",".join(["%s"] * len(shift_ids))
        cursor.execute(f"""
            SELECT shift_type_id, segment_type, start_time, end_time
            FROM shift_time_segments
            WHERE shift_type_id IN ({placeholders})
            ORDER BY shift_type_id, order_index
        """, tuple(shift_ids))
        shift_segments = cursor.fetchall()
        cursor.close()

        for seg in shift_segments:
            shift_id = seg["shift_type_id"]
            if shift_id not in segments_by_shift:
                segments_by_shift[shift_id] = []
            segments_by_shift[shift_id].append({
                "type": seg["segment_type"],
                "start": seg["start_time"],
                "end": seg["end_time"]
            })

    # טעינת תעריפי מערכי דיור (לחודש הקודם)
    housing_rates_cache = preloaded_housing_rates_cache
    if housing_rates_cache is None:
        housing_rates_cache = get_all_housing_rates_for_month(conn, prev_year, prev_month)

    # בניית מפת תעריפים לפי (shift_id, housing_array_id) עם תעריפי חול ושבת
    shift_rates = {}
    for r in all_reports:
        shift_id = r.get("shift_type_id")
        housing_array_id = r.get("housing_array_id")
        rate_key = (shift_id, housing_array_id)
        if shift_id and rate_key not in shift_rates:
            weekday_rate = get_effective_hourly_rate(
                r, minimum_wage, is_shabbat=False, housing_rates_cache=housing_rates_cache
            )
            shabbat_rate = get_effective_hourly_rate(
                r, minimum_wage, is_shabbat=True, housing_rates_cache=housing_rates_cache
            )
            shift_rates[rate_key] = {"weekday": weekday_rate, "shabbat": shabbat_rate}

    # ארגון דיווחים לפי ימים - בסדר כרונולוגי
    reports_by_day = {}
    for r in all_reports:
        r_date = r["date"]
        if isinstance(r_date, datetime):
            r_date = r_date.date()
        if r_date not in reports_by_day:
            reports_by_day[r_date] = []
        reports_by_day[r_date].append(r)

    # מיון הימים בסדר כרונולוגי
    sorted_days = sorted(reports_by_day.keys())

    # בניית רשימת אירועים לכל יום (בציר מנורמל 08:00-08:00)
    # כל יום מקבל offset של 1440 דקות ביחס ליום הקודם
    all_events = []
    work_segments_all = []  # כל סגמנטי העבודה לבדיקת חפיפה עם כוננויות
    day_base_offset = 0  # offset מצטבר לכל יום

    for day_idx, day_date in enumerate(sorted_days):
        # חישוב offset ביחס ליום הראשון
        if day_idx == 0:
            day_base_offset = 0
        else:
            # כל יום מקבל 1440 דקות נוספות
            prev_day = sorted_days[day_idx - 1]
            days_diff = (day_date - prev_day).days
            day_base_offset += days_diff * MINUTES_PER_DAY

        day_reports = reports_by_day[day_date]

        for r in day_reports:
            report_start_str = r["start_time"]
            report_end_str = r["end_time"]
            shift_id = r["shift_type_id"]
            housing_array_id = r.get("housing_array_id")

            # המרת זמני דיווח לדקות
            rs_parts = report_start_str.split(":")
            report_start_min = int(rs_parts[0]) * 60 + int(rs_parts[1])
            re_parts = report_end_str.split(":")
            report_end_min = int(re_parts[0]) * 60 + int(re_parts[1])

            # בדיקה אם זו משמרת בוקר של אותו יום (לפני 08:00)
            # משמרת כזו לא רלוונטית ל-carryover כי היא לא חלק מיום העבודה 08:00-08:00
            is_morning_only_shift = (
                report_start_min < 480 and
                report_end_min < 480 and
                report_end_min > report_start_min
            )
            if is_morning_only_shift:
                continue

            # נרמול לציר 08:00-08:00 של היום
            if report_start_min < 480:
                report_start_min += MINUTES_PER_DAY
            if report_end_min <= 480:
                report_end_min += MINUTES_PER_DAY
            if report_end_min <= report_start_min:
                report_end_min += MINUTES_PER_DAY

            # הוספת offset של היום
            report_start_min += day_base_offset
            report_end_min += day_base_offset

            # בדיקה אם יש סגמנטים מוגדרים למשמרת
            if shift_id in segments_by_shift:
                for seg in segments_by_shift[shift_id]:
                    seg_start_parts = seg["start"].split(":")
                    seg_start_min = int(seg_start_parts[0]) * 60 + int(seg_start_parts[1])
                    seg_end_parts = seg["end"].split(":")
                    seg_end_min = int(seg_end_parts[0]) * 60 + int(seg_end_parts[1])

                    # נרמול
                    if seg_start_min < 480:
                        seg_start_min += MINUTES_PER_DAY
                    if seg_end_min <= 480:
                        seg_end_min += MINUTES_PER_DAY
                    if seg_end_min <= seg_start_min:
                        seg_end_min += MINUTES_PER_DAY

                    # הוספת offset של היום
                    seg_start_min += day_base_offset
                    seg_end_min += day_base_offset

                    # בדיקת חפיפה עם הדיווח
                    overlap_start = max(report_start_min, seg_start_min)
                    overlap_end = min(report_end_min, seg_end_min)

                    if overlap_end > overlap_start:
                        event = {
                            "start": overlap_start,
                            "end": overlap_end,
                            "type": seg["type"],
                            "shift_id": shift_id,
                            "housing_array_id": housing_array_id
                        }
                        all_events.append(event)
                        if seg["type"] == "work":
                            work_segments_all.append((overlap_start, overlap_end))
            else:
                # אין סגמנטים מוגדרים - כל הדיווח הוא עבודה
                event = {
                    "start": report_start_min,
                    "end": report_end_min,
                    "type": "work",
                    "shift_id": shift_id,
                    "housing_array_id": housing_array_id
                }
                all_events.append(event)
                work_segments_all.append((report_start_min, report_end_min))

    if not all_events:
        return (0, 0, None, 0, None)

    # מיון לפי זמן התחלה
    all_events.sort(key=lambda x: x["start"])

    # בניית רצפי עבודה - רצף נשבר על ידי:
    # 1. הפסקה >= 60 דקות
    # 2. כוננות (רק אם אין עבודה שחופפת לה)
    # 3. שינוי תעריף (אבל מעביר offset)
    current_chain = []
    current_chain_shift_id = None
    current_chain_housing_array_id = None
    last_work_end = None
    chain_total = 0  # סה"כ דקות שנצברו (כולל מ-chains קודמים שנשברו בגלל תעריף)

    for evt in all_events:
        if evt["type"] == "standby":
            # כוננות שוברת רצף רק אם אין עבודה שחופפת לה
            standby_overlaps_work = any(
                ws[0] < evt["end"] and ws[1] > evt["start"]
                for ws in work_segments_all
            )
            if not standby_overlaps_work:
                # כוננות שוברת רצף
                if current_chain:
                    chain_total = 0  # כוננות מאפסת לגמרי
                    current_chain = []
                    current_chain_shift_id = None
                    current_chain_housing_array_id = None
                last_work_end = None
        else:
            # עבודה
            should_break = False
            break_reason = ""

            # בדיקת הפסקה גדולה
            if last_work_end is not None:
                gap = evt["start"] - last_work_end
                if gap >= BREAK_THRESHOLD_MINUTES if (year, month) >= (2026, 2) else gap > BREAK_THRESHOLD_MINUTES:
                    should_break = True
                    break_reason = "gap"

            # בדיקת שינוי תעריף - משווים תעריף חול (התעריף הבסיסי)
            if not should_break and current_chain_shift_id is not None:
                current_rate_key = (current_chain_shift_id, current_chain_housing_array_id, None)
                new_rate_key = (evt["shift_id"], evt.get("housing_array_id"), None)
                current_rates = shift_rates.get(current_rate_key, {"weekday": minimum_wage})
                new_rates = shift_rates.get(new_rate_key, {"weekday": minimum_wage})
                if current_rates["weekday"] != new_rates["weekday"]:
                    should_break = True
                    break_reason = "rate_change"

            if should_break:
                if current_chain:
                    chain_minutes = sum(seg[1] - seg[0] for seg in current_chain)
                    if break_reason == "rate_change":
                        # שינוי תעריף מעביר offset
                        chain_total += chain_minutes
                    else:
                        # הפסקה מאפסת
                        chain_total = 0
                    current_chain = []
                    current_chain_shift_id = None
                    current_chain_housing_array_id = None

            current_chain.append((evt["start"], evt["end"]))
            current_chain_shift_id = evt["shift_id"]
            current_chain_housing_array_id = evt.get("housing_array_id")
            last_work_end = evt["end"]

    # סגירת רצף אחרון
    if not current_chain:
        return (0, 0, None, 0, None)

    # חישוב סך הדקות ברצף האחרון + offset מרצפים קודמים
    last_chain_minutes = sum(seg[1] - seg[0] for seg in current_chain)
    chain_total_minutes = chain_total + last_chain_minutes

    # זמן הסיום של הרצף האחרון - נרמול לציר יום בודד (08:00-08:00)
    # מחזירים את הזמן ביחס ליום האחרון בלבד
    last_end_time_raw = current_chain[-1][1]
    # ננרמל ל-1920 (08:00 ביום הבא) כמו הקוד המקורי
    last_end_time = last_end_time_raw % MINUTES_PER_DAY
    if last_end_time <= 480:
        last_end_time += MINUTES_PER_DAY  # מנרמל לציר 08:00-08:00

    # חישוב שעות לילה ברצף (22:00-06:00)
    chain_night_minutes = 0
    for seg_start, seg_end in current_chain:
        # המרה מציר מצטבר לציר 00:00-24:00
        real_start = (seg_start + 480) % 1440
        real_end = (seg_end + 480) % 1440
        # טיפול בסגמנטים שחוצים חצות
        if real_end <= real_start and seg_end > seg_start:
            real_end += 1440
        chain_night_minutes += calculate_night_hours_in_segment(real_start, real_end)

    # מחזיר את הדקות, זמן הסיום, shift_id של הרצף האחרון, דקות לילה, ו-housing_array_id
    return (chain_total_minutes, last_end_time, current_chain_shift_id, chain_night_minutes, current_chain_housing_array_id)


def get_daily_segments_data(
    conn, person_id: int, year: int, month: int, shabbat_cache: Dict, minimum_wage: float,
    person_status_cache: Optional[Dict[int, dict]] = None,
    apartment_type_cache: Optional[Dict[int, int]] = None,
    housing_rates_cache: Optional[Dict] = None,
    preloaded_reports: Optional[List] = None,
    preloaded_segments: Optional[Dict[int, List]] = None,
    preloaded_weekday_overrides: Optional[tuple[dict[int, tuple[str, str]], dict[int, tuple[str, str]]]] = None,
    preloaded_prev_month_sick_dates: Optional[list[date]] = None,
    preloaded_prev_month_reports: Optional[list[dict[str, Any]]] = None,
    preloaded_prev_month_housing_rates_cache: Optional[Dict] = None,
):
    """
    Calculates detailed daily segments for a given employee and month.
    Used by guide_view and simple_summary_view.

    Optional cache parameters for batch optimization:
    - person_status_cache: dict mapping person_id to status dict
    - apartment_type_cache: dict mapping apartment_id to apartment_type_id
    - housing_rates_cache: dict mapping (shift_type_id, housing_array_id) to rate info
    - preloaded_reports: pre-fetched reports for this person (skips DB query)
    - preloaded_segments: dict mapping shift_type_id to segments list (skips DB query)
    - preloaded_weekday_overrides: tuple of apartment / housing-array weekday overrides
    - preloaded_prev_month_sick_dates: previous month sick dates for continuity
    - preloaded_prev_month_reports: previous month reports for carryover calculation
    - preloaded_prev_month_housing_rates_cache: previous month housing rates cache
    """
    # Use preloaded reports if provided (bulk optimization)
    if preloaded_reports is not None:
        reports = preloaded_reports
    else:
        start_dt, end_dt = month_range_ts(year, month)

        # Convert datetime to date for PostgreSQL date column
        start_date = start_dt.date()
        end_date = end_dt.date()

        # Get housing array filter
        housing_filter = get_housing_array_filter()

        # Fetch reports - with optional housing array filter
        if housing_filter is not None:
            reports = conn.execute("""
                SELECT tr.*,
                       st.name AS shift_name,
                       st.color AS shift_color,
                       st.is_special_hourly AS shift_is_special_hourly,
                       ap.name AS apartment_name,
                       ap.apartment_type_id,
                       ap.housing_array_id,
                       at.hourly_wage_supplement,
                       at.name AS apartment_type_name,
                       ha.name AS housing_array_name,
                       rate_at.name AS rate_apartment_type_name,
                       rate_at.hourly_wage_supplement AS rate_hourly_wage_supplement,
                       p.is_married,
                       p.name as person_name,
                       ap.city AS apartment_city
                FROM time_reports tr
                LEFT JOIN shift_types st ON st.id = tr.shift_type_id
                JOIN apartments ap ON ap.id = tr.apartment_id
                LEFT JOIN apartment_types at ON at.id = ap.apartment_type_id
                LEFT JOIN apartment_types rate_at ON rate_at.id = tr.rate_apartment_type_id
                LEFT JOIN housing_arrays ha ON ha.id = ap.housing_array_id
                LEFT JOIN people p ON p.id = tr.person_id
                WHERE tr.person_id = %s AND tr.date >= %s AND tr.date < %s
                  AND ap.housing_array_id = %s
                ORDER BY tr.date, tr.start_time
            """, (person_id, start_date, end_date, housing_filter)).fetchall()
        else:
            reports = conn.execute("""
                SELECT tr.*,
                       st.name AS shift_name,
                       st.color AS shift_color,
                       st.is_special_hourly AS shift_is_special_hourly,
                       ap.name AS apartment_name,
                       ap.apartment_type_id,
                       ap.housing_array_id,
                       at.hourly_wage_supplement,
                       at.name AS apartment_type_name,
                       ha.name AS housing_array_name,
                       rate_at.name AS rate_apartment_type_name,
                       rate_at.hourly_wage_supplement AS rate_hourly_wage_supplement,
                       p.is_married,
                       p.name as person_name,
                       ap.city AS apartment_city
                FROM time_reports tr
                LEFT JOIN shift_types st ON st.id = tr.shift_type_id
                LEFT JOIN apartments ap ON ap.id = tr.apartment_id
                LEFT JOIN apartment_types at ON at.id = ap.apartment_type_id
                LEFT JOIN apartment_types rate_at ON rate_at.id = tr.rate_apartment_type_id
                LEFT JOIN housing_arrays ha ON ha.id = ap.housing_array_id
                LEFT JOIN people p ON p.id = tr.person_id
                WHERE tr.person_id = %s AND tr.date >= %s AND tr.date < %s
                ORDER BY tr.date, tr.start_time
            """, (person_id, start_date, end_date)).fetchall()

    reports = _filter_asd_completion_reports_for_one_time_exclusion(reports, year, month)
    person_name = reports[0]["person_name"] if reports else ""

    # Override apartment types and marital status with historical data
    # Use provided caches or build them (for backward compatibility)
    apartment_ids = {r["apartment_id"] for r in reports if r["apartment_id"]}
    if apartment_type_cache is None:
        apartment_type_cache = {}
        for apt_id in apartment_ids:
            hist_type = get_apartment_type_for_month(conn, apt_id, year, month)
            if hist_type is not None:
                apartment_type_cache[apt_id] = hist_type

    # Historical marital status - use cache or fetch
    if person_status_cache is not None and person_id in person_status_cache:
        historical_person = person_status_cache[person_id]
    else:
        historical_person = get_person_status_for_month(conn, person_id, year, month)
    historical_is_married = historical_person.get("is_married")

    # Build housing rates cache - use provided or fetch (with historical support)
    if housing_rates_cache is None:
        housing_rates_cache = get_all_housing_rates_for_month(conn, year, month)

    # Build apartment type change dates cache
    apartment_change_dates = get_all_apartment_type_change_dates(conn, list(apartment_ids))

    # טעינת חלונות פרימיום (ימים מיוחדים בלבד) לחודש המבוקש + כרית 2 ימים מכל צד
    # מסננים שבת/חג כי אלה כבר מטופלים ב-_get_shabbat_boundaries + shabbat_cache
    _month_start_d = date(year, month, 1)
    if month == 12:
        _month_end_d = date(year + 1, 1, 1)
    else:
        _month_end_d = date(year, month + 1, 1)
    _all_windows = get_premium_windows_for_range(
        conn, _month_start_d - timedelta(days=2), _month_end_d + timedelta(days=2)
    )
    premium_windows_all = [w for w in _all_windows if w.origin not in ("shabbat", "holiday")]

    # Fetch segments early - needed for weekday overrides when (year, month) >= (2026, 2)
    shift_ids = {r["shift_type_id"] for r in reports if r["shift_type_id"]}
    if (year, month) >= (2026, 2):
        shift_ids.add(WEEKDAY_SHIFT_TYPE_ID)
    if preloaded_segments is not None:
        segments_by_shift = {
            sid: segs for sid, segs in preloaded_segments.items()
            if sid in shift_ids
        }
    else:
        shift_segments = []
        if shift_ids:
            placeholders = ",".join(["%s"] * len(shift_ids))
            shift_segments = conn.execute(
                """
                SELECT seg.*, st.name AS shift_name
                FROM shift_time_segments seg
                JOIN shift_types st ON st.id = seg.shift_type_id
                WHERE seg.shift_type_id IN ({})
                ORDER BY seg.shift_type_id, seg.order_index, seg.id
                """.format(placeholders),
                tuple(shift_ids),
            ).fetchall()
        segments_by_shift = {}
        for seg in shift_segments:
            segments_by_shift.setdefault(seg["shift_type_id"], []).append(seg)

    # טעינת שעות עבודה בחול לפי דירה (מתוך shift_time_overrides) לשימוש בחופשה/מחלה
    # חל רק מ-02/2026 ואילך
    weekday_work_overrides: dict[int, list[dict]] = {}
    weekday_shift_overrides: dict[int, list[dict]] = {}
    apartment_housing_map: dict[int, int | None] = {}
    if (year, month) >= (2026, 2):
        for r in reports:
            apt_id = r.get("apartment_id")
            ha_id = r.get("housing_array_id")
            if apt_id and ha_id:
                apartment_housing_map[apt_id] = ha_id
        if preloaded_weekday_overrides is not None:
            apt_overrides, ha_defaults = preloaded_weekday_overrides
        else:
            apt_overrides, ha_defaults = _fetch_weekday_overrides_for_month(conn, year, month)
        weekday_work_overrides = _build_weekday_work_overrides(
            apartment_ids, apartment_housing_map, apt_overrides, ha_defaults
        )
        base_segs = segments_by_shift.get(WEEKDAY_SHIFT_TYPE_ID, [])
        weekday_shift_overrides = _build_weekday_shift_overrides(
            apartment_ids, apartment_housing_map, apt_overrides, ha_defaults, base_segs
        )

    # Apply historical overrides to reports
    processed_reports = []
    for r in reports:
        r_dict = dict(r)

        # Save actual apartment type for visual indicator (from apartments table)
        r_dict["actual_apartment_type_id"] = r_dict.get("apartment_type_id")
        # תוספת שעתית של סוג הדירה בפועל (לפני החלפת apartment_type_id לחישוב תעריף)
        r_dict["actual_hourly_wage_supplement"] = r_dict.get("hourly_wage_supplement") or 0

        # Override apartment_type_id for rate calculation
        # Priority: rate_apartment_type_id (if set) > historical > current
        rate_apt_type = r_dict.get("rate_apartment_type_id")
        if rate_apt_type:
            # Use the explicit rate_apartment_type_id from the report
            r_dict["apartment_type_id"] = rate_apt_type
            # Also use the rate apartment type's hourly_wage_supplement for rate calculation
            rate_supplement = r_dict.get("rate_hourly_wage_supplement")
            if rate_supplement is not None:
                r_dict["hourly_wage_supplement"] = rate_supplement
        else:
            # Fall back to historical apartment type
            apt_id = r_dict.get("apartment_id")
            if apt_id and apt_id in apartment_type_cache:
                r_dict["apartment_type_id"] = apartment_type_cache[apt_id]
        
        # Override is_married
        if historical_is_married is not None:
            r_dict["is_married"] = historical_is_married

        # Add apartment type change date
        apt_id = r_dict.get("apartment_id")
        r_dict["apartment_type_change_date"] = apartment_change_dates.get(apt_id)

        processed_reports.append(r_dict)

    reports = processed_reports

    # זיהוי רצפי ימי מחלה לחישוב אחוזי תשלום מדורגים (כולל המשכיות מחודש קודם)
    prev_month_sick_dates = (
        preloaded_prev_month_sick_dates
        if preloaded_prev_month_sick_dates is not None
        else _fetch_prev_month_sick_dates(conn, person_id, year, month)
    )
    sick_day_sequence = _identify_sick_day_sequences(reports, prev_month_sick_dates)

    # תוספת ותק ASD: +3₪ למדריך קבוע עם שנה+ ותק
    employee_type = historical_person.get("employee_type")
    person_start_date_row = conn.execute(
        "SELECT start_date FROM people WHERE id = %s", (person_id,)
    ).fetchone()
    asd_seniority_bonus = _get_asd_seniority_supplement(
        employee_type,
        person_start_date_row["start_date"] if person_start_date_row else None,
        year, month,
    )

    # Build a map of (shift_type_id, housing_array_id) -> {"weekday": rate, "shabbat": rate}
    # This allows using custom rates for different housing arrays
    shift_rates = {}
    shift_names_map = {}  # Map shift_id -> shift_name
    shift_is_special_hourly = {}  # Map shift_id -> is_special_hourly (for variable rate tracking)
    shabbat_shifts = set()  # Track which shifts are Shabbat/holiday shifts
    apt_type_supplement = {}  # Map apartment_type_id -> hourly_wage_supplement (in agorot), including ASD seniority when eligible
    apt_type_apartment_supplement = {}  # Map apartment_type_id -> apartment-type supplement only (in agorot)
    for r in reports:
        shift_id = r.get("shift_type_id")
        housing_array_id = r.get("housing_array_id")
        rate_apartment_type_id = r.get("rate_apartment_type_id") or r.get("apartment_type_id")  # Include effective apartment type for rate calculation
        if shift_id:
            # Include rate_apartment_type_id in key because same shift can have different rates
            rate_key = (shift_id, housing_array_id, rate_apartment_type_id)
            if rate_key not in shift_rates:
                # תוספת ותק ASD: הזרקה לחישוב התעריף (חלה רק כש-fallback לשכר מינימום)
                effective_supplement = r.get("hourly_wage_supplement") or 0
                if asd_seniority_bonus and is_asd_housing_array(housing_array_id):
                    effective_supplement += asd_seniority_bonus
                rate_report = dict(r)
                rate_report["hourly_wage_supplement"] = effective_supplement
                weekday_rate = get_effective_hourly_rate(
                    rate_report, minimum_wage, is_shabbat=False, housing_rates_cache=housing_rates_cache
                )
                shabbat_rate = get_effective_hourly_rate(
                    rate_report, minimum_wage, is_shabbat=True, housing_rates_cache=housing_rates_cache
                )
                shift_rates[rate_key] = {
                    "weekday": weekday_rate,
                    "shabbat": shabbat_rate,
                    "supplement": effective_supplement,
                    "apartment_supplement": r.get("hourly_wage_supplement") or 0,
                    "asd_seniority_supplement": asd_seniority_bonus if (
                        asd_seniority_bonus and is_asd_housing_array(housing_array_id)
                    ) else 0,
                }
            if shift_id not in shift_names_map:
                shift_names_map[shift_id] = r.get("shift_name", "")
            if shift_id not in shift_is_special_hourly:
                shift_is_special_hourly[shift_id] = r.get("shift_is_special_hourly", False)
            if shift_id in SHABBAT_SHIFT_IDS or shift_id in TAGBUR_SHIFT_IDS:
                shabbat_shifts.add(shift_id)
        # מילוי תוספת סוג דירה (לתשלום / לתעריף)
        is_asd_array = is_asd_housing_array(housing_array_id)
        apt_type_id = r.get("apartment_type_id")
        if apt_type_id and apt_type_id not in apt_type_supplement:
            base_supplement = r.get("hourly_wage_supplement") or 0
            if asd_seniority_bonus and is_asd_array:
                base_supplement += asd_seniority_bonus
            apt_type_supplement[apt_type_id] = base_supplement
        if apt_type_id and apt_type_id not in apt_type_apartment_supplement:
            apt_type_apartment_supplement[apt_type_id] = r.get("hourly_wage_supplement") or 0
        actual_apt_tid = r.get("actual_apartment_type_id")
        if actual_apt_tid and actual_apt_tid not in apt_type_supplement:
            actual_supplement = r.get("actual_hourly_wage_supplement") or 0
            if asd_seniority_bonus and is_asd_array:
                actual_supplement += asd_seniority_bonus
            apt_type_supplement[actual_apt_tid] = actual_supplement
        if actual_apt_tid and actual_apt_tid not in apt_type_apartment_supplement:
            apt_type_apartment_supplement[actual_apt_tid] = r.get("actual_hourly_wage_supplement") or 0

    # טעינת תעריפי "שעת עבודה" לכל housing_array_id שנמצא בדיווחים
    # כי Uncovered Minutes ישולמו לפי תעריף זה
    all_housing_arrays = {r.get("housing_array_id") for r in reports if r.get("housing_array_id")}
    for ha_id in all_housing_arrays:
        # גם rate_apartment_type_id=None כי אלו שעות לא מכוסות ללא שינוי תעריף
        rate_key = (WORK_HOUR_SHIFT_ID, ha_id, None)
        if rate_key not in shift_rates:
            # יצירת report מדומה לחישוב התעריף
            dummy_report = {
                "shift_type_id": WORK_HOUR_SHIFT_ID,
                "housing_array_id": ha_id,
                "is_married": historical_is_married if historical_is_married is not None else False,
                "hourly_wage_supplement": 0
            }
            weekday_rate = get_effective_hourly_rate(
                dummy_report, minimum_wage, is_shabbat=False, housing_rates_cache=housing_rates_cache
            )
            shabbat_rate = get_effective_hourly_rate(
                dummy_report, minimum_wage, is_shabbat=True, housing_rates_cache=housing_rates_cache
            )
            shift_rates[rate_key] = {
                "weekday": weekday_rate,
                "shabbat": shabbat_rate,
                "supplement": 0,
                "apartment_supplement": 0,
                "asd_seniority_supplement": 0,
            }

    # תעריפי "שעת עבודה" לפי סוג דירה לתשלום.
    # שעות לא מכוסות מקבלות shift_id=138, אבל עדיין צריכות לקבל תוספת סוג דירה/ותק לפי הדירה בפועל.
    work_hour_rate_supplements = {}
    for r in reports:
        ha_id = r.get("housing_array_id")
        rate_apt_type_id = r.get("rate_apartment_type_id") or r.get("apartment_type_id")
        if not ha_id or not rate_apt_type_id:
            continue
        is_asd_array = is_asd_housing_array(ha_id)
        apartment_supplement = r.get("hourly_wage_supplement") or 0
        seniority_supplement = asd_seniority_bonus if (asd_seniority_bonus and is_asd_array) else 0
        work_hour_rate_supplements[(ha_id, rate_apt_type_id)] = (
            apartment_supplement + seniority_supplement,
            apartment_supplement,
            seniority_supplement,
        )

    for (ha_id, rate_apt_type_id), (effective_supplement, apartment_supplement, seniority_supplement) in work_hour_rate_supplements.items():
        rate_key = (WORK_HOUR_SHIFT_ID, ha_id, rate_apt_type_id)
        if rate_key in shift_rates:
            continue
        dummy_report = {
            "shift_type_id": WORK_HOUR_SHIFT_ID,
            "housing_array_id": ha_id,
            "is_married": historical_is_married if historical_is_married is not None else False,
            "hourly_wage_supplement": effective_supplement,
        }
        weekday_rate = get_effective_hourly_rate(
            dummy_report, minimum_wage, is_shabbat=False, housing_rates_cache=housing_rates_cache
        )
        shabbat_rate = get_effective_hourly_rate(
            dummy_report, minimum_wage, is_shabbat=True, housing_rates_cache=housing_rates_cache
        )
        shift_rates[rate_key] = {
            "weekday": weekday_rate,
            "shabbat": shabbat_rate,
            "supplement": effective_supplement,
            "apartment_supplement": apartment_supplement,
            "asd_seniority_supplement": seniority_supplement,
        }

    # טעינת שם המשמרת "שעת עבודה" אם לא קיים
    if WORK_HOUR_SHIFT_ID not in shift_names_map:
        work_hour_name = conn.execute(
            "SELECT name FROM shift_types WHERE id = %s", (WORK_HOUR_SHIFT_ID,)
        ).fetchone()
        shift_names_map[WORK_HOUR_SHIFT_ID] = work_hour_name["name"] if work_hour_name else "שעת עבודה"

    daily_map = {}
    
    for r in reports:
        if not r["shift_type_id"]:
            continue

        # בדיקה אם יש שעות בדיווח
        has_times = r["start_time"] and r["end_time"]

        # אם אין שעות - בודקים אם יש סגמנטים מוגדרים למשמרת (למשל יום מחלה/חופשה)
        if not has_times:
            shift_name_check = (r.get("shift_name") or "")
            is_sick_or_vacation_no_times = ("מחלה" in shift_name_check or "חופשה" in shift_name_check)
            apt_id = r.get("apartment_id")

            if is_sick_or_vacation_no_times and apt_id and apt_id in weekday_work_overrides:
                # חופשה/מחלה: שעות לפי override של משמרת חול לדירה
                override_segs = weekday_work_overrides[apt_id]
                if override_segs:
                    r = dict(r)
                    r["start_time"] = override_segs[0]["start_time"]
                    r["end_time"] = override_segs[-1]["end_time"]
                else:
                    continue
            else:
                seg_list_check = segments_by_shift.get(r["shift_type_id"], [])
                if seg_list_check:
                    first_seg = seg_list_check[0]
                    r = dict(r)
                    r["start_time"] = first_seg["start_time"]
                    r["end_time"] = first_seg["end_time"]
                else:
                    continue

        # Split shifts across midnight
        rep_start_orig, rep_end_orig = span_minutes(r["start_time"], r["end_time"])
        r_date = to_local_date(r["date"])
        
        # משמרת לווי רפואי (148) - לפחות שעה עבודה
        is_medical_escort = (r["shift_type_id"] == 148)
        escort_bonus_minutes = 0
        if is_medical_escort:
            duration = rep_end_orig - rep_start_orig
            if duration < 60:
                escort_bonus_minutes = 60 - duration
        
        parts = []
        if rep_end_orig <= MINUTES_PER_DAY:
            parts.append((r_date, rep_start_orig, rep_end_orig, escort_bonus_minutes))
        else:
            # בפיצול חצות, הבונוס בדרך כלל שייך ליום ההתחלה, אבל נצמיד אותו לחלק הראשון
            parts.append((r_date, rep_start_orig, MINUTES_PER_DAY, escort_bonus_minutes))
            next_day = r_date + timedelta(days=1)
            parts.append((next_day, 0, rep_end_orig - MINUTES_PER_DAY, 0))

        seg_list = segments_by_shift.get(r["shift_type_id"], [])
        has_predefined_segments = bool(seg_list)  # האם יש סגמנטים מוגדרים מראש למשמרת

        # משמרת חול: החלפת seg_list לפי override של הדירה (work + standby)
        shift_type_id = r.get("shift_type_id")
        if shift_type_id == WEEKDAY_SHIFT_TYPE_ID:
            apt_id = r.get("apartment_id")
            if apt_id and apt_id in weekday_shift_overrides:
                seg_list = weekday_shift_overrides[apt_id]
                has_predefined_segments = True

        if not seg_list:
            # אין סגמנטים מוגדרים - יצירת סגמנט דינמי
            seg_list = [{
                "start_time": r["start_time"],
                "end_time": r["end_time"],
                "segment_type": "work",
                "id": None
            }]

        work_type = None
        shift_name_str = (r["shift_name"] or "")
        is_sick_report = ("מחלה" in shift_name_str)
        is_vacation_report = ("חופשה" in shift_name_str)

        # משמרות עם סגמנטים קבועים - משתמשים בסגמנטים המוגדרים ישירות (לא לפי שעות דיווח)
        # כולל: משמרות תגבור, יום חופשה, יום מחלה
        is_fixed_segments_shift = is_tagbur_shift(shift_type_id) or is_vacation_report or is_sick_report

        special_absence_shift_id = None

        # חופשה/מחלה: החלפת seg_list בסגמנטי עבודה לפי override של משמרת חול לדירה
        if (is_sick_report or is_vacation_report):
            apt_id = r.get("apartment_id")
            special_absence_shift_id = _absence_payment_shift_id(r.get("apartment_type_id"))
            if special_absence_shift_id:
                if special_absence_shift_id == WEEKDAY_SHIFT_TYPE_ID and apt_id and apt_id in weekday_shift_overrides:
                    seg_list = [dict(seg) for seg in weekday_shift_overrides[apt_id]]
                else:
                    seg_list = [dict(seg) for seg in segments_by_shift.get(special_absence_shift_id, [])]
                for seg in seg_list:
                    seg["source_shift_type_id"] = special_absence_shift_id
            elif apt_id and apt_id in weekday_work_overrides:
                seg_list = weekday_work_overrides[apt_id]

        # משמרת לילה - סגמנטים דינמיים לפי זמן הכניסה בפועל
        # החוק: 2 שעות ראשונות עבודה, עד 06:30 כוננות, 06:30-08:00 עבודה
        is_night = is_night_shift(shift_type_id)

        # בדיקת סימון כוננות ASD - חל על כל סוגי המשמרות
        has_asd_night = bool(r.get("asd_night_marking"))
        asd_night_high_func = False
        actual_apt_id_for_asd = r.get("actual_apartment_type_id")
        if has_asd_night and actual_apt_id_for_asd == HIGH_FUNCTIONING_APT_TYPE:
            asd_night_high_func = True

        # מערך דיור ASD: משמרת לילה משתמשת בסגמנטים מהטבלה כמו שהם
        is_asd_apartment = is_asd_housing_array(r.get("housing_array_id"))

        if is_night and not is_asd_apartment:
            # יצירת סגמנטים דינמיים לפי זמן הכניסה בפועל
            entry_time = rep_start_orig  # זמן הכניסה בדקות
            exit_time = rep_end_orig if rep_end_orig > entry_time else rep_end_orig + MINUTES_PER_DAY
            # קבועי זמן מיובאים מ-core/constants.py:
            # NIGHT_SHIFT_WORK_FIRST_MINUTES, NIGHT_SHIFT_STANDBY_END, NIGHT_SHIFT_MORNING_END

            # מציאת ה-seg_id של כוננות לילה מהסגמנטים המוגדרים בטבלה
            night_standby_seg_id = None
            if shift_type_id in segments_by_shift:
                for seg in segments_by_shift[shift_type_id]:
                    if seg.get("segment_type") == "standby":
                        night_standby_seg_id = seg.get("id")
                        break

            # חישוב הסגמנטים הדינמיים
            dynamic_segments = []

            # סגמנט 1: 2 שעות ראשונות עבודה
            work1_start = entry_time
            work1_end = min(entry_time + NIGHT_SHIFT_WORK_FIRST_MINUTES, exit_time)
            if work1_end > work1_start:
                dynamic_segments.append({
                    "start_time": f"{(work1_start // 60) % 24:02d}:{work1_start % 60:02d}",
                    "end_time": f"{(work1_end // 60) % 24:02d}:{work1_end % 60:02d}",
                    "segment_type": "work",
                    "id": _NIGHT_FIRST_WORK_SEGMENT_MARKER
                })

            # סגמנט 2: כוננות מסוף 2 שעות עבודה עד 06:30
            standby_start = work1_end
            # 06:30 - אם הכניסה אחרי חצות, 06:30 הוא באותו יום; אחרת ביום הבא
            standby_end_time = NIGHT_SHIFT_STANDBY_END if entry_time < MINUTES_PER_DAY else NIGHT_SHIFT_STANDBY_END + MINUTES_PER_DAY
            if entry_time >= NOON_MINUTES:  # אם נכנס אחרי 12:00, 06:30 הוא למחרת
                standby_end_time = NIGHT_SHIFT_STANDBY_END + MINUTES_PER_DAY
            standby_end = min(standby_end_time, exit_time)
            if standby_end > standby_start:
                # ASD + תפקוד נמוך: כוננות הופכת לשעות עבודה
                if has_asd_night and actual_apt_id_for_asd == LOW_FUNCTIONING_APT_TYPE:
                    dynamic_segments.append({
                        "start_time": f"{(standby_start // 60) % 24:02d}:{standby_start % 60:02d}",
                        "end_time": f"{(standby_end // 60) % 24:02d}:{standby_end % 60:02d}",
                        "segment_type": "work",
                        "id": None
                    })
                else:
                    dynamic_segments.append({
                        "start_time": f"{(standby_start // 60) % 24:02d}:{standby_start % 60:02d}",
                        "end_time": f"{(standby_end // 60) % 24:02d}:{standby_end % 60:02d}",
                        "segment_type": "standby",
                        "id": night_standby_seg_id
                    })

            # סגמנט 3: עבודה 06:30-08:00
            morning_start = standby_end_time
            morning_end_time = NIGHT_SHIFT_MORNING_END if entry_time < MINUTES_PER_DAY else NIGHT_SHIFT_MORNING_END + MINUTES_PER_DAY
            if entry_time >= NOON_MINUTES:  # אם נכנס אחרי 12:00, 08:00 הוא למחרת
                morning_end_time = NIGHT_SHIFT_MORNING_END + MINUTES_PER_DAY
            morning_end = min(morning_end_time, exit_time)
            if morning_end > morning_start and morning_start < exit_time:
                dynamic_segments.append({
                    "start_time": f"{(morning_start // 60) % 24:02d}:{morning_start % 60:02d}",
                    "end_time": f"{(morning_end // 60) % 24:02d}:{morning_end % 60:02d}",
                    "segment_type": "work",
                    "id": None
                })

            # החלפת רשימת הסגמנטים בסגמנטים הדינמיים
            seg_list = dynamic_segments

        # משמרת תגבור - סגמנטים דינמיים לפי זמני שבת
        # תגבור שישי (108): סגמנט ראשון מתחיל שעה לפני כניסת שבת
        # תגבור שבת (109): סגמנט אחרון מסתיים שעתיים אחרי צאת שבת
        # מ-04/2026 לא מאריכים מעבר לשעת הדיווח בצד הדינמי.
        # שאר הסגמנטים נשארים במקום - הפיצול לשבת מתבצע אוטומטית
        if is_tagbur_shift(shift_type_id) and seg_list:
            effective_report_end = rep_end_orig if rep_end_orig > rep_start_orig else rep_end_orig + MINUTES_PER_DAY
            seg_list = _apply_tagbur_dynamic_boundaries(
                shift_type_id,
                seg_list,
                r_date,
                rep_start_orig,
                effective_report_end,
                year,
                month,
                shabbat_cache,
            )

        # אם זו משמרת תגבור - מוסיפים את הסגמנטים ישירות בלי לחשב חפיפה עם שעות הדיווח
        if is_fixed_segments_shift and seg_list:
            CUTOFF = 480  # 08:00
            display_date = r_date  # יום הדיווח
            day_key = display_date.strftime("%d/%m/%Y")
            entry = daily_map.setdefault(day_key, {"shifts": set(), "segments": [], "is_fixed_segments": False, "escort_bonus_minutes": 0, "day_shift_types": set(), "housing_array_id": None})
            entry["is_fixed_segments"] = True  # סימון שזו משמרת קבועה
            entry["day_shift_types"].add(r["shift_type_id"])  # Track shift types for Shabbat detection
            if r.get("housing_array_id") is not None:
                entry["housing_array_id"] = r.get("housing_array_id")
            _register_asd_night_labels(entry, r, has_asd_night, actual_apt_id_for_asd)
            # ASD + תפקוד גבוה: סימון לתשלום כוננות 150 ש"ח
            if asd_night_high_func:
                entry["asd_night_high_func"] = True
            if r["shift_name"]:
                entry["shifts"].add(r["shift_name"])
            if is_sick_report:
                entry["special_absence_type"] = "sick" if special_absence_shift_id else None
            elif is_vacation_report:
                entry["special_absence_type"] = "vacation" if special_absence_shift_id else None

            # חישוב זמני התחלה וסיום של הסגמנטים המוגדרים
            first_seg_start, _ = span_minutes(seg_list[0]["start_time"], seg_list[0]["end_time"])
            _, last_seg_end = span_minutes(seg_list[-1]["start_time"], seg_list[-1]["end_time"])

            # נתוני הדיווח לשעות לא מכוסות
            apartment_type_id = r.get("apartment_type_id")
            actual_apartment_type_id = r.get("actual_apartment_type_id")
            rate_apartment_type_id = r.get("rate_apartment_type_id") or apartment_type_id
            is_married = r.get("is_married")
            apartment_name = r.get("apartment_name", "")
            apartment_type_name = r.get("apartment_type_name", "")
            housing_array_name = r.get("housing_array_name", "")
            rate_apartment_type_name = r.get("rate_apartment_type_name", "")
            apartment_type_change_date = r.get("apartment_type_change_date", "")
            housing_array_id = r.get("housing_array_id")

            # שעות לא מכוסות לפני תחילת המשמרת
            # חופשה/מחלה: אין שעות לא מכוסות כלל (הכל חלק מהחופשה/מחלה)
            # תגבור ערב (108): שעות לא מכוסות רק לפני (אחרי = שבת/חג)
            # תגבור שבת/חג (109): שעות לא מכוסות רק אחרי (לפני = שבת/חג)
            is_tagbur_eve = (shift_type_id == TAGBUR_FRIDAY_SHIFT_ID)
            is_tagbur_holy = (shift_type_id == TAGBUR_SHABBAT_SHIFT_ID)
            is_vacation_or_sick = is_vacation_report or is_sick_report

            if (year, month) >= (2025, 12):
                # מ-12/2025: לוגיקה חדשה - חסימה לפי סוג משמרת תגבור + חופשה/מחלה
                skip_uncov_before = is_tagbur_holy or is_vacation_or_sick
                skip_uncov_after = is_tagbur_eve or is_vacation_or_sick
            else:
                # לפני 12/2025: לוגיקה ישנה - חסימה לפי יום בשבוע (תואם לתלושים שכבר יצאו)
                is_saturday = r_date.weekday() == 5
                is_friday = r_date.weekday() == 4
                skip_uncov_before = is_saturday
                skip_uncov_after = is_friday

            if rep_start_orig < first_seg_start and not skip_uncov_before:
                uncov_start = rep_start_orig
                uncov_end = first_seg_start
                entry["segments"].append((uncov_start, uncov_end, "work", "work", WORK_HOUR_SHIFT_ID, None, apartment_type_id, is_married, apartment_name, r_date, actual_apartment_type_id, None, housing_array_id, apartment_type_name, housing_array_name, rate_apartment_type_name, apartment_type_change_date, rate_apartment_type_id))

            # מעקב אחרי זמן הסיום של הסגמנט הקודם לזיהוי מעבר יום
            prev_seg_end = None
            days_offset = 0  # כמה ימים עברו מתחילת המשמרת

            for seg in seg_list:
                seg_start, seg_end = span_minutes(seg["start_time"], seg["end_time"])

                # זיהוי מעבר יום: אם זמן ההתחלה קטן מזמן הסיום של הסגמנט הקודם
                # זה אומר שעברנו חצות והסגמנט הזה הוא ביום הבא
                if prev_seg_end is not None and seg_start < prev_seg_end:
                    days_offset += 1
                prev_seg_end = seg_end

                # קביעת התאריך האמיתי של הסגמנט
                # חופשה/מחלה: כל הסגמנטים שייכים ליום הדיווח (גם חלק הבוקר של למחרת)
                # כדי שחיפוש ברצף ימי מחלה יחזיר את מספר היום הנכון
                if is_sick_report or is_vacation_report:
                    actual_seg_date = r_date
                else:
                    actual_seg_date = r_date + timedelta(days=days_offset)

                # קביעת סוג אפקטיבי
                if is_sick_report:
                    effective_seg_type = "standby" if special_absence_shift_id and seg.get("segment_type") == "standby" else "sick"
                elif is_vacation_report:
                    effective_seg_type = "standby" if special_absence_shift_id and seg.get("segment_type") == "standby" else "vacation"
                else:
                    effective_seg_type = seg["segment_type"]
                    # ASD + תפקוד נמוך: כוננות הופכת לשעות עבודה
                    if effective_seg_type == "standby" and has_asd_night and actual_apt_id_for_asd == LOW_FUNCTIONING_APT_TYPE:
                        effective_seg_type = "work"

                # קביעת תווית לפי סוג הסגמנט
                if effective_seg_type == "standby":
                    label = "כוננות"
                elif effective_seg_type == "vacation":
                    label = "חופשה"
                elif effective_seg_type == "sick":
                    label = "מחלה"
                else:
                    label = "work"

                segment_id = seg.get("id")
                segment_shift_type_id = seg.get("source_shift_type_id") or r["shift_type_id"]

                # For fixed segment shifts (tagbur/vacation/sick), standby_defined_end = seg_end (full standby)
                standby_defined_end = seg_end if effective_seg_type == "standby" else None
                apartment_city = r.get("apartment_city", "")
                entry["segments"].append((seg_start, seg_end, effective_seg_type, label, segment_shift_type_id, segment_id, apartment_type_id, is_married, apartment_name, actual_seg_date, actual_apartment_type_id, standby_defined_end, housing_array_id, apartment_type_name, housing_array_name, rate_apartment_type_name, apartment_type_change_date, rate_apartment_type_id, apartment_city))

            # שעות לא מכוסות אחרי סיום המשמרת
            # חופשה/מחלה: אין שעות לא מכוסות כלל
            # תגבור ערב (108): שעות לא מכוסות רק לפני (אחרי = שבת/חג)
            # תגבור שבת/חג (109): שעות לא מכוסות רק אחרי (לפני = שבת/חג)
            effective_rep_end = rep_end_orig if rep_end_orig > rep_start_orig else rep_end_orig + MINUTES_PER_DAY
            if effective_rep_end > last_seg_end and not skip_uncov_after:
                uncov_start = last_seg_end
                uncov_end = effective_rep_end
                # התאריך של הסגמנט הלא מכוסה אחרי = התאריך של הסגמנט האחרון
                last_seg_date = r_date + timedelta(days=days_offset)
                entry["segments"].append((uncov_start, uncov_end, "work", "work", WORK_HOUR_SHIFT_ID, None, apartment_type_id, is_married, apartment_name, last_seg_date, actual_apartment_type_id, None, housing_array_id, apartment_type_name, housing_array_name, rate_apartment_type_name, apartment_type_change_date, rate_apartment_type_id))

            continue  # דלג על העיבוד הרגיל עבור משמרת זו

        for p_date, p_start, p_end, p_escort_bonus in parts:
            # Split segments crossing 08:00 cutoff
            CUTOFF = 480  # 08:00
            sub_parts = []
            if p_start < CUTOFF < p_end:
                sub_parts.append((p_start, CUTOFF))
                sub_parts.append((CUTOFF, p_end))
            else:
                sub_parts.append((p_start, p_end))

            for s_start, s_end in sub_parts:
                # Assign to workday and normalize times
                # דיווח ששעת הסיום שלו לפני 08:00 שייך ליום העבודה הקודם
                # אבל רק אם זה המשך של משמרת שהתחילה לפני חצות
                # דיווח עצמאי = הדיווח המקורי התחיל אחרי חצות (00:00-08:00) ביום הנוכחי
                # לדוגמה: דיווח 02:00-06:30 הוא עצמאי ולא המשך משמרת
                is_standalone_night_shift = (p_date == r_date and rep_start_orig < CUTOFF)
                if s_end <= CUTOFF and not is_standalone_night_shift:
                    # Belongs to previous day's workday (continuation of shift)
                    display_date = p_date - timedelta(days=1)
                    norm_start = s_start + MINUTES_PER_DAY
                    norm_end = s_end + MINUTES_PER_DAY
                else:
                    # Belongs to current day's workday
                    display_date = p_date
                    norm_start = s_start
                    norm_end = s_end

                if display_date.year != year or display_date.month != month:
                    logger.debug(f"Skipping report outside month: person_id={person_id}, date={display_date}, requested={year}-{month:02d}")
                    continue

                day_key = display_date.strftime("%d/%m/%Y")
                if day_key not in daily_map:
                    daily_map[day_key] = {
                        "shifts": set(),
                        "segments": [],
                        "is_fixed_segments": False,
                        "escort_bonus_minutes": 0,
                        "day_shift_types": set(),
                        "housing_array_id": None,
                    }
                entry = daily_map[day_key]
                entry["day_shift_types"].add(r["shift_type_id"])  # Track shift types for Shabbat detection
                if r.get("housing_array_id") is not None:
                    entry["housing_array_id"] = r.get("housing_array_id")
                _register_asd_night_labels(entry, r, has_asd_night, actual_apt_id_for_asd)

                # ASD לילה + תפקוד גבוה: סימון לתשלום כוננות 150 ש"ח
                if asd_night_high_func:
                    entry["asd_night_high_func"] = True

                # Add bonus only once per part
                if s_start == p_start:
                    entry["escort_bonus_minutes"] += p_escort_bonus

                if r["shift_name"]:
                    entry["shifts"].add(r["shift_name"])
                    
                minutes_covered = 0
                covered_intervals = []  # לאיסוף אינטרוולים מכוסים לחישוב "חורים" בהמשך
                is_second_day = (p_date > r_date)
                
                # Sort segments chronologically by start time
                seg_list_sorted = sorted(seg_list, key=lambda s: span_minutes(s["start_time"], s["end_time"])[0])

                # Rotate the list so that the segment corresponding to the report start time comes first
                # This ensures that normalization flows correctly (e.g. 06:30-08:00 is end of shift, not start)
                rotate_idx = 0
                rep_start_min = rep_start_orig % MINUTES_PER_DAY

                # Find the segment that starts closest to (and before/at) the report start time
                best_start_diff = -1

                # Define threshold for morning segments: segments before 08:00 might be "next day" segments
                MORNING_CUTOFF = 480  # 08:00

                for i, seg in enumerate(seg_list_sorted):
                    seg_start_min, _ = span_minutes(seg["start_time"], seg["end_time"])

                    # Fix for bug: When report starts in afternoon (e.g. 15:00) and a segment starts
                    # in early morning (e.g. 06:30), that segment is likely NEXT DAY, not before report.
                    # This prevents treating 06:30-08:00 as the first segment for a 15:00-08:00 report.
                    is_morning_segment = seg_start_min < MORNING_CUTOFF
                    is_afternoon_report = rep_start_min >= NOON_MINUTES

                    if is_morning_segment and is_afternoon_report:
                        # Skip this morning segment - it's next day, not before the report
                        continue

                    if seg_start_min <= rep_start_min:
                        if seg_start_min > best_start_diff:
                            best_start_diff = seg_start_min
                            rotate_idx = i
                    elif best_start_diff == -1:
                        # If we haven't found any starting before, and this is the first one,
                        # checking implies we might need to wrap around.
                        # But we continue to see if there are others.
                        pass

                # If no segment starts before report time:
                # - If report starts BEFORE the first segment of the shift definition,
                #   keep rotate_idx=0 (start from the first segment)
                # - If report starts AFTER all segments (late in day),
                #   then it might belong to the LAST segment wrapping around
                # For a report 08:00-08:00 with first segment at 12:00,
                # the 08:00-12:00 gap is just waiting time, so start from segment 0
                if best_start_diff == -1 and seg_list_sorted:
                    first_seg_start, _ = span_minutes(seg_list_sorted[0]["start_time"], seg_list_sorted[0]["end_time"])

                    # For afternoon reports, find first non-morning segment
                    if rep_start_min >= NOON_MINUTES:  # Report is in afternoon/evening
                        first_afternoon_idx = None
                        for i, seg in enumerate(seg_list_sorted):
                            seg_start_min, _ = span_minutes(seg["start_time"], seg["end_time"])
                            if seg_start_min >= MORNING_CUTOFF:
                                first_afternoon_idx = i
                                break

                        if first_afternoon_idx is not None:
                            rotate_idx = first_afternoon_idx
                        else:
                            # All segments are morning - unusual case, use first
                            rotate_idx = 0
                    else:
                        # Report is in morning/early hours, use standard logic
                        if rep_start_min < first_seg_start:
                            rotate_idx = 0
                        else:
                            # Report starts late in morning (e.g. 05:00)
                            rotate_idx = len(seg_list_sorted) - 1

                seg_list_ordered = seg_list_sorted[rotate_idx:] + seg_list_sorted[:rotate_idx]
                
                # Normalize segments from shift definition to be continuous
                last_s_end_norm = -1
                for seg in seg_list_ordered:
                    # Use unique variable names to avoid shadowing
                    orig_s_start, orig_s_end = span_minutes(seg["start_time"], seg["end_time"])
                    
                    # Make segments continuous relative to the first one
                    if last_s_end_norm == -1:
                        # First segment: align to report start day roughly
                        # If orig_s_start is far from rep_start_min, adjust? 
                        # Actually, just start with it as is (or +1440 if needed?)
                        # No, simple normalization should work if we start with the "right" segment.
                        pass
                    else:
                        while orig_s_start < last_s_end_norm:
                            orig_s_start += MINUTES_PER_DAY
                            orig_s_end += MINUTES_PER_DAY
                    
                    last_s_end_norm = orig_s_end
                    
                    # Adjust segments to the timeline of the current report part
                    if is_second_day:
                        current_seg_start = orig_s_start - MINUTES_PER_DAY
                        current_seg_end = orig_s_end - MINUTES_PER_DAY
                    else:
                        current_seg_start = orig_s_start
                        current_seg_end = orig_s_end

                    # Calculate overlap between report part (s_start, s_end) and segment
                    overlap = overlap_minutes(s_start, s_end, current_seg_start, current_seg_end)

                    # אם הסגמנט חוצה חצות ואין חפיפה, ננסה עם הזמנים מוזזים
                    # זה קורה כשדיווח 00:00-08:00 צריך להתאים לסגמנט 20:00-08:00
                    if overlap <= 0 and orig_s_end > MINUTES_PER_DAY:
                        # נסה עם הסגמנט מוזז אחורה ביום
                        shifted_start = orig_s_start - MINUTES_PER_DAY
                        shifted_end = orig_s_end - MINUTES_PER_DAY
                        overlap = overlap_minutes(s_start, s_end, shifted_start, shifted_end)
                        if overlap > 0:
                            current_seg_start = shifted_start
                            current_seg_end = shifted_end

                    if overlap <= 0:
                        continue
                        
                    minutes_covered += overlap

                    # שמירת אינטרוול מכוסה לחישוב "חורים" בהמשך
                    inter_start = max(s_start, current_seg_start)
                    inter_end = min(s_end, current_seg_end)
                    if inter_start < inter_end:
                        covered_intervals.append((inter_start, inter_end))

                    # Determine effective type
                    if is_sick_report:
                         effective_seg_type = "sick"
                    elif is_vacation_report:
                         effective_seg_type = "vacation"
                    else:
                         effective_seg_type = seg["segment_type"]
                         # ASD + תפקוד נמוך: כוננות הופכת לשעות עבודה
                         if effective_seg_type == "standby" and has_asd_night and actual_apt_id_for_asd == LOW_FUNCTIONING_APT_TYPE:
                             effective_seg_type = "work"

                    # קביעת תווית לפי סוג הסגמנט
                    if effective_seg_type == "standby":
                        label = "כוננות"
                    elif effective_seg_type == "vacation":
                        label = "חופשה"
                    elif effective_seg_type == "sick":
                        label = "מחלה"
                    else:
                        label = "work"

                    # Calculate effective normalized start/end for the segment
                    eff_start_in_part = max(current_seg_start, s_start)
                    eff_end_in_part = min(current_seg_end, s_end)
                    
                    # Apply same normalization to segment boundaries
                    if s_end <= CUTOFF:
                        eff_start = eff_start_in_part + MINUTES_PER_DAY
                        eff_end = eff_end_in_part + MINUTES_PER_DAY
                    else:
                        eff_start = eff_start_in_part
                        eff_end = eff_end_in_part
                    
                    segment_id = seg.get("id")
                    apartment_type_id = r.get("apartment_type_id")
                    actual_apartment_type_id = r.get("actual_apartment_type_id")
                    rate_apartment_type_id = r.get("rate_apartment_type_id") or apartment_type_id
                    is_married = r.get("is_married")
                    apartment_name = r.get("apartment_name", "")
                    apartment_type_name = r.get("apartment_type_name", "")
                    housing_array_name = r.get("housing_array_name", "")
                    rate_apartment_type_name = r.get("rate_apartment_type_name", "")
                    apartment_type_change_date = r.get("apartment_type_change_date", "")

                    # Store actual_date (p_date) for correct Shabbat calculation even when displayed under different day
                    # For standby segments, also store the defined end time (before min with report end)
                    # to detect early exit: if eff_end < standby_defined_end, it's early exit
                    standby_defined_end = current_seg_end if effective_seg_type == "standby" else None
                    housing_array_id = r.get("housing_array_id")
                    apartment_city = r.get("apartment_city", "")
                    entry["segments"].append((eff_start, eff_end, effective_seg_type, label, r["shift_type_id"], segment_id, apartment_type_id, is_married, apartment_name, p_date, actual_apartment_type_id, standby_defined_end, housing_array_id, apartment_type_name, housing_array_name, rate_apartment_type_name, apartment_type_change_date, rate_apartment_type_id, apartment_city))

                # Uncovered minutes -> work
                # חישוב שעות עבודה שלא מכוסות ע"י סגמנטים מוגדרים
                total_part_minutes = s_end - s_start
                remaining = total_part_minutes - minutes_covered

                if remaining > 0:
                    # מיזוג אינטרוולים חופפים ומציאת זמנים לא מכוסים
                    merged_covered = merge_intervals(covered_intervals)
                    uncovered_intervals = find_uncovered_intervals(merged_covered, s_start, s_end)

                    # יצירת סגמנטי עבודה לכל זמן לא מכוסה
                    segment_id = None
                    apartment_type_id = r.get("apartment_type_id")
                    actual_apartment_type_id = r.get("actual_apartment_type_id") or apartment_type_id
                    rate_apartment_type_id = r.get("rate_apartment_type_id") or apartment_type_id
                    is_married = r.get("is_married")
                    apartment_name = r.get("apartment_name", "")
                    apartment_type_name = r.get("apartment_type_name", "")
                    housing_array_name = r.get("housing_array_name", "")
                    rate_apartment_type_name = r.get("rate_apartment_type_name", "")
                    apartment_type_change_date = r.get("apartment_type_change_date", "")
                    housing_array_id = r.get("housing_array_id")
                    apartment_city = r.get("apartment_city", "")

                    for uncov_start, uncov_end in uncovered_intervals:
                        uncov_duration = uncov_end - uncov_start
                        if uncov_duration <= 0:
                            continue

                        # נרמול זמנים לפי יום עבודה
                        if s_end <= CUTOFF:
                            eff_uncov_start = uncov_start + MINUTES_PER_DAY
                            eff_uncov_end = uncov_end + MINUTES_PER_DAY
                        else:
                            eff_uncov_start = uncov_start
                            eff_uncov_end = uncov_end

                        # הוספת סגמנט עבודה - שעות מחוץ לסגמנטים מוגדרים
                        # אם יש סגמנטים מוגדרים מראש (103,105,106,107) - תעריף "שעת עבודה"
                        # אם אין סגמנטים מוגדרים (120 ליווי בי"ח) - תעריף המשמרת המקורית
                        uncovered_shift_id = WORK_HOUR_SHIFT_ID if has_predefined_segments else r["shift_type_id"]
                        entry["segments"].append((
                            eff_uncov_start, eff_uncov_end, "work", "work",
                            uncovered_shift_id, segment_id,
                            apartment_type_id, is_married,
                            apartment_name, p_date, actual_apartment_type_id, None, housing_array_id, apartment_type_name, housing_array_name, rate_apartment_type_name, apartment_type_change_date, rate_apartment_type_id, apartment_city
                        ))

    # Process Daily Segments
    daily_segments = []

    # We need access to is_shabbat_time and calculate_wage_rate which are in logic.py
    # They are imported.

    # Track carryover minutes from previous day's chain ending
    # This is used when a work chain continues from 06:30-08:00 to 08:00-...
    # חישוב carryover מהחודש הקודם
    prev_month_carryover_minutes, prev_month_chain_end, prev_month_chain_shift_id, prev_month_night_minutes, prev_month_chain_housing_array_id = _calculate_previous_month_carryover(
        conn,
        person_id,
        year,
        month,
        minimum_wage,
        preloaded_reports=preloaded_prev_month_reports,
        preloaded_segments=preloaded_segments,
        preloaded_housing_rates_cache=preloaded_prev_month_housing_rates_cache,
    )
    prev_day_carryover_minutes = prev_month_carryover_minutes
    prev_day_chain_end_time = prev_month_chain_end  # זמן סיום הרצף מהחודש הקודם
    prev_day_chain_shift_id = prev_month_chain_shift_id  # shift_id של הרצף האחרון - לבדיקת שינוי תעריף
    prev_day_night_minutes = prev_month_night_minutes  # דקות לילה ברצף הקודם - לקביעת רצף לילה
    prev_day_chain_housing_array_id = prev_month_chain_housing_array_id  # housing_array_id של הרצף האחרון

    # לעקוב אחרי התאריך הקודם - מאתחלים ליום האחרון של החודש הקודם
    # כדי שהבדיקה הראשונה תזהה רציפות נכונה
    if month == 1:
        prev_day_date = date(year - 1, 12, 31)
    else:
        # מציאת היום האחרון של החודש הקודם
        first_of_month = date(year, month, 1)
        prev_day_date = first_of_month - timedelta(days=1)

    for day, entry in sorted(daily_map.items()):
        if (year, month) >= (2026, 3):
            entry["segments"] = _trim_night_first_work_overlaps(entry.get("segments", []))

        shift_names = sorted(entry["shifts"])
        day_shift_ids = entry.get("day_shift_types", set())  # IDs של המשמרות ביום הזה
        is_fixed_segments = entry.get("is_fixed_segments", False)

        day_parts = day.split("/")
        day_date = datetime(int(day_parts[2]), int(day_parts[1]), int(day_parts[0]), tzinfo=LOCAL_TZ).date()

        # בדיקה אם הימים רציפים - אם לא, לאפס carryover
        if prev_day_date is not None:
            days_diff = (day_date - prev_day_date).days
            if days_diff != 1:
                # הימים לא רציפים - אין carryover
                prev_day_carryover_minutes = 0
        
        # Prepare Hebrew Date and Day Name
        days_map = {0: "שני", 1: "שלישי", 2: "רביעי", 3: "חמישי", 4: "שישי", 5: "שבת", 6: "ראשון"}
        day_name_he = days_map.get(day_date.weekday(), "")
        
        h_year, h_month, h_day = hebrew.from_gregorian(day_date.year, day_date.month, day_date.day)
        hebrew_months = {
            1: "ניסן", 2: "אייר", 3: "סיוון", 4: "תמוז", 5: "אב", 6: "אלול",
            7: "תשרי", 8: "חשוון", 9: "כסלו", 10: "טבת", 11: "שבט", 12: "אדר",
            13: "אדר ב'"
        }
        month_name = hebrew_months.get(h_month, str(h_month))
        if h_month == 12 and hebrew.leap(h_year): month_name = "אדר א'"
        elif h_month == 13: month_name = "אדר ב'"
        hebrew_date_str = f"{to_gematria(h_day)} ב{month_name} {to_gematria(h_year)}"
        
        
        # Shabbat / Holiday name
        special_day_name = ""
        day_str = day_date.strftime("%Y-%m-%d")
        
        # Check current day for holiday or parsha
        day_info = shabbat_cache.get(day_str)
        if day_info:
            if day_info.get("holiday"):
                special_day_name = day_info["holiday"]
            elif day_info.get("parsha"):
                special_day_name = day_info["parsha"]
        
        # If Friday and no holiday found, check Saturday for parsha
        if not special_day_name and day_date.weekday() == 4: # Friday
            sat_date = day_date + timedelta(days=1)
            sat_str = sat_date.strftime("%Y-%m-%d")
            sat_info = shabbat_cache.get(sat_str)
            if sat_info and sat_info.get("parsha"):
                special_day_name = sat_info["parsha"]
        
        if special_day_name:
            day_name_he = f"{day_name_he}, {special_day_name}"
        
        # Sort and Dedup Segments
        # entry["segments"]: (start, end, type, label, shift_id, seg_id, apt_type, married, apt_name, actual_date)
        raw_segments = entry["segments"]

        work_segments = []
        standby_segments = []
        vacation_segments = []
        sick_segments = []

        for seg_entry in raw_segments:
            # Normalize length to 19 (now includes apartment_city)
            if len(seg_entry) < 19:
                # Pad with None
                seg_entry = seg_entry + (None,) * (19 - len(seg_entry))

            s_start, s_end, s_type, label, sid, seg_id, apt_type, married, apt_name, actual_date, actual_apt_type, standby_defined_end, housing_array_id, apt_type_name, ha_name, rate_apt_type_name, apt_type_change_date, rate_apt_type, apt_city = seg_entry

            if s_type == "standby":
                # Include shift_type_id (sid) for priority selection when merging
                # Include standby_defined_end for early exit detection
                # Include apt_city for Purim standby rate calculation
                standby_segments.append((s_start, s_end, seg_id, apt_type, married, actual_date, sid, actual_apt_type, standby_defined_end, apt_city))
            elif s_type == "vacation":
                vacation_segments.append((s_start, s_end, actual_date, apt_name, apt_type_name, ha_name, rate_apt_type_name, sid, seg_id, apt_type, married, housing_array_id, actual_apt_type))
            elif s_type == "sick":
                sick_segments.append((s_start, s_end, actual_date, apt_name, apt_type_name, ha_name, rate_apt_type_name, sid, seg_id, apt_type, married, housing_array_id, actual_apt_type))
            else:
                work_segments.append((s_start, s_end, label, sid, apt_name, actual_date, apt_type, actual_apt_type, rate_apt_type, housing_array_id, apt_type_name, ha_name, rate_apt_type_name, apt_type_change_date, apt_city))
                
        work_segments.sort(key=lambda x: x[0])
        standby_segments.sort(key=lambda x: x[0])
        vacation_segments.sort(key=lambda x: x[0])
        sick_segments.sort(key=lambda x: x[0])
        
        # Dedup work - include shift_id to not merge different shifts at same time
        deduped = []
        seen = set()
        for w in work_segments:
            k = (w[0], w[1], w[3])  # (start, end, shift_id)
            if k not in seen:
                deduped.append(w)
                seen.add(k)
        work_segments = deduped  # Each is (start, end, label, sid, apt_name, actual_date, apt_type, actual_apt_type, rate_apt_type, housing_array_id, apt_type_name, ha_name, rate_apt_type_name, apt_type_change_date, apt_city)
        work_segments, overlap_warnings = _resolve_work_segment_overlaps(
            work_segments, shift_rates, minimum_wage
        )

        # Note: Night chain detection is now done per-chain, not per-day
        # A chain is a "night chain" if it has 2+ hours in 22:00-06:00 range
        # This includes carryover hours from previous day/month

        # Dedup standby - now includes shift_type_id, standby_defined_end, apt_city (10 elements)
        deduped_sb = []
        seen_sb = set()
        for sb in standby_segments:
            k = (sb[0], sb[1], sb[2])  # (start, end, seg_id)
            if k not in seen_sb:
                deduped_sb.append(sb)
                seen_sb.add(k)
        standby_segments = deduped_sb

        # Merge continuous standby segments BEFORE cancellation check
        # This ensures we check the FULL standby period, not individual fragments
        # Each standby keeps its original seg_id (from its shift type) for correct rate calculation
        # Also keep the max standby_defined_end for early exit detection
        standby_segments.sort(key=lambda x: x[0])
        merged_standbys = []

        for sb in standby_segments:
            sb_start, sb_end, seg_id, apt_type, married, actual_date, shift_type_id, actual_apt_type, standby_defined_end, sb_apt_city = sb

            if merged_standbys and sb_start <= merged_standbys[-1][1]:  # Overlapping or adjacent
                # Extend the previous merged standby, keep original seg_id
                # Keep the max standby_defined_end for early exit detection
                prev = merged_standbys[-1]
                new_defined_end = max(prev[8] or 0, standby_defined_end or 0) if (prev[8] or standby_defined_end) else None
                merged_standbys[-1] = (prev[0], max(prev[1], sb_end), prev[2], prev[3], prev[4], prev[5], prev[6], prev[7], new_defined_end, prev[9])
            else:
                merged_standbys.append((sb_start, sb_end, seg_id, apt_type, married, actual_date, shift_type_id, actual_apt_type, standby_defined_end, sb_apt_city))

        # Standby Trim Logic - subtract work time from standby instead of cancelling
        # NEW: Early exit detection - if standby ends before its defined end due to early exit,
        # convert partial standby to work hours (continues the chain)
        cancelled_standbys = []
        trimmed_standbys = []
        early_exit_work_segments = []  # כוננויות חלקיות בגלל יציאה מוקדמת - יהפכו לעבודה

        for sb in merged_standbys:
            sb_start, sb_end, seg_id, apt_type, married, actual_date, shift_type_id, actual_apt_type, standby_defined_end, sb_apt_city = sb
            duration = sb_end - sb_start
            if duration <= 0: continue

            # Calculate total overlap with work
            total_overlap = 0
            for w in work_segments:
                total_overlap += overlap_minutes(sb_start, sb_end, w[0], w[1])

            ratio = total_overlap / duration if duration > 0 else 0

            # בדיקת יציאה מוקדמת: אם שעת סיום הכוננות בפועל < שעת סיום הכוננות המוגדרת
            # ואין עבודה שחופפת לכוננות = יציאה מוקדמת
            is_early_exit = (
                standby_defined_end is not None and
                sb_end < standby_defined_end and
                total_overlap == 0  # אין עבודה בתוך הכוננות
            )

            if is_early_exit:
                # יציאה מוקדמת - הכוננות החלקית הופכת לשעות עבודה שממשיכות את הרצף
                # הוספה לרשימת סגמנטי עבודה במקום כוננות
                # (start, end, label, sid, apt_name, actual_date, apt_type, actual_apt_type, rate_apt_type, housing_array_id, apt_type_name, ha_name, rate_apt_type_name, apt_type_change_date, apt_city)
                early_exit_work_segments.append((
                    sb_start, sb_end, "כוננות חלקית", shift_type_id,
                    "", actual_date, apt_type, actual_apt_type, apt_type, None, "", "", "", "", sb_apt_city
                ))
                # לא מוסיפים ל-trimmed_standbys ולא ל-cancelled_standbys
                continue

            if ratio >= STANDBY_CANCEL_OVERLAP_THRESHOLD:
                # כוננות מתבטלת - מורידים עד 70₪, משלמים את ההפרש
                # חלון פרימיום (פורים) עם standby_mode='shabbat': תעריף כוננות שבת
                _pw_filtered = filter_windows_by_city(premium_windows_all, sb_apt_city or "")
                premium_rate = _get_premium_standby_rate(
                    conn, apt_type, bool(married), actual_date, sb_start, _pw_filtered, year, month
                ) if actual_date else None
                if premium_rate is not None:
                    standby_rate = premium_rate
                elif seg_id:
                    standby_rate = get_standby_rate(conn, seg_id, apt_type, bool(married), year, month)
                else:
                    standby_rate = DEFAULT_STANDBY_RATE
                partial_pay = max(0, standby_rate - MAX_CANCELLED_STANDBY_DEDUCTION)

                # בחודשים 11/2025 ו-12/2025: אם הכוננות בוטלה בגלל חפיפה עם משמרת שמירה על דייר (149) - ביטול מלא ללא תשלום
                # אבל לא ביום שישי, שבת או חג
                NIGHT_WATCH_SHIFT_ID = 149  # שמירה על דייר בלילה
                if (year == 2025 and month in (11, 12)):
                    # בדיקה אם היום הוא לא שישי, שבת או חג
                    day_str = actual_date.strftime("%Y-%m-%d") if actual_date else None
                    day_info = shabbat_cache.get(day_str) if day_str else None
                    is_shabbat_or_holiday = actual_date and (
                        actual_date.weekday() in (FRIDAY, SATURDAY) or
                        (day_info and (day_info.get("enter") or day_info.get("exit")))
                    )
                    if not is_shabbat_or_holiday:
                        # בדיקה אם יש חפיפה עם משמרת שמירה על דייר
                        has_night_watch_overlap = any(
                            w[3] == NIGHT_WATCH_SHIFT_ID and overlap_minutes(sb_start, sb_end, w[0], w[1]) > 0
                            for w in work_segments
                        )
                        if has_night_watch_overlap:
                            partial_pay = 0

                reason = f"חפיפה ({int(ratio*100)}%)"
                if partial_pay > 0:
                    reason += f" - שולם {partial_pay:.0f}₪"
                cancelled_standbys.append({
                    "start": sb_start % MINUTES_PER_DAY,
                    "end": sb_end % MINUTES_PER_DAY,
                    "reason": reason,
                    "partial_pay": partial_pay
                })
            else:
                # Trim: subtract work segments from standby
                remaining_parts = [(sb_start, sb_end)]

                for w in work_segments:
                    w_start, w_end = w[0], w[1]
                    new_parts = []
                    for r_start, r_end in remaining_parts:
                        inter_start = max(r_start, w_start)
                        inter_end = min(r_end, w_end)

                        if inter_start < inter_end:
                            # There is overlap - subtract it
                            if r_start < inter_start:
                                new_parts.append((r_start, inter_start))
                            if inter_end < r_end:
                                new_parts.append((inter_end, r_end))
                        else:
                            # No overlap - keep as is
                            new_parts.append((r_start, r_end))
                    remaining_parts = new_parts

                # Add trimmed parts (keep shift_type_id, actual_apt_type, and standby_defined_end)
                for r_start, r_end in remaining_parts:
                    if r_end > r_start:
                        trimmed_standbys.append((r_start, r_end, seg_id, apt_type, married, actual_date, shift_type_id, actual_apt_type, standby_defined_end, sb_apt_city))

        standby_segments = trimmed_standbys

        # הוספת סגמנטי כוננות חלקית (יציאה מוקדמת) לרשימת העבודה
        # הם ייכנסו לרצף העבודה ויחושבו כשעות עבודה
        if early_exit_work_segments:
            work_segments.extend(early_exit_work_segments)
            work_segments.sort(key=lambda x: x[0])
        
        # Calculate Chains
        chains_detail = []

        # משמרת קבועה לגמרי = רק חופשה/מחלה (ללא משמרות עבודה כלל)
        # תגבור עכשיו חלק מהרצף הרגיל לחישוב שעות נוספות
        has_only_vacation_or_sick = is_fixed_segments and len(work_segments) == 0
        is_fully_fixed = has_only_vacation_or_sick

        d_calc100 = 0; d_calc125 = 0; d_calc150 = 0; d_calc175 = 0; d_calc200 = 0
        d_payment = 0; d_standby_pay = 0
        chains = []
        # cancelled_standbys נבנה למעלה בשלב ה-Standby Trim Logic - לא לאתחל מחדש!
        paid_standby_ids = set()  # Track paid standbys to avoid double payment

        # עיבוד חופשה/מחלה/כוננויות רק אם זה יום קבוע לגמרי (אין משמרות עבודה)
        if is_fully_fixed:
            # עיבוד סגמנטי חופשה
            for s, e, actual_date, v_apt_name, v_apt_type_name, v_ha_name, v_rate_apt_type_name, sid, seg_id, apt_type, married, housing_array_id, actual_apt_type in vacation_segments:
                duration = e - s
                if apt_type in SPECIAL_ABSENCE_PAYMENT_APT_TYPES:
                    pay, paid_minutes, effective_rate = _calculate_special_absence_segment_payment(
                        conn,
                        segment_type="work",
                        duration=duration,
                        shift_type_id=sid,
                        segment_id=seg_id,
                        apartment_type_id=apt_type,
                        housing_array_id=housing_array_id,
                        is_married=bool(married),
                        minimum_wage=minimum_wage,
                        year=year,
                        month=month,
                        housing_rates_cache=housing_rates_cache,
                    )
                else:
                    pay = _mul_pay(round(duration / 60, 2), round(minimum_wage, 2))  # חופשה = שעות ותעריף מעוגלים (שיטת מירב)
                    paid_minutes = duration
                    effective_rate = minimum_wage
                d_calc100 += paid_minutes
                d_payment += pay

                start_str = f"{s // 60 % 24:02d}:{s % 60:02d}"
                end_str = f"{e // 60 % 24:02d}:{e % 60:02d}"

                chains.append({
                    "start_time": start_str,
                    "end_time": end_str,
                    "total_minutes": paid_minutes,
                    "payment": pay,
                    "calc100": paid_minutes,
                    "calc125": 0, "calc150": 0, "calc175": 0, "calc200": 0,
                    "type": "vacation",
                    "apartment_name": v_apt_name or "",
                    "apartment_type_name": v_apt_type_name or "",
                    "rate_apartment_type_name": v_rate_apt_type_name or "",
                    "housing_array_name": v_ha_name or "",
                    "shift_name": "חופשה",
                    "shift_type": "חופשה",
                    "segments": [(start_str, end_str, "חופשה")],
                    "break_reason": "",
                    "from_prev_day": False,
                    "effective_rate": effective_rate,
                })

            # עיבוד סגמנטי מחלה - עם אחוזי תשלום מדורגים לפי חוק דמי מחלה
            for s, e, actual_date, sk_apt_name, sk_apt_type_name, sk_ha_name, sk_rate_apt_type_name, sid, seg_id, apt_type, married, housing_array_id, actual_apt_type in sick_segments:
                duration = e - s

                # קביעת מספר יום המחלה ברצף ואחוז התשלום
                sick_date = actual_date.date() if isinstance(actual_date, datetime) else actual_date
                sick_day_num = sick_day_sequence.get(sick_date, 1)
                sick_rate = get_sick_payment_rate(sick_day_num)

                # חישוב תשלום לפי האחוז המדורג - שעות ותעריף מעוגלים (שיטת מירב)
                if apt_type in SPECIAL_ABSENCE_PAYMENT_APT_TYPES:
                    base_pay, paid_minutes, effective_rate = _calculate_special_absence_segment_payment(
                        conn,
                        segment_type="work",
                        duration=duration,
                        shift_type_id=sid,
                        segment_id=seg_id,
                        apartment_type_id=apt_type,
                        housing_array_id=housing_array_id,
                        is_married=bool(married),
                        minimum_wage=minimum_wage,
                        year=year,
                        month=month,
                        housing_rates_cache=housing_rates_cache,
                    )
                    pay = _round_pay(base_pay * sick_rate)
                else:
                    pay = _mul_pay(round(duration / 60, 2), round(minimum_wage, 2) * sick_rate)
                    paid_minutes = duration
                    effective_rate = minimum_wage
                d_calc100 += paid_minutes
                d_payment += pay

                start_str = f"{s // 60 % 24:02d}:{s % 60:02d}"
                end_str = f"{e // 60 % 24:02d}:{e % 60:02d}"

                chains.append({
                    "start_time": start_str,
                    "end_time": end_str,
                    "total_minutes": paid_minutes,
                    "payment": pay,
                    "calc100": paid_minutes,
                    "calc125": 0, "calc150": 0, "calc175": 0, "calc200": 0,
                    "type": "sick",
                    "apartment_name": sk_apt_name or "",
                    "apartment_type_name": sk_apt_type_name or "",
                    "rate_apartment_type_name": sk_rate_apt_type_name or "",
                    "housing_array_name": sk_ha_name or "",
                    "shift_name": "מחלה",
                    "shift_type": "מחלה",
                    "segments": [(start_str, end_str, "מחלה")],
                    "break_reason": "",
                    "from_prev_day": False,
                    "effective_rate": effective_rate,
                    "sick_day_number": sick_day_num,
                    "sick_rate_percent": int(sick_rate * 100),
                })

            special_absence_type = entry.get("special_absence_type")
            if special_absence_type and standby_segments:
                label = "חופשה" if special_absence_type == "vacation" else "מחלה"
                for sb_start, sb_end, seg_id, apt_type, married, actual_date, shift_type_id, actual_apt_type, _standby_defined_end, _sb_apt_city in standby_segments:
                    duration = sb_end - sb_start
                    pay, paid_minutes, effective_rate = _calculate_special_absence_segment_payment(
                        conn,
                        segment_type="standby",
                        duration=duration,
                        shift_type_id=shift_type_id,
                        segment_id=seg_id,
                        apartment_type_id=apt_type,
                        housing_array_id=entry.get("housing_array_id"),
                        is_married=bool(married),
                        minimum_wage=minimum_wage,
                        year=year,
                        month=month,
                        housing_rates_cache=housing_rates_cache,
                    )
                    sick_day_num = None
                    sick_rate_percent = None
                    if special_absence_type == "sick":
                        sick_date = actual_date.date() if isinstance(actual_date, datetime) else actual_date
                        sick_day_num = sick_day_sequence.get(sick_date, 1)
                        sick_rate = get_sick_payment_rate(sick_day_num)
                        sick_rate_percent = int(sick_rate * 100)
                        pay = _round_pay(pay * sick_rate)
                    d_payment += pay
                    start_str = f"{sb_start // 60 % 24:02d}:{sb_start % 60:02d}"
                    end_str = f"{sb_end // 60 % 24:02d}:{sb_end % 60:02d}"
                    chains.append({
                        "start_time": start_str,
                        "end_time": end_str,
                        "total_minutes": paid_minutes,
                        "payment": pay,
                        "calc100": 0,
                        "calc125": 0, "calc150": 0, "calc175": 0, "calc200": 0,
                        "type": special_absence_type,
                        "apartment_name": "",
                        "apartment_type_name": "",
                        "rate_apartment_type_name": "",
                        "housing_array_name": "",
                        "shift_name": f"{label} - כוננות",
                        "shift_type": label,
                        "segments": [(start_str, end_str, "כוננות")],
                        "break_reason": "קיזוז כוננות 70₪",
                        "from_prev_day": False,
                        "effective_rate": effective_rate,
                        "sick_day_number": sick_day_num,
                        "sick_rate_percent": sick_rate_percent,
                    })

            # עיבוד כוננויות רק למשמרות תגבור (לא לחופשה/מחלה)
            is_tagbur = bool(day_shift_ids & TAGBUR_SHIFT_IDS)  # בדיקה לפי ID
            if is_tagbur and standby_segments and not special_absence_type:
                for sb_start, sb_end, seg_id, apt_type, married, actual_date, _shift_type_id, actual_apt_type, _standby_defined_end, _sb_apt_city in standby_segments:
                    duration = sb_end - sb_start
                    if duration <= 0:
                        continue

                    # בדיקה אם כבר שילמנו על כוננות ביום הזה
                    # כוננות משולמת פעם אחת ליום לכל סוג דירה
                    standby_key = ("apt", apt_type)
                    if standby_key in paid_standby_ids:
                        continue  # כבר שולם, דלג

                    # חישוב תשלום כוננות
                    # ASD לילה + תפקוד גבוה: כוננות 150 ש"ח
                    if entry.get("asd_night_high_func") and actual_apt_type == HIGH_FUNCTIONING_APT_TYPE:
                        standby_rate = ASD_NIGHT_STANDBY_RATE
                    else:
                        # חלון פרימיום (פורים) עם standby_mode='shabbat': תעריף כוננות שבת
                        _pw_filtered = filter_windows_by_city(premium_windows_all, _sb_apt_city or "")
                        premium_rate = _get_premium_standby_rate(
                            conn, apt_type, bool(married), actual_date, sb_start, _pw_filtered, year, month
                        ) if actual_date else None
                        if premium_rate is not None:
                            standby_rate = premium_rate
                        elif seg_id:
                            standby_rate = get_standby_rate(conn, seg_id, apt_type, bool(married), year, month)
                        else:
                            standby_rate = DEFAULT_STANDBY_RATE
                    d_standby_pay += standby_rate
                    paid_standby_ids.add(standby_key)

                    start_str = f"{sb_start // 60 % 24:02d}:{sb_start % 60:02d}"
                    end_str = f"{sb_end // 60 % 24:02d}:{sb_end % 60:02d}"
                    tb_asd_lbl = _asd_night_label_for_row(
                        entry.get("asd_night_label_by_apt"),
                        entry.get("asd_night_label_by_apt_type"),
                        "",
                        actual_apt_type,
                    ) if calculate_night_hours_in_segment(sb_start % 1440, sb_end % 1440) > 0 else ""

                    # ASD לילה: סימון המשמרת והסוג — לפי מערך דיור + משמרת לילה ביום
                    tb_is_asd_night = (
                        is_asd_housing_array(entry.get("housing_array_id"))
                        and NIGHT_SHIFT_ID in entry.get("day_shift_types", set())
                    )
                    tb_sb_shift_name = "לילה" if tb_is_asd_night else "כוננות"
                    tb_sb_shift_type = "כוננות לילה" if tb_is_asd_night else "כוננות"

                    chains.append({
                        "start_time": start_str,
                        "end_time": end_str,
                        "total_minutes": duration,
                        "payment": standby_rate,
                        "calc100": 0, "calc125": 0, "calc150": 0, "calc175": 0, "calc200": 0,
                        "type": "standby",
                        "apartment_name": "",
                        "apartment_type_id": actual_apt_type,  # Use actual type for visual indicator
                        "apartment_type_name": "",
                        "rate_apartment_type_name": "",
                        "housing_array_name": "",
                        "shift_name": tb_sb_shift_name,
                        "shift_type": tb_sb_shift_type,
                        "segments": [(start_str, end_str, "כוננות")],
                        "break_reason": "",
                        "from_prev_day": False,
                        "effective_rate": 0,
                        "standby_rate": standby_rate,
                        "asd_night_label": tb_asd_lbl,
                    })

            total_minutes = sum(w[1]-w[0] for w in work_segments) + sum(v[1]-v[0] for v in vacation_segments) + sum(s[1]-s[0] for s in sick_segments)

            # Add escort bonus payment (does NOT add to chain/carryover - bonus is separate from work hours)
            bonus_mins = entry.get("escort_bonus_minutes", 0)
            if bonus_mins > 0:
                # מציאת ה-chain של הליווי הרפואי לקבלת התעריף
                for chain in chains:
                    if chain.get("type") == "work" and chain.get("total_minutes", 0) < 60:
                        effective_rate = chain.get("effective_rate", minimum_wage)
                        bonus_pay = _mul_pay(round(bonus_mins / 60, 2), round(effective_rate, 2))  # שיטת מירב

                        # תשלום בלבד - לא מוסיפים לדקות הרצף
                        d_payment += bonus_pay

                        # עדכון תשלום ה-chain והערה על הבונוס (לתצוגה בלבד)
                        chain["payment"] += bonus_pay
                        chain["escort_bonus_pay"] = bonus_pay  # שמירת הבונוס לצבירה חודשית
                        if chain.get("segments"):
                            old_seg = chain["segments"][0]
                            start_time = old_seg[0]
                            end_time = old_seg[1]
                            chain["segments"] = [(start_time, end_time, f"100% (+ בונוס {bonus_mins} דק')")]
                        break

            # Add partial payments from cancelled standbys (when standby > 70₪)
            cancelled_partial_pay = sum(c.get("partial_pay", 0) for c in cancelled_standbys)
            d_standby_pay += cancelled_partial_pay

            # מיון chains לפי זמן התחלה ביום עבודה (08:00-08:00)
            def fixed_chain_sort_key(c):
                t = c.get("start_time", "00:00")
                h, m = map(int, t.split(":"))
                minutes = h * 60 + m
                # יום עבודה מתחיל ב-08:00 (480 דקות)
                # זמנים 00:00-07:59 הם בעצם 24:00-31:59 ביום העבודה
                if minutes < 480:  # לפני 08:00
                    minutes += MINUTES_PER_DAY
                return minutes

            chains.sort(key=fixed_chain_sort_key)

            daily_segments.append({
                "day": day,
                "day_name": day_name_he,
                "hebrew_date": hebrew_date_str,
                "date_obj": day_date,
                "payment": d_payment,
                "standby_payment": d_standby_pay,
                "calc100": d_calc100, "calc125": d_calc125, "calc150": d_calc150, "calc175": d_calc175, "calc200": d_calc200,
                "shift_names": shift_names,
                "has_work": len(work_segments) > 0,
                "total_minutes": total_minutes,
                "total_minutes_no_standby": total_minutes,
                "chains": chains,
                "cancelled_standbys": cancelled_standbys,
            })
            continue  # דלג לסיבוב הבא - כבר סיימנו את היום הזה

        # Merge all events for processing - כל המשמרות כולל תגבור
        all_events = []
        for s, e, l, sid, apt_name, actual_date, apt_type, actual_apt_type, rate_apt_type, housing_array_id, apt_type_name, ha_name, rate_apt_type_name, apt_type_change_date, apt_city in work_segments:
            all_events.append({"start": s, "end": e, "type": "work", "label": l, "shift_id": sid, "apartment_name": apt_name or "", "apartment_type_id": actual_apt_type, "rate_apt_type": rate_apt_type, "actual_date": actual_date or day_date, "housing_array_id": housing_array_id, "apartment_type_name": apt_type_name or "", "housing_array_name": ha_name or "", "rate_apt_type_name": rate_apt_type_name or "", "apt_type_change_date": apt_type_change_date or ""})
        for s, e, seg_id, apt, married, actual_date, _shift_type_id, actual_apt_type, _standby_defined_end, sb_apt_city in standby_segments:
            all_events.append({"start": s, "end": e, "type": "standby", "label": "כוננות", "seg_id": seg_id, "apt": apt, "actual_apt_type": actual_apt_type, "married": married, "actual_date": actual_date or day_date, "apt_city": sb_apt_city or ""})
        for s, e, actual_date, v_apt_name, v_apt_type_name, v_ha_name, v_rate_apt_type_name, *_ in vacation_segments:
            all_events.append({"start": s, "end": e, "type": "vacation", "label": "חופשה", "actual_date": actual_date or day_date, "apartment_name": v_apt_name or "", "apartment_type_name": v_apt_type_name or "", "housing_array_name": v_ha_name or "", "rate_apt_type_name": v_rate_apt_type_name or ""})
        for s, e, actual_date, sk_apt_name, sk_apt_type_name, sk_ha_name, sk_rate_apt_type_name, *_ in sick_segments:
            all_events.append({"start": s, "end": e, "type": "sick", "label": "מחלה", "actual_date": actual_date or day_date, "apartment_name": sk_apt_name or "", "apartment_type_name": sk_apt_type_name or "", "housing_array_name": sk_ha_name or "", "rate_apt_type_name": sk_rate_apt_type_name or ""})

        all_events.sort(key=lambda x: x["start"])

        # Build a set of work segment boundaries for quick lookup
        # This helps determine if standby truly breaks the chain or if work continues through it
        work_starts = {ws[0] for ws in work_segments}  # All work start times
        work_ends = {ws[1] for ws in work_segments}    # All work end times

        # Process chains logic - כל המשמרות כולל תגבור מחושבות ברצף אחד

        current_chain_segments = []
        last_end = None
        last_etype = None

        def calculate_chain_pay(segments, minutes_offset=0, carryover_night_minutes=0):
            # segments is list of (start, end, label, shift_id, apartment_name, actual_date, apt_type, actual_apt_type, rate_apt_type, housing_array_id, apt_type_name, ha_name, rate_apt_type_name, apt_type_change_date, apt_city)
            # Convert to format expected by _calculate_chain_wages: (start, end, shift_id, actual_date)
            # Include actual_date for each segment for correct Shabbat calculation
            chain_segs = [(s, e, sid, adate) for s, e, l, sid, apt, adate, apt_type, actual_apt_type, rate_apt_type, ha_id, apt_type_name, ha_name, rate_apt_type_name, apt_type_change_date, apt_city in segments]

            # חלונות פרימיום מסוננים לפי עיר הדירה הראשונה ברצף
            first_city = segments[0][14] if segments else ""
            chain_premium_windows = filter_windows_by_city(premium_windows_all, first_city or "")

            # Calculate night hours in current chain segments
            # Times are in extended 00:00-32:00 axis (0-1920 minutes)
            # where 1440+ represents next day (00:00-08:00 after midnight)
            current_chain_night_minutes = 0
            for s, e, l, sid, apt, adate, apt_type, actual_apt_type, rate_apt_type, ha_id, apt_type_name, ha_name, rate_apt_type_name, apt_type_change_date, apt_city in segments:
                # Convert from extended 00:00-32:00 axis to 00:00-24:00 axis
                real_start = s % 1440
                real_end = e % 1440
                # Handle overnight segments (when end wraps around to next day)
                if real_end <= real_start and e > s:
                    real_end += 1440
                current_chain_night_minutes += calculate_night_hours_in_segment(real_start, real_end)

            # Total night minutes in chain = carryover + current
            total_chain_night_minutes = carryover_night_minutes + current_chain_night_minutes

            # A chain is a "night chain" if it has 2+ hours (120 min) in 22:00-06:00 range
            chain_is_night = total_chain_night_minutes >= NIGHT_HOURS_THRESHOLD

            # Use optimized block calculation with carryover offset
            # Pass night chain flag for 7-hour workday threshold
            # Each segment includes its actual_date for correct Shabbat boundary calculation
            result = _calculate_chain_wages(
                chain_segs, shabbat_cache, minutes_offset, chain_is_night,
                premium_windows=chain_premium_windows,
            )

            c_100 = result["calc100"]
            c_125 = result["calc125"]
            c_150 = result["calc150"]
            c_175 = result["calc175"]
            c_200 = result["calc200"]
            seg_detail = result.get("segments_detail", [])

            # Get effective rates from first segment's (shift_id, housing_array_id, rate_apt_type) - accounts for different housing rates
            first_shift_id = segments[0][3] if segments else None
            first_housing_array_id = segments[0][9] if segments else None
            first_rate_apt_type = segments[0][8] if segments else None  # rate_apartment_type_id
            rate_key = (first_shift_id, first_housing_array_id, first_rate_apt_type)
            rates_dict = shift_rates.get(rate_key, {"weekday": minimum_wage, "shabbat": minimum_wage})
            effective_rate = rates_dict["weekday"]  # תעריף ברירת מחדל

            # חישוב תשלום לפי סגמנטים - כל סגמנט עם התעריף המתאים לו (חול/שבת)
            if seg_detail:
                c_pay = 0
                for seg_start, seg_end, seg_label, is_shabbat in seg_detail:
                    seg_minutes = seg_end - seg_start
                    # קביעת תעריף לפי שבת/חול
                    seg_rate = rates_dict["shabbat"] if is_shabbat else rates_dict["weekday"]
                    # קביעת מכפיל לפי אחוז
                    if "200%" in seg_label:
                        multiplier = 2.0
                    elif "175%" in seg_label:
                        multiplier = 1.75
                    elif "150%" in seg_label:
                        multiplier = 1.5
                    elif "125%" in seg_label:
                        multiplier = 1.25
                    else:
                        multiplier = 1.0
                    # חישוב תשלום בנוסחת גשר: round(שעות,2) × round(תעריף×מכפיל,2)
                    c_pay += _mul_pay(round(seg_minutes / 60, 2), round(seg_rate * multiplier, 2))
            else:
                # חישוב תשלום בנוסחת גשר: round(שעות,2) × round(תעריף×מכפיל,2) → עיגול לעשרון (שיטת מירב)
                c_pay = (
                    _mul_pay(round(c_100/60, 2), round(effective_rate * 1.0, 2)) +
                    _mul_pay(round(c_125/60, 2), round(effective_rate * 1.25, 2)) +
                    _mul_pay(round(c_150/60, 2), round(effective_rate * 1.5, 2)) +
                    _mul_pay(round(c_175/60, 2), round(effective_rate * 1.75, 2)) +
                    _mul_pay(round(c_200/60, 2), round(effective_rate * 2.0, 2))
                )

            return c_pay, c_100, c_125, c_150, c_175, c_200, seg_detail, effective_rate

        def close_chain_and_record(segments, break_reason="", minutes_offset=0, carryover_night_minutes=0):
            """Close current chain and add to chains list.
            Each rate segment becomes a separate row in chains.
            Returns (pay, c100, c125, c150, c175, c200, chain_total_minutes, chain_ends_at_0800, chain_night_minutes)"""
            if not segments:
                return 0, 0, 0, 0, 0, 0, 0, False, 0

            pay, c100, c125, c150, c175, c200, seg_detail, effective_rate = calculate_chain_pay(segments, minutes_offset, carryover_night_minutes)

            # Calculate total chain duration (including offset from previous day)
            chain_duration = sum(e - s for s, e, l, sid, apt, adate, apt_type, actual_apt_type, rate_apt_type, ha_id, apt_type_name, ha_name, rate_apt_type_name, apt_type_change_date, apt_city in segments)
            chain_total_minutes = minutes_offset + chain_duration

            # Get apartment names and types from segments - segments is (start, end, label, sid, apt_name, actual_date, apt_type, actual_apt_type, rate_apt_type, housing_array_id, apt_type_name, ha_name)
            chain_apartments = set()
            chain_shift_names = set()
            chain_apt_types = set()
            chain_shift_ids = set()
            chain_actual_apt_types = set()
            chain_rate_apt_types = set()
            chain_apt_type_names = set()
            chain_ha_names = set()
            for s, e, l, sid, apt, adate, apt_type, actual_apt_type, rate_apt_type, ha_id, apt_type_name, ha_name, rate_apt_type_name, apt_type_change_date, apt_city in segments:
                if apt:
                    chain_apartments.add(apt)
                if apt_type:
                    chain_apt_types.add(apt_type)
                if actual_apt_type:
                    chain_actual_apt_types.add(actual_apt_type)
                if rate_apt_type:
                    chain_rate_apt_types.add(rate_apt_type)
                if apt_type_name:
                    chain_apt_type_names.add(apt_type_name)
                if ha_name:
                    chain_ha_names.add(ha_name)
                if sid:
                    chain_shift_ids.add(sid)
                    shift_name = shift_names_map.get(sid, "")
                    if shift_name:
                        chain_shift_names.add(shift_name)
            apt_name = ", ".join(sorted(chain_apartments)) if chain_apartments else ""
            # Use the first (or only) apartment type for the chain
            chain_apt_type = list(chain_apt_types)[0] if chain_apt_types else None
            chain_actual_apt = list(chain_actual_apt_types)[0] if chain_actual_apt_types else None
            chain_rate_apt = list(chain_rate_apt_types)[0] if chain_rate_apt_types else None
            chain_apt_type_name = list(chain_apt_type_names)[0] if chain_apt_type_names else ""
            chain_ha_name = list(chain_ha_names)[0] if chain_ha_names else ""
            chain_shift_id = list(chain_shift_ids)[0] if chain_shift_ids else None
            # שם המשמרת הספציפי של ה-chain (לא כל המשמרות של היום)
            shift_name_str = shift_names_map.get(chain_shift_id, "") if chain_shift_id else ""

            # Helper function: Split a rate segment by apartment boundaries
            def split_segment_by_apartments(seg_start, seg_end, seg_label, is_shabbat, segs):
                """
                פיצול סגמנט לפי גבולות דירות.
                אם יש כמה דירות באותו טווח זמן, מחזיר רשימת תת-סגמנטים.
                """
                result_segments = []
                current_start = seg_start

                # מיון הסגמנטים המקוריים לפי זמן התחלה
                sorted_segs = sorted(segs, key=lambda x: x[0])

                for s, e, l, sid, apt, adate, apt_type, actual_apt_type, rate_apt_type, ha_id, apt_type_name, ha_name, rate_apt_type_name, apt_type_change_date, apt_city in sorted_segs:
                    # בדיקה אם יש חפיפה עם הטווח הנוכחי
                    if s < seg_end and e > current_start:
                        # זמן התחלה של החפיפה
                        overlap_start = max(current_start, s)
                        # זמן סיום של החפיפה
                        overlap_end = min(seg_end, e)

                        if overlap_end > overlap_start:
                            result_segments.append({
                                "start": overlap_start,
                                "end": overlap_end,
                                "label": seg_label,
                                "is_shabbat": is_shabbat,
                                "apt_name": apt,
                                "apt_type": apt_type,
                                "actual_apt_type": actual_apt_type,
                                "rate_apt_type": rate_apt_type,
                                "shift_id": sid,
                                "housing_array_id": ha_id,
                                "apt_type_name": apt_type_name,
                                "ha_name": ha_name,
                                "rate_apt_type_name": rate_apt_type_name,
                                "apt_type_change_date": apt_type_change_date
                            })
                            current_start = overlap_end

                    if current_start >= seg_end:
                        break

                # אם לא נמצאו חפיפות, החזר סגמנט בודד עם ברירת מחדל
                if not result_segments:
                    if segs:
                        s, e, l, sid, apt, adate, apt_type, actual_apt_type, rate_apt_type, ha_id, apt_type_name, ha_name, rate_apt_type_name, apt_type_change_date, apt_city = segs[0]
                        result_segments.append({
                            "start": seg_start,
                            "end": seg_end,
                            "label": seg_label,
                            "is_shabbat": is_shabbat,
                            "apt_name": apt,
                            "apt_type": apt_type,
                            "actual_apt_type": actual_apt_type,
                            "rate_apt_type": rate_apt_type,
                            "shift_id": sid,
                            "housing_array_id": ha_id,
                            "apt_type_name": apt_type_name,
                            "ha_name": ha_name,
                            "rate_apt_type_name": rate_apt_type_name,
                            "apt_type_change_date": apt_type_change_date
                        })
                    else:
                        result_segments.append({
                            "start": seg_start,
                            "end": seg_end,
                            "label": seg_label,
                            "is_shabbat": is_shabbat,
                            "apt_name": "",
                            "apt_type": None,
                            "actual_apt_type": None,
                            "rate_apt_type": None,
                            "shift_id": None,
                            "housing_array_id": None,
                            "apt_type_name": "",
                            "ha_name": "",
                            "rate_apt_type_name": "",
                            "apt_type_change_date": ""
                        })

                return result_segments

            # Create a separate chain row for each rate segment, split by apartment
            # First, expand all segments by apartment boundaries
            expanded_segments = []
            for seg_start, seg_end, seg_label, is_shabbat in seg_detail:
                sub_segments = split_segment_by_apartments(seg_start, seg_end, seg_label, is_shabbat, segments)
                expanded_segments.extend(sub_segments)

            # Now create chain rows from expanded segments
            prev_seg_label = None
            prev_seg_apt_name = None
            prev_seg_shift_id = None

            for i, sub_seg in enumerate(expanded_segments):
                is_first = (i == 0)
                is_last = (i == len(expanded_segments) - 1)

                seg_start = sub_seg["start"]
                seg_end = sub_seg["end"]
                seg_label = sub_seg["label"]
                is_shabbat = sub_seg["is_shabbat"]
                seg_apt_name = sub_seg["apt_name"]
                seg_apt_type = sub_seg["apt_type"]
                seg_actual_apt = sub_seg["actual_apt_type"]
                seg_rate_apt = sub_seg["rate_apt_type"]
                seg_shift_id = sub_seg["shift_id"]
                seg_housing_array_id = sub_seg.get("housing_array_id")
                seg_apt_type_name = sub_seg.get("apt_type_name", "")
                seg_ha_name = sub_seg.get("ha_name", "")
                seg_rate_apt_type_name = sub_seg.get("rate_apt_type_name", "")
                seg_apt_type_change_date = sub_seg.get("apt_type_change_date", "")

                seg_duration = seg_end - seg_start

                # Calculate payment and counts for this segment based on its label
                seg_c100, seg_c125, seg_c150, seg_c175, seg_c200 = 0, 0, 0, 0, 0
                seg_c150_shabbat, seg_c150_overtime = 0, 0
                if "100%" in seg_label:
                    seg_c100 = seg_duration
                elif "125%" in seg_label:
                    seg_c125 = seg_duration
                elif "150%" in seg_label:
                    seg_c150 = seg_duration
                    # Check if Shabbat or overtime
                    if is_shabbat:
                        seg_c150_shabbat = seg_duration
                    else:
                        seg_c150_overtime = seg_duration
                elif "175%" in seg_label:
                    seg_c175 = seg_duration
                elif "200%" in seg_label:
                    seg_c200 = seg_duration

                # קבלת תעריף לפי (shift_id, housing_array_id, rate_apt_type) של הסגמנט הספציפי
                seg_rate_key = (seg_shift_id, seg_housing_array_id, seg_rate_apt)
                seg_rates_dict = shift_rates.get(seg_rate_key, {
                    "weekday": minimum_wage,
                    "shabbat": minimum_wage,
                    "supplement": 0,
                    "apartment_supplement": 0,
                    "asd_seniority_supplement": 0,
                })
                # בחירת תעריף שבת או חול לפי is_shabbat (מטבלת shift_type_housing_rates)
                seg_rate = seg_rates_dict["shabbat"] if is_shabbat else seg_rates_dict["weekday"]

                # תוספת סוג דירה מוצגת בעמודת «בסיס» בטבלה, לא בתווית הפירוט (אחוזים בלבד)
                hourly_supplement = apt_type_apartment_supplement.get(seg_actual_apt, 0)
                apartment_supplement_nis = round(hourly_supplement / 100, 2) if hourly_supplement else 0.0
                asd_seniority_supplement_nis = round(
                    (seg_rates_dict.get("asd_seniority_supplement", 0) or 0) / 100, 2
                )

                # חישוב תשלום בנוסחת גשר: round(שעות,2) × round(תעריף×מכפיל,2) → עיגול לעשרון (שיטת מירב)
                seg_pay = (
                    _mul_pay(round(seg_c100/60, 2), round(seg_rate * 1.0, 2)) +
                    _mul_pay(round(seg_c125/60, 2), round(seg_rate * 1.25, 2)) +
                    _mul_pay(round(seg_c150/60, 2), round(seg_rate * 1.5, 2)) +
                    _mul_pay(round(seg_c175/60, 2), round(seg_rate * 1.75, 2)) +
                    _mul_pay(round(seg_c200/60, 2), round(seg_rate * 2.0, 2))
                )

                start_str = f"{seg_start // 60 % 24:02d}:{seg_start % 60:02d}"
                end_str = f"{seg_end // 60 % 24:02d}:{seg_end % 60:02d}"

                # Determine shift type label (לפי ID, לא לפי שם)
                # השתמש ב-shift_id של הסגמנט הספציפי, לא הרצף כולו
                current_shift_id = seg_shift_id or chain_shift_id
                current_actual_apt = seg_actual_apt if seg_actual_apt is not None else chain_actual_apt
                current_rate_apt = seg_rate_apt if seg_rate_apt is not None else chain_rate_apt

                # קביעת תווית סוג המשמרת
                # is_shabbat מציין אם הזמן בפועל הוא בשבת (לפי כניסה/יציאה)
                # זה חשוב יותר מסוג המשמרת כי משמרת שבת יכולה להמשיך אחרי צאת שבת
                shift_type_label = ""
                if is_tagbur_shift(current_shift_id):
                    shift_type_label = "תגבור"
                elif is_implicit_tagbur(current_shift_id, current_actual_apt, current_rate_apt):
                    # משמרת שישי/שבת בדירה טיפולית עם תעריף דירה רגילה = תגבור
                    shift_type_label = "תגבור"
                elif is_night_shift(current_shift_id):
                    shift_type_label = "לילה"
                elif is_shabbat:
                    # הזמן בפועל הוא בתוך שבת (לפי שעות כניסה/יציאה)
                    shift_type_label = "שבת"
                else:
                    # אחרי צאת שבת או יום חול רגיל
                    shift_type_label = "חול"

                # השתמש בדירה הספציפית של הסגמנט, לא של הרצף כולו
                display_apt_name = seg_apt_name if seg_apt_name else apt_name
                # סוג דירה בפועל (להצגה וללב) - actual_apt_type, לא rate type
                display_apt_type = seg_actual_apt if seg_actual_apt is not None else chain_apt_type
                # שם סוג הדירה בפועל לפי ה-ID
                display_apt_type_name = APT_TYPE_NAMES.get(display_apt_type, seg_apt_type_name or chain_apt_type_name)
                display_ha_name = seg_ha_name if seg_ha_name else chain_ha_name
                # שם סוג דירה לתשלום - רק אם שונה מסוג הדירה בפועל
                # seg_rate_apt = סוג לתשלום (מ-rate_apartment_type_id או היסטורי)
                # seg_actual_apt = סוג הדירה בפועל
                if seg_rate_apt is not None and seg_rate_apt != seg_actual_apt:
                    display_rate_apt_type_name = APT_TYPE_NAMES.get(seg_rate_apt, seg_rate_apt_type_name or "")
                else:
                    display_rate_apt_type_name = ""
                # תאריך שינוי סוג הדירה
                display_apt_type_change_date = seg_apt_type_change_date

                # קביעת סיבת מעבר שורה (אם לא השורה הראשונה)
                # מציג את הסיבה בשורה הנוכחית (למה התחלנו שורה חדשה)
                row_split_reason = ""
                if not is_first:
                    # בדיקת סיבות למעבר שורה
                    if prev_seg_label != seg_label:
                        # שינוי אחוז
                        row_split_reason = f"מעבר ל-{seg_label}"
                    elif prev_seg_apt_name and seg_apt_name and prev_seg_apt_name != seg_apt_name:
                        # שינוי דירה
                        row_split_reason = "דירה אחרת"
                    elif prev_seg_shift_id and seg_shift_id and prev_seg_shift_id != seg_shift_id:
                        # שינוי משמרת
                        row_split_reason = "משמרת אחרת"

                # שמירת ערכים לסגמנט הבא
                prev_seg_label = seg_label
                prev_seg_apt_name = seg_apt_name
                prev_seg_shift_id = seg_shift_id

                # קביעת הערה סופית - סיבת מעבר או סיבת שבירה (בשורה האחרונה)
                final_reason = row_split_reason
                if is_last and break_reason:
                    # בשורה האחרונה, אם יש סיבת שבירה, היא עדיפה
                    final_reason = break_reason

                # שם המשמרת הספציפי של הסגמנט (לא כל המשמרות של היום)
                seg_shift_name = shift_names_map.get(current_shift_id, "") if current_shift_id else shift_name_str

                rate_supp_ag = seg_rates_dict.get("supplement", 0)
                actual_supp_ag = apt_type_supplement.get(seg_actual_apt, 0)
                display_base = _display_base_hourly(
                    seg_rate, minimum_wage, rate_supp_ag, actual_supp_ag
                )
                show_basis_supplements = _should_show_hourly_supplements_in_basis(
                    seg_rate, minimum_wage, rate_supp_ag
                )

                chains.append({
                    "start_time": start_str,
                    "end_time": end_str,
                    "total_minutes": seg_duration,
                    "payment": seg_pay,
                    "calc100": seg_c100,
                    "calc125": seg_c125,
                    "calc150": seg_c150,
                    "calc150_shabbat": seg_c150_shabbat,
                    "calc150_overtime": seg_c150_overtime,
                    "calc175": seg_c175,
                    "calc200": seg_c200,
                    "type": "work",
                    "apartment_name": display_apt_name,
                    "apartment_type_id": display_apt_type,
                    "apartment_type_name": display_apt_type_name,
                    "rate_apartment_type_name": display_rate_apt_type_name,
                    "apartment_type_change_date": display_apt_type_change_date,
                    "housing_array_name": display_ha_name,
                    "shift_name": seg_shift_name,
                    "shift_type": shift_type_label,
                    "shift_id": current_shift_id,  # For identifying special shifts like medical escort
                    "is_special_hourly": shift_is_special_hourly.get(chain_shift_id, False),  # For variable rate tracking
                    "segments": [(start_str, end_str, seg_label)],
                    "break_reason": final_reason,
                    "from_prev_day": (seg_start >= MINUTES_PER_DAY) if is_first else False,
                    "effective_rate": seg_rate,  # שימוש בתעריף הנכון (שבת או חול) לפי is_shabbat
                    "display_base_rate": display_base,  # תעריף בסיס לתצוגה (כולל תוספת דירה בפועל)
                    "apartment_hourly_supplement_nis": apartment_supplement_nis if show_basis_supplements else 0.0,  # תוספת סוג דירה לשעה (שקלים)
                    "asd_seniority_supplement_nis": asd_seniority_supplement_nis if show_basis_supplements else 0.0,  # תוספת ותק ASD לשעה (שקלים)
                    "hourly_wage_supplement": seg_rates_dict.get("supplement", 0),  # כלל התוספות השעתיות באגורות
                    "asd_night_label": _asd_night_label_for_row(
                        entry.get("asd_night_label_by_apt"),
                        entry.get("asd_night_label_by_apt_type"),
                        display_apt_name or "",
                        display_apt_type,
                    ) if calculate_night_hours_in_segment(seg_start % 1440, seg_end % 1440) > 0 else "",
                })

            # Check if chain ends at 08:00 boundary (1920 = 08:00 + 1440)
            # This indicates the chain continues to the next workday
            chain_ends_at_0800 = (segments[-1][1] == 1920) if segments else False

            # Calculate night minutes in this chain (for carryover to next day)
            # Times are in extended 00:00-32:00 axis (0-1920 minutes)
            chain_night_minutes = 0
            for s, e, l, sid, apt, adate, apt_type, actual_apt_type, rate_apt_type, ha_id, apt_type_name, ha_name, rate_apt_type_name, apt_type_change_date, apt_city in segments:
                # Convert from extended 00:00-32:00 axis to 00:00-24:00 axis
                real_start = s % 1440
                real_end = e % 1440
                # Handle overnight segments (when end wraps around to next day)
                if real_end <= real_start and e > s:
                    real_end += 1440
                chain_night_minutes += calculate_night_hours_in_segment(real_start, real_end)
            # Include carryover night minutes in the total
            chain_night_minutes += carryover_night_minutes

            return pay, c100, c125, c150, c175, c200, chain_total_minutes, chain_ends_at_0800, chain_night_minutes

        # Determine if we should use carryover from previous day
        # Carryover applies if the gap between previous chain end and first work start is <= 60 minutes
        first_work_start = None
        first_work_shift_id = None
        first_work_housing_array_id = None
        for evt in all_events:
            if evt["type"] == "work":
                first_work_start = evt["start"]
                first_work_shift_id = evt.get("shift_id")
                first_work_housing_array_id = evt.get("housing_array_id")
                break

        use_carryover = False
        rate_changed_from_prev_day = False
        if first_work_start is not None and prev_day_carryover_minutes > 0:
            # בדיקת הפסקה בין סוף הרצף הקודם לתחילת העבודה היום
            # prev_day_chain_end_time הוא בציר מנורמל (08:00 = 480, אחרי חצות +1440)
            # first_work_start הוא גם בציר מנורמל
            # אם הרצף הקודם הסתיים ב-1920 (08:00) והיום מתחיל ב-480 (08:00), ההפסקה היא 0
            # אם הרצף הקודם הסתיים ב-1890 (07:30) והיום מתחיל ב-480 (08:00), ההפסקה היא 30 דקות

            # המרה לציר אחיד: סוף יום קודם הוא ביחס ל-1440 (תחילת יום חדש)
            # first_work_start הוא בציר של היום הנוכחי (מתחיל מ-480)
            # צריך להשוות: (first_work_start + 1440) - prev_day_chain_end_time
            # או: first_work_start - (prev_day_chain_end_time - 1440)

            prev_end_in_new_day = prev_day_chain_end_time - 1440  # המרה לציר היום הבא
            gap_minutes = first_work_start - prev_end_in_new_day

            # אם ההפסקה היא פחות מ-60 דקות, הרצף נמשך
            # לפני 02/2026: הפסקה של בדיוק 60 דקות לא שוברת רצף (תואם לתלושים שכבר יצאו)
            use_carryover = (gap_minutes < BREAK_THRESHOLD_MINUTES) if (year, month) >= (2026, 2) else (gap_minutes <= BREAK_THRESHOLD_MINUTES)

            # בדיקה אם התעריף השתנה בין הרצף הקודם לרצף הנוכחי
            # שינוי תעריף לא שובר את הרצף לגמרי - הוא מעביר את ה-offset
            if use_carryover and prev_day_chain_shift_id is not None and first_work_shift_id is not None:
                prev_rate_key = (prev_day_chain_shift_id, prev_day_chain_housing_array_id, None)
                first_rate_key = (first_work_shift_id, first_work_housing_array_id, None)
                prev_rates = shift_rates.get(prev_rate_key, {"weekday": minimum_wage})
                first_rates = shift_rates.get(first_rate_key, {"weekday": minimum_wage})
                if prev_rates["weekday"] != first_rates["weekday"]:
                    rate_changed_from_prev_day = True

        current_offset = prev_day_carryover_minutes if use_carryover else 0
        current_night_minutes = prev_day_night_minutes if use_carryover else 0  # Night minutes from carryover

        # Reset carryover tracking for this day
        day_carryover_for_next = 0
        last_chain_ended_at_0800 = False
        last_chain_total = 0
        last_chain_night_minutes = 0  # Track night minutes for carryover to next day
        last_chain_shift_id = None
        last_chain_housing_array_id = None

        # Re-process chains with proper carryover
        # We need to re-process since the first chain might need offset
        current_chain_segments = []
        last_end = None
        last_etype = None
        d_calc100 = 0; d_calc125 = 0; d_calc150 = 0; d_calc175 = 0; d_calc200 = 0
        d_payment = 0
        chains = []  # Reset chains list
        first_chain_of_day = True

        for event in all_events:
            start, end, etype = event["start"], event["end"], event["type"]
            is_special = etype in ("standby", "vacation", "sick")

            should_break = False
            break_reason = ""
            if current_chain_segments:
                if is_special:
                    # כוננות שוברת רצף רק אם אין עבודה שחופפת לה או ממשיכה אחריה
                    # בדיקה: האם יש עבודה שמסתיימת אחרי תחילת הכוננות או מתחילה לפני סוף הכוננות?
                    standby_overlaps_work = any(
                        ws[0] < end and ws[1] > start  # עבודה חופפת לכוננות
                        for ws in work_segments
                    )
                    if standby_overlaps_work:
                        # יש עבודה שחופפת לכוננות - לא לשבור רצף
                        should_break = False
                    else:
                        should_break = True
                        break_reason = etype
                elif last_end is not None and ((start - last_end) >= BREAK_THRESHOLD_MINUTES if (year, month) >= (2026, 2) else (start - last_end) > BREAK_THRESHOLD_MINUTES):
                    should_break = True
                    break_reason = f"הפסקה ({start - last_end} דקות)"

            # בדיקה נוספת: האם התעריף משתנה?
            # אם הסגמנט החדש הוא עם תעריף שונה מהסגמנטים הקודמים ב-chain, צריך לסגור את ה-chain
            if not is_special and current_chain_segments and not should_break:
                new_shift_id = event.get("shift_id")
                new_housing_array_id = event.get("housing_array_id")
                new_rate_apt_type = event.get("rate_apt_type")
                new_rate_key = (new_shift_id, new_housing_array_id, new_rate_apt_type)
                new_rates = shift_rates.get(new_rate_key, {"weekday": minimum_wage})
                # בדיקת התעריף של ה-chain הנוכחי - משווים תעריף חול
                current_shift_id = current_chain_segments[0][3] if current_chain_segments else None
                current_housing_array_id = current_chain_segments[0][9] if current_chain_segments else None
                current_rate_apt_type = current_chain_segments[0][8] if current_chain_segments else None
                current_rate_key = (current_shift_id, current_housing_array_id, current_rate_apt_type)
                current_rates = shift_rates.get(current_rate_key, {"weekday": minimum_wage})
                if new_rates["weekday"] != current_rates["weekday"]:
                    should_break = True
                    break_reason = "שינוי תעריף"

            if should_break:
                chain_offset = current_offset
                chain_night_offset = current_night_minutes
                pay, c100, c125, c150, c175, c200, chain_total, ends_at_0800, chain_night = close_chain_and_record(
                    current_chain_segments, break_reason, chain_offset, chain_night_offset)
                d_payment += pay
                d_calc100 += c100; d_calc125 += c125; d_calc150 += c150; d_calc175 += c175; d_calc200 += c200

                # Track last chain info for potential carryover to next day
                last_chain_total = chain_total
                last_chain_ended_at_0800 = ends_at_0800
                last_chain_night_minutes = chain_night
                last_chain_shift_id = current_chain_segments[0][3] if current_chain_segments else None
                last_chain_housing_array_id = current_chain_segments[0][9] if current_chain_segments else None

                # אם נשבר בגלל שינוי תעריף, צריך להעביר את ה-minutes offset ל-chain הבא
                # כי ה-overtime נמשך על פני כל יום העבודה
                # גם שעות הלילה מועברות כי הרצף ממשיך
                if break_reason == "שינוי תעריף":
                    current_offset = chain_total  # ה-offset לchain הבא כולל את כל הדקות עד עכשיו
                    current_night_minutes = chain_night  # גם שעות הלילה מועברות
                else:
                    current_offset = 0  # הפסקה/כוננות מאפסת את ה-offset
                    current_night_minutes = 0  # גם שעות הלילה מתאפסות

                current_chain_segments = []
                first_chain_of_day = False

            if is_special:
                if etype == "standby":
                    is_cont = (last_etype == "standby" and last_end == start)

                    # בדיקה אם כבר שילמנו על כוננות ביום הזה
                    # כוננות משולמת פעם אחת ליום לכל סוג דירה
                    apt_type = event.get("apt")
                    standby_key = ("apt", apt_type)
                    already_paid = standby_key in paid_standby_ids

                    if not is_cont and not already_paid:
                        # ASD לילה + תפקוד גבוה: כוננות 150 ש"ח
                        actual_apt_type_ev = event.get("actual_apt_type")
                        if entry.get("asd_night_high_func") and actual_apt_type_ev == HIGH_FUNCTIONING_APT_TYPE:
                            rate = ASD_NIGHT_STANDBY_RATE
                        else:
                            # חלון פרימיום (פורים) עם standby_mode='shabbat': תעריף כוננות שבת
                            ev_actual_date = event.get("actual_date")
                            _pw_filtered = filter_windows_by_city(premium_windows_all, event.get("apt_city", "") or "")
                            premium_rate = _get_premium_standby_rate(
                                conn, apt_type, bool(event.get("married")),
                                ev_actual_date, start, _pw_filtered, year, month
                            ) if ev_actual_date else None
                            if premium_rate is not None:
                                rate = premium_rate
                            else:
                                rate = get_standby_rate(conn, event.get("seg_id") or 0, apt_type, bool(event.get("married")), year, month)
                        d_standby_pay += rate
                        paid_standby_ids.add(standby_key)

                    sb_asd_lbl = _asd_night_label_for_row(
                        entry.get("asd_night_label_by_apt"),
                        entry.get("asd_night_label_by_apt_type"),
                        event.get("apartment_name") or "",
                        event.get("actual_apt_type"),
                    )

                    # ASD לילה: סימון המשמרת והסוג — לפי מערך דיור + משמרת לילה ביום
                    is_asd_night_standby = (
                        is_asd_housing_array(entry.get("housing_array_id"))
                        and NIGHT_SHIFT_ID in entry.get("day_shift_types", set())
                    )
                    sb_shift_name = "לילה" if is_asd_night_standby else "כוננות"
                    sb_shift_type = "כוננות לילה" if is_asd_night_standby else "כוננות"

                    chains.append({
                        "start_time": f"{start // 60 % 24:02d}:{start % 60:02d}",
                        "end_time": f"{end // 60 % 24:02d}:{end % 60:02d}",
                        "total_minutes": end - start,
                        "payment": 0,
                        "calc100": 0, "calc125": 0, "calc150": 0, "calc175": 0, "calc200": 0,
                        "type": "standby",
                        "apartment_name": event.get("apartment_name", ""),
                        "apartment_type_id": event.get("actual_apt_type"),  # Use actual type for visual indicator
                        "apartment_type_name": "",
                        "rate_apartment_type_name": "",
                        "housing_array_name": "",
                        "shift_name": sb_shift_name,
                        "shift_type": sb_shift_type,
                        "segments": [],
                        "break_reason": "",
                        "from_prev_day": start >= MINUTES_PER_DAY,
                        "effective_rate": minimum_wage,
                        "asd_night_label": sb_asd_lbl,
                    })
                elif etype == "vacation" or etype == "sick":
                    duration = end - start
                    hrs = round(duration / 60, 2)  # שעות מעוגלות
                    pay = hrs * round(minimum_wage, 2)  # שיטת מירב
                    d_payment += pay
                    d_calc100 += duration  # מחלה/חופשה = 100%

                    label = "חופשה" if etype == "vacation" else "מחלה"
                    chains.append({
                        "start_time": f"{start // 60 % 24:02d}:{start % 60:02d}",
                        "end_time": f"{end // 60 % 24:02d}:{end % 60:02d}",
                        "total_minutes": duration,
                        "payment": pay,
                        "calc100": duration, "calc125": 0, "calc150": 0, "calc175": 0, "calc200": 0,
                        "type": etype,  # "vacation" או "sick"
                        "apartment_name": event.get("apartment_name", ""),
                        "apartment_type_id": None,
                        "apartment_type_name": event.get("apartment_type_name", ""),
                        "rate_apartment_type_name": event.get("rate_apt_type_name", ""),
                        "housing_array_name": event.get("housing_array_name", ""),
                        "shift_name": label,
                        "shift_type": label,
                        "segments": [(f"{start // 60 % 24:02d}:{start % 60:02d}", f"{end // 60 % 24:02d}:{end % 60:02d}", label)],
                        "break_reason": "",
                        "from_prev_day": start >= MINUTES_PER_DAY,
                        "effective_rate": minimum_wage,
                    })

                last_end = end
                last_etype = etype
            else:
                # segments: (start, end, label, shift_id, apt_name, actual_date, apt_type, actual_apt_type, rate_apt_type, housing_array_id, apt_type_name, ha_name, rate_apt_type_name, apt_type_change_date, apt_city)
                # apt_type = rate_apt_type (לחישוב), actual_apt_type = apartment_type_id (להצגה)
                current_chain_segments.append((start, end, event["label"], event["shift_id"], event.get("apartment_name", ""), event.get("actual_date"), event.get("rate_apt_type"), event.get("apartment_type_id"), event.get("rate_apt_type"), event.get("housing_array_id"), event.get("apartment_type_name", ""), event.get("housing_array_name", ""), event.get("rate_apt_type_name", ""), event.get("apt_type_change_date", ""), event.get("apt_city", "")))
                last_end = end
                last_etype = etype

        # Close last chain
        if current_chain_segments:
            chain_offset = current_offset
            chain_night_offset = current_night_minutes
            pay, c100, c125, c150, c175, c200, chain_total, ends_at_0800, chain_night = close_chain_and_record(
                current_chain_segments, "", chain_offset, chain_night_offset)
            d_payment += pay
            d_calc100 += c100; d_calc125 += c125; d_calc150 += c150; d_calc175 += c175; d_calc200 += c200

            # Track for potential carryover
            last_chain_total = chain_total
            last_chain_ended_at_0800 = ends_at_0800
            last_chain_night_minutes = chain_night
            last_chain_shift_id = current_chain_segments[0][3] if current_chain_segments else None
            last_chain_housing_array_id = current_chain_segments[0][9] if current_chain_segments else None

        # Update carryover for next day
        # If the last chain ended at 08:00 (1920 normalized), save its total for next day
        if last_chain_ended_at_0800:
            prev_day_carryover_minutes = last_chain_total
            prev_day_chain_end_time = 1920  # 08:00 normalized
            prev_day_night_minutes = last_chain_night_minutes  # Night minutes for next day's chain
            prev_day_chain_shift_id = last_chain_shift_id
            prev_day_chain_housing_array_id = last_chain_housing_array_id
        else:
            prev_day_carryover_minutes = 0
            prev_day_chain_end_time = 0
            prev_day_night_minutes = 0
            prev_day_chain_shift_id = None
            prev_day_chain_housing_array_id = None
            
        # Calculate total_minutes
        total_minutes = sum(w[1]-w[0] for w in work_segments)
        for sb in standby_segments:
            total_minutes += sb[1] - sb[0]

        # מיון chains לפי זמן התחלה ביום עבודה (08:00-08:00)
        # זמנים לפני 08:00 שייכים לסוף יום העבודה ולכן ממוינים אחרי זמנים מ-08:00+
        def chain_sort_key(c):
            t = c.get("start_time", "00:00")
            h, m = map(int, t.split(":"))
            minutes = h * 60 + m
            # יום עבודה מתחיל ב-08:00 (480 דקות)
            # זמנים 00:00-07:59 הם בעצם 24:00-31:59 ביום העבודה
            if minutes < 480:  # לפני 08:00
                minutes += MINUTES_PER_DAY
            return minutes

        chains.sort(key=chain_sort_key)

        # Add escort bonus payment (does NOT add to chain/carryover - bonus is separate from work hours)
        bonus_mins = entry.get("escort_bonus_minutes", 0)
        if bonus_mins > 0:
            # מציאת ה-chain של הליווי הרפואי לקבלת התעריף
            for chain in chains:
                if chain.get("type") == "work" and chain.get("total_minutes", 0) < 60:
                    effective_rate = chain.get("effective_rate", minimum_wage)
                    bonus_pay = _mul_pay(round(bonus_mins / 60, 2), round(effective_rate, 2))  # שיטת מירב

                    # תשלום בלבד - לא מוסיפים לדקות הרצף
                    d_payment += bonus_pay

                    # עדכון תשלום ה-chain והערה על הבונוס (לתצוגה בלבד)
                    chain["payment"] += bonus_pay
                    chain["escort_bonus_pay"] = bonus_pay  # שמירת הבונוס לצבירה חודשית
                    if chain.get("segments"):
                        old_seg = chain["segments"][0]
                        start_time = old_seg[0]
                        end_time = old_seg[1]
                        chain["segments"] = [(start_time, end_time, f"100% (+ בונוס {bonus_mins} דק')")]
                    break

        # Add partial payments from cancelled standbys (when standby > 70₪)
        cancelled_partial_pay = sum(c.get("partial_pay", 0) for c in cancelled_standbys)
        d_standby_pay += cancelled_partial_pay

        daily_segments.append({
            "day": day,
            "day_name": day_name_he,
            "hebrew_date": hebrew_date_str,
            "date_obj": day_date,
            "payment": d_payment,
            "standby_payment": d_standby_pay,
            "calc100": d_calc100, "calc125": d_calc125, "calc150": d_calc150, "calc175": d_calc175, "calc200": d_calc200,
            "shift_names": shift_names,
            "has_work": len(work_segments) > 0,
            "total_minutes": total_minutes,
            "total_minutes_no_standby": sum(w[1]-w[0] for w in work_segments),
            "chains": chains,
            "cancelled_standbys": cancelled_standbys,
            "overlap_warnings": overlap_warnings,
        })

        # עדכון התאריך הקודם לסיבוב הבא
        prev_day_date = day_date

    return daily_segments, reports[0]["person_name"] if reports else ""


def aggregate_daily_segments_to_monthly(
    conn,
    daily_segments: List[Dict],
    person_id: int,
    year: int,
    month: int,
    minimum_wage: float,
    preloaded_payment_comps: Optional[List] = None,
    person_start_date: Optional[Any] = None,
    housing_filter: Optional[int] = None
) -> Dict[str, Any]:
    """
    מאחד את כל הנתונים מ-daily_segments למילון monthly_totals.
    זהו מקור האמת היחיד לחישוב שכר - מחליף את calculate_person_monthly_totals.

    Args:
        conn: חיבור לדאטבייס
        daily_segments: רשימת ימים עם פירוט הרצפים (מ-get_daily_segments_data)
        person_id: מזהה העובד
        year: שנה
        month: חודש
        minimum_wage: שכר מינימום לחודש
        preloaded_payment_comps: רשימת רכיבי תשלום שנטענו מראש (אופטימיזציה)
        person_start_date: תאריך תחילת העבודה של העובד (אופטימיזציה)

    Returns:
        מילון monthly_totals עם כל השדות הנדרשים לכל הטאבים
    """
    from utils.utils import calculate_accruals
    from datetime import datetime
    from zoneinfo import ZoneInfo

    LOCAL_TZ = ZoneInfo("Asia/Jerusalem")

    # אתחול סיכומים
    monthly_totals = {
        # שעות לפי אחוזים (בדקות)
        "calc100": 0,
        "calc125": 0,
        "calc150": 0,
        "calc150_shabbat": 0,
        "calc150_shabbat_100": 0,
        "calc150_shabbat_50": 0,
        "calc150_overtime": 0,
        "calc175": 0,
        "calc200": 0,
        "calc_variable": 0,

        # תשלומים לפי אחוזים
        "payment_calc100": 0.0,
        "payment_calc125": 0.0,
        "payment_calc150": 0.0,
        "payment_calc150_overtime": 0.0,
        "payment_calc150_shabbat": 0.0,
        "payment_calc175": 0.0,
        "payment_calc200": 0.0,
        "payment_calc_variable": 0.0,

        # סיכומים
        "total_hours": 0,
        "payment": 0.0,
        "standby": 0,
        "standby_payment": 0.0,

        # חופשה ומחלה
        "vacation_minutes": 0,
        "vacation_payment": 0.0,
        "vacation_payment_details": [],
        "vacation": 0,
        "vacation_days_taken": 0,
        "sick_minutes": 0,
        "effective_sick_minutes": 0,
        "non_effective_sick_minutes": 0,
        "sick_payment": 0.0,
        "sick_payment_details": [],
        "sick_days_taken": 0,
        "sick_days_accrued": 0.0,
        "vacation_days_accrued": 0.0,

        # נסיעות, תומך מקצועי, תשלום חג ותוספות
        "travel": 0.0,
        "professional_support": 0.0,
        "holiday_payment": 0.0,
        "extras": 0.0,
        "extras_for_pension": 0.0,

        # ימי עבודה
        "actual_work_days": 0,

        # תעריף משתנה - מבנה חדש לתמיכה במספר תעריפים
        "variable_rate_value": minimum_wage,
        "variable_rate_extra_payment": 0.0,
        "variable_rates": {},  # {rate_value: {calc100, calc125, calc150, calc175, calc200, payment}}

        # תעריף בסיס ממוצע - לחישוב תעריף בגשר (כולל תוספות סוג דירה)
        "regular_minutes_sum": 0,
        "regular_rate_x_minutes_sum": 0.0,
        "component_rate_minutes_sum": {},
        "component_rate_x_minutes_sum": {},
        "component_base_rates": {},
    }

    def add_component_rate(component_key: str, minutes: int | float, rate: float) -> None:
        if minutes <= 0:
            return
        monthly_totals["component_rate_minutes_sum"][component_key] = (
            monthly_totals["component_rate_minutes_sum"].get(component_key, 0) + minutes
        )
        monthly_totals["component_rate_x_minutes_sum"][component_key] = (
            monthly_totals["component_rate_x_minutes_sum"].get(component_key, 0.0) + minutes * rate
        )

    sick_payment_details_by_rate: dict[float, dict[str, float]] = {}
    vacation_payment_details_by_rate: dict[float, dict[str, float]] = {}

    def add_sick_payment_detail(minutes: int | float, payment: float, rate: float) -> None:
        if payment <= 0 or rate <= 0:
            return
        rounded_rate = round(rate, 2)
        paid_hours = round(payment / rounded_rate, 2)
        detail = sick_payment_details_by_rate.setdefault(
            rounded_rate,
            {"hours": 0.0, "rate": rounded_rate, "payment": 0.0, "raw_minutes": 0.0},
        )
        detail["hours"] += paid_hours
        detail["payment"] += payment
        detail["raw_minutes"] += minutes

    def add_vacation_payment_detail(minutes: int | float, payment: float, rate: float) -> None:
        if payment <= 0 or rate <= 0:
            return
        rounded_rate = round(rate, 2)
        detail = vacation_payment_details_by_rate.setdefault(
            rounded_rate,
            {"hours": 0.0, "rate": rounded_rate, "payment": 0.0},
        )
        detail["hours"] += round(minutes / 60, 2)
        detail["payment"] += payment

    # ספירת ימי עבודה, חופשה ומחלה
    work_days_set = set()
    vacation_days_set = set()
    sick_days_set = set()
    standby_days_set = set()

    # עיבוד כל הימים
    for day in daily_segments:
        day_date = day.get("date_obj")

        # ספירת ימי עבודה
        if day.get("has_work"):
            work_days_set.add(day_date)

        # צבירת סיכומים יומיים
        monthly_totals["payment"] += day.get("payment", 0) or 0
        monthly_totals["standby_payment"] += day.get("standby_payment", 0) or 0

        # עיבוד רצפים (chains) לחישוב מדויק של שעות ותשלומים
        for chain in day.get("chains", []):
            chain_type = chain.get("type", "work")
            effective_rate = chain.get("effective_rate", minimum_wage)

            # תעריף משתנה: משמרת עם תעריף שעתי מיוחד, או תעריף שונה משכר מינימום + תוספת
            is_special_hourly = chain.get("is_special_hourly", False)
            supplement = float(chain.get("hourly_wage_supplement", 0)) / 100
            is_variable_rate = is_special_hourly or abs(effective_rate - minimum_wage - supplement) > 0.01

            if chain_type == "work":
                # אתחול מילון לתעריף משתנה אם צריך
                if is_variable_rate:
                    rate_key = round(effective_rate, 2)
                    if rate_key not in monthly_totals["variable_rates"]:
                        monthly_totals["variable_rates"][rate_key] = {
                            "calc100": 0, "calc125": 0, "calc150": 0,
                            "calc175": 0, "calc200": 0, "payment": 0.0
                        }

                # תעריף מעוגל לחישוב (שיטת מירב)
                rounded_rate = round(effective_rate, 2)

                # שעות רגילות (100%)
                c100 = chain.get("calc100", 0) or 0
                if c100 > 0:
                    if is_variable_rate:
                        monthly_totals["calc_variable"] += c100
                        monthly_totals["payment_calc_variable"] += _mul_pay(round(c100 / 60, 2), rounded_rate)
                        monthly_totals["variable_rate_value"] = effective_rate
                        # שמירה גם במבנה החדש
                        monthly_totals["variable_rates"][rate_key]["calc100"] += c100
                        monthly_totals["variable_rates"][rate_key]["payment"] += _mul_pay(round(c100 / 60, 2), rounded_rate)
                    else:
                        monthly_totals["calc100"] += c100
                        monthly_totals["payment_calc100"] += _mul_pay(round(c100 / 60, 2), rounded_rate)
                        add_component_rate("calc100", c100, rounded_rate)

                # שעות נוספות 125%
                c125 = chain.get("calc125", 0) or 0
                if c125 > 0:
                    if is_variable_rate:
                        monthly_totals["calc_variable"] += c125
                        monthly_totals["payment_calc_variable"] += _mul_pay(round(c125 / 60, 2), round(rounded_rate * 1.25, 2))
                        monthly_totals["variable_rate_value"] = effective_rate
                        # שמירה גם במבנה החדש
                        monthly_totals["variable_rates"][rate_key]["calc125"] += c125
                        monthly_totals["variable_rates"][rate_key]["payment"] += _mul_pay(round(c125 / 60, 2), round(rounded_rate * 1.25, 2))
                    else:
                        monthly_totals["calc125"] += c125
                        monthly_totals["payment_calc125"] += _mul_pay(round(c125 / 60, 2), round(rounded_rate * 1.25, 2))
                        add_component_rate("calc125", c125, rounded_rate)

                # שעות נוספות 150% (כולל הפרדה בין חול לשבת)
                c150 = chain.get("calc150", 0) or 0
                c150_shabbat = chain.get("calc150_shabbat", 0) or 0
                c150_overtime = chain.get("calc150_overtime", 0) or 0

                if c150 > 0:
                    if is_variable_rate:
                        monthly_totals["calc_variable"] += c150
                        monthly_totals["payment_calc_variable"] += _mul_pay(round(c150 / 60, 2), round(rounded_rate * 1.5, 2))
                        monthly_totals["variable_rate_value"] = effective_rate
                        # שמירה גם במבנה החדש
                        monthly_totals["variable_rates"][rate_key]["calc150"] += c150
                        monthly_totals["variable_rates"][rate_key]["payment"] += _mul_pay(round(c150 / 60, 2), round(rounded_rate * 1.5, 2))
                    else:
                        monthly_totals["calc150"] += c150
                        monthly_totals["payment_calc150"] += _mul_pay(round(c150 / 60, 2), round(rounded_rate * 1.5, 2))
                        add_component_rate("calc150", c150, rounded_rate)

                        # הפרדה בין שבת לחול
                        if c150_shabbat > 0:
                            monthly_totals["calc150_shabbat"] += c150_shabbat
                            monthly_totals["calc150_shabbat_100"] += c150_shabbat
                            monthly_totals["calc150_shabbat_50"] += c150_shabbat
                            monthly_totals["payment_calc150_shabbat"] += _mul_pay(round(c150_shabbat / 60, 2), round(rounded_rate * 1.5, 2))
                            add_component_rate("calc150_shabbat", c150_shabbat, rounded_rate)
                            add_component_rate("calc150_shabbat_100", c150_shabbat, rounded_rate)
                            add_component_rate("calc150_shabbat_50", c150_shabbat, rounded_rate)
                        if c150_overtime > 0:
                            monthly_totals["calc150_overtime"] += c150_overtime
                            monthly_totals["payment_calc150_overtime"] += _mul_pay(round(c150_overtime / 60, 2), round(rounded_rate * 1.5, 2))
                            add_component_rate("calc150_overtime", c150_overtime, rounded_rate)

                # שעות שבת 175%
                c175 = chain.get("calc175", 0) or 0
                if c175 > 0:
                    if is_variable_rate:
                        monthly_totals["calc_variable"] += c175
                        monthly_totals["payment_calc_variable"] += _mul_pay(round(c175 / 60, 2), round(rounded_rate * 1.75, 2))
                        monthly_totals["variable_rate_value"] = effective_rate
                        # שמירה גם במבנה החדש
                        monthly_totals["variable_rates"][rate_key]["calc175"] += c175
                        monthly_totals["variable_rates"][rate_key]["payment"] += _mul_pay(round(c175 / 60, 2), round(rounded_rate * 1.75, 2))
                    else:
                        monthly_totals["calc175"] += c175
                        monthly_totals["payment_calc175"] += _mul_pay(round(c175 / 60, 2), round(rounded_rate * 1.75, 2))
                        add_component_rate("calc175", c175, rounded_rate)

                # שעות שבת 200%
                c200 = chain.get("calc200", 0) or 0
                if c200 > 0:
                    if is_variable_rate:
                        monthly_totals["calc_variable"] += c200
                        monthly_totals["payment_calc_variable"] += _mul_pay(round(c200 / 60, 2), round(rounded_rate * 2.0, 2))
                        monthly_totals["variable_rate_value"] = effective_rate
                        # שמירה גם במבנה החדש
                        monthly_totals["variable_rates"][rate_key]["calc200"] += c200
                        monthly_totals["variable_rates"][rate_key]["payment"] += _mul_pay(round(c200 / 60, 2), round(rounded_rate * 2.0, 2))
                    else:
                        monthly_totals["calc200"] += c200
                        monthly_totals["payment_calc200"] += _mul_pay(round(c200 / 60, 2), round(rounded_rate * 2.0, 2))
                        add_component_rate("calc200", c200, rounded_rate)

                # בונוס ליווי רפואי (תשלום בלבד, לא נספר בשעות)
                escort_bonus = chain.get("escort_bonus_pay", 0) or 0
                if escort_bonus > 0:
                    if is_variable_rate:
                        monthly_totals["payment_calc_variable"] += escort_bonus
                        monthly_totals["variable_rates"][rate_key]["payment"] += escort_bonus
                    else:
                        monthly_totals["payment_calc100"] += escort_bonus

                # צבירת תעריף בסיס ממוצע עבור שעות רגילות (לא תעריף משתנה)
                if not is_variable_rate:
                    total_chain_minutes = c100 + c125 + c150 + c175 + c200
                    if total_chain_minutes > 0:
                        monthly_totals["regular_minutes_sum"] += total_chain_minutes
                        monthly_totals["regular_rate_x_minutes_sum"] += total_chain_minutes * rounded_rate

            elif chain_type == "standby":
                standby_days_set.add(day_date)

            elif chain_type == "vacation":
                vacation_days_set.add(day_date)
                vacation_mins = chain.get("total_minutes", 0) or 0
                vacation_pay = chain.get("payment", 0) or 0
                vacation_effective_rate = chain.get("effective_rate", minimum_wage) or minimum_wage
                monthly_totals["vacation_minutes"] += vacation_mins
                monthly_totals["vacation_payment"] += vacation_pay
                add_vacation_payment_detail(vacation_mins, vacation_pay, vacation_effective_rate)

            elif chain_type == "sick":
                sick_days_set.add(day_date)
                sick_mins = chain.get("total_minutes", 0) or 0
                sick_pay = chain.get("payment", 0) or 0
                sick_rate = chain.get("sick_rate_percent", 100) / 100
                sick_effective_rate = chain.get("effective_rate", minimum_wage) or minimum_wage
                monthly_totals["sick_minutes"] += sick_mins
                monthly_totals["sick_payment"] += sick_pay
                monthly_totals["effective_sick_minutes"] += int(sick_mins * sick_rate)
                monthly_totals["non_effective_sick_minutes"] += int(sick_mins * (1 - sick_rate))
                add_sick_payment_detail(sick_mins, sick_pay, sick_effective_rate)

    # חישוב סך שעות אפקטיביות (עבודה + חופשה + מחלה אפקטיבית, ללא כוננויות)
    raw_total_minutes = sum(
        day.get("total_minutes_no_standby", 0) or 0
        for day in daily_segments
    )
    monthly_totals["total_hours"] = raw_total_minutes - monthly_totals["non_effective_sick_minutes"]

    # ספירת כוננויות
    monthly_totals["standby"] = len(standby_days_set)

    # ימי עבודה בפועל (כולל חופשה ומחלה)
    monthly_totals["actual_work_days"] = len(work_days_set | vacation_days_set | sick_days_set)

    # ימי חופשה שנוצלו
    monthly_totals["vacation_days_taken"] = len(vacation_days_set)

    # תשלום חופשה - בדרך כלל מגיע מסיכום הרצפים; fallback לשכר מינימום אם אין פירוט תשלום
    if monthly_totals["vacation_minutes"] > 0 and monthly_totals["vacation_payment"] == 0:
        monthly_totals["vacation_payment"] = _mul_pay(round(monthly_totals["vacation_minutes"] / 60, 2), round(minimum_wage, 2))
        add_vacation_payment_detail(
            monthly_totals["vacation_minutes"],
            monthly_totals["vacation_payment"],
            minimum_wage,
        )
    monthly_totals["vacation"] = monthly_totals["vacation_minutes"]
    monthly_totals["vacation_payment_details"] = [
        {
            "hours": round(detail["hours"], 2),
            "rate": round(detail["rate"], 2),
            "payment": round(detail["payment"], 2),
        }
        for detail in sorted(vacation_payment_details_by_rate.values(), key=lambda item: item["rate"])
    ]

    # ימי מחלה שנוצלו (התשלום כבר חושב בלולאה עם האחוזים המדורגים)
    monthly_totals["sick_days_taken"] = len(sick_days_set)
    monthly_totals["sick_payment_details"] = [
        {
            "hours": round(detail["hours"], 2),
            "rate": round(detail["rate"], 2),
            "payment": round(detail["payment"], 2),
            "raw_hours": round(detail["raw_minutes"] / 60, 2),
        }
        for detail in sorted(sick_payment_details_by_rate.values(), key=lambda item: item["rate"])
    ]

    # שליפת נסיעות ותוספות - שימוש בנתונים שנטענו מראש אם קיימים
    if preloaded_payment_comps is not None:
        payment_comps = preloaded_payment_comps
    else:
        month_start = datetime(year, month, 1, tzinfo=LOCAL_TZ)
        if month == 12:
            month_end = datetime(year + 1, 1, 1, tzinfo=LOCAL_TZ)
        else:
            month_end = datetime(year, month + 1, 1, tzinfo=LOCAL_TZ)

        # סינון רכיבי תשלום לפי מערך דיור אם נדרש
        housing_filter = get_housing_array_filter()
        if housing_filter is not None:
            payment_comps = conn.execute("""
                SELECT (pc.quantity * pc.rate) as total_amount, pc.component_type_id,
                       COALESCE(pct.for_pension, FALSE) as for_pension
                FROM payment_components pc
                JOIN apartments ap ON ap.id = pc.apartment_id
                LEFT JOIN payment_component_types pct ON pc.component_type_id = pct.id
                WHERE pc.person_id = %s AND pc.date >= %s AND pc.date < %s
                  AND ap.housing_array_id = %s
            """, (person_id, month_start, month_end, housing_filter)).fetchall()
        else:
            payment_comps = conn.execute("""
                SELECT pc.quantity * pc.rate as total_amount, pc.component_type_id,
                       COALESCE(pct.for_pension, FALSE) as for_pension
                FROM payment_components pc
                LEFT JOIN payment_component_types pct ON pc.component_type_id = pct.id
                WHERE pc.person_id = %s AND pc.date >= %s AND pc.date < %s
            """, (person_id, month_start, month_end)).fetchall()

    for pc in payment_comps:
        amount = (pc["total_amount"] or 0) / 100
        component_type = pc["component_type_id"]
        if component_type == 2 or component_type == 7:
            monthly_totals["travel"] += amount
        elif component_type == 13:
            monthly_totals["professional_support"] += amount
        elif pc.get("for_pension"):
            monthly_totals["extras_for_pension"] += amount
        else:
            monthly_totals["extras"] += amount

    # שליפת פרטי העובד לחישוב צבירות - שימוש בנתונים שנטענו מראש אם קיימים
    if person_start_date is not None:
        start_date_ts = person_start_date
    else:
        person = conn.execute(
            "SELECT start_date FROM people WHERE id = %s", (person_id,)
        ).fetchone()
        start_date_ts = person["start_date"] if person else None

    # חישוב צבירות (מחלה וחופשה)
    if start_date_ts is not None:
        accruals = calculate_accruals(
            actual_work_days=monthly_totals["actual_work_days"],
            start_date_ts=start_date_ts,
            report_year=year,
            report_month=month
        )
        monthly_totals["sick_days_accrued"] = accruals.get("sick_days_accrued", 0)
        monthly_totals["vacation_days_accrued"] = accruals.get("vacation_days_accrued", 0)
        monthly_totals["vacation_details"] = accruals.get("vacation_details", {
            "seniority": 1,
            "annual_quota": 12,
            "job_scope_pct": 100
        })
    else:
        monthly_totals["vacation_details"] = {
            "seniority": 1,
            "annual_quota": 12,
            "job_scope_pct": 100
        }

    # תעריף בסיס ממוצע - ממוצע משוקלל של כל התעריפים בשעות רגילות
    # כולל תוספות סוג דירה ותוספת ותק ASD. זהו גם התעריף שגשר מייצא.
    if monthly_totals["regular_minutes_sum"] > 0:
        monthly_totals["average_base_rate"] = (
            monthly_totals["regular_rate_x_minutes_sum"] / monthly_totals["regular_minutes_sum"]
        )
    else:
        monthly_totals["average_base_rate"] = minimum_wage

    base_rate_for_work = round(monthly_totals["average_base_rate"], 2)
    for component_key, minutes_sum in monthly_totals["component_rate_minutes_sum"].items():
        if minutes_sum > 0:
            monthly_totals["component_base_rates"][component_key] = (
                monthly_totals["component_rate_x_minutes_sum"].get(component_key, 0.0) / minutes_sum
            )

    def component_base_rate(component_key: str) -> float:
        return round(monthly_totals["component_base_rates"].get(component_key, base_rate_for_work), 2)

    rate100 = component_base_rate("calc100")
    rate125_base = component_base_rate("calc125")
    rate150_overtime_base = component_base_rate("calc150_overtime")
    rate150_shabbat_100_base = component_base_rate("calc150_shabbat_100")
    rate150_shabbat_50_base = component_base_rate("calc150_shabbat_50")
    rate175_base = component_base_rate("calc175")
    rate200_base = component_base_rate("calc200")

    # תשלום סופי כולל - מחושב מהרכיבים המפורטים (לא מ-payment שכולל כפילויות)
    # payment_calc100/125/150/175/200 = עבודה בתעריף הבסיס הממוצע
    # variable_rates = עבודה בתעריפים מיוחדים
    # vacation_payment = ימי חופשה
    # sick_payment = ימי מחלה
    variable_rates_total = sum(
        data.get("payment", 0) for data in monthly_totals["variable_rates"].values()
    )
    monthly_totals["total_payment"] = (
        monthly_totals["payment_calc100"] +
        monthly_totals["payment_calc125"] +
        monthly_totals["payment_calc150"] +
        monthly_totals["payment_calc175"] +
        monthly_totals["payment_calc200"] +
        variable_rates_total +
        monthly_totals["vacation_payment"] +
        monthly_totals["sick_payment"] +
        monthly_totals["standby_payment"] +
        monthly_totals["travel"] +
        monthly_totals["professional_support"] +
        monthly_totals["holiday_payment"] +
        monthly_totals["extras"] +
        monthly_totals["extras_for_pension"]
    )

    # שמירת שכר אפקטיבי
    monthly_totals["effective_hourly_rate"] = minimum_wage

    # חישוב סה"כ תואם לתצוגה: כל רכיב מעוגל ל-1 ספרה לפני הסיכום
    # כך הסה"כ = סכום השורות המוצגות
    h100 = round(monthly_totals.get("calc100", 0) / 60, 2)
    h125 = round(monthly_totals.get("calc125", 0) / 60, 2)
    h150_overtime = round(monthly_totals.get("calc150_overtime", 0) / 60, 2)
    h150_shabbat_100 = round(monthly_totals.get("calc150_shabbat_100", 0) / 60, 2)
    h150_shabbat_50 = round(monthly_totals.get("calc150_shabbat_50", 0) / 60, 2)
    h175 = round(monthly_totals.get("calc175", 0) / 60, 2)
    h200 = round(monthly_totals.get("calc200", 0) / 60, 2)

    # חישוב מחדש מסה"כ דקות (שיטת מירב - מחשב מהשעות הסופיות, לא מצבירת רצפים)
    monthly_totals["payment_calc100"] = _mul_pay(h100, round(rate100 * 1.0, 2))
    monthly_totals["payment_calc125"] = _mul_pay(h125, round(rate125_base * 1.25, 2))
    monthly_totals["payment_calc150_overtime"] = _mul_pay(h150_overtime, round(rate150_overtime_base * 1.5, 2))
    monthly_totals["payment_calc150_shabbat"] = (
        _mul_pay(h150_shabbat_100, round(rate150_shabbat_100_base * 1.0, 2)) +
        _mul_pay(h150_shabbat_50, round(rate150_shabbat_50_base * 0.5, 2))
    )
    monthly_totals["payment_calc150"] = monthly_totals["payment_calc150_overtime"] + monthly_totals["payment_calc150_shabbat"]
    monthly_totals["payment_calc175"] = _mul_pay(h175, round(rate175_base * 1.75, 2))
    monthly_totals["payment_calc200"] = _mul_pay(h200, round(rate200_base * 2.0, 2))

    # כל רכיב מעוגל בשיטת מירב (Decimal, round half up)
    gesher_total = (
        _mul_pay(h100, round(rate100 * 1.0, 2)) +
        _mul_pay(h125, round(rate125_base * 1.25, 2)) +
        _mul_pay(h150_overtime, round(rate150_overtime_base * 1.5, 2)) +
        _mul_pay(h150_shabbat_100, round(rate150_shabbat_100_base * 1.0, 2)) +
        _mul_pay(h150_shabbat_50, round(rate150_shabbat_50_base * 0.5, 2)) +
        _mul_pay(h175, round(rate175_base * 1.75, 2)) +
        _mul_pay(h200, round(rate200_base * 2.0, 2)) +
        _round_pay(monthly_totals.get("payment_calc_variable", 0) or 0) +
        _round_pay(monthly_totals.get("standby_payment", 0) or 0) +
        _round_pay(monthly_totals.get("vacation_payment", 0) or 0) +
        _round_pay(monthly_totals.get("sick_payment", 0) or 0) +
        _round_pay(monthly_totals.get("travel", 0) or 0) +
        _round_pay(monthly_totals.get("professional_support", 0) or 0) +
        _round_pay(monthly_totals.get("holiday_payment", 0) or 0) +
        _round_pay(monthly_totals.get("extras", 0) or 0) +
        _round_pay(monthly_totals.get("extras_for_pension", 0) or 0)
    )

    monthly_totals["gesher_total"] = round(gesher_total, 2)
    # display_total = סכום השורות המעוגלות (ללא round(2) בסוף) - מתאים לחישוב ידני
    monthly_totals["display_total"] = round(gesher_total, 1)
    # rounded_total = gesher_total לעקביות
    monthly_totals["rounded_total"] = monthly_totals["gesher_total"]
    # total_payment = rounded_total לעקביות בכל המערכת
    monthly_totals["total_payment"] = monthly_totals["rounded_total"]

    return monthly_totals

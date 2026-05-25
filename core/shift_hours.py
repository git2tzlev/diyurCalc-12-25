"""
חישוב שעות עבודה/כוננות למשמרת - מקור אמת יחיד.

משמש את דוח משמרות (web + PDF) ואת כל מי שצריך לפרק משמרת לשעות.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Tuple

from core.constants import (
    LOW_FUNCTIONING_APT_TYPE,
    NIGHT_SHIFT_ID,
    NIGHT_SHIFT_WORK_FIRST_MINUTES,
    NIGHT_SHIFT_STANDBY_END,
    TAGBUR_FRIDAY_PRE_ENTRY_MINUTES,
    TAGBUR_FRIDAY_SHIFT_ID,
    TAGBUR_SHABBAT_POST_EXIT_MINUTES,
    TAGBUR_SHABBAT_SHIFT_ID,
    is_asd_housing_array,
    is_tagbur_shift,
)
from core.time_utils import MINUTES_PER_DAY, MINUTES_PER_HOUR, _get_shabbat_boundaries, span_minutes


def _format_minutes_as_hhmm(minutes: int) -> str:
    return f"{(minutes // MINUTES_PER_HOUR) % 24:02d}:{minutes % MINUTES_PER_HOUR:02d}"


def apply_tagbur_dynamic_boundaries(
    shift_type_id: int,
    seg_list: list[dict],
    report_date: date,
    report_start_min: int,
    report_end_min: int,
    year: int,
    month: int,
    shabbat_cache: Dict[str, Dict[str, str]],
) -> list[dict]:
    """
    התאמת גבולות תגבור לפי שבת/חג.

    מ-04/2026 לא מאריכים תגבור מעבר לדיווח בפועל בצד הדינמי.
    """
    if not is_tagbur_shift(shift_type_id) or not seg_list:
        return seg_list

    adjusted = [dict(seg) for seg in seg_list]
    shabbat_enter, shabbat_exit = _get_shabbat_boundaries(report_date, shabbat_cache)
    clip_to_report = (year, month) >= (2026, 4)

    if shift_type_id == TAGBUR_FRIDAY_SHIFT_ID and shabbat_enter > 0:
        first_seg = adjusted[0]
        _first_seg_start, first_seg_end = span_minutes(first_seg["start_time"], first_seg["end_time"])
        dynamic_start = shabbat_enter - TAGBUR_FRIDAY_PRE_ENTRY_MINUTES
        new_first_start = max(dynamic_start, report_start_min) if clip_to_report else dynamic_start

        if len(adjusted) > 1:
            second_seg_start, _ = span_minutes(adjusted[1]["start_time"], adjusted[1]["end_time"])
            new_first_end = second_seg_start
        else:
            new_first_end = first_seg_end

        adjusted[0] = {
            **first_seg,
            "start_time": _format_minutes_as_hhmm(new_first_start),
            "end_time": _format_minutes_as_hhmm(new_first_end),
        }

    elif shift_type_id == TAGBUR_SHABBAT_SHIFT_ID and shabbat_exit > 0:
        last_seg = adjusted[-1]
        dynamic_end = (shabbat_exit % MINUTES_PER_DAY) + TAGBUR_SHABBAT_POST_EXIT_MINUTES
        new_last_end = min(dynamic_end, report_end_min) if clip_to_report else dynamic_end

        adjusted[-1] = {
            **last_seg,
            "start_time": last_seg["start_time"],
            "end_time": _format_minutes_as_hhmm(new_last_end),
        }

    return adjusted


def calculate_segment_hours(
    start_time: str,
    end_time: str,
    shift_type_id: int,
    segments_by_shift: Dict[int, List[dict]],
) -> Tuple[float, float]:
    """
    חישוב שעות עבודה וכוננות לפי סגמנטים מטבלת shift_time_segments.

    Returns:
        (work_hours, standby_hours)
    """
    actual_start, actual_end = span_minutes(start_time, end_time)
    total_minutes = actual_end - actual_start

    segment_list = segments_by_shift.get(shift_type_id, [])
    if not segment_list:
        return total_minutes / 60, 0.0

    work_minutes = 0
    standby_minutes = 0
    covered_minutes = 0

    for seg in segment_list:
        seg_start, seg_end = span_minutes(seg["start_time"], seg["end_time"])
        overlap_start = max(seg_start, actual_start)
        overlap_end = min(seg_end, actual_end)

        if overlap_end > overlap_start:
            minutes = overlap_end - overlap_start
            covered_minutes += minutes
            if seg.get("segment_type") == "standby":
                standby_minutes += minutes
            else:
                work_minutes += minutes

    uncovered_minutes = total_minutes - covered_minutes
    if uncovered_minutes > 0:
        work_minutes += uncovered_minutes

    return work_minutes / 60, standby_minutes / 60


def calculate_night_shift_hours(
    start_time: str, end_time: str,
) -> Tuple[float, float]:
    """
    חישוב שעות עבודה/כוננות למשמרת לילה (107) - אלגוריתם קבוע.

    2 שעות עבודה ראשונות → כוננות עד 06:30 → עבודה אחרי 06:30.
    """
    actual_start, actual_end = span_minutes(start_time, end_time)

    # שלב 1: עבודה ראשונה
    first_work_minutes = min(NIGHT_SHIFT_WORK_FIRST_MINUTES, actual_end - actual_start)
    work_end_first = actual_start + NIGHT_SHIFT_WORK_FIRST_MINUTES

    # שלב 2: כוננות עד 06:30
    if actual_end < actual_start:
        actual_end_adjusted = actual_end + 24 * 60
    else:
        actual_end_adjusted = actual_end

    if actual_start >= 12 * 60:
        standby_end_target = NIGHT_SHIFT_STANDBY_END + 24 * 60
    else:
        standby_end_target = NIGHT_SHIFT_STANDBY_END

    standby_end = min(standby_end_target, actual_end_adjusted)
    standby_minutes = max(0, standby_end - work_end_first)

    # שלב 3: עבודה בוקר
    morning_work_minutes = max(0, actual_end_adjusted - standby_end_target)

    work_hours = round((first_work_minutes + morning_work_minutes) / 60, 2)
    standby_hours = round(standby_minutes / 60, 2)
    return work_hours, standby_hours


def calculate_shift_hours(
    start_time: str,
    end_time: str,
    shift_type_id: int,
    segments_by_shift: Dict[int, List[dict]],
    apartment_type_id: int | None = None,
    housing_array_id: int | None = None,
) -> Tuple[float, float]:
    """
    חישוב שעות עבודה/כוננות למשמרת - פונקציה מרכזית.

    מרכזת את כל הלוגיקה:
    - משמרת לילה (107) מחוץ למערך ASD → אלגוריתם דינמי
    - משמרת לילה במערך ASD → לפי סגמנטים מהטבלה
    - ערות בלילה (LOW_FUNCTIONING) → כוננות נספרת כעבודה
    - שאר המשמרות → לפי סגמנטים

    Returns:
        (work_hours, standby_hours)
    """
    is_asd = is_asd_housing_array(housing_array_id)
    is_night = shift_type_id == NIGHT_SHIFT_ID

    # משמרת לילה מחוץ למערך ASD → אלגוריתם דינמי
    if is_night and not is_asd:
        work_hours, standby_hours = calculate_night_shift_hours(start_time, end_time)
    else:
        work_hours, standby_hours = calculate_segment_hours(
            start_time, end_time, shift_type_id, segments_by_shift
        )

    # ערות בלילה: כל הכוננות נספרת כעבודה
    if apartment_type_id == LOW_FUNCTIONING_APT_TYPE and standby_hours > 0:
        work_hours += standby_hours
        standby_hours = 0.0

    return round(work_hours, 2), round(standby_hours, 2)


def calculate_tagbur_segments(
    start_time: str,
    end_time: str,
    shift_type_id: int,
    segments_by_shift: Dict[int, List[dict]],
    report_date: date | None = None,
    year: int | None = None,
    month: int | None = None,
    shabbat_cache: Dict[str, Dict[str, str]] | None = None,
) -> List[dict]:
    """
    פירוק משמרת תגבור למקטעים לתצוגה (שורה לכל סגמנט).

    Returns:
        רשימת מקטעים: [{"display_start", "display_end", "work_hours", "standby_hours"}]
    """
    actual_start, actual_end = span_minutes(start_time, end_time)
    effective_actual_end = actual_end if actual_end > actual_start else actual_end + MINUTES_PER_DAY
    segment_list = segments_by_shift.get(shift_type_id, [])
    has_dynamic_context = (
        report_date is not None
        and year is not None
        and month is not None
        and shabbat_cache is not None
    )
    if has_dynamic_context:
        segment_list = apply_tagbur_dynamic_boundaries(
            shift_type_id,
            segment_list,
            report_date,
            actual_start,
            effective_actual_end,
            year,
            month,
            shabbat_cache,
        )

    # איסוף מקטעים חופפים
    overlapping = []
    for seg in segment_list:
        seg_start, seg_end = span_minutes(seg["start_time"], seg["end_time"])
        overlap_start = max(seg_start, actual_start)
        overlap_end = min(seg_end, actual_end)

        if has_dynamic_context and shift_type_id == TAGBUR_FRIDAY_SHIFT_ID and seg is segment_list[0]:
            overlap_start = min(seg_start, actual_start)
        elif has_dynamic_context and shift_type_id == TAGBUR_SHABBAT_SHIFT_ID and seg is segment_list[-1]:
            overlap_end = max(seg_end, actual_end)

        if overlap_end > overlap_start:
            overlapping.append({
                "overlap_start": overlap_start,
                "overlap_end": overlap_end,
                "segment_type": seg.get("segment_type", "work"),
            })

    result = []
    for i, seg_data in enumerate(overlapping):
        is_first = (i == 0)
        is_last = (i == len(overlapping) - 1)
        calc_start = seg_data["overlap_start"]
        calc_end = seg_data["overlap_end"]
        segment_type = seg_data["segment_type"]

        if not has_dynamic_context:
            if is_first and shift_type_id == TAGBUR_FRIDAY_SHIFT_ID:
                calc_start = actual_start
            if is_last and shift_type_id == TAGBUR_SHABBAT_SHIFT_ID:
                calc_end = actual_end

        display_start = f"{(calc_start // 60) % 24:02d}:{calc_start % 60:02d}"
        display_end = f"{(calc_end // 60) % 24:02d}:{calc_end % 60:02d}"

        segment_minutes = calc_end - calc_start
        segment_hours = round(segment_minutes / 60, 2)

        if segment_type == "standby":
            work_hours, standby_hours = 0.0, segment_hours
        else:
            work_hours, standby_hours = segment_hours, 0.0

        result.append({
            "display_start": display_start,
            "display_end": display_end,
            "work_hours": work_hours,
            "standby_hours": standby_hours,
            "is_first": is_first,
            "is_last": is_last,
        })

    return result

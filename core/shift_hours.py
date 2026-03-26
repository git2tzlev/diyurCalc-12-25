"""
חישוב שעות עבודה/כוננות למשמרת - מקור אמת יחיד.

משמש את דוח משמרות (web + PDF) ואת כל מי שצריך לפרק משמרת לשעות.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from core.constants import (
    HIGH_FUNCTIONING_APT_TYPE,
    LOW_FUNCTIONING_APT_TYPE,
    NIGHT_SHIFT_ID,
    NIGHT_SHIFT_WORK_FIRST_MINUTES,
    NIGHT_SHIFT_STANDBY_END,
    TAGBUR_FRIDAY_SHIFT_ID,
    TAGBUR_SHABBAT_SHIFT_ID,
)
from core.time_utils import span_minutes


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
) -> Tuple[float, float]:
    """
    חישוב שעות עבודה/כוננות למשמרת - פונקציה מרכזית.

    מרכזת את כל הלוגיקה:
    - משמרת לילה (107) לא-ASD → אלגוריתם דינמי
    - משמרת לילה ASD → לפי סגמנטים מהטבלה
    - ערות בלילה (LOW_FUNCTIONING) → כוננות נספרת כעבודה
    - שאר המשמרות → לפי סגמנטים

    Returns:
        (work_hours, standby_hours)
    """
    is_asd = apartment_type_id in (HIGH_FUNCTIONING_APT_TYPE, LOW_FUNCTIONING_APT_TYPE)
    is_night = shift_type_id == NIGHT_SHIFT_ID

    # משמרת לילה לא-ASD → אלגוריתם דינמי
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
) -> List[dict]:
    """
    פירוק משמרת תגבור למקטעים לתצוגה (שורה לכל סגמנט).

    Returns:
        רשימת מקטעים: [{"display_start", "display_end", "work_hours", "standby_hours"}]
    """
    actual_start, actual_end = span_minutes(start_time, end_time)
    segment_list = segments_by_shift.get(shift_type_id, [])

    # איסוף מקטעים חופפים
    overlapping = []
    for seg in segment_list:
        seg_start, seg_end = span_minutes(seg["start_time"], seg["end_time"])
        overlap_start = max(seg_start, actual_start)
        overlap_end = min(seg_end, actual_end)

        if overlap_end > overlap_start:
            overlapping.append({
                "overlap_start": overlap_start,
                "overlap_end": overlap_end,
                "segment_type": seg.get("segment_type", "work"),
            })

    is_friday_tagbor = (shift_type_id == TAGBUR_FRIDAY_SHIFT_ID)
    is_shabbat_tagbor = (shift_type_id == TAGBUR_SHABBAT_SHIFT_ID)

    result = []
    for i, seg_data in enumerate(overlapping):
        is_first = (i == 0)
        is_last = (i == len(overlapping) - 1)
        overlap_start = seg_data["overlap_start"]
        overlap_end = seg_data["overlap_end"]
        segment_type = seg_data["segment_type"]

        # תגבור שישי: מקטע ראשון - התחלה מהדיווח
        if is_first and is_friday_tagbor:
            calc_start = actual_start
        else:
            calc_start = overlap_start

        # תגבור שבת: מקטע אחרון - סיום מהדיווח
        if is_last and is_shabbat_tagbor:
            calc_end = actual_end
        else:
            calc_end = overlap_end

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

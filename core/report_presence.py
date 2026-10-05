"""Shared queries for detecting guides that have reportable monthly data."""
from __future__ import annotations

from datetime import date
from datetime import time, timedelta
from typing import Optional

from core.constants import (
    ASD_COMPLETION_EXCLUSION_MONTH,
    ASD_COMPLETION_EXCLUSION_YEAR,
    ASD_HOUSING_ARRAY_ID,
    COMPLETION_APARTMENT_IDS,
    NIGHT_SHIFT_MORNING_END,
)
from core.time_utils import MINUTES_PER_DAY


def _is_asd_completion_exclusion_period(
    start_date: date,
    end_date: date,
    housing_array_id: Optional[int],
) -> bool:
    return (
        housing_array_id == ASD_HOUSING_ARRAY_ID
        and start_date == date(ASD_COMPLETION_EXCLUSION_YEAR, ASD_COMPLETION_EXCLUSION_MONTH, 1)
        and end_date == date(ASD_COMPLETION_EXCLUSION_YEAR, ASD_COMPLETION_EXCLUSION_MONTH + 1, 1)
    )


def _is_completion_exclusion_period(start_date: date, end_date: date) -> bool:
    return (
        start_date == date(ASD_COMPLETION_EXCLUSION_YEAR, ASD_COMPLETION_EXCLUSION_MONTH, 1)
        and end_date == date(ASD_COMPLETION_EXCLUSION_YEAR, ASD_COMPLETION_EXCLUSION_MONTH + 1, 1)
    )


def get_report_presence_counts(
    conn,
    start_date: date,
    end_date: date,
    housing_array_id: Optional[int] = None,
) -> tuple[dict[int, int], set[int]]:
    """Return shift counts and payment-component-only guide ids for a period."""
    counts: dict[int, int] = {}
    has_payment_components: set[int] = set()

    if housing_array_id is not None:
        exclude_completion = _is_asd_completion_exclusion_period(
            start_date, end_date, housing_array_id
        )
        completion_filter_sql = "AND tr.apartment_id <> ALL(%s)" if exclude_completion else ""
        completion_params = (list(COMPLETION_APARTMENT_IDS),) if exclude_completion else ()
        for row in conn.execute(
            f"""
            SELECT tr.person_id, COUNT(*) AS cnt
            FROM time_reports tr
            JOIN apartments ap ON ap.id = tr.apartment_id
            WHERE tr.date >= %s AND tr.date < %s
              AND ap.housing_array_id = %s
              {completion_filter_sql}
            GROUP BY tr.person_id
            """,
            (start_date, end_date, housing_array_id, *completion_params),
        ):
            counts[row["person_id"]] = row["cnt"]

        for row in conn.execute(
            """
            SELECT DISTINCT pc.person_id
            FROM payment_components pc
            JOIN apartments ap ON ap.id = pc.apartment_id
            WHERE pc.date >= %s AND pc.date < %s
              AND ap.housing_array_id = %s
            """,
            (start_date, end_date, housing_array_id),
        ):
            has_payment_components.add(row["person_id"])
    else:
        if _is_completion_exclusion_period(start_date, end_date):
            count_sql = """
                SELECT tr.person_id, COUNT(*) AS cnt
                FROM time_reports tr
                LEFT JOIN apartments ap ON ap.id = tr.apartment_id
                WHERE tr.date >= %s AND tr.date < %s
                  AND NOT (
                    COALESCE(ap.housing_array_id = %s, FALSE)
                    AND tr.apartment_id = ANY(%s)
                  )
                GROUP BY tr.person_id
            """
            count_params = (
                start_date, end_date, ASD_HOUSING_ARRAY_ID, list(COMPLETION_APARTMENT_IDS)
            )
        else:
            count_sql = """
                SELECT person_id, COUNT(*) AS cnt
                FROM time_reports
                WHERE date >= %s AND date < %s
                GROUP BY person_id
            """
            count_params = (start_date, end_date)
        for row in conn.execute(count_sql, count_params):
            counts[row["person_id"]] = row["cnt"]

        for row in conn.execute(
            """
            SELECT DISTINCT person_id
            FROM payment_components
            WHERE date >= %s AND date < %s
            """,
            (start_date, end_date),
        ):
            has_payment_components.add(row["person_id"])

    return counts, has_payment_components


def get_payment_period_presence_counts(
    conn,
    payment_year: int,
    payment_month: int,
    housing_array_id: Optional[int] = None,
) -> dict[int, int]:
    """Return guides with completion records that are eligible for this payment month."""
    event_params: list = [
        payment_year,
        payment_month,
        ["included_in_export", "exported"],
        ["time_reports", "payment_components"],
    ]
    report_params: list = [
        payment_year,
        payment_month,
        payment_year,
        payment_month,
    ]
    component_params: list = [
        payment_year,
        payment_month,
        payment_year,
        payment_month,
    ]
    event_housing_sql = ""
    report_housing_sql = ""
    component_housing_sql = ""
    if housing_array_id is not None:
        event_housing_sql = "AND sie.housing_array_id = %s"
        report_housing_sql = "AND ap.housing_array_id = %s"
        component_housing_sql = "AND ap.housing_array_id = %s"
        event_params.append(housing_array_id)
        report_params.append(housing_array_id)
        component_params.append(housing_array_id)

    rows = conn.execute(
        f"""
        SELECT person_id, COUNT(*) AS cnt
        FROM (
            SELECT sie.person_id, sie.source_table, sie.source_id
            FROM salary_impact_events sie
            WHERE sie.payment_year = %s AND sie.payment_month = %s
              AND sie.status = ANY(%s)
              AND sie.source_table = ANY(%s)
              {event_housing_sql}

            UNION ALL

            SELECT tr.person_id, 'time_reports', tr.id
            FROM time_reports tr
            JOIN apartments ap ON ap.id = tr.apartment_id
            WHERE tr.payment_year = %s AND tr.payment_month = %s
              AND (EXTRACT(YEAR FROM tr.date)::int * 100 + EXTRACT(MONTH FROM tr.date)::int)
                  < (%s * 100 + %s)
              AND NOT EXISTS (
                  SELECT 1 FROM salary_impact_events sie
                  WHERE sie.source_table = 'time_reports' AND sie.source_id = tr.id
                    AND sie.payment_year = tr.payment_year
                    AND sie.payment_month = tr.payment_month
              )
              {report_housing_sql}

            UNION ALL

            SELECT pc.person_id, 'payment_components', pc.id
            FROM payment_components pc
            JOIN apartments ap ON ap.id = pc.apartment_id
            WHERE pc.payment_year = %s AND pc.payment_month = %s
              AND (EXTRACT(YEAR FROM pc.date)::int * 100 + EXTRACT(MONTH FROM pc.date)::int)
                  < (%s * 100 + %s)
              AND NOT EXISTS (
                  SELECT 1 FROM salary_impact_events sie
                  WHERE sie.source_table = 'payment_components' AND sie.source_id = pc.id
                    AND sie.payment_year = pc.payment_year
                    AND sie.payment_month = pc.payment_month
              )
              {component_housing_sql}
        ) payment_items
        GROUP BY person_id
        """,
        tuple(event_params + report_params + component_params),
    )
    return {row["person_id"]: row["cnt"] for row in rows}


def _parse_report_time(value) -> time | None:
    if value is None:
        return None
    if isinstance(value, time):
        return value
    try:
        hours, minutes = str(value).split(":", 1)
        return time(int(hours), int(minutes[:2]))
    except (TypeError, ValueError):
        return None


def _split_onto_workday(
    report_date: date,
    report_start_min: int,
    part_date: date,
    part_start: int,
    part_end: int,
) -> list[tuple[date, int, int]]:
    """משייך מקטע ליום עבודה 08:00-08:00, כמו בדוח הרצפים."""
    pieces = (
        [(part_start, NIGHT_SHIFT_MORNING_END), (NIGHT_SHIFT_MORNING_END, part_end)]
        if part_start < NIGHT_SHIFT_MORNING_END < part_end
        else [(part_start, part_end)]
    )
    is_standalone = part_date == report_date and report_start_min < NIGHT_SHIFT_MORNING_END
    intervals: list[tuple[date, int, int]] = []
    for start_min, end_min in pieces:
        if end_min <= NIGHT_SHIFT_MORNING_END and not is_standalone:
            intervals.append((
                part_date - timedelta(days=1),
                start_min + MINUTES_PER_DAY,
                end_min + MINUTES_PER_DAY,
            ))
        else:
            intervals.append((part_date, start_min, end_min))
    return intervals


def _workday_intervals(
    report_date: date,
    report_start_min: int,
    work_start: int,
    work_end: int,
) -> list[tuple[date, int, int]]:
    """מפצל טווח עבודה לימי עבודה. שעת סיום יכולה לעבור חצות."""
    intervals: list[tuple[date, int, int]] = []
    cursor = work_start
    while cursor < work_end:
        day_offset = cursor // MINUTES_PER_DAY
        day_origin = day_offset * MINUTES_PER_DAY
        part_end = min(work_end, day_origin + MINUTES_PER_DAY)
        intervals.extend(_split_onto_workday(
            report_date,
            report_start_min,
            report_date + timedelta(days=day_offset),
            cursor - day_origin,
            part_end - day_origin,
        ))
        cursor = part_end
    return intervals


def _merge_same_report_intervals(
    intervals: list[tuple[int, int, int]],
) -> list[tuple[int, int, int]]:
    """מאחד מקטעים צמודים של אותו דיווח לטווח אחד."""
    merged: list[tuple[int, int, int]] = []
    for report_id, start_min, end_min in sorted(intervals, key=lambda item: (item[0], item[1])):
        if merged and merged[-1][0] == report_id and start_min <= merged[-1][2]:
            merged[-1] = (report_id, merged[-1][1], max(merged[-1][2], end_min))
            continue
        merged.append((report_id, start_min, end_min))
    return merged


def _count_overlapping_pairs(intervals: list[tuple[int, int, int]]) -> int:
    """סופר זוגות דיווחים שונים ששעות העבודה שלהם נחתכות."""
    ordered = sorted(_merge_same_report_intervals(intervals), key=lambda item: item[1])
    overlap_count = 0
    for idx, (left_id, left_start, left_end) in enumerate(ordered):
        for right_id, right_start, right_end in ordered[idx + 1:]:
            if right_start >= left_end:
                break
            if left_id != right_id and left_start < right_end and right_start < left_end:
                overlap_count += 1
    return overlap_count


def get_report_overlap_counts(
    conn,
    start_date: date,
    end_date: date,
    housing_array_id: Optional[int] = None,
) -> dict[int, int]:
    """מחזיר כמה זוגות דיווחי עבודה חופפים יש לכל מדריך בתקופה.

    החפיפה נספרת כמו בדוח הרצפים: רק מקטעי עבודה, ורק בתוך אותו יום עבודה
    מ-08:00 עד 08:00. דיווח בוקר שמתחיל אחרי חצות נשאר ביומו ולא נחשב המשך
    של משמרת שנכנסה ביום הקודם.
    """
    params: list = [start_date, end_date]
    housing_join = ""
    housing_filter = ""
    if housing_array_id is not None:
        housing_join = "JOIN apartments ap ON ap.id = tr.apartment_id"
        housing_filter = "AND ap.housing_array_id = %s"
        params.append(housing_array_id)

    cursor_or_rows = conn.execute(
        f"""
        SELECT tr.id, tr.person_id, tr.date, tr.start_time, tr.end_time, tr.shift_type_id
        FROM time_reports tr
        {housing_join}
        WHERE tr.date >= %s AND tr.date < %s
          AND tr.start_time IS NOT NULL
          AND tr.end_time IS NOT NULL
          {housing_filter}
        ORDER BY tr.person_id, tr.date, tr.start_time
        """,
        tuple(params),
    )
    rows = (
        cursor_or_rows.fetchall()
        if hasattr(cursor_or_rows, "fetchall")
        else cursor_or_rows
    )

    shift_ids = sorted({row["shift_type_id"] for row in rows if row.get("shift_type_id")})
    segments_by_shift: dict[int, list[dict]] = {}
    if shift_ids:
        placeholders = ",".join(["%s"] * len(shift_ids))
        segment_rows = conn.execute(
            f"""
            SELECT shift_type_id, start_time, end_time, segment_type
            FROM shift_time_segments
            WHERE shift_type_id IN ({placeholders})
            ORDER BY shift_type_id, order_index, id
            """,
            tuple(shift_ids),
        )
        segment_rows = (
            segment_rows.fetchall()
            if hasattr(segment_rows, "fetchall")
            else segment_rows
        )
        for row in segment_rows:
            segments_by_shift.setdefault(row["shift_type_id"], []).append(row)

    by_person_day = _collect_workday_intervals(rows, segments_by_shift, start_date, end_date)
    counts: dict[int, int] = {}
    for (person_id, _workday), intervals in by_person_day.items():
        overlap_count = _count_overlapping_pairs(intervals)
        if overlap_count:
            counts[person_id] = counts.get(person_id, 0) + overlap_count

    return counts


def _collect_workday_intervals(
    rows,
    segments_by_shift: dict[int, list[dict]],
    start_date: date,
    end_date: date,
) -> dict[tuple[int, date], list[tuple[int, int, int]]]:
    """מקבץ מקטעי עבודה לפי מדריך ויום עבודה."""
    by_person_day: dict[tuple[int, date], list[tuple[int, int, int]]] = {}
    for row in rows:
        report_date = row["date"]
        if hasattr(report_date, "date"):
            report_date = report_date.date()
        start_t = _parse_report_time(row["start_time"])
        end_t = _parse_report_time(row["end_time"])
        if not start_t or not end_t:
            continue

        report_start_min = start_t.hour * 60 + start_t.minute
        report_end_min = end_t.hour * 60 + end_t.minute
        if report_end_min <= report_start_min:
            report_end_min += MINUTES_PER_DAY

        work_parts = _report_work_parts(
            report_start_min,
            report_end_min,
            segments_by_shift.get(row.get("shift_type_id"), []),
        )
        for work_start, work_end in work_parts:
            for workday, start_min, end_min in _workday_intervals(
                report_date, report_start_min, work_start, work_end,
            ):
                if start_date <= workday < end_date:
                    by_person_day.setdefault((row["person_id"], workday), []).append(
                        (row["id"], start_min, end_min)
                    )
    return by_person_day


def _report_work_parts(
    report_start_min: int,
    report_end_min: int,
    shift_segments: list[dict],
) -> list[tuple[int, int]]:
    """מחזיר את מקטעי העבודה של דיווח. בלי סגמנטים, כל הדיווח הוא עבודה."""
    work_parts: list[tuple[int, int]] = []
    for segment in shift_segments:
        if segment.get("segment_type") != "work":
            continue
        seg_start_t = _parse_report_time(segment.get("start_time"))
        seg_end_t = _parse_report_time(segment.get("end_time"))
        if not seg_start_t or not seg_end_t:
            continue
        seg_start = seg_start_t.hour * 60 + seg_start_t.minute
        seg_end = seg_end_t.hour * 60 + seg_end_t.minute
        if seg_end <= seg_start:
            seg_end += MINUTES_PER_DAY
        overlap_start = max(report_start_min, seg_start)
        overlap_end = min(report_end_min, seg_end)
        if overlap_end > overlap_start:
            work_parts.append((overlap_start, overlap_end))
    if not shift_segments:
        work_parts.append((report_start_min, report_end_min))
    return work_parts

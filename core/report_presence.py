"""Shared queries for detecting guides that have reportable monthly data."""
from __future__ import annotations

from datetime import date
from datetime import datetime, time, timedelta
from typing import Optional

from core.constants import (
    ASD_COMPLETION_EXCLUSION_MONTH,
    ASD_COMPLETION_EXCLUSION_YEAR,
    ASD_HOUSING_ARRAY_ID,
    COMPLETION_APARTMENT_IDS,
)


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


def get_report_overlap_counts(
    conn,
    start_date: date,
    end_date: date,
    housing_array_id: Optional[int] = None,
) -> dict[int, int]:
    """Return number of overlapping time-report pairs per guide for a period."""
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

    by_person: dict[int, list[tuple[int, datetime, datetime]]] = {}
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
            report_end_min += 24 * 60

        shift_segments = segments_by_shift.get(row.get("shift_type_id"), [])
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
                seg_end += 24 * 60
            overlap_start = max(report_start_min, seg_start)
            overlap_end = min(report_end_min, seg_end)
            if overlap_end > overlap_start:
                work_parts.append((overlap_start, overlap_end))

        if not shift_segments:
            work_parts.append((report_start_min, report_end_min))

        for work_start, work_end in work_parts:
            start_dt = datetime.combine(report_date, time()) + timedelta(minutes=work_start)
            end_dt = datetime.combine(report_date, time()) + timedelta(minutes=work_end)
            by_person.setdefault(row["person_id"], []).append((row["id"], start_dt, end_dt))

    counts: dict[int, int] = {}
    for person_id, intervals in by_person.items():
        intervals.sort(key=lambda item: item[1])
        overlap_count = 0
        for idx, (left_id, left_start, left_end) in enumerate(intervals):
            for right_id, right_start, right_end in intervals[idx + 1:]:
                if right_start >= left_end:
                    break
                if left_start < right_end and right_start < left_end:
                    overlap_count += 1
        if overlap_count:
            counts[person_id] = overlap_count

    return counts

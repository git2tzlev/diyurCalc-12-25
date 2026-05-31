"""
Guide routes for DiyurCalc application.
Contains routes for viewing guide details and summaries.
"""
from __future__ import annotations

import calendar
import html
import time
import logging
from datetime import datetime, date, timedelta
from typing import Optional, Dict, List

from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from core.config import config
from core.database import (
    get_conn,
    get_default_period,
    get_housing_array_filter,
    get_multi_housing_guides,
    set_housing_array_filter,
)
from core.time_utils import get_shabbat_times_cache
from core.logic import (
    get_payment_codes,
    get_available_months_for_person,
    auto_approve_substitute_travel,
)
from core.history import get_minimum_wage_for_month
from app_utils import get_daily_segments_data, aggregate_daily_segments_to_monthly
from core.constants import (
    PERMANENT_EMPLOYEE_TYPE,
    HIGH_FUNCTIONING_APT_TYPE, LOW_FUNCTIONING_APT_TYPE,
    CLEANING_SHIFT_ID, MEDICAL_ESCORT_SHIFT_ID, NIGHT_WATCH_SHIFT_ID, WORK_HOUR_SHIFT_ID,
    COMPLETION_APARTMENT_IDS,
    is_asd_housing_array,
    should_exclude_asd_completion_report,
)
from core.holiday_payment import (
    calculate_holiday_payments,
    get_holiday_payment_setup,
    get_holiday_payment_dates_in_month,
    save_holiday_payment_setup,
    get_holiday_dates_in_month,
    _has_sufficient_seniority,
    _get_special_holiday_payment_window_details,
    _report_overlaps_special_holiday_window,
)
from core.auth import enforce_housing_filter_guide_access
from services.pdf_renderer import render_html_to_pdf_bytes
from utils.utils import month_range_ts, format_currency, format_currency_total, human_date

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
templates.env.filters["format_currency"] = format_currency
templates.env.filters["format_currency_total"] = format_currency_total
templates.env.filters["human_date"] = human_date
templates.env.globals["app_version"] = config.VERSION

COMPLETION_APARTMENT_NAME = "השלמות"
GENERIC_ERROR = "שגיאת מערכת. נסי שוב מאוחר יותר"
DISPLAY_PRIORITY_SHIFT_IDS = {
    WORK_HOUR_SHIFT_ID,
    CLEANING_SHIFT_ID,
    MEDICAL_ESCORT_SHIFT_ID,
    NIGHT_WATCH_SHIFT_ID,
}


def _format_shifts_email_text(template: str, person_name: str, year: int, month: int) -> str:
    """Apply the small set of placeholders allowed in shift report emails."""
    return (
        (template or "")
        .replace("{name}", person_name)
        .replace("{month}", f"{month:02d}")
        .replace("{year}", str(year))
    )


def _sanitize_email_subject(subject: str) -> str:
    return " ".join((subject or "").split())


def _build_shifts_email_body(person_name: str, year: int, month: int, extra_message: str = "") -> str:
    formatted_extra = _format_shifts_email_text(extra_message, person_name, year, month).strip()
    safe_extra = html.escape(formatted_extra).replace("\n", "<br>")
    extra_block = f"\n{safe_extra}\n" if safe_extra else ""
    return f"""שלום {html.escape(person_name)},

מצורף דוח המשמרות שלך לחודש {month:02d}/{year}.
{extra_block}
בברכה,
מדור שכר
צהר הלב

<span style="color: #888; font-size: 11px;">─────────────────────────────</span>
<span style="color: red; font-size: 11px;">הודעה זו נשלחה באופן אוטומטי. אין להשיב למייל זה.</span>
"""


def _is_completion_apartment(apartment_id: Optional[int] = None, apartment_name: Optional[str] = None) -> bool:
    """האם הדירה היא דירת השלמות - זיהוי לפי ID."""
    if apartment_id is not None:
        return apartment_id in COMPLETION_APARTMENT_IDS
    if not apartment_name:
        return False
    normalized = " ".join(str(apartment_name).split())
    base_name = normalized.split("(", 1)[0].strip()
    return base_name == COMPLETION_APARTMENT_NAME


def _validate_guide_access(person_id: int, housing_filter: Optional[int]) -> None:
    """בדיקת הרשאת צפייה במדריך לפי פילטר מערך דיור."""
    enforce_housing_filter_guide_access(person_id, housing_filter)


def _inject_holiday_payment(
    conn, monthly_totals: dict, person_id: int,
    year: int, month: int, shabbat_cache: dict,
    minimum_wage: float, housing_filter: int | None,
) -> None:
    """הזרקת תשלום חג ל-monthly_totals (in-place)."""
    person_type = conn.execute(
        "SELECT type FROM people WHERE id = %s", (person_id,)
    ).fetchone()
    if not person_type or person_type["type"] != PERMANENT_EMPLOYEE_TYPE:
        return

    hp_map = calculate_holiday_payments(
        conn.conn, year, month, shabbat_cache, minimum_wage,
        housing_filter=housing_filter,
    )
    hp_data = hp_map.get(person_id)
    if hp_data and hp_data["amount"] > 0:
        hp = hp_data["amount"]
        monthly_totals["holiday_payment"] = hp
        monthly_totals["holiday_payment_count"] = hp_data["count"]
        monthly_totals["holiday_payment_rate"] = hp_data["rate"]
        monthly_totals["holiday_payment_hours"] = (
            round(hp / round(minimum_wage, 2), 2)
            if minimum_wage else 0
        )
        monthly_totals["holiday_payment_details"] = hp_data.get("details", [])
        hp_rounded = round(round(hp, 2), 1)
        monthly_totals["total_payment"] = monthly_totals.get("total_payment", 0) + hp_rounded
        monthly_totals["gesher_total"] = monthly_totals.get("gesher_total", 0) + hp_rounded
        monthly_totals["display_total"] = monthly_totals.get("display_total", 0) + hp_rounded
        monthly_totals["rounded_total"] = monthly_totals.get("rounded_total", 0) + hp_rounded




def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _build_holiday_payment_chain_summary(
    conn,
    person_id: int,
    year: int,
    month: int,
    shabbat_cache: dict,
    minimum_wage: float,
    housing_filter: int | None,
) -> dict:
    """פירוט תשלום חג לתצוגה בתחתית דוח הרצפים."""
    holiday_dates, work_dates_by_holiday = get_holiday_payment_dates_in_month(
        conn.conn, year, month, shabbat_cache
    )
    summary = {
        "has_holiday_dates": bool(holiday_dates),
        "holiday_dates": [d.strftime("%d/%m/%Y") for d in holiday_dates],
        "amount": 0.0,
        "count": 0,
        "rate": 0.0,
        "status": "none",
        "calculation": "",
        "payment_details": [],
        "notes": [],
    }
    if not holiday_dates:
        return summary

    person = conn.execute(
        "SELECT id, type, start_date FROM people WHERE id = %s",
        (person_id,),
    ).fetchone()
    if not person or person.get("type") != PERMANENT_EMPLOYEE_TYPE:
        summary["status"] = "cancelled"
        summary["notes"].append("לא שולם: העובד אינו מדריך קבוע.")
        return summary

    start_date = _as_date(person.get("start_date"))
    has_seniority = _has_sufficient_seniority(start_date, year, month)

    hp_map = calculate_holiday_payments(
        conn.conn, year, month, shabbat_cache, minimum_wage,
        housing_filter=housing_filter,
    )
    hp_data = hp_map.get(person_id)
    if hp_data:
        summary["amount"] = round(float(hp_data.get("amount") or 0), 2)
        summary["count"] = int(hp_data.get("count") or 0)
        summary["rate"] = round(float(hp_data.get("rate") or 0), 2)
    if summary["amount"] > 0:
        summary["status"] = "paid"
        summary["calculation"] = (
            f"{summary['count']} ימי חג × {summary['rate']:.2f} ₪ = {summary['amount']:.2f} ₪"
        )
        if minimum_wage > 0 and summary["rate"] > 0:
            summary["payment_details"].append({
                "count": summary["count"],
                "hours": round(summary["rate"] / round(minimum_wage, 2), 2),
                "rate": round(minimum_wage, 2),
                "payment": summary["amount"],
            })
    else:
        summary["status"] = "cancelled"

    if not has_seniority:
        summary["notes"].append("לא שולם/בוטל: אין ותק של 3 חודשים בתחילת חודש הדיווח.")

    start_dt, end_dt = month_range_ts(year, month)
    if housing_filter is not None:
        reports = conn.execute("""
            SELECT tr.apartment_id, tr.date, tr.start_time, tr.end_time, tr.shift_type_id,
                   ap.name AS apartment_name
            FROM time_reports tr
            JOIN apartments ap ON ap.id = tr.apartment_id
            WHERE tr.person_id = %s
              AND tr.date >= %s AND tr.date < %s
              AND ap.housing_array_id = %s
        """, (person_id, start_dt.date(), end_dt.date(), housing_filter)).fetchall()
    else:
        reports = conn.execute("""
            SELECT tr.apartment_id, tr.date, tr.start_time, tr.end_time, tr.shift_type_id,
                   ap.name AS apartment_name
            FROM time_reports tr
            LEFT JOIN apartments ap ON ap.id = tr.apartment_id
            WHERE tr.person_id = %s
              AND tr.date >= %s AND tr.date < %s
        """, (person_id, start_dt.date(), end_dt.date())).fetchall()

    reports_by_apartment: dict[int, list[dict]] = {}
    month_apartments: set[int] = set()
    for report in reports:
        apt_id = report.get("apartment_id")
        report_date = _as_date(report.get("date"))
        if not apt_id or not report_date:
            continue
        month_apartments.add(apt_id)
        reports_by_apartment.setdefault(apt_id, []).append({
            "date": report_date,
            "start_time": report.get("start_time"),
            "end_time": report.get("end_time"),
            "shift_type_id": report.get("shift_type_id"),
            "apartment_name": report.get("apartment_name") or "",
        })

    saved_rows = conn.execute("""
        SELECT apartment_id, guide_1_id, guide_2_id, guide_3_id, guide_2_no_holiday_payment
        FROM holiday_payment_apartment_guides
        WHERE year = %s AND month = %s
          AND (%s IN (guide_1_id, guide_2_id, guide_3_id))
    """, (year, month, person_id)).fetchall()
    saved_any = conn.execute("""
        SELECT 1
        FROM holiday_payment_apartment_guides
        WHERE year = %s AND month = %s
        LIMIT 1
    """, (year, month)).fetchone()
    candidate_apartments = {row["apartment_id"] for row in saved_rows} if saved_any else month_apartments

    if not candidate_apartments:
        summary["notes"].append("לא שולם: המדריך לא משויך כמדריך קבוע לדירה בחודש זה.")

    regular_holiday_dates = set(get_holiday_dates_in_month(year, month, shabbat_cache))
    special_holiday_windows = _get_special_holiday_payment_window_details(conn.conn, year, month)

    for holiday_date in holiday_dates:
        work_dates = work_dates_by_holiday.get(holiday_date, {holiday_date})
        worked_apartments = []
        for apt_id in candidate_apartments:
            for report in reports_by_apartment.get(apt_id, []):
                worked_on_regular_holiday = (
                    holiday_date in regular_holiday_dates
                    and report["date"] == holiday_date
                )
                worked_in_special_window = any(
                    _report_overlaps_special_holiday_window(
                        conn.conn, report, holiday_date, window
                    )
                    for window in special_holiday_windows.get(holiday_date, [])
                )
                worked_on_legacy_holiday_date = (
                    not special_holiday_windows.get(holiday_date)
                    and report["date"] in work_dates
                )
                if worked_on_regular_holiday or worked_in_special_window or worked_on_legacy_holiday_date:
                    worked_apartments.append(report["apartment_name"] or f"דירה {apt_id}")
                    break
        if worked_apartments:
            apt_text = ", ".join(sorted(set(worked_apartments)))
            summary["notes"].append(
                f"{holiday_date.strftime('%d/%m/%Y')}: לא שולם עבור {apt_text} כי יש דיווח עבודה ביום/חלון החג."
            )
        elif has_seniority and summary["amount"] <= 0 and candidate_apartments:
            summary["notes"].append(
                f"{holiday_date.strftime('%d/%m/%Y')}: לא שולם לפי ניהול תשלום חג לחודש זה."
            )

    if summary["amount"] <= 0 and not summary["calculation"]:
        summary["calculation"] = "0 ימי חג לתשלום = 0.00 ₪"

    seen = set()
    unique_notes = []
    for note in summary["notes"]:
        if note not in seen:
            seen.add(note)
            unique_notes.append(note)
    summary["notes"] = unique_notes
    return summary


def _sort_shift_rows_for_display(shifts_data: list[dict]) -> list[dict]:
    """העברת משמרות דירת השלמות לסוף הטבלה והסתרת התאריך/יום שלהן."""
    ordered_rows: list[dict] = []
    for idx, shift in enumerate(shifts_data):
        row = dict(shift)
        row["is_completion_apartment"] = _is_completion_apartment(
            apartment_id=row.get("apartment_id"),
            apartment_name=row.get("apartment"),
        )
        row["_display_order"] = idx
        if row["is_completion_apartment"]:
            row["date"] = ""
            row["day"] = ""
        ordered_rows.append(row)

    ordered_rows.sort(
        key=lambda row: (
            1 if row.get("is_completion_apartment") else 0,
            row.get("_display_order", 0),
        )
    )

    for row in ordered_rows:
        row.pop("_display_order", None)

    return ordered_rows


def _parse_hhmm_to_minutes(value: str) -> int:
    hours, minutes = map(int, str(value)[:5].split(":"))
    return hours * 60 + minutes


def _workday_axis_interval(start_time: str, end_time: str) -> tuple[int, int]:
    """המרת שעות לציר יום עבודה 08:00-08:00 לצורך התאמת שורות הדוח לחישוב."""
    start = _parse_hhmm_to_minutes(start_time)
    end = _parse_hhmm_to_minutes(end_time)

    if start < 480:
        start += 1440
    if end <= start:
        end += 1440
    elif end <= 480:
        end += 1440

    return start, end


def _allocation_windows_for_report(report_date: date, start_time: str, end_time: str) -> list[tuple[date, int, int]]:
    start, end = _workday_axis_interval(start_time, end_time)
    if end <= 1920:
        return [(report_date, start, end)]
    return [
        (report_date, start, 1920),
        (report_date + timedelta(days=1), 480, end - 1440),
    ]


def _chain_paid_minutes(chain: dict) -> tuple[float, float]:
    """מחזיר (דקות עבודה לתצוגה, דקות כוננות לתצוגה) לפי מקור החישוב."""
    total_minutes = chain.get("total_minutes", 0) or 0
    chain_type = chain.get("type", "work")

    if chain_type == "standby":
        return 0.0, float(total_minutes)
    if chain_type == "sick":
        sick_rate = (chain.get("sick_rate_percent", 100) or 0) / 100
        return float(total_minutes) * sick_rate, 0.0
    return float(total_minutes), 0.0


def _prioritized_overlap_rows(chain: dict, overlaps: list[tuple[dict, int]]) -> list[tuple[dict, int]]:
    """
    בחפיפה בין משמרת ארוכה לשורה נקודתית, השורה הנקודתית מקבלת את השעות שלה.

    זה משפיע רק על תצוגת שורות הדוח. סה"כ השעות נשאר לפי מנוע השכר.
    """
    chain_shift_id = chain.get("shift_id")
    if chain_shift_id not in DISPLAY_PRIORITY_SHIFT_IDS:
        return overlaps

    priority_overlaps = [
        (row, overlap)
        for row, overlap in overlaps
        if row.get("_allocation_shift_type_id") == chain_shift_id
    ]
    return priority_overlaps or overlaps


def _apply_calculated_hours_to_shift_rows(shifts_data: list[dict], daily_segments: list[dict]) -> tuple[float, int]:
    """
    התאמת עמודות עבודה/כוננות בשורות דוח המשמרות למנוע החישוב בלי לשנות את מבנה הדוח.

    השורות נשארות שורות דיווח. ההקצאה נעשית לפי חפיפת זמן מול רצפי החישוב של אותו יום עבודה.
    """
    rows_by_day: dict[date, list[dict]] = {}
    for row in shifts_data:
        row["_calc_by_day"] = {}
        windows = row.get("_allocation_windows", [])
        if windows:
            for day, _start, _end in windows:
                rows_by_day.setdefault(day, []).append(row)
        elif row.get("_allocation_no_time_day") is not None:
            rows_by_day.setdefault(row["_allocation_no_time_day"], []).append(row)

    for day in daily_segments:
        day_date = day.get("date_obj")
        if day_date is None:
            continue
        day_rows = rows_by_day.get(day_date, [])
        if not day_rows:
            continue

        def add_row_minutes(row: dict, work_minutes: float = 0.0, standby_minutes: float = 0.0) -> None:
            day_alloc = row["_calc_by_day"].setdefault(day_date, {"work": 0.0, "standby": 0.0})
            day_alloc["work"] += work_minutes
            day_alloc["standby"] += standby_minutes

        for chain in day.get("chains", []):
            chain_start = chain.get("start_time")
            chain_end = chain.get("end_time")
            if not chain_start or not chain_end:
                continue

            calc_work_minutes, calc_standby_minutes = _chain_paid_minutes(chain)
            calc_minutes = calc_work_minutes + calc_standby_minutes
            if calc_minutes <= 0:
                continue

            chain_axis_start, chain_axis_end = _workday_axis_interval(chain_start, chain_end)
            chain_duration = max(0, chain_axis_end - chain_axis_start)
            if chain_duration <= 0:
                continue

            overlaps: list[tuple[dict, int]] = []
            for row in day_rows:
                overlap = 0
                for window_day, row_start, row_end in row.get("_allocation_windows", []):
                    if window_day != day_date:
                        continue
                    overlap += max(0, min(chain_axis_end, row_end) - max(chain_axis_start, row_start))
                if overlap > 0:
                    overlaps.append((row, overlap))

            if not overlaps:
                chain_type = chain.get("type", "work")
                candidate_rows = [
                    row for row in day_rows
                    if not row.get("_allocation_windows")
                    and (
                        chain_type == "work"
                        or (chain_type == "sick" and "מחלה" in (row.get("shift_type") or ""))
                        or (chain_type == "vacation" and "חופשה" in (row.get("shift_type") or ""))
                    )
                ]
                if not candidate_rows:
                    continue

                target_row = candidate_rows[0]
                add_row_minutes(target_row, calc_work_minutes, calc_standby_minutes)
                continue

            allocation_overlaps = _prioritized_overlap_rows(chain, overlaps)
            total_overlap = sum(overlap for _row, overlap in allocation_overlaps)
            if total_overlap <= 0:
                continue

            work_ratio = calc_work_minutes / chain_duration
            standby_ratio = calc_standby_minutes / chain_duration
            for row, overlap in allocation_overlaps:
                weight = overlap / total_overlap
                add_row_minutes(
                    row,
                    calc_work_minutes * weight if total_overlap != chain_duration else overlap * work_ratio,
                    calc_standby_minutes * weight if total_overlap != chain_duration else overlap * standby_ratio,
                )

        target_work_minutes = day.get("total_minutes_no_standby", 0) or 0
        for chain in day.get("chains", []):
            if chain.get("type") == "sick":
                sick_rate = (chain.get("sick_rate_percent", 100) or 0) / 100
                target_work_minutes -= (chain.get("total_minutes", 0) or 0) * (1 - sick_rate)

        current_work_minutes = sum(
            (row.get("_calc_by_day", {}).get(day_date, {}) or {}).get("work", 0.0)
            for row in day_rows
        )
        work_diff = round(target_work_minutes - current_work_minutes, 6)
        if abs(work_diff) > 0.000001:
            adjustable_rows = [
                row for row in day_rows
                if ((row.get("_calc_by_day", {}).get(day_date, {}) or {}).get("work", 0.0) > 0)
            ]
            if work_diff > 0:
                target_row = adjustable_rows[-1] if adjustable_rows else day_rows[-1]
                add_row_minutes(target_row, work_diff, 0.0)
            else:
                remaining_reduction = abs(work_diff)
                for row in reversed(adjustable_rows):
                    current = (row.get("_calc_by_day", {}).get(day_date, {}) or {}).get("work", 0.0)
                    reduction = min(current, remaining_reduction)
                    row["_calc_by_day"][day_date]["work"] = current - reduction
                    remaining_reduction -= reduction
                    if remaining_reduction <= 0.000001:
                        break

    total_work_hours = 0.0
    standby_count = 0
    for row in shifts_data:
        row_work_minutes = sum(day_alloc.get("work", 0.0) for day_alloc in row.get("_calc_by_day", {}).values())
        row_standby_minutes = sum(day_alloc.get("standby", 0.0) for day_alloc in row.get("_calc_by_day", {}).values())
        work_hours = round(row_work_minutes / 60, 2)
        standby_hours = round(row_standby_minutes / 60, 2)
        row["work_hours"] = work_hours
        row["standby_hours"] = standby_hours
        total_work_hours += work_hours
        if standby_hours > 0:
            standby_count += 1

    target_work_hours = 0.0
    target_standby_hours = 0.0
    for day in daily_segments:
        target_work_minutes = day.get("total_minutes_no_standby", 0) or 0
        for chain in day.get("chains", []):
            if chain.get("type") == "sick":
                sick_rate = (chain.get("sick_rate_percent", 100) or 0) / 100
                target_work_minutes -= (chain.get("total_minutes", 0) or 0) * (1 - sick_rate)
            elif chain.get("type") == "standby":
                target_standby_hours += (chain.get("total_minutes", 0) or 0) / 60
        target_work_hours += target_work_minutes / 60

    def close_rounding_gap(field: str, target_hours: float) -> None:
        current_hours = round(sum(row.get(field, 0.0) or 0.0 for row in shifts_data), 2)
        diff = round(round(target_hours, 2) - current_hours, 2)
        if diff == 0:
            return
        for row in reversed(shifts_data):
            if row.get(field, 0.0) > 0:
                row[field] = round((row.get(field, 0.0) or 0.0) + diff, 2)
                return

    close_rounding_gap("work_hours", target_work_hours)
    close_rounding_gap("standby_hours", target_standby_hours)
    total_work_hours = round(sum(row.get("work_hours", 0.0) or 0.0 for row in shifts_data), 2)
    standby_count = sum(1 for row in shifts_data if (row.get("standby_hours", 0.0) or 0.0) > 0)

    for key in ("_calc_by_day", "_allocation_windows", "_allocation_no_time_day", "_allocation_shift_type_id"):
        for row in shifts_data:
            row.pop(key, None)

    return round(total_work_hours, 2), standby_count


def _summarize_display_chains(chains: list[dict]) -> dict:
    """סיכומי תצוגה לשורות רצפים שמוצגות בפועל."""
    summary = {
        "payment": 0.0,
        "calc100": 0.0,
        "calc125": 0.0,
        "calc150": 0.0,
        "calc175": 0.0,
        "calc200": 0.0,
        "total_minutes": 0.0,
        "total_minutes_no_standby": 0.0,
        "has_work": False,
    }

    for chain in chains:
        total_minutes = chain.get("total_minutes", 0) or 0
        chain_type = chain.get("type")

        summary["total_minutes"] += total_minutes

        if chain_type == "standby":
            continue

        summary["payment"] += chain.get("payment", 0) or 0
        summary["calc100"] += chain.get("calc100", 0) or 0
        summary["calc125"] += chain.get("calc125", 0) or 0
        summary["calc150"] += chain.get("calc150", 0) or 0
        summary["calc175"] += chain.get("calc175", 0) or 0
        summary["calc200"] += chain.get("calc200", 0) or 0
        summary["total_minutes_no_standby"] += total_minutes
        if chain_type == "work":
            summary["has_work"] = True

    return summary


def _prepare_daily_segments_for_display(daily_segments: list[dict]) -> list[dict]:
    """
    הכנת רצפים לתצוגה בלי לשנות את הסדר הכרונולוגי.

    דירת השלמות מוצגת בתוך היום המקורי שלה כמו כל דירה אחרת.
    """
    display_days: list[dict] = []

    for day in daily_segments:
        display_day = dict(day)
        display_chains: list[dict] = []
        day_token = day.get("date_obj").isoformat() if day.get("date_obj") else day.get("day", "")

        for chain in day.get("chains", []):
            display_chain = dict(chain)
            display_chain["display_group_token"] = day_token
            display_chain["is_completion_apartment"] = _is_completion_apartment(
                apartment_name=display_chain.get("apartment_name")
            )
            display_chains.append(display_chain)

        display_day["chains"] = display_chains
        display_days.append(display_day)

    return display_days


def simple_summary_view(
    request: Request,
    person_id: int,
    month: Optional[int] = None,
    year: Optional[int] = None
) -> HTMLResponse:
    """Simple summary view for a guide."""
    # בדיקת הרשאה - מנהל מסגרת יכול לראות רק מדריכים מהמערך שלו
    housing_filter = get_housing_array_filter()
    _validate_guide_access(person_id, housing_filter)

    start_time = time.time()
    logger.info(f"Starting simple_summary_view for person_id={person_id}, {month}/{year}")

    conn_start = time.time()
    with get_conn() as conn:
        conn_time = time.time() - conn_start
        logger.info(f"Database connection took: {conn_time:.4f}s")
        # Defaults
        if month is None or year is None:
            now = datetime.now(config.LOCAL_TZ)
            year, month = now.year, now.month

        # Minimum Wage (historical - for the selected month)
        wage_start = time.time()
        minimum_wage = get_minimum_wage_for_month(conn.conn, year, month)
        logger.info(f"get_minimum_wage_for_month took: {time.time() - wage_start:.4f}s, value={minimum_wage} for {year}/{month}")

        shabbat_start = time.time()
        shabbat_cache = get_shabbat_times_cache(conn.conn)
        logger.info(f"get_shabbat_times_cache took: {time.time() - shabbat_start:.4f}s")

        # Get data
        segments_start = time.time()
        daily_segments, person_name = get_daily_segments_data(conn, person_id, year, month, shabbat_cache, minimum_wage)
        logger.info(f"get_daily_segments_data took: {time.time() - segments_start:.4f}s")

        person = conn.execute("SELECT * FROM people WHERE id = %s", (person_id,)).fetchone()

        # Aggregate
        summary = {
            "weekday": {"count": 0, "payment": 0},
            "friday": {"count": 0, "payment": 0},
            "saturday": {"count": 0, "payment": 0},
            "overtime": {"hours": 0, "payment": 0},
            "total_payment": 0
        }

        for day in daily_segments:
            # Skip if no work/vacation/sick (just empty day)
            if not day.get("payment") and not day.get("has_work"):
                continue

            # Determine type
            # Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
            wd = day["date_obj"].weekday()
            day_str = day["date_obj"].strftime("%Y-%m-%d")
            day_info = shabbat_cache.get(day_str)

            # זיהוי חג באמצע השבוע לפי טבלת shabbat_times
            is_holiday = bool(day_info and (day_info.get("enter") or day_info.get("exit")) and wd != 4 and wd != 5)
            is_friday = (wd == 4) or (is_holiday and day_info and day_info.get("enter") and not day_info.get("exit"))
            is_saturday = (wd == 5) or (is_holiday and not is_friday)
            is_weekday = not is_friday and not is_saturday

            day_payment = day["payment"] or 0

            # Calculate Overtime part (125% + 150% non-shabbat)
            overtime_hours = 0
            overtime_payment = 0

            for seg in day["segments"]:
                rate = seg.get("rate", 100)
                if rate > 100 and not seg.get("is_shabbat", False):
                    overtime_hours += seg["hours"]
                    overtime_payment += seg["payment"]

            # Accumulate
            if is_weekday:
                summary["weekday"]["count"] += 1
                summary["weekday"]["payment"] += day_payment
            elif is_friday:
                summary["friday"]["count"] += 1
                summary["friday"]["payment"] += day_payment
            elif is_saturday:
                summary["saturday"]["count"] += 1
                summary["saturday"]["payment"] += day_payment

            summary["overtime"]["hours"] += overtime_hours
            summary["overtime"]["payment"] += overtime_payment
            summary["total_payment"] += day_payment

    render_start = time.time()
    response = templates.TemplateResponse(
        "simple_summary.html",
        {
            "request": request,
            "person": person,
            "summary": summary,
            "year": year,
            "month": month,
            "person_name": person_name,
        },
    )
    render_time = time.time() - render_start
    logger.info(f"Template rendering took: {render_time:.4f}s")

    total_time = time.time() - start_time
    logger.info(f"Total simple_summary_view execution time: {total_time:.4f}s")

    return response


def guide_view(
    request: Request,
    person_id: int,
    month: Optional[int] = None,
    year: Optional[int] = None
) -> HTMLResponse:
    """Detailed guide view with full monthly report."""
    # בדיקת הרשאה - מנהל מסגרת יכול לראות רק מדריכים מהמערך שלו
    housing_filter = get_housing_array_filter()
    _validate_guide_access(person_id, housing_filter)

    func_start_time = time.time()
    logger.info(f"Starting guide_view for person_id={person_id}, {month}/{year}")

    conn_start = time.time()
    with get_conn() as conn:
        conn_time = time.time() - conn_start
        logger.info(f"Database connection took: {conn_time:.4f}s")

        # שכר מינימום יישלף בהמשך לפי החודש הנבחר

        person = conn.execute(
            """
            SELECT p.id, p.name, p.phone, p.email, p.type, p.is_active, p.start_date, p.meirav_code, 
                   e.code as employer_code, e.name as employer_name
            FROM people p
            LEFT JOIN employers e ON p.employer_id = e.id
            WHERE p.id = %s
            """,
            (person_id,),
        ).fetchone()
        if not person:
            raise HTTPException(status_code=404, detail="מדריך לא נמצא")

        # Fetch payment codes early to avoid connection issues later
        payment_start = time.time()
        payment_codes = get_payment_codes(conn.conn)
        logger.info(f"get_payment_codes took: {time.time() - payment_start:.4f}s")
        if not payment_codes:
            # Try once more with a fresh connection if first fetch failed
            try:
                with get_conn() as temp_conn:
                    payment_codes = get_payment_codes(temp_conn.conn)
            except Exception as e:
                logger.warning(f"Secondary fetch of payment codes failed: {e}")

        # Optimized: Fetch available months
        months_start = time.time()
        months = get_available_months_for_person(conn.conn, person_id)
        logger.info(f"get_available_months_for_person took: {time.time() - months_start:.4f}s")

        # Prepare months options for template
        months_options = [{"year": y, "month": m, "label": f"{m:02d}/{y}"} for y, m in months]

        shabbat_cache = None
        if not months:
            selected_year, selected_month = year or datetime.now().year, month or datetime.now().month
            # שליפת שכר מינימום לפי החודש הנבחר
            MINIMUM_WAGE = get_minimum_wage_for_month(conn.conn, selected_year, selected_month)
            daily_segments = []
            monthly_totals = {
                "total_hours": 0.0,
                "calc100": 0.0,
                "calc125": 0.0,
                "calc150": 0.0,
                "calc150_shabbat": 0.0,
                "calc150_shabbat_100": 0.0,
                "calc150_shabbat_50": 0.0,
                "calc150_overtime": 0.0,
                "calc175": 0.0,
                "calc200": 0.0,
                "vacation_minutes": 0.0,
                "vacation_payment": 0.0,
                "travel": 0.0,
                "extras": 0.0,
                "sick_days_accrued": 0.0,
                "vacation_days_accrued": 0.0,
                "payment": 0.0,
                "actual_work_days": 0.0,
                "vacation_days_taken": 0.0,
                "standby": 0.0,
                "standby_payment": 0.0,
            }
        else:
            # Select month/year
            if month is None or year is None:
                selected_year, selected_month = months[-1]
            else:
                selected_year, selected_month = year, month

            # שליפת שכר מינימום לפי החודש הנבחר
            wage_start = time.time()
            MINIMUM_WAGE = get_minimum_wage_for_month(conn.conn, selected_year, selected_month)
            logger.info(f"get_minimum_wage_for_month took: {time.time() - wage_start:.4f}s, value={MINIMUM_WAGE} for {selected_year}/{selected_month}")

            # Get monthly data
            shabbat_start = time.time()
            shabbat_cache = get_shabbat_times_cache(conn.conn)
            logger.info(f"get_shabbat_times_cache took: {time.time() - shabbat_start:.4f}s")

            segments_calc_start = time.time()
            daily_segments, person_name = get_daily_segments_data(
                conn, person_id, selected_year, selected_month, shabbat_cache, MINIMUM_WAGE
            )
            logger.info(f"get_daily_segments_data took: {time.time() - segments_calc_start:.4f}s")

            # חישוב monthly_totals ממקור אחד - daily_segments
            # זה מחליף את calculate_person_monthly_totals והדריסות הידניות
            totals_start = time.time()
            monthly_totals = aggregate_daily_segments_to_monthly(
                conn, daily_segments, person_id, selected_year, selected_month, MINIMUM_WAGE,
                housing_filter=housing_filter,
            )
            logger.info(f"aggregate_daily_segments_to_monthly took: {time.time() - totals_start:.4f}s")

            _inject_holiday_payment(
                conn, monthly_totals, person_id,
                selected_year, selected_month, shabbat_cache,
                MINIMUM_WAGE, housing_filter,
            )

            # אישור אוטומטי של נסיעות מדריך מחליף
            start_dt, end_dt = month_range_ts(selected_year, selected_month)
            auto_approve_substitute_travel(conn.conn, person_id, start_dt.date(), end_dt.date())

        daily_segments = _prepare_daily_segments_for_display(daily_segments)
        monthly_report = prepare_guide_pdf_data(
            conn,
            person_id,
            selected_year,
            selected_month,
            housing_filter,
        )
        if not monthly_report:
            last_day = calendar.monthrange(selected_year, selected_month)[1]
            monthly_report = {
                "person": {
                    "id": person["id"],
                    "name": person["name"],
                    "email": person["email"],
                    "type": person["type"],
                },
                "shifts_data": [],
                "payments_data": [],
                "completion_payments_data": [],
                "total_work_hours": 0.0,
                "standby_count": 0,
                "total_additions": 0.0,
                "total_additions_no_travel": 0.0,
                "total_salary": 0.0,
                "period_start": f"01/{selected_month:02d}/{str(selected_year)[2:]}",
                "period_end": f"{last_day}/{selected_month:02d}/{str(selected_year)[2:]}",
                "generation_time": datetime.now(config.LOCAL_TZ).strftime("%H:%M:%S %d.%m.%Y"),
                "variable_shifts": [],
                "variable_rate_total": 0.0,
            }

        guide_notes = _fetch_notes(conn, person_id, selected_year, selected_month)
        if shabbat_cache is None:
            shabbat_cache = get_shabbat_times_cache(conn.conn)
        holiday_payment_setup = get_holiday_payment_setup(
            conn.conn, selected_year, selected_month, shabbat_cache, housing_filter,
        )
        holiday_payment_chain_summary = _build_holiday_payment_chain_summary(
            conn, person_id, selected_year, selected_month, shabbat_cache,
            MINIMUM_WAGE, housing_filter,
        )

        # בדיקת מדריך במספר מערכי דיור
        other_housing_arrays: list[str] = []
        if selected_year and selected_month:
            start_dt_date, end_dt_date = month_range_ts(selected_year, selected_month)
            multi = get_multi_housing_guides(conn, start_dt_date.date(), end_dt_date.date())
            if person_id in multi:
                other_housing_arrays = multi[person_id]

    # Calculate total standby count
    total_standby_count = monthly_totals.get("standby", 0)

    # Get unique years for dropdown
    years = sorted(set(m["year"] for m in months_options), reverse=True) if months_options else [selected_year]

    render_start = time.time()
    response = templates.TemplateResponse(
        "guide.html",
        {
            "request": request,
            "person": person,
            "months": months_options,
            "years": years,
            "selected_year": selected_year,
            "selected_month": selected_month,
            "daily_segments": daily_segments,
            "monthly_totals": monthly_totals,
            "monthly_report": monthly_report,
            "payment_codes": payment_codes or {},
            "minimum_wage": MINIMUM_WAGE,
            "total_standby_count": total_standby_count,
            "guide_notes": guide_notes,
            "other_housing_arrays": other_housing_arrays,
            "holiday_payment_setup": holiday_payment_setup,
            "holiday_payment_chain_summary": holiday_payment_chain_summary,
        },
    )
    render_time = time.time() - render_start
    logger.info(f"Template rendering took: {render_time:.4f}s")

    total_time = time.time() - func_start_time
    logger.info(f"Total guide_view execution time: {total_time:.4f}s")

    return response


def _get_hebrew_day_name(date_obj: date) -> str:
    """המרת תאריך ליום בשבוע בעברית."""
    days = ["ב", "ג", "ד", "ה", "ו", "ש", "א"]  # Monday=0 ... Sunday=6
    return days[date_obj.weekday()]


def shifts_report_pdf(
    request: Request,
    person_id: int,
    month: Optional[int] = None,
    year: Optional[int] = None
) -> Response:
    """
    הורדת דוח משמרות כ-PDF.
    """
    housing_filter = get_housing_array_filter()
    _validate_guide_access(person_id, housing_filter)

    with get_conn() as conn:
        if month is None or year is None:
            now = datetime.now(config.LOCAL_TZ)
            year, month = now.year, now.month

        person = conn.execute(
            "SELECT id, name FROM people WHERE id = %s",
            (person_id,),
        ).fetchone()
        if not person:
            raise HTTPException(status_code=404, detail="מדריך לא נמצא")

    # יצירת PDF (ללא צורך ב-session token - הנתונים נשלפים ישירות)
    pdf_bytes = _generate_shifts_pdf(person_id, year, month)

    if not pdf_bytes:
        raise HTTPException(status_code=500, detail="שגיאה ביצירת PDF")

    # URL-encode Hebrew filename for Content-Disposition header
    from urllib.parse import quote
    filename = f"דוח_משמרות_{person['name']}_{month:02d}_{year}.pdf"
    filename_encoded = quote(filename, safe='')

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}"
        }
    )


def prepare_guide_pdf_data(conn, person_id: int, year: int, month: int, housing_filter: Optional[int] = None) -> Optional[Dict]:
    """
    הכנת נתונים לדוח PDF של מדריך.

    Args:
        conn: חיבור לדאטאבייס
        person_id: מזהה המדריך
        year: שנה
        month: חודש
        housing_filter: מזהה מערך דיור לסינון (None = ללא סינון)

    Returns:
        Dict עם כל הנתונים הנדרשים לתבנית guide_shifts_pdf.html, או None אם המדריך לא נמצא
    """
    import calendar
    from core.time_utils import get_shabbat_times_cache
    from core.history import get_minimum_wage_for_month

    person = conn.execute(
        "SELECT id, name, email, type FROM people WHERE id = %s",
        (person_id,)
    ).fetchone()

    if not person:
        return None

    # תאריכי החודש
    start_dt, end_dt = month_range_ts(year, month)
    start_date = start_dt.date()
    end_date = end_dt.date()

    # שליפת משמרות (עם סינון דירות למערך כשמוגדר פילטר — תואם לחישוב הסגמנטים)
    if housing_filter is not None:
        reports = conn.execute("""
            SELECT
                tr.id, tr.date, tr.start_time, tr.end_time, tr.shift_type_id,
                tr.apartment_id,
                tr.rate_apartment_type_id, tr.asd_night_marking,
                tr.description,
                a.apartment_type_id,
                a.housing_array_id,
                st.name AS shift_type_name,
                a.name AS apartment_name,
                rate_at.name AS rate_apartment_type_name
            FROM time_reports tr
            LEFT JOIN shift_types st ON tr.shift_type_id = st.id
            LEFT JOIN apartments a ON tr.apartment_id = a.id
            LEFT JOIN apartment_types rate_at ON rate_at.id = tr.rate_apartment_type_id
            WHERE tr.person_id = %s
              AND tr.date >= %s AND tr.date < %s
              AND (a.id IS NULL OR a.housing_array_id = %s)
            ORDER BY tr.date, tr.start_time
        """, (person_id, start_date, end_date, housing_filter)).fetchall()
    else:
        reports = conn.execute("""
            SELECT
                tr.id, tr.date, tr.start_time, tr.end_time, tr.shift_type_id,
                tr.apartment_id,
                tr.rate_apartment_type_id, tr.asd_night_marking,
                tr.description,
                a.apartment_type_id,
                a.housing_array_id,
                st.name AS shift_type_name,
                a.name AS apartment_name,
                rate_at.name AS rate_apartment_type_name
            FROM time_reports tr
            LEFT JOIN shift_types st ON tr.shift_type_id = st.id
            LEFT JOIN apartments a ON tr.apartment_id = a.id
            LEFT JOIN apartment_types rate_at ON rate_at.id = tr.rate_apartment_type_id
            WHERE tr.person_id = %s
              AND tr.date >= %s AND tr.date < %s
            ORDER BY tr.date, tr.start_time
        """, (person_id, start_date, end_date)).fetchall()

    reports = [
        report for report in reports
        if not should_exclude_asd_completion_report(
            year,
            month,
            report.get("housing_array_id"),
            report.get("apartment_id"),
        )
    ]

    # שליפת סגמנטים
    shift_ids = list({r["shift_type_id"] for r in reports if r["shift_type_id"]})
    segments_by_shift = {}
    if shift_ids:
        placeholders = ",".join(["%s"] * len(shift_ids))
        segments = conn.execute(f"""
            SELECT shift_type_id, segment_type, start_time, end_time
            FROM shift_time_segments
            WHERE shift_type_id IN ({placeholders})
            ORDER BY shift_type_id, order_index
        """, tuple(shift_ids)).fetchall()
        for seg in segments:
            segments_by_shift.setdefault(seg["shift_type_id"], []).append(seg)

    shabbat_cache = get_shabbat_times_cache(conn.conn if hasattr(conn, "conn") else conn)

    # בניית שורות הדוח
    shifts_data = []
    total_work_hours = 0.0
    standby_count = 0

    def _build_apartment_display_simple(r: dict) -> str:
        """בונה שם דירה עם סוג תשלום שונה אם קיים."""
        apt_name = r.get("apartment_name") or ""
        rate_type_name = r.get("rate_apartment_type_name")
        if rate_type_name and r.get("rate_apartment_type_id") != r.get("apartment_type_id"):
            return f"{apt_name} (משולם כ: {rate_type_name})"
        return apt_name

    def _asd_night_note_pdf(r: dict) -> str:
        """תווית ASD לילה לפי סוג דירה."""
        if not r.get("asd_night_marking"):
            return ""
        apt_type = r.get("apartment_type_id")
        if apt_type == HIGH_FUNCTIONING_APT_TYPE:
            return "שינה בסלון"
        if apt_type == LOW_FUNCTIONING_APT_TYPE:
            return "ערות בלילה"
        return ""

    def _compose_shift_note(r: dict) -> str:
        """שילוב הערת הדיווח עם הערת ASD לעמודת ההערה בדוח."""
        note_parts: list[str] = []
        description = (r.get("description") or "").strip()
        asd_note = _asd_night_note_pdf(r)

        if description:
            note_parts.append(description)
        if asd_note and asd_note not in note_parts:
            note_parts.append(asd_note)

        return " | ".join(note_parts)

    def _is_asd_apartment_pdf(r: dict) -> bool:
        """האם הדירה שייכת למערך ASD."""
        return is_asd_housing_array(r.get("housing_array_id"))

    for r in reports:
        r_date = r["date"]
        if isinstance(r_date, datetime):
            r_date = r_date.date()

        # עיבוד שם סוג משמרת (הסרת המילה "משמרת")
        shift_name = r["shift_type_name"] or ""
        if shift_name.startswith("משמרת "):
            shift_name = shift_name[len("משמרת "):]

        # בדיקה אם זו משמרת תגבור עם מקטעים
        is_tagbor = "תגבור" in shift_name
        segment_list = segments_by_shift.get(r["shift_type_id"], [])

        if is_tagbor and segment_list and r["start_time"] and r["end_time"]:
            # תצוגה מיוחדת למשמרת תגבור - שורה לכל מקטע
            from core.shift_hours import calculate_tagbur_segments
            tagbur_segs = calculate_tagbur_segments(
                r["start_time"], r["end_time"],
                r["shift_type_id"], segments_by_shift,
                report_date=r_date,
                year=year,
                month=month,
                shabbat_cache=shabbat_cache,
            )
            for seg_data in tagbur_segs:
                work_hours = seg_data["work_hours"]
                standby_hours = seg_data["standby_hours"]
                alloc_start, alloc_end = _workday_axis_interval(
                    seg_data["display_start"], seg_data["display_end"]
                )
                total_work_hours += work_hours
                if standby_hours > 0:
                    standby_count += 1

                shifts_data.append({
                    "date": r_date.strftime("%d/%m/%y") if seg_data["is_first"] else "",
                    "day": _get_hebrew_day_name(r_date) if seg_data["is_first"] else "",
                    "apartment": _build_apartment_display_simple(r) if seg_data["is_first"] else "",
                    "apartment_id": r.get("apartment_id"),
                    "shift_type": shift_name if seg_data["is_first"] else "",
                    "start_time": seg_data["display_start"],
                    "end_time": seg_data["display_end"],
                    "work_hours": work_hours,
                    "standby_hours": standby_hours,
                    "note": _compose_shift_note(r) if seg_data["is_first"] else "",
                    "tagbor_group": True,
                    "tagbor_first": seg_data["is_first"],
                    "tagbor_last": seg_data["is_last"],
                    "_allocation_windows": [(r_date, alloc_start, alloc_end)],
                    "_allocation_shift_type_id": r["shift_type_id"],
                })
        else:
            # חישוב שעות - פונקציה מרכזית (לילה/רגיל/ASD)
            from core.shift_hours import calculate_shift_hours
            work_hours, standby_hours = 0.0, 0.0
            if r["start_time"] and r["end_time"]:
                work_hours, standby_hours = calculate_shift_hours(
                    r["start_time"], r["end_time"],
                    r["shift_type_id"], segments_by_shift,
                    apartment_type_id=r.get("apartment_type_id"),
                    housing_array_id=r.get("housing_array_id"),
                )

            total_work_hours += work_hours
            if standby_hours > 0:
                standby_count += 1
            allocation_windows = []
            if r["start_time"] and r["end_time"]:
                allocation_windows = _allocation_windows_for_report(r_date, r["start_time"], r["end_time"])

            shifts_data.append({
                "date": r_date.strftime("%d/%m/%y"),
                "day": _get_hebrew_day_name(r_date),
                "apartment": _build_apartment_display_simple(r),
                "apartment_id": r.get("apartment_id"),
                "shift_type": shift_name,
                "start_time": r["start_time"][:5] if r["start_time"] else "",
                "end_time": r["end_time"][:5] if r["end_time"] else "",
                "work_hours": round(work_hours, 2),
                "standby_hours": round(standby_hours, 2),
                "note": _compose_shift_note(r),
                "_allocation_windows": allocation_windows,
                "_allocation_no_time_day": r_date if not allocation_windows else None,
                "_allocation_shift_type_id": r["shift_type_id"],
            })

    # שליפת תשלומים נוספים (לפי דירות במערך כשמוגדר פילטר)
    if housing_filter is not None:
        payment_comps = conn.execute("""
            SELECT
                pc.quantity, pc.rate, pc.description,
                pc.apartment_id,
                pct.name AS component_type_name
            FROM payment_components pc
            LEFT JOIN payment_component_types pct ON pc.component_type_id = pct.id
            INNER JOIN apartments a ON pc.apartment_id = a.id
            WHERE pc.person_id = %s
              AND pc.date >= %s AND pc.date < %s
              AND a.housing_array_id = %s
            ORDER BY pc.date
        """, (person_id, start_date, end_date, housing_filter)).fetchall()
    else:
        payment_comps = conn.execute("""
            SELECT
                pc.quantity, pc.rate, pc.description,
                pc.apartment_id,
                pct.name AS component_type_name
            FROM payment_components pc
            LEFT JOIN payment_component_types pct ON pc.component_type_id = pct.id
            WHERE pc.person_id = %s
              AND pc.date >= %s AND pc.date < %s
            ORDER BY pc.date
        """, (person_id, start_date, end_date)).fetchall()

    payments_by_type: Dict[str, float] = {}
    completion_payments_by_type: Dict[str, float] = {}
    total_additions = 0.0
    total_additions_no_travel = 0.0
    for pc in payment_comps:
        # תעריפים באגורות - מחלקים ב-100
        amount = (pc["quantity"] * pc["rate"]) / 100
        total_additions += amount
        type_name = pc["component_type_name"] or "אחר"
        # בדיקה אם זה נסיעות - אם כן, איחוד לשורה אחת ללא תיאור
        is_travel = "נסיעות" in type_name or "נסיעה" in type_name
        is_professional_support = "תומך מקצועי" in type_name
        if is_travel or is_professional_support:
            # נסיעות / תומך מקצועי - שורה אחת עם שם הסוג בלבד
            key = type_name
        elif pc["description"]:
            key = f"{type_name} - {pc['description']}"
        else:
            key = type_name
        # פיצול תשלומים: השלמות לחוד, רגילים לחוד
        target = completion_payments_by_type if _is_completion_apartment(apartment_id=pc.get("apartment_id")) else payments_by_type
        target[key] = target.get(key, 0) + amount
        # חישוב תוספות ללא נסיעות
        if not is_travel:
            total_additions_no_travel += amount

    payments_data = [
        {"description": desc, "amount": round(amt, 2)}
        for desc, amt in payments_by_type.items()
    ]
    completion_payments_data = [
        {"description": desc, "amount": round(amt, 2)}
        for desc, amt in completion_payments_by_type.items()
    ]

    # חישוב תעריפים משתנים מ-daily_segments
    MINIMUM_WAGE = get_minimum_wage_for_month(conn.conn, year, month)
    shabbat_cache = get_shabbat_times_cache(conn.conn)

    daily_segments, _ = get_daily_segments_data(
        conn, person_id, year, month, shabbat_cache, MINIMUM_WAGE
    )
    monthly_totals = aggregate_daily_segments_to_monthly(
        conn, daily_segments, person_id, year, month, MINIMUM_WAGE,
        housing_filter=housing_filter,
    )
    _inject_holiday_payment(
        conn, monthly_totals, person_id,
        year, month, shabbat_cache,
        MINIMUM_WAGE, housing_filter,
    )
    total_work_hours, standby_count = _apply_calculated_hours_to_shift_rows(
        shifts_data, daily_segments
    )

    # הוספת שורת תשלום חג לטבלת התשלומים
    if monthly_totals.get("holiday_payment"):
        hp_hours = monthly_totals.get("holiday_payment_hours", 0) or 0
        holiday_dates, _holiday_work_dates = get_holiday_payment_dates_in_month(
            conn.conn, year, month, shabbat_cache
        )
        holiday_count = len(holiday_dates)
        hp_details = monthly_totals.get("holiday_payment_details", []) or []
        apartment_ids = sorted({
            detail.get("apartment_id")
            for detail in hp_details
            if detail.get("apartment_id")
        })
        apartment_names = {}
        if apartment_ids:
            apt_rows = conn.execute(
                "SELECT id, name FROM apartments WHERE id = ANY(%s)",
                (apartment_ids,),
            ).fetchall()
            apartment_names = {row["id"]: row["name"] for row in apt_rows}
        hours_by_apartment = {}
        for detail in hp_details:
            apartment_id = detail.get("apartment_id")
            if not apartment_id:
                continue
            hours_by_apartment[apartment_id] = (
                hours_by_apartment.get(apartment_id, 0)
                + (detail.get("hours") or 0)
            )
        holiday_detail = "; ".join(
            f"{apartment_names.get(apartment_id, f'דירה {apartment_id}')}: {hours:.2f} שעות"
            for apartment_id, hours in sorted(
                hours_by_apartment.items(),
                key=lambda item: apartment_names.get(item[0], ""),
            )
        )
        payments_data.append({
            "description": f"תשלום חג ({holiday_count} חגים)",
            "detail": holiday_detail,
            "amount": round(monthly_totals["holiday_payment"], 2),
            "work_hours": round(hp_hours, 2),
        })
        total_additions += monthly_totals["holiday_payment"]
        total_additions_no_travel += monthly_totals["holiday_payment"]

    # פירוט שורות תלוש/גשר שבהן יש פילוג תעריפים.
    # ב-ASD מציגים רק רכיבי שכר שבהם אותה שורת תלוש מורכבת מיותר מתעריף בסיס אחד.
    is_asd = housing_filter is not None and is_asd_housing_array(housing_filter)
    variable_shifts = []
    variable_rate_total_from_rows = 0.0  # סה"כ מחושב מהשורות המעוגלות
    show_asd_breakdown = False

    if is_asd:
        component_labels = {
            "calc100": "שעות רגילות (100%)",
            "calc125": "שעות נוספות (125%)",
            "calc150_overtime": "שעות נוספות (150%)",
            "calc150_shabbat_100": "שעות שבת - בסיס (100%)",
            "calc150_shabbat_50": "שעות שבת - תוספת (50%)",
            "calc175": "שעות שבת (175%)",
            "calc200": "שעות שבת (200%)",
        }
        component_multipliers = {
            "calc100": 1.0,
            "calc125": 1.25,
            "calc150_overtime": 1.5,
            "calc150_shabbat_100": 1.0,
            "calc150_shabbat_50": 0.5,
            "calc175": 1.75,
            "calc200": 2.0,
        }
        variable_by_component = {}

        def add_component_breakdown(component_key: str, minutes: int | float, chain: dict, rate: float) -> None:
            if minutes <= 0:
                return
            rounded_rate = round(rate, 2)
            apartment_type_name = (chain.get("apartment_type_name") or "").replace("דירה ", "").strip()
            supplement_parts = []
            apartment_supplement = chain.get("apartment_hourly_supplement_nis", 0) or 0
            if apartment_supplement > 0:
                supplement_parts.append(f"+{apartment_supplement:.2f} סוג דירה")
            asd_seniority_supplement = chain.get("asd_seniority_supplement_nis", 0) or 0
            if asd_seniority_supplement > 0:
                supplement_parts.append(f"+{asd_seniority_supplement:.2f} ותק ASD")
            reason = " | ".join([part for part in [apartment_type_name, ", ".join(supplement_parts)] if part]) or "-"
            group_key = (component_key, rounded_rate, reason)
            if group_key not in variable_by_component:
                variable_by_component[group_key] = {
                    "component_key": component_key,
                    "shift_name": component_labels[component_key],
                    "minutes": 0,
                    "payment": 0.0,
                    "rate": rounded_rate,
                    "multiplier": component_multipliers[component_key],
                    "reason": reason,
                }
            hours = round(minutes / 60, 2)
            variable_by_component[group_key]["minutes"] += minutes
            variable_by_component[group_key]["payment"] += hours * round(
                rounded_rate * component_multipliers[component_key], 2
            )

        for day in daily_segments:
            for chain in day.get("chains", []):
                if chain.get("type", "work") != "work":
                    continue
                chain_rate = chain.get("effective_rate", MINIMUM_WAGE) or MINIMUM_WAGE
                add_component_breakdown("calc100", chain.get("calc100", 0) or 0, chain, chain_rate)
                add_component_breakdown("calc125", chain.get("calc125", 0) or 0, chain, chain_rate)
                add_component_breakdown("calc150_overtime", chain.get("calc150_overtime", 0) or 0, chain, chain_rate)
                shabbat_150_minutes = chain.get("calc150_shabbat", 0) or 0
                add_component_breakdown("calc150_shabbat_100", shabbat_150_minutes, chain, chain_rate)
                add_component_breakdown("calc150_shabbat_50", shabbat_150_minutes, chain, chain_rate)
                add_component_breakdown("calc175", chain.get("calc175", 0) or 0, chain, chain_rate)
                add_component_breakdown("calc200", chain.get("calc200", 0) or 0, chain, chain_rate)

        component_rates = {}
        for (component_key, rate, _reason) in variable_by_component.keys():
            component_rates.setdefault(component_key, set()).add(rate)
        component_keys_with_multiple_rates = {
            component_key for component_key, rates in component_rates.items() if len(rates) > 1
        }
        show_asd_breakdown = bool(component_keys_with_multiple_rates)

        for data in variable_by_component.values():
            if data["component_key"] not in component_keys_with_multiple_rates:
                continue
            hours = round(data["minutes"] / 60, 2)
            payment = round(data["payment"], 1)
            base_payment = round(hours * data["rate"], 2)
            overtime_payment = round(payment - base_payment, 1)
            variable_shifts.append({
                "shift_name": data["shift_name"],
                "hours": hours,
                "rate": data["rate"],
                "overtime_payment": overtime_payment,
                "payment": payment,
                "reason": data["reason"],
            })
            variable_rate_total_from_rows += payment

    else:
        variable_by_shift = {}
        for day in daily_segments:
            for chain in day.get("chains", []):
                chain_shift_name = chain.get("shift_name", "") or ""
                chain_rate = chain.get("effective_rate", MINIMUM_WAGE) or MINIMUM_WAGE

                if not chain_shift_name:
                    continue

                is_special_hourly = chain.get("is_special_hourly", False)
                supplement = float(chain.get("hourly_wage_supplement", 0)) / 100
                is_variable_rate = is_special_hourly or abs(chain_rate - MINIMUM_WAGE - supplement) > 0.01
                if not is_variable_rate:
                    continue

                calc100 = chain.get("calc100", 0) or 0
                calc125 = chain.get("calc125", 0) or 0
                calc150 = chain.get("calc150", 0) or 0
                calc150_shabbat = chain.get("calc150_shabbat", 0) or 0
                calc175 = chain.get("calc175", 0) or 0
                calc200 = chain.get("calc200", 0) or 0
                total_minutes = calc100 + calc125 + calc150 + calc175 + calc200
                shabbat_minutes = calc150_shabbat + calc175 + calc200

                if total_minutes <= 0:
                    continue

                rounded_rate = round(chain_rate, 2)
                gesher_payment = (
                    round(calc100 / 60, 2) * 1.0 * rounded_rate +
                    round(calc125 / 60, 2) * 1.25 * rounded_rate +
                    round(calc150 / 60, 2) * 1.5 * rounded_rate +
                    round(calc175 / 60, 2) * 1.75 * rounded_rate +
                    round(calc200 / 60, 2) * 2.0 * rounded_rate +
                    (chain.get("escort_bonus_pay", 0) or 0)
                )

                group_key = (chain_shift_name, rounded_rate)
                if group_key not in variable_by_shift:
                    variable_by_shift[group_key] = {
                        "shift_name": chain_shift_name,
                        "minutes": 0,
                        "shabbat_minutes": 0,
                        "payment": 0,
                        "rate": rounded_rate,
                    }
                variable_by_shift[group_key]["minutes"] += total_minutes
                variable_by_shift[group_key]["shabbat_minutes"] += shabbat_minutes
                variable_by_shift[group_key]["payment"] += gesher_payment

        shift_name_rates = {}
        for (shift_name, rate), _data in variable_by_shift.items():
            shift_name_rates.setdefault(shift_name, set()).add(rate)
        shift_names_with_multiple_rates = {
            shift_name for shift_name, rates in shift_name_rates.items() if len(rates) > 1
        }

        for data in variable_by_shift.values():
            hours = round(data["minutes"] / 60, 2)
            payment = round(data["payment"], 1)
            rate = data["rate"]
            base_shift_name = data["shift_name"]
            if base_shift_name in shift_names_with_multiple_rates:
                is_shabbat = data["shabbat_minutes"] > (data["minutes"] * 0.5)
                display_name = f"{base_shift_name} (שבת)" if is_shabbat else f"{base_shift_name} (חול)"
            else:
                display_name = base_shift_name

            base_payment = round(hours * rate, 2)
            overtime_payment = round(payment - base_payment, 1)
            variable_shifts.append({
                "shift_name": display_name,
                "hours": hours,
                "rate": rate,
                "overtime_payment": overtime_payment,
                "payment": payment,
                "reason": "-",
            })
            variable_rate_total_from_rows += payment

    # חישוב תאריכי תקופה
    last_day = calendar.monthrange(year, month)[1]
    period_start = f"01/{month:02d}/{str(year)[2:]}"
    period_end = f"{last_day}/{month:02d}/{str(year)[2:]}"
    generation_time = datetime.now(config.LOCAL_TZ).strftime("%H:%M:%S %d.%m.%Y")

    summary_total_salary = monthly_totals.get("rounded_total", 0)
    # סה"כ תעריף משתנה - סכום הערכים המעוגלים המוצגים בטבלה
    variable_rate_total = round(variable_rate_total_from_rows, 1)
    shifts_data = _sort_shift_rows_for_display(shifts_data)

    return {
        "person": dict(person),
        "shifts_data": shifts_data,
        "payments_data": payments_data,
        "completion_payments_data": completion_payments_data,
        "total_work_hours": round(total_work_hours, 2),
        "standby_count": standby_count,
        "total_additions": round(total_additions, 2),
        "total_additions_no_travel": round(total_additions_no_travel, 2),
        "total_salary": round(summary_total_salary, 2),
        "period_start": period_start,
        "period_end": period_end,
        "generation_time": generation_time,
        "variable_shifts": variable_shifts,
        "variable_rate_total": variable_rate_total,
        "is_asd_multi_rate": is_asd and show_asd_breakdown,
    }


def shifts_report_preview(
    request: Request,
    person_id: int,
    month: Optional[int] = None,
    year: Optional[int] = None
) -> HTMLResponse:
    """תצוגה מקדימה של דוח משמרות (זהה ל-PDF) — למשתמש מורשה לפי מערך דיור."""
    housing_filter = get_housing_array_filter()
    _validate_guide_access(person_id, housing_filter)

    if month is None or year is None:
        now = datetime.now(config.LOCAL_TZ)
        year, month = now.year, now.month

    with get_conn() as conn:
        pdf_data = prepare_guide_pdf_data(conn, person_id, year, month, housing_filter)

    if not pdf_data:
        raise HTTPException(status_code=404, detail="מדריך לא נמצא")

    return templates.TemplateResponse("guide_shifts_pdf.html", {
        "request": request,
        "show_total_salary": True,
        **pdf_data,
    })


def _generate_shifts_pdf(person_id: int, year: int, month: int, session_token: Optional[str] = None) -> Optional[bytes]:
    """
    יצירת PDF לדוח משמרות באמצעות Edge/Chrome headless.
    משתמש ב-prepare_guide_pdf_data להכנת הנתונים.

    Args:
        person_id: מזהה המדריך
        year: שנה
        month: חודש
        session_token: לא בשימוש (נשמר לתאימות)
    """
    from jinja2 import Environment, FileSystemLoader

    try:
        logger.info(f"Generating shifts PDF for person_id={person_id}, {month}/{year}")

        # הכנת נתונים באמצעות הפונקציה המשותפת
        with get_conn() as conn:
            hf = get_housing_array_filter()
            pdf_data = prepare_guide_pdf_data(conn, person_id, year, month, hf)

        if not pdf_data:
            logger.error(f"Person not found: {person_id}")
            return None

        # רנדור התבנית
        env = Environment(loader=FileSystemLoader(str(config.TEMPLATES_DIR)))
        template = env.get_template("guide_shifts_pdf.html")
        html_content = template.render(**pdf_data)

        return render_html_to_pdf_bytes(html_content)

    except Exception as e:
        logger.error(f"Error generating shifts PDF: {e}", exc_info=True)
        return None


async def shifts_report_email(
    request: Request,
    person_id: int,
    year: int,
    month: int
) -> JSONResponse:
    """
    שליחת דוח משמרות במייל.
    """
    import asyncio
    from services.email_service import get_email_settings, send_email_with_pdf

    try:
        housing_filter = get_housing_array_filter()
        _validate_guide_access(person_id, housing_filter)

        # קבלת פרטי מייל מותאמים מה-body
        custom_email = None
        custom_subject = ""
        extra_message = ""
        try:
            body = await request.json()
            custom_email = body.get('email')
            custom_subject = body.get('subject') or ""
            extra_message = body.get('extra_message') or ""
        except Exception:
            pass

    except HTTPException as e:
        return JSONResponse({"success": False, "error": e.detail}, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Error in shifts_report_email setup: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": GENERIC_ERROR})

    array_filter_for_thread = housing_filter

    def send_email_task(
        pid: int,
        y: int,
        m: int,
        email: Optional[str],
        subject_template: str,
        message_template: str,
    ) -> dict:
        """
        רץ בתהליכון: חובה לקבע את פילטר מערך הדיור ב-ContextVar
        (אחרת מנהל מסגרת לא מקבל את אותו חישוב כמו בבקשת HTTP).
        """
        set_housing_array_filter(array_filter_for_thread)
        try:
            with get_conn() as conn:
                settings = get_email_settings(conn)
                if not settings:
                    return {"success": False, "error": "הגדרות מייל לא נמצאו"}

                person = conn.execute(
                    "SELECT id, name, email FROM people WHERE id = %s",
                    (pid,)
                ).fetchone()

                if not person:
                    return {"success": False, "error": "מדריך לא נמצא"}

                target_email = email if email else person['email']
                if not target_email:
                    return {"success": False, "error": f"למדריך {person['name']} אין כתובת מייל"}

                # יצירת PDF
                pdf_bytes = _generate_shifts_pdf(pid, y, m)
                if not pdf_bytes:
                    return {"success": False, "error": "שגיאה ביצירת PDF"}

                default_subject = f"דוח משמרות - {person['name']} - {m:02d}/{y}"
                formatted_subject = _format_shifts_email_text(
                    subject_template,
                    person['name'],
                    y,
                    m,
                )
                subject = _sanitize_email_subject(formatted_subject) or default_subject
                body_text = _build_shifts_email_body(person['name'], y, m, message_template)
                pdf_filename = f"דוח_משמרות_{person['name']}_{m:02d}_{y}.pdf"

                result = send_email_with_pdf(
                    settings=settings,
                    to_email=target_email,
                    to_name=person['name'],
                    subject=subject,
                    body=body_text,
                    pdf_bytes=pdf_bytes,
                    pdf_filename=pdf_filename
                )

                if result['success']:
                    return {"success": True, "message": f"המייל נשלח בהצלחה ל-{target_email}"}
                return result

        except Exception as e:
            logger.error(f"Error sending shifts email: {e}")
            return {"success": False, "error": GENERIC_ERROR}
        finally:
            set_housing_array_filter(None)

    try:
        result = await asyncio.to_thread(
            send_email_task,
            person_id,
            year,
            month,
            custom_email,
            custom_subject,
            extra_message,
        )
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Error in shifts_report_email execution: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": GENERIC_ERROR})


def _prepare_chains_pdf_data(conn, person_id: int, year: int, month: int) -> Optional[dict]:
    """הכנת נתונים לתבנית PDF של דוח רצפים."""
    person = conn.execute(
        """
        SELECT p.id, p.name, p.type, p.email, p.meirav_code
        FROM people p WHERE p.id = %s
        """,
        (person_id,),
    ).fetchone()
    if not person:
        return None

    MINIMUM_WAGE = get_minimum_wage_for_month(conn.conn, year, month)
    shabbat_cache = get_shabbat_times_cache(conn.conn)
    daily_segments, _ = get_daily_segments_data(conn, person_id, year, month, shabbat_cache, MINIMUM_WAGE)
    hf = get_housing_array_filter()
    monthly_totals = aggregate_daily_segments_to_monthly(
        conn, daily_segments, person_id, year, month, MINIMUM_WAGE, housing_filter=hf
    )
    _inject_holiday_payment(
        conn, monthly_totals, person_id,
        year, month, shabbat_cache,
        MINIMUM_WAGE, hf,
    )
    holiday_payment_chain_summary = _build_holiday_payment_chain_summary(
        conn, person_id, year, month, shabbat_cache, MINIMUM_WAGE, hf,
    )
    daily_segments = _prepare_daily_segments_for_display(daily_segments)
    monthly_report = prepare_guide_pdf_data(conn, person_id, year, month, hf)

    return {
        "person": person,
        "daily_segments": daily_segments,
        "monthly_totals": monthly_totals,
        "monthly_report": monthly_report,
        "minimum_wage": MINIMUM_WAGE,
        "selected_month": month,
        "selected_year": year,
        "holiday_payment_chain_summary": holiday_payment_chain_summary,
        "generation_time": datetime.now().strftime("%d/%m/%Y %H:%M"),
    }


def _generate_chains_pdf(person_id: int, year: int, month: int) -> Optional[bytes]:
    """יצירת PDF לדוח רצפים באמצעות Edge/Chrome headless."""
    from jinja2 import Environment, FileSystemLoader

    try:
        logger.info(f"Generating chains PDF for person_id={person_id}, {month}/{year}")

        with get_conn() as conn:
            pdf_data = _prepare_chains_pdf_data(conn, person_id, year, month)

        if not pdf_data:
            logger.error(f"Person not found: {person_id}")
            return None

        env = Environment(loader=FileSystemLoader(str(config.TEMPLATES_DIR)))
        template = env.get_template("guide_chains_pdf.html")
        html_content = template.render(**pdf_data)

        return render_html_to_pdf_bytes(html_content)

    except Exception as e:
        logger.error(f"Error generating chains PDF: {e}", exc_info=True)
        return None


async def chains_report_email(
    request: Request,
    person_id: int,
    year: int,
    month: int
) -> JSONResponse:
    """שליחת דוח רצפים במייל כ-PDF."""
    import asyncio
    from services.email_service import get_email_settings, send_email_with_pdf

    try:
        housing_filter = get_housing_array_filter()
        _validate_guide_access(person_id, housing_filter)

        custom_email = None
        try:
            body = await request.json()
            custom_email = body.get('email')
        except Exception:
            pass

    except HTTPException as e:
        return JSONResponse({"success": False, "error": e.detail}, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Error in chains_report_email setup: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": GENERIC_ERROR})

    array_filter_for_thread = housing_filter

    def send_email_task(pid: int, y: int, m: int, email: Optional[str]) -> dict:
        set_housing_array_filter(array_filter_for_thread)
        try:
            # שליפת הגדרות ופרטי מדריך - חיבור DB קצר
            with get_conn() as conn:
                settings = get_email_settings(conn)
                if not settings:
                    return {"success": False, "error": "הגדרות מייל לא נמצאו"}

                person = conn.execute(
                    "SELECT id, name, email FROM people WHERE id = %s",
                    (pid,)
                ).fetchone()

            if not person:
                return {"success": False, "error": "מדריך לא נמצא"}

            target_email = email if email else person['email']
            if not target_email:
                return {"success": False, "error": f"למדריך {person['name']} אין כתובת מייל"}

            # יצירת PDF ושליחת מייל - ללא חיבור DB
            pdf_bytes = _generate_chains_pdf(pid, y, m)
            if not pdf_bytes:
                return {"success": False, "error": "שגיאה ביצירת PDF"}

            subject = f"דוח פירוט רצפים - {person['name']} - {m:02d}/{y}"
            body_text = f"""שלום {person['name']},

מצורף דוח פירוט הרצפים שלך לחודש {m:02d}/{y}.

בברכה,
מדור שכר
צהר הלב

<span style="color: #888; font-size: 11px;">─────────────────────────────</span>
<span style="color: red; font-size: 11px;">הודעה זו נשלחה באופן אוטומטי. אין להשיב למייל זה.</span>
"""
            pdf_filename = f"דוח_רצפים_{person['name']}_{m:02d}_{y}.pdf"

            result = send_email_with_pdf(
                settings=settings,
                to_email=target_email,
                to_name=person['name'],
                subject=subject,
                body=body_text,
                pdf_bytes=pdf_bytes,
                pdf_filename=pdf_filename
            )

            if result['success']:
                return {"success": True, "message": f"המייל נשלח בהצלחה ל-{target_email}"}
            return result

        except Exception as e:
            logger.error(f"Error sending chains email: {e}")
            return {"success": False, "error": GENERIC_ERROR}
        finally:
            set_housing_array_filter(None)

    try:
        result = await asyncio.to_thread(send_email_task, person_id, year, month, custom_email)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Error in chains_report_email execution: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": GENERIC_ERROR})


# =============================================================================
# הערות למדריך - Guide Monthly Notes
# =============================================================================


def _fetch_notes(conn, person_id: int, year: int, month: int) -> List[dict]:
    """שליפת הערות למדריך וחודש מסוים."""
    rows = conn.execute(
        """
        SELECT n.id, n.content, n.created_at, n.updated_at,
               p.name AS created_by_name
        FROM guide_monthly_notes n
        LEFT JOIN people p ON n.created_by = p.id
        WHERE n.person_id = %s AND n.year = %s AND n.month = %s
        ORDER BY n.created_at DESC
        """,
        (person_id, year, month),
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_all_notes_for_month(
    conn,
    year: int,
    month: int,
    housing_filter: int | None = None,
) -> List[dict]:
    """שליפת כל הערות המדריכים לחודש, עם סינון מערך דיור אם קיים."""
    if housing_filter is not None:
        rows = conn.execute(
            """
            SELECT n.id, n.person_id, n.content, n.created_at, n.updated_at,
                   guide.name AS guide_name,
                   guide.meirav_code AS guide_employee_number,
                   guide.id_number AS guide_id_number,
                   creator.name AS created_by_name
            FROM guide_monthly_notes n
            JOIN people guide ON guide.id = n.person_id
            LEFT JOIN people creator ON creator.id = n.created_by
            WHERE n.year = %s AND n.month = %s
              AND guide.housing_array_id = %s
            ORDER BY guide.name, n.created_at DESC, n.id DESC
            """,
            (year, month, housing_filter),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT n.id, n.person_id, n.content, n.created_at, n.updated_at,
                   guide.name AS guide_name,
                   guide.meirav_code AS guide_employee_number,
                   guide.id_number AS guide_id_number,
                   creator.name AS created_by_name
            FROM guide_monthly_notes n
            JOIN people guide ON guide.id = n.person_id
            LEFT JOIN people creator ON creator.id = n.created_by
            WHERE n.year = %s AND n.month = %s
            ORDER BY guide.name, n.created_at DESC, n.id DESC
            """,
            (year, month),
        ).fetchall()
    return [dict(r) for r in rows]


def guide_notes_management(
    request: Request,
    year: int | None = None,
    month: int | None = None,
) -> HTMLResponse:
    """עמוד ניהול מרוכז לכל הערות המדריכים בחודש."""
    if year is None or month is None:
        default_year, default_month = get_default_period(request)
        year = year or default_year
        month = month or default_month

    housing_filter = get_housing_array_filter()
    with get_conn() as conn:
        notes = _fetch_all_notes_for_month(conn, year, month, housing_filter)

    return templates.TemplateResponse(
        "guide_notes_management.html",
        {
            "request": request,
            "notes": notes,
            "selected_year": year,
            "selected_month": month,
            "years": list(range(2023, 2028)),
        },
    )


def get_guide_notes(request: Request, person_id: int, year: int, month: int) -> JSONResponse:
    """API: שליפת הערות למדריך וחודש."""
    _validate_guide_access(person_id, get_housing_array_filter())
    with get_conn() as conn:
        notes = _fetch_notes(conn, person_id, year, month)
    for n in notes:
        n["created_at"] = n["created_at"].strftime("%d/%m/%Y %H:%M") if n["created_at"] else ""
    return JSONResponse({"notes": notes})


async def add_guide_note(request: Request, person_id: int) -> JSONResponse:
    """API: הוספת הערה למדריך."""
    _validate_guide_access(person_id, get_housing_array_filter())
    data = await request.json()
    content = (data.get("content") or "").strip()
    year = data.get("year")
    month = data.get("month")

    if not content:
        return JSONResponse({"success": False, "error": "תוכן ההערה ריק"}, status_code=400)

    user = getattr(request.state, "current_user", None)
    created_by = user.get("person_id") if user else None

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO guide_monthly_notes (person_id, year, month, content, created_by)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (person_id, year, month, content, created_by),
        )
        conn.conn.commit()

    return JSONResponse({"success": True})


async def delete_guide_note(request: Request, note_id: int) -> JSONResponse:
    """API: מחיקת הערה."""
    with get_conn() as conn:
        note = conn.execute(
            "SELECT person_id FROM guide_monthly_notes WHERE id = %s",
            (note_id,),
        ).fetchone()
        if not note:
            return JSONResponse({"success": False, "error": "הערה לא נמצאה"}, status_code=404)

        _validate_guide_access(note["person_id"], get_housing_array_filter())
        conn.execute("DELETE FROM guide_monthly_notes WHERE id = %s", (note_id,))
        conn.conn.commit()

    return JSONResponse({"success": True})


def get_holiday_payment_setup_api(request: Request, year: int, month: int) -> JSONResponse:
    """API: נתוני ניהול תשלום חג לחודש."""
    housing_filter = get_housing_array_filter()
    with get_conn() as conn:
        shabbat_cache = get_shabbat_times_cache(conn.conn)
        setup = get_holiday_payment_setup(conn.conn, year, month, shabbat_cache, housing_filter)
    return JSONResponse(setup)


async def save_holiday_payment_setup_api(request: Request) -> JSONResponse:
    """API: שמירת ניהול תשלום חג לחודש."""
    from core.history import is_month_locked

    data = await request.json()
    year = int(data.get("year") or 0)
    month = int(data.get("month") or 0)
    rows = data.get("rows") or []
    if year <= 0 or month < 1 or month > 12:
        return JSONResponse({"success": False, "error": "חודש או שנה לא תקינים"}, status_code=400)
    if not isinstance(rows, list):
        return JSONResponse({"success": False, "error": "מבנה נתונים לא תקין"}, status_code=400)

    housing_filter = get_housing_array_filter()
    try:
        with get_conn() as conn:
            if is_month_locked(conn.conn, year, month):
                return JSONResponse(
                    {"success": False, "error": "החודש נעול לעריכה"},
                    status_code=400,
                )
            save_holiday_payment_setup(conn.conn, year, month, rows, housing_filter)
        return JSONResponse({"success": True})
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.error("Error saving holiday payment setup: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "שגיאה בשמירת תשלום חג"}, status_code=500)

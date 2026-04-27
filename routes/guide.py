"""
Guide routes for DiyurCalc application.
Contains routes for viewing guide details and summaries.
"""
from __future__ import annotations

import calendar
import time
import logging
from datetime import datetime
from typing import Optional, Dict, List

from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from core.config import config
from core.database import (
    get_conn,
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
    is_asd_housing_array,
)
from core.holiday_payment import calculate_holiday_payments
from utils.utils import month_range_ts, format_currency, format_currency_total, human_date

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
templates.env.filters["format_currency"] = format_currency
templates.env.filters["format_currency_total"] = format_currency_total
templates.env.filters["human_date"] = human_date
templates.env.globals["app_version"] = config.VERSION

COMPLETION_APARTMENT_NAME = "השלמות"


def _validate_guide_access(person_id: int, housing_filter: Optional[int]) -> None:
    """
    בודק שלמשתמש יש הרשאה לצפות במדריך.
    זורק HTTPException 403 אם אין הרשאה.
    """
    if housing_filter is None:
        return  # מנהל על - יכול לראות הכל

    # בדוק שהמדריך שייך למערך הדיור של המשתמש
    with get_conn() as conn:
        row = conn.execute(
            "SELECT housing_array_id FROM people WHERE id = %s",
            (person_id,)
        ).fetchone()

        if not row or row["housing_array_id"] != housing_filter:
            raise HTTPException(status_code=403, detail="אין הרשאה לצפות במדריך זה")


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
        hp_rounded = round(round(hp, 2), 1)
        monthly_totals["total_payment"] = monthly_totals.get("total_payment", 0) + hp_rounded
        monthly_totals["gesher_total"] = monthly_totals.get("gesher_total", 0) + hp_rounded
        monthly_totals["display_total"] = monthly_totals.get("display_total", 0) + hp_rounded
        monthly_totals["rounded_total"] = monthly_totals.get("rounded_total", 0) + hp_rounded


def _is_completion_apartment(apartment_name: Optional[str]) -> bool:
    """האם שם הדירה מייצג את דירת ההשלמות."""
    if not apartment_name:
        return False
    normalized = " ".join(str(apartment_name).split())
    base_name = normalized.split("(", 1)[0].strip()
    return base_name == COMPLETION_APARTMENT_NAME


def _sort_shift_rows_for_display(shifts_data: list[dict]) -> list[dict]:
    """העברת משמרות דירת השלמות לסוף הטבלה והסתרת התאריך/יום שלהן."""
    ordered_rows: list[dict] = []
    for idx, shift in enumerate(shifts_data):
        row = dict(shift)
        row["is_completion_apartment"] = _is_completion_apartment(row.get("apartment"))
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
    העברת רצפי דירת השלמות לסקשן נפרד בסוף, ללא תאריך.

    הרצפים עצמם נשארים באותו סדר כרונולוגי חודשי, אבל לא מוצגים תחת היום המקורי.
    """
    display_days: list[dict] = []
    completion_chains: list[dict] = []
    completion_summary = {
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

    for day in daily_segments:
        regular_chains: list[dict] = []
        moved_chains: list[dict] = []
        day_token = day.get("date_obj").isoformat() if day.get("date_obj") else day.get("day", "")

        for chain in day.get("chains", []):
            if chain.get("type") == "work" and _is_completion_apartment(chain.get("apartment_name")):
                moved_chain = dict(chain)
                moved_chain["display_group_token"] = day_token
                moved_chain["is_completion_apartment"] = True
                moved_chains.append(moved_chain)
            else:
                regular_chains.append(chain)

        if regular_chains:
            display_day = dict(day)
            display_day["chains"] = regular_chains
            if moved_chains:
                regular_summary = _summarize_display_chains(regular_chains)
                display_day["payment"] = regular_summary["payment"]
                display_day["calc100"] = regular_summary["calc100"]
                display_day["calc125"] = regular_summary["calc125"]
                display_day["calc150"] = regular_summary["calc150"]
                display_day["calc175"] = regular_summary["calc175"]
                display_day["calc200"] = regular_summary["calc200"]
                display_day["total_minutes"] = regular_summary["total_minutes"]
                display_day["total_minutes_no_standby"] = regular_summary["total_minutes_no_standby"]
                display_day["has_work"] = regular_summary["has_work"]
            display_days.append(display_day)

        elif day.get("chains") and not moved_chains:
            display_days.append(dict(day))

        if moved_chains:
            moved_summary = _summarize_display_chains(moved_chains)
            completion_chains.extend(moved_chains)
            completion_summary["payment"] += moved_summary["payment"]
            completion_summary["calc100"] += moved_summary["calc100"]
            completion_summary["calc125"] += moved_summary["calc125"]
            completion_summary["calc150"] += moved_summary["calc150"]
            completion_summary["calc175"] += moved_summary["calc175"]
            completion_summary["calc200"] += moved_summary["calc200"]
            completion_summary["total_minutes"] += moved_summary["total_minutes"]
            completion_summary["total_minutes_no_standby"] += moved_summary["total_minutes_no_standby"]
            completion_summary["has_work"] = completion_summary["has_work"] or moved_summary["has_work"]

    if completion_chains:
        display_days.append({
            "day": "",
            "day_name": "",
            "hebrew_date": "",
            "date_obj": None,
            "payment": completion_summary["payment"],
            "standby_payment": 0.0,
            "calc100": completion_summary["calc100"],
            "calc125": completion_summary["calc125"],
            "calc150": completion_summary["calc150"],
            "calc175": completion_summary["calc175"],
            "calc200": completion_summary["calc200"],
            "shift_names": [],
            "has_work": completion_summary["has_work"],
            "total_minutes": completion_summary["total_minutes"],
            "total_minutes_no_standby": completion_summary["total_minutes_no_standby"],
            "chains": completion_chains,
            "cancelled_standbys": [],
            "is_completion_section": True,
            "section_title": f"דירת {COMPLETION_APARTMENT_NAME}",
        })

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
                conn, daily_segments, person_id, selected_year, selected_month, MINIMUM_WAGE
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
    from core.time_utils import span_minutes, get_shabbat_times_cache
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
                r["shift_type_id"], segments_by_shift
            )
            for seg_data in tagbur_segs:
                work_hours = seg_data["work_hours"]
                standby_hours = seg_data["standby_hours"]
                total_work_hours += work_hours
                if standby_hours > 0:
                    standby_count += 1

                shifts_data.append({
                    "date": r_date.strftime("%d/%m/%y") if seg_data["is_first"] else "",
                    "day": _get_hebrew_day_name(r_date) if seg_data["is_first"] else "",
                    "apartment": _build_apartment_display_simple(r) if seg_data["is_first"] else "",
                    "shift_type": shift_name if seg_data["is_first"] else "",
                    "start_time": seg_data["display_start"],
                    "end_time": seg_data["display_end"],
                    "work_hours": work_hours,
                    "standby_hours": standby_hours,
                    "note": _compose_shift_note(r) if seg_data["is_first"] else "",
                    "tagbor_group": True,
                    "tagbor_first": seg_data["is_first"],
                    "tagbor_last": seg_data["is_last"],
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

            shifts_data.append({
                "date": r_date.strftime("%d/%m/%y"),
                "day": _get_hebrew_day_name(r_date),
                "apartment": _build_apartment_display_simple(r),
                "shift_type": shift_name,
                "start_time": r["start_time"][:5] if r["start_time"] else "",
                "end_time": r["end_time"][:5] if r["end_time"] else "",
                "work_hours": round(work_hours, 2),
                "standby_hours": round(standby_hours, 2),
                "note": _compose_shift_note(r),
            })

    # שליפת תשלומים נוספים (לפי דירות במערך כשמוגדר פילטר)
    if housing_filter is not None:
        payment_comps = conn.execute("""
            SELECT
                pc.quantity, pc.rate, pc.description,
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
                pct.name AS component_type_name
            FROM payment_components pc
            LEFT JOIN payment_component_types pct ON pc.component_type_id = pct.id
            WHERE pc.person_id = %s
              AND pc.date >= %s AND pc.date < %s
            ORDER BY pc.date
        """, (person_id, start_date, end_date)).fetchall()

    payments_by_type: Dict[str, float] = {}
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
        payments_by_type[key] = payments_by_type.get(key, 0) + amount
        # חישוב תוספות ללא נסיעות
        if not is_travel:
            total_additions_no_travel += amount

    payments_data = [
        {"description": desc, "amount": round(amt, 2)}
        for desc, amt in payments_by_type.items()
    ]

    # חישוב תעריפים משתנים מ-daily_segments
    MINIMUM_WAGE = get_minimum_wage_for_month(conn.conn, year, month)
    shabbat_cache = get_shabbat_times_cache(conn.conn)

    daily_segments, _ = get_daily_segments_data(
        conn, person_id, year, month, shabbat_cache, MINIMUM_WAGE
    )
    monthly_totals = aggregate_daily_segments_to_monthly(
        conn, daily_segments, person_id, year, month, MINIMUM_WAGE
    )
    _inject_holiday_payment(
        conn, monthly_totals, person_id,
        year, month, shabbat_cache,
        MINIMUM_WAGE, housing_filter,
    )

    # הוספת שורת תשלום חג לטבלת התשלומים
    if monthly_totals.get("holiday_payment"):
        hp_count = monthly_totals.get("holiday_payment_count", 0)
        payments_data.append({
            "description": f"תשלום חג ({hp_count} ימים)",
            "amount": round(monthly_totals["holiday_payment"], 2),
        })
        total_additions += monthly_totals["holiday_payment"]
        total_additions_no_travel += monthly_totals["holiday_payment"]

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

            if is_variable_rate:
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
                h100 = round(calc100 / 60, 2)
                h125 = round(calc125 / 60, 2)
                h150 = round(calc150 / 60, 2)
                h175 = round(calc175 / 60, 2)
                h200 = round(calc200 / 60, 2)

                gesher_payment = (
                    h100 * 1.0 * rounded_rate +
                    h125 * 1.25 * rounded_rate +
                    h150 * 1.5 * rounded_rate +
                    h175 * 1.75 * rounded_rate +
                    h200 * 2.0 * rounded_rate +
                    (chain.get("escort_bonus_pay", 0) or 0)
                )

                group_key = (chain_shift_name, rounded_rate)
                if group_key not in variable_by_shift:
                    variable_by_shift[group_key] = {
                        "shift_name": chain_shift_name,
                        "minutes": 0,
                        "shabbat_minutes": 0,
                        "payment": 0,
                        "rate": rounded_rate
                    }
                variable_by_shift[group_key]["minutes"] += total_minutes
                variable_by_shift[group_key]["shabbat_minutes"] += shabbat_minutes
                variable_by_shift[group_key]["payment"] += gesher_payment

    # בדיקה אילו משמרות יש להן תעריפים שונים
    shift_names_with_multiple_rates = set()
    shift_name_rates = {}
    for (shift_name, rate), data in variable_by_shift.items():
        if shift_name not in shift_name_rates:
            shift_name_rates[shift_name] = set()
        shift_name_rates[shift_name].add(rate)
    for shift_name, rates in shift_name_rates.items():
        if len(rates) > 1:
            shift_names_with_multiple_rates.add(shift_name)

    variable_shifts = []
    variable_rate_total_from_rows = 0.0  # סה"כ מחושב מהשורות המעוגלות
    for group_key, data in variable_by_shift.items():
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
            "payment": payment
        })
        variable_rate_total_from_rows += payment  # סכימת הערכים המעוגלים

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
    import subprocess
    import tempfile
    import os
    import time as time_module
    from jinja2 import Environment, FileSystemLoader

    temp_html_path = None
    temp_pdf_path = None

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

        # שמירה לקובץ זמני
        fd, temp_html_path = tempfile.mkstemp(suffix='.html')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(html_content)

        fd_pdf, temp_pdf_path = tempfile.mkstemp(suffix='.pdf')
        os.close(fd_pdf)

        # חיפוש דפדפן
        browser_paths = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
        ]

        browser_exe = None
        for path in browser_paths:
            if os.path.exists(path):
                browser_exe = path
                break

        if not browser_exe:
            logger.error("No suitable browser found for PDF generation")
            return None

        cmd = [
            browser_exe,
            "--headless",
            "--disable-gpu",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=10000",
            "--no-pdf-header-footer",
            f"--print-to-pdf={temp_pdf_path}",
            temp_html_path
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )

        try:
            stdout, stderr = process.communicate(timeout=45)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return None
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

        time_module.sleep(2)

        if os.path.exists(temp_pdf_path) and os.path.getsize(temp_pdf_path) > 0:
            with open(temp_pdf_path, "rb") as f:
                return f.read()
        return None

    except Exception as e:
        logger.error(f"Error generating shifts PDF: {e}", exc_info=True)
        return None

    finally:
        from services.email_service import safe_delete_file
        if temp_html_path:
            safe_delete_file(temp_html_path, initial_wait=1.0)
        if temp_pdf_path:
            safe_delete_file(temp_pdf_path, initial_wait=1.0)


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

        # קבלת מייל מותאם אישית מה-body
        custom_email = None
        try:
            body = await request.json()
            custom_email = body.get('email')
        except:
            pass

    except HTTPException as e:
        return JSONResponse({"success": False, "error": e.detail}, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Error in shifts_report_email setup: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": f"שגיאה: {str(e)}"})

    array_filter_for_thread = housing_filter

    def send_email_task(pid: int, y: int, m: int, email: Optional[str]) -> dict:
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

                # הכנת תוכן המייל
                subject = f"דוח משמרות - {person['name']} - {m:02d}/{y}"
                body_text = f"""שלום {person['name']},

מצורף דוח המשמרות שלך לחודש {m:02d}/{y}.

בברכה,
מדור שכר
צהר הלב

<span style="color: #888; font-size: 11px;">─────────────────────────────</span>
<span style="color: red; font-size: 11px;">הודעה זו נשלחה באופן אוטומטי. אין להשיב למייל זה.</span>
"""
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
            return {"success": False, "error": str(e)}
        finally:
            set_housing_array_filter(None)

    try:
        result = await asyncio.to_thread(send_email_task, person_id, year, month, custom_email)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Error in shifts_report_email execution: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": f"שגיאה בשליחת המייל: {str(e)}"})


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
    monthly_totals = aggregate_daily_segments_to_monthly(conn, daily_segments, person_id, year, month, MINIMUM_WAGE)
    _inject_holiday_payment(
        conn, monthly_totals, person_id,
        year, month, shabbat_cache,
        MINIMUM_WAGE, get_housing_array_filter(),
    )
    daily_segments = _prepare_daily_segments_for_display(daily_segments)

    return {
        "person": person,
        "daily_segments": daily_segments,
        "monthly_totals": monthly_totals,
        "selected_month": month,
        "selected_year": year,
        "generation_time": datetime.now().strftime("%d/%m/%Y %H:%M"),
    }


def _generate_chains_pdf(person_id: int, year: int, month: int) -> Optional[bytes]:
    """יצירת PDF לדוח רצפים באמצעות Edge/Chrome headless."""
    import subprocess
    import tempfile
    import os
    import time as time_module
    from jinja2 import Environment, FileSystemLoader

    temp_html_path = None
    temp_pdf_path = None

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

        fd, temp_html_path = tempfile.mkstemp(suffix='.html')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(html_content)

        fd_pdf, temp_pdf_path = tempfile.mkstemp(suffix='.pdf')
        os.close(fd_pdf)

        browser_paths = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
        ]

        browser_exe = None
        for path in browser_paths:
            if os.path.exists(path):
                browser_exe = path
                break

        if not browser_exe:
            logger.error("No suitable browser found for PDF generation")
            return None

        cmd = [
            browser_exe,
            "--headless",
            "--disable-gpu",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=10000",
            "--no-pdf-header-footer",
            f"--print-to-pdf={temp_pdf_path}",
            temp_html_path
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )

        try:
            stdout, stderr = process.communicate(timeout=45)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return None
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

        time_module.sleep(2)

        if os.path.exists(temp_pdf_path) and os.path.getsize(temp_pdf_path) > 0:
            with open(temp_pdf_path, "rb") as f:
                return f.read()
        return None

    except Exception as e:
        logger.error(f"Error generating chains PDF: {e}", exc_info=True)
        return None

    finally:
        from services.email_service import safe_delete_file
        if temp_html_path:
            safe_delete_file(temp_html_path, initial_wait=1.0)
        if temp_pdf_path:
            safe_delete_file(temp_pdf_path, initial_wait=1.0)


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
        except:
            pass

    except HTTPException as e:
        return JSONResponse({"success": False, "error": e.detail}, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Error in chains_report_email setup: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": f"שגיאה: {str(e)}"})

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
            return {"success": False, "error": str(e)}
        finally:
            set_housing_array_filter(None)

    try:
        result = await asyncio.to_thread(send_email_task, person_id, year, month, custom_email)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Error in chains_report_email execution: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": f"שגיאה בשליחת המייל: {str(e)}"})


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


def get_guide_notes(request: Request, person_id: int, year: int, month: int) -> JSONResponse:
    """API: שליפת הערות למדריך וחודש."""
    with get_conn() as conn:
        notes = _fetch_notes(conn, person_id, year, month)
    for n in notes:
        n["created_at"] = n["created_at"].strftime("%d/%m/%Y %H:%M") if n["created_at"] else ""
    return JSONResponse({"notes": notes})


async def add_guide_note(request: Request, person_id: int) -> JSONResponse:
    """API: הוספת הערה למדריך."""
    data = await request.json()
    content = (data.get("content") or "").strip()
    year = data.get("year")
    month = data.get("month")

    if not content:
        return JSONResponse({"success": False, "error": "תוכן ההערה ריק"}, status_code=400)

    user = getattr(request.state, "current_user", None)
    created_by = user["id"] if user else None

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
        conn.execute("DELETE FROM guide_monthly_notes WHERE id = %s", (note_id,))
        conn.conn.commit()

    return JSONResponse({"success": True})

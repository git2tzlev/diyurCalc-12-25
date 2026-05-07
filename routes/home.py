"""
Home page routes for DiyurCalc application.
"""
from __future__ import annotations

import time
import logging
from datetime import datetime, date
from typing import Optional

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from core.config import config
from core.database import get_conn, get_housing_array_filter, get_default_period, get_multi_housing_guides
from core.logic import get_active_guides
from core.report_presence import get_report_presence_counts
from core.time_utils import get_shabbat_times_cache
from core.holiday_payment import get_holiday_payment_setup
from utils.utils import month_range_ts, available_months_from_db, format_currency, human_date

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
templates.env.filters["format_currency"] = format_currency
templates.env.filters["human_date"] = human_date
templates.env.globals["app_version"] = config.VERSION


def home(
    request: Request,
    month: Optional[int] = None,
    year: Optional[int] = None,
    q: Optional[str] = None
) -> HTMLResponse:
    """Home page route showing guides and monthly overview."""
    func_start_time = time.time()
    logger.info(f"Starting home for month={month}, year={year}, q={q}")

    # קבלת פילטר מערך דיור (לשימוש בשאילתות)
    housing_filter = get_housing_array_filter()

    guides_start = time.time()
    guides = get_active_guides(housing_filter)
    logger.info(f"get_active_guides took: {time.time() - guides_start:.4f}s")

    months_start = time.time()
    months_all = available_months_from_db(housing_filter)
    logger.info(f"available_months_from_db took: {time.time() - months_start:.4f}s")

    if months_all:
        if month is None or year is None:
            # נסה לקרוא מהעוגייה, אחרת חודש קודם
            default_year, default_month = get_default_period(request)
            # בדוק שהחודש קיים בנתונים
            if (default_year, default_month) in months_all:
                selected_year, selected_month = default_year, default_month
            else:
                selected_year, selected_month = months_all[-1]
        else:
            selected_year, selected_month = year, month
    else:
        selected_year = selected_month = None

    months_options = [{"year": y, "month": m, "label": f"{m:02d}/{y}"} for y, m in months_all]
    years_options = sorted({y for y, _ in months_all}, reverse=True)

    counts: dict[int, int] = {}
    notes_counts: dict[int, int] = {}
    has_payment_components: set[int] = set()
    multi_housing: dict[int, list[str]] = {}
    holiday_payment_setup: dict | None = None
    if selected_year and selected_month:
        start_dt, end_dt = month_range_ts(selected_year, selected_month)
        # Convert datetime to date for PostgreSQL date column
        start_date = start_dt.date()
        end_date = end_dt.date()
        counts_start = time.time()
        with get_conn() as conn:
            counts, has_payment_components = get_report_presence_counts(
                conn, start_date, end_date, housing_filter,
            )

            for row in conn.execute(
                """
                SELECT person_id, COUNT(*) AS cnt
                FROM guide_monthly_notes
                WHERE year = %s AND month = %s
                GROUP BY person_id
                """,
                (selected_year, selected_month),
            ):
                notes_counts[row["person_id"]] = row["cnt"]

            multi_housing = get_multi_housing_guides(conn, start_date, end_date)
            shabbat_cache = get_shabbat_times_cache(conn.conn)
            holiday_payment_setup = get_holiday_payment_setup(
                conn.conn, selected_year, selected_month, shabbat_cache, housing_filter,
            )
        logger.info(f"Counts query took: {time.time() - counts_start:.4f}s")

    # Calculate seniority years for each guide
    reference_date = datetime.now(config.LOCAL_TZ).date()
    if selected_year and selected_month:
        reference_date = datetime(selected_year, selected_month, 1, tzinfo=config.LOCAL_TZ).date()

    allowed_types = {"permanent", "substitute"}
    guides_filtered = []
    q_norm = q.lower().strip() if q else None
    for g in guides:
        if g["type"] not in allowed_types:
            continue
        if q_norm and q_norm not in (g["name"] or "").lower():
            continue

        if selected_year and selected_month:
            # הצג מדריכים עם משמרות או רכיבי תשלום
            # (כשיש סינון לפי מערך דיור, has_payment_components כבר מסונן)
            if counts.get(g["id"], 0) < 1 and g["id"] not in has_payment_components:
                continue

        # Calculate seniority years
        seniority_years = None
        if g.get("start_date"):
            try:
                # Handle datetime, date objects (from psycopg2) and timestamp (int/float)
                if isinstance(g["start_date"], datetime):
                    start_dt = g["start_date"].date()
                elif isinstance(g["start_date"], date):
                    start_dt = g["start_date"]
                else:
                    # Assume it's a timestamp
                    start_dt = datetime.fromtimestamp(g["start_date"], config.LOCAL_TZ).date()
                diff = reference_date - start_dt
                seniority_years = diff.days / 365.25
                if seniority_years < 0:
                    seniority_years = 0
            except Exception as e:
                logger.warning(f"Error calculating seniority for guide {g.get('id')} ({g.get('name')}): {e}, start_date type: {type(g.get('start_date'))}, value: {g.get('start_date')}")
                seniority_years = None

        guide_dict = dict(g)
        guide_dict["seniority_years"] = seniority_years
        guides_filtered.append(guide_dict)

    render_start = time.time()
    response = templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "guides": guides_filtered,
            "months": months_options,
            "years": years_options,
            "selected_year": selected_year,
            "selected_month": selected_month,
            "counts": counts,
            "notes_counts": notes_counts,
            "multi_housing": multi_housing,
            "q": q or "",
            "holiday_payment_setup": holiday_payment_setup,
        },
    )
    render_time = time.time() - render_start
    logger.info(f"Template rendering took: {render_time:.4f}s")

    total_time = time.time() - func_start_time
    logger.info(f"Total home execution time: {total_time:.4f}s")

    return response

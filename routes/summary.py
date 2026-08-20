"""
Summary routes for DiyurCalc application.
Contains general summary and export functionality.
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from core.config import config
from core.database import get_conn, get_default_period, get_housing_array_filter
from core.logic import (
    get_payment_codes,
    calculate_monthly_summary,
)
from core.history import get_minimum_wage_for_month
from services.gesher_difference import build_approved_completion_gesher_rows
from services.gesher_exporter import (
    apply_completion_rows_to_summary_data,
    with_completion_export_codes,
)
from utils.utils import format_currency, human_date, person_matches_search
import logging

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
templates.env.filters["format_currency"] = format_currency
templates.env.filters["human_date"] = human_date
templates.env.globals["app_version"] = config.VERSION


def general_summary(
    request: Request,
    year: Optional[int] = None,
    month: Optional[int] = None,
    q: Optional[str] = None
) -> HTMLResponse:
    """General monthly summary view."""
    start_time = time.time()
    logger.info(f"Starting general_summary for {month}/{year}, filter: {q}")

    # Set default date if not provided - נסה לקרוא מהעוגייה, אחרת חודש קודם
    if year is None or month is None:
        default_year, default_month = get_default_period(request)
        if year is None:
            year = default_year
        if month is None:
            month = default_month

    conn_start = time.time()
    with get_conn() as conn:
        conn_time = time.time() - conn_start
        logger.info(f"Database connection took: {conn_time:.4f}s")
        
        # 1. Fetch Payment Codes
        payment_start = time.time()
        payment_codes = get_payment_codes(conn.conn)
        payment_time = time.time() - payment_start
        logger.info(f"Payment codes fetch took: {payment_time:.4f}s")

        # Get minimum wage for the month
        minimum_wage = get_minimum_wage_for_month(conn.conn, year, month)

        pre_calc_time = time.time()
        logger.info("Starting optimized calculation...")

        # Use optimized bulk calculation
        summary_data, grand_totals = calculate_monthly_summary(conn.conn, year, month)
        completion_result = build_approved_completion_gesher_rows(
            conn,
            year,
            month,
            housing_array_id=get_housing_array_filter(),
        )
        apply_completion_rows_to_summary_data(
            summary_data,
            grand_totals,
            completion_result["rows"],
        )
        export_codes = {
            str(code.get("merav_code") or ""): (
                code.get("internal_key"), "money", code.get("display_name")
            )
            for code in payment_codes
            if code.get("merav_code")
        }
        completion_codes = with_completion_export_codes(export_codes)
        existing_keys = {code.get("internal_key") for code in payment_codes}
        for symbol in ("253", "317"):
            internal_key, _value_type, display_name = completion_codes[symbol]
            if internal_key not in existing_keys:
                payment_codes.append({
                    "internal_key": internal_key,
                    "display_name": display_name,
                    "merav_code": symbol,
                    "display_order": 190 if symbol == "253" else 191,
                })

        loop_time = time.time() - pre_calc_time
        logger.info(f"Optimized calculation took: {loop_time:.4f}s")

    # סינון לפי שם, מספר עובד או מספר זהות
    filtered_summary_data = summary_data
    if q and q.strip():
        filtered_summary_data = [
            row for row in summary_data
            if person_matches_search(
                q,
                row.get("name"),
                row.get("merav_code") or row.get("meirav_code"),
                row.get("id_number"),
            )
        ]
        logger.info(f"Filtered {len(summary_data)} -> {len(filtered_summary_data)} results")

    year_options = [2023, 2024, 2025, 2026]
    
    render_start = time.time()
    response = templates.TemplateResponse("general_summary.html", {
        "request": request,
        "payment_codes": payment_codes,
        "summary_data": filtered_summary_data,
        "grand_totals": grand_totals,
        "selected_year": year,
        "selected_month": month,
        "search_query": q or "",
        "years": year_options,
        "minimum_wage": minimum_wage
    })
    render_time = time.time() - render_start
    logger.info(f"Template rendering took: {render_time:.4f}s")
    
    total_time = time.time() - start_time
    logger.info(f"Total general_summary execution time: {total_time:.4f}s")
    
    return response

"""
Reports management routes for DiyurCalc application.
דף ניהול דוחות - שליחת דוחות מדריכים במייל.
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from core.config import config
from core.database import get_conn, get_housing_array_filter, get_default_period
from core.logic import get_active_guides
from core.report_presence import get_report_presence_counts
from core.auth import create_action_token, get_user_housing_array
from routes.guide import prepare_guide_pdf_data
from services.guide_reports_excel_export import build_guide_reports_excel
from utils.utils import month_range_ts, available_months_from_db, format_currency, human_date

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
templates.env.filters["format_currency"] = format_currency
templates.env.filters["human_date"] = human_date
templates.env.globals["app_version"] = config.VERSION


def _effective_reports_housing_filter(
    request: Request,
    housing_array_id: Optional[int] = None,
) -> Optional[int]:
    managed_array = get_user_housing_array(request)
    if managed_array is not None:
        return managed_array
    return housing_array_id if housing_array_id is not None else get_housing_array_filter()


def _guides_with_reports_for_period(
    *,
    year: int,
    month: int,
    housing_array_id: Optional[int],
) -> list[dict]:
    start_dt, end_dt = month_range_ts(year, month)
    start_date = start_dt.date()
    end_date = end_dt.date()

    guides = get_active_guides(housing_array_id)
    with get_conn() as conn:
        counts, has_payment_components = get_report_presence_counts(
            conn, start_date, end_date, housing_array_id,
        )

    allowed_types = {"permanent", "substitute"}
    return [
        g for g in guides
        if g["type"] in allowed_types
        and (counts.get(g["id"], 0) >= 1 or g["id"] in has_payment_components)
    ]


def reports_management(
    request: Request,
    month: Optional[int] = None,
    year: Optional[int] = None,
    housing_array_id: Optional[int] = None
) -> HTMLResponse:
    """
    דף ניהול דוחות - שליחת דוחות מדריכים במייל.

    מציג את כל המדריכים עם דוחות לחודש הנבחר ומאפשר:
    - שליחת כל הדוחות למייל אחד (PDF משולב)
    - שליחת דוח לכל מדריך למייל שלו
    - שליחת דוח בודד למייל מותאם
    """
    managed_array = get_user_housing_array(request)

    # שליפת מערכי דיור (מנהל מסגרת — רק המערך שלו)
    housing_arrays = []
    with get_conn() as conn:
        if managed_array is not None:
            row = conn.execute(
                "SELECT id, name FROM housing_arrays WHERE id = %s",
                (managed_array,),
            ).fetchone()
            housing_arrays = (
                [{"id": row["id"], "name": row["name"]}] if row else []
            )
        else:
            rows = conn.execute(
                "SELECT id, name FROM housing_arrays ORDER BY name"
            ).fetchall()
            housing_arrays = [{"id": r["id"], "name": r["name"]} for r in rows]

    # שימוש בפילטר מהפרמטר או מהעוגייה; מנהל מסגרת תמיד מוגבל למערך שלו
    effective_filter = _effective_reports_housing_filter(request, housing_array_id)

    # שליפת חודשים זמינים
    months_all = available_months_from_db(effective_filter)

    if months_all:
        if month is None or year is None:
            # נסה לקרוא מהעוגייה, אחרת חודש קודם
            default_year, default_month = get_default_period(request)
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

    # שליפת מדריכים עם דוחות לחודש הנבחר
    guides_with_reports = []
    counts: dict[int, int] = {}
    has_payment_components: set[int] = set()

    if selected_year and selected_month:
        start_dt, end_dt = month_range_ts(selected_year, selected_month)
        start_date = start_dt.date()
        end_date = end_dt.date()
        guides = _guides_with_reports_for_period(
            year=selected_year,
            month=selected_month,
            housing_array_id=effective_filter,
        )
        with get_conn() as conn:
            counts, has_payment_components = get_report_presence_counts(
                conn, start_date, end_date, effective_filter,
            )

        # סינון מדריכים עם דוחות בלבד
        allowed_types = {"permanent", "substitute"}
        for g in guides:
            if g["type"] not in allowed_types:
                continue
            if counts.get(g["id"], 0) < 1 and g["id"] not in has_payment_components:
                continue

            guides_with_reports.append({
                "id": g["id"],
                "name": g["name"],
                "email": g.get("email") or "",
                "type": "קבוע" if g["type"] == "permanent" else "מחליף",
                "shift_count": counts.get(g["id"], 0),
                "has_email": bool(g.get("email")),
            })

    guides_with_email = sum(1 for g in guides_with_reports if g["has_email"])

    return templates.TemplateResponse(
        "reports.html",
        {
            "request": request,
            "guides": guides_with_reports,
            "months": months_options,
            "years": years_options,
            "selected_year": selected_year,
            "selected_month": selected_month,
            "total_guides": len(guides_with_reports),
            "guides_with_email": guides_with_email,
            "housing_arrays": housing_arrays,
            "selected_housing_array": effective_filter,
            "reports_array_locked": managed_array is not None,
            "bulk_send_token": create_action_token(request, "bulk_send"),
            "retry_failed_token": create_action_token(request, "retry_failed_email"),
        }
    )


def export_guide_reports_excel(
    request: Request,
    year: int,
    month: int,
    housing_array_id: Optional[int] = None,
) -> Response:
    """Export guide monthly reports as an Excel file for the external system."""
    effective_filter = _effective_reports_housing_filter(request, housing_array_id)
    guides = _guides_with_reports_for_period(
        year=year,
        month=month,
        housing_array_id=effective_filter,
    )

    current_user = getattr(request.state, "current_user", None)
    exported_by = current_user.get("name") if current_user else ""
    guide_reports = []
    selected_housing_array_name = ""

    with get_conn() as conn:
        if effective_filter is not None:
            ha = conn.execute(
                "SELECT name FROM housing_arrays WHERE id = %s",
                (effective_filter,),
            ).fetchone()
            selected_housing_array_name = ha["name"] if ha else ""

        for guide in guides:
            person = conn.execute("""
                SELECT p.id, p.id_number, p.meirav_code, p.name, p.email, p.type,
                       p.housing_array_id, ha.name AS housing_array_name
                FROM people p
                LEFT JOIN housing_arrays ha ON ha.id = p.housing_array_id
                WHERE p.id = %s
            """, (guide["id"],)).fetchone()
            if not person:
                continue
            pdf_data = prepare_guide_pdf_data(conn, guide["id"], year, month, effective_filter)
            if not pdf_data:
                continue
            guide_reports.append({
                "person": dict(person),
                "pdf_data": pdf_data,
            })

    excel_bytes = build_guide_reports_excel(
        year=year,
        month=month,
        exported_by=exported_by,
        selected_housing_array_id=effective_filter,
        selected_housing_array_name=selected_housing_array_name,
        guide_reports=guide_reports,
    )
    filename = f"guide_reports_{year}_{month:02d}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )

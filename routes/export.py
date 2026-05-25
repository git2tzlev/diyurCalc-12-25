"""
Export routes for DiyurCalc application.
Contains file export functionality for various formats.
"""
from __future__ import annotations

import calendar
from datetime import datetime, date, timedelta
from typing import Optional, List
from urllib.parse import quote

from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from core.config import config
from core.database import get_conn, get_housing_array_filter, get_default_period, get_multi_housing_guides
from core.logic import calculate_monthly_summary
from core.auth import enforce_housing_filter_guide_access
from services import gesher_exporter
from services.gesher_archive import (
    get_gesher_export_file,
    list_gesher_export_files,
    save_gesher_export_file,
    set_gesher_export_status,
    update_gesher_export_note,
)
from utils.utils import format_currency, human_date


def _validate_guide_access(person_id: int, housing_filter: Optional[int]) -> None:
    """בדיקת הרשאת ייצוא מדריך לפי פילטר מערך דיור."""
    enforce_housing_filter_guide_access(
        person_id,
        housing_filter,
        message="אין הרשאה לייצא מדריך זה",
    )


def _filter_multi_housing_for_summary(
    multi_housing: dict[int, list[str]],
    summary_data: list[dict],
) -> dict[int, list[str]]:
    """Limit multi-housing warnings to people visible in the current summary."""
    visible_person_ids = {
        row.get("person_id") or row.get("id")
        for row in summary_data
        if row.get("person_id") or row.get("id")
    }
    return {
        person_id: arrays
        for person_id, arrays in multi_housing.items()
        if person_id in visible_person_ids
    }


def _build_blocked_multi_housing_warnings(
    preview: list[dict],
    blocked_multi_housing: dict[int, list[str]],
) -> list[dict]:
    """Build warning rows for people blocked from Gesher export."""
    preview_by_id = {person["person_id"]: person for person in preview}
    warnings = []
    for person_id, arrays in blocked_multi_housing.items():
        person = preview_by_id.get(person_id)
        if not person:
            continue
        warnings.append({
            "person_id": person_id,
            "name": person["name"],
            "meirav_code": person["meirav_code"],
            "arrays": arrays,
            "reason": gesher_exporter.get_blocked_multi_housing_reason(arrays),
        })
    return warnings


def _remove_blocked_preview_people(
    preview: list[dict],
    blocked_multi_housing: dict[int, list[str]],
) -> list[dict]:
    """Remove people that must not be exported from the selectable preview table."""
    if not blocked_multi_housing:
        return preview
    blocked_ids = set(blocked_multi_housing)
    return [person for person in preview if person["person_id"] not in blocked_ids]


templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
templates.env.filters["format_currency"] = format_currency
templates.env.filters["human_date"] = human_date
templates.env.globals["app_version"] = config.VERSION


def _current_user_id(request: Request) -> Optional[int]:
    user = getattr(request.state, "current_user", None)
    return user.get("person_id") if user else None


def _archive_gesher_file(
    conn,
    request: Request,
    *,
    year: int,
    month: int,
    company_code: Optional[str],
    export_scope: str,
    filename: str,
    content: str,
    encoding: str,
    person_ids: Optional[List[int]] = None,
    housing_array_id: Optional[int] = None,
) -> None:
    """Best-effort archive of a generated Gesher file."""
    try:
        save_gesher_export_file(
            conn,
            year=year,
            month=month,
            company_code=company_code,
            housing_array_id=(
                housing_array_id
                if housing_array_id is not None
                else get_housing_array_filter()
            ),
            export_scope=export_scope,
            filename=filename,
            content=content,
            encoding=encoding,
            created_by=_current_user_id(request),
            person_ids=person_ids,
        )
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "Could not archive Gesher export file %s", filename, exc_info=True
        )


def export_gesher(
    request: Request,
    year: int,
    month: int,
    company: Optional[str] = None,
    filter_name: Optional[str] = None,
    encoding: str = "ascii"
) -> Response:
    """
    ייצוא קובץ גשר למירב - לפי מפעל
    company: קוד מפעל (001 או 400)
    encoding: קידוד הקובץ (ascii / windows-1255 / utf-8)
    """
    if not company:
        raise HTTPException(status_code=400, detail="חובה לבחור מפעל")

    with get_conn() as conn:
        content = gesher_exporter.generate_gesher_file(conn, year, month, filter_name, company)

        # שם קובץ עם קוד מפעל
        filename = f"gesher_{company}_{year}_{month:02d}.mrv"
        if content:
            _archive_gesher_file(
                conn,
                request,
                year=year,
                month=month,
                company_code=company,
                export_scope="company",
                filename=filename,
                content=content,
                encoding=encoding,
            )

    # קידוד הקובץ
    encoded_content = content.encode(encoding, errors='replace')

    return Response(
        content=encoded_content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Type": f"text/plain; charset={encoding}"
        }
    )


def export_gesher_person(
    request: Request,
    person_id: int,
    year: int,
    month: int,
    encoding: str = "ascii"
) -> Response:
    """
    ייצוא קובץ גשר לעובד בודד
    """
    # בדיקת הרשאה - מנהל מסגרת יכול לייצא רק מדריכים מהמערך שלו
    housing_filter = get_housing_array_filter()
    _validate_guide_access(person_id, housing_filter)

    with get_conn() as conn:
        blocked = gesher_exporter.get_blocked_multi_housing_for_gesher(
            conn, year, month, [person_id],
        )
        if person_id in blocked:
            raise HTTPException(
                status_code=400,
                detail=f"לא ניתן לייצא מדריך זה לגשר: {gesher_exporter.get_blocked_multi_housing_reason(blocked[person_id])}",
            )

        # שליפת שם העובד לשם הקובץ
        person = conn.execute(
            "SELECT name, meirav_code, housing_array_id FROM people WHERE id = %s",
            (person_id,),
        ).fetchone()
        if not person:
            raise HTTPException(status_code=404, detail="עובד לא נמצא")

        content, company = gesher_exporter.generate_gesher_file_for_person(conn, person_id, year, month)

    if not content:
        raise HTTPException(status_code=400, detail="לא ניתן לייצר קובץ - אין קוד מירב לעובד")

    encoded_content = content.encode(encoding, errors='replace')

    # שם קובץ - שימוש בקוד מירב במקום שם (כי זה תמיד ASCII)
    meirav_code = person['meirav_code'] or person_id
    filename = f"gesher_{meirav_code}_{year}_{month:02d}.mrv"

    with get_conn() as conn:
        _archive_gesher_file(
            conn,
            request,
            year=year,
            month=month,
            company_code=company,
            export_scope="person",
            filename=filename,
            content=content,
            encoding=encoding,
            person_ids=[person_id],
            housing_array_id=person.get("housing_array_id"),
        )

    # לשם התצוגה בדפדפן - שם מקודד ב-URL encoding
    display_name = quote(person['name'], safe='')

    return Response(
        content=encoded_content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename={filename}; filename*=UTF-8''{display_name}_{year}_{month:02d}.mrv",
            "Content-Type": f"text/plain; charset={encoding}"
        }
    )


def export_gesher_multiple(
    request: Request,
    person_ids: List[int],
    year: int,
    month: int,
    encoding: str = "ascii"
) -> Response:
    """
    ייצוא קובץ גשר ממוזג למספר עובדים נבחרים
    """
    # בדיקת הרשאה - מנהל מסגרת יכול לייצא רק מדריכים מהמערך שלו
    housing_filter = get_housing_array_filter()
    for person_id in person_ids:
        _validate_guide_access(person_id, housing_filter)

    with get_conn() as conn:
        blocked = gesher_exporter.get_blocked_multi_housing_for_gesher(conn, year, month, person_ids)
        exportable_person_ids = [pid for pid in person_ids if pid not in blocked]
        if not exportable_person_ids:
            raise HTTPException(
                status_code=400,
                detail="כל המדריכים שנבחרו לא עוברים לקובץ גשר כי הם פעילים ביותר ממערך דיור בחודש זה",
            )
        content, company = gesher_exporter.generate_gesher_file_for_multiple(conn, exportable_person_ids, year, month)

    if not content:
        raise HTTPException(status_code=400, detail="לא נוצרו נתונים - אין קוד מירב לעובדים שנבחרו")

    # קידוד הקובץ
    encoded_content = content.encode(encoding, errors='replace')

    # שם קובץ עם קוד מפעל
    filename = f"gesher_{company}_{year}_{month:02d}.mrv"

    with get_conn() as conn:
        _archive_gesher_file(
            conn,
            request,
            year=year,
            month=month,
            company_code=company,
            export_scope="multiple",
            filename=filename,
            content=content,
            encoding=encoding,
            person_ids=exportable_person_ids,
        )

    return Response(
        content=encoded_content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Type": f"text/plain; charset={encoding}"
        }
    )


def export_gesher_preview(
    request: Request,
    year: Optional[int] = None,
    month: Optional[int] = None,
    show_zero: Optional[str] = None
) -> HTMLResponse:
    """תצוגה מקדימה של ייצוא גשר"""
    if year is None or month is None:
        default_year, default_month = get_default_period(request)
        if year is None:
            year = default_year
        if month is None:
            month = default_month

    show_zero_flag = show_zero == "1"

    with get_conn() as conn:
        raw_conn = conn.conn if hasattr(conn, 'conn') else conn
        summary_data, _ = calculate_monthly_summary(raw_conn, year, month)
        preview = gesher_exporter.get_export_preview(
            conn,
            year,
            month,
            limit=100,
            summary_data=summary_data
        )
        export_codes = gesher_exporter.load_export_config_from_db(conn)
        if not export_codes:
            export_codes = gesher_exporter.load_export_config()
        # שליפת מפעלים - רק אלו שיש להם עובדים במערך הנבחר
        housing_filter = get_housing_array_filter()
        if housing_filter is not None:
            employers = conn.execute("""
                SELECT DISTINCT e.code, e.name
                FROM employers e
                JOIN people p ON p.employer_id = e.id
                WHERE e.is_active::integer = 1 AND p.housing_array_id = %s
                ORDER BY e.code
            """, (housing_filter,)).fetchall()
        else:
            employers = conn.execute(
                "SELECT code, name FROM employers WHERE is_active::integer = 1 ORDER BY code"
            ).fetchall()

        # זיהוי מדריכים במספר מערכי דיור
        start_date = date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        end_date = date(year, month, last_day) + timedelta(days=1)
        multi_housing = get_multi_housing_guides(conn, start_date, end_date)
        multi_housing = _filter_multi_housing_for_summary(multi_housing, summary_data)
        blocked_multi_housing = gesher_exporter.get_blocked_multi_housing_for_gesher(conn, year, month)
        blocked_multi_housing = _filter_multi_housing_for_summary(blocked_multi_housing, summary_data)

    missing_merav_list = []
    for person_data in summary_data:
        meirav_code = person_data.get('merav_code') or person_data.get('meirav_code')
        if not meirav_code:
            missing_merav_list.append({
                'id': person_data.get('person_id') or person_data.get('id'),
                'name': person_data.get('name', '')
            })
    missing_merav_count = len(missing_merav_list)

    # אם לא מבקשים להציג ערכים 0, מסננים שורות ועובדים ללא נתונים
    if not show_zero_flag:
        filtered_preview = []
        for person in preview:
            # סינון שורות: לכסף - בודקים payment, לשאר - בודקים quantity
            non_zero_lines = [
                line for line in person['lines']
                if (line['type'] == 'money' and line['payment'] > 0) or
                   (line['type'] != 'money' and line['quantity'] > 0)
            ]
            if non_zero_lines:
                filtered_preview.append({
                    'person_id': person['person_id'],
                    'name': person['name'],
                    'meirav_code': person['meirav_code'],
                    'lines': non_zero_lines
                })
        preview = filtered_preview

    blocked_gesher_list = _build_blocked_multi_housing_warnings(preview, blocked_multi_housing)
    preview = _remove_blocked_preview_people(preview, blocked_multi_housing)
    if blocked_multi_housing:
        multi_housing = {
            person_id: arrays
            for person_id, arrays in multi_housing.items()
            if person_id not in blocked_multi_housing
        }

    return templates.TemplateResponse("gesher_preview.html", {
        "request": request,
        "preview": preview,
        "export_codes": export_codes,
        "employers": employers,
        "selected_year": year,
        "selected_month": month,
        "show_zero": show_zero_flag,
        "years": list(range(2023, 2027)),
        "missing_merav_count": missing_merav_count,
        "missing_merav_list": missing_merav_list,
        "multi_housing": multi_housing,
        "blocked_gesher_list": blocked_gesher_list,
    })


def export_excel(year: Optional[int] = None, month: Optional[int] = None) -> Response:
    """ייצוא סיכום חודשי לאקסל"""
    now = datetime.now(config.LOCAL_TZ)
    if year is None:
        year = now.year
    if month is None:
        month = now.month

    from core.logic import calculate_monthly_summary
    import pandas as pd
    from io import BytesIO

    with get_conn() as conn:
        summary_data, grand_totals = calculate_monthly_summary(conn.conn, year, month)

    # Create Excel file
    output = BytesIO()

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Main summary sheet
        summary_rows = []
        for person_data in summary_data:
            totals = person_data.get('totals', {})
            row = {
                'שם': person_data.get('name', ''),
                'קוד מירב': person_data.get('merav_code', ''),
                'שעות עבודה': round(totals.get('total_hours', 0) / 60, 2),
                'תשלום': round(totals.get('rounded_total', 0) or totals.get('total_payment', 0), 2),
                'כוננויות': totals.get('standby', 0),
                'תשלום כוננויות': round(totals.get('standby_payment', 0), 2),
                'ימי עבודה': totals.get('actual_work_days', 0),
                'חופשה נוצלה': totals.get('vacation_days_taken', 0),
                'שעות 100%': round(totals.get('calc100', 0) / 60, 2),
                'שעות 125%': round(totals.get('calc125', 0) / 60, 2),
                'שעות 150%': round(totals.get('calc150', 0) / 60, 2),
                'שעות 175%': round(totals.get('calc175', 0) / 60, 2),
                'שעות 200%': round(totals.get('calc200', 0) / 60, 2),
                'נסיעות': round(totals.get('travel', 0), 2),
                'תוספות': round(totals.get('extras', 0), 2),
                'ת.מקצועי': round(totals.get('professional_support', 0), 2),
                'סה"כ': round(totals.get('rounded_total', 0), 2),
            }
            summary_rows.append(row)

        if summary_rows:
            df_summary = pd.DataFrame(summary_rows)
            df_summary.to_excel(writer, sheet_name='סיכום חודשי', index=False)
        else:
            # Create empty sheet with headers if no data
            df_empty = pd.DataFrame(columns=['שם', 'קוד מירב', 'שעות עבודה', 'תשלום'])
            df_empty.to_excel(writer, sheet_name='סיכום חודשי', index=False)

        # Grand totals sheet
        grand_totals_data = [{
            'סה"כ שעות עבודה': round(grand_totals.get('total_hours', 0) / 60, 2),
            'סה"כ לתשלום': round(grand_totals.get('rounded_total', 0) or grand_totals.get('payment', 0), 2),
            'סה"כ כוננויות': grand_totals.get('standby', 0),
            'תשלום כוננויות': round(grand_totals.get('standby_payment', 0), 2),
            'ימי עבודה': grand_totals.get('actual_work_days', 0),
            'חופשה נוצלה': grand_totals.get('vacation_days_taken', 0),
        }]
        df_totals = pd.DataFrame(grand_totals_data)
        df_totals.to_excel(writer, sheet_name='סיכום כללי', index=False)

    output.seek(0)

    filename = f"summary_{year}_{month:02d}.xlsx"
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


def gesher_archive_page(
    request: Request,
    year: Optional[int] = None,
    month: Optional[int] = None,
    company: Optional[str] = None,
) -> HTMLResponse:
    """Management page for archived Gesher export files."""
    housing_filter = get_housing_array_filter()
    with get_conn() as conn:
        files = list_gesher_export_files(
            conn,
            year=year,
            month=month,
            company_code=company,
            housing_array_id=housing_filter,
        )
        employers = conn.execute(
            "SELECT code, name FROM employers WHERE is_active::integer = 1 ORDER BY code"
        ).fetchall()

    return templates.TemplateResponse("gesher_archive.html", {
        "request": request,
        "files": files,
        "selected_year": year,
        "selected_month": month,
        "selected_company": company or "",
        "years": list(range(2023, 2028)),
        "employers": employers,
    })


def download_gesher_archive_file(request: Request, file_id: int) -> Response:
    """Download an archived Gesher file."""
    housing_filter = get_housing_array_filter()
    with get_conn() as conn:
        file_row = get_gesher_export_file(conn, file_id, housing_array_id=housing_filter)
    if not file_row:
        raise HTTPException(status_code=404, detail="קובץ גשר לא נמצא")

    encoding = file_row.get("encoding") or "ascii"
    filename = file_row.get("filename") or f"gesher_{file_id}.mrv"
    return Response(
        content=(file_row.get("content") or "").encode(encoding, errors="replace"),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename={quote(filename)}",
            "Content-Type": f"text/plain; charset={encoding}",
        },
    )


def view_gesher_archive_file(request: Request, file_id: int) -> HTMLResponse:
    """View an archived Gesher file content."""
    housing_filter = get_housing_array_filter()
    with get_conn() as conn:
        file_row = get_gesher_export_file(conn, file_id, housing_array_id=housing_filter)
    if not file_row:
        raise HTTPException(status_code=404, detail="קובץ גשר לא נמצא")

    return templates.TemplateResponse("gesher_archive_view.html", {
        "request": request,
        "file": file_row,
    })


async def update_gesher_archive_note(request: Request, file_id: int) -> RedirectResponse:
    """Update an archived Gesher file note."""
    form = await request.form()
    notes = (form.get("notes") or "").strip()
    housing_filter = get_housing_array_filter()
    with get_conn() as conn:
        updated = update_gesher_export_note(
            conn,
            file_id,
            notes,
            updated_by=_current_user_id(request),
            housing_array_id=housing_filter,
        )
    if not updated:
        raise HTTPException(status_code=404, detail="קובץ גשר לא נמצא")
    return RedirectResponse(url="/admin/gesher-files", status_code=303)


async def update_gesher_archive_status(request: Request, file_id: int) -> RedirectResponse:
    """Update archived Gesher file status."""
    form = await request.form()
    status = (form.get("status") or "").strip()
    housing_filter = get_housing_array_filter()
    with get_conn() as conn:
        updated = set_gesher_export_status(
            conn,
            file_id,
            status,
            updated_by=_current_user_id(request),
            housing_array_id=housing_filter,
        )
    if not updated:
        raise HTTPException(status_code=404, detail="קובץ גשר לא נמצא")
    return RedirectResponse(url="/admin/gesher-files", status_code=303)

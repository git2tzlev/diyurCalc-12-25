"""
Email routes for DiyurCalc application.
Contains routes for email settings management and sending guide reports.
פונקציות הגדרות מייל דורשות הרשאת מנהל על (super_admin).
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator

from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import StreamingResponse

from core.config import config
from core.database import get_conn
from core.auth import is_super_admin
from services.email_service import (
    get_email_settings,
    save_email_settings,
    test_email_connection,
    send_test_email,
    send_guide_email,
    send_all_guides_email,
    send_all_guides_to_single_email,
    send_selected_guides_email,
    send_selected_guides_to_single_email,
    generate_batch_id,
    process_guide_for_bulk,
    get_email_logs,
    get_batch_summary,
    insert_email_log,
)

from utils.utils import format_currency, human_date

logger = logging.getLogger(__name__)


def _require_super_admin(request: Request) -> None:
    """בודק שהמשתמש הוא מנהל על, אחרת זורק שגיאה 403."""
    if not is_super_admin(request):
        raise HTTPException(status_code=403, detail="אין הרשאה - נדרש מנהל על")

templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
templates.env.filters["format_currency"] = format_currency
templates.env.filters["human_date"] = human_date
templates.env.globals["app_version"] = config.VERSION


def email_settings_page(request: Request) -> HTMLResponse:
    """Display email settings management page. רק למנהל על."""
    _require_super_admin(request)
    with get_conn() as conn:
        settings = get_email_settings(conn)

    return templates.TemplateResponse(
        "email_settings.html",
        {
            "request": request,
            "settings": settings or {},
        }
    )


async def update_email_settings(request: Request) -> RedirectResponse:
    """Update email settings from form submission. רק למנהל על."""
    _require_super_admin(request)
    try:
        form_data = await request.form()

        settings = {
            "smtp_host": form_data.get("smtp_host", ""),
            "smtp_port": int(form_data.get("smtp_port", 587)),
            "smtp_user": form_data.get("smtp_user", ""),
            "smtp_password": form_data.get("smtp_password", ""),
            "from_email": form_data.get("from_email", ""),
            "from_name": form_data.get("from_name", "דיור003"),
            "smtp_secure": form_data.get("smtp_secure") == "on",
        }

        with get_conn() as conn:
            # If password is empty, keep the existing one
            if not settings["smtp_password"]:
                existing = get_email_settings(conn)
                if existing:
                    settings["smtp_password"] = existing.get("smtp_password", "")

            success = save_email_settings(conn, settings)

        if success:
            return RedirectResponse(
                url="/admin/email-settings?saved=1",
                status_code=303
            )
        else:
            return RedirectResponse(
                url="/admin/email-settings?error=1",
                status_code=303
            )

    except Exception as e:
        logger.error(f"Error updating email settings: {e}", exc_info=True)
        return RedirectResponse(
            url="/admin/email-settings?error=1",
            status_code=303
        )


async def test_email_settings(request: Request) -> JSONResponse:
    """Test email connection with current settings. רק למנהל על."""
    _require_super_admin(request)
    try:
        form_data = await request.json()

        settings = {
            "smtp_host": form_data.get("smtp_host", ""),
            "smtp_port": int(form_data.get("smtp_port", 587)),
            "smtp_user": form_data.get("smtp_user", ""),
            "smtp_password": form_data.get("smtp_password", ""),
            "smtp_secure": form_data.get("smtp_secure", False),
        }

        # If password is empty, try to get from DB
        if not settings["smtp_password"]:
            with get_conn() as conn:
                existing = get_email_settings(conn)
                if existing:
                    settings["smtp_password"] = existing.get("smtp_password", "")

        result = test_email_connection(settings)
        return JSONResponse(result)

    except Exception as e:
        logger.error(f"Error testing email: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)})


async def send_guide_email_route(request: Request, person_id: int, year: int, month: int) -> JSONResponse:
    """שליחת דוח מדריך במייל."""
    try:
        custom_email = None
        try:
            body = await request.json()
            custom_email = body.get('email')
        except:
            pass

        import asyncio

        result = await asyncio.to_thread(send_guide_email, person_id, year, month, custom_email)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Error in send_guide_email_route: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)})


async def send_all_guides_email_route(request: Request, year: int, month: int) -> JSONResponse:
    """שליחת דוחות לכל המדריכים הפעילים."""
    try:
        import asyncio
        result = await asyncio.to_thread(send_all_guides_email, year, month)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Error in send_all_guides_email_route: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)})


async def send_all_to_single_email_route(request: Request, year: int, month: int) -> JSONResponse:
    """שליחת כל דוחות המדריכים למייל אחד."""
    try:
        body = await request.json()
        target_email = body.get('email')

        if not target_email:
            return JSONResponse({"success": False, "error": "יש להזין כתובת מייל"})

        import asyncio
        result = await asyncio.to_thread(send_all_guides_to_single_email, year, month, target_email)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Error in send_all_to_single_email_route: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)})


async def send_selected_guides_emails_route(request: Request, year: int, month: int) -> JSONResponse:
    """שליחת דוחות למדריכים נבחרים בלבד."""
    try:
        body = await request.json()
        guide_ids = body.get('guide_ids', [])

        if not guide_ids:
            return JSONResponse({"success": False, "error": "לא נבחרו מדריכים"})

        import asyncio
        result = await asyncio.to_thread(send_selected_guides_email, guide_ids, year, month)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Error in send_selected_guides_emails_route: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)})


async def send_selected_guides_to_single_email_route(request: Request, year: int, month: int) -> JSONResponse:
    """שליחת דוחות מדריכים נבחרים למייל אחד."""
    try:
        body = await request.json()
        target_email = body.get('email')
        guide_ids = body.get('guide_ids', [])

        if not target_email:
            return JSONResponse({"success": False, "error": "יש להזין כתובת מייל"})

        if not guide_ids:
            return JSONResponse({"success": False, "error": "לא נבחרו מדריכים"})

        import asyncio
        result = await asyncio.to_thread(send_selected_guides_to_single_email, guide_ids, year, month, target_email)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Error in send_selected_guides_to_single_email_route: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)})


async def send_test_email_route(request: Request) -> JSONResponse:
    """Send a test email to verify email settings. רק למנהל על."""
    _require_super_admin(request)
    try:
        form_data = await request.json()
        to_email = form_data.get("to_email", "")

        if not to_email:
            return JSONResponse({"success": False, "error": "יש להזין כתובת מייל"})

        with get_conn() as conn:
            result = send_test_email(conn, to_email)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Error in send_test_email_route: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)})


# ─── SSE Bulk Send ────────────────────────────────────────────


def _sse_event(event: str, data: dict) -> str:
    """פורמט אירוע SSE."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def send_bulk_stream(request: Request, year: int, month: int) -> StreamingResponse:
    """שליחת דוחות מרוכזת עם SSE לעדכון התקדמות בזמן אמת."""
    import asyncio

    # שליפת הגדרות ורשימת מדריכים
    with get_conn() as conn:
        settings = get_email_settings(conn)
        if not settings:
            async def error_stream() -> AsyncGenerator[str, None]:
                yield _sse_event("error", {"message": "הגדרות מייל לא נמצאו"})
            return StreamingResponse(error_stream(), media_type="text/event-stream")

        guides = conn.execute("""
            SELECT DISTINCT p.id, p.name, p.email
            FROM people p
            JOIN time_reports tr ON tr.person_id = p.id
            WHERE p.is_active = TRUE
            AND EXTRACT(YEAR FROM tr.date) = %s
            AND EXTRACT(MONTH FROM tr.date) = %s
            ORDER BY p.name
        """, (year, month)).fetchall()

    if not guides:
        async def empty_stream() -> AsyncGenerator[str, None]:
            yield _sse_event("error", {"message": "לא נמצאו מדריכים עם משמרות בחודש זה"})
        return StreamingResponse(empty_stream(), media_type="text/event-stream")

    guides_list = [dict(g) for g in guides]
    batch_id = generate_batch_id()
    user = getattr(request.state, "current_user", None)
    sent_by = user.get("person_id") if user else None

    CONCURRENCY = 3

    async def event_stream() -> AsyncGenerator[str, None]:
        """זרם אירועי SSE עם עיבוד מקבילי."""
        yield _sse_event("start", {"total": len(guides_list), "batchId": batch_id})

        sent = []
        skipped = []
        processed = 0
        loop = asyncio.get_event_loop()

        for i in range(0, len(guides_list), CONCURRENCY):
            # בדיקה שהלקוח עדיין מחובר
            if await request.is_disconnected():
                logger.info(f"Client disconnected during bulk send batch {batch_id}")
                break

            batch = guides_list[i:i + CONCURRENCY]

            # שליחת אירוע sending לכל המדריכים ב-batch
            for g in batch:
                yield _sse_event("sending", {"id": g["id"], "name": g["name"]})

            # עיבוד מקבילי באמצעות ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
                futures = [
                    loop.run_in_executor(
                        executor,
                        process_guide_for_bulk,
                        g, year, month, batch_id, settings, sent_by,
                    )
                    for g in batch
                ]
                results = await asyncio.gather(*futures)

            for result in results:
                processed += 1
                if result["status"] == "sent":
                    sent.append(result)
                else:
                    skipped.append(result)

                yield _sse_event("progress", {
                    "processed": processed,
                    "total": len(guides_list),
                    "currentId": result["id"],
                    "currentName": result["name"],
                    "status": result["status"],
                    "reason": result.get("reason"),
                    "sent": len(sent),
                    "skipped": len(skipped),
                })

        yield _sse_event("complete", {
            "sent": sent,
            "skipped": skipped,
            "batchId": batch_id,
        })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─── Email Logs Routes ───────────────────────────────────────


async def email_logs_route(request: Request, year: int, month: int) -> JSONResponse:
    """שליפת לוגי שליחת מייל לחודש מסוים."""
    try:
        with get_conn() as conn:
            logs = get_email_logs(conn, year=year, month=month, limit=500)
        return JSONResponse({"success": True, "logs": logs}, media_type="application/json; charset=utf-8")
    except Exception as e:
        logger.error(f"Error fetching email logs: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)})


async def email_batch_summary_route(request: Request, batch_id: str) -> JSONResponse:
    """סיכום batch שליחה."""
    try:
        with get_conn() as conn:
            summary = get_batch_summary(conn, batch_id)
        return JSONResponse({"success": True, "summary": summary})
    except Exception as e:
        logger.error(f"Error fetching batch summary: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)})


async def retry_failed_route(request: Request) -> JSONResponse:
    """שליחה מחדש של מיילים שנכשלו ב-batch."""
    try:
        body = await request.json()
        batch_id = body.get("batch_id")
        year = body.get("year")
        month = body.get("month")

        if not batch_id:
            return JSONResponse({"success": False, "error": "חסר batch_id"})

        with get_conn() as conn:
            settings = get_email_settings(conn)
            if not settings:
                return JSONResponse({"success": False, "error": "הגדרות מייל לא נמצאו"})

            failed_logs = get_email_logs(conn, batch_id=batch_id, status="failed")

        if not failed_logs:
            return JSONResponse({"success": False, "error": "לא נמצאו שליחות שנכשלו ב-batch זה"})

        user = getattr(request.state, "current_user", None)
        sent_by = user.get("person_id") if user else None
        retry_batch_id = f"{batch_id}-retry"

        results = {"success": [], "failed": []}
        for log in failed_logs:
            guide = {
                "id": log["recipient_id"],
                "name": log["recipient_name"],
                "email": log["recipient_email"],
            }
            result = process_guide_for_bulk(
                guide,
                year or log.get("year"),
                month or log.get("month"),
                retry_batch_id,
                settings,
                sent_by,
            )
            if result["status"] == "sent":
                results["success"].append(result["name"])
            else:
                results["failed"].append(result["name"])

        return JSONResponse({
            "success": True,
            "message": f"נשלחו מחדש {len(results['success'])} מתוך {len(failed_logs)}",
            "details": results,
            "retry_batch_id": retry_batch_id,
        })
    except Exception as e:
        logger.error(f"Error in retry_failed: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)})

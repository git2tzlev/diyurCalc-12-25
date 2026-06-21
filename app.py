"""
Refactored main application file for DiyurCalc.
Uses modular structure with separate route handlers.
"""
from __future__ import annotations

import logging
import signal
import sys
import atexit
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
import psycopg2

from core.config import config
from core.database import (
    set_demo_mode, get_demo_mode_from_cookie, close_all_pools,
    get_housing_array_from_cookie, set_db_actor_person_id,
    set_housing_array_filter, get_conn
)
from utils.utils import human_date, format_currency, format_currency_total
from routes.home import home
from routes.reports import reports_management, export_guide_reports_excel
from routes.guide import (
    guide_view,
    shifts_report_pdf, shifts_report_preview, shifts_report_email, chains_report_email,
    get_guide_notes, add_guide_note, delete_guide_note,
    get_holiday_payment_setup_api, save_holiday_payment_setup_api,
    guide_notes_management,
)
from routes.admin import (
    manage_payment_codes, update_payment_codes,
    demo_sync_page, sync_demo_database, demo_sync_status,
    get_month_lock_status, lock_month_api, unlock_month_api,
    manage_special_days, add_special_day, toggle_special_day, delete_special_day,
    business_rules_page,
)
from routes.summary import general_summary
from routes.export import (
    export_gesher,
    export_gesher_person,
    export_gesher_multiple,
    export_gesher_preview,
    export_excel,
    gesher_archive_page,
    download_gesher_archive_file,
    view_gesher_archive_file,
    update_gesher_archive_note,
    update_gesher_archive_status,
)
from routes.completions import (
    completions_page,
    completion_difference_report,
    completion_gesher_file_report,
    completion_impact_report,
    completion_overall_impact_report,
    completion_reports_bulk_send_stream,
)
from routes.email import (
    email_settings_page,
    update_email_settings,
    test_email_settings,
    send_test_email_route,
    send_guide_email_route,
    send_all_guides_email_route,
    send_all_to_single_email_route,
    send_selected_guides_emails_route,
    send_selected_guides_to_single_email_route,
    send_bulk_stream,
    email_logs_route,
    email_batch_summary_route,
    retry_failed_route,
)
from routes.stats import (
    stats_page,
    get_salary_by_housing_array,
    get_salary_by_guide,
    get_hours_distribution,
    get_extras_distribution,
    get_monthly_trends,
    get_comparison_data,
    get_shift_types_distribution,
    get_all_stats,
    get_compare_housing_arrays,
    get_top_apartments_by_percent,
    get_apartments_in_array,
    get_apartments_in_array_by_percent,
    get_apartment_details,
    get_guide_yearly,
    get_housing_arrays_list,
    get_apartments_list,
    get_guides_list,
    get_overtime_by_housing_array,
    send_overtime_email_route,
)
from routes.auth import login_page, login_submit, logout
from core.auth import (
    can_login,
    validate_session_token,
    refresh_session_user,
    SESSION_COOKIE_NAME,
    is_framework_manager,
    is_super_admin,
    get_user_housing_array,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
GENERIC_ERROR = "שגיאת מערכת. נסי שוב מאוחר יותר"

# Global flag to track shutdown
_shutting_down = False


def cleanup_resources():
    """Clean up resources before shutdown."""
    global _shutting_down
    if _shutting_down:
        return
    
    _shutting_down = True
    logger.info("Cleaning up resources...")
    
    try:
        # Close database connection pools
        close_all_pools()
        logger.info("Resource cleanup completed")
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    cleanup_resources()
    sys.exit(0)


# Register signal handlers for graceful shutdown
if hasattr(signal, 'SIGTERM'):
    signal.signal(signal.SIGTERM, signal_handler)
if hasattr(signal, 'SIGINT'):
    signal.signal(signal.SIGINT, signal_handler)

# Register cleanup on exit
atexit.register(cleanup_resources)


async def run_startup_tasks():
    """Ensure runtime defaults exist in production and demo databases."""
    from core.runtime_defaults import ensure_runtime_defaults

    ensure_runtime_defaults(include_demo=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await run_startup_tasks()
    try:
        yield
    finally:
        logger.info("Application shutting down...")
        cleanup_resources()


app = FastAPI(title="ניהול משמרות בענן", lifespan=lifespan)
templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
templates.env.filters["human_date"] = human_date
templates.env.filters["format_currency"] = format_currency
templates.env.filters["format_currency_total"] = format_currency_total
templates.env.globals["app_version"] = config.VERSION


# Middleware to set demo mode and housing array filter from cookies
class DemoModeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        token = request.cookies.get(SESSION_COOKIE_NAME)
        user = validate_session_token(token) if token else None
        demo_mode = bool(
            user
            and user.get("role") == "super_admin"
            and get_demo_mode_from_cookie(request)
        )
        set_demo_mode(demo_mode)
        housing_array_id = get_housing_array_from_cookie(request)
        set_housing_array_filter(housing_array_id)
        response = await call_next(request)

        # שמירת תקופה נבחרת בעוגייה כשיש month+year ב-query params
        qp = request.query_params
        q_month = qp.get("month")
        q_year = qp.get("year")
        if q_month and q_year and q_month.isdigit() and q_year.isdigit():
            response.set_cookie(
                key="selected_period",
                value=f"{q_year}-{q_month}",
                max_age=86400 * 365,
                httponly=False,
                samesite="lax",
            )

        return response


# Middleware לאימות משתמשים
class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware לבדיקת התחברות בכל בקשה."""

    # נתיבים שלא דורשים התחברות
    PUBLIC_ROUTES = {"/login", "/static", "/health"}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # בדיקת session cookie
        token = request.cookies.get(SESSION_COOKIE_NAME)
        session_user = validate_session_token(token) if token else None
        user = None

        # שמירת פרטי המשתמש ב-request state (גם אם None). בנתיבים מוגנים
        # נרענן מול ה-DB כדי לכבד שינוי role / סטטוס / מערך דיור מיידית.
        request.state.current_user = None

        # נתיבים ציבוריים - לא צריך בדיקה
        if any(path.startswith(route) for route in self.PUBLIC_ROUTES):
            return await call_next(request)

        user = refresh_session_user(session_user)
        request.state.current_user = user
        set_db_actor_person_id(user.get("person_id") if user else None)

        if not user:
            # הפניה לעמוד התחברות עבור בקשות HTML
            if "text/html" in request.headers.get("accept", ""):
                return RedirectResponse(url="/login", status_code=303)
            # שגיאה 401 עבור בקשות API
            return JSONResponse(
                {"error": "לא מחובר למערכת"},
                status_code=401
            )

        if not can_login(user.get("role") or ""):
            return JSONResponse(
                {"error": "אין הרשאה להתחבר למערכת"},
                status_code=403,
            )

        # עבור מנהל מסגרת - כפה סינון לפי המערך שלו
        # חשוב: זה רץ אחרי DemoModeMiddleware ולכן דורס את הערך מהעוגייה
        if user.get("role") == "framework_manager" and user.get("housing_array_id"):
            set_housing_array_filter(user["housing_array_id"])

        try:
            response = await call_next(request)
            return response
        finally:
            set_db_actor_person_id(None)


# סדר ה-middleware חשוב!
# AuthMiddleware נוסף ראשון ולכן רץ אחרון (אחרי DemoModeMiddleware)
# זה מאפשר לו לדרוס את ה-filter של מנהל מסגרת
app.add_middleware(AuthMiddleware)
app.add_middleware(DemoModeMiddleware)

# Mount static files
if config.STATIC_DIR:
    app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")

# Global exception handler for database connection errors
@app.exception_handler(psycopg2.OperationalError)
async def database_connection_error_handler(request: Request, exc: psycopg2.OperationalError):
    """Handle database connection errors with helpful messages."""
    error_msg = str(exc)

    if not config.DEBUG:
        user_message = "שגיאת חיבור לבסיס הנתונים. נסי שוב מאוחר יותר."
    elif "could not translate host name" in error_msg or "Name or service not known" in error_msg:
        user_message = (
            "שגיאת חיבור לבסיס הנתונים: לא ניתן לפתור את שם השרת.\n\n"
            "אפשרויות לפתרון:\n"
            "1. בדוק את חיבור האינטרנט\n"
            "2. ודא שהחיבור ל-VPN פעיל (אם נדרש)\n"
            "3. בדוק את הגדרות ה-DNS\n"
            "4. ודא שה-DATABASE_URL נכון בקובץ .env"
        )
    elif "connection refused" in error_msg.lower():
        user_message = (
            "שגיאת חיבור לבסיס הנתונים: השרת דחה את החיבור.\n\n"
            "אפשרויות לפתרון:\n"
            "1. ודא ששרת בסיס הנתונים פועל\n"
            "2. בדוק את מספר הפורט\n"
            "3. ודא שהחומת אש מאפשרת חיבורים"
        )
    else:
        user_message = f"שגיאת חיבור לבסיס הנתונים: {error_msg}"

    logger.error("Database connection error: %s", error_msg, exc_info=True)
    
    # Return HTML error page for web requests
    if request.url.path.startswith('/api/'):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={
                "error": user_message,
                "error_type": "database_connection_error"
            }
        )
    
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "error_message": user_message,
            "error_id": None,
            "back_url": "/"
        },
        status_code=503
    )

# Route registrations
@app.get("/health")
def health_check():
    """Health check endpoint that tests database connectivity."""
    try:
        from core.database import get_conn
        with get_conn() as conn:
            # Simple query to test connection
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        logger.error("Health check failed: %s", e, exc_info=True)
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "database": "disconnected",
                "error": "database unavailable",
            },
        )


# Auth routes
@app.get("/login", response_class=HTMLResponse)
def login_route(request: Request, error: str = None):
    """עמוד התחברות."""
    return login_page(request, error)


@app.post("/login")
async def login_submit_route(request: Request):
    """עיבוד טופס התחברות."""
    return await login_submit(request)


@app.get("/logout")
def logout_route(request: Request):
    """התנתקות מהמערכת."""
    return logout(request)


@app.get("/", response_class=HTMLResponse)
def home_route(request: Request, month: int | None = None, year: int | None = None, q: str | None = None):
    """Home page route."""
    return home(request, month, year, q)


@app.get("/reports", response_class=HTMLResponse)
def reports_route(request: Request, month: int | None = None, year: int | None = None, housing_array_id: int | None = None):
    """Reports management page - send guide reports via email."""
    return reports_management(request, month, year, housing_array_id)


@app.get("/reports/export-guide-reports-excel")
def export_guide_reports_excel_route(
    request: Request,
    year: int,
    month: int,
    housing_array_id: int | None = None,
):
    """Export guide reports for external system import."""
    return export_guide_reports_excel(request, year, month, housing_array_id)


@app.get("/guide", include_in_schema=False)
@app.get("/guide/", include_in_schema=False)
def redirect_to_home():
    """Redirect /guide to home page."""
    return RedirectResponse(url="/")


@app.get("/guide/{person_id}", response_class=HTMLResponse)
def guide_route(request: Request, person_id: int, month: int | None = None, year: int | None = None):
    """Detailed guide view."""
    return guide_view(request, person_id, month, year)


@app.get("/guide/{person_id}/shifts/preview", response_class=HTMLResponse)
def shifts_report_preview_route(request: Request, person_id: int, month: int | None = None, year: int | None = None):
    """תצוגה מקדימה של הדוח שנשלח במייל."""
    return shifts_report_preview(request, person_id, month, year)


@app.get("/guide/{person_id}/shifts/pdf")
def shifts_report_pdf_route(request: Request, person_id: int, month: int | None = None, year: int | None = None):
    """Download shifts report as PDF."""
    return shifts_report_pdf(request, person_id, month, year)


@app.post("/api/send-shifts-email/{person_id}")
async def shifts_report_email_route(request: Request, person_id: int, year: int, month: int):
    """Send shifts report via email."""
    try:
        return await shifts_report_email(request, person_id, year, month)
    except Exception as e:
        logger.error(f"Unhandled error in shifts_report_email_route: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": GENERIC_ERROR})


@app.post("/api/send-chains-email/{person_id}")
async def chains_report_email_route(request: Request, person_id: int, year: int, month: int):
    """Send chains report via email as PDF."""
    try:
        return await chains_report_email(request, person_id, year, month)
    except Exception as e:
        logger.error(f"Unhandled error in chains_report_email_route: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": GENERIC_ERROR})


@app.get("/api/guide/{person_id}/notes")
def get_guide_notes_route(request: Request, person_id: int, year: int, month: int):
    """שליפת הערות למדריך."""
    return get_guide_notes(request, person_id, year, month)


@app.post("/api/guide/{person_id}/notes")
async def add_guide_note_route(request: Request, person_id: int):
    """הוספת הערה למדריך."""
    return await add_guide_note(request, person_id)


@app.delete("/api/guide/notes/{note_id}")
async def delete_guide_note_route(request: Request, note_id: int):
    """מחיקת הערה."""
    return await delete_guide_note(request, note_id)


@app.get("/notes", response_class=HTMLResponse)
def guide_notes_management_route(
    request: Request,
    year: int | None = None,
    month: int | None = None,
):
    """עמוד ניהול כל הערות המדריכים."""
    return guide_notes_management(request, year, month)


@app.get("/api/holiday-payment-setup")
def holiday_payment_setup_route(request: Request, year: int, month: int):
    """נתוני ניהול תשלום חג."""
    return get_holiday_payment_setup_api(request, year, month)


@app.post("/api/holiday-payment-setup")
async def save_holiday_payment_setup_route(request: Request):
    """שמירת ניהול תשלום חג."""
    return await save_holiday_payment_setup_api(request)


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
def redirect_admin_to_home():
    """Redirect /admin to home page."""
    return RedirectResponse(url="/")


@app.get("/admin/payment-codes", response_class=HTMLResponse)
def manage_payment_codes_route(request: Request):
    """Payment codes management page."""
    return manage_payment_codes(request)


@app.get("/admin/business-rules", response_class=HTMLResponse)
def business_rules_route(request: Request):
    """Business rules catalog page."""
    return business_rules_page(request)


@app.post("/admin/payment-codes/update")
async def update_payment_codes_route(request: Request):
    """Update payment codes."""
    return await update_payment_codes(request)


@app.get("/admin/demo-sync", response_class=HTMLResponse)
def demo_sync_route(request: Request):
    """Demo database sync page."""
    return demo_sync_page(request)


@app.get("/admin/demo-sync/run")
async def sync_demo_route(request: Request, token: str = ""):
    """Run demo database sync with SSE progress."""
    return await sync_demo_database(request, token)


@app.get("/admin/demo-sync/status")
def demo_sync_status_route(request: Request):
    """Get demo database status."""
    return demo_sync_status(request)


# Special Days (Premium Days) Management
@app.get("/admin/special-days", response_class=HTMLResponse)
def manage_special_days_route(request: Request):
    """Special days management page."""
    return manage_special_days(request)


@app.post("/admin/special-days/add")
async def add_special_day_route(request: Request):
    """Add a new special day."""
    return await add_special_day(request)


@app.post("/api/special-days/toggle")
async def toggle_special_day_route(request: Request):
    """Toggle special day active status."""
    return await toggle_special_day(request)


@app.post("/api/special-days/delete")
async def delete_special_day_route(request: Request):
    """Delete a special day."""
    return await delete_special_day(request)


# Month Lock APIs
@app.get("/api/month-lock/{year}/{month}")
def get_month_lock_route(request: Request, year: int, month: int):
    """Get month lock status."""
    return get_month_lock_status(request, year, month)


@app.post("/api/month-lock")
async def lock_month_route(request: Request):
    """Lock a month."""
    return await lock_month_api(request)


@app.post("/api/month-unlock")
async def unlock_month_route(request: Request):
    """Unlock a month."""
    return await unlock_month_api(request)


@app.get("/summary", response_class=HTMLResponse)
def general_summary_route(request: Request, year: int = None, month: int = None):
    """General monthly summary."""
    return general_summary(request, year, month)


@app.get("/export/gesher")
def export_gesher_route(request: Request, year: int, month: int, company: str = None, filter_name: str = None, encoding: str = "ascii"):
    """Export Gesher file by company."""
    return export_gesher(request, year, month, company, filter_name, encoding)


@app.get("/export/gesher/person/{person_id}")
def export_gesher_person_route(request: Request, person_id: int, year: int, month: int, encoding: str = "ascii"):
    """Export Gesher file for individual person."""
    return export_gesher_person(request, person_id, year, month, encoding)


@app.post("/export/gesher/multiple")
def export_gesher_multiple_route(request: Request, year: int, month: int, person_ids: str, encoding: str = "ascii"):
    """Export Gesher files for multiple people as ZIP."""
    # person_ids מגיע כמחרוזת מופרדת בפסיקים
    ids = [int(x.strip()) for x in person_ids.split(",") if x.strip().isdigit()]
    if not ids:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="לא נבחרו עובדים")
    return export_gesher_multiple(request, ids, year, month, encoding)


@app.get("/export/gesher/preview")
def export_gesher_preview_route(request: Request, year: int = None, month: int = None, show_zero: str = None):
    """Gesher export preview."""
    return export_gesher_preview(request, year, month, show_zero)


@app.get("/export/excel")
def export_excel_route(year: int = None, month: int = None):
    """Export monthly summary to Excel."""
    return export_excel(year, month)


@app.get("/admin/gesher-files", response_class=HTMLResponse)
def gesher_archive_route(request: Request, year: int = None, month: int = None, company: str = None):
    """Gesher export files archive."""
    return gesher_archive_page(request, year, month, company)


@app.get("/admin/gesher-files/{file_id}", response_class=HTMLResponse)
def view_gesher_archive_route(request: Request, file_id: int):
    """View archived Gesher file."""
    return view_gesher_archive_file(request, file_id)


@app.get("/admin/gesher-files/{file_id}/download")
def download_gesher_archive_route(request: Request, file_id: int):
    """Download archived Gesher file."""
    return download_gesher_archive_file(request, file_id)


@app.post("/admin/gesher-files/{file_id}/note")
async def update_gesher_archive_note_route(request: Request, file_id: int):
    """Update archived Gesher file note."""
    return await update_gesher_archive_note(request, file_id)


@app.post("/admin/gesher-files/{file_id}/status")
async def update_gesher_archive_status_route(request: Request, file_id: int):
    """Update archived Gesher file status."""
    return await update_gesher_archive_status(request, file_id)


@app.get("/completions", response_class=HTMLResponse)
def completions_route(request: Request, year: int = None, month: int = None):
    """Retroactive completions page."""
    return completions_page(request, year, month)


@app.get("/completions/difference/{file_id}")
def completion_difference_route(
    request: Request,
    file_id: int,
    payment_year: int,
    payment_month: int,
):
    """Generate completion differences against an archived final Gesher file."""
    return completion_difference_report(request, file_id, payment_year, payment_month)


@app.get("/completions/impact/{work_year}/{work_month}", response_class=HTMLResponse)
def completion_impact_route(
    request: Request,
    work_year: int,
    work_month: int,
    payment_year: int,
    payment_month: int,
):
    """Show completion impact by guide and Gesher symbol."""
    return completion_impact_report(request, work_year, work_month, payment_year, payment_month)


@app.get("/completions/impact-all", response_class=HTMLResponse)
def completion_overall_impact_route(
    request: Request,
    payment_year: int,
    payment_month: int,
):
    """Show total completion impact for one payment month."""
    return completion_overall_impact_report(request, payment_year, payment_month)


@app.get("/completions/gesher-file")
def completion_gesher_file_route(
    request: Request,
    payment_year: int,
    payment_month: int,
):
    """Generate a Gesher file for payment-month completion differences."""
    return completion_gesher_file_report(request, payment_year, payment_month)


@app.get("/api/completions/send-reports-stream")
async def completion_reports_bulk_send_stream_route(
    request: Request,
    payment_year: int,
    payment_month: int,
    token: str = "",
    demo_email: str = "",
):
    """Send work-month shift reports for guides with completions."""
    return await completion_reports_bulk_send_stream(
        request, payment_year, payment_month, token, demo_email
    )


# Statistics routes
@app.get("/stats", response_class=HTMLResponse)
def stats_route(request: Request, year: int = None, month: int = None):
    """Statistics dashboard page."""
    return stats_page(request, year, month)


@app.get("/api/stats/by-housing")
def stats_by_housing_route(year: int, month: int):
    """Get salary by housing array."""
    return get_salary_by_housing_array(year, month)


@app.get("/api/stats/by-guide")
def stats_by_guide_route(year: int, month: int, limit: int = 20):
    """Get salary by guide."""
    return get_salary_by_guide(year, month, limit)


@app.get("/api/stats/hours-distribution")
def stats_hours_distribution_route(year: int, month: int):
    """Get hours distribution."""
    return get_hours_distribution(year, month)


@app.get("/api/stats/extras-distribution")
def stats_extras_distribution_route(year: int, month: int):
    """Get extras distribution (standby, vacation, sick)."""
    return get_extras_distribution(year, month)


@app.get("/api/stats/monthly-trends")
def stats_monthly_trends_route(year: int, months_back: int = 6):
    """Get monthly trends."""
    return get_monthly_trends(year, months_back)


@app.get("/api/stats/comparison")
def stats_comparison_route(year1: int, month1: int, year2: int, month2: int):
    """Get comparison data between two months."""
    return get_comparison_data(year1, month1, year2, month2)


@app.get("/api/stats/shift-types")
def stats_shift_types_route(year: int, month: int):
    """Get shift types distribution."""
    return get_shift_types_distribution(year, month)


@app.get("/api/stats/all")
def stats_all_route(year: int, month: int):
    """Get all stats data in one call - faster loading."""
    return get_all_stats(year, month)


@app.get("/api/stats/compare-arrays")
def stats_compare_arrays_route(request: Request, year: int, month: int, array_ids: str):
    """Compare 2-5 housing arrays."""
    ids = [int(x) for x in array_ids.split(",") if x.strip()]
    return get_compare_housing_arrays(request, year, month, ids)


@app.get("/api/stats/top-apartments")
def stats_top_apartments_route(year: int, month: int, percent: int = 100, limit: int = 10):
    """Get top apartments by percent."""
    return get_top_apartments_by_percent(year, month, percent, limit)


@app.get("/api/stats/apartments-in-array")
def stats_apartments_in_array_route(year: int, month: int, housing_array_id: int):
    """Get all apartments in a housing array."""
    return get_apartments_in_array(year, month, housing_array_id)


@app.get("/api/stats/apartments-in-array-by-percent")
def stats_apartments_in_array_by_percent_route(year: int, month: int, housing_array_id: int):
    """Get apartments in array with percent breakdown."""
    return get_apartments_in_array_by_percent(year, month, housing_array_id)


@app.get("/api/stats/apartment-details")
def stats_apartment_details_route(year: int, month: int, apartment_id: int):
    """Get apartment details - shifts and guides."""
    return get_apartment_details(year, month, apartment_id)


@app.get("/api/stats/guide-yearly")
def stats_guide_yearly_route(person_id: int, year: int):
    """Get guide yearly trend - 12 months."""
    return get_guide_yearly(person_id, year)


@app.get("/api/stats/housing-arrays")
def stats_housing_arrays_route():
    """Get list of housing arrays."""
    return get_housing_arrays_list()


@app.get("/api/stats/apartments")
def stats_apartments_route(housing_array_id: int = None):
    """Get list of apartments."""
    return get_apartments_list(housing_array_id)


@app.get("/api/stats/guides")
def stats_guides_route():
    """Get list of active guides."""
    return get_guides_list()


@app.get("/api/stats/overtime")
def stats_overtime_route(year: int, month: int):
    """שעות נוספות לפי מערך דיור עם פירוט מדריכים."""
    return get_overtime_by_housing_array(year, month)


@app.post("/api/stats/send-overtime-email")
async def stats_send_overtime_email_route(request: Request, year: int, month: int):
    """שליחת דוח שעות נוספות לרכז מערך דיור."""
    return await send_overtime_email_route(request, year, month)


# Email routes
@app.get("/admin/email-settings", response_class=HTMLResponse)
def email_settings_route(request: Request):
    """Email settings page."""
    return email_settings_page(request)


@app.post("/admin/email-settings/update")
async def update_email_settings_route(request: Request):
    """Update email settings."""
    return await update_email_settings(request)


@app.post("/admin/email-settings/test")
async def test_email_settings_route(request: Request):
    """Test email connection."""
    return await test_email_settings(request)


@app.post("/admin/email-settings/send-test")
async def send_test_email_api(request: Request):
    """Send a test email."""
    return await send_test_email_route(request)


@app.post("/api/send-guide-email/{person_id}")
async def send_guide_email_api(request: Request, person_id: int, year: int, month: int):
    """Send guide report email to a specific person."""
    return await send_guide_email_route(request, person_id, year, month)


@app.post("/api/send-all-guides-email")
async def send_all_guides_email_api(request: Request, year: int, month: int):
    """שליחת דוחות לכל המדריכים הפעילים."""
    return await send_all_guides_email_route(request, year, month)


@app.post("/api/send-all-to-single-email")
async def send_all_to_single_email_api(request: Request, year: int, month: int):
    """שליחת כל דוחות המדריכים למייל אחד."""
    return await send_all_to_single_email_route(request, year, month)


@app.post("/api/send-guides-emails")
async def send_selected_guides_emails_api(request: Request, year: int, month: int):
    """שליחת דוחות למדריכים נבחרים בלבד."""
    return await send_selected_guides_emails_route(request, year, month)


@app.post("/api/send-guides-to-single-email")
async def send_selected_guides_to_single_email_api(request: Request, year: int, month: int):
    """שליחת דוחות מדריכים נבחרים למייל אחד."""
    return await send_selected_guides_to_single_email_route(request, year, month)


@app.get("/api/send-bulk-stream")
async def send_bulk_stream_api(request: Request, year: int, month: int, token: str = ""):
    """שליחה מרוכזת עם SSE לעדכון התקדמות בזמן אמת."""
    return await send_bulk_stream(request, year, month, token)


@app.get("/api/email-logs")
async def email_logs_api(request: Request, year: int, month: int):
    """שליפת לוגי שליחת מייל."""
    return await email_logs_route(request, year, month)


@app.get("/api/email-batch-summary/{batch_id}")
async def email_batch_summary_api(request: Request, batch_id: str):
    """סיכום batch שליחה."""
    return await email_batch_summary_route(request, batch_id)


@app.post("/api/retry-failed-emails")
async def retry_failed_emails_api(request: Request):
    """שליחה מחדש של מיילים שנכשלו."""
    return await retry_failed_route(request)


@app.post("/api/toggle-demo-mode")
async def toggle_demo_mode(request: Request):
    """Toggle between demo and production database."""
    if not is_super_admin(request):
        return JSONResponse({"success": False, "error": "אין הרשאה"}, status_code=403)

    # Verify password (from environment variable)
    try:
        body = await request.json()
        password = body.get("password", "")
    except Exception:
        password = ""

    # Password must be configured in environment variable DEMO_MODE_PASSWORD
    if not config.DEMO_MODE_PASSWORD:
        logger.error("DEMO_MODE_PASSWORD not configured in environment")
        return JSONResponse({"success": False, "error": "סיסמה לא מוגדרת במערכת"}, status_code=500)

    if password != config.DEMO_MODE_PASSWORD:
        return JSONResponse({"success": False, "error": "סיסמה שגויה"}, status_code=401)

    current_demo = get_demo_mode_from_cookie(request)
    new_demo = not current_demo

    response = JSONResponse({
        "success": True,
        "demo_mode": new_demo,
        "db_name": "פיתוח" if new_demo else "עבודה"
    })

    # Set cookie (expires in 24 hours)
    response.set_cookie(
        key="demo_mode",
        value="true" if new_demo else "false",
        max_age=86400,
        httponly=False,
        samesite="lax"
    )

    return response


@app.get("/api/demo-mode-status")
def demo_mode_status(request: Request):
    """Get current demo mode status."""
    demo = bool(is_super_admin(request) and get_demo_mode_from_cookie(request))
    return {
        "demo_mode": demo,
        "db_name": "פיתוח" if demo else "עבודה"
    }


@app.get("/api/housing-arrays")
def get_housing_arrays(request: Request):
    """מחזיר רשימת כל מערכי הדיור."""
    user_housing_array = get_user_housing_array(request)
    with get_conn() as conn:
        if user_housing_array is not None:
            rows = conn.execute(
                "SELECT id, name FROM housing_arrays WHERE id = %s ORDER BY name",
                (user_housing_array,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name FROM housing_arrays ORDER BY name"
            ).fetchall()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


@app.post("/api/set-housing-array-filter")
async def set_housing_array_filter_api(request: Request):
    """מגדיר את מערך הדיור לסינון (שומר בעוגייה). רק למנהל על."""
    # מנהל מסגרת לא יכול לשנות את מערך הדיור שלו
    if is_framework_manager(request):
        return JSONResponse(
            {"success": False, "error": "אין הרשאה לשנות מערך דיור"},
            status_code=403
        )

    try:
        body = await request.json()
        housing_array_id = body.get("housing_array_id")
    except Exception:
        housing_array_id = None

    response = JSONResponse({
        "success": True,
        "housing_array_id": housing_array_id
    })

    if housing_array_id is not None:
        response.set_cookie(
            key="housing_array_id",
            value=str(housing_array_id),
            max_age=86400 * 30,  # 30 days
            httponly=False,
            samesite="lax"
        )
    else:
        response.delete_cookie("housing_array_id")

    return response


@app.get("/api/housing-array-status")
def housing_array_status(request: Request):
    """מחזיר את מצב הסינון הנוכחי לפי מערך דיור."""
    current_id = get_housing_array_from_cookie(request)
    current_name = None
    if current_id:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM housing_arrays WHERE id = %s",
                (current_id,)
            ).fetchone()
            if row:
                current_name = row["name"]
    return {
        "housing_array_id": current_id,
        "housing_array_name": current_name
    }


@app.post("/api/set-selected-period")
async def set_selected_period_api(request: Request):
    """שומר את החודש והשנה שנבחרו בעוגייה."""
    try:
        body = await request.json()
        year = body.get("year")
        month = body.get("month")
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid request"}, status_code=400)

    if not year or not month:
        return JSONResponse({"success": False, "error": "Missing year or month"}, status_code=400)

    response = JSONResponse({"success": True, "year": year, "month": month})
    response.set_cookie(
        key="selected_period",
        value=f"{year}-{month}",
        max_age=86400 * 365,  # שנה
        httponly=False,
        samesite="lax"
    )
    return response


if __name__ == "__main__":
    import uvicorn
    try:
        uvicorn.run(
            "app:app",
            host=config.HOST,
            port=config.PORT,
            reload=config.DEBUG
        )
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
        cleanup_resources()
    except Exception as e:
        logger.error(f"Error running application: {e}")
        cleanup_resources()
        raise

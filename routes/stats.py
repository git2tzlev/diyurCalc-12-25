"""
Statistics routes for DiyurCalc application.
Contains routes for visual analytics dashboard with Chart.js.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, List

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from core.config import config
from core.database import get_conn, get_housing_array_filter, get_default_period
from core.auth import get_user_housing_array, is_framework_manager
from core.logic import calculate_monthly_summary
from utils.utils import format_currency, human_date, available_months_from_db

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
templates.env.filters["format_currency"] = format_currency
templates.env.filters["human_date"] = human_date
templates.env.globals["app_version"] = config.VERSION

# Cache לנתונים - מונע חישוב חוזר
_stats_cache = {}


def _stats_cache_key(year: int, month: int) -> str:
    """מפתח cache כולל פילטר מערך דיור (מניעת דליפת נתונים בין מנהל על למנהל מסגרת)."""
    hf = get_housing_array_filter()
    return f"{year}-{month}-{hf if hf is not None else 'all'}"


def _get_cached_summary(year: int, month: int):
    """מחזיר נתוני סיכום מה-cache או מחשב אותם."""
    cache_key = _stats_cache_key(year, month)
    if cache_key not in _stats_cache:
        with get_conn() as conn:
            summary_data, grand_totals = calculate_monthly_summary(conn.conn, year, month)
            _stats_cache[cache_key] = (summary_data, grand_totals)
    return _stats_cache[cache_key]


def clear_stats_cache():
    """מנקה את ה-cache (לקריאה אחרי עדכון נתונים)."""
    global _stats_cache
    _stats_cache = {}


# פלטת צבעים לגרפים
CHART_COLORS = [
    "#1f6feb", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6",
    "#EC4899", "#06B6D4", "#84CC16", "#F97316", "#6366F1"
]


def _generate_colors(count: int) -> List[str]:
    """יוצר פלטת צבעים לגרפים."""
    return (CHART_COLORS * ((count // len(CHART_COLORS)) + 1))[:count]


def stats_page(
    request: Request,
    year: Optional[int] = None,
    month: Optional[int] = None
) -> HTMLResponse:
    """
    דף סטטיסטיקות ראשי עם גרפים אינטראקטיביים.

    Args:
        request: בקשת FastAPI
        year: שנה לתצוגה
        month: חודש לתצוגה
    """
    if year is None or month is None:
        default_year, default_month = get_default_period(request)
        if year is None:
            year = default_year
        if month is None:
            month = default_month

    housing_filter = get_housing_array_filter()
    months_all = available_months_from_db(housing_filter)
    months_options = [{"year": y, "month": m, "label": f"{m:02d}/{y}"} for y, m in months_all]
    years_options = sorted({y for y, _ in months_all}, reverse=True)

    return templates.TemplateResponse(
        "stats.html",
        {
            "request": request,
            "selected_year": year,
            "selected_month": month,
            "months": months_options,
            "years": years_options,
            "is_framework_manager": is_framework_manager(request),
        },
    )


def get_salary_by_housing_array(year: int, month: int) -> JSONResponse:
    """
    שכר לפי מערך דיור - API לגרף.

    גישה פשוטה: מדריך שעבד בשני מערכים יופיע בשניהם עם כל השכר שלו.
    הסכום הכולל של הגרף עשוי להיות גבוה יותר מייצוא שכר (אם יש מדריכים ביותר ממערך אחד).
    כשמוגדר פילטר מערך דיור — נספר רק עבודה בדירות של אותו מערך.
    """
    from collections import defaultdict

    # שימוש ב-cache - אותו חישוב כמו ייצוא שכר
    summary_data, grand_totals = _get_cached_summary(year, month)
    housing_filter = get_housing_array_filter()

    with get_conn() as conn:
        # שליפת מערכי דיור לכל מדריך לפי הדירות שעבד בהן
        totals_by_housing = defaultdict(float)

        for person in summary_data:
            person_id = person["person_id"]
            total_payment = person["totals"].get("total_payment", 0)

            if total_payment <= 0:
                continue

            # מציאת כל מערכי הדיור שהמדריך עבד בהם
            if housing_filter is not None:
                housing_arrays = conn.execute("""
                    SELECT DISTINCT ha.name
                    FROM time_reports tr
                    JOIN apartments ap ON ap.id = tr.apartment_id
                    JOIN housing_arrays ha ON ha.id = ap.housing_array_id
                    WHERE tr.person_id = %s
                      AND EXTRACT(YEAR FROM tr.date) = %s
                      AND EXTRACT(MONTH FROM tr.date) = %s
                      AND ap.housing_array_id = %s
                """, (person_id, year, month, housing_filter)).fetchall()
            else:
                housing_arrays = conn.execute("""
                    SELECT DISTINCT ha.name
                    FROM time_reports tr
                    JOIN apartments ap ON ap.id = tr.apartment_id
                    JOIN housing_arrays ha ON ha.id = ap.housing_array_id
                    WHERE tr.person_id = %s
                      AND EXTRACT(YEAR FROM tr.date) = %s
                      AND EXTRACT(MONTH FROM tr.date) = %s
                """, (person_id, year, month)).fetchall()

            # הוספת כל השכר של המדריך לכל מערך שעבד בו
            for row in housing_arrays:
                totals_by_housing[row["name"]] += total_payment

    # בניית הנתונים לגרף - מיון לפי סכום
    sorted_housing = sorted(totals_by_housing.items(), key=lambda x: x[1], reverse=True)

    labels = [name for name, _ in sorted_housing]
    data = [round(total, 2) for _, total in sorted_housing]

    # הסכום הכולל מה-cache - זהה לייצוא שכר
    grand_total = grand_totals.get("total_payment", 0)

    return JSONResponse({
        "labels": labels,
        "datasets": [{
            "label": "שכר כולל (ש\"ח)",
            "data": data,
            "backgroundColor": _generate_colors(len(data))
        }],
        "total": round(grand_total, 2)
    })


def get_salary_by_guide(year: int, month: int, limit: int = 20) -> JSONResponse:
    """שכר לפי מדריך - Top N מדריכים."""
    summary_data, _ = _get_cached_summary(year, month)

    sorted_data = sorted(
        summary_data,
        key=lambda x: x["totals"].get("total_payment", 0),
        reverse=True
    )[:limit]

    labels = [d["name"] for d in sorted_data]
    data = [d["totals"].get("total_payment", 0) for d in sorted_data]

    return JSONResponse({
        "labels": labels,
        "datasets": [{
            "label": "שכר כולל (ש\"ח)",
            "data": data,
            "backgroundColor": "#1f6feb"
        }]
    })


def get_hours_distribution(year: int, month: int) -> JSONResponse:
    """התפלגות שעות לפי אחוזים (100%, 125%, 150%, 175%, 200%)."""
    summary_data, _ = _get_cached_summary(year, month)

    # סיכום כל השעות מכל המדריכים
    calc100 = sum(p["totals"].get("calc100", 0) for p in summary_data)
    calc125 = sum(p["totals"].get("calc125", 0) for p in summary_data)
    calc150 = sum(p["totals"].get("calc150", 0) for p in summary_data)
    calc175 = sum(p["totals"].get("calc175", 0) for p in summary_data)
    calc200 = sum(p["totals"].get("calc200", 0) for p in summary_data)

    labels = ["100%", "125%", "150%", "175%", "200%"]
    data = [
        calc100 / 60,  # המרה מדקות לשעות
        calc125 / 60,
        calc150 / 60,
        calc175 / 60,
        calc200 / 60,
    ]

    colors = ["#4CAF50", "#8BC34A", "#FFC107", "#FF5722", "#E91E63"]

    # סינון ערכים אפס מהגרף
    filtered_labels = []
    filtered_data = []
    filtered_colors = []
    for i, val in enumerate(data):
        if val > 0:
            filtered_labels.append(labels[i])
            filtered_data.append(round(val, 1))
            filtered_colors.append(colors[i])

    return JSONResponse({
        "labels": filtered_labels if filtered_labels else labels,
        "datasets": [{
            "label": "שעות",
            "data": filtered_data if filtered_data else [0] * len(labels),
            "backgroundColor": filtered_colors if filtered_colors else colors
        }]
    })


def get_extras_distribution(year: int, month: int) -> JSONResponse:
    """התפלגות כוננויות, חופשות, מחלות."""
    summary_data, _ = _get_cached_summary(year, month)

    # סיכום מכל המדריכים
    standby = sum(p["totals"].get("standby_payment", 0) for p in summary_data)
    vacation = sum(p["totals"].get("vacation_payment", 0) for p in summary_data)
    sick = sum(p["totals"].get("sick_payment", 0) for p in summary_data)
    travel = sum(p["totals"].get("travel", 0) for p in summary_data)
    extras = sum(p["totals"].get("extras", 0) for p in summary_data)

    labels = ["כוננויות", "חופשות", "מחלות", "נסיעות", "תוספות"]
    data = [standby, vacation, sick, travel, extras]
    colors = ["#3B82F6", "#10B981", "#EF4444", "#F59E0B", "#8B5CF6"]

    # סינון ערכים אפס
    filtered_labels = []
    filtered_data = []
    filtered_colors = []
    for i, val in enumerate(data):
        if val > 0:
            filtered_labels.append(labels[i])
            filtered_data.append(round(val, 2))
            filtered_colors.append(colors[i])

    return JSONResponse({
        "labels": filtered_labels if filtered_labels else labels,
        "datasets": [{
            "label": "סכום (ש\"ח)",
            "data": filtered_data if filtered_data else [0] * len(labels),
            "backgroundColor": filtered_colors if filtered_colors else colors
        }]
    })


def get_monthly_trends(year: int, months_back: int = 6) -> JSONResponse:
    """מגמות חודשיות - השוואה בין חודשים."""
    trends_total = []
    trends_hours = []
    labels = []

    current_month = datetime.now(config.LOCAL_TZ).month
    current_year = year

    for i in range(months_back - 1, -1, -1):
        target_month = current_month - i
        target_year = current_year

        while target_month <= 0:
            target_month += 12
            target_year -= 1

        _, grand_totals = _get_cached_summary(target_year, target_month)

        labels.append(f"{target_month:02d}/{target_year}")
        trends_total.append(grand_totals.get("total_payment", 0))
        trends_hours.append(grand_totals.get("total_hours", 0) / 60 if grand_totals.get("total_hours") else 0)

    return JSONResponse({
        "labels": labels,
        "datasets": [
            {
                "label": "שכר כולל (ש\"ח)",
                "data": trends_total,
                "borderColor": "#1f6feb",
                "backgroundColor": "rgba(31, 111, 235, 0.1)",
                "yAxisID": "y",
                "fill": True
            },
            {
                "label": "שעות עבודה",
                "data": trends_hours,
                "borderColor": "#10B981",
                "backgroundColor": "rgba(16, 185, 129, 0.1)",
                "yAxisID": "y1",
                "fill": True
            }
        ]
    })


def get_comparison_data(
    year1: int, month1: int,
    year2: int, month2: int
) -> JSONResponse:
    """השוואה בין שני חודשים."""
    _, totals1 = _get_cached_summary(year1, month1)
    _, totals2 = _get_cached_summary(year2, month2)

    categories = ["שכר כולל", "שעות רגילות", "שעות נוספות", "שבת", "כוננויות", "חופשות"]

    data1 = [
        totals1.get("total_payment", 0),
        totals1.get("calc100", 0) / 60,
        (totals1.get("calc125", 0) + totals1.get("calc150", 0)) / 60,
        (totals1.get("calc175", 0) + totals1.get("calc200", 0)) / 60,
        totals1.get("standby_payment", 0),
        totals1.get("vacation_payment", 0),
    ]

    data2 = [
        totals2.get("total_payment", 0),
        totals2.get("calc100", 0) / 60,
        (totals2.get("calc125", 0) + totals2.get("calc150", 0)) / 60,
        (totals2.get("calc175", 0) + totals2.get("calc200", 0)) / 60,
        totals2.get("standby_payment", 0),
        totals2.get("vacation_payment", 0),
    ]

    return JSONResponse({
        "labels": categories,
        "datasets": [
            {
                "label": f"{month1:02d}/{year1}",
                "data": data1,
                "backgroundColor": "#1f6feb"
            },
            {
                "label": f"{month2:02d}/{year2}",
                "data": data2,
                "backgroundColor": "#10B981"
            }
        ]
    })


def get_all_stats(year: int, month: int) -> JSONResponse:
    """
    מחזיר את כל הנתונים לגרפים בקריאה אחת.
    זה מונע קריאות רשת מרובות ומאיץ את הטעינה.
    """
    from collections import defaultdict

    # שליפת נתוני בסיס - אותו חישוב כמו ייצוא שכר
    summary_data, grand_totals = _get_cached_summary(year, month)

    housing_filter = get_housing_array_filter()

    with get_conn() as conn:
        # סוגי משמרות
        if housing_filter is not None:
            shift_rows = conn.execute("""
                SELECT st.name, COUNT(*) as count
                FROM time_reports tr
                JOIN shift_types st ON st.id = tr.shift_type_id
                JOIN apartments ap ON ap.id = tr.apartment_id
                WHERE EXTRACT(YEAR FROM tr.date) = %s
                  AND EXTRACT(MONTH FROM tr.date) = %s
                  AND ap.housing_array_id = %s
                GROUP BY st.id, st.name ORDER BY count DESC
            """, (year, month, housing_filter)).fetchall()
        else:
            shift_rows = conn.execute("""
                SELECT st.name, COUNT(*) as count
                FROM time_reports tr
                JOIN shift_types st ON st.id = tr.shift_type_id
                WHERE EXTRACT(YEAR FROM tr.date) = %s AND EXTRACT(MONTH FROM tr.date) = %s
                GROUP BY st.id, st.name ORDER BY count DESC
            """, (year, month)).fetchall()

        # === חישוב שכר לפי מערך - מדריך מופיע בכל מערך שעבד בו ===
        totals_by_housing = defaultdict(float)

        for person in summary_data:
            person_id = person["person_id"]
            total_payment = person["totals"].get("total_payment", 0)

            if total_payment <= 0:
                continue

            # מציאת כל מערכי הדיור שהמדריך עבד בהם
            if housing_filter is not None:
                housing_arrays = conn.execute("""
                    SELECT DISTINCT ha.name
                    FROM time_reports tr
                    JOIN apartments ap ON ap.id = tr.apartment_id
                    JOIN housing_arrays ha ON ha.id = ap.housing_array_id
                    WHERE tr.person_id = %s
                      AND EXTRACT(YEAR FROM tr.date) = %s
                      AND EXTRACT(MONTH FROM tr.date) = %s
                      AND ap.housing_array_id = %s
                """, (person_id, year, month, housing_filter)).fetchall()
            else:
                housing_arrays = conn.execute("""
                    SELECT DISTINCT ha.name
                    FROM time_reports tr
                    JOIN apartments ap ON ap.id = tr.apartment_id
                    JOIN housing_arrays ha ON ha.id = ap.housing_array_id
                    WHERE tr.person_id = %s
                      AND EXTRACT(YEAR FROM tr.date) = %s
                      AND EXTRACT(MONTH FROM tr.date) = %s
                """, (person_id, year, month)).fetchall()

            # הוספת כל השכר לכל מערך
            for row in housing_arrays:
                totals_by_housing[row["name"]] += total_payment

    # מיון לפי סכום
    sorted_housing = sorted(totals_by_housing.items(), key=lambda x: x[1], reverse=True)
    housing_labels = [name for name, _ in sorted_housing]
    housing_data = [round(total, 2) for _, total in sorted_housing]

    # === שכר לפי מדריך ===
    sorted_guides = sorted(summary_data, key=lambda x: x["totals"].get("total_payment", 0), reverse=True)[:20]
    guides_labels = [g["name"] for g in sorted_guides]
    guides_data = [g["totals"].get("total_payment", 0) for g in sorted_guides]

    # === התפלגות שעות ===
    calc100 = sum(p["totals"].get("calc100", 0) for p in summary_data) / 60
    calc125 = sum(p["totals"].get("calc125", 0) for p in summary_data) / 60
    calc150 = sum(p["totals"].get("calc150", 0) for p in summary_data) / 60
    calc175 = sum(p["totals"].get("calc175", 0) for p in summary_data) / 60
    calc200 = sum(p["totals"].get("calc200", 0) for p in summary_data) / 60
    hours_data = [calc100, calc125, calc150, calc175, calc200]

    # === כוננויות ותוספות ===
    extras_data = [
        sum(p["totals"].get("standby_payment", 0) for p in summary_data),
        sum(p["totals"].get("vacation_payment", 0) for p in summary_data),
        sum(p["totals"].get("sick_payment", 0) for p in summary_data),
        sum(p["totals"].get("travel", 0) for p in summary_data),
        sum(p["totals"].get("extras", 0) for p in summary_data),
    ]

    return JSONResponse({
        "summary": {
            "total_salary": sum(guides_data),
            "total_hours": sum(hours_data),
            "total_guides": len(summary_data),
            "total_standby": extras_data[0]
        },
        "by_housing": {
            "labels": housing_labels,
            "data": housing_data
        },
        "by_guide": {
            "labels": guides_labels,
            "data": guides_data
        },
        "hours": {
            "labels": ["100%", "125%", "150%", "175%", "200%"],
            "data": [round(h, 1) for h in hours_data]
        },
        "extras": {
            "labels": ["כוננויות", "חופשות", "מחלות", "נסיעות", "תוספות"],
            "data": [round(e, 2) for e in extras_data]
        },
        "shift_types": {
            "labels": [r["name"] for r in shift_rows],
            "data": [r["count"] for r in shift_rows]
        }
    })


def get_shift_types_distribution(year: int, month: int) -> JSONResponse:
    """התפלגות סוגי משמרות."""
    with get_conn() as conn:
        housing_filter = get_housing_array_filter()

        if housing_filter is not None:
            rows = conn.execute("""
                SELECT st.name, COUNT(*) as count
                FROM time_reports tr
                JOIN shift_types st ON st.id = tr.shift_type_id
                JOIN apartments ap ON ap.id = tr.apartment_id
                WHERE EXTRACT(YEAR FROM tr.date) = %s
                  AND EXTRACT(MONTH FROM tr.date) = %s
                  AND ap.housing_array_id = %s
                GROUP BY st.id, st.name
                ORDER BY count DESC
            """, (year, month, housing_filter)).fetchall()
        else:
            rows = conn.execute("""
                SELECT st.name, COUNT(*) as count
                FROM time_reports tr
                JOIN shift_types st ON st.id = tr.shift_type_id
                WHERE EXTRACT(YEAR FROM tr.date) = %s
                  AND EXTRACT(MONTH FROM tr.date) = %s
                GROUP BY st.id, st.name
                ORDER BY count DESC
            """, (year, month)).fetchall()

    labels = [r["name"] for r in rows]
    data = [r["count"] for r in rows]

    return JSONResponse({
        "labels": labels,
        "datasets": [{
            "label": "מספר משמרות",
            "data": data,
            "backgroundColor": _generate_colors(len(data))
        }]
    })


# =============================================================================
# APIs חדשים לדשבורד מורחב
# =============================================================================


def _aggregate_by_apartment(
    summary_data: List,
    year: int,
    month: int,
    housing_array_id: Optional[int] = None,
) -> dict:
    """
    אגרגציה של נתוני סיכום לפי דירה.

    Args:
        housing_array_id: אם מוגדר — רק דירות ודיווחים באותו מערך דיור.

    Returns:
        dict: {apartment_id: {name, housing_array_id, housing_array_name, totals}}
    """
    from collections import defaultdict

    with get_conn() as conn:
        # שליפת כל הדירות עם מערך הדיור שלהן
        apartments = {}
        if housing_array_id is not None:
            rows = conn.execute("""
                SELECT ap.id, ap.name, ap.housing_array_id, ha.name as housing_array_name
                FROM apartments ap
                LEFT JOIN housing_arrays ha ON ha.id = ap.housing_array_id
                WHERE ap.housing_array_id = %s
            """, (housing_array_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT ap.id, ap.name, ap.housing_array_id, ha.name as housing_array_name
                FROM apartments ap
                LEFT JOIN housing_arrays ha ON ha.id = ap.housing_array_id
            """).fetchall()
        for r in rows:
            apartments[r["id"]] = {
                "name": r["name"],
                "housing_array_id": r["housing_array_id"],
                "housing_array_name": r["housing_array_name"] or "ללא מערך"
            }

        # שליפת קישור מדריך+דירה -> תשלומים מפורטים
        # צריך לשלוף את הדיווחים עצמם כדי לדעת איזה תשלום שייך לאיזו דירה
        if housing_array_id is not None:
            reports = conn.execute("""
                SELECT tr.person_id, tr.apartment_id
                FROM time_reports tr
                JOIN apartments ap ON ap.id = tr.apartment_id
                WHERE EXTRACT(YEAR FROM tr.date) = %s
                  AND EXTRACT(MONTH FROM tr.date) = %s
                  AND ap.housing_array_id = %s
            """, (year, month, housing_array_id)).fetchall()
        else:
            reports = conn.execute("""
                SELECT tr.person_id, tr.apartment_id
                FROM time_reports tr
                WHERE EXTRACT(YEAR FROM tr.date) = %s
                  AND EXTRACT(MONTH FROM tr.date) = %s
            """, (year, month)).fetchall()

    # מיפוי מדריך -> דירות
    person_apartments = defaultdict(set)
    for r in reports:
        person_apartments[r["person_id"]].add(r["apartment_id"])

    # אגרגציה לפי דירה
    apartment_totals = defaultdict(lambda: {
        "total_payment": 0,
        "total_hours": 0,
        "calc100": 0, "calc125": 0, "calc150": 0, "calc175": 0, "calc200": 0,
        "standby_payment": 0,
        "guides_count": 0
    })

    for person in summary_data:
        person_id = person["person_id"]
        totals = person["totals"]
        person_apts = person_apartments.get(person_id, set())

        if not person_apts:
            continue

        # חלוקה שווה בין הדירות של המדריך (אם עבד ביותר מדירה אחת)
        apt_count = len(person_apts)
        for apt_id in person_apts:
            apartment_totals[apt_id]["total_payment"] += totals.get("total_payment", 0) / apt_count
            apartment_totals[apt_id]["total_hours"] += totals.get("total_hours", 0) / apt_count
            apartment_totals[apt_id]["calc100"] += totals.get("calc100", 0) / apt_count
            apartment_totals[apt_id]["calc125"] += totals.get("calc125", 0) / apt_count
            apartment_totals[apt_id]["calc150"] += totals.get("calc150", 0) / apt_count
            apartment_totals[apt_id]["calc175"] += totals.get("calc175", 0) / apt_count
            apartment_totals[apt_id]["calc200"] += totals.get("calc200", 0) / apt_count
            apartment_totals[apt_id]["standby_payment"] += totals.get("standby_payment", 0) / apt_count
            apartment_totals[apt_id]["guides_count"] += 1

    # בניית התוצאה הסופית
    result = {}
    for apt_id, totals in apartment_totals.items():
        if apt_id in apartments:
            result[apt_id] = {
                **apartments[apt_id],
                "totals": totals
            }

    return result


def get_compare_housing_arrays(
    request: Request,
    year: int,
    month: int,
    array_ids: List[int],
) -> JSONResponse:
    """
    השוואת 2-5 מערכי דיור - שכר ב-2 החודשים האחרונים.

    Args:
        year: שנה נבחרת
        month: חודש נבחר
        array_ids: רשימת מזהי מערכי דיור להשוואה (2-5)
    """
    from collections import defaultdict

    if is_framework_manager(request):
        return JSONResponse(
            {"error": "השוואת מערכים אינה זמינה למנהל מסגרת"},
            status_code=403,
        )

    if not array_ids or len(array_ids) < 2:
        return JSONResponse({"error": "יש לבחור לפחות 2 מערכי דיור"}, status_code=400)
    if len(array_ids) > 5:
        array_ids = array_ids[:5]

    # חישוב החודש הקודם
    prev_month = month - 1
    prev_year = year
    if prev_month <= 0:
        prev_month = 12
        prev_year -= 1

    with get_conn() as conn:
        # שליפת שמות המערכים
        array_names = {}
        placeholders = ",".join(["%s"] * len(array_ids))
        rows = conn.execute(f"""
            SELECT id, name FROM housing_arrays WHERE id IN ({placeholders})
        """, tuple(array_ids)).fetchall()
        for r in rows:
            array_names[r["id"]] = r["name"]

        # שליפת קישור מדריך -> מערך לשני החודשים
        def get_person_to_housing(y: int, m: int) -> dict:
            rows = conn.execute("""
                SELECT DISTINCT tr.person_id, ap.housing_array_id
                FROM time_reports tr
                JOIN apartments ap ON ap.id = tr.apartment_id
                WHERE EXTRACT(YEAR FROM tr.date) = %s
                  AND EXTRACT(MONTH FROM tr.date) = %s
                  AND ap.housing_array_id = ANY(%s)
            """, (y, m, array_ids)).fetchall()
            return {r["person_id"]: r["housing_array_id"] for r in rows}

    # סיכומים לכל חודש
    summary_curr, _ = _get_cached_summary(year, month)
    summary_prev, _ = _get_cached_summary(prev_year, prev_month)

    person_to_housing_curr = get_person_to_housing(year, month)
    person_to_housing_prev = get_person_to_housing(prev_year, prev_month)

    # אגרגציה לפי מערך
    def aggregate_by_array(summary_data: List, person_to_housing: dict) -> dict:
        totals = defaultdict(float)
        for person in summary_data:
            pid = person["person_id"]
            if pid in person_to_housing:
                hid = person_to_housing[pid]
                totals[hid] += person["totals"].get("total_payment", 0)
        return totals

    totals_curr = aggregate_by_array(summary_curr, person_to_housing_curr)
    totals_prev = aggregate_by_array(summary_prev, person_to_housing_prev)

    # בניית הנתונים לגרף
    labels = [array_names.get(aid, f"מערך {aid}") for aid in array_ids]
    data_curr = [totals_curr.get(aid, 0) for aid in array_ids]
    data_prev = [totals_prev.get(aid, 0) for aid in array_ids]

    return JSONResponse({
        "labels": labels,
        "datasets": [
            {
                "label": f"{month:02d}/{year}",
                "data": data_curr,
                "backgroundColor": "#1f6feb"
            },
            {
                "label": f"{prev_month:02d}/{prev_year}",
                "data": data_prev,
                "backgroundColor": "#10B981"
            }
        ]
    })


def get_top_apartments_by_percent(
    year: int,
    month: int,
    percent: int = 100,
    limit: int = 10,
) -> JSONResponse:
    """
    Top 10 דירות עם הכי הרבה שכר באחוז מסוים.

    Args:
        year: שנה
        month: חודש
        percent: אחוז לסינון (100/125/150/175/200)
        limit: מספר דירות להציג
    """
    summary_data, _ = _get_cached_summary(year, month)
    apartment_data = _aggregate_by_apartment(
        summary_data, year, month, get_housing_array_filter()
    )

    # מיפוי אחוז לשדה
    percent_field = f"calc{percent}"
    if percent_field not in ["calc100", "calc125", "calc150", "calc175", "calc200"]:
        percent_field = "calc100"

    # מיון לפי השדה הנבחר
    sorted_apartments = sorted(
        apartment_data.items(),
        key=lambda x: x[1]["totals"].get(percent_field, 0),
        reverse=True
    )[:limit]

    labels = [apt["name"] for _, apt in sorted_apartments]
    data = [apt["totals"].get(percent_field, 0) / 60 for _, apt in sorted_apartments]  # המרה לשעות

    return JSONResponse({
        "labels": labels,
        "datasets": [{
            "label": f"שעות {percent}%",
            "data": [round(d, 1) for d in data],
            "backgroundColor": "#8B5CF6"
        }]
    })


def _reject_housing_param_mismatch(housing_array_id: int) -> Optional[JSONResponse]:
    """403 אם מנסים לשלוף מערך דיור שאינו הפילטר הפעיל (מנהל מסגרת / עוגייה)."""
    hf = get_housing_array_filter()
    if hf is not None and housing_array_id != hf:
        return JSONResponse({"error": "אין הרשאה למערך דיור זה"}, status_code=403)
    return None


def get_apartments_in_array(
    year: int,
    month: int,
    housing_array_id: int,
) -> JSONResponse:
    """
    כל הדירות במערך דיור מסוים - סך השכר.

    Args:
        year: שנה
        month: חודש
        housing_array_id: מזהה מערך דיור
    """
    denied = _reject_housing_param_mismatch(housing_array_id)
    if denied:
        return denied

    summary_data, _ = _get_cached_summary(year, month)
    apartment_data = _aggregate_by_apartment(summary_data, year, month, housing_array_id)

    # סינון לפי מערך דיור
    filtered = {
        apt_id: apt
        for apt_id, apt in apartment_data.items()
        if apt["housing_array_id"] == housing_array_id
    }

    # מיון לפי שכר
    sorted_apartments = sorted(
        filtered.items(),
        key=lambda x: x[1]["totals"].get("total_payment", 0),
        reverse=True
    )

    labels = [apt["name"] for _, apt in sorted_apartments]
    data = [apt["totals"].get("total_payment", 0) for _, apt in sorted_apartments]

    return JSONResponse({
        "labels": labels,
        "datasets": [{
            "label": "שכר כולל (ש\"ח)",
            "data": [round(d, 2) for d in data],
            "backgroundColor": _generate_colors(len(data))
        }]
    })


def get_apartments_in_array_by_percent(
    year: int,
    month: int,
    housing_array_id: int
) -> JSONResponse:
    """
    כל הדירות במערך דיור - פילוח לפי אחוזים.

    Args:
        year: שנה
        month: חודש
        housing_array_id: מזהה מערך דיור
    """
    denied = _reject_housing_param_mismatch(housing_array_id)
    if denied:
        return denied

    summary_data, _ = _get_cached_summary(year, month)
    apartment_data = _aggregate_by_apartment(summary_data, year, month, housing_array_id)

    # סינון לפי מערך דיור
    filtered = {
        apt_id: apt
        for apt_id, apt in apartment_data.items()
        if apt["housing_array_id"] == housing_array_id
    }

    # מיון לפי שכר כולל
    sorted_apartments = sorted(
        filtered.items(),
        key=lambda x: x[1]["totals"].get("total_payment", 0),
        reverse=True
    )

    labels = [apt["name"] for _, apt in sorted_apartments]

    # בניית datasets לכל אחוז
    datasets = []
    percent_colors = {
        100: "#4CAF50",
        125: "#8BC34A",
        150: "#FFC107",
        175: "#FF5722",
        200: "#E91E63"
    }

    for percent in [100, 125, 150, 175, 200]:
        field = f"calc{percent}"
        data = [apt["totals"].get(field, 0) / 60 for _, apt in sorted_apartments]
        # רק אם יש נתונים
        if sum(data) > 0:
            datasets.append({
                "label": f"{percent}%",
                "data": [round(d, 1) for d in data],
                "backgroundColor": percent_colors[percent]
            })

    return JSONResponse({
        "labels": labels,
        "datasets": datasets
    })


def get_apartment_details(
    year: int,
    month: int,
    apartment_id: int,
) -> JSONResponse:
    """
    פרטי דירה - שעות ושכר לפי סוג משמרת + מדריכים.

    Args:
        year: שנה
        month: חודש
        apartment_id: מזהה דירה
    """
    hf = get_housing_array_filter()
    with get_conn() as conn:
        # שליפת שם הדירה
        apt_row = conn.execute(
            "SELECT name, housing_array_id FROM apartments WHERE id = %s",
            (apartment_id,),
        ).fetchone()
        if hf is not None and (
            not apt_row or apt_row["housing_array_id"] != hf
        ):
            return JSONResponse(
                {"error": "אין הרשאה לדירה זו"},
                status_code=403,
            )
        apartment_name = apt_row["name"] if apt_row else f"דירה {apartment_id}"

        # שליפת כל המדריכים שעבדו בדירה בחודש
        guides = conn.execute("""
            SELECT DISTINCT p.id, p.name
            FROM time_reports tr
            JOIN people p ON p.id = tr.person_id
            WHERE tr.apartment_id = %s
              AND EXTRACT(YEAR FROM tr.date) = %s
              AND EXTRACT(MONTH FROM tr.date) = %s
            ORDER BY p.name
        """, (apartment_id, year, month)).fetchall()

        # שליפת סוגי משמרות בדירה
        shift_types = conn.execute("""
            SELECT st.id, st.name, COUNT(*) as count,
                   SUM(EXTRACT(EPOCH FROM (tr.end_time - tr.start_time))/60) as total_minutes
            FROM time_reports tr
            JOIN shift_types st ON st.id = tr.shift_type_id
            WHERE tr.apartment_id = %s
              AND EXTRACT(YEAR FROM tr.date) = %s
              AND EXTRACT(MONTH FROM tr.date) = %s
            GROUP BY st.id, st.name
            ORDER BY count DESC
        """, (apartment_id, year, month)).fetchall()

    # נתוני משמרות
    shift_labels = [s["name"] for s in shift_types]
    shift_hours = [round((s["total_minutes"] or 0) / 60, 1) for s in shift_types]
    shift_counts = [s["count"] for s in shift_types]

    # נתוני מדריכים - שימוש ב-summary הקיים
    guide_labels = [g["name"] for g in guides]
    guide_salaries = []

    summary_data, _ = _get_cached_summary(year, month)

    for guide in guides:
        for person in summary_data:
            if person["person_id"] == guide["id"]:
                # הערכה - חלק יחסי מהשכר (אם עבד ביותר מדירה אחת)
                guide_salaries.append(person["totals"].get("total_payment", 0))
                break
        else:
            guide_salaries.append(0)

    return JSONResponse({
        "apartment_name": apartment_name,
        "shifts": {
            "labels": shift_labels,
            "datasets": [
                {
                    "label": "שעות",
                    "data": shift_hours,
                    "backgroundColor": "#1f6feb",
                    "yAxisID": "y"
                },
                {
                    "label": "מספר משמרות",
                    "data": shift_counts,
                    "backgroundColor": "#10B981",
                    "yAxisID": "y1"
                }
            ]
        },
        "guides": {
            "labels": guide_labels,
            "datasets": [{
                "label": "שכר כולל (ש\"ח)",
                "data": [round(s, 2) for s in guide_salaries],
                "backgroundColor": _generate_colors(len(guide_salaries))
            }]
        }
    })


def get_guide_yearly(person_id: int, year: int) -> JSONResponse:
    """
    מגמת שכר של מדריך ב-12 החודשים האחרונים.

    Args:
        person_id: מזהה מדריך
        year: שנה (נקודת התחלה)
    """
    hf = get_housing_array_filter()
    if hf is not None:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT housing_array_id FROM people WHERE id = %s",
                (person_id,),
            ).fetchone()
        if not row or row["housing_array_id"] != hf:
            return JSONResponse(
                {"error": "אין הרשאה למדריך זה"},
                status_code=403,
            )

    from app_utils import get_daily_segments_data, aggregate_daily_segments_to_monthly
    from core.time_utils import get_shabbat_times_cache
    from core.history import get_minimum_wage_for_month
    from core.database import PostgresConnection

    labels = []
    salary_data = []
    hours_data = []

    # 12 חודשים אחורה מהחודש הנוכחי
    current_month = datetime.now(config.LOCAL_TZ).month
    current_year = year

    with get_conn() as conn:
        # שליפת שם המדריך
        guide_row = conn.execute(
            "SELECT name FROM people WHERE id = %s", (person_id,)
        ).fetchone()
        guide_name = guide_row["name"] if guide_row else f"מדריך {person_id}"

        shabbat_cache = get_shabbat_times_cache(conn.conn)
        conn_wrapper = PostgresConnection(conn.conn, use_pool=False)

        for i in range(11, -1, -1):
            target_month = current_month - i
            target_year = current_year

            while target_month <= 0:
                target_month += 12
                target_year -= 1

            minimum_wage = get_minimum_wage_for_month(conn.conn, target_year, target_month)

            try:
                daily_segments, _ = get_daily_segments_data(
                    conn_wrapper, person_id, target_year, target_month,
                    shabbat_cache, minimum_wage
                )
                monthly_totals = aggregate_daily_segments_to_monthly(
                    conn_wrapper, daily_segments, person_id,
                    target_year, target_month, minimum_wage
                )

                salary = monthly_totals.get("total_payment", 0)
                hours = monthly_totals.get("total_hours", 0) / 60
            except Exception:
                salary = 0
                hours = 0

            labels.append(f"{target_month:02d}/{target_year}")
            salary_data.append(round(salary, 2))
            hours_data.append(round(hours, 1))

    return JSONResponse({
        "guide_name": guide_name,
        "labels": labels,
        "datasets": [
            {
                "label": "שכר (ש\"ח)",
                "data": salary_data,
                "borderColor": "#1f6feb",
                "backgroundColor": "rgba(31, 111, 235, 0.1)",
                "fill": True,
                "yAxisID": "y"
            },
            {
                "label": "שעות",
                "data": hours_data,
                "borderColor": "#10B981",
                "backgroundColor": "rgba(16, 185, 129, 0.1)",
                "fill": True,
                "yAxisID": "y1"
            }
        ]
    })


def get_housing_arrays_list() -> JSONResponse:
    """רשימת מערכי הדיור (מסוננת כשמוגדר פילטר מערך — מנהל מסגרת / עוגייה)."""
    hf = get_housing_array_filter()
    with get_conn() as conn:
        if hf is not None:
            rows = conn.execute(
                "SELECT id, name FROM housing_arrays WHERE id = %s ORDER BY name",
                (hf,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name FROM housing_arrays ORDER BY name"
            ).fetchall()

    return JSONResponse({
        "arrays": [{"id": r["id"], "name": r["name"]} for r in rows]
    })


def get_apartments_list(housing_array_id: Optional[int] = None) -> JSONResponse:
    """רשימת דירות, אופציונלי לפי מערך דיור."""
    hf = get_housing_array_filter()
    if hf is not None:
        if housing_array_id is not None and housing_array_id != hf:
            return JSONResponse(
                {"error": "אין הרשאה למערך דיור זה"},
                status_code=403,
            )
        housing_array_id = hf

    with get_conn() as conn:
        if housing_array_id:
            rows = conn.execute("""
                SELECT id, name FROM apartments
                WHERE housing_array_id = %s
                ORDER BY name
            """, (housing_array_id,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name FROM apartments ORDER BY name"
            ).fetchall()

    return JSONResponse({
        "apartments": [{"id": r["id"], "name": r["name"]} for r in rows]
    })


def get_guides_list() -> JSONResponse:
    """רשימת מדריכים פעילים (מסוננת לפי מערך כשמוגדר פילטר)."""
    hf = get_housing_array_filter()
    with get_conn() as conn:
        if hf is not None:
            rows = conn.execute("""
                SELECT id, name FROM people
                WHERE is_active::integer = 1 AND housing_array_id = %s
                ORDER BY name
            """, (hf,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, name FROM people
                WHERE is_active::integer = 1
                ORDER BY name
            """).fetchall()

    return JSONResponse({
        "guides": [{"id": r["id"], "name": r["name"]} for r in rows]
    })


def get_overtime_by_housing_array(year: int, month: int) -> JSONResponse:
    """
    שעות נוספות לפי מערך דיור עם פירוט מדריכים.

    מחזיר רשימת מערכי דיור עם שעות נוספות (125%, 150%, 175%, 200%) ועלותן הכספית,
    כולל פירוט מדריכים עם חריגות ומייל הרכז לכל מערך.
    """
    from collections import defaultdict

    summary_data, _ = _get_cached_summary(year, month)

    # מסנן מדריכים עם שעות מעל 100% (125%, 150% חול בלבד, 175%, 200%)
    overtime_people = [
        p for p in summary_data
        if (p["totals"].get("calc125", 0) > 0
            or p["totals"].get("calc150_overtime", 0) > 0
            or p["totals"].get("calc175", 0) > 0
            or p["totals"].get("calc200", 0) > 0)
    ]

    if not overtime_people:
        return JSONResponse({
            "arrays": [],
            "totals": {
                "hours_125": 0, "hours_150": 0, "hours_175": 0, "hours_200": 0,
                "cost_125": 0, "cost_150": 0, "cost_175": 0, "cost_200": 0,
                "total_cost": 0, "guides_with_overtime": 0, "arrays_count": 0
            }
        })

    person_ids = [p["person_id"] for p in overtime_people]
    hf = get_housing_array_filter()

    with get_conn() as conn:
        # מציאת מערך הדיור לכל מדריך לפי הדירות שעבד בהן בחודש
        if hf is not None:
            person_housing_rows = conn.execute("""
                SELECT DISTINCT tr.person_id, ha.id AS housing_array_id, ha.name AS housing_array_name
                FROM time_reports tr
                JOIN apartments ap ON ap.id = tr.apartment_id
                JOIN housing_arrays ha ON ha.id = ap.housing_array_id
                WHERE tr.person_id = ANY(%s)
                  AND EXTRACT(YEAR FROM tr.date) = %s
                  AND EXTRACT(MONTH FROM tr.date) = %s
                  AND ap.housing_array_id = %s
            """, (person_ids, year, month, hf)).fetchall()
        else:
            person_housing_rows = conn.execute("""
                SELECT DISTINCT tr.person_id, ha.id AS housing_array_id, ha.name AS housing_array_name
                FROM time_reports tr
                JOIN apartments ap ON ap.id = tr.apartment_id
                JOIN housing_arrays ha ON ha.id = ap.housing_array_id
                WHERE tr.person_id = ANY(%s)
                  AND EXTRACT(YEAR FROM tr.date) = %s
                  AND EXTRACT(MONTH FROM tr.date) = %s
            """, (person_ids, year, month)).fetchall()

        # שליפת פרטי רכז לכל מערך דיור
        coordinator_rows = conn.execute("""
            SELECT p.housing_array_id, p.email, p.name AS coordinator_name
            FROM people p
            JOIN roles r ON r.id = p.role_id
            WHERE r.name = 'framework_manager'
              AND p.is_active::integer = 1
              AND p.housing_array_id IS NOT NULL
        """).fetchall()

    # מיפוי מדריך -> מערכי דיור
    person_to_arrays: dict = defaultdict(list)
    for row in person_housing_rows:
        person_to_arrays[row["person_id"]].append({
            "id": row["housing_array_id"],
            "name": row["housing_array_name"],
        })

    # מיפוי מערך -> פרטי רכז
    array_coordinator: dict = {}
    for row in coordinator_rows:
        array_coordinator[row["housing_array_id"]] = {
            "email": row["email"] or "",
            "name": row["coordinator_name"],
        }

    # אגרגציה לפי מערך דיור
    arrays_data: dict = defaultdict(lambda: {
        "name": "",
        "hours_125": 0.0, "hours_150": 0.0, "hours_175": 0.0, "hours_200": 0.0,
        "cost_125": 0.0, "cost_150": 0.0, "cost_175": 0.0, "cost_200": 0.0,
        "guides": [],
    })

    for person in overtime_people:
        pid = person["person_id"]
        totals = person["totals"]
        hours_125 = totals.get("calc125", 0) / 60
        hours_150 = totals.get("calc150_overtime", 0) / 60   # חול בלבד, לא שבת
        hours_175 = totals.get("calc175", 0) / 60
        hours_200 = totals.get("calc200", 0) / 60
        cost_125 = totals.get("payment_calc125", 0)
        cost_150 = totals.get("payment_calc150_overtime", 0)  # חול בלבד
        cost_175 = totals.get("payment_calc175", 0)
        cost_200 = totals.get("payment_calc200", 0)
        total_cost = cost_125 + cost_150 + cost_175 + cost_200

        for array_info in person_to_arrays.get(pid, []):
            array_id = array_info["id"]
            arrays_data[array_id]["name"] = array_info["name"]
            arrays_data[array_id]["hours_125"] += hours_125
            arrays_data[array_id]["hours_150"] += hours_150
            arrays_data[array_id]["hours_175"] += hours_175
            arrays_data[array_id]["hours_200"] += hours_200
            arrays_data[array_id]["cost_125"] += cost_125
            arrays_data[array_id]["cost_150"] += cost_150
            arrays_data[array_id]["cost_175"] += cost_175
            arrays_data[array_id]["cost_200"] += cost_200
            arrays_data[array_id]["guides"].append({
                "name": person["name"],
                "person_id": pid,
                "hours_125": round(hours_125, 1),
                "hours_150": round(hours_150, 1),
                "hours_175": round(hours_175, 1),
                "hours_200": round(hours_200, 1),
                "cost_125": round(cost_125, 2),
                "cost_150": round(cost_150, 2),
                "cost_175": round(cost_175, 2),
                "cost_200": round(cost_200, 2),
                "total_overtime_cost": round(total_cost, 2),
            })

    # בניית רשימה ממוינת לפי עלות שעות נוספות
    result_arrays = []
    for array_id, data in arrays_data.items():
        total_cost = data["cost_125"] + data["cost_150"] + data["cost_175"] + data["cost_200"]
        coordinator = array_coordinator.get(array_id, {"email": "", "name": ""})
        result_arrays.append({
            "id": array_id,
            "name": data["name"],
            "coordinator_email": coordinator["email"],
            "coordinator_name": coordinator["name"],
            "hours_125": round(data["hours_125"], 1),
            "hours_150": round(data["hours_150"], 1),
            "hours_175": round(data["hours_175"], 1),
            "hours_200": round(data["hours_200"], 1),
            "cost_125": round(data["cost_125"], 2),
            "cost_150": round(data["cost_150"], 2),
            "cost_175": round(data["cost_175"], 2),
            "cost_200": round(data["cost_200"], 2),
            "total_overtime_cost": round(total_cost, 2),
            "guides_count": len(data["guides"]),
            "guides": sorted(data["guides"], key=lambda g: -g["total_overtime_cost"]),
        })

    result_arrays.sort(key=lambda a: -a["total_overtime_cost"])

    # סיכומים כלליים — 150% = חול בלבד (calc150_overtime), לא שבת
    total_hours_125 = sum(p["totals"].get("calc125", 0) for p in overtime_people) / 60
    total_hours_150 = sum(p["totals"].get("calc150_overtime", 0) for p in overtime_people) / 60
    total_hours_175 = sum(p["totals"].get("calc175", 0) for p in overtime_people) / 60
    total_hours_200 = sum(p["totals"].get("calc200", 0) for p in overtime_people) / 60
    total_cost_125 = sum(p["totals"].get("payment_calc125", 0) for p in overtime_people)
    total_cost_150 = sum(p["totals"].get("payment_calc150_overtime", 0) for p in overtime_people)
    total_cost_175 = sum(p["totals"].get("payment_calc175", 0) for p in overtime_people)
    total_cost_200 = sum(p["totals"].get("payment_calc200", 0) for p in overtime_people)

    return JSONResponse({
        "arrays": result_arrays,
        "totals": {
            "hours_125": round(total_hours_125, 1),
            "hours_150": round(total_hours_150, 1),
            "hours_175": round(total_hours_175, 1),
            "hours_200": round(total_hours_200, 1),
            "cost_125": round(total_cost_125, 2),
            "cost_150": round(total_cost_150, 2),
            "cost_175": round(total_cost_175, 2),
            "cost_200": round(total_cost_200, 2),
            "total_cost": round(total_cost_125 + total_cost_150 + total_cost_175 + total_cost_200, 2),
            "guides_with_overtime": len(overtime_people),
            "arrays_count": len(result_arrays),
        },
    })


async def send_overtime_email_route(request: Request, year: int, month: int) -> JSONResponse:
    """
    שליחת דוח שעות נוספות לרכז של מערך דיור.

    Body: { housing_array_id: int, custom_email?: string }
    """
    import asyncio
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from services.email_service import get_email_settings

    try:
        body = await request.json()
        housing_array_id = body.get("housing_array_id")
        custom_email = body.get("custom_email", "").strip()

        if not housing_array_id:
            return JSONResponse({"success": False, "error": "חסר housing_array_id"})

        managed_hid = get_user_housing_array(request)
        if managed_hid is not None and int(housing_array_id) != int(managed_hid):
            return JSONResponse(
                {"success": False, "error": "אין הרשאה לשלוח דוח למערך זה"},
                status_code=403,
            )

        # שליפת נתוני שעות נוספות
        overtime_response = get_overtime_by_housing_array(year, month)
        overtime_json = overtime_response.body
        import json as _json
        overtime_data = _json.loads(overtime_json)

        # מציאת המערך הנבחר
        target_array = next(
            (a for a in overtime_data["arrays"] if a["id"] == housing_array_id), None
        )
        if not target_array:
            return JSONResponse({"success": False, "error": "לא נמצאו שעות נוספות למערך זה"})

        # קביעת כתובת מייל
        to_email = custom_email or target_array.get("coordinator_email", "")
        if not to_email:
            return JSONResponse({"success": False, "error": "לא הוגדרה כתובת מייל לרכז המערך"})

        with get_conn() as conn:
            settings = get_email_settings(conn)

        if not settings:
            return JSONResponse({"success": False, "error": "הגדרות מייל לא נמצאו"})

        # בניית תוכן HTML לאימייל
        guides_rows = "".join(
            f"""<tr style="border-bottom:1px solid #eee;">
                <td style="padding:8px 12px;">{g['name']}</td>
                <td style="padding:8px 12px; text-align:center; background:#fffbeb;">{g['hours_125']:.1f}</td>
                <td style="padding:8px 12px; text-align:center; background:#fff1f2;">{g['hours_150']:.1f}</td>
                <td style="padding:8px 12px; text-align:center; background:#fdf4ff;">{g['hours_175']:.1f}</td>
                <td style="padding:8px 12px; text-align:center; background:#f5f3ff;">{g['hours_200']:.1f}</td>
                <td style="padding:8px 12px; text-align:center; font-weight:700;">₪{g['total_overtime_cost']:,.0f}</td>
            </tr>"""
            for g in target_array["guides"]
        )

        total_hours_ot = target_array['hours_125'] + target_array['hours_150']
        total_hours_shabbat = target_array['hours_175'] + target_array['hours_200']

        html_body = f"""
        <html dir="rtl"><body style="font-family: Arial, sans-serif; color: #1e293b; background: #f8fafc; margin:0; padding:20px;">
        <div style="max-width:750px; margin:0 auto; background:white; border-radius:12px; box-shadow:0 4px 12px rgba(0,0,0,0.08); overflow:hidden;">
            <div style="background:linear-gradient(135deg,#667eea,#764ba2); padding:24px 28px; color:white;">
                <h2 style="margin:0; font-size:20px;">דוח שעות נוספות — {target_array['name']}</h2>
                <p style="margin:6px 0 0; opacity:0.85; font-size:14px;">חודש {month:02d}/{year}</p>
            </div>
            <div style="padding:24px 28px;">
                <div style="display:flex; gap:12px; margin-bottom:24px; flex-wrap:wrap;">
                    <div style="flex:1; min-width:100px; background:#fef3c7; border-radius:10px; padding:14px; text-align:center;">
                        <div style="font-size:22px; font-weight:700; color:#d97706;">{target_array['hours_125']:.1f}</div>
                        <div style="font-size:11px; color:#92400e; margin-top:4px;">שעות 125%</div>
                    </div>
                    <div style="flex:1; min-width:100px; background:#fee2e2; border-radius:10px; padding:14px; text-align:center;">
                        <div style="font-size:22px; font-weight:700; color:#dc2626;">{target_array['hours_150']:.1f}</div>
                        <div style="font-size:11px; color:#991b1b; margin-top:4px;">שעות 150%</div>
                    </div>
                    <div style="flex:1; min-width:100px; background:#fce7f3; border-radius:10px; padding:14px; text-align:center;">
                        <div style="font-size:22px; font-weight:700; color:#9d174d;">{target_array['hours_175']:.1f}</div>
                        <div style="font-size:11px; color:#831843; margin-top:4px;">שעות 175% (שבת)</div>
                    </div>
                    <div style="flex:1; min-width:100px; background:#f5f3ff; border-radius:10px; padding:14px; text-align:center;">
                        <div style="font-size:22px; font-weight:700; color:#7c3aed;">{target_array['hours_200']:.1f}</div>
                        <div style="font-size:11px; color:#5b21b6; margin-top:4px;">שעות 200% (שבת)</div>
                    </div>
                    <div style="flex:1; min-width:100px; background:#ecfdf5; border-radius:10px; padding:14px; text-align:center;">
                        <div style="font-size:20px; font-weight:700; color:#065f46;">₪{target_array['total_overtime_cost']:,.0f}</div>
                        <div style="font-size:11px; color:#064e3b; margin-top:4px;">עלות כוללת</div>
                    </div>
                </div>
                <table style="width:100%; border-collapse:collapse; font-size:13px;">
                    <thead>
                        <tr style="color:#64748b; font-size:12px;">
                            <th style="padding:9px 10px; text-align:right; font-weight:600; background:#f1f5f9;">שם מדריך</th>
                            <th style="padding:9px 10px; text-align:center; font-weight:600; background:#fef9c3;">ש׳ 125%</th>
                            <th style="padding:9px 10px; text-align:center; font-weight:600; background:#fee2e2;">ש׳ 150%</th>
                            <th style="padding:9px 10px; text-align:center; font-weight:600; background:#fce7f3;">ש׳ 175%</th>
                            <th style="padding:9px 10px; text-align:center; font-weight:600; background:#f5f3ff;">ש׳ 200%</th>
                            <th style="padding:9px 10px; text-align:center; font-weight:600; background:#f1f5f9;">סה"כ עלות</th>
                        </tr>
                    </thead>
                    <tbody>{guides_rows}</tbody>
                    <tfoot>
                        <tr style="background:#f8fafc; font-weight:700; border-top:2px solid #e2e8f0;">
                            <td style="padding:9px 10px;">סה"כ</td>
                            <td style="padding:9px 10px; text-align:center; color:#d97706;">{target_array['hours_125']:.1f}</td>
                            <td style="padding:9px 10px; text-align:center; color:#dc2626;">{target_array['hours_150']:.1f}</td>
                            <td style="padding:9px 10px; text-align:center; color:#9d174d;">{target_array['hours_175']:.1f}</td>
                            <td style="padding:9px 10px; text-align:center; color:#7c3aed;">{target_array['hours_200']:.1f}</td>
                            <td style="padding:9px 10px; text-align:center; color:#065f46;">₪{target_array['total_overtime_cost']:,.0f}</td>
                        </tr>
                    </tfoot>
                </table>
            </div>
            <div style="padding:16px 28px; background:#f8fafc; border-top:1px solid #e2e8f0; text-align:center; font-size:12px; color:#94a3b8;">
                הודעה זו נשלחה אוטומטית ממערכת DiyurCalc — עמותת צהר הלב
            </div>
        </div>
        </body></html>
        """

        def _send() -> dict:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"דוח שעות נוספות — {target_array['name']} — {month:02d}/{year}"
            msg["From"] = f"{settings.get('from_name', 'צהר')} <{settings['from_email']}>"
            msg["To"] = to_email
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            use_ssl = settings.get("smtp_secure", False)
            smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
            with smtp_cls(settings["smtp_host"], int(settings["smtp_port"])) as server:
                if not use_ssl:
                    server.starttls()
                server.login(settings["smtp_user"], settings["smtp_password"])
                server.sendmail(settings["from_email"], to_email, msg.as_string())
            return {"success": True, "message": f"הדוח נשלח בהצלחה אל {to_email}"}

        result = await asyncio.to_thread(_send)
        return JSONResponse(result)

    except Exception as e:
        logger.error(f"Error in send_overtime_email_route: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)})

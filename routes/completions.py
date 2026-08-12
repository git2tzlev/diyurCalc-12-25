"""Retroactive completion views and difference reports."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.responses import StreamingResponse

from core.config import config
from core.constants import MANUAL_COMPLETION_SYMBOLS
from core.database import (
    get_conn,
    get_default_period,
    get_housing_array_filter,
    is_demo_mode,
    set_demo_mode,
    set_housing_array_filter,
)
from core.auth import create_action_token, validate_action_token
from core.payment_period import get_payment_period_completions
from services.gesher_archive import get_gesher_export_file, list_gesher_export_files
from services.gesher_difference import (
    COMPLETION_QUANTITY_TARGET_SYMBOLS,
    build_completion_gesher_audit,
    build_completion_gesher_audit_excel,
    build_current_gesher_lines,
    build_difference_excel,
    compare_line_sets,
    enrich_paid_lines,
    get_legacy_completion_items,
    parse_gesher_file_lines,
)
from services.salary_impact import (
    build_salary_impact_completion_rows,
    get_salary_impact_events,
    update_salary_impact_group_status,
)
from services.email_service import (
    generate_batch_id,
    get_email_settings,
    process_guide_for_bulk,
)
from routes.guide import prepare_guide_pdf_data
from services.guide_reports_excel_export import build_guide_reports_excel
from utils.utils import human_date

templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
templates.env.filters["human_date"] = human_date
templates.env.globals["app_version"] = config.VERSION
logger = logging.getLogger(__name__)

COMPLETION_ACTION_LABELS = {
    "created": "יצירה",
    "updated": "עדכון",
    "deleted": "מחיקה",
}
COMPLETION_STATUS_LABELS = {
    "open": "ממתין לאישור",
    "included_in_export": "מאושר לייצוא",
    "exported": "שולם",
    "ignored": "לא לתשלום",
    "cancelled": "בוטל",
    "superseded": "הוחלף",
}
COMPLETION_BADGE_STATUSES = {
    "open": "open",
    "approved": "included_in_export",
    "paid": "exported",
}
COMPLETION_PAGE_STATUSES = tuple(COMPLETION_BADGE_STATUSES.values())
COMPLETION_FIELD_LABELS = {
    "id": "מזהה רשומה",
    "date": "תאריך עבודה",
    "person_id": "מדריך",
    "apartment_id": "דירה",
    "start_time": "שעת התחלה",
    "end_time": "שעת סיום",
    "shift_type_id": "סוג משמרת",
    "component_type_id": "סוג רכיב",
    "quantity": "כמות",
    "rate": "תעריף/סכום",
    "description": "תיאור",
    "is_approved": "מאושר",
    "approved_by": "אושר על ידי",
    "approved_at": "אושר בתאריך",
    "for_pension": "לפנסיה",
    "payment_year": "שנת תשלום",
    "payment_month": "חודש תשלום",
    "payment_note": "הערת תשלום",
    "payment_marked_at": "סומן לתשלום בתאריך",
    "payment_marked_by": "סומן לתשלום על ידי",
    "rate_apartment_type_id": "סוג דירה לתעריף",
    "asd_night_marking": "סימון לילה ASD",
    "exclude_standby": "ללא כוננות",
    "attachment_url": "קובץ מצורף",
    "fixed_payment_id": "תשלום קבוע מקור",
    "is_fixed_payment": "תשלום קבוע",
    "created_at": "נוצר בתאריך",
    "created_by": "נוצר על ידי",
    "updated_at": "עודכן בתאריך",
    "updated_by": "עודכן על ידי",
}


def _prepare_completion_event_for_display(event: dict) -> dict:
    result = dict(event)
    event_type = str(event.get("event_type") or "")
    result["event_type_label"] = COMPLETION_ACTION_LABELS.get(event_type, "שינוי")
    result["status_label"] = COMPLETION_STATUS_LABELS.get(event.get("status"), "לא ידוע")
    if event_type == "created":
        result["changed_fields_label"] = "כל נתוני הרשומה החדשה"
    elif event_type == "deleted":
        result["changed_fields_label"] = "כל נתוני הרשומה שנמחקה"
    else:
        labels = []
        for field in event.get("changed_fields") or []:
            label = COMPLETION_FIELD_LABELS.get(str(field), "שדה מערכת")
            if label not in labels:
                labels.append(label)
        result["changed_fields_label"] = ", ".join(labels) or "לא נרשמו שדות"
    return result


def _sse_event(event: str, data: dict) -> str:
    import json

    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _completion_task_status(statuses: set[str]) -> tuple[bool, str]:
    """סטטוס מסכם למשימת דוח: האם כולה שולמה, ותווית להצגה."""
    if statuses == {"exported"}:
        return True, COMPLETION_STATUS_LABELS["exported"]
    if len(statuses) == 1:
        status = next(iter(statuses))
        return False, COMPLETION_STATUS_LABELS.get(status, "לא ידוע")
    return False, "מעורב"


def _completion_report_tasks(completion_events: list[dict]) -> list[dict]:
    """Build unique guide+work-month report tasks for marked completions."""
    tasks_by_key = {}
    for item in completion_events:
        work_year = item.get("work_year")
        work_month = item.get("work_month")
        person_id = item.get("person_id")
        if not work_year or not work_month or not person_id:
            continue
        key = (int(person_id), int(work_year), int(work_month))
        if key not in tasks_by_key:
            tasks_by_key[key] = {
                "task_id": f"{person_id}-{work_year}-{work_month:02d}",
                "id": int(person_id),
                "name": item.get("person_name") or "",
                "email": item.get("person_email") or "",
                "work_year": int(work_year),
                "work_month": int(work_month),
                "items_count": 0,
                "statuses": set(),
            }
        tasks_by_key[key]["items_count"] += 1
        tasks_by_key[key]["statuses"].add(str(item.get("status") or ""))
    tasks = []
    for task in tasks_by_key.values():
        is_paid, status_label = _completion_task_status(task.pop("statuses"))
        tasks.append({**task, "is_paid": is_paid, "status_label": status_label})
    return sorted(
        tasks,
        key=lambda task: (task["work_year"], task["work_month"], task["name"]),
    )


def _completion_amount_badges(rows: list[dict]) -> list[dict]:
    """Sum completion gesher rows into one badge per salary symbol."""
    badges: dict[str, dict] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "")
        badge = badges.setdefault(symbol, {
            "symbol": symbol,
            "display_name": row.get("display_name") or "הפרשי השלמות",
            "is_quantity": symbol in COMPLETION_QUANTITY_TARGET_SYMBOLS,
            "is_manual": symbol in MANUAL_COMPLETION_SYMBOLS,
            "amount": 0.0,
            "quantity": 0.0,
        })
        badge["amount"] += float(row.get("amount") or 0)
        badge["quantity"] += float(row.get("quantity") or 0)
    for badge in badges.values():
        badge["amount"] = round(badge["amount"], 2)
        badge["quantity"] = round(badge["quantity"], 2)
    return [badges[symbol] for symbol in sorted(badges)]


def _unique_texts(values) -> list[str]:
    """רשימת טקסטים ייחודיים בסדר הופעתם, בלי ריקים."""
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _completion_month_entry(work_year: int, work_month: int, final_files: list[dict]) -> dict:
    return {
        "work_year": work_year,
        "work_month": work_month,
        "events": [],
        "legacy_items": [],
        "final_files": final_files,
    }


def _finalize_completion_guide(guide: dict, group_rows_by_status: dict) -> dict:
    """Add per-month and guide-level counters, amounts and export links."""
    months = []
    for (work_year, work_month), month in sorted(guide["months"].items()):
        group_key = (guide["person_id"], work_year, work_month)
        for name, status in COMPLETION_BADGE_STATUSES.items():
            month[f"{name}_rows"] = group_rows_by_status[status].get(group_key, [])
            month[f"{name}_badges"] = _completion_amount_badges(month[f"{name}_rows"])
            month[f"{name}_count"] = sum(
                1 for event in month["events"] if event.get("status") == status
            )
        month["validation_errors"] = _unique_texts(
            event.get("validation_error") for event in month["events"]
        )
        months.append(month)
    return {
        **{key: guide[key] for key in ("person_id", "person_name", "meirav_code")},
        **{
            f"{name}_count": sum(month[f"{name}_count"] for month in months)
            for name in COMPLETION_BADGE_STATUSES
        },
        **{
            f"{name}_badges": _completion_amount_badges(
                [row for month in months for row in month[f"{name}_rows"]]
            )
            for name in COMPLETION_BADGE_STATUSES
        },
        "months": months,
        "events_count": sum(len(month["events"]) for month in months),
        "legacy_count": sum(len(month["legacy_items"]) for month in months),
        "validation_errors": _unique_texts(
            error for month in months for error in month["validation_errors"]
        ),
        "difference_links": [
            {"file": file, "work_year": month["work_year"], "work_month": month["work_month"]}
            for month in months
            for file in month["final_files"]
        ],
        "months_without_final_file": [month for month in months if not month["final_files"]],
    }


def _build_completion_guides(
    events: list[dict],
    legacy_items: list[dict],
    group_rows_by_status: dict,
    final_files_by_month: dict,
) -> list[dict]:
    """Group event and legacy completions per guide, ordered alphabetically."""
    guides: dict[int, dict] = {}

    def month_for(item: dict) -> dict:
        person_id = int(item.get("person_id") or 0)
        guide = guides.setdefault(person_id, {
            "person_id": person_id,
            "person_name": item.get("person_name") or "",
            "meirav_code": item.get("meirav_code") or "",
            "months": {},
        })
        key = (int(item.get("work_year") or 0), int(item.get("work_month") or 0))
        if key not in guide["months"]:
            guide["months"][key] = _completion_month_entry(
                *key, final_files_by_month.get(key, [])
            )
        return guide["months"][key]

    for event in events:
        month_for(event)["events"].append(event)
    for item in legacy_items:
        month_for(item)["legacy_items"].append(item)

    return sorted(
        (
            _finalize_completion_guide(guide, group_rows_by_status)
            for guide in guides.values()
        ),
        key=lambda guide: guide["person_name"],
    )


def _unique_completion_person_ids(completion_items: list[dict]) -> list[int]:
    person_ids_by_name: dict[int, str] = {}
    for item in completion_items:
        person_id = item.get("person_id")
        if not person_id:
            continue
        person_ids_by_name[int(person_id)] = item.get("person_name") or ""
    return [
        person_id
        for person_id, _name in sorted(
            person_ids_by_name.items(),
            key=lambda item: item[1],
        )
    ]


def completions_page(
    request: Request,
    year: Optional[int] = None,
    month: Optional[int] = None,
) -> HTMLResponse:
    """Show payment-period completions grouped per guide, sorted alphabetically."""
    if year is None or month is None:
        default_year, default_month = get_default_period(request)
        year = year or default_year
        month = month or default_month

    housing_filter = get_housing_array_filter()
    with get_conn() as conn:
        legacy_completion_data = get_payment_period_completions(
            conn, year, month, housing_array_id=housing_filter
        )
        legacy_items = get_legacy_completion_items(
            conn,
            legacy_completion_data["items"],
            payment_year=year,
            payment_month=month,
        )
        events = [
            _prepare_completion_event_for_display(event)
            for event in get_salary_impact_events(
                conn,
                year,
                month,
                housing_array_id=housing_filter,
                statuses=COMPLETION_PAGE_STATUSES,
            )
        ]
        group_rows_by_status = {
            status: build_salary_impact_completion_rows(
                conn, year, month, statuses=(status,), housing_array_id=housing_filter
            )["group_rows"]
            for status in COMPLETION_PAGE_STATUSES
        }
        work_month_keys = {
            (int(item.get("work_year") or 0), int(item.get("work_month") or 0))
            for item in events + legacy_items
        }
        final_files_by_month = {}
        for work_year, work_month in work_month_keys:
            files = list_gesher_export_files(
                conn,
                year=work_year,
                month=work_month,
                housing_array_id=housing_filter,
            )
            final_files_by_month[(work_year, work_month)] = [
                file for file in files
                if file.get("is_final") and not file.get("is_cancelled")
            ]
        guides = _build_completion_guides(
            events,
            legacy_items,
            group_rows_by_status,
            final_files_by_month,
        )
        email_tasks = _completion_report_tasks(events)

    return templates.TemplateResponse("completions.html", {
        "request": request,
        "selected_year": year,
        "selected_month": month,
        "years": list(range(2023, 2028)),
        "guides": guides,
        "total_items": len(events) + len(legacy_items),
        "email_tasks": email_tasks,
        "completion_bulk_send_token": create_action_token(request, "completion_bulk_send"),
        "completion_status_token": create_action_token(request, "completion_status"),
        "completion_gesher_check_token": create_action_token(request, "completion_gesher_check"),
        "is_demo_mode": is_demo_mode(),
    })


def completion_gesher_check(
    request: Request,
    payment_year: int,
    payment_month: int,
    token: str,
) -> JSONResponse:
    """Run a read-only audit of all payment-month completions against final Gesher files."""
    if not validate_action_token(request, token, "completion_gesher_check"):
        raise HTTPException(status_code=403, detail="אין הרשאה לבצע בדיקה מול הגשר")
    if payment_year < 2023 or payment_month not in range(1, 13):
        raise HTTPException(status_code=400, detail="חודש תשלום אינו תקין")
    with get_conn() as conn:
        result = build_completion_gesher_audit(
            conn,
            payment_year,
            payment_month,
            housing_array_id=get_housing_array_filter(),
        )
    return JSONResponse(result)


def completion_gesher_check_excel(
    request: Request,
    payment_year: int,
    payment_month: int,
    token: str,
) -> Response:
    """Download the centralized Gesher audit as Excel."""
    if not validate_action_token(request, token, "completion_gesher_check"):
        raise HTTPException(status_code=403, detail="אין הרשאה להוריד בדיקה מול הגשר")
    if payment_year < 2023 or payment_month not in range(1, 13):
        raise HTTPException(status_code=400, detail="חודש תשלום אינו תקין")
    with get_conn() as conn:
        result = build_completion_gesher_audit(
            conn,
            payment_year,
            payment_month,
            housing_array_id=get_housing_array_filter(),
        )
        excel_bytes = build_completion_gesher_audit_excel(result)
    filename = f"completion_gesher_check_{payment_year}_{payment_month:02d}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def change_completion_group_status(
    request: Request,
    *,
    payment_year: int,
    payment_month: int,
    person_id: int,
    from_status: str,
    to_status: str,
    token: str,
    work_year: Optional[int] = None,
    work_month: Optional[int] = None,
    export_file_id: Optional[int] = None,
) -> Response:
    """Approve, reopen or mark a guide's completions as paid, for one work month or all."""
    if not validate_action_token(request, token, "completion_status"):
        raise HTTPException(status_code=403, detail="אין הרשאה לשנות סטטוס השלמה")
    current_user = getattr(request.state, "current_user", None) or {}
    actor_person_id = current_user.get("person_id")
    with get_conn() as conn:
        try:
            updated = update_salary_impact_group_status(
                conn,
                payment_year=payment_year,
                payment_month=payment_month,
                person_id=person_id,
                work_year=work_year,
                work_month=work_month,
                from_status=from_status,
                to_status=to_status,
                actor_person_id=actor_person_id,
                export_file_id=export_file_id,
                housing_array_id=get_housing_array_filter(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="לא נמצאו אירועים מתאימים לשינוי")
    from urllib.parse import urlencode
    return Response(
        status_code=303,
        headers={"Location": f"/completions?{urlencode({'year': payment_year, 'month': payment_month})}"},
    )


async def completion_reports_bulk_send_stream(
    request: Request,
    payment_year: int,
    payment_month: int,
    token: str = "",
    demo_email: str = "",
    task_ids: str = "",
) -> StreamingResponse:
    """Send one work-month shift report email for each selected guide completion task."""
    import asyncio

    if not validate_action_token(request, token, "completion_bulk_send"):
        async def forbidden_stream():
            yield _sse_event("error", {"message": "אין הרשאה להפעלת שליחה מרוכזת"})

        return StreamingResponse(forbidden_stream(), media_type="text/event-stream")

    housing_filter = get_housing_array_filter()
    demo_mode = is_demo_mode()
    demo_email = (demo_email or "").strip()
    if demo_mode and not demo_email:
        async def missing_demo_email_stream():
            yield _sse_event("error", {"message": "במצב דמו יש להזין כתובת מייל לשליחה"})

        return StreamingResponse(missing_demo_email_stream(), media_type="text/event-stream")

    with get_conn() as conn:
        settings = get_email_settings(conn)
        if not settings:
            async def settings_error_stream():
                yield _sse_event("error", {"message": "הגדרות מייל לא נמצאו"})

            return StreamingResponse(settings_error_stream(), media_type="text/event-stream")

        tasks = _completion_report_tasks(
            get_salary_impact_events(
                conn,
                payment_year,
                payment_month,
                housing_array_id=housing_filter,
                statuses=COMPLETION_PAGE_STATUSES,
            )
        )

    selected_ids = {part for part in (task_ids or "").split(",") if part}
    tasks = [task for task in tasks if task["task_id"] in selected_ids]
    if not tasks:
        async def empty_stream():
            yield _sse_event("error", {"message": "לא נבחרו דוחות השלמות לשליחה"})

        return StreamingResponse(empty_stream(), media_type="text/event-stream")

    batch_id = generate_batch_id()
    current_user = getattr(request.state, "current_user", None)
    sent_by = current_user.get("person_id") if current_user else None
    concurrency = 3

    def process_completion_report_task(task: dict) -> dict:
        set_demo_mode(demo_mode)
        set_housing_array_filter(housing_filter)
        try:
            return process_guide_for_bulk(
                {
                    "id": task["id"],
                    "name": task["name"],
                    "email": demo_email if demo_mode else task["email"],
                },
                task["work_year"],
                task["work_month"],
                batch_id,
                settings,
                sent_by,
                housing_filter,
            )
        finally:
            set_housing_array_filter(None)
            set_demo_mode(False)

    async def event_stream():
        yield _sse_event("start", {
            "total": len(tasks),
            "batchId": batch_id,
            "paymentYear": payment_year,
            "paymentMonth": payment_month,
        })

        sent = []
        skipped = []
        processed = 0
        loop = asyncio.get_event_loop()

        for i in range(0, len(tasks), concurrency):
            if await request.is_disconnected():
                logger.info("Client disconnected during completion reports send batch %s", batch_id)
                break

            batch = tasks[i:i + concurrency]
            for task in batch:
                yield _sse_event("sending", task)

            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = [
                    loop.run_in_executor(
                        executor,
                        process_completion_report_task,
                        task,
                    )
                    for task in batch
                ]
                results = await asyncio.gather(*futures)

            for task, result in zip(batch, results):
                processed += 1
                enriched = {
                    **result,
                    "task_id": task["task_id"],
                    "work_year": task["work_year"],
                    "work_month": task["work_month"],
                    "items_count": task["items_count"],
                }
                if enriched["status"] == "sent":
                    sent.append(enriched)
                else:
                    skipped.append(enriched)

                yield _sse_event("progress", {
                    "processed": processed,
                    "total": len(tasks),
                    "currentTaskId": task["task_id"],
                    "currentId": task["id"],
                    "currentName": task["name"],
                    "workYear": task["work_year"],
                    "workMonth": task["work_month"],
                    "status": enriched["status"],
                    "reason": enriched.get("reason"),
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


def _completion_ids_for_work_month(completion_data: dict, work_year: int, work_month: int) -> tuple[set[int], set[int], list[dict]]:
    items = completion_data["by_work_month"].get((work_year, work_month), [])
    report_ids = {
        int(item["id"]) for item in items
        if item.get("item_type") == "time_report"
    }
    component_ids = {
        int(item["id"]) for item in items
        if item.get("item_type") == "payment_component"
    }
    return report_ids, component_ids, items


def _file_person_ids(file_row: dict) -> Optional[set[int]]:
    person_ids = file_row.get("person_ids")
    if not person_ids:
        return None
    return {int(person_id) for person_id in person_ids if person_id}


def completion_guides_report_excel(
    request: Request,
    file_id: int,
    payment_year: int,
    payment_month: int,
) -> Response:
    """Export guide reports only for guides marked as completions for the archived work month."""
    current_filter = get_housing_array_filter()
    with get_conn() as conn:
        file_row = get_gesher_export_file(conn, file_id, housing_array_id=current_filter)
        if not file_row:
            raise HTTPException(status_code=404, detail="קובץ גשר לא נמצא")

        work_year = int(file_row["year"])
        work_month = int(file_row["month"])
        file_housing_filter = file_row.get("housing_array_id")

        old_filter = get_housing_array_filter()
        if file_housing_filter != old_filter:
            set_housing_array_filter(file_housing_filter)

        try:
            completion_items = [
                event for event in get_salary_impact_events(
                    conn,
                    payment_year,
                    payment_month,
                    housing_array_id=file_housing_filter,
                )
                if int(event.get("work_year") or 0) == work_year
                and int(event.get("work_month") or 0) == work_month
            ]
            person_ids = _unique_completion_person_ids(completion_items)
            if not person_ids:
                raise HTTPException(status_code=404, detail="לא נמצאו מדריכים שסומנו להשלמה לחודש העבודה של הקובץ")

            selected_housing_array_name = ""
            if file_housing_filter is not None:
                ha = conn.execute(
                    "SELECT name FROM housing_arrays WHERE id = %s",
                    (file_housing_filter,),
                ).fetchone()
                selected_housing_array_name = ha["name"] if ha else ""

            current_user = getattr(request.state, "current_user", None)
            exported_by = current_user.get("name") if current_user else ""
            guide_reports = []
            for person_id in person_ids:
                person = conn.execute("""
                    SELECT p.id, p.id_number, p.meirav_code, p.name, p.email, p.type,
                           p.housing_array_id, ha.name AS housing_array_name
                    FROM people p
                    LEFT JOIN housing_arrays ha ON ha.id = p.housing_array_id
                    WHERE p.id = %s
                """, (person_id,)).fetchone()
                if not person:
                    continue

                pdf_data = prepare_guide_pdf_data(
                    conn, person_id, work_year, work_month, file_housing_filter
                )
                if not pdf_data:
                    continue

                guide_reports.append({
                    "person": dict(person),
                    "pdf_data": pdf_data,
                })
        finally:
            if file_housing_filter != old_filter:
                set_housing_array_filter(old_filter)

    excel_bytes = build_guide_reports_excel(
        year=work_year,
        month=work_month,
        exported_by=exported_by,
        selected_housing_array_id=file_housing_filter,
        selected_housing_array_name=selected_housing_array_name,
        guide_reports=guide_reports,
    )
    filename = (
        f"completion_guide_reports_{work_year}_{work_month:02d}_"
        f"paid_{payment_year}_{payment_month:02d}.xlsx"
    )
    from urllib.parse import quote

    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def completion_difference_report(
    request: Request,
    file_id: int,
    payment_year: int,
    payment_month: int,
) -> Response:
    """Validate and generate an Excel difference report for one final Gesher file."""
    current_filter = get_housing_array_filter()
    with get_conn() as conn:
        file_row = get_gesher_export_file(conn, file_id, housing_array_id=current_filter)
        if not file_row:
            raise HTTPException(status_code=404, detail="קובץ גשר לא נמצא")
        if file_row.get("is_cancelled") or not file_row.get("is_final"):
            raise HTTPException(status_code=400, detail="אפשר להפיק הפרשים רק מול קובץ גשר סופי")

        work_year = int(file_row["year"])
        work_month = int(file_row["month"])
        file_housing_filter = file_row.get("housing_array_id")
        old_filter = get_housing_array_filter()
        if file_housing_filter != old_filter:
            set_housing_array_filter(file_housing_filter)

        try:
            completion_items = [
                event for event in get_salary_impact_events(
                    conn,
                    payment_year,
                    payment_month,
                    housing_array_id=file_housing_filter,
                )
                if int(event.get("work_year") or 0) == work_year
                and int(event.get("work_month") or 0) == work_month
            ]

            paid_lines = enrich_paid_lines(
                conn,
                parse_gesher_file_lines(file_row.get("content") or ""),
            )
            file_person_ids = _file_person_ids(file_row)
            current_with = build_current_gesher_lines(
                conn,
                work_year,
                work_month,
                company_code=file_row.get("company_code"),
                person_ids=file_person_ids,
            )
            completion_diffs = compare_line_sets(paid_lines, current_with)
            excel_bytes = build_difference_excel(
                diffs=completion_diffs,
                completions=completion_items,
                file_row=file_row,
                payment_year=payment_year,
                payment_month=payment_month,
            )
        finally:
            if file_housing_filter != old_filter:
                set_housing_array_filter(old_filter)

    filename = f"completion_diff_{work_year}_{work_month:02d}_paid_{payment_year}_{payment_month:02d}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

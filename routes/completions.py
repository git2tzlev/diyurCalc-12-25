"""Retroactive completion views and difference reports."""
from __future__ import annotations

from collections import defaultdict
from io import BytesIO
import logging
from typing import Optional
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.responses import StreamingResponse

from core.config import config
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
    build_completion_impact_rows,
    build_completion_gesher_file,
    build_completion_gesher_rows,
    build_current_gesher_lines,
    build_difference_excel,
    compare_line_sets,
    enrich_paid_lines,
    is_completion_info_source_symbol,
    is_completion_payable_source_symbol,
    parse_gesher_file_lines,
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


def _sse_event(event: str, data: dict) -> str:
    import json

    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _completion_report_tasks(completion_data: dict) -> list[dict]:
    """Build unique guide+work-month report tasks for marked completions."""
    tasks_by_key = {}
    for item in completion_data["items"]:
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
            }
        tasks_by_key[key]["items_count"] += 1
    return sorted(
        tasks_by_key.values(),
        key=lambda task: (task["work_year"], task["work_month"], task["name"]),
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
    """Show payment-period completions grouped by original work month."""
    if year is None or month is None:
        default_year, default_month = get_default_period(request)
        year = year or default_year
        month = month or default_month

    housing_filter = get_housing_array_filter()
    with get_conn() as conn:
        completion_data = get_payment_period_completions(
            conn, year, month, housing_array_id=housing_filter
        )
        groups = []
        for (work_year, work_month), items in sorted(completion_data["by_work_month"].items()):
            files = list_gesher_export_files(
                conn,
                year=work_year,
                month=work_month,
                housing_array_id=housing_filter,
            )
            final_files = [
                file for file in files
                if file.get("is_final") and not file.get("is_cancelled")
            ]
            groups.append({
                "work_year": work_year,
                "work_month": work_month,
                "items": items,
                "final_files": final_files,
            })
        email_tasks = _completion_report_tasks(completion_data)

    return templates.TemplateResponse("completions.html", {
        "request": request,
        "selected_year": year,
        "selected_month": month,
        "years": list(range(2023, 2028)),
        "groups": groups,
        "total_items": len(completion_data["items"]),
        "email_tasks": email_tasks,
        "email_tasks_with_email": sum(1 for task in email_tasks if task.get("email")),
        "completion_bulk_send_token": create_action_token(request, "completion_bulk_send"),
        "is_demo_mode": is_demo_mode(),
    })


async def completion_reports_bulk_send_stream(
    request: Request,
    payment_year: int,
    payment_month: int,
    token: str = "",
    demo_email: str = "",
) -> StreamingResponse:
    """Send one work-month shift report email for each guide completion task."""
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

        completion_data = get_payment_period_completions(
            conn, payment_year, payment_month, housing_array_id=housing_filter
        )
        tasks = _completion_report_tasks(completion_data)

    if not tasks:
        async def empty_stream():
            yield _sse_event("error", {"message": "לא נמצאו דוחות השלמות לשליחה"})

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


def _group_impact_by_person(diffs: list[dict]) -> list[dict]:
    groups_by_key = {}
    for diff in diffs:
        key = (
            diff.get("person_name") or "",
            diff.get("employee_code") or "",
        )
        if key not in groups_by_key:
            groups_by_key[key] = {
                "person_name": key[0],
                "employee_code": key[1],
                "amount_diff": 0.0,
                "info_amount_diff": 0.0,
                "rows": [],
            }
        groups_by_key[key]["rows"].append(diff)
        amount_diff = float(diff.get("amount_diff") or 0)
        if diff.get("is_payable_completion_symbol"):
            groups_by_key[key]["amount_diff"] = round(
                groups_by_key[key]["amount_diff"] + amount_diff,
                2,
            )
        elif diff.get("is_info_completion_symbol"):
            groups_by_key[key]["info_amount_diff"] = round(
                groups_by_key[key]["info_amount_diff"] + amount_diff,
                2,
            )
    return sorted(
        groups_by_key.values(),
        key=lambda group: (group["person_name"], group["employee_code"]),
    )


def completion_impact_report(
    request: Request,
    work_year: int,
    work_month: int,
    payment_year: int,
    payment_month: int,
) -> HTMLResponse:
    """Show Gesher symbol impact of marked completions without requiring an archive file."""
    housing_filter = get_housing_array_filter()
    with get_conn() as conn:
        completion_data = get_payment_period_completions(
            conn, payment_year, payment_month, housing_array_id=housing_filter
        )
        report_ids, component_ids, completion_items = _completion_ids_for_work_month(
            completion_data, work_year, work_month
        )
        before_lines = build_current_gesher_lines(
            conn,
            work_year,
            work_month,
            company_code=None,
            excluded_time_report_ids=report_ids,
            excluded_payment_component_ids=component_ids,
        )
        after_lines = build_current_gesher_lines(
            conn,
            work_year,
            work_month,
            company_code=None,
        )
        diffs = build_completion_impact_rows(before_lines, after_lines)
        for diff in diffs:
            diff["is_payable_completion_symbol"] = is_completion_payable_source_symbol(
                diff.get("symbol")
            )
            diff["is_info_completion_symbol"] = is_completion_info_source_symbol(
                diff.get("symbol")
            )

    return templates.TemplateResponse("completions_impact.html", {
        "request": request,
        "work_year": work_year,
        "work_month": work_month,
        "payment_year": payment_year,
        "payment_month": payment_month,
        "completions": completion_items,
        "groups": _group_impact_by_person(diffs),
        "total_diffs": len(diffs),
        "total_payable_amount_diff": round(
            sum(
                float(diff.get("amount_diff") or 0)
                for diff in diffs
                if diff.get("is_payable_completion_symbol")
            ),
            2,
        ),
        "total_info_amount_diff": round(
            sum(
                float(diff.get("amount_diff") or 0)
                for diff in diffs
                if diff.get("is_info_completion_symbol")
            ),
            2,
        ),
    })


def completion_overall_impact_report(
    request: Request,
    payment_year: int,
    payment_month: int,
) -> HTMLResponse:
    """Show Gesher impact for all completions marked for one payment month."""
    housing_filter = get_housing_array_filter()
    with get_conn() as conn:
        completion_data = get_payment_period_completions(
            conn, payment_year, payment_month, housing_array_id=housing_filter
        )
        all_diffs = []
        for (work_year, work_month), _items in sorted(completion_data["by_work_month"].items()):
            report_ids, component_ids, _completion_items = _completion_ids_for_work_month(
                completion_data, work_year, work_month
            )
            before_lines = build_current_gesher_lines(
                conn,
                work_year,
                work_month,
                company_code=None,
                excluded_time_report_ids=report_ids,
                excluded_payment_component_ids=component_ids,
                include_negative_values=True,
            )
            after_lines = build_current_gesher_lines(
                conn,
                work_year,
                work_month,
                company_code=None,
                include_negative_values=True,
            )
            diffs = build_completion_impact_rows(before_lines, after_lines)
            for diff in diffs:
                diff["work_year"] = work_year
                diff["work_month"] = work_month
                diff["is_payable_completion_symbol"] = is_completion_payable_source_symbol(
                    diff.get("symbol")
                )
                diff["is_info_completion_symbol"] = is_completion_info_source_symbol(
                    diff.get("symbol")
                )
            all_diffs.extend(diffs)

    return templates.TemplateResponse("completions_impact.html", {
        "request": request,
        "is_overall_payment_impact": True,
        "work_year": None,
        "work_month": None,
        "payment_year": payment_year,
        "payment_month": payment_month,
        "completions": completion_data["items"],
        "groups": _group_impact_by_person(all_diffs),
        "total_diffs": len(all_diffs),
        "total_payable_amount_diff": round(
            sum(
                float(diff.get("amount_diff") or 0)
                for diff in all_diffs
                if diff.get("is_payable_completion_symbol")
            ),
            2,
        ),
        "total_info_amount_diff": round(
            sum(
                float(diff.get("amount_diff") or 0)
                for diff in all_diffs
                if diff.get("is_info_completion_symbol")
            ),
            2,
        ),
    })


def completion_gesher_file_report(
    request: Request,
    payment_year: int,
    payment_month: int,
) -> Response:
    """Generate a Gesher file with aggregated completion differences for a payment month."""
    housing_filter = get_housing_array_filter()
    with get_conn() as conn:
        completion_data = get_payment_period_completions(
            conn, payment_year, payment_month, housing_array_id=housing_filter
        )
        all_diffs = []
        for (work_year, work_month), _items in sorted(completion_data["by_work_month"].items()):
            report_ids, component_ids, _completion_items = _completion_ids_for_work_month(
                completion_data, work_year, work_month
            )
            before_lines = build_current_gesher_lines(
                conn,
                work_year,
                work_month,
                company_code=None,
                excluded_time_report_ids=report_ids,
                excluded_payment_component_ids=component_ids,
                include_negative_values=True,
            )
            after_lines = build_current_gesher_lines(
                conn,
                work_year,
                work_month,
                company_code=None,
                include_negative_values=True,
            )
            all_diffs.extend(build_completion_impact_rows(before_lines, after_lines))

        rows = build_completion_gesher_rows(all_diffs)
        rows_by_company = defaultdict(list)
        for row in rows:
            rows_by_company[row.get("employer_code") or "001"].append(row)

    if len(rows_by_company) <= 1:
        company_code = next(iter(rows_by_company), "001")
        content = build_completion_gesher_file(
            rows_by_company.get(company_code, []),
            payment_year,
            payment_month,
            company_code=company_code,
        )
        filename = f"completion_gesher_differences_{company_code}_{payment_year}_{payment_month:02d}.mrv"
        return Response(
            content=content.encode("ascii", errors="replace"),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    zip_buffer = BytesIO()
    with ZipFile(zip_buffer, "w", compression=ZIP_DEFLATED) as zip_file:
        for company_code, company_rows in sorted(rows_by_company.items()):
            content = build_completion_gesher_file(
                company_rows,
                payment_year,
                payment_month,
                company_code=company_code,
            )
            zip_file.writestr(
                f"completion_gesher_differences_{company_code}_{payment_year}_{payment_month:02d}.mrv",
                content.encode("ascii", errors="replace"),
            )
    zip_buffer.seek(0)
    filename = f"completion_gesher_differences_{payment_year}_{payment_month:02d}.zip"
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


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
            completion_data = get_payment_period_completions(
                conn, payment_year, payment_month, housing_array_id=file_housing_filter
            )
            _report_ids, _component_ids, completion_items = _completion_ids_for_work_month(
                completion_data, work_year, work_month
            )
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
        file_person_ids = _file_person_ids(file_row)

        old_filter = get_housing_array_filter()
        if file_housing_filter != old_filter:
            set_housing_array_filter(file_housing_filter)

        try:
            completion_data = get_payment_period_completions(
                conn, payment_year, payment_month, housing_array_id=file_housing_filter
            )
            report_ids, component_ids, completion_items = _completion_ids_for_work_month(
                completion_data, work_year, work_month
            )

            paid_lines = enrich_paid_lines(
                conn,
                parse_gesher_file_lines(file_row.get("content") or ""),
            )
            current_without = build_current_gesher_lines(
                conn,
                work_year,
                work_month,
                company_code=file_row.get("company_code"),
                person_ids=file_person_ids,
                excluded_time_report_ids=report_ids,
                excluded_payment_component_ids=component_ids,
            )
            unrelated_diffs = compare_line_sets(paid_lines, current_without)
            if unrelated_diffs:
                return templates.TemplateResponse("completions_blocked.html", {
                    "request": request,
                    "file": file_row,
                    "payment_year": payment_year,
                    "payment_month": payment_month,
                    "diffs": unrelated_diffs,
                    "completions": completion_items,
                })

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

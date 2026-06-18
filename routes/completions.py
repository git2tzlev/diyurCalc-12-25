"""Retroactive completion views and difference reports."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from core.config import config
from core.database import (
    get_conn,
    get_default_period,
    get_housing_array_filter,
    set_housing_array_filter,
)
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
from utils.utils import human_date

templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
templates.env.filters["human_date"] = human_date
templates.env.globals["app_version"] = config.VERSION


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

    return templates.TemplateResponse("completions.html", {
        "request": request,
        "selected_year": year,
        "selected_month": month,
        "years": list(range(2023, 2028)),
        "groups": groups,
        "total_items": len(completion_data["items"]),
    })


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
        content = build_completion_gesher_file(rows, payment_year, payment_month)

    filename = f"completion_gesher_differences_{payment_year}_{payment_month:02d}.mrv"
    return Response(
        content=content.encode("ascii", errors="replace"),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
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

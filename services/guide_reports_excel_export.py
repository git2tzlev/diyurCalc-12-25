"""Excel export for guide monthly reports displayed in the external system."""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from io import BytesIO
import calendar
from typing import Any, Optional

import pandas as pd

from core.config import config


EXPORT_VERSION = 1
EXPORT_INFO_COLUMNS = ["key", "value"]
GUIDES_COLUMNS = [
    "guide_report_id", "person_id", "id_number", "meirav_code", "guide_name",
    "email", "person_type", "year", "month", "housing_array_id",
    "housing_array_name", "period_start_iso", "period_start_display",
    "period_end_iso", "period_end_display", "total_work_hours",
    "standby_count", "total_additions_no_travel", "total_salary",
    "generation_time_display", "row_count", "variable_row_count", "report_hash",
]
REPORT_ROWS_COLUMNS = [
    "guide_report_id", "row_order", "row_type", "date_iso", "date_display",
    "day", "apartment", "shift_type", "start_time", "end_time", "work_hours",
    "standby_hours", "amount", "description", "is_completion", "css_class",
]
VARIABLE_ROWS_COLUMNS = [
    "guide_report_id", "row_order", "shift_name", "reason", "hours", "rate",
    "overtime_payment", "payment", "is_asd_multi_rate",
]


def _value(row: dict[str, Any], key: str, default: Any = "") -> Any:
    value = row.get(key, default)
    return default if value is None else value


def _period_iso(year: int, month: int) -> tuple[str, str]:
    last_day = calendar.monthrange(year, month)[1]
    return f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last_day:02d}"


def _display_date_to_iso(value: Any) -> str:
    if not value or value == "-":
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    parts = str(value).split("/")
    if len(parts) != 3:
        return ""
    day, month, year = parts
    if len(year) == 2:
        year = f"20{year}"
    try:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    except (TypeError, ValueError):
        return ""


def _export_info_rows(values: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"key": key, "value": value} for key, value in values.items()]


def _report_id(person_id: int, year: int, month: int) -> str:
    return f"G{person_id}_{year}_{month:02d}"


def _shift_css_class(shift: dict[str, Any]) -> str:
    classes = []
    if shift.get("is_completion_apartment"):
        classes.append("completion-row")
    if shift.get("tagbor_group"):
        classes.append("tagbor-group")
    if shift.get("tagbor_first"):
        classes.append("tagbor-first")
    if shift.get("tagbor_last"):
        classes.append("tagbor-last")
    return " ".join(classes)


def _payment_row(
    *,
    guide_report_id: str,
    row_order: int,
    row_type: str,
    payment: dict[str, Any],
    is_completion: bool,
) -> dict[str, Any]:
    return {
        "guide_report_id": guide_report_id,
        "row_order": row_order,
        "row_type": row_type,
        "date_iso": "",
        "date_display": "-",
        "day": "-",
        "apartment": _value(payment, "description"),
        "shift_type": "-",
        "start_time": "-",
        "end_time": "-",
        "work_hours": _value(payment, "work_hours", ""),
        "standby_hours": "",
        "amount": round(float(payment.get("amount") or 0), 2),
        "description": _value(payment, "detail", _value(payment, "description")),
        "is_completion": is_completion,
        "css_class": "travel-row completion-row" if is_completion else "travel-row",
    }


def _report_rows_for_guide(guide_report_id: str, pdf_data: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    row_order = 1
    completion_header_shown = False

    def add_payment_rows(payments: list[dict[str, Any]], row_type: str, is_completion: bool) -> None:
        nonlocal row_order
        for payment in payments:
            rows.append(_payment_row(
                guide_report_id=guide_report_id,
                row_order=row_order,
                row_type=row_type,
                payment=payment,
                is_completion=is_completion,
            ))
            row_order += 1

    for shift in pdf_data.get("shifts_data", []):
        is_completion = bool(shift.get("is_completion_apartment"))
        if is_completion and not completion_header_shown:
            completion_header_shown = True
            add_payment_rows(pdf_data.get("payments_data", []), "payment", False)
            rows.append({
                "guide_report_id": guide_report_id,
                "row_order": row_order,
                "row_type": "completion_separator",
                "date_iso": "",
                "date_display": "",
                "day": "",
                "apartment": "",
                "shift_type": "",
                "start_time": "",
                "end_time": "",
                "work_hours": "",
                "standby_hours": "",
                "amount": "",
                "description": "השלמות מחודשים קודמים",
                "is_completion": True,
                "css_class": "completion-separator",
            })
            row_order += 1

        date_display = _value(shift, "date")
        rows.append({
            "guide_report_id": guide_report_id,
            "row_order": row_order,
            "row_type": "shift",
            "date_iso": _display_date_to_iso(date_display),
            "date_display": date_display,
            "day": _value(shift, "day"),
            "apartment": _value(shift, "apartment"),
            "shift_type": _value(shift, "shift_type"),
            "start_time": _value(shift, "start_time"),
            "end_time": _value(shift, "end_time"),
            "work_hours": round(float(shift.get("work_hours") or 0), 2),
            "standby_hours": round(float(shift.get("standby_hours") or 0), 2),
            "amount": "",
            "description": _value(shift, "note"),
            "is_completion": is_completion,
            "css_class": _shift_css_class(shift),
        })
        row_order += 1

    if not completion_header_shown:
        add_payment_rows(pdf_data.get("payments_data", []), "payment", False)
    add_payment_rows(pdf_data.get("completion_payments_data", []), "completion_payment", True)
    return rows


def _variable_rows_for_guide(guide_report_id: str, pdf_data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for idx, row in enumerate(pdf_data.get("variable_shifts", []), start=1):
        rows.append({
            "guide_report_id": guide_report_id,
            "row_order": idx,
            "shift_name": _value(row, "shift_name"),
            "reason": _value(row, "reason"),
            "hours": round(float(row.get("hours") or 0), 2),
            "rate": round(float(row.get("rate") or 0), 2),
            "overtime_payment": round(float(row.get("overtime_payment") or 0), 2),
            "payment": round(float(row.get("payment") or 0), 2),
            "is_asd_multi_rate": bool(pdf_data.get("is_asd_multi_rate")),
        })
    return rows


def _file_hash(rows: list[dict[str, Any]]) -> str:
    raw = repr(rows).encode("utf-8", errors="replace")
    return sha256(raw).hexdigest()


def build_guide_reports_excel(
    *,
    year: int,
    month: int,
    exported_by: Optional[str],
    selected_housing_array_id: Optional[int],
    selected_housing_array_name: Optional[str],
    guide_reports: list[dict[str, Any]],
) -> bytes:
    """Build the external-system Excel file for all guide reports in one month."""
    exported_at_dt = datetime.now(config.LOCAL_TZ)
    exported_at_iso = exported_at_dt.isoformat(timespec="seconds")
    exported_at_display = exported_at_dt.strftime("%Y-%m-%d %H:%M:%S")
    period_start_iso, period_end_iso = _period_iso(year, month)
    period_start_display = guide_reports[0]["pdf_data"].get("period_start") if guide_reports else ""
    period_end_display = guide_reports[0]["pdf_data"].get("period_end") if guide_reports else ""

    guide_rows = []
    report_rows = []
    variable_rows = []

    for item in guide_reports:
        person = item["person"]
        pdf_data = item["pdf_data"]
        guide_report_id = _report_id(int(person["id"]), year, month)
        rows_for_guide = _report_rows_for_guide(guide_report_id, pdf_data)
        variable_for_guide = _variable_rows_for_guide(guide_report_id, pdf_data)
        report_rows.extend(rows_for_guide)
        variable_rows.extend(variable_for_guide)

        guide_rows.append({
            "guide_report_id": guide_report_id,
            "person_id": person["id"],
            "id_number": person.get("id_number") or "",
            "meirav_code": person.get("meirav_code") or "",
            "guide_name": person.get("name") or pdf_data.get("person", {}).get("name") or "",
            "email": person.get("email") or "",
            "person_type": person.get("type") or "",
            "year": year,
            "month": month,
            "housing_array_id": person.get("housing_array_id") or selected_housing_array_id or "",
            "housing_array_name": person.get("housing_array_name") or selected_housing_array_name or "",
            "period_start_iso": period_start_iso,
            "period_start_display": pdf_data.get("period_start") or "",
            "period_end_iso": period_end_iso,
            "period_end_display": pdf_data.get("period_end") or "",
            "total_work_hours": round(float(pdf_data.get("total_work_hours") or 0), 2),
            "standby_count": int(pdf_data.get("standby_count") or 0),
            "total_additions_no_travel": round(float(pdf_data.get("total_additions_no_travel") or 0), 2),
            "total_salary": round(float(pdf_data.get("total_salary") or 0), 2),
            "generation_time_display": pdf_data.get("generation_time") or "",
            "row_count": len(rows_for_guide),
            "variable_row_count": len(variable_for_guide),
            "report_hash": _file_hash(rows_for_guide + variable_for_guide),
        })

    export_info_rows = _export_info_rows({
        "source_system": "diyur003",
        "export_version": EXPORT_VERSION,
        "guide_report_id_scope": "file-local identifier; do not use as database primary key",
        "money_unit": "NIS decimal",
        "money_example": "236.00 means 236 shekels",
        "date_format": "YYYY-MM-DD",
        "time_format": "HH:MM",
        "year": year,
        "month": month,
        "period_start_iso": period_start_iso,
        "period_start_display": period_start_display,
        "period_end_iso": period_end_iso,
        "period_end_display": period_end_display,
        "exported_at_iso": exported_at_iso,
        "exported_at_display": exported_at_display,
        "exported_by": exported_by or "",
        "selected_housing_array_id": selected_housing_array_id or "",
        "selected_housing_array_name": selected_housing_array_name or "",
        "guides_count": len(guide_rows),
        "report_rows_count": len(report_rows),
        "variable_rate_rows_count": len(variable_rows),
    })

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(export_info_rows, columns=EXPORT_INFO_COLUMNS).to_excel(
            writer, sheet_name="export_info", index=False
        )
        pd.DataFrame(guide_rows, columns=GUIDES_COLUMNS).to_excel(
            writer, sheet_name="guides", index=False
        )
        pd.DataFrame(report_rows, columns=REPORT_ROWS_COLUMNS).to_excel(
            writer, sheet_name="report_rows", index=False
        )
        pd.DataFrame(variable_rows, columns=VARIABLE_ROWS_COLUMNS).to_excel(
            writer, sheet_name="variable_rate_rows", index=False
        )

    output.seek(0)
    return output.getvalue()

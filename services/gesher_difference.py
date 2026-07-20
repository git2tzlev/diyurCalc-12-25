"""Generate completion-based Gesher difference checks and Excel reports."""
from __future__ import annotations

from collections import defaultdict
from io import BytesIO
from typing import Any, Optional

import pandas as pd

from core.payment_period import get_payment_period_completions
from core.logic import calculate_monthly_summary
from services import gesher_exporter
from services.gesher_archive import get_gesher_export_file, list_gesher_export_files


MONEY_EPSILON = 0.01
QUANTITY_EPSILON = 0.01
RATE_IS_NOT_MONEY_VALUE_TYPES = {"days_with_total_hours"}
COMPLETION_PENSION_SOURCE_SYMBOLS = {"360", "362", "363"}
COMPLETION_NON_PENSION_SOURCE_SYMBOLS = {
    "366", "368", "370", "371", "373", "374", "382", "434",
}
COMPLETION_TARGET_SYMBOLS = {
    "pension": "317",
    "non_pension": "253",
}
COMPLETION_TARGET_DISPLAY_NAMES = {
    "253": "הפרשי השלמות לא לפנסיה",
    "317": "הפרשי השלמות לפנסיה",
}
COMPLETION_QUANTITY_TARGET_SYMBOLS: set[str] = set()


class CompletionGesherBlockedError(Exception):
    """Raised when payment-month completions cannot be safely exported."""

    def __init__(self, blocks: list[dict[str, Any]]):
        self.blocks = blocks
        super().__init__("לא ניתן לצרף השלמות לגשר כי נמצאו חסימות")


def _amount_for_line(quantity: float, rate: float) -> float:
    """Calculate line amount using Gesher conventions."""
    if abs(quantity) < QUANTITY_EPSILON:
        return round(rate, 2)
    return round(quantity * rate, 2)


def _clean_employee_code(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits.zfill(6) if digits else ""


def _file_person_ids(file_row: dict[str, Any]) -> Optional[set[int]]:
    person_ids = file_row.get("person_ids")
    if not person_ids:
        return None
    return {int(person_id) for person_id in person_ids if person_id}


def _completion_ids_from_items(items: list[dict[str, Any]]) -> tuple[set[int], set[int]]:
    report_ids = {
        int(item["id"]) for item in items
        if item.get("item_type") == "time_report"
    }
    component_ids = {
        int(item["id"]) for item in items
        if item.get("item_type") == "payment_component"
    }
    return report_ids, component_ids


def parse_gesher_file_lines(content: str) -> list[dict[str, Any]]:
    """Parse archived Gesher file content into comparable line dictionaries."""
    rows = []
    for line_number, raw_line in enumerate((content or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        if not (parts[0].isdigit() and parts[1].isdigit()):
            continue
        try:
            quantity = float(parts[2])
            rate = float(parts[3])
        except ValueError:
            continue
        rows.append({
            "employee_code": _clean_employee_code(parts[0]),
            "symbol": parts[1],
            "quantity": round(quantity, 2),
            "rate": round(rate, 2),
            "amount": _amount_for_line(quantity, rate),
            "line_number": line_number,
            "raw_line": raw_line,
        })
    return rows


def _person_lookup(conn) -> dict[str, dict[str, Any]]:
    rows = conn.execute("""
        SELECT p.id, p.name, p.meirav_code, e.code AS employer_code
        FROM people p
        LEFT JOIN employers e ON e.id = p.employer_id
        WHERE p.meirav_code IS NOT NULL AND p.meirav_code != ''
    """).fetchall()
    result = {}
    for row in rows:
        code = _clean_employee_code(row["meirav_code"])
        if code:
            result[code] = {
                "person_id": row["id"],
                "person_name": row["name"],
                "employer_code": row["employer_code"],
            }
    return result


def _export_code_lookup(conn) -> dict[str, dict[str, str]]:
    export_codes = gesher_exporter.load_export_config_from_db(conn)
    if not export_codes:
        export_codes = gesher_exporter.load_export_config()
    result = {}
    for symbol, value_tuple in export_codes.items():
        if len(value_tuple) >= 3:
            internal_key, value_type, display_name = value_tuple
        else:
            internal_key, value_type = value_tuple
            display_name = internal_key
        result[symbol] = {
            "internal_key": internal_key,
            "value_type": value_type,
            "display_name": display_name,
        }
    return result


def build_current_gesher_lines(
    conn,
    year: int,
    month: int,
    *,
    company_code: Optional[str],
    person_ids: Optional[set[int]] = None,
    excluded_time_report_ids: Optional[set[int]] = None,
    excluded_payment_component_ids: Optional[set[int]] = None,
    include_negative_values: bool = False,
) -> list[dict[str, Any]]:
    """Build comparable Gesher lines from the current monthly calculation."""
    export_codes = gesher_exporter.load_export_config_from_db(conn)
    if not export_codes:
        export_codes = gesher_exporter.load_export_config()
    options = gesher_exporter.get_export_options()
    minimum_wage = gesher_exporter.get_minimum_wage(conn, year, month)
    people_by_code = _person_lookup(conn)

    raw_conn = conn.conn if hasattr(conn, "conn") else conn
    summary_data, _ = calculate_monthly_summary(
        raw_conn,
        year,
        month,
        person_ids=person_ids,
        excluded_time_report_ids=excluded_time_report_ids,
        excluded_payment_component_ids=excluded_payment_component_ids,
    )

    return build_gesher_lines_from_summary(
        conn,
        summary_data,
        year,
        month,
        company_code=company_code,
        person_ids=person_ids,
        include_negative_values=include_negative_values,
    )


def build_gesher_lines_from_summary(
    conn,
    summary_data: list[dict[str, Any]],
    year: int,
    month: int,
    *,
    company_code: Optional[str],
    person_ids: Optional[set[int]] = None,
    include_negative_values: bool = False,
) -> list[dict[str, Any]]:
    """Convert an already calculated monthly summary to comparable Gesher lines."""
    export_codes = gesher_exporter.load_export_config_from_db(conn)
    if not export_codes:
        export_codes = gesher_exporter.load_export_config()
    options = gesher_exporter.get_export_options()
    minimum_wage = gesher_exporter.get_minimum_wage(conn, year, month)
    people_by_code = _person_lookup(conn)
    result = []
    for person_data in summary_data:
        if person_ids and person_data.get("person_id") not in person_ids:
            continue
        employee_code = _clean_employee_code(person_data.get("merav_code"))
        if not employee_code:
            continue
        person_meta = people_by_code.get(employee_code, {})
        if company_code and person_meta.get("employer_code") != company_code:
            continue

        totals = person_data.get("totals", {})
        for symbol, value_tuple in export_codes.items():
            if symbol in gesher_exporter.EXCLUDED_EXPORT_CODES:
                continue
            if len(value_tuple) >= 3:
                internal_key, value_type, display_name = value_tuple
            else:
                internal_key, value_type = value_tuple
                display_name = internal_key

            quantity, rate = gesher_exporter.calculate_value(
                totals, internal_key, value_type, minimum_wage
            )
            if not options["export_zero_values"]:
                if value_type.startswith("hours_") and abs(quantity) < options["min_amount"]:
                    continue
                if include_negative_values:
                    if abs(quantity) < options["min_amount"] and abs(rate) < options["min_amount"]:
                        continue
                elif value_type == "money" and rate < options["min_amount"]:
                    continue
                elif quantity < options["min_amount"] and rate < options["min_amount"]:
                    continue

            result.append({
                "employee_code": employee_code,
                "person_id": person_data.get("person_id"),
                "person_name": person_data.get("name", ""),
                "employer_code": person_meta.get("employer_code") or "001",
                "symbol": symbol,
                "internal_key": internal_key,
                "display_name": display_name,
                "value_type": value_type,
                "quantity": round(quantity, 2),
                "rate": round(rate, 2),
                "amount": _amount_for_line(quantity, rate),
            })
    return result


def _completion_target_for_source_symbol(symbol: Any) -> Optional[str]:
    source_symbol = str(symbol or "").strip()
    if source_symbol in COMPLETION_PENSION_SOURCE_SYMBOLS:
        return COMPLETION_TARGET_SYMBOLS["pension"]
    if source_symbol in COMPLETION_NON_PENSION_SOURCE_SYMBOLS:
        return COMPLETION_TARGET_SYMBOLS["non_pension"]
    return None


def build_completion_gesher_rows(diffs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate completion differences into the target Gesher symbols."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for diff in diffs:
        target_symbol = _completion_target_for_source_symbol(diff.get("symbol"))
        if not target_symbol:
            continue
        amount_diff = round(float(diff.get("amount_diff") or 0), 2)
        quantity_diff = round(float(diff.get("quantity_diff") or 0), 2)
        if target_symbol in COMPLETION_QUANTITY_TARGET_SYMBOLS:
            if abs(quantity_diff) < QUANTITY_EPSILON:
                continue
        elif abs(amount_diff) < MONEY_EPSILON:
            continue
        employee_code = _clean_employee_code(diff.get("employee_code"))
        if not employee_code:
            continue

        employer_code = str(diff.get("employer_code") or "001").strip() or "001"
        key = (employer_code, employee_code, target_symbol)
        if key not in grouped:
            grouped[key] = {
                "employer_code": employer_code,
                "employee_code": employee_code,
                "person_id": diff.get("person_id"),
                "person_name": diff.get("person_name", ""),
                "symbol": target_symbol,
                "amount": 0.0,
                "quantity": 0.0,
                "source_symbols": set(),
            }
        grouped[key]["amount"] = round(grouped[key]["amount"] + amount_diff, 2)
        grouped[key]["quantity"] = round(grouped[key]["quantity"] + quantity_diff, 2)
        grouped[key]["source_symbols"].add(str(diff.get("symbol") or ""))

    rows = []
    for row in grouped.values():
        if row["symbol"] in COMPLETION_QUANTITY_TARGET_SYMBOLS:
            if abs(float(row["quantity"])) < QUANTITY_EPSILON:
                continue
        elif abs(float(row["amount"])) < MONEY_EPSILON:
            continue
        row["source_symbols"] = ", ".join(sorted(row["source_symbols"]))
        rows.append(row)

    return sorted(rows, key=lambda row: (row["employee_code"], row["symbol"]))


def build_completion_gesher_file(rows: list[dict[str, Any]], year: int, month: int, company_code: Optional[str] = None) -> str:
    """Build a Gesher-format file for aggregated completion differences."""
    company = company_code or gesher_exporter.get_export_options()["default_company"]
    text = gesher_exporter.format_gesher_header(company, year, month) + "\r\n"
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if symbol in COMPLETION_QUANTITY_TARGET_SYMBOLS:
            quantity = round(float(row.get("quantity") or 0), 2)
            rate = 0.0
        else:
            quantity = 0.0
            rate = round(float(row["amount"]), 2)
        text += gesher_exporter.format_gesher_line(
            employee_code=int(row["employee_code"]),
            symbol=symbol,
            quantity=quantity,
            rate=rate,
        ) + "\r\n"
    return text


def _build_unverified_completion_diffs(
    before_lines: list[dict[str, Any]],
    after_lines: list[dict[str, Any]],
    *,
    diff_type: str = "השלמה ללא קובץ גשר סופי",
) -> list[dict[str, Any]]:
    """Build completion diffs without an archived paid file, used only after explicit approval."""

    def aggregate(lines: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for line in lines:
            key = (line["employee_code"], line["symbol"])
            if key not in grouped:
                grouped[key] = dict(line)
                grouped[key]["quantity"] = 0.0
                grouped[key]["amount"] = 0.0
            grouped[key]["quantity"] = round(grouped[key]["quantity"] + float(line.get("quantity") or 0), 2)
            grouped[key]["amount"] = round(grouped[key]["amount"] + float(line.get("amount") or 0), 2)
        return grouped

    before = aggregate(before_lines)
    after = aggregate(after_lines)
    diffs = []
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        row = dict(new or old or {})
        quantity_diff = round(
            float(new.get("quantity") if new else 0) - float(old.get("quantity") if old else 0),
            2,
        )
        amount_diff = round(
            float(new.get("amount") if new else 0) - float(old.get("amount") if old else 0),
            2,
        )
        if abs(quantity_diff) < QUANTITY_EPSILON and abs(amount_diff) < MONEY_EPSILON:
            continue
        row.update({
            "quantity_diff": quantity_diff,
            "amount_diff": amount_diff,
            "diff_type": diff_type,
        })
        diffs.append(row)
    return diffs


def _build_current_completion_diffs(
    conn,
    work_year: int,
    work_month: int,
    company_code: str,
    items: list[dict[str, Any]],
    *,
    person_ids: Optional[set[int]] = None,
    diff_type: str = "השלמה ללא קובץ גשר סופי",
) -> list[dict[str, Any]]:
    """Calculate only the delta created by marked completion items."""
    report_ids, component_ids = _completion_ids_from_items(items)
    current_without = build_current_gesher_lines(
        conn,
        work_year,
        work_month,
        company_code=company_code,
        person_ids=person_ids,
        excluded_time_report_ids=report_ids,
        excluded_payment_component_ids=component_ids,
        include_negative_values=True,
    )
    current_with = build_current_gesher_lines(
        conn,
        work_year,
        work_month,
        company_code=company_code,
        person_ids=person_ids,
        include_negative_values=True,
    )
    return _build_unverified_completion_diffs(
        current_without,
        current_with,
        diff_type=diff_type,
    )


def build_approved_completion_gesher_rows(
    conn,
    payment_year: int,
    payment_month: int,
    *,
    company_code: Optional[str] = None,
    housing_array_id: Optional[int] = None,
    allow_unverified_missing_final: bool = False,
) -> dict[str, Any]:
    """Build approved completion rows from salary-impact events.

    ``allow_unverified_missing_final`` remains in the public signature while old
    callers are migrated; event-driven calculation never depends on a final file.
    """
    del allow_unverified_missing_final
    from services.salary_impact import build_salary_impact_completion_rows

    result = build_salary_impact_completion_rows(
        conn,
        payment_year,
        payment_month,
        statuses=("included_in_export",),
        company_code=company_code,
        housing_array_id=housing_array_id,
    )
    return {
        **result,
        "blocks": [
            {
                "type": "invalid_salary_impact_event",
                "message": event.get("validation_error"),
                "event_id": event.get("id"),
                "company_code": event.get("employer_code") or "001",
            }
            for event in result["invalid_events"]
            if event.get("status") == "included_in_export"
        ],
        "items": result["events"],
        "approved_files": [],
    }


def build_legacy_completion_gesher_rows_from_final_file(
    conn,
    payment_year: int,
    payment_month: int,
    *,
    company_code: Optional[str] = None,
    housing_array_id: Optional[int] = None,
    allow_unverified_missing_final: bool = False,
) -> dict[str, Any]:
    """
    Build payment-month completion Gesher rows.

    The rule is:
    1. Find marked completions for the payment month.
    2. For each work month + company, prefer a final archived Gesher file when available.
    3. Compare the final file to the current calculation without the marked completions.
       If unrelated differences exist, export the full current gap for that work month.
    4. If no final file exists, also export the clean delta of the marked completions.
    5. Convert completion differences into target completion symbols 253/317.
    """
    completion_data = get_payment_period_completions(
        conn,
        payment_year,
        payment_month,
        housing_array_id=housing_array_id,
    )
    relevant_items = [
        item for item in completion_data["items"]
        if not company_code or str(item.get("employer_code") or "001") == str(company_code)
    ]

    items_by_work_month_company: dict[tuple[int, int, str], list[dict[str, Any]]] = defaultdict(list)
    for item in relevant_items:
        work_year = item.get("work_year")
        work_month = item.get("work_month")
        if not work_year or not work_month:
            continue
        item_company = str(item.get("employer_code") or "001")
        items_by_work_month_company[(int(work_year), int(work_month), item_company)].append(item)

    blocks: list[dict[str, Any]] = []
    all_diffs: list[dict[str, Any]] = []
    approved_files: list[dict[str, Any]] = []

    for (work_year, work_month, item_company), items in sorted(items_by_work_month_company.items()):
        final_files = [
            file for file in list_gesher_export_files(
                conn,
                year=work_year,
                month=work_month,
                company_code=item_company,
                housing_array_id=housing_array_id,
            )
            if file.get("is_final") and not file.get("is_cancelled")
        ]
        if not final_files:
            completion_diffs = _build_current_completion_diffs(
                conn,
                work_year,
                work_month,
                item_company,
                items,
                diff_type="השלמה ללא קובץ גשר סופי",
            )
            for diff in completion_diffs:
                diff["work_year"] = work_year
                diff["work_month"] = work_month
                diff["source_file_id"] = None
                diff["source_file_name"] = "ללא קובץ גשר סופי"
                diff["employer_code"] = item_company
                diff["is_unverified_missing_final"] = True
            all_diffs.extend(completion_diffs)
            approved_files.append({
                "id": None,
                "filename": "ללא קובץ גשר סופי",
                "work_year": work_year,
                "work_month": work_month,
                "company_code": item_company,
                "items_count": len(items),
                "is_unverified_missing_final": True,
            })
            continue

        file_row = get_gesher_export_file(
            conn,
            int(final_files[0]["id"]),
            housing_array_id=housing_array_id,
        )
        if not file_row or file_row.get("is_cancelled") or not file_row.get("is_final"):
            completion_diffs = _build_current_completion_diffs(
                conn,
                work_year,
                work_month,
                item_company,
                items,
                diff_type="השלמה ללא קובץ גשר סופי תקין",
            )
            for diff in completion_diffs:
                diff["work_year"] = work_year
                diff["work_month"] = work_month
                diff["source_file_id"] = None
                diff["source_file_name"] = "קובץ גשר סופי לא זמין"
                diff["employer_code"] = item_company
                diff["is_unverified_missing_final"] = True
            all_diffs.extend(completion_diffs)
            approved_files.append({
                "id": None,
                "filename": "קובץ גשר סופי לא זמין",
                "work_year": work_year,
                "work_month": work_month,
                "company_code": item_company,
                "items_count": len(items),
                "is_unverified_missing_final": True,
            })
            continue

        report_ids, component_ids = _completion_ids_from_items(items)
        paid_lines = enrich_paid_lines(
            conn,
            parse_gesher_file_lines(file_row.get("content") or ""),
        )
        file_person_ids = _file_person_ids(file_row)
        current_without = build_current_gesher_lines(
            conn,
            work_year,
            work_month,
            company_code=item_company,
            person_ids=file_person_ids,
            excluded_time_report_ids=report_ids,
            excluded_payment_component_ids=component_ids,
        )
        unrelated_diffs = compare_line_sets(paid_lines, current_without)
        if unrelated_diffs:
            current_with = build_current_gesher_lines(
                conn,
                work_year,
                work_month,
                company_code=item_company,
                person_ids=file_person_ids,
            )
            completion_diffs = compare_line_sets(paid_lines, current_with)
            for diff in completion_diffs:
                diff["work_year"] = work_year
                diff["work_month"] = work_month
                diff["source_file_id"] = file_row.get("id")
                diff["source_file_name"] = file_row.get("filename")
                diff["employer_code"] = item_company
                diff["has_unrelated_diffs"] = True
            all_diffs.extend(completion_diffs)
            approved_files.append({
                "id": file_row.get("id"),
                "filename": file_row.get("filename"),
                "work_year": work_year,
                "work_month": work_month,
                "company_code": item_company,
                "items_count": len(items),
                "has_unrelated_diffs": True,
            })
            continue

        current_with = build_current_gesher_lines(
            conn,
            work_year,
            work_month,
            company_code=item_company,
            person_ids=file_person_ids,
        )
        completion_diffs = compare_line_sets(paid_lines, current_with)
        for diff in completion_diffs:
            diff["work_year"] = work_year
            diff["work_month"] = work_month
            diff["source_file_id"] = file_row.get("id")
            diff["source_file_name"] = file_row.get("filename")
            diff["employer_code"] = item_company
        all_diffs.extend(completion_diffs)
        approved_files.append({
            "id": file_row.get("id"),
            "filename": file_row.get("filename"),
            "work_year": work_year,
            "work_month": work_month,
            "company_code": item_company,
            "items_count": len(items),
        })

    rows = build_completion_gesher_rows(all_diffs)
    if company_code:
        rows = [
            row for row in rows
            if str(row.get("employer_code") or "001") == str(company_code)
        ]
    for row in rows:
        row["display_name"] = COMPLETION_TARGET_DISPLAY_NAMES.get(
            str(row.get("symbol") or ""),
            "הפרשי השלמות",
        )
        if str(row.get("symbol") or "") in COMPLETION_QUANTITY_TARGET_SYMBOLS:
            row["quantity"] = round(float(row.get("quantity") or 0), 2)
            row["rate"] = 0.0
        else:
            row["quantity"] = 0.0
            row["rate"] = round(float(row.get("amount") or 0), 2)

    return {
        "rows": rows,
        "blocks": blocks,
        "diffs": all_diffs,
        "items": relevant_items,
        "approved_files": approved_files,
    }


def enrich_paid_lines(conn, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add person and display metadata to parsed paid lines when possible."""
    people_by_code = _person_lookup(conn)
    symbols = _export_code_lookup(conn)
    enriched = []
    for line in lines:
        row = dict(line)
        person = people_by_code.get(row["employee_code"], {})
        symbol = symbols.get(row["symbol"], {})
        row["person_id"] = person.get("person_id")
        row["person_name"] = person.get("person_name", "")
        row["internal_key"] = symbol.get("internal_key", "")
        row["display_name"] = symbol.get("display_name", row["symbol"])
        row["value_type"] = symbol.get("value_type", "")
        enriched.append(row)
    return enriched


def aggregate_lines(lines: list[dict[str, Any]]) -> dict[tuple[str, str, float], dict[str, Any]]:
    """Aggregate lines by employee + symbol + rate."""
    grouped: dict[tuple[str, str, float], dict[str, Any]] = {}
    for line in lines:
        key = (line["employee_code"], line["symbol"], round(float(line["rate"]), 2))
        if key not in grouped:
            grouped[key] = dict(line)
            grouped[key]["quantity"] = 0.0
            grouped[key]["amount"] = 0.0
        grouped[key]["quantity"] = round(grouped[key]["quantity"] + float(line.get("quantity") or 0), 2)
        grouped[key]["amount"] = round(grouped[key]["amount"] + float(line.get("amount") or 0), 2)
    return grouped


def compare_line_sets(
    base_lines: list[dict[str, Any]],
    current_lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return differences between two comparable Gesher line sets."""
    base = aggregate_lines(base_lines)
    current = aggregate_lines(current_lines)
    diffs = []
    for key in sorted(set(base) | set(current)):
        old = base.get(key)
        new = current.get(key)
        row = dict(new or old or {})
        old_quantity = float(old.get("quantity") if old else 0)
        old_amount = float(old.get("amount") if old else 0)
        new_quantity = float(new.get("quantity") if new else 0)
        new_amount = float(new.get("amount") if new else 0)
        quantity_diff = round(new_quantity - old_quantity, 2)
        amount_diff = round(new_amount - old_amount, 2)
        if abs(quantity_diff) < QUANTITY_EPSILON and abs(amount_diff) < MONEY_EPSILON:
            continue
        if old is None:
            diff_type = "שורה נוספה"
        elif new is None:
            diff_type = "שורה ירדה"
        elif abs(quantity_diff) >= QUANTITY_EPSILON:
            diff_type = "כמות השתנתה"
        else:
            diff_type = "סכום השתנה"
        display_rate = None
        if row.get("value_type") not in RATE_IS_NOT_MONEY_VALUE_TYPES:
            display_rate = round(float(row.get("rate") or 0), 2)
        row.update({
            "paid_quantity": round(old_quantity, 2),
            "paid_amount": round(old_amount, 2),
            "current_quantity": round(new_quantity, 2),
            "current_amount": round(new_amount, 2),
            "quantity_diff": quantity_diff,
            "amount_diff": amount_diff,
            "display_rate": display_rate,
            "diff_type": diff_type,
        })
        diffs.append(row)
    return diffs


def _diffs_to_rows(diffs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for diff in diffs:
        rows.append({
            "שם מדריך": diff.get("person_name", ""),
            "קוד מירב": diff.get("employee_code", ""),
            "סמל": diff.get("symbol", ""),
            "רכיב": diff.get("display_name", ""),
            "תעריף": "" if diff.get("display_rate") is None else diff.get("display_rate", 0),
            "כמות ששולמה": diff.get("paid_quantity", 0),
            "סכום ששולם": diff.get("paid_amount", 0),
            "כמות נוכחית": diff.get("current_quantity", 0),
            "סכום נוכחי": diff.get("current_amount", 0),
            "הפרש כמות": diff.get("quantity_diff", 0),
            "הפרש לתשלום": diff.get("amount_diff", 0),
            "סוג שינוי": diff.get("diff_type", ""),
        })
    return rows


def _completion_rows(completions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in completions:
        is_report = item.get("item_type") == "time_report" or item.get("source_table") == "time_reports"
        snapshot = item.get("new_data") or item.get("old_data") or {}
        rows.append({
            "סוג": "משמרת" if is_report else "רכיב תשלום",
            "שם מדריך": item.get("person_name", ""),
            "קוד מירב": item.get("meirav_code", ""),
            "תאריך עבודה": item.get("date") or item.get("work_date"),
            "חודש עבודה": f"{item.get('work_month'):02d}/{item.get('work_year')}",
            "דירה": item.get("apartment_name", ""),
            "משמרת/רכיב": item.get("shift_name") if is_report else item.get("component_name"),
            "שעות/כמות": (
                f"{snapshot.get('start_time') or ''}-{snapshot.get('end_time') or ''}"
                if is_report else snapshot.get("quantity")
            ),
            "הערת תשלום": item.get("payment_note") or snapshot.get("payment_note", ""),
            "סומן ע\"י": item.get("payment_marked_by_name") or item.get("actor_label", ""),
            "סומן בתאריך": item.get("payment_marked_at") or item.get("created_at"),
        })
    return rows


def build_difference_excel(
    *,
    diffs: list[dict[str, Any]],
    completions: list[dict[str, Any]],
    file_row: dict[str, Any],
    payment_year: int,
    payment_month: int,
) -> bytes:
    """Build the approved differences Excel file."""
    output = BytesIO()
    diff_rows = _diffs_to_rows(diffs)
    summary_by_guide: dict[tuple[str, str], float] = defaultdict(float)
    for row in diff_rows:
        summary_by_guide[(row["שם מדריך"], row["קוד מירב"])] += float(row["הפרש לתשלום"] or 0)

    summary_rows = [
        {"שם מדריך": name, "קוד מירב": code, "הפרש לתשלום": round(amount, 2)}
        for (name, code), amount in sorted(summary_by_guide.items())
    ]
    file_rows = [{
        "קובץ בסיס": file_row.get("filename"),
        "חודש עבודה": f"{file_row.get('month'):02d}/{file_row.get('year')}",
        "חודש תשלום": f"{payment_month:02d}/{payment_year}",
        "מפעל": file_row.get("company_code") or "",
        "מערך": file_row.get("housing_array_name") or "כל המערכים",
        "הופק בתאריך": file_row.get("created_at"),
        "הופק ע\"י": file_row.get("created_by_name") or "",
        "הערה": file_row.get("notes") or "",
    }]

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(diff_rows).to_excel(writer, sheet_name="הפרשים לתשלום", index=False)
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="סיכום מדריכים", index=False)
        pd.DataFrame(_completion_rows(completions)).to_excel(writer, sheet_name="רשימת השלמות", index=False)
        pd.DataFrame(file_rows).to_excel(writer, sheet_name="פרטי קובץ בסיס", index=False)

    output.seek(0)
    return output.getvalue()

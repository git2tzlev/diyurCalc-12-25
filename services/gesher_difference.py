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
# רכיבים שמועברים כהשלמה בסמל ייעודי משלהם, לפי מפתח פנימי ולא לפי סמל מקור
# (סמלי המקור נשלפים מ-payment_codes וניתנים לעריכה במסך סמלי שכר).
COMPLETION_PASSTHROUGH_TARGETS = {
    "sick_days_taken": "414",
    "vacation_days_taken": "427",
    "sick_days_accrued": "410",
    "vacation_days_accrued": "799",
    "sick_payment": "306",
    "vacation": "332",
    "vacation_minutes": "332",
}
# רכיבים שההשלמה שלהם משולמת ידנית: מחושבים ומוצגים, אך נחסמים בכתיבה לגשר.
# המיפוי לפי מפתח פנימי, כמו COMPLETION_PASSTHROUGH_TARGETS ומאותה סיבה.
COMPLETION_MANUAL_TARGETS = {
    "professional_support": "243",
}
COMPLETION_TARGET_DISPLAY_NAMES = {
    "243": "תומך מקצועי - לתשלום ידני",
    "253": "הפרשי השלמות לא לפנסיה",
    "317": "הפרשי השלמות לפנסיה",
    "306": "תשלום מחלה רטרו",
    "332": "תשלום חופשה רטרו",
    "410": "זכות מחלה רטרו",
    "414": "ניצול מחלה רטרו",
    "427": "ניצול חופשה רטרו",
    "799": "זכות חופשה רטרו",
}
COMPLETION_QUANTITY_TARGET_SYMBOLS = {"410", "414", "427", "799"}
COMPLETION_HOURS_TARGET_SYMBOLS = {"306", "332"}


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


def _completion_target_for_diff(diff: dict[str, Any]) -> Optional[str]:
    """סמל היעד של שורת הפרש - רטרו ייעודי, תשלום ידני, אחרת פנסיה/לא פנסיה."""
    internal_key = str(diff.get("internal_key") or "").strip()
    passthrough = COMPLETION_PASSTHROUGH_TARGETS.get(internal_key)
    if passthrough:
        return passthrough
    manual = COMPLETION_MANUAL_TARGETS.get(internal_key)
    if manual:
        return manual
    source_symbol = str(diff.get("symbol") or "").strip()
    if source_symbol in COMPLETION_PENSION_SOURCE_SYMBOLS:
        return COMPLETION_TARGET_SYMBOLS["pension"]
    if source_symbol in COMPLETION_NON_PENSION_SOURCE_SYMBOLS:
        return COMPLETION_TARGET_SYMBOLS["non_pension"]
    return None


def _is_significant_completion_value(
    target_symbol: str,
    quantity: float,
    amount: float,
) -> bool:
    """סמלי ימים ושעות נמדדים לפי כמות, סמלי כסף לפי סכום."""
    if target_symbol in COMPLETION_QUANTITY_TARGET_SYMBOLS | COMPLETION_HOURS_TARGET_SYMBOLS:
        return abs(quantity) >= QUANTITY_EPSILON
    return abs(amount) >= MONEY_EPSILON


def finalize_completion_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """קביעת שם תצוגה, כמות, תעריף וסכום לכל שורת השלמה לפי סוג סמל היעד."""
    for row in rows:
        symbol = str(row.get("symbol") or "")
        row["display_name"] = COMPLETION_TARGET_DISPLAY_NAMES.get(symbol, "הפרשי השלמות")
        if symbol in COMPLETION_QUANTITY_TARGET_SYMBOLS:
            row["quantity"] = round(float(row.get("quantity") or 0), 2)
            row["rate"] = 0.0
            row["amount"] = 0.0
        elif symbol in COMPLETION_HOURS_TARGET_SYMBOLS:
            row["quantity"] = round(float(row.get("quantity") or 0), 2)
            row["rate"] = round(float(row.get("rate") or 0), 2)
            row["amount"] = round(row["quantity"] * row["rate"], 2)
        else:
            row["quantity"] = 0.0
            row["rate"] = round(float(row.get("amount") or 0), 2)
    return rows


def build_completion_gesher_rows(diffs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate completion differences into the target Gesher symbols."""
    grouped: dict[tuple[str, str, str, float], dict[str, Any]] = {}
    for diff in diffs:
        target_symbol = _completion_target_for_diff(diff)
        if not target_symbol:
            continue
        amount_diff = round(float(diff.get("amount_diff") or 0), 2)
        quantity_diff = round(float(diff.get("quantity_diff") or 0), 2)
        if not _is_significant_completion_value(target_symbol, quantity_diff, amount_diff):
            continue
        employee_code = _clean_employee_code(diff.get("employee_code"))
        if not employee_code:
            continue

        employer_code = str(diff.get("employer_code") or "001").strip() or "001"
        # סמלי שעות משלמים כמות בתעריף של חודש העבודה, לכן תעריפים שונים אינם מתמזגים
        group_rate = (
            round(float(diff.get("rate") or 0), 2)
            if target_symbol in COMPLETION_HOURS_TARGET_SYMBOLS
            else 0.0
        )
        key = (employer_code, employee_code, target_symbol, group_rate)
        if key not in grouped:
            grouped[key] = {
                "employer_code": employer_code,
                "employee_code": employee_code,
                "person_id": diff.get("person_id"),
                "person_name": diff.get("person_name", ""),
                "symbol": target_symbol,
                "rate": group_rate,
                "amount": 0.0,
                "quantity": 0.0,
                "source_symbols": set(),
            }
        grouped[key]["amount"] = round(grouped[key]["amount"] + amount_diff, 2)
        grouped[key]["quantity"] = round(grouped[key]["quantity"] + quantity_diff, 2)
        grouped[key]["source_symbols"].add(str(diff.get("symbol") or ""))

    rows = []
    for row in grouped.values():
        if not _is_significant_completion_value(row["symbol"], row["quantity"], row["amount"]):
            continue
        row["source_symbols"] = ", ".join(sorted(row["source_symbols"]))
        rows.append(row)

    return sorted(rows, key=lambda row: (row["employee_code"], row["symbol"], row["rate"]))


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
    person_ids: Optional[set[int]] = None,
    allow_unverified_missing_final: bool = False,
    request_cache: Optional[dict] = None,
) -> dict[str, Any]:
    """Build one completion result from approved events and untracked legacy marks."""
    del allow_unverified_missing_final
    from services.salary_impact import build_salary_impact_completion_rows

    selected_person_ids = {int(person_id) for person_id in (person_ids or set())}
    cache_key = (
        "approved-completions",
        payment_year,
        payment_month,
        company_code,
        housing_array_id,
        tuple(sorted(selected_person_ids)),
    )
    if request_cache is not None and cache_key in request_cache:
        return request_cache[cache_key]

    event_result = build_salary_impact_completion_rows(
        conn,
        payment_year,
        payment_month,
        statuses=("included_in_export",),
        company_code=company_code,
        housing_array_id=housing_array_id,
        person_ids=selected_person_ids or None,
        request_cache=request_cache,
    )
    completion_data = get_payment_period_completions(
        conn,
        payment_year,
        payment_month,
        housing_array_id=housing_array_id,
    )
    legacy_items = get_legacy_completion_items(
        conn,
        completion_data["items"],
        payment_year=payment_year,
        payment_month=payment_month,
        company_code=company_code,
        person_ids=selected_person_ids or None,
    )
    legacy_result = build_legacy_completion_gesher_rows_from_final_file(
        conn,
        payment_year,
        payment_month,
        company_code=company_code,
        housing_array_id=housing_array_id,
        person_ids=selected_person_ids or None,
        completion_items=legacy_items,
    )
    rows = _merge_completion_rows(event_result["rows"], legacy_result["rows"])
    invalid_notices = [
        {
            "type": "invalid_salary_impact_event",
            "message": event.get("validation_error"),
            "event_id": event.get("id"),
            "person_name": event.get("person_name") or "",
            "work_year": event.get("work_year"),
            "work_month": event.get("work_month"),
            "company_code": event.get("employer_code") or "001",
        }
        for event in event_result["invalid_events"]
        if event.get("status") == "included_in_export"
    ]
    missing_code_warnings = [
        notice for notice in invalid_notices
        if notice.get("message") == "חסר קוד מירב למדריך"
    ]
    invalid_blocks = [
        notice for notice in invalid_notices
        if notice.get("message") != "חסר קוד מירב למדריך"
    ]
    result = {
        **event_result,
        "rows": rows,
        "blocks": invalid_blocks + legacy_result["blocks"],
        "warnings": missing_code_warnings,
        "items": event_result["events"] + legacy_items,
        "legacy_items": legacy_items,
        "approved_files": legacy_result["approved_files"],
        "legacy_diffs": legacy_result["diffs"],
    }
    if request_cache is not None:
        request_cache[cache_key] = result
    return result


def get_legacy_completion_items(
    conn,
    items: list[dict[str, Any]],
    *,
    payment_year: int,
    payment_month: int,
    company_code: Optional[str] = None,
    person_ids: Optional[set[int]] = None,
) -> list[dict[str, Any]]:
    """Return marked payment-period rows that were never captured as salary events."""
    relevant = [
        item for item in items
        if (not company_code or str(item.get("employer_code") or "001") == str(company_code))
        and (not person_ids or int(item.get("person_id") or 0) in person_ids)
    ]
    if not relevant:
        return []
    source_tables = {
        "time_reports" if item.get("item_type") == "time_report" else "payment_components"
        for item in relevant
    }
    rows = conn.execute("""
        SELECT source_table, source_id
        FROM salary_impact_events
        WHERE payment_year = %s AND payment_month = %s
          AND source_table = ANY(%s)
          AND source_id = ANY(%s)
    """, (
        payment_year,
        payment_month,
        list(source_tables),
        [int(item["id"]) for item in relevant],
    )).fetchall()
    event_keys = {
        (str(row["source_table"]), int(row["source_id"]))
        for row in rows
    }
    result = []
    for item in relevant:
        source_table = "time_reports" if item.get("item_type") == "time_report" else "payment_components"
        if (source_table, int(item["id"])) not in event_keys:
            result.append(item)
    return result


def _merge_completion_rows(*row_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge event and legacy amounts without losing their employee scope."""
    grouped: dict[tuple[str, str, str, float], dict[str, Any]] = {}
    for rows in row_groups:
        for source_row in rows:
            row = dict(source_row)
            row_amount = float(row.get("amount") or 0)
            row_quantity = float(row.get("quantity") or 0)
            row_source_symbols = str(row.get("source_symbols") or "")
            symbol = str(row.get("symbol") or "")
            key = (
                str(row.get("employer_code") or "001"),
                _clean_employee_code(row.get("employee_code")),
                symbol,
                round(float(row.get("rate") or 0), 2)
                if symbol in COMPLETION_HOURS_TARGET_SYMBOLS
                else 0.0,
            )
            if not key[1] or not key[2]:
                continue
            if key not in grouped:
                grouped[key] = row
                grouped[key]["rate"] = key[3]
                grouped[key]["amount"] = 0.0
                grouped[key]["quantity"] = 0.0
                grouped[key]["source_symbols"] = set()
            grouped[key]["amount"] = round(
                float(grouped[key].get("amount") or 0) + row_amount,
                2,
            )
            grouped[key]["quantity"] = round(
                float(grouped[key].get("quantity") or 0) + row_quantity,
                2,
            )
            grouped[key]["source_symbols"].update(
                symbol.strip() for symbol in row_source_symbols.split(",") if symbol.strip()
            )
    result = []
    for row in grouped.values():
        if not _is_significant_completion_value(row["symbol"], row["quantity"], row["amount"]):
            continue
        row["source_symbols"] = ", ".join(sorted(row["source_symbols"]))
        result.append(row)
    finalize_completion_rows(result)
    return sorted(result, key=lambda row: (row["employee_code"], row["symbol"], row["rate"]))


def build_legacy_completion_gesher_rows_from_final_file(
    conn,
    payment_year: int,
    payment_month: int,
    *,
    company_code: Optional[str] = None,
    housing_array_id: Optional[int] = None,
    person_ids: Optional[set[int]] = None,
    completion_items: Optional[list[dict[str, Any]]] = None,
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
    completion_data = (
        get_payment_period_completions(
            conn,
            payment_year,
            payment_month,
            housing_array_id=housing_array_id,
        )
        if completion_items is None
        else {"items": completion_items}
    )
    relevant_items = [
        item for item in completion_data["items"]
        if not company_code or str(item.get("employer_code") or "001") == str(company_code)
        if not person_ids or int(item.get("person_id") or 0) in person_ids
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
                person_ids=person_ids,
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
                person_ids=person_ids,
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
        if person_ids:
            paid_lines = [
                line for line in paid_lines
                if int(line.get("person_id") or 0) in person_ids
            ]
            file_person_ids = set(person_ids)
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
    finalize_completion_rows(rows)

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


COMPLETION_AUDIT_EXPECTED_STATUSES = ("open", "included_in_export")
COMPLETION_AUDIT_INACTIVE_STATUSES = ("ignored", "cancelled", "superseded")
COMPLETION_AUDIT_ALL_STATUSES = (
    *COMPLETION_AUDIT_EXPECTED_STATUSES,
    *COMPLETION_AUDIT_INACTIVE_STATUSES,
)


def _audit_row_groups(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Aggregate completion rows for a stable employee+symbol audit comparison."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for source in rows:
        employee_code = _clean_employee_code(source.get("employee_code"))
        symbol = str(source.get("symbol") or "").strip()
        if not employee_code or not symbol:
            continue
        key = (employee_code, symbol)
        row = grouped.setdefault(key, {
            "employee_code": employee_code,
            "person_id": source.get("person_id"),
            "person_name": source.get("person_name") or "",
            "symbol": symbol,
            "display_name": source.get("display_name") or COMPLETION_TARGET_DISPLAY_NAMES.get(
                symbol, "הפרשי השלמות"
            ),
            "quantity": 0.0,
            "amount": 0.0,
            "rates": set(),
        })
        if not row.get("person_id") and source.get("person_id"):
            row["person_id"] = source.get("person_id")
        if not row.get("person_name") and source.get("person_name"):
            row["person_name"] = source.get("person_name")
        row["quantity"] = round(row["quantity"] + float(source.get("quantity") or 0), 2)
        row["amount"] = round(row["amount"] + float(source.get("amount") or 0), 2)
        row["rates"].add(round(float(source.get("rate") or 0), 2))
    return grouped


def compare_completion_audit_rows(
    expected_rows: list[dict[str, Any]],
    actual_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Classify expected versus actual completion export rows."""
    expected = _audit_row_groups(expected_rows)
    actual = _audit_row_groups(actual_rows)
    result = []
    for key in sorted(set(expected) | set(actual)):
        wanted = expected.get(key)
        found = actual.get(key)
        row = dict(wanted or found or {})
        expected_quantity = round(float(wanted.get("quantity") if wanted else 0), 2)
        expected_amount = round(float(wanted.get("amount") if wanted else 0), 2)
        actual_quantity = round(float(found.get("quantity") if found else 0), 2)
        actual_amount = round(float(found.get("amount") if found else 0), 2)
        expected_rates = sorted(wanted.get("rates", set())) if wanted else []
        actual_rates = sorted(found.get("rates", set())) if found else []
        if wanted is None:
            category = "extra"
        elif found is None:
            category = "missing"
        elif (
            abs(expected_quantity - actual_quantity) >= QUANTITY_EPSILON
            or abs(expected_amount - actual_amount) >= MONEY_EPSILON
        ):
            category = "mismatch"
        else:
            category = "matched"
        row.update({
            "category": category,
            "expected_quantity": expected_quantity,
            "expected_amount": expected_amount,
            "expected_rates": expected_rates,
            "actual_quantity": actual_quantity,
            "actual_amount": actual_amount,
            "actual_rates": actual_rates,
            "quantity_difference": round(actual_quantity - expected_quantity, 2),
            "amount_difference": round(actual_amount - expected_amount, 2),
        })
        row.pop("rates", None)
        result.append(row)
    return result


def _event_as_completion_item(event: dict[str, Any]) -> dict[str, Any]:
    return {
        **event,
        "id": event.get("source_id"),
        "item_type": (
            "time_report" if event.get("source_table") == "time_reports"
            else "payment_component"
        ),
    }


def build_completion_gesher_audit(
    conn,
    payment_year: int,
    payment_month: int,
    *,
    housing_array_id: Optional[int] = None,
) -> dict[str, Any]:
    """Audit all payment-month completions against the latest final work-month files."""
    from services.salary_impact import build_salary_impact_completion_rows, get_salary_impact_events

    all_events = get_salary_impact_events(
        conn,
        payment_year,
        payment_month,
        housing_array_id=housing_array_id,
        statuses=COMPLETION_AUDIT_ALL_STATUSES,
    )
    expected_event_result = build_salary_impact_completion_rows(
        conn,
        payment_year,
        payment_month,
        statuses=COMPLETION_AUDIT_EXPECTED_STATUSES,
        housing_array_id=housing_array_id,
    )
    completion_data = get_payment_period_completions(
        conn, payment_year, payment_month, housing_array_id=housing_array_id
    )
    legacy_items = get_legacy_completion_items(
        conn,
        completion_data["items"],
        payment_year=payment_year,
        payment_month=payment_month,
    )

    group_items: dict[tuple[int, int, str], list[dict[str, Any]]] = defaultdict(list)
    for event in all_events:
        if event.get("work_year") and event.get("work_month"):
            key = (
                int(event["work_year"]), int(event["work_month"]),
                str(event.get("employer_code") or "001"),
            )
            group_items[key].append(_event_as_completion_item(event))
    for item in legacy_items:
        if item.get("work_year") and item.get("work_month"):
            key = (
                int(item["work_year"]), int(item["work_month"]),
                str(item.get("employer_code") or "001"),
            )
            group_items[key].append(item)

    groups = []
    all_entries = []
    for (work_year, work_month, company_code), items in sorted(group_items.items()):
        final_files = [
            row for row in list_gesher_export_files(
                conn,
                year=work_year,
                month=work_month,
                company_code=company_code,
                housing_array_id=housing_array_id,
            )
            if row.get("is_final") and not row.get("is_cancelled")
        ]
        group = {
            "work_year": work_year,
            "work_month": work_month,
            "company_code": company_code,
            "file_id": None,
            "filename": "",
            "status": "checked",
            "entries": [],
        }
        if not final_files:
            group.update({"status": "skipped", "reason": "אין קובץ גשר סופי"})
            groups.append(group)
            continue
        file_row = get_gesher_export_file(
            conn, int(final_files[0]["id"]), housing_array_id=housing_array_id
        )
        if not file_row or file_row.get("is_cancelled") or not file_row.get("is_final"):
            group.update({"status": "skipped", "reason": "קובץ הגשר הסופי אינו זמין"})
            groups.append(group)
            continue
        group["file_id"] = file_row.get("id")
        group["filename"] = file_row.get("filename") or ""

        paid_lines = enrich_paid_lines(conn, parse_gesher_file_lines(file_row.get("content") or ""))
        file_person_ids = _file_person_ids(file_row)
        current_lines = build_current_gesher_lines(
            conn, work_year, work_month,
            company_code=company_code,
            person_ids=file_person_ids,
        )
        paid_employee_codes = {line["employee_code"] for line in paid_lines}
        current_employee_codes = {line["employee_code"] for line in current_lines}
        actual_diffs = compare_line_sets(paid_lines, current_lines)
        for diff in actual_diffs:
            diff["employer_code"] = company_code
        actual_rows = finalize_completion_rows(build_completion_gesher_rows(actual_diffs))

        event_diffs = [
            diff for diff in expected_event_result["diffs"]
            if int(diff.get("work_year") or 0) == work_year
            and int(diff.get("work_month") or 0) == work_month
            and str(diff.get("employer_code") or "001") == company_code
        ]
        expected_rows = finalize_completion_rows(build_completion_gesher_rows(event_diffs))
        legacy_group = [item for item in legacy_items if item in items]
        if legacy_group:
            legacy_people = {int(item["person_id"]) for item in legacy_group if item.get("person_id")}
            legacy_diffs = _build_current_completion_diffs(
                conn, work_year, work_month, company_code, legacy_group,
                person_ids=legacy_people or None,
                diff_type="השלמה בחישוב ישן",
            )
            expected_rows = _merge_completion_rows(
                expected_rows,
                finalize_completion_rows(build_completion_gesher_rows(legacy_diffs)),
            )

        entries = compare_completion_audit_rows(expected_rows, actual_rows)
        for entry in entries:
            employee_code = entry.get("employee_code") or ""
            if entry["category"] == "matched":
                reason_category = "matched"
            elif employee_code not in paid_employee_codes and employee_code in current_employee_codes:
                reason_category = "new_in_current"
            elif employee_code in paid_employee_codes and employee_code not in current_employee_codes:
                reason_category = "removed_from_current"
            elif entry["category"] == "extra":
                reason_category = "untracked_bridge_difference"
            elif entry["category"] == "missing":
                reason_category = "expected_not_in_bridge_difference"
            else:
                reason_category = "value_mismatch"
            entry["reason_category"] = reason_category
        for entry in entries:
            entry.update({
                "work_year": work_year,
                "work_month": work_month,
                "company_code": company_code,
                "filename": group["filename"],
            })
        group["entries"] = entries
        group["status"] = "matched" if all(
            entry["category"] == "matched" for entry in entries
        ) else "issues"
        groups.append(group)
        all_entries.extend(entries)

    invalid_events = [
        event for event in expected_event_result["invalid_events"]
        if event.get("status") in COMPLETION_AUDIT_EXPECTED_STATUSES
    ]
    for event in invalid_events:
        entry = {
            "category": "missing",
            "reason_category": "invalid_event",
            "employee_code": _clean_employee_code(event.get("meirav_code")),
            "person_id": event.get("person_id"),
            "person_name": event.get("person_name") or "",
            "symbol": "",
            "display_name": event.get("validation_error") or "אירוע השלמה לא תקין",
            "expected_quantity": 0.0,
            "expected_amount": 0.0,
            "expected_rates": [],
            "actual_quantity": 0.0,
            "actual_amount": 0.0,
            "actual_rates": [],
            "quantity_difference": 0.0,
            "amount_difference": 0.0,
            "work_year": event.get("work_year"),
            "work_month": event.get("work_month"),
            "company_code": str(event.get("employer_code") or "001"),
            "filename": "",
        }
        for group in groups:
            if (
                group["work_year"] == event.get("work_year")
                and group["work_month"] == event.get("work_month")
                and group["company_code"] == entry["company_code"]
            ):
                group["entries"].append(entry)
                if group["status"] != "skipped":
                    group["status"] = "issues"
                break
        all_entries.append(entry)

    counts = {category: 0 for category in (
        "missing", "extra", "mismatch", "unrelated", "skipped", "matched"
    )}
    for entry in all_entries:
        counts[entry["category"]] += 1
    counts["skipped"] = sum(group["status"] == "skipped" for group in groups)
    issue_count = sum(counts[key] for key in ("missing", "extra", "mismatch"))
    return {
        "payment_year": payment_year,
        "payment_month": payment_month,
        "is_valid": issue_count == 0,
        "counts": counts,
        "checked_groups": sum(group["status"] != "skipped" for group in groups),
        "skipped_groups": counts["skipped"],
        "groups": groups,
        "entries": all_entries,
        "invalid_events": [
            {
                "id": event.get("id"),
                "person_name": event.get("person_name") or "",
                "work_year": event.get("work_year"),
                "work_month": event.get("work_month"),
                "message": event.get("validation_error") or "",
            }
            for event in invalid_events
        ],
    }


def build_completion_gesher_audit_excel(result: dict[str, Any]) -> bytes:
    """Export the already-calculated audit result without changing its findings."""
    labels = {
        "missing": "חסר", "extra": "מיותר", "mismatch": "אי התאמה",
        "unrelated": "שינוי לא קשור", "matched": "תואם",
    }
    reason_labels = {
        "new_in_current": "המדריך לא היה בגשר הקודם וכעת נמצא",
        "removed_from_current": "המדריך היה בגשר הקודם וכעת אינו נמצא",
        "untracked_bridge_difference": "הפרש בגשר ללא השלמה רשומה",
        "expected_not_in_bridge_difference": "השלמה בעמוד שלא נמצאה בהשוואת הגשר",
        "value_mismatch": "אי התאמה בסכום או בכמות",
        "invalid_event": "לא ניתן לבדוק בגלל נתונים חסרים",
        "matched": "התאמה מלאה",
    }
    detail_rows = []
    for row in result.get("entries", []):
        symbol = str(row.get("symbol") or "")
        is_quantity = symbol in COMPLETION_QUANTITY_TARGET_SYMBOLS
        expected_value = float(
            (row.get("expected_quantity") if is_quantity else row.get("expected_amount")) or 0
        )
        actual_value = float(
            (row.get("actual_quantity") if is_quantity else row.get("actual_amount")) or 0
        )
        detail_rows.append({
            "תוצאה": labels.get(row.get("category"), row.get("category", "")),
            "מקור ההבדל": reason_labels.get(
                row.get("reason_category"), row.get("reason_category", "")
            ),
            "חודש עבודה": f"{int(row.get('work_month') or 0):02d}/{row.get('work_year') or ''}",
            "מעסיק": row.get("company_code", ""),
            "קובץ גשר": row.get("filename", ""),
            "מדריך": row.get("person_name", ""),
            "קוד מירב": row.get("employee_code", ""),
            "סמל": symbol,
            "רכיב": row.get("display_name", ""),
            "יחידה": "ימים" if is_quantity else "₪",
            "חישוב ההשלמות בעמוד": round(expected_value, 2),
            "השוואת הגשר": round(actual_value, 2),
            "פער": round(actual_value - expected_value, 2),
        })
    group_rows = [{
        "חודש עבודה": f"{group['work_month']:02d}/{group['work_year']}",
        "מעסיק": group["company_code"],
        "סטטוס": group["status"],
        "קובץ גשר": group.get("filename", ""),
        "סיבה": group.get("reason", ""),
    } for group in result.get("groups", [])]
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(detail_rows).to_excel(writer, sheet_name="פירוט בדיקה", index=False)
        pd.DataFrame(group_rows).to_excel(writer, sheet_name="חודשי עבודה", index=False)
    output.seek(0)
    return output.getvalue()

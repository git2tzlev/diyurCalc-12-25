"""Generate completion-based Gesher difference checks and Excel reports."""
from __future__ import annotations

from collections import defaultdict
from io import BytesIO
from typing import Any, Optional

import pandas as pd

from core.logic import calculate_monthly_summary
from services import gesher_exporter


MONEY_EPSILON = 0.01
QUANTITY_EPSILON = 0.01
COMPLETION_PENSION_SOURCE_SYMBOLS = {"360", "362", "363"}
COMPLETION_NON_PENSION_SOURCE_SYMBOLS = {
    "366", "368", "370", "371", "373", "374", "382", "434",
}
COMPLETION_PROFESSIONAL_SUPPORT_SYMBOLS = {"243"}
COMPLETION_INFO_SOURCE_SYMBOLS = {"299", "698", "767"}
COMPLETION_TARGET_SYMBOLS = {
    "pension": "317",
    "non_pension": "253",
    "professional_support": "243",
}


def _amount_for_line(quantity: float, rate: float) -> float:
    """Calculate line amount using Gesher conventions."""
    if abs(quantity) < QUANTITY_EPSILON:
        return round(rate, 2)
    return round(quantity * rate, 2)


def _clean_employee_code(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits.zfill(6) if digits else ""


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
        excluded_time_report_ids=excluded_time_report_ids,
        excluded_payment_component_ids=excluded_payment_component_ids,
    )

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
                if include_negative_values:
                    if abs(quantity) < options["min_amount"] and abs(rate) < options["min_amount"]:
                        continue
                elif value_type.startswith("hours_") and quantity < options["min_amount"]:
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
    if source_symbol in COMPLETION_PROFESSIONAL_SUPPORT_SYMBOLS:
        return COMPLETION_TARGET_SYMBOLS["professional_support"]
    return None


def is_completion_payable_source_symbol(symbol: Any) -> bool:
    """Whether a source Gesher symbol should be counted as payable completion impact."""
    return _completion_target_for_source_symbol(symbol) is not None


def is_completion_info_source_symbol(symbol: Any) -> bool:
    """Whether a source Gesher symbol is informational and not part of payable impact."""
    return str(symbol or "").strip() in COMPLETION_INFO_SOURCE_SYMBOLS


def build_completion_gesher_rows(diffs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate completion differences into the target Gesher symbols."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for diff in diffs:
        target_symbol = _completion_target_for_source_symbol(diff.get("symbol"))
        if not target_symbol:
            continue
        amount_diff = round(float(diff.get("amount_diff") or 0), 2)
        if abs(amount_diff) < MONEY_EPSILON:
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
                "source_symbols": set(),
            }
        grouped[key]["amount"] = round(grouped[key]["amount"] + amount_diff, 2)
        grouped[key]["source_symbols"].add(str(diff.get("symbol") or ""))

    rows = []
    for row in grouped.values():
        if abs(float(row["amount"])) < MONEY_EPSILON:
            continue
        row["source_symbols"] = ", ".join(sorted(row["source_symbols"]))
        rows.append(row)

    return sorted(rows, key=lambda row: (row["employee_code"], row["symbol"]))


def build_completion_gesher_file(rows: list[dict[str, Any]], year: int, month: int, company_code: Optional[str] = None) -> str:
    """Build a Gesher-format file for aggregated completion differences."""
    company = company_code or gesher_exporter.get_export_options()["default_company"]
    text = gesher_exporter.format_gesher_header(company, year, month) + "\r\n"
    for row in rows:
        text += gesher_exporter.format_gesher_line(
            employee_code=int(row["employee_code"]),
            symbol=row["symbol"],
            quantity=0.0,
            rate=round(float(row["amount"]), 2),
        ) + "\r\n"
    return text


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
        row.update({
            "paid_quantity": round(old_quantity, 2),
            "paid_amount": round(old_amount, 2),
            "current_quantity": round(new_quantity, 2),
            "current_amount": round(new_amount, 2),
            "quantity_diff": quantity_diff,
            "amount_diff": amount_diff,
            "diff_type": diff_type,
        })
        diffs.append(row)
    return diffs


def build_completion_impact_rows(
    before_lines: list[dict[str, Any]],
    after_lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compare completion impact by employee and Gesher symbol."""

    def aggregate_for_impact(lines: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for line in lines:
            key = (line["employee_code"], line["symbol"])
            if key not in grouped:
                grouped[key] = dict(line)
                grouped[key]["quantity"] = 0.0
                grouped[key]["amount"] = 0.0
                grouped[key]["rates"] = set()
            grouped[key]["quantity"] = round(grouped[key]["quantity"] + float(line.get("quantity") or 0), 2)
            grouped[key]["amount"] = round(grouped[key]["amount"] + float(line.get("amount") or 0), 2)
            grouped[key]["rates"].add(round(float(line.get("rate") or 0), 2))
        return grouped

    def format_rates(rates: set[float]) -> str:
        return ", ".join(f"{rate:.2f}" for rate in sorted(rates))

    before = aggregate_for_impact(before_lines)
    after = aggregate_for_impact(after_lines)
    rows = []
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        row = dict(new or old or {})
        before_quantity = float(old.get("quantity") if old else 0)
        before_amount = float(old.get("amount") if old else 0)
        after_quantity = float(new.get("quantity") if new else 0)
        after_amount = float(new.get("amount") if new else 0)
        quantity_diff = round(after_quantity - before_quantity, 2)
        amount_diff = round(after_amount - before_amount, 2)
        if abs(quantity_diff) < QUANTITY_EPSILON and abs(amount_diff) < MONEY_EPSILON:
            continue

        before_rates = old.get("rates", set()) if old else set()
        after_rates = new.get("rates", set()) if new else set()
        before_rate_label = format_rates(before_rates)
        after_rate_label = format_rates(after_rates)
        rate_label = before_rate_label
        if before_rate_label != after_rate_label:
            rate_label = f"{before_rate_label or '0.00'} -> {after_rate_label or '0.00'}"

        if old is None:
            diff_type = "שורה נוספה"
        elif new is None:
            diff_type = "שורה ירדה"
        elif before_rates != after_rates:
            diff_type = "תעריף השתנה"
        elif abs(quantity_diff) >= QUANTITY_EPSILON:
            diff_type = "כמות השתנתה"
        else:
            diff_type = "סכום השתנה"

        row.update({
            "rate": next(iter(after_rates or before_rates or {0.0})),
            "rate_label": rate_label,
            "before_quantity": round(before_quantity, 2),
            "before_amount": round(before_amount, 2),
            "after_quantity": round(after_quantity, 2),
            "after_amount": round(after_amount, 2),
            "quantity_diff": quantity_diff,
            "amount_diff": amount_diff,
            "diff_type": diff_type,
        })
        row.pop("rates", None)
        rows.append(row)
    return rows


def _diffs_to_rows(diffs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for diff in diffs:
        rows.append({
            "שם מדריך": diff.get("person_name", ""),
            "קוד מירב": diff.get("employee_code", ""),
            "סמל": diff.get("symbol", ""),
            "רכיב": diff.get("display_name", ""),
            "תעריף": diff.get("rate", 0),
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
        is_report = item.get("item_type") == "time_report"
        rows.append({
            "סוג": "משמרת" if is_report else "רכיב תשלום",
            "שם מדריך": item.get("person_name", ""),
            "קוד מירב": item.get("meirav_code", ""),
            "תאריך עבודה": item.get("date"),
            "חודש עבודה": f"{item.get('work_month'):02d}/{item.get('work_year')}",
            "דירה": item.get("apartment_name", ""),
            "משמרת/רכיב": item.get("shift_name") if is_report else item.get("component_name"),
            "שעות/כמות": (
                f"{item.get('start_time') or ''}-{item.get('end_time') or ''}"
                if is_report else item.get("quantity")
            ),
            "הערת תשלום": item.get("payment_note", ""),
            "סומן ע\"י": item.get("payment_marked_by_name", ""),
            "סומן בתאריך": item.get("payment_marked_at"),
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

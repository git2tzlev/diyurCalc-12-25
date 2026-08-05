from __future__ import annotations

from datetime import date

from openpyxl import Workbook

from core.clothing_pay import (
    apply_clothing_pay_to_totals,
    clothing_fte_from_monthly_hours,
    clothing_person_ineligibility_reason,
    clothing_period_months,
    has_clothing_seniority,
    parse_legacy_clothing_xlsx,
)
from services.gesher_exporter import calculate_value


def test_clothing_period_is_august_through_july():
    months = clothing_period_months(2026)
    assert len(months) == 12
    assert months[0] == (2025, 8)
    assert months[-1] == (2026, 7)


def test_clothing_seniority_uses_august_first_boundary():
    assert has_clothing_seniority(date(2025, 8, 1), 2026) is True
    assert has_clothing_seniority(date(2025, 8, 2), 2026) is False


def test_clothing_person_must_be_permanent_and_tzohar_regardless_of_active_flag():
    eligible = {
        "is_active": True,
        "type": "permanent",
        "housing_array_id": 1,
        "start_date": date(2025, 8, 1),
    }
    assert clothing_person_ineligibility_reason(eligible, 2026) == ""
    assert clothing_person_ineligibility_reason({**eligible, "is_active": False}, 2026) == ""
    assert clothing_person_ineligibility_reason({**eligible, "type": "substitute"}, 2026) == "not_permanent"
    assert clothing_person_ineligibility_reason({**eligible, "housing_array_id": 2}, 2026) == "not_tzohar_halev"


def test_clothing_fte_caps_each_month_separately():
    # The excess in one month must not compensate for a missing month.
    hours = [364] + [182] * 10 + [0]
    assert clothing_fte_from_monthly_hours(hours) == 11 / 12
    assert clothing_fte_from_monthly_hours([300] * 12) == 1.0


def test_parse_legacy_clothing_xlsx_reads_monthly_hours(tmp_path):
    path = tmp_path / "legacy-hours.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Mifal", "Hodesh", "Worker", "Merav", "FullName", "SHaot"])
    sheet.append(["400", date(2025, 8, 1), 123456789, 4177, "מדריך", 200])
    workbook.save(path)

    rows = parse_legacy_clothing_xlsx(path)

    assert len(rows) == 1
    assert rows[0].source_year == 2025
    assert rows[0].source_month == 8
    assert rows[0].id_number == "123456789"
    assert rows[0].meirav_code == "4177"
    assert rows[0].source_hours == 200


def test_apply_clothing_pay_updates_all_totals():
    totals = {
        "total_payment": 100,
        "gesher_total": 100,
        "display_total": 100,
        "rounded_total": 100,
    }
    details = {"amount": 151.04, "fte_percent": 8.33}

    apply_clothing_pay_to_totals(totals, details)

    assert totals["clothing_pay"] == 151.04
    assert totals["clothing_pay_details"] == details
    assert totals["total_payment"] == 251.0
    assert totals["gesher_total"] == 251.0
    assert totals["display_total"] == 251.0
    assert totals["rounded_total"] == 251.0


def test_clothing_pay_is_exported_as_one_unit_at_calculated_rate():
    quantity, rate = calculate_value(
        {"clothing_pay": 850.56}, "clothing_pay", "money_as_unit"
    )
    assert quantity == 1.0
    assert rate == 850.56
